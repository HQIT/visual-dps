"""Redis 姿态总线：Stream 分片队列（事件 Worker）+ Pub/Sub（UI 实时）。"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

import redis as sync_redis

from services.event_engine.sharding import (
    logical_shard_count,
    logical_shard_id,
    stream_key_for_camera,
    stream_key_for_shard,
    worker_owned_stream_keys,
)
from services.pipeline_log import log_pipeline_stage

logger = logging.getLogger(__name__)

POSE_CHANNEL_PREFIX = "pose:live:"
POSE_SNAPSHOT_PREFIX = "pose:snapshot:"
# 兼容旧引用：单 shard 或未分片时的默认键名
POSE_STREAM_KEY = stream_key_for_shard(0)
POSE_STREAM_GROUP = os.environ.get("POSE_STREAM_GROUP", "event-workers")
POSE_STREAM_MAXLEN = max(100, int(os.environ.get("POSE_STREAM_MAXLEN", "2000")))
POSE_SCHEMA_VERSION = 1
SNAPSHOT_TTL_SEC = max(3, int(os.environ.get("LIVE_SNAPSHOT_TTL_SEC", "10")))


def redis_url() -> str:
    from services.live_bus import redis_url as _url

    return _url()


def channel_for(camera_id: str) -> str:
    return f"{POSE_CHANNEL_PREFIX}{camera_id}"


def snapshot_key_for(camera_id: str) -> str:
    return f"{POSE_SNAPSHOT_PREFIX}{camera_id}"


def pose_delivery_mode() -> str:
    """stream = Redis Stream 分片队列；pubsub = 仅 Pub/Sub（旧行为）。"""
    return os.environ.get("POSE_DELIVERY", "stream").strip().lower() or "stream"


def all_pose_stream_keys() -> list[str]:
    """全部 logical shard 对应的 Stream 键（监控/拓扑用）。"""
    return [stream_key_for_shard(i) for i in range(logical_shard_count())]


def build_pose_frame(
    *,
    camera_id: str,
    frame_idx: int,
    persons: list,
    infer_width: int,
    infer_height: int,
    run_id: str = "",
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "schema": POSE_SCHEMA_VERSION,
        "kind": "pose",
        "ts": time.time(),
        "camera_id": str(camera_id),
        "frame_idx": int(frame_idx),
        "infer_width": int(infer_width),
        "infer_height": int(infer_height),
        "persons": list(persons),
    }
    rid = str(run_id or "").strip()
    if rid:
        frame["run_id"] = rid
    return frame


def ensure_pose_stream_group(
    stream_key: str | None = None,
    client: sync_redis.Redis | None = None,
) -> None:
    own = client is None
    if own:
        client = sync_redis.from_url(redis_url(), decode_responses=True)
    key = stream_key or POSE_STREAM_KEY
    try:
        client.xgroup_create(key, POSE_STREAM_GROUP, id="0", mkstream=True)
        logger.info("Created pose stream group %s on %s", POSE_STREAM_GROUP, key)
    except sync_redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
    finally:
        if own and client is not None:
            client.close()


def ensure_pose_stream_groups(
    stream_keys: list[str] | None = None,
    client: sync_redis.Redis | None = None,
) -> None:
    keys = stream_keys if stream_keys is not None else worker_owned_stream_keys()
    own = client is None
    if own:
        client = sync_redis.from_url(redis_url(), decode_responses=True)
    try:
        for key in keys:
            ensure_pose_stream_group(key, client=client)
    finally:
        if own and client is not None:
            client.close()


def publish_pose_frame(
    camera_id: str,
    *,
    frame_idx: int,
    persons: list,
    infer_width: int,
    infer_height: int,
    run_id: str = "",
) -> bool:
    cid = str(camera_id or "").strip()
    if not cid:
        return False
    frame = build_pose_frame(
        camera_id=cid,
        frame_idx=frame_idx,
        persons=persons,
        infer_width=infer_width,
        infer_height=infer_height,
        run_id=run_id,
    )
    payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    stream_key = stream_key_for_camera(cid)
    try:
        client = sync_redis.from_url(redis_url(), decode_responses=True)
        pipe = client.pipeline(transaction=False)
        if pose_delivery_mode() == "stream":
            pipe.xadd(
                stream_key,
                {"payload": payload},
                maxlen=POSE_STREAM_MAXLEN,
                approximate=True,
            )
        pipe.set(snapshot_key_for(cid), payload, ex=SNAPSHOT_TTL_SEC)
        pipe.publish(channel_for(cid), payload)
        pipe.execute()
        client.close()
        log_pipeline_stage(
            "pose_published",
            camera_id=cid,
            frame_idx=frame_idx,
            run_id=run_id or None,
            persons=len(persons),
            delivery=pose_delivery_mode(),
            stream_key=stream_key,
            logical_shard=logical_shard_id(cid),
        )
        return True
    except Exception as exc:
        logger.warning("Redis publish_pose_frame failed camera=%s: %s", cid, exc)
        return False


def list_recent_pose_frames(camera_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """从该 camera 所属 shard 的 Stream 取最近若干帧（新→旧）。"""
    cid = str(camera_id or "").strip()
    if not cid or limit < 1:
        return []
    stream_key = stream_key_for_camera(cid)
    try:
        client = sync_redis.from_url(redis_url(), decode_responses=True)
        rows = client.xrevrange(stream_key, count=max(limit * 6, 24))
        client.close()
    except Exception as exc:
        logger.warning("Redis list_recent_pose_frames failed camera=%s: %s", cid, exc)
        return []
    out: list[dict[str, Any]] = []
    for _entry_id, fields in rows:
        raw = (fields or {}).get("payload")
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or str(data.get("camera_id") or "") != cid:
            continue
        out.append(data)
        if len(out) >= limit:
            break
    return out


def get_pose_snapshot(camera_id: str) -> dict[str, Any] | None:
    cid = str(camera_id or "").strip()
    if not cid:
        return None
    try:
        client = sync_redis.from_url(redis_url(), decode_responses=True)
        raw = client.get(snapshot_key_for(cid))
        client.close()
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Redis get_pose_snapshot failed camera=%s: %s", cid, exc)
        return None


def default_consumer_name() -> str:
    explicit = os.environ.get("EVENT_WORKER_CONSUMER_NAME", "").strip()
    if explicit:
        return explicit
    return f"worker-{uuid.uuid4().hex[:8]}"


def camera_id_from_channel(channel: str) -> str:
    if channel.startswith(POSE_CHANNEL_PREFIX):
        return channel[len(POSE_CHANNEL_PREFIX) :]
    return channel

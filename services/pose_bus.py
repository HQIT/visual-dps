"""Redis 姿态总线：Stream 队列（事件 Worker LB）+ Pub/Sub（UI 实时）。"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

import redis as sync_redis

logger = logging.getLogger(__name__)

POSE_CHANNEL_PREFIX = "pose:live:"
POSE_SNAPSHOT_PREFIX = "pose:snapshot:"
POSE_STREAM_KEY = os.environ.get("POSE_STREAM_KEY", "pose:stream")
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
    """stream = Redis Stream 竞争消费；pubsub = 仅 Pub/Sub（旧行为）。"""
    return os.environ.get("POSE_DELIVERY", "stream").strip().lower() or "stream"


def build_pose_frame(
    *,
    camera_id: str,
    frame_idx: int,
    persons: list,
    infer_width: int,
    infer_height: int,
    source_mode: str = "stream",
    run_id: str = "",
    video_time_sec: float | None = None,
    video_fps: float | None = None,
    latency_ms: dict | None = None,
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
        "source_mode": str(source_mode or "stream"),
    }
    if run_id:
        frame["run_id"] = str(run_id)
    if video_time_sec is not None:
        frame["video_time_sec"] = float(video_time_sec)
    if video_fps is not None:
        frame["video_fps"] = float(video_fps)
    if latency_ms:
        frame["latency_ms"] = dict(latency_ms)
    return frame


def is_benchmark_pose(frame: dict[str, Any]) -> bool:
    return bool(str(frame.get("run_id") or "").strip()) or frame.get("source_mode") == "file"


def ensure_pose_stream_group(client: sync_redis.Redis | None = None) -> None:
    own = client is None
    if own:
        client = sync_redis.from_url(redis_url(), decode_responses=True)
    try:
        client.xgroup_create(POSE_STREAM_KEY, POSE_STREAM_GROUP, id="0", mkstream=True)
        logger.info("Created pose stream group %s on %s", POSE_STREAM_GROUP, POSE_STREAM_KEY)
    except sync_redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise
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
    source_mode: str = "stream",
    run_id: str = "",
    video_time_sec: float | None = None,
    video_fps: float | None = None,
    latency_ms: dict | None = None,
) -> bool:
    cid = str(camera_id or "").strip()
    if not cid:
        return False
    bench = bool(str(run_id or os.environ.get("INFERENCE_RUN_ID", "")).strip())
    effective_run_id = str(run_id or os.environ.get("INFERENCE_RUN_ID", "")).strip()
    effective_mode = "file" if bench else str(source_mode or "stream")
    frame = build_pose_frame(
        camera_id=cid,
        frame_idx=frame_idx,
        persons=persons,
        infer_width=infer_width,
        infer_height=infer_height,
        source_mode=effective_mode,
        run_id=effective_run_id,
        video_time_sec=video_time_sec,
        video_fps=video_fps,
        latency_ms=latency_ms,
    )
    payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    skip_live = bench or is_benchmark_pose(frame)
    try:
        client = sync_redis.from_url(redis_url(), decode_responses=True)
        pipe = client.pipeline(transaction=False)
        if pose_delivery_mode() == "stream":
            pipe.xadd(
                POSE_STREAM_KEY,
                {"payload": payload},
                maxlen=POSE_STREAM_MAXLEN,
                approximate=True,
            )
        if not skip_live:
            pipe.set(snapshot_key_for(cid), payload, ex=SNAPSHOT_TTL_SEC)
            pipe.publish(channel_for(cid), payload)
        pipe.execute()
        client.close()
        return True
    except Exception as exc:
        logger.warning("Redis publish_pose_frame failed camera=%s: %s", cid, exc)
        return False


def list_recent_pose_frames(camera_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
    """从 pose:stream 取某路最近若干帧（新→旧）。"""
    cid = str(camera_id or "").strip()
    if not cid or limit < 1:
        return []
    try:
        client = sync_redis.from_url(redis_url(), decode_responses=True)
        rows = client.xrevrange(POSE_STREAM_KEY, count=max(limit * 6, 24))
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

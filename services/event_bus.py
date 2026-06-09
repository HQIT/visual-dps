"""Redis 事件 overlay 总线（事件 Worker → UI）。"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import redis as sync_redis

logger = logging.getLogger(__name__)

EVENT_CHANNEL_PREFIX = "event:live:"
EVENT_SNAPSHOT_PREFIX = "event:snapshot:"
EVENT_SCHEMA_VERSION = 1
SNAPSHOT_TTL_SEC = max(3, int(os.environ.get("LIVE_SNAPSHOT_TTL_SEC", "10")))


def redis_url() -> str:
    from services.live_bus import redis_url as _url

    return _url()


def channel_for(camera_id: str) -> str:
    return f"{EVENT_CHANNEL_PREFIX}{camera_id}"


def snapshot_key_for(camera_id: str) -> str:
    return f"{EVENT_SNAPSHOT_PREFIX}{camera_id}"


def _camera_id_from_channel(channel: str) -> str:
    if channel.startswith(EVENT_CHANNEL_PREFIX):
        return channel[len(EVENT_CHANNEL_PREFIX) :]
    return channel


def build_event_frame(
    *,
    camera_id: str,
    frame_idx: int,
    collisions: list,
    alarm_collisions: list,
    skeletons: list | None = None,
    ts: float | None = None,
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "schema": EVENT_SCHEMA_VERSION,
        "kind": "event",
        "ts": float(ts if ts is not None else time.time()),
        "camera_id": str(camera_id),
        "frame_idx": int(frame_idx),
        "collisions": list(collisions),
        "alarm_collisions": list(alarm_collisions),
    }
    if skeletons is not None:
        frame["skeletons"] = list(skeletons)
    return frame


def publish_event_frame(
    camera_id: str,
    *,
    frame_idx: int,
    collisions: list,
    alarm_collisions: list,
    skeletons: list | None = None,
) -> bool:
    cid = str(camera_id or "").strip()
    if not cid:
        return False
    frame = build_event_frame(
        camera_id=cid,
        frame_idx=frame_idx,
        collisions=collisions,
        alarm_collisions=alarm_collisions,
        skeletons=skeletons,
    )
    payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    try:
        client = sync_redis.from_url(redis_url(), decode_responses=True)
        pipe = client.pipeline(transaction=False)
        pipe.set(snapshot_key_for(cid), payload, ex=SNAPSHOT_TTL_SEC)
        pipe.publish(channel_for(cid), payload)
        pipe.execute()
        client.close()
        return True
    except Exception as exc:
        logger.warning("Redis publish_event_frame failed camera=%s: %s", cid, exc)
        return False


def publish_event_frame_dict(frame: dict[str, Any]) -> bool:
    """写入完整 EventFrame（供 edge REST 等外部来源）。"""
    if not isinstance(frame, dict) or str(frame.get("kind") or "") != "event":
        return False
    cid = str(frame.get("camera_id") or "").strip()
    if not cid:
        return False
    payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    try:
        client = sync_redis.from_url(redis_url(), decode_responses=True)
        pipe = client.pipeline(transaction=False)
        pipe.set(snapshot_key_for(cid), payload, ex=SNAPSHOT_TTL_SEC)
        pipe.publish(channel_for(cid), payload)
        pipe.execute()
        client.close()
        return True
    except Exception as exc:
        logger.warning("Redis publish_event_frame_dict failed camera=%s: %s", cid, exc)
        return False


def get_event_snapshot(camera_id: str) -> dict[str, Any] | None:
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
        logger.warning("Redis get_event_snapshot failed camera=%s: %s", cid, exc)
        return None


def camera_id_from_channel(channel: str) -> str:
    return _camera_id_from_channel(channel)

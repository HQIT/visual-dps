"""Edge 节点状态（Redis 快照，与 pose/event 管道分离）。"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import redis as sync_redis

from services.pose_bus import get_pose_snapshot

logger = logging.getLogger(__name__)

EDGE_STATUS_SCHEMA = 1
EDGE_STATUS_PREFIX = "edge:status:"
EDGE_STATUS_TTL_SEC = max(15, int(os.environ.get("EDGE_STATUS_TTL_SEC", "60")))
EDGE_STATUS_STALE_SEC = max(
    5.0,
    float(os.environ.get("EDGE_STATUS_STALE_SEC", str(max(EDGE_STATUS_TTL_SEC - 5, 30)))),
)

EDGE_STATES = frozenset({"running", "idle", "error"})


def _redis_client() -> sync_redis.Redis:
    from services.live_bus import redis_url

    return sync_redis.from_url(redis_url(), decode_responses=True)


def status_key_for(camera_id: str) -> str:
    return f"{EDGE_STATUS_PREFIX}{camera_id}"


def publish_edge_status(camera_id: str, payload: dict[str, Any]) -> bool:
    cid = str(camera_id or "").strip()
    if not cid:
        return False
    body = dict(payload)
    body.setdefault("schema", EDGE_STATUS_SCHEMA)
    body["camera_id"] = cid
    body["ts"] = float(body.get("ts") or time.time())
    state = str(body.get("state") or "running").strip().lower()
    body["state"] = state if state in EDGE_STATES else "running"
    try:
        client = _redis_client()
        client.set(
            status_key_for(cid),
            json.dumps(body, ensure_ascii=False, separators=(",", ":")),
            ex=EDGE_STATUS_TTL_SEC,
        )
        client.close()
        return True
    except Exception as exc:
        logger.warning("Redis publish_edge_status failed camera=%s: %s", cid, exc)
        return False


def get_edge_status(camera_id: str) -> dict[str, Any] | None:
    cid = str(camera_id or "").strip()
    if not cid:
        return None
    try:
        client = _redis_client()
        raw = client.get(status_key_for(cid))
        client.close()
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Redis get_edge_status failed camera=%s: %s", cid, exc)
        return None


def _age_sec(ts: float | None, *, now: float | None = None) -> float | None:
    if ts is None:
        return None
    ref = float(now if now is not None else time.time())
    return max(0.0, round(ref - float(ts), 2))


def build_edge_runtime(camera_id: str, *, now: float | None = None) -> dict[str, Any]:
    """供 API / 拓扑使用的 edge 运行态视图（central 路勿调用）。"""
    ref = float(now if now is not None else time.time())
    status = get_edge_status(camera_id)
    pose = get_pose_snapshot(camera_id)

    status_ts = float(status["ts"]) if status and status.get("ts") is not None else None
    pose_ts = float(pose["ts"]) if pose and pose.get("ts") is not None else None
    status_age = _age_sec(status_ts, now=ref)
    pose_age = _age_sec(pose_ts, now=ref)

    agent_alive = status_age is not None and status_age <= EDGE_STATUS_STALE_SEC
    state = str(status.get("state") or "").strip().lower() if status else ""
    if not state:
        state = "unknown"

    pose_fresh = pose_age is not None and pose_age <= EDGE_STATUS_STALE_SEC
    # idle：画面中无人，pose 不更新仍属正常
    pose_ok = pose_fresh or (agent_alive and state == "idle")

    if not agent_alive:
        display = "边缘失联"
        health = "error"
    elif state == "error":
        display = str(status.get("message") or "边缘异常").strip() or "边缘异常"
        health = "error"
    elif state == "idle":
        display = "边缘运行中（空闲）"
        health = "ok"
    elif state == "running":
        display = "边缘运行中"
        health = "ok"
    else:
        display = "边缘上报"
        health = "warn" if agent_alive else "error"

    return {
        "agent_alive": agent_alive,
        "state": state,
        "health": health,
        "display": display,
        "message": str(status.get("message") or "").strip() if status else "",
        "agent_version": str(status.get("agent_version") or "").strip() if status else "",
        "last_status_age_sec": status_age,
        "last_pose_age_sec": pose_age,
        "pose_ok": pose_ok,
        "metrics": status.get("metrics") if isinstance(status, dict) and isinstance(status.get("metrics"), dict) else {},
    }

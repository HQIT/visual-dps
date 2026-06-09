"""Edge 上报 EventFrame 入库与 Java 回调（Phase B）。"""

from __future__ import annotations

import os
import time
from typing import Any

from services.box_identity import parse_collision_token
from services.event_bus import publish_event_frame_dict


def _video_fps(app_config: dict | None, camera: dict | None) -> float:
    cfg = app_config or {}
    cam = camera or {}
    settings = cam.get("settings") if isinstance(cam.get("settings"), dict) else {}
    raw = settings.get("inference.frame_rate")
    if raw is None:
        raw = (cfg.get("inference") or {}).get("frame_rate", 15)
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 15.0


def _skeletons_payload(raw: list | None) -> list[dict[str, Any]] | None:
    if not raw:
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kps = item.get("keypoints") or []
        out.append(
            {
                "person_id": int(item.get("person_id") or 0),
                "keypoints": list(kps),
            }
        )
    return out or None


def ingest_edge_event_frame(
    camera_id: str,
    *,
    frame: dict[str, Any],
    camera: dict | None = None,
    app_config: dict | None = None,
    callback_reporter=None,
) -> bool:
    cid = str(camera_id or "").strip()
    if not cid:
        return False
    body = dict(frame)
    body["schema"] = int(body.get("schema") or 1)
    body["kind"] = "event"
    body["camera_id"] = cid
    body["ts"] = float(body.get("ts") or time.time())
    body["frame_idx"] = int(body.get("frame_idx") or 0)
    body["collisions"] = list(body.get("collisions") or [])
    body["alarm_collisions"] = list(body.get("alarm_collisions") or [])
    skeletons = body.get("skeletons")
    if skeletons is not None:
        body["skeletons"] = _skeletons_payload(skeletons if isinstance(skeletons, list) else []) or []

    ok = publish_event_frame_dict(body)
    if not ok:
        return False

    alarm_collisions = body.get("alarm_collisions") or []
    if callback_reporter and alarm_collisions:
        fps = _video_fps(app_config, camera)
        frame_idx = int(body["frame_idx"])
        video_time_sec = frame_idx / fps
        upload_tag = f"infer_{cid}"
        for collision in alarm_collisions:
            shelf_code, box_id = parse_collision_token(collision)
            if not box_id:
                continue
            callback_reporter.enqueue_pick_finished(
                box_id=box_id,
                frame_idx=frame_idx,
                video_time_sec=video_time_sec,
                upload_tag=upload_tag,
                shelf_code=shelf_code or None,
            )
    return True

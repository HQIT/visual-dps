"""推理容器内可选碰撞检测（默认 stream 模式由 event-worker 处理）。"""

from __future__ import annotations

import os

from services.event_engine.collision import CollisionProcessor
from services.pose_bus import pose_delivery_mode


def collision_in_inference() -> bool:
    raw = os.environ.get("COLLISION_IN_INFERENCE", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return pose_delivery_mode() != "stream"


def create_collision_processor(boxes: list, app_config: dict) -> CollisionProcessor | None:
    if not boxes:
        return None
    infer_cfg = app_config.get("inference") if isinstance(app_config.get("inference"), dict) else {}
    return CollisionProcessor(
        boxes,
        alarm_min_consecutive_frames=int(infer_cfg.get("alarm_min_consecutive_frames", 3) or 3),
        alarm_cooldown_frames=int(infer_cfg.get("alarm_cooldown_frames", 6) or 6),
        alarm_min_consecutive_sec=float(infer_cfg.get("alarm_min_consecutive_sec", 0) or 0),
        alarm_cooldown_sec=float(infer_cfg.get("alarm_cooldown_sec", 0) or 0),
        video_fps=float(infer_cfg.get("frame_rate", 15) or 15),
    )


def format_alarm_gate_note(app_config: dict) -> str:
    infer_cfg = app_config.get("inference") if isinstance(app_config.get("inference"), dict) else {}
    min_sec = float(infer_cfg.get("alarm_min_consecutive_sec", 0) or 0)
    if min_sec > 0:
        cool_sec = float(infer_cfg.get("alarm_cooldown_sec", 0) or 0)
        return f"time_trigger={min_sec:.2f}s time_cooldown={cool_sec:.2f}s"
    return (
        f"frames>={int(infer_cfg.get('alarm_min_consecutive_frames', 3) or 3)} "
        f"cooldown={int(infer_cfg.get('alarm_cooldown_frames', 6) or 6)}f"
    )

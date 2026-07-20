"""前置门控终端日志（风格对齐 event-worker [COLLISION]）。"""

from __future__ import annotations

import os
from typing import Any

from services.event_engine.pick_prefilter.decision import PrefilterDecision
from services.wall_clock import wall_time_str


def prefilter_log_enabled() -> bool:
    """PREFILTER_LOG=1 时输出；未设置时跟随 COLLISION_LOG。"""
    for key in ("PREFILTER_LOG", "COLLISION_LOG"):
        val = os.environ.get(key, "").strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
    return False


def _format_log_video_time(sec: float) -> str:
    total = max(0.0, float(sec))
    m, s = divmod(int(total), 60)
    ms = int(round((total - int(total)) * 1000))
    return f"{m:02d}:{s:02d}.{ms:03d}"


def _resolve_log_video_time(pose: dict[str, Any], fallback_fps: float) -> tuple[float, str]:
    vts = pose.get("video_time_sec")
    if vts is not None:
        try:
            sec = float(vts)
            return sec, _format_log_video_time(sec)
        except (TypeError, ValueError):
            pass
    frame_idx = int(pose.get("frame_idx") or 0)
    fps = float(pose.get("video_fps") or fallback_fps or 15.0)
    sec = max(0.0, float(frame_idx - 1) / fps) if frame_idx > 0 and fps > 0 else 0.0
    return sec, _format_log_video_time(sec)


def _fmt_speed(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{float(val):.6f}".rstrip("0").rstrip(".")


def log_prefilter_decision(
    pose_frame: dict[str, Any],
    decision: PrefilterDecision,
    *,
    video_fps: float,
) -> None:
    if not prefilter_log_enabled():
        return

    camera_id = str(pose_frame.get("camera_id") or "")
    frame_idx = int(pose_frame.get("frame_idx") or 0)
    run_id = str(pose_frame.get("run_id") or "")
    src = pose_frame.get("source_mode") or "stream"
    vsec, vtext = _resolve_log_video_time(pose_frame, video_fps)
    wall_time = wall_time_str()
    tag = "FILTERED" if decision.blocked else "PASS"
    filtered = "true" if decision.blocked else "false"

    line = (
        f"[PREFILTER][{tag}] time={wall_time} camera={camera_id} source={src} "
        f"video_time={vtext} video_sec={vsec:.3f} frame={frame_idx} "
        f"track={decision.track_id} "
        f"{decision.speed_feature}={_fmt_speed(decision.speed_value)} "
        f"threshold={_fmt_speed(decision.speed_threshold)} "
        f"ankle_max_speed={_fmt_speed(decision.ankle_max_speed)} "
        f"ankle_max_speed_norm={_fmt_speed(decision.ankle_max_speed_norm)} "
        f"filtered={filtered}"
    )
    if run_id:
        line = line.replace(f" camera={camera_id}", f" run_id={run_id} camera={camera_id}", 1)
    print(line, flush=True)

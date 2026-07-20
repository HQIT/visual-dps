"""Event Worker 终端日志：PREFILTER / COLLISION 统一字段与格式。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from services.event_engine.pick_prefilter.decision import PrefilterDecision
from services.wall_clock import wall_time_str


def collision_log_enabled() -> bool:
    return os.environ.get("COLLISION_LOG", "").strip().lower() in ("1", "true", "yes", "on")


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


def resolve_log_video_time(pose: dict[str, Any], fallback_fps: float) -> tuple[float, str]:
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


def fmt_optional_float(val: float | None) -> str:
    if val is None:
        return "—"
    return f"{float(val):.6f}".rstrip("0").rstrip(".")


def fmt_optional_int(val: int | None) -> str:
    if val is None:
        return "—"
    return str(int(val))


def fmt_optional_str(val: str | None) -> str:
    text = str(val or "").strip()
    return text if text else "—"


def fmt_optional_bool(val: bool | None) -> str:
    if val is None:
        return "—"
    return "true" if val else "false"


def fmt_token_list(vals: list[str] | None) -> str:
    tokens = [str(v).strip() for v in (vals or []) if str(v).strip()]
    return repr(tokens)


@dataclass
class EventLogContext:
    wall_time: str
    camera_id: str
    run_id: str
    source: str
    video_time: str
    video_sec: float
    frame_idx: int
    latency_ms: Any


def event_log_context_from_pose(pose: dict[str, Any], video_fps: float) -> EventLogContext:
    vsec, vtext = resolve_log_video_time(pose, video_fps)
    return EventLogContext(
        wall_time=wall_time_str(),
        camera_id=str(pose.get("camera_id") or ""),
        run_id=str(pose.get("run_id") or "").strip(),
        source=str(pose.get("source_mode") or "stream"),
        video_time=vtext,
        video_sec=vsec,
        frame_idx=int(pose.get("frame_idx") or 0),
        latency_ms=pose.get("latency_ms") or {},
    )


@dataclass
class PrefilterLogEntry:
    decision: PrefilterDecision
    hits: list[str]


def format_event_log_line(
    category: str,
    tag: str,
    ctx: EventLogContext,
    *,
    track: int | None = None,
    hits: list[str] | None = None,
    alarms: list[str] | None = None,
    speed_feature: str | None = None,
    speed_value: float | None = None,
    speed_threshold: float | None = None,
    ankle_max_speed: float | None = None,
    ankle_max_speed_norm: float | None = None,
    filtered: bool | None = None,
) -> str:
    """统一字段顺序：time / run_id / camera / source / video_* / frame / track / hits / alarms / latency_ms / 门控字段。"""
    return (
        f"[{category}][{tag}] time={ctx.wall_time} "
        f"run_id={fmt_optional_str(ctx.run_id)} camera={ctx.camera_id} source={ctx.source} "
        f"video_time={ctx.video_time} video_sec={ctx.video_sec:.3f} frame={ctx.frame_idx} "
        f"track={fmt_optional_int(track)} hits={fmt_token_list(hits)} alarms={fmt_token_list(alarms)} "
        f"latency_ms={ctx.latency_ms} "
        f"speed_feature={fmt_optional_str(speed_feature)} speed_value={fmt_optional_float(speed_value)} "
        f"threshold={fmt_optional_float(speed_threshold)} "
        f"ankle_max_speed={fmt_optional_float(ankle_max_speed)} "
        f"ankle_max_speed_norm={fmt_optional_float(ankle_max_speed_norm)} "
        f"filtered={fmt_optional_bool(filtered)}"
    )

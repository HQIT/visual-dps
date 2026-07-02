"""全链路延迟追踪：随 pose/event 帧传递，UI 侧写 JSONL。"""

from __future__ import annotations

import os
import time
from typing import Any

TRACE_SCHEMA = 1


def enabled() -> bool:
    return os.environ.get("PIPELINE_LATENCY_TRACE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def sample_every() -> int:
    raw = os.environ.get("PIPELINE_LATENCY_SAMPLE", "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def sample_hit(frame_idx: int) -> bool:
    if not enabled():
        return False
    fi = int(frame_idx or 0)
    if fi <= 0:
        return True
    return (fi % sample_every()) == 0


def new_trace(
    camera_id: str,
    frame_idx: int,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": TRACE_SCHEMA,
        "camera_id": str(camera_id),
        "frame_idx": int(frame_idx),
        "run_id": run_id or "",
        "t": {},
        "latency_ms": {},
        "meta": {},
    }


def stamp(trace: dict[str, Any] | None, key: str, at: float | None = None) -> dict[str, Any] | None:
    if trace is None:
        return None
    tmap = trace.setdefault("t", {})
    tmap[key] = float(at if at is not None else time.time())
    return trace


def set_captured_at(trace: dict[str, Any] | None, captured_at: float) -> dict[str, Any] | None:
    if trace is None or captured_at <= 0:
        return trace
    return stamp(trace, "rtsp_captured", at=captured_at)


def merge_timeline(dst: dict[str, Any] | None, src: dict[str, Any] | None) -> dict[str, Any] | None:
    if dst is None:
        return src
    if src is None:
        return dst
    src_t = src.get("t") if isinstance(src.get("t"), dict) else {}
    if src_t:
        dst_t = dst.setdefault("t", {})
        for key, value in src_t.items():
            if key not in dst_t and value is not None:
                dst_t[key] = value
    src_meta = src.get("meta") if isinstance(src.get("meta"), dict) else {}
    if src_meta:
        dst_meta = dst.setdefault("meta", {})
        for key, value in src_meta.items():
            if key not in dst_meta:
                dst_meta[key] = value
    return dst


def extract_trace(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    tr = payload.get("latency_trace")
    return tr if isinstance(tr, dict) else None


def _diff_ms(t: dict[str, Any], start: str, end: str) -> float | None:
    a = t.get(start)
    b = t.get(end)
    if a is None or b is None:
        return None
    try:
        return round((float(b) - float(a)) * 1000.0, 1)
    except (TypeError, ValueError):
        return None


def finalize(trace: dict[str, Any] | None, *, meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if trace is None:
        return None
    t = trace.get("t") if isinstance(trace.get("t"), dict) else {}
    latency: dict[str, float] = {}
    for name, start, end in (
        ("snap_to_infer", "rtsp_captured", "infer_snap"),
        ("infer_to_det_done", "infer_snap", "det_done"),
        ("det_to_pose_done", "det_start", "pose_done"),
        ("snap_to_pose", "rtsp_captured", "pose_done"),
        ("infer_loop", "infer_snap", "pose_done"),
        ("pose_to_worker", "pose_published", "worker_received"),
        ("worker_process", "worker_received", "worker_done"),
        ("worker_to_event", "worker_done", "event_published"),
        ("event_to_merge", "event_published", "livehub_merged"),
        ("pose_to_merge", "pose_published", "livehub_merged"),
        ("snap_to_merge", "rtsp_captured", "livehub_merged"),
    ):
        value = _diff_ms(t, start, end)
        if value is not None:
            latency[name] = value
    if meta:
        trace.setdefault("meta", {}).update(meta)
    trace["latency_ms"] = latency
    return trace


def skeleton_source_for_merge(
    pose: dict[str, Any],
    event: dict[str, Any],
) -> tuple[list, str]:
    """按 frame_idx 选骨架，避免 event 时间戳更新但帧号滞后导致「人不跟画」。"""
    pose_skeletons = list(pose.get("persons") or pose.get("skeletons") or [])
    event_skeletons = list(event.get("skeletons") or [])
    pose_fi = int(pose.get("frame_idx") or 0)
    event_fi = int(event.get("frame_idx") or 0)

    if pose_skeletons and event_skeletons:
        if pose_fi >= event_fi:
            return pose_skeletons, "pose"
        return event_skeletons, "event"
    if pose_skeletons:
        return pose_skeletons, "pose"
    if event_skeletons:
        return event_skeletons, "event"
    return [], "none"


def merge_meta_for_live(pose: dict[str, Any], event: dict[str, Any], skeleton_source: str) -> dict[str, Any]:
    pose_fi = int(pose.get("frame_idx") or 0)
    event_fi = int(event.get("frame_idx") or 0)
    return {
        "pose_frame_idx": pose_fi,
        "event_frame_idx": event_fi,
        "pose_ts": float(pose.get("ts") or 0),
        "event_ts": float(event.get("ts") or 0),
        "persons": len(pose.get("persons") or pose.get("skeletons") or []),
        "skeleton_source": skeleton_source,
        "frame_idx_delta": pose_fi - event_fi,
        "infer_width": int(pose.get("infer_width") or 0),
        "infer_height": int(pose.get("infer_height") or 0),
    }


def build_live_trace(pose: dict[str, Any] | None, event: dict[str, Any] | None) -> dict[str, Any] | None:
    pose = pose if isinstance(pose, dict) else {}
    event = event if isinstance(event, dict) else {}
    frame_idx = int(pose.get("frame_idx") or event.get("frame_idx") or 0)
    if not sample_hit(frame_idx):
        return None
    camera_id = str(pose.get("camera_id") or event.get("camera_id") or "").strip()
    if not camera_id:
        return None

    trace = extract_trace(pose) or new_trace(camera_id, frame_idx)
    trace = merge_timeline(trace, extract_trace(event)) or trace
    trace["camera_id"] = camera_id
    trace["frame_idx"] = frame_idx
    _, skeleton_source = skeleton_source_for_merge(pose, event)
    meta = merge_meta_for_live(pose, event, skeleton_source)
    stamp(trace, "livehub_merged")
    return finalize(trace, meta=meta)


def sse_payload_enabled() -> bool:
    if not enabled():
        return False
    raw = os.environ.get("PIPELINE_LATENCY_SSE_PAYLOAD", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def compact_for_sse(trace: dict[str, Any] | None) -> dict[str, Any] | None:
    if trace is None:
        return None
    return {
        "schema": trace.get("schema", TRACE_SCHEMA),
        "frame_idx": trace.get("frame_idx"),
        "latency_ms": trace.get("latency_ms") or {},
        "meta": trace.get("meta") or {},
    }

"""视频时间轴工具（离线评测 / worker 日志）。"""

from __future__ import annotations


def format_video_time(sec: float | None) -> str:
    if sec is None or sec < 0:
        return "--:--.---"
    total = float(sec)
    m, s = divmod(int(total), 60)
    ms = int(round((total - int(total)) * 1000))
    return f"{m:02d}:{s:02d}.{ms:03d}"


def frame_index_to_sec(frame_count: int, video_fps: float) -> float:
    """将 1-based 帧序号转为视频内秒数（首帧 = 0s）。"""
    fps = float(video_fps or 25.0)
    if fps <= 0:
        fps = 25.0
    idx = max(1, int(frame_count or 1))
    return float(idx - 1) / fps


def media_time_from_capture(
    pos_msec: float,
    pos_frames: int,
    video_fps: float,
    frame_idx: int,
) -> float:
    """file 模式：读帧同线程捕获的媒体位置 → 与浏览器 currentTime 对齐。

    不可在 async 推理结束后再 cap.get(POS_MSEC)，否则 demux 可能已前进 ~10s。
    """
    fps = float(video_fps or 25.0)
    if fps <= 0:
        fps = 25.0
    pm = float(pos_msec or 0.0)
    if pm > 0:
        return pm / 1000.0
    pf = int(pos_frames or 0)
    if pf > 0:
        return max(0.0, float(pf - 1) / fps)
    return frame_index_to_sec(frame_idx, fps)


def compute_video_time_sec(
    frame_count: int,
    video_fps: float,
    pos_msec: float = 0.0,
    pos_frames: int = 0,
) -> float:
    return media_time_from_capture(pos_msec, pos_frames, video_fps, frame_count)


def resolve_video_time(pose: dict, fallback_fps: float = 15.0) -> tuple[float, str]:
    """从 pose 帧解析视频内秒数（优先落库 video_time_sec）。"""
    if not isinstance(pose, dict):
        return 0.0, format_video_time(0.0)
    vts = pose.get("video_time_sec")
    if vts is not None:
        try:
            sec = float(vts)
            return sec, format_video_time(sec)
        except (TypeError, ValueError):
            pass
    frame_idx = int(pose.get("frame_idx") or 0)
    fps = float(pose.get("video_fps") or fallback_fps or 15.0)
    sec = frame_index_to_sec(frame_idx, fps) if frame_idx > 0 else 0.0
    return sec, format_video_time(sec)

"""关键点速度：中值窗平滑坐标后再差分，抑制 17 点抖动。"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from pick_state.features.geometry import ANKLE_LEFT, ANKLE_RIGHT, read_kpt

MEDIAN_FILTER_WINDOW = 3
MAX_VELOCITY_GAP_FRAMES = 2
FOOT_ANKLE_INDICES = (ANKLE_LEFT, ANKLE_RIGHT)


@dataclass
class _KptBuffer:
    positions: list[tuple[float, float]] = field(default_factory=list)
    prev_filtered: tuple[float, float] | None = None
    prev_frame_idx: int | None = None
    prev_ts: float = 0.0


class VelocityTracker:
    """按 (track_id, kpt_idx) 维护位置历史，输出速度与归一化速度。"""

    def __init__(self, *, infer_width: int, infer_height: int, video_fps: float = 15.0):
        self.diag = math.hypot(max(1, infer_width), max(1, infer_height))
        self.fps = max(1.0, float(video_fps))
        self._buffers: dict[tuple[int, int], _KptBuffer] = {}

    def reset(self) -> None:
        self._buffers.clear()

    def _speed(
        self, buf: _KptBuffer, x: float, y: float, *, frame_idx: int, ts: float
    ) -> tuple[float | None, float | None]:
        if buf.prev_frame_idx is not None and frame_idx - buf.prev_frame_idx > MAX_VELOCITY_GAP_FRAMES:
            buf.prev_filtered = None
            buf.prev_frame_idx = None

        buf.positions.append((x, y))
        recent = buf.positions[-MEDIAN_FILTER_WINDOW:]
        fx = float(statistics.median([p[0] for p in recent]))
        fy = float(statistics.median([p[1] for p in recent]))

        speed: float | None = None
        speed_norm: float | None = None
        if buf.prev_filtered is not None and buf.prev_frame_idx is not None:
            dt = ts - buf.prev_ts
            if dt <= 0:
                dt = (frame_idx - buf.prev_frame_idx) / self.fps
            if dt > 0:
                vx = (fx - buf.prev_filtered[0]) / dt
                vy = (fy - buf.prev_filtered[1]) / dt
                speed = math.hypot(vx, vy)
                speed_norm = speed / self.diag

        buf.prev_filtered = (fx, fy)
        buf.prev_frame_idx = frame_idx
        buf.prev_ts = ts
        return speed, speed_norm

    def update(
        self, person: dict[str, Any], *, track_id: int, frame_idx: int, timestamp_sec: float
    ) -> dict[str, float | bool | None]:
        ankle_speeds: list[float] = []
        ankle_norms: list[float] = []

        for kpt_idx in FOOT_ANKLE_INDICES:
            pt = read_kpt(person, kpt_idx)
            if pt is None:
                continue
            buf = self._buffers.setdefault((track_id, kpt_idx), _KptBuffer())
            speed, speed_norm = self._speed(
                buf, pt[0], pt[1], frame_idx=frame_idx, ts=timestamp_sec
            )
            if speed is not None:
                ankle_speeds.append(speed)
            if speed_norm is not None:
                ankle_norms.append(speed_norm)

        return {
            "ankle_max_speed": round(max(ankle_speeds), 3) if ankle_speeds else None,
            "ankle_max_speed_norm": round(max(ankle_norms), 6) if ankle_norms else None,
            "velocity_valid": bool(ankle_norms),
        }

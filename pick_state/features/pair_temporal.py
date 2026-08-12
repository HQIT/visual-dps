"""(人, 货框) 对的时序特征：停留、趋近、手腕速度。

有状态，必须**逐帧**喂——包括没有任何命中的帧，否则停留计数不会清零，
训练与线上口径会错开。时间差一律用 `frame_idx` 差 / fps，不用时间戳，
保证离线构数据集和线上 runner 完全一致。
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

WINDOW_FRAMES = 15
DWELL_CAP = 30
MAX_GAP_FRAMES = 4  # 超过这个间隔视为新的一次接触，不算差分

TEMPORAL_FEATURE_KEYS = [
    "dwell_frames_norm",
    "dwell_ratio_win",
    "depth_ratio_delta",
    "center_dist_delta",
    "wrist_speed_norm",
    "wrist_speed_win_mean",
]


@dataclass
class _PairState:
    consecutive: int = 0
    history: deque[int] = field(default_factory=lambda: deque(maxlen=WINDOW_FRAMES))
    speeds: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW_FRAMES))
    prev_depth_ratio: float | None = None
    prev_center_dist: float | None = None
    prev_wrist: tuple[float, float] | None = None
    prev_frame_idx: int | None = None


class PairTemporalTracker:
    def __init__(
        self,
        *,
        infer_width: int,
        infer_height: int,
        video_fps: float = 15.0,
        window_frames: int = WINDOW_FRAMES,
        dwell_cap: int = DWELL_CAP,
    ):
        self.diag = math.hypot(max(1, infer_width), max(1, infer_height))
        self.fps = max(1.0, float(video_fps))
        self.window_frames = max(1, int(window_frames))
        self.dwell_cap = max(1, int(dwell_cap))
        self._states: dict[str, _PairState] = {}

    def reset(self) -> None:
        self._states.clear()

    def _state(self, key: str) -> _PairState:
        st = self._states.get(key)
        if st is None:
            st = _PairState(
                history=deque(maxlen=self.window_frames),
                speeds=deque(maxlen=self.window_frames),
            )
            self._states[key] = st
        return st

    def update(
        self, frame_idx: int, active: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, float]]:
        """active: {key: {depth_ratio, center_dist_norm, wrist_xy}}；返回每个 active key 的特征。"""
        for key, st in self._states.items():
            if key not in active:
                st.consecutive = 0
                st.history.append(0)
                st.prev_depth_ratio = None
                st.prev_center_dist = None
                st.prev_wrist = None
                st.prev_frame_idx = None

        out: dict[str, dict[str, float]] = {}
        for key, cur in active.items():
            st = self._state(key)
            gap = (
                frame_idx - st.prev_frame_idx if st.prev_frame_idx is not None else None
            )
            continuous = gap is not None and 0 < gap <= MAX_GAP_FRAMES
            if not continuous:
                st.prev_depth_ratio = None
                st.prev_center_dist = None
                st.prev_wrist = None

            st.consecutive += 1
            st.history.append(1)

            depth_ratio = float(cur.get("depth_ratio") or 0.0)
            center_dist = float(cur.get("center_dist_norm") or 0.0)
            wx, wy = cur.get("wrist_xy") or (0.0, 0.0)

            dt = (gap / self.fps) if continuous and gap else (1.0 / self.fps)
            speed = 0.0
            if st.prev_wrist is not None and dt > 0:
                speed = math.hypot(wx - st.prev_wrist[0], wy - st.prev_wrist[1]) / dt / self.diag
            st.speeds.append(speed)

            out[key] = {
                "dwell_frames_norm": min(st.consecutive, self.dwell_cap) / self.dwell_cap,
                "dwell_ratio_win": sum(st.history) / self.window_frames,
                "depth_ratio_delta": (
                    depth_ratio - st.prev_depth_ratio if st.prev_depth_ratio is not None else 0.0
                ),
                "center_dist_delta": (
                    center_dist - st.prev_center_dist if st.prev_center_dist is not None else 0.0
                ),
                "wrist_speed_norm": speed,
                "wrist_speed_win_mean": sum(st.speeds) / len(st.speeds),
            }

            st.prev_depth_ratio = depth_ratio
            st.prev_center_dist = center_dist
            st.prev_wrist = (float(wx), float(wy))
            st.prev_frame_idx = frame_idx

        return out

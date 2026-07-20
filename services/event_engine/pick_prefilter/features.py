"""流式聚合速度特征（生产化：dt 优先 pose.ts，墙钟 gap reset）。"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any

from services.event_engine.pick_prefilter.angles import KPT_SCORE_MIN, _read_kpt

KPT_COUNT = 17
UPPER_KPT_INDICES = (5, 6, 7, 8, 9, 10)
LOWER_KPT_INDICES = (11, 12, 13, 14, 15, 16)
KNEE_ANKLE_KPT_INDICES = (13, 14, 15, 16)
FOOT_ANKLE_INDICES = (15, 16)
TORSO_SHOULDER_INDICES = (5, 6)
TORSO_HIP_INDICES = (11, 12)
WRIST_INDICES = (9, 10)

MEDIAN_FILTER_WINDOW = 3


@dataclass
class _KptState:
    frame_idx: int = 0
    timestamp_sec: float = 0.0
    x: float = 0.0
    y: float = 0.0
    score: float = 0.0


@dataclass
class _TrackKptBuffer:
    positions: list[tuple[float, float, float, int, float]] = field(default_factory=list)
    prev_filtered: tuple[float, float] | None = None
    prev_meta: _KptState | None = None


@dataclass
class AggregateVelocitySnapshot:
    ankle_max_speed: float | None = None
    ankle_max_speed_norm: float | None = None
    velocity_valid: bool = False


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _filtered_xy(buffer: _TrackKptBuffer) -> tuple[float, float] | None:
    recent = buffer.positions[-MEDIAN_FILTER_WINDOW:]
    if not recent:
        return None
    mx = _median([p[0] for p in recent])
    my = _median([p[1] for p in recent])
    if mx is None or my is None:
        return None
    return mx, my


def _compute_speed(
    x: float,
    y: float,
    prev: _KptState,
    *,
    frame_idx: int,
    ts: float,
    diag: float,
    video_fps: float,
) -> dict[str, Any]:
    dt = ts - prev.timestamp_sec
    if dt <= 0:
        dt = (frame_idx - prev.frame_idx) / max(1.0, video_fps)
    if dt <= 0:
        return {"speed": None, "speed_norm": None, "velocity_valid": False}
    vx = (x - prev.x) / dt
    vy = (y - prev.y) / dt
    speed = math.hypot(vx, vy)
    return {
        "speed": round(speed, 3),
        "speed_norm": round(speed / diag, 6),
        "velocity_valid": True,
    }


def _torso_xy(person: dict[str, Any]) -> tuple[float, float, float] | None:
    shoulders: list[tuple[float, float, float]] = []
    for idx in TORSO_SHOULDER_INDICES:
        pt = _read_kpt(person, idx)
        if pt is not None:
            shoulders.append(pt)
    if len(shoulders) >= 2:
        xs = [p[0] for p in shoulders]
        ys = [p[1] for p in shoulders]
        scores = [p[2] for p in shoulders]
        return sum(xs) / 2.0, sum(ys) / 2.0, min(scores)
    if len(shoulders) == 1:
        return shoulders[0]

    hips: list[tuple[float, float, float]] = []
    for idx in TORSO_HIP_INDICES:
        pt = _read_kpt(person, idx)
        if pt is not None:
            hips.append(pt)
    if len(hips) >= 2:
        xs = [p[0] for p in hips]
        ys = [p[1] for p in hips]
        scores = [p[2] for p in hips]
        return sum(xs) / 2.0, sum(ys) / 2.0, min(scores)
    if len(hips) == 1:
        return hips[0]
    return None


def _max_of_speeds(speeds: list[float | None]) -> float | None:
    valid = [float(s) for s in speeds if s is not None and math.isfinite(float(s))]
    if not valid:
        return None
    return max(valid)


def _should_reset_buffer(buf: _TrackKptBuffer, *, ts: float, max_pose_gap_sec: float) -> bool:
    if buf.prev_meta is None or max_pose_gap_sec <= 0:
        return False
    prev_ts = float(buf.prev_meta.timestamp_sec or 0.0)
    if prev_ts <= 0 or ts <= 0:
        return False
    return (ts - prev_ts) > max_pose_gap_sec


class IncrementalAggregateVelocityTracker:
    """按 track 增量维护聚合速度；生产环境用墙钟 gap reset。"""

    def __init__(
        self,
        *,
        infer_width: int,
        infer_height: int,
        video_fps: float = 15.0,
        max_pose_gap_sec: float = 0.0,
    ):
        self.infer_width = max(1, int(infer_width))
        self.infer_height = max(1, int(infer_height))
        self.diag = math.hypot(self.infer_width, self.infer_height)
        self.video_fps = max(1.0, float(video_fps))
        self.max_pose_gap_sec = max(0.0, float(max_pose_gap_sec))
        self._kpt_buffers: dict[tuple[int, int], _TrackKptBuffer] = {}
        self._torso_buffers: dict[int, _TrackKptBuffer] = {}

    def set_infer_size(self, infer_width: int, infer_height: int) -> None:
        w = max(1, int(infer_width))
        h = max(1, int(infer_height))
        if w == self.infer_width and h == self.infer_height:
            return
        self.infer_width = w
        self.infer_height = h
        self.diag = math.hypot(w, h)
        self.reset_session()

    def set_max_pose_gap_sec(self, max_pose_gap_sec: float) -> None:
        self.max_pose_gap_sec = max(0.0, float(max_pose_gap_sec))

    def reset_session(self) -> None:
        self._kpt_buffers.clear()
        self._torso_buffers.clear()

    def remove_track(self, track_id: int) -> None:
        dead = [k for k in self._kpt_buffers if k[0] == track_id]
        for k in dead:
            self._kpt_buffers.pop(k, None)
        self._torso_buffers.pop(track_id, None)

    def update_for_person(
        self,
        track_id: int,
        person: dict[str, Any],
        *,
        frame_idx: int,
        timestamp_sec: float,
    ) -> AggregateVelocitySnapshot:
        ts = float(timestamp_sec or 0.0)
        if ts <= 0 and frame_idx > 0:
            ts = frame_idx / self.video_fps

        kpt_speeds: dict[int, float | None] = {}
        kpt_speeds_norm: dict[int, float | None] = {}
        for kpt_idx in range(KPT_COUNT):
            key = (track_id, kpt_idx)
            buf = self._kpt_buffers.setdefault(key, _TrackKptBuffer())
            pt = _read_kpt(person, kpt_idx)
            if pt is None:
                kpt_speeds[kpt_idx] = None
                kpt_speeds_norm[kpt_idx] = None
                continue

            x, y, score = pt
            if _should_reset_buffer(buf, ts=ts, max_pose_gap_sec=self.max_pose_gap_sec):
                buf.prev_filtered = None
                buf.prev_meta = None
                buf.positions.clear()

            buf.positions.append((x, y, score, frame_idx, ts))
            filtered = _filtered_xy(buf)
            if filtered is None:
                kpt_speeds[kpt_idx] = None
                kpt_speeds_norm[kpt_idx] = None
                continue

            fx, fy = filtered
            speed_val: float | None = None
            speed_norm_val: float | None = None
            if buf.prev_filtered is not None and buf.prev_meta is not None:
                prev_state = _KptState(
                    frame_idx=buf.prev_meta.frame_idx,
                    timestamp_sec=buf.prev_meta.timestamp_sec,
                    x=buf.prev_filtered[0],
                    y=buf.prev_filtered[1],
                )
                vel = _compute_speed(
                    fx,
                    fy,
                    prev_state,
                    frame_idx=frame_idx,
                    ts=ts,
                    diag=self.diag,
                    video_fps=self.video_fps,
                )
                if vel["velocity_valid"]:
                    speed_val = vel["speed"]
                    speed_norm_val = vel["speed_norm"]
            kpt_speeds[kpt_idx] = speed_val
            kpt_speeds_norm[kpt_idx] = speed_norm_val
            buf.prev_filtered = (fx, fy)
            buf.prev_meta = _KptState(frame_idx=frame_idx, timestamp_sec=ts, x=fx, y=fy, score=score)

        torso_buf = self._torso_buffers.setdefault(track_id, _TrackKptBuffer())
        torso_pt = _torso_xy(person)
        torso_valid = False
        if torso_pt is not None:
            tx, ty, tscore = torso_pt
            if _should_reset_buffer(torso_buf, ts=ts, max_pose_gap_sec=self.max_pose_gap_sec):
                torso_buf.prev_filtered = None
                torso_buf.prev_meta = None
                torso_buf.positions.clear()

            torso_buf.positions.append((tx, ty, tscore, frame_idx, ts))
            t_filtered = _filtered_xy(torso_buf)
            if t_filtered is not None:
                tfx, tfy = t_filtered
                if torso_buf.prev_filtered is not None and torso_buf.prev_meta is not None:
                    prev_state = _KptState(
                        frame_idx=torso_buf.prev_meta.frame_idx,
                        timestamp_sec=torso_buf.prev_meta.timestamp_sec,
                        x=torso_buf.prev_filtered[0],
                        y=torso_buf.prev_filtered[1],
                    )
                    vel = _compute_speed(
                        tfx,
                        tfy,
                        prev_state,
                        frame_idx=frame_idx,
                        ts=ts,
                        diag=self.diag,
                        video_fps=self.video_fps,
                    )
                    if vel["velocity_valid"]:
                        torso_valid = True
                torso_buf.prev_filtered = (tfx, tfy)
                torso_buf.prev_meta = _KptState(
                    frame_idx=frame_idx,
                    timestamp_sec=ts,
                    x=tfx,
                    y=tfy,
                    score=tscore,
                )

        ankle_speeds = [kpt_speeds.get(i) for i in FOOT_ANKLE_INDICES]
        ankle_norm_speeds = [kpt_speeds_norm.get(i) for i in FOOT_ANKLE_INDICES]
        ankle_max = _max_of_speeds(ankle_speeds)
        ankle_max_norm = _max_of_speeds(ankle_norm_speeds)
        has_ankle = ankle_max is not None or ankle_max_norm is not None
        return AggregateVelocitySnapshot(
            ankle_max_speed=round(ankle_max, 3) if ankle_max is not None else None,
            ankle_max_speed_norm=round(ankle_max_norm, 6) if ankle_max_norm is not None else None,
            velocity_valid=has_ankle or torso_valid,
        )

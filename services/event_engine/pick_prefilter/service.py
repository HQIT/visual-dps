"""碰撞前置门控服务：维护 tracker 状态并判定 should_block。"""

from __future__ import annotations

import time
from typing import Any

from services.event_engine.pick_prefilter.angles import compute_prefilter_angle_features
from services.event_engine.pick_prefilter.config import PickPrefilterConfig
from services.event_engine.pick_prefilter.decision import PrefilterDecision
from services.event_engine.pick_prefilter.features import IncrementalAggregateVelocityTracker
from services.event_engine.pick_prefilter.gate import evaluate_pick_prefilter_block


class PickPrefilterGate:
    """按摄像头维护速度 tracker；enabled 时由 CollisionProcessor 调用。"""

    STALE_SEC = 1.2

    def __init__(
        self,
        cfg: PickPrefilterConfig,
        *,
        infer_width: int,
        infer_height: int,
        video_fps: float,
        pose_frame_interval: int,
    ):
        self.cfg = cfg
        gap = cfg.resolve_max_pose_gap_sec(
            pose_frame_interval=pose_frame_interval,
            frame_rate=video_fps,
        )
        self._tracker = IncrementalAggregateVelocityTracker(
            infer_width=infer_width,
            infer_height=infer_height,
            video_fps=video_fps,
            max_pose_gap_sec=gap,
        )
        self._track_last_ts: dict[int, float] = {}

    @classmethod
    def from_config(
        cls,
        section: dict[str, Any],
        *,
        infer_width: int,
        infer_height: int,
        video_fps: float,
        pose_frame_interval: int,
    ) -> PickPrefilterGate:
        cfg = PickPrefilterConfig.from_section(section)
        return cls(
            cfg,
            infer_width=infer_width,
            infer_height=infer_height,
            video_fps=video_fps,
            pose_frame_interval=pose_frame_interval,
        )

    def apply_config(
        self,
        section: dict[str, Any],
        *,
        infer_width: int,
        infer_height: int,
        video_fps: float,
        pose_frame_interval: int,
    ) -> None:
        self.cfg = PickPrefilterConfig.from_section(section)
        self._tracker.set_infer_size(infer_width, infer_height)
        self._tracker.video_fps = max(1.0, float(video_fps))
        gap = self.cfg.resolve_max_pose_gap_sec(
            pose_frame_interval=pose_frame_interval,
            frame_rate=video_fps,
        )
        self._tracker.set_max_pose_gap_sec(gap)

    def reset_session(self) -> None:
        self._tracker.reset_session()
        self._track_last_ts.clear()

    def _resolve_timestamp(self, pose_frame: dict[str, Any], frame_idx: int) -> float:
        ts = float(pose_frame.get("ts") or 0.0)
        if ts <= 0:
            ts = float(pose_frame.get("timestamp_sec") or 0.0)
        if ts <= 0 and frame_idx > 0:
            ts = frame_idx / self._tracker.video_fps
        return ts

    def _cleanup_stale_tracks(self, now_ts: float) -> None:
        dead = [
            tid
            for tid, last_ts in self._track_last_ts.items()
            if now_ts - last_ts > self.STALE_SEC
        ]
        for tid in dead:
            self._track_last_ts.pop(tid, None)
            self._tracker.remove_track(tid)

    def evaluate(
        self,
        pose_frame: dict[str, Any],
        track_id: int,
        person: dict[str, Any],
    ) -> PrefilterDecision:
        if not self.cfg.enabled:
            return PrefilterDecision(
                blocked=False,
                track_id=track_id,
                speed_feature=self.cfg.speed_feature,
                speed_value=None,
                speed_threshold=self.cfg.speed_threshold,
            )

        frame_idx = int(pose_frame.get("frame_idx") or 0)
        ts = self._resolve_timestamp(pose_frame, frame_idx)
        if ts > 0:
            self._track_last_ts[track_id] = ts
            self._cleanup_stale_tracks(ts)
        else:
            self._track_last_ts[track_id] = time.time()
            self._cleanup_stale_tracks(time.time())

        snapshot = self._tracker.update_for_person(
            track_id,
            person,
            frame_idx=frame_idx,
            timestamp_sec=ts,
        )

        row: dict[str, Any] = {}
        row.update(compute_prefilter_angle_features(person))
        row["ankle_max_speed"] = snapshot.ankle_max_speed
        row["ankle_max_speed_norm"] = snapshot.ankle_max_speed_norm

        speed_raw = row.get(self.cfg.speed_feature)
        speed_value = float(speed_raw) if speed_raw is not None else None
        blocked = evaluate_pick_prefilter_block(row, self.cfg)
        return PrefilterDecision(
            blocked=blocked,
            track_id=track_id,
            speed_feature=self.cfg.speed_feature,
            speed_value=speed_value,
            speed_threshold=self.cfg.speed_threshold,
            ankle_max_speed=snapshot.ankle_max_speed,
            ankle_max_speed_norm=snapshot.ankle_max_speed_norm,
        )

    def should_block(self, pose_frame: dict[str, Any], track_id: int, person: dict[str, Any]) -> bool:
        return self.evaluate(pose_frame, track_id, person).blocked

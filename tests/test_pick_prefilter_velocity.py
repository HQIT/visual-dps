"""pick_prefilter 速度 tracker 单测（headless 语义：dt 用 ts）。"""

from __future__ import annotations

import math
import unittest

from services.event_engine.pick_prefilter.features import (
    IncrementalAggregateVelocityTracker,
    _KptState,
    _compute_speed,
)


def _person_with_ankles(lx: float, ly: float, rx: float, ry: float) -> dict:
    keypoints = [[0.0, 0.0, 0.0] for _ in range(17)]
    for idx, x, y in (
        (5, 200.0, 100.0),
        (6, 240.0, 100.0),
        (11, 210.0, 200.0),
        (12, 230.0, 200.0),
        (15, lx, ly),
        (16, rx, ry),
    ):
        keypoints[idx] = [x, y, 1.0]
    return {"keypoints": keypoints}


class PickPrefilterVelocityTests(unittest.TestCase):
    def test_compute_speed_prefers_wall_clock_dt(self):
        diag = math.hypot(852, 480)
        prev = _KptState(frame_idx=1, timestamp_sec=1000.0, x=400.0, y=400.0)
        vel = _compute_speed(
            410.0,
            400.0,
            prev,
            frame_idx=2,
            ts=1000.0 + (2.0 / 15.0),
            diag=diag,
            video_fps=15.0,
        )
        self.assertTrue(vel["velocity_valid"])
        self.assertAlmostEqual(float(vel["speed"]), 75.0, delta=0.1)
        self.assertAlmostEqual(float(vel["speed_norm"]), 75.0 / diag, delta=0.001)

    def test_compute_speed_frame_idx_fallback_overestimates_when_ts_missing(self):
        diag = math.hypot(852, 480)
        prev = _KptState(frame_idx=3, timestamp_sec=0.0, x=400.0, y=400.0)
        vel_short_dt = _compute_speed(
            410.0,
            400.0,
            prev,
            frame_idx=4,
            ts=0.0,
            diag=diag,
            video_fps=15.0,
        )
        vel_long_dt = _compute_speed(
            410.0,
            400.0,
            prev,
            frame_idx=4,
            ts=2.0 / 15.0,
            diag=diag,
            video_fps=15.0,
        )
        self.assertAlmostEqual(float(vel_short_dt["speed"]), 150.0, delta=0.1)
        self.assertAlmostEqual(float(vel_long_dt["speed"]), 75.0, delta=0.1)
        self.assertGreater(float(vel_short_dt["speed"]), float(vel_long_dt["speed"]) * 1.5)

    def test_tracker_pose_gap_resets_velocity_history(self):
        tracker = IncrementalAggregateVelocityTracker(
            infer_width=852,
            infer_height=480,
            video_fps=15.0,
            max_pose_gap_sec=0.2,
        )
        dt = 0.05
        person = _person_with_ankles(400.0, 400.0, 420.0, 400.0)
        for i in range(4):
            tracker.update_for_person(
                1,
                person,
                frame_idx=i + 1,
                timestamp_sec=1.0 + i * dt,
            )
        snap_after_gap = tracker.update_for_person(
            1,
            _person_with_ankles(410.0, 400.0, 430.0, 400.0),
            frame_idx=5,
            timestamp_sec=2.0,
        )
        self.assertIsNone(snap_after_gap.ankle_max_speed_norm)


if __name__ == "__main__":
    unittest.main()

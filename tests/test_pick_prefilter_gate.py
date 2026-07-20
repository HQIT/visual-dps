"""pick_prefilter 门控逻辑单测。"""

from __future__ import annotations

import unittest

from services.event_engine.pick_prefilter.config import PickPrefilterConfig
from services.event_engine.pick_prefilter.gate import evaluate_pick_prefilter_block


def _cfg(**kwargs) -> PickPrefilterConfig:
    base = PickPrefilterConfig(enabled=True)
    for k, v in kwargs.items():
        setattr(base, k, v)
    return base


class PickPrefilterGateTests(unittest.TestCase):
    def test_high_speed_no_triple_standing_blocks(self):
        row = {
            "ankle_max_speed_norm": 0.10,
            "arm_torso_angle_max": 50.0,
            "elbow_angle_mean": 120.0,
            "wrist_elevation_angle_max": 30.0,
            "shoulder_hip_knee_angle_min": 150.0,
        }
        self.assertTrue(evaluate_pick_prefilter_block(row, _cfg()))

    def test_high_speed_triple_exempt_allows(self):
        row = {
            "ankle_max_speed_norm": 0.10,
            "arm_torso_angle_max": 95.0,
            "elbow_angle_mean": 155.0,
            "wrist_elevation_angle_max": 65.0,
            "shoulder_hip_knee_angle_min": 150.0,
        }
        self.assertFalse(evaluate_pick_prefilter_block(row, _cfg()))

    def test_high_speed_crouching_allows(self):
        row = {
            "ankle_max_speed_norm": 0.10,
            "arm_torso_angle_max": 50.0,
            "elbow_angle_mean": 120.0,
            "wrist_elevation_angle_max": 30.0,
            "shoulder_hip_knee_angle_min": 100.0,
        }
        self.assertFalse(evaluate_pick_prefilter_block(row, _cfg()))

    def test_low_speed_allows(self):
        row = {
            "ankle_max_speed_norm": 0.05,
            "shoulder_hip_knee_angle_min": 150.0,
        }
        self.assertFalse(evaluate_pick_prefilter_block(row, _cfg()))

    def test_missing_stance_angle_blocks_when_high_speed(self):
        row = {
            "ankle_max_speed_norm": 0.10,
            "arm_torso_angle_max": 50.0,
            "elbow_angle_mean": 120.0,
            "wrist_elevation_angle_max": 30.0,
        }
        self.assertTrue(evaluate_pick_prefilter_block(row, _cfg()))


if __name__ == "__main__":
    unittest.main()

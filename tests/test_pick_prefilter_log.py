"""pick_prefilter 日志单测。"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from services.event_engine.collision import CollisionProcessor
from services.event_engine.pick_prefilter.decision import PrefilterDecision
from services.event_engine.pick_prefilter.log import log_prefilter_decision


class PrefilterLogTests(unittest.TestCase):
    def test_log_filtered_line(self):
        decision = PrefilterDecision(
            blocked=True,
            track_id=3,
            speed_feature="ankle_max_speed_norm",
            speed_value=0.092,
            speed_threshold=0.081770,
            ankle_max_speed=90.0,
            ankle_max_speed_norm=0.092,
        )
        pose = {
            "camera_id": "test_camera",
            "frame_idx": 1788,
            "source_mode": "stream",
        }
        buf = io.StringIO()
        with patch.dict(os.environ, {"PREFILTER_LOG": "1"}, clear=False):
            with redirect_stdout(buf):
                log_prefilter_decision(pose, decision, video_fps=15.0)
        line = buf.getvalue().strip()
        self.assertIn("[PREFILTER][FILTERED]", line)
        self.assertIn("ankle_max_speed_norm=0.092", line)
        self.assertIn("threshold=0.08177", line)
        self.assertIn("filtered=true", line)
        self.assertIn("track=3", line)

    def test_no_log_without_wrist_collision(self):
        """无手腕进框时不应输出 prefilter 日志（由 collision 层控制）。"""
        buf = io.StringIO()

        class _Gate:
            def evaluate(self, *_a, **_k):
                return PrefilterDecision(
                    blocked=False,
                    track_id=1,
                    speed_feature="ankle_max_speed_norm",
                    speed_value=0.2,
                    speed_threshold=0.081770,
                )

        proc = CollisionProcessor([], video_fps=15.0)
        pose = {
            "frame_idx": 10,
            "persons": [
                {
                    "keypoints": [[0, 0, 1] for _ in range(17)],
                }
            ],
        }
        with patch.dict(os.environ, {"PREFILTER_LOG": "1"}, clear=False):
            with redirect_stdout(buf):
                proc.process(pose, prefilter=_Gate())
        self.assertEqual(buf.getvalue().strip(), "")


if __name__ == "__main__":
    unittest.main()

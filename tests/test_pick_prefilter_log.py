"""pick_prefilter 日志单测（统一 event_log 格式）。"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from services.event_engine.event_log import (
    event_log_context_from_pose,
    format_event_log_line,
)
from services.event_engine.pick_prefilter.decision import PrefilterDecision
from services.event_engine.worker import EventRedisWorker


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
        ctx = event_log_context_from_pose(pose, video_fps=15.0)
        line = format_event_log_line(
            "PREFILTER",
            "FILTERED",
            ctx,
            track=decision.track_id,
            hits=["A01:3", "A01:5"],
            alarms=[],
            speed_feature=decision.speed_feature,
            speed_value=decision.speed_value,
            speed_threshold=decision.speed_threshold,
            ankle_max_speed=decision.ankle_max_speed,
            ankle_max_speed_norm=decision.ankle_max_speed_norm,
            filtered=decision.blocked,
        )
        self.assertIn("[PREFILTER][FILTERED]", line)
        self.assertIn("hits=['A01:3', 'A01:5']", line)
        self.assertIn("ankle_max_speed_norm=0.092", line)
        self.assertIn("threshold=0.08177", line)
        self.assertIn("filtered=true", line)
        self.assertIn("track=3", line)
        self.assertIn("alarms=[]", line)

    def test_worker_emits_prefilter_only_when_enabled(self):
        decision = PrefilterDecision(
            blocked=True,
            track_id=2,
            speed_feature="ankle_max_speed_norm",
            speed_value=0.2,
            speed_threshold=0.081770,
        )
        worker = EventRedisWorker({"paths": {"json_dir": "localdata/json"}})
        pose = {"camera_id": "cam1", "frame_idx": 5, "source_mode": "stream"}
        buf = io.StringIO()
        with patch.dict(os.environ, {"PREFILTER_LOG": "1", "COLLISION_LOG": "0"}, clear=False):
            with redirect_stdout(buf):
                from services.event_engine.event_log import PrefilterLogEntry

                worker._log_event_frame(
                    pose,
                    collisions=[],
                    alarm_collisions=[],
                    prefilter_logs=[
                        PrefilterLogEntry(decision=decision, hits=["Box_1"]),
                    ],
                )
        line = buf.getvalue().strip()
        self.assertIn("[PREFILTER][FILTERED]", line)
        self.assertIn("hits=['Box_1']", line)
        self.assertNotIn("[COLLISION]", line)


if __name__ == "__main__":
    unittest.main()

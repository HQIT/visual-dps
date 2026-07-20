"""Event Worker 统一终端日志单测。"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from services.event_engine.collision import CollisionProcessor
from services.event_engine.event_log import (
    EventLogContext,
    PrefilterLogEntry,
    event_log_context_from_pose,
    format_event_log_line,
)
from services.event_engine.pick_prefilter.decision import PrefilterDecision
from services.event_engine.worker import EventRedisWorker


class EventLogFormatTests(unittest.TestCase):
    def test_unified_prefilter_line_has_all_fields(self):
        ctx = EventLogContext(
            wall_time="2026-07-20 15:00:00.123",
            camera_id="test_camera",
            run_id="",
            source="stream",
            video_time="01:59.200",
            video_sec=119.2,
            frame_idx=1788,
            latency_ms={"infer": 12},
        )
        line = format_event_log_line(
            "PREFILTER",
            "FILTERED",
            ctx,
            track=3,
            hits=["A01:3"],
            alarms=[],
            speed_feature="ankle_max_speed_norm",
            speed_value=0.092,
            speed_threshold=0.081770,
            ankle_max_speed=90.0,
            ankle_max_speed_norm=0.092,
            filtered=True,
        )
        self.assertIn("[PREFILTER][FILTERED]", line)
        self.assertIn("run_id=—", line)
        self.assertIn("hits=['A01:3']", line)
        self.assertIn("alarms=[]", line)
        self.assertIn("latency_ms={'infer': 12}", line)
        self.assertIn("speed_feature=ankle_max_speed_norm", line)
        self.assertIn("speed_value=0.092", line)
        self.assertIn("threshold=0.08177", line)
        self.assertIn("ankle_max_speed=90", line)
        self.assertIn("ankle_max_speed_norm=0.092", line)
        self.assertIn("filtered=true", line)

    def test_unified_collision_line_has_all_fields(self):
        pose = {
            "camera_id": "cam1",
            "frame_idx": 100,
            "source_mode": "stream",
            "latency_ms": {},
        }
        ctx = event_log_context_from_pose(pose, video_fps=15.0)
        line = format_event_log_line(
            "COLLISION",
            "HIT",
            ctx,
            hits=["shelf1:2"],
            alarms=[],
        )
        self.assertIn("[COLLISION][HIT]", line)
        self.assertIn("hits=['shelf1:2']", line)
        self.assertIn("alarms=[]", line)
        self.assertIn("track=—", line)
        self.assertIn("speed_feature=—", line)
        self.assertIn("filtered=—", line)

    def test_worker_logs_prefilter_and_collision_with_same_fields(self):
        worker = EventRedisWorker({"paths": {"json_dir": "localdata/json"}})
        pose = {
            "camera_id": "test_camera",
            "frame_idx": 10,
            "source_mode": "stream",
            "latency_ms": {"total": 8},
        }
        decision = PrefilterDecision(
            blocked=False,
            track_id=1,
            speed_feature="ankle_max_speed_norm",
            speed_value=0.05,
            speed_threshold=0.081770,
            ankle_max_speed=40.0,
            ankle_max_speed_norm=0.05,
        )
        buf = io.StringIO()
        with patch.dict(os.environ, {"COLLISION_LOG": "1"}, clear=False):
            with redirect_stdout(buf):
                worker._log_event_frame(
                    pose,
                    collisions=["A01:1"],
                    alarm_collisions=[],
                    prefilter_logs=[PrefilterLogEntry(decision=decision, hits=["A01:1"])],
                )
        lines = [ln for ln in buf.getvalue().strip().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("[PREFILTER][PASS]"))
        self.assertTrue(lines[1].startswith("[COLLISION][HIT]"))
        for key in (
            "run_id=",
            "camera=test_camera",
            "hits=['A01:1']",
            "alarms=[]",
            "latency_ms={'total': 8}",
            "speed_feature=",
            "filtered=",
        ):
            self.assertIn(key, lines[0])
            self.assertIn(key, lines[1])

    def test_collision_processor_collects_prefilter_logs_without_print(self):
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
                result = proc.process(pose, prefilter=_Gate())
        self.assertEqual(buf.getvalue().strip(), "")
        self.assertEqual(result.get("prefilter_logs"), [])


if __name__ == "__main__":
    unittest.main()

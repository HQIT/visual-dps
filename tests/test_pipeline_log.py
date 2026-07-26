"""流水线 logging 模块单测。"""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from unittest.mock import patch

from services import pipeline_log


def _reset_pipeline_logger() -> None:
    pipeline_log._configured = False
    pipeline_log._config_loaded = False
    logger = logging.getLogger(pipeline_log._LOGGER_NAME)
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)


class PipelineLogTests(unittest.TestCase):
    def setUp(self):
        _reset_pipeline_logger()

    def tearDown(self):
        _reset_pipeline_logger()

    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            pipeline_log.apply_pipeline_log_config({"pipeline_log": {"enabled": False}})
            self.assertFalse(pipeline_log.pipeline_log_enabled())
            pipeline_log.log_pipeline_stage("pose_published", camera_id="cam1", frame_idx=1)
            self.assertEqual(logging.getLogger(pipeline_log._LOGGER_NAME).handlers, [])

    def test_config_enables_file_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_config = {
                "pipeline_log": {
                    "enabled": True,
                    "file_enabled": True,
                    "stdout": False,
                    "dir": tmp,
                    "sample": 1,
                }
            }
            with patch.dict(os.environ, {}, clear=True):
                pipeline_log.apply_pipeline_log_config(app_config)
                pipeline_log.configure_pipeline_logger(role="worker")
                pipeline_log.log_pipeline_stage(
                    "worker_received",
                    camera_id="cam1",
                    frame_idx=10,
                    persons=2,
                )
                _reset_pipeline_logger()
                log_path = os.path.join(tmp, "worker.log")
                self.assertTrue(os.path.isfile(log_path))
                content = open(log_path, encoding="utf-8").read()
                self.assertIn("[PIPELINE]", content)
                self.assertIn("stage=worker_received", content)

    def test_env_overrides_config(self):
        app_config = {"pipeline_log": {"enabled": False, "file_enabled": False}}
        with patch.dict(os.environ, {"PIPELINE_LOG": "1"}, clear=True):
            pipeline_log.apply_pipeline_log_config(app_config)
            self.assertTrue(pipeline_log.pipeline_log_enabled())

    def test_sample_skips_non_hit_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_config = {
                "pipeline_log": {
                    "enabled": True,
                    "file_enabled": True,
                    "stdout": False,
                    "dir": tmp,
                    "sample": 30,
                }
            }
            with patch.dict(os.environ, {}, clear=True):
                pipeline_log.apply_pipeline_log_config(app_config)
                pipeline_log.configure_pipeline_logger(role="infer_cam1")
                pipeline_log.log_pipeline_stage("rtsp_frame", camera_id="cam1", frame_idx=29)
                log_path = os.path.join(tmp, "infer_cam1.log")
                if os.path.isfile(log_path):
                    self.assertEqual(open(log_path, encoding="utf-8").read(), "")
                pipeline_log.log_pipeline_stage("rtsp_frame", camera_id="cam1", frame_idx=30)
                _reset_pipeline_logger()
                self.assertTrue(os.path.isfile(log_path))
                self.assertIn("stage=rtsp_frame", open(log_path, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()

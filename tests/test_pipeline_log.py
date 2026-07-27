"""流水线 logging 模块单测。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from unittest.mock import patch

from services import pipeline_log


def _reset_process_logging() -> None:
    pipeline_log._configured = False
    pipeline_log._config_loaded = False
    pipeline_log._file_handler_attached = False
    pipeline_log._active_file_config_key = None
    pipeline_log._invalidate_camera_pipeline_cache()
    for name in pipeline_log._ALL_LOGGER_NAMES:
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)


def _write_inference_status(tmp: str, camera_id: str, *, state: str = "running") -> None:
    status_dir = os.path.join(tmp, "inference")
    os.makedirs(status_dir, exist_ok=True)
    path = os.path.join(status_dir, f"{camera_id}.status.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"camera_id": camera_id, "state": state, "is_inferencing": state == "running"},
                ensure_ascii=False,
            )
        )


class PipelineLogTests(unittest.TestCase):
    def setUp(self):
        _reset_process_logging()

    def tearDown(self):
        _reset_process_logging()

    def test_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_config = {
                "paths": {
                    "camera_ips_file": os.path.join(tmp, "camera_ips.json"),
                    "base_localdata_dir": tmp,
                },
                "pipeline_log": {"enabled": False, "file_enabled": True, "dir": tmp, "stdout": False},
            }
            with open(app_config["paths"]["camera_ips_file"], "w", encoding="utf-8") as f:
                json.dump([], f)
            with patch.dict(os.environ, {}, clear=True):
                pipeline_log.configure_process_logging(role="worker", app_config=app_config)
                self.assertFalse(pipeline_log.pipeline_log_enabled())
                self.assertFalse(pipeline_log.pipeline_log_process_active())
                pipeline_log.log_pipeline_stage("pose_published", camera_id="cam1", frame_idx=1)
                log_path = os.path.join(tmp, "worker.log")
                content = open(log_path, encoding="utf-8").read() if os.path.isfile(log_path) else ""
                self.assertNotIn("stage=pose_published", content)

    def test_config_enables_file_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_config = {
                "paths": {
                    "camera_ips_file": os.path.join(tmp, "camera_ips.json"),
                    "base_localdata_dir": tmp,
                },
                "pipeline_log": {
                    "enabled": True,
                    "file_enabled": True,
                    "stdout": False,
                    "dir": tmp,
                    "sample": 1,
                },
            }
            with open(app_config["paths"]["camera_ips_file"], "w", encoding="utf-8") as f:
                json.dump([{"id": "cam1", "name": "cam1", "url": "rtsp://x/cam1"}], f)
            _write_inference_status(tmp, "cam1")
            with patch.dict(os.environ, {}, clear=True):
                pipeline_log.configure_process_logging(role="worker", app_config=app_config)
                pipeline_log.log_pipeline_stage(
                    "worker_received",
                    camera_id="cam1",
                    frame_idx=10,
                    persons=2,
                )
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
                pipeline_log.configure_process_logging(role="infer_cam1", app_config=app_config)
                pipeline_log.log_pipeline_stage("rtsp_frame", camera_id="cam1", frame_idx=29)
                log_path = os.path.join(tmp, "infer_cam1.log")
                if os.path.isfile(log_path):
                    self.assertEqual(open(log_path, encoding="utf-8").read(), "")
                pipeline_log.log_pipeline_stage("rtsp_frame", camera_id="cam1", frame_idx=30)
                self.assertTrue(os.path.isfile(log_path))
                self.assertIn("stage=rtsp_frame", open(log_path, encoding="utf-8").read())

    def test_boot_logger_always_available(self):
        with patch.dict(os.environ, {}, clear=True):
            pipeline_log.configure_process_logging(role="worker", app_config={"pipeline_log": {"enabled": False}})
            boot = pipeline_log.get_boot_logger()
            self.assertTrue(boot.handlers)

    def test_collision_logger_respects_env(self):
        app_config = {"pipeline_log": {"enabled": False}}
        with patch.dict(os.environ, {"COLLISION_LOG": "1"}, clear=True):
            pipeline_log.configure_process_logging(role="worker", app_config=app_config)
            collision = pipeline_log.get_collision_logger()
            self.assertTrue(collision.handlers)
            with patch.object(collision, "info") as mock_info:
                collision.info("[COLLISION][HIT] test=1")
                mock_info.assert_called_once()

    def test_reload_updates_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_path = os.path.join(tmp, "runtime_config.json")
            app_config = {"pipeline_log": {"enabled": True, "stdout": False, "file_enabled": False, "sample": 10}}
            with patch.dict(os.environ, {"RUNTIME_CONFIG_FILE": runtime_path}, clear=True):
                pipeline_log.configure_process_logging(role="worker", app_config=app_config)
                self.assertEqual(pipeline_log.pipeline_log_sample_every(), 10)
                with open(runtime_path, "w", encoding="utf-8") as f:
                    f.write('{"pipeline_log": {"sample": 5}}')
                pipeline_log.reload_process_logging(app_config)
                self.assertEqual(pipeline_log.pipeline_log_sample_every(), 5)

    def test_rotating_file_handler_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_config = {
                "pipeline_log": {
                    "enabled": True,
                    "file_enabled": True,
                    "stdout": False,
                    "dir": tmp,
                    "max_bytes": 2048,
                    "backup_count": 2,
                    "sample": 1,
                }
            }
            with patch.dict(os.environ, {}, clear=True):
                pipeline_log.configure_process_logging(role="worker", app_config=app_config)
                logger = logging.getLogger(pipeline_log._LOGGER_PIPELINE)
                file_handlers = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
                self.assertEqual(len(file_handlers), 1)
                self.assertEqual(file_handlers[0].maxBytes, 2048)
                self.assertEqual(file_handlers[0].backupCount, 2)

    def test_worker_skips_when_inference_inactive(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_config = {
                "paths": {
                    "camera_ips_file": os.path.join(tmp, "camera_ips.json"),
                    "base_localdata_dir": tmp,
                },
                "pipeline_log": {
                    "enabled": True,
                    "file_enabled": True,
                    "stdout": False,
                    "dir": tmp,
                    "sample": 1,
                },
            }
            with open(app_config["paths"]["camera_ips_file"], "w", encoding="utf-8") as f:
                json.dump([{"id": "cam1", "name": "cam1", "url": "rtsp://x/cam1"}], f)
            with patch.dict(os.environ, {}, clear=True):
                pipeline_log.configure_process_logging(role="worker", app_config=app_config)
                pipeline_log.log_pipeline_stage("worker_received", camera_id="cam1", frame_idx=1)
                log_path = os.path.join(tmp, "worker.log")
                content = open(log_path, encoding="utf-8").read() if os.path.isfile(log_path) else ""
                self.assertNotIn("stage=worker_received", content)

    def test_worker_skips_when_camera_pipeline_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_config = {
                "paths": {
                    "camera_ips_file": os.path.join(tmp, "camera_ips.json"),
                    "base_localdata_dir": tmp,
                },
                "pipeline_log": {
                    "enabled": True,
                    "file_enabled": True,
                    "stdout": False,
                    "dir": tmp,
                    "sample": 1,
                },
            }
            with open(app_config["paths"]["camera_ips_file"], "w", encoding="utf-8") as f:
                json.dump(
                    [
                        {
                            "id": "cam1",
                            "name": "cam1",
                            "url": "rtsp://x/cam1",
                            "settings": {"pipeline_log.enabled": False},
                        }
                    ],
                    f,
                )
            _write_inference_status(tmp, "cam1")
            with patch.dict(os.environ, {}, clear=True):
                pipeline_log.configure_process_logging(role="worker", app_config=app_config)
                pipeline_log.log_pipeline_stage("worker_received", camera_id="cam1", frame_idx=1)
                log_path = os.path.join(tmp, "worker.log")
                content = open(log_path, encoding="utf-8").read() if os.path.isfile(log_path) else ""
                self.assertNotIn("stage=worker_received", content)

    def test_worker_logs_when_global_off_camera_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_config = {
                "paths": {
                    "camera_ips_file": os.path.join(tmp, "camera_ips.json"),
                    "base_localdata_dir": tmp,
                },
                "pipeline_log": {
                    "enabled": False,
                    "file_enabled": True,
                    "stdout": False,
                    "dir": tmp,
                    "sample": 1,
                },
            }
            with open(app_config["paths"]["camera_ips_file"], "w", encoding="utf-8") as f:
                json.dump(
                    [
                        {
                            "id": "cam1",
                            "path": "cam1",
                            "name": "cam1",
                            "url": "rtsp://127.0.0.1/cam1",
                            "source_type": "external",
                            "settings": {"pipeline_log.enabled": True},
                        }
                    ],
                    f,
                )
            _write_inference_status(tmp, "cam1")
            with patch.dict(os.environ, {}, clear=True):
                pipeline_log.configure_process_logging(role="worker", app_config=app_config)
                self.assertFalse(pipeline_log.pipeline_log_enabled())
                self.assertTrue(pipeline_log.pipeline_log_process_active())
                pipeline_log.log_pipeline_stage("worker_received", camera_id="cam1", frame_idx=1)
                content = open(os.path.join(tmp, "worker.log"), encoding="utf-8").read()
                self.assertIn("stage=worker_received", content)


if __name__ == "__main__":
    unittest.main()

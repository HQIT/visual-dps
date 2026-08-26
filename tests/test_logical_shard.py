"""logical shard 分片单元测试。"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "sharding",
    _ROOT / "services" / "event_engine" / "sharding.py",
)
assert _spec and _spec.loader
sharding = importlib.util.module_from_spec(_spec)
sys.modules["sharding"] = sharding
_spec.loader.exec_module(sharding)


class LogicalShardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_camera_maps_to_stable_shard(self) -> None:
        os.environ["POSE_LOGICAL_SHARD_COUNT"] = "16"
        a = sharding.logical_shard_id("cam01")
        b = sharding.logical_shard_id("cam01")
        self.assertEqual(a, b)
        self.assertGreaterEqual(a, 0)
        self.assertLess(a, 16)

    def test_stream_key_for_camera(self) -> None:
        os.environ["POSE_LOGICAL_SHARD_COUNT"] = "16"
        os.environ.pop("POSE_STREAM_KEY", None)
        os.environ["POSE_STREAM_KEY_PREFIX"] = "pose:stream"
        sid = sharding.logical_shard_id("cam02")
        self.assertEqual(sharding.stream_key_for_camera("cam02"), f"pose:stream:{sid}")

    def test_worker_owned_shard_range(self) -> None:
        os.environ["POSE_LOGICAL_SHARD_COUNT"] = "16"
        os.environ["EVENT_WORKER_SHARD_START"] = "0"
        os.environ["EVENT_WORKER_SHARD_END"] = "7"
        self.assertEqual(sharding.worker_owned_shard_ids(), list(range(8)))

    def test_worker_owned_shard_ids_from_env_dict(self) -> None:
        os.environ["POSE_LOGICAL_SHARD_COUNT"] = "16"
        os.environ.pop("EVENT_WORKER_SHARD_START", None)
        os.environ.pop("EVENT_WORKER_SHARD_END", None)
        ids = sharding.worker_owned_shard_ids(
            {
                "EVENT_WORKER_SHARD_START": "8",
                "EVENT_WORKER_SHARD_END": "15",
            }
        )
        self.assertEqual(ids, list(range(8, 16)))

    def test_owns_camera_by_logical_shard(self) -> None:
        os.environ["POSE_LOGICAL_SHARD_COUNT"] = "16"
        os.environ["EVENT_WORKER_SHARD_START"] = "0"
        os.environ["EVENT_WORKER_SHARD_END"] = "7"
        cam = "cam-fixed-shard-test"
        sid = sharding.logical_shard_id(cam)
        owned = sharding.worker_owned_shard_ids()
        self.assertEqual(sharding.owns_camera(cam), sid in owned)

    def test_legacy_single_stream(self) -> None:
        os.environ["POSE_LOGICAL_SHARD_COUNT"] = "1"
        os.environ["POSE_STREAM_KEY"] = "pose:stream"
        self.assertEqual(sharding.stream_key_for_camera("cam99"), "pose:stream")


if __name__ == "__main__":
    unittest.main()

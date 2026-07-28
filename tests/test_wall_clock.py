"""wall_clock 时区统一单测。"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services.wall_clock import log_timezone_name, wall_datetime, wall_time_str


class WallClockTests(unittest.TestCase):
    def test_default_timezone_name(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(log_timezone_name(), "Asia/Shanghai")

    def test_respects_tz_env(self):
        with patch.dict(os.environ, {"TZ": "Asia/Tokyo"}, clear=True):
            self.assertEqual(log_timezone_name(), "Asia/Tokyo")

    def test_wall_time_str_format(self):
        fixed = datetime(2026, 7, 28, 14, 39, 36, 725000, tzinfo=timezone(timedelta(hours=8)))
        with patch("services.wall_clock.wall_datetime", return_value=fixed):
            self.assertEqual(wall_time_str(), "2026-07-28 14:39:36.725")

    def test_shanghai_fallback_without_zoneinfo(self):
        """无 tzdata 时 Asia/Shanghai 应回退 UTC+8，而非 UTC。"""
        with patch.dict(os.environ, {"TZ": "Asia/Shanghai"}, clear=True):
            with patch("services.wall_clock.ZoneInfo", side_effect=KeyError("no tzdata")):
                dt = wall_datetime()
                self.assertEqual(dt.utcoffset(), timedelta(hours=8))
                self.assertEqual(wall_time_str()[:10], dt.strftime("%Y-%m-%d"))


if __name__ == "__main__":
    unittest.main()

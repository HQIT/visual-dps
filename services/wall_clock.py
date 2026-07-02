"""统一本地墙钟时间（日志与回调 finishTime 毫秒戳）。"""

from __future__ import annotations

import time
from datetime import datetime


def wall_time_str() -> str:
    """本地时间 YYYY-MM-DD HH:MM:SS.mmm（碰撞/回调日志墙钟）。"""
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S") + f".{now.microsecond // 1000:03d}"


def epoch_ms() -> int:
    """Unix 纪元毫秒（Java finishTime 等回调字段）。"""
    return int(time.time() * 1000)

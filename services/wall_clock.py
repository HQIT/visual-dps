"""统一本地墙钟时间（日志与回调 finishTime 毫秒戳）。"""

from __future__ import annotations

import time
from datetime import datetime


def wall_time_str() -> str:
    """本地时间 YYYY-MM-DD HH:MM:SS（与 event-worker 碰撞日志一致）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def epoch_ms() -> int:
    """Unix 纪元毫秒（Java finishTime 等回调字段）。"""
    return int(time.time() * 1000)

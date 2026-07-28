"""统一本地墙钟时间（日志与回调 finishTime 毫秒戳）。"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# 与 compose / inference_container_service 默认一致
_DEFAULT_TZ_NAME = "Asia/Shanghai"
# 无 tzdata 时（部分 slim 推理镜像）对国内现场的固定偏移
_CN_FALLBACK_TZ_NAMES = frozenset(
    {
        "Asia/Shanghai",
        "PRC",
        "Asia/Chongqing",
        "Asia/Harbin",
        "Asia/Urumqi",
        "CST-8",
    }
)
_CN_FALLBACK_TZ = timezone(timedelta(hours=8), name="CST")


def log_timezone_name() -> str:
    """当前日志墙钟使用的 IANA 时区名（来自 TZ 环境变量）。"""
    return (os.environ.get("TZ") or _DEFAULT_TZ_NAME).strip() or _DEFAULT_TZ_NAME


def _resolve_log_timezone():
    """解析日志用时区；无 tzdata 时对 Asia/Shanghai 等回退 UTC+8。"""
    name = log_timezone_name()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        if name in _CN_FALLBACK_TZ_NAMES:
            return _CN_FALLBACK_TZ
        return timezone.utc


def wall_datetime() -> datetime:
    """带时区的当前本地墙钟（供日志与回调 ISO 时间）。"""
    return datetime.now(_resolve_log_timezone())


def wall_time_str() -> str:
    """本地时间 YYYY-MM-DD HH:MM:SS.mmm（[PIPELINE]/碰撞/回调日志墙钟）。"""
    now = wall_datetime()
    return now.strftime("%Y-%m-%d %H:%M:%S") + f".{now.microsecond // 1000:03d}"


def epoch_ms() -> int:
    """Unix 纪元毫秒（Java finishTime 等回调字段）。"""
    return int(time.time() * 1000)

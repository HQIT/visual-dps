"""前置门控终端日志（字段与 event_log 统一，由 worker 集中输出）。"""

from __future__ import annotations

from services.event_engine.event_log import prefilter_log_enabled

__all__ = ["prefilter_log_enabled"]

"""前置门控单次判定结果（供日志与调试）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PrefilterDecision:
    blocked: bool
    track_id: int
    speed_feature: str
    speed_value: float | None
    speed_threshold: float
    ankle_max_speed: float | None = None
    ankle_max_speed_norm: float | None = None

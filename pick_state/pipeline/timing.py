"""pick_state 分阶段耗时（live profiling 用）。"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


def stage_profiling_enabled() -> bool:
    raw = os.environ.get("PICK_STATE_STAGE_PROFILE", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return raw in ("1", "true", "yes", "on", "")


@dataclass
class StageTimings:
    track_ms: float = 0.0
    feature_ms: float = 0.0
    box_ms: float = 0.0
    pair_temporal_ms: float = 0.0
    action_track_ms: float = 0.0
    pair_score_ms: float = 0.0
    action_gate_ms: float = 0.0
    box_gate_ms: float = 0.0
    alarm_ms: float = 0.0
    n_persons: int = 0
    n_hits: int = 0
    n_picking: int = 0
    n_action_gate_calls: int = 0

    def total_ms(self) -> float:
        return (
            self.track_ms
            + self.feature_ms
            + self.box_ms
            + self.pair_temporal_ms
            + self.action_track_ms
            + self.pair_score_ms
            + self.action_gate_ms
            + self.box_gate_ms
            + self.alarm_ms
        )

    def as_log_fields(self) -> dict[str, float | int]:
        return {
            "track_ms": round(self.track_ms, 2),
            "feature_ms": round(self.feature_ms, 2),
            "box_ms": round(self.box_ms, 2),
            "pair_temporal_ms": round(self.pair_temporal_ms, 2),
            "action_track_ms": round(self.action_track_ms, 2),
            "pair_score_ms": round(self.pair_score_ms, 2),
            "action_gate_ms": round(self.action_gate_ms, 2),
            "box_gate_ms": round(self.box_gate_ms, 2),
            "alarm_ms": round(self.alarm_ms, 2),
            "pick_total_ms": round(self.total_ms(), 2),
            "n_persons": self.n_persons,
            "n_hits": self.n_hits,
            "n_picking": self.n_picking,
            "n_action_gate": self.n_action_gate_calls,
        }

    def is_hot_path(self) -> bool:
        return self.n_hits > 0 or self.n_picking > 0 or self.n_action_gate_calls > 0


@dataclass
class StageTimer:
    timings: StageTimings = field(default_factory=StageTimings)
    enabled: bool = True

    @contextmanager
    def span(self, field_name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - t0) * 1000.0
            current = getattr(self.timings, field_name)
            setattr(self.timings, field_name, current + elapsed)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FrameContext:
    record_id: str
    frame_idx: int
    camera_slug: str = ""
    persons: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class FeatureVector:
    frame_idx: int
    person_track_id: str
    values: dict[str, float | None] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PickDecision:
    person_track_id: str
    score_raw: float
    score_smooth: float
    is_picking: bool
    expert: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    frame_idx: int
    pick_decisions: list[PickDecision] = field(default_factory=list)
    box_hits: list[str] = field(default_factory=list)
    alarm_hits: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

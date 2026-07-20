"""碰撞前置门控运行时配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_SPEED_FEATURE = "ankle_max_speed_norm"
DEFAULT_SPEED_THRESHOLD = 0.081770
DEFAULT_ARM_TORSO_MIN = 90.0
DEFAULT_ELBOW_MIN = 150.0
DEFAULT_WRIST_ELEVATION_MIN = 60.0
DEFAULT_STANCE_FEATURE = "shoulder_hip_knee_angle_min"
DEFAULT_STANCE_THRESHOLD = 140.0


@dataclass
class PickPrefilterConfig:
    enabled: bool = False
    speed_feature: str = DEFAULT_SPEED_FEATURE
    speed_threshold: float = DEFAULT_SPEED_THRESHOLD
    arm_torso_min: float = DEFAULT_ARM_TORSO_MIN
    elbow_min: float = DEFAULT_ELBOW_MIN
    wrist_elevation_min: float = DEFAULT_WRIST_ELEVATION_MIN
    stance_feature: str = DEFAULT_STANCE_FEATURE
    stance_threshold: float = DEFAULT_STANCE_THRESHOLD
    max_pose_gap_sec: float = 0.0

    @classmethod
    def from_section(cls, section: dict[str, Any] | None) -> PickPrefilterConfig:
        sec = section if isinstance(section, dict) else {}
        return cls(
            enabled=bool(sec.get("enabled", False)),
            speed_feature=str(sec.get("speed_feature") or DEFAULT_SPEED_FEATURE).strip()
            or DEFAULT_SPEED_FEATURE,
            speed_threshold=float(sec.get("speed_threshold", DEFAULT_SPEED_THRESHOLD)),
            arm_torso_min=float(sec.get("arm_torso_min", DEFAULT_ARM_TORSO_MIN)),
            elbow_min=float(sec.get("elbow_min", DEFAULT_ELBOW_MIN)),
            wrist_elevation_min=float(sec.get("wrist_elevation_min", DEFAULT_WRIST_ELEVATION_MIN)),
            stance_feature=str(sec.get("stance_feature") or DEFAULT_STANCE_FEATURE).strip()
            or DEFAULT_STANCE_FEATURE,
            stance_threshold=float(sec.get("stance_threshold", DEFAULT_STANCE_THRESHOLD)),
            max_pose_gap_sec=float(sec.get("max_pose_gap_sec", 0.0) or 0.0),
        )

    def resolve_max_pose_gap_sec(self, *, pose_frame_interval: int, frame_rate: float) -> float:
        """0 表示自动：interval/frame_rate × 2.5。"""
        if self.max_pose_gap_sec > 0:
            return self.max_pose_gap_sec
        interval = max(1, int(pose_frame_interval or 1))
        fps = max(1.0, float(frame_rate or 15.0))
        return (interval / fps) * 2.5

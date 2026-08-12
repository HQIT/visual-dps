"""特征提取：阶段 1 只实现 rule_expert 所需 5 维；完整 44 维见计划文档。"""

from pick_state.features.bank import FeatureBank
from pick_state.features.geometry import compute_angle_features
from pick_state.features.velocity import VelocityTracker

# 阶段 1 已实现
FEATURE_CATALOG_V1 = [
    "ankle_max_speed_norm",
    "arm_torso_angle_max",
    "elbow_angle_mean",
    "wrist_elevation_angle_max",
    "shoulder_hip_knee_angle_min",
]

__all__ = ["FeatureBank", "VelocityTracker", "compute_angle_features", "FEATURE_CATALOG_V1"]

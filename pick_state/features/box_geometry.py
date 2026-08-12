"""人-货框配对几何特征。

纯 2D 量，无新依赖。核心假设：固定机位下，人脚在画面里的 y 坐标与像素身高
共同编码「人离相机多远」，因此可以在不做相机标定的前提下近似出站位深度。
"""

from __future__ import annotations

from typing import Any

from pick_state.features.geometry import (
    ANKLE_LEFT,
    ANKLE_RIGHT,
    KNEE_LEFT,
    KNEE_RIGHT,
    SHOULDER_LEFT,
    SHOULDER_RIGHT,
    compute_side_angle_features,
    read_xy,
)

SIDE_FEATURE_KEYS = [
    "arm_torso_angle_side",
    "elbow_angle_side",
    "wrist_elevation_angle_side",
]

PAIR_FEATURE_KEYS = [
    "depth_ratio",
    "margin_gap",
    "candidate_count",
    "center_dist_norm",
    "wrist_bearing_x",
    "wrist_bearing_y",
    "box_bottom_y_norm",
    "stance_gap_norm",
    "person_height_norm",
    "stance_valid",
    "wrist_score",
    *SIDE_FEATURE_KEYS,
]


def _mean_y(person: dict[str, Any], a: int, b: int) -> float | None:
    pa, pb = read_xy(person, a), read_xy(person, b)
    ys = [p[1] for p in (pa, pb) if p is not None]
    return sum(ys) / len(ys) if ys else None


def _foot_y(person: dict[str, Any]) -> tuple[float | None, bool]:
    """脚部 y；踝不可见时退化到膝，第二个返回值标记是否用了踝。"""
    ankle = _mean_y(person, ANKLE_LEFT, ANKLE_RIGHT)
    if ankle is not None:
        return ankle, True
    return _mean_y(person, KNEE_LEFT, KNEE_RIGHT), False


def box_bottom_y(box: Any) -> float:
    return float(box.contour[:, 0, 1].max())


def compute_pair_features(
    person: dict[str, Any],
    hit: dict[str, Any],
    box: Any,
    *,
    infer_height: int,
) -> dict[str, float | None]:
    """hit 来自 BoxTrigger.hits_for_person，box 是对应的 BoxDef。"""
    h = float(max(1, infer_height))
    inradius = float(max(1.0, box.inradius))
    wx, wy = hit["wrist_xy"]
    cx, cy = box.center
    bottom = box_bottom_y(box)

    foot_y, used_ankle = _foot_y(person)
    shoulder_y = _mean_y(person, SHOULDER_LEFT, SHOULDER_RIGHT)

    out: dict[str, float | None] = {
        "depth_ratio": float(hit["depth_ratio"]),
        "margin_gap": float(hit["margin_gap"]),
        "candidate_count": float(hit["candidate_count"]),
        "center_dist_norm": float(hit["center_dist"]) / inradius,
        "wrist_bearing_x": (wx - cx) / inradius,
        "wrist_bearing_y": (wy - cy) / inradius,
        "box_bottom_y_norm": bottom / h,
        "stance_gap_norm": (foot_y - bottom) / h if foot_y is not None else None,
        "person_height_norm": (
            (foot_y - shoulder_y) / h if foot_y is not None and shoulder_y is not None else None
        ),
        "stance_valid": 1.0 if used_ankle else 0.0,
        # 触发这次命中的手腕有多可信。手被遮挡时姿态估计基本是猜的，
        # 「在框里」这个证据的可靠性得让模型自己权衡，不能只靠一个硬门槛。
        "wrist_score": float(hit.get("wrist_score") or 0.0),
    }
    out.update(compute_side_angle_features(person, hit["wrist_idx"]))
    return out

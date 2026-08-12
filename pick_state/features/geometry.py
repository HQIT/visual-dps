"""单帧几何特征：角度与站姿。语义对齐 visual-dps pick_prefilter/angles.py（本仓独立实现，不依赖该仓）。"""

from __future__ import annotations

import math
from typing import Any

KPT_SCORE_MIN = 0.3

SHOULDER_LEFT, SHOULDER_RIGHT = 5, 6
ELBOW_LEFT, ELBOW_RIGHT = 7, 8
WRIST_LEFT, WRIST_RIGHT = 9, 10
HIP_LEFT, HIP_RIGHT = 11, 12
KNEE_LEFT, KNEE_RIGHT = 13, 14
ANKLE_LEFT, ANKLE_RIGHT = 15, 16

SIDE_DEFS = (
    ("left", SHOULDER_LEFT, ELBOW_LEFT, WRIST_LEFT),
    ("right", SHOULDER_RIGHT, ELBOW_RIGHT, WRIST_RIGHT),
)
SHOULDER_HIP_KNEE_DEFS = (
    (SHOULDER_LEFT, HIP_LEFT, KNEE_LEFT),
    (SHOULDER_RIGHT, HIP_RIGHT, KNEE_RIGHT),
)


def read_kpt(person: dict[str, Any], idx: int) -> tuple[float, float, float] | None:
    kpts = person.get("keypoints") or []
    if idx >= len(kpts):
        return None
    kp = kpts[idx]
    if not isinstance(kp, (list, tuple)) or len(kp) < 2:
        return None
    score = float(kp[2]) if len(kp) > 2 else 0.0
    if score < KPT_SCORE_MIN:
        return None
    return float(kp[0]), float(kp[1]), score


def read_xy(person: dict[str, Any], idx: int) -> tuple[float, float] | None:
    pt = read_kpt(person, idx)
    return None if pt is None else (pt[0], pt[1])


def _center(person: dict[str, Any], a: int, b: int) -> tuple[float, float] | None:
    pa, pb = read_xy(person, a), read_xy(person, b)
    if pa is None and pb is None:
        return None
    if pa is None:
        return pb
    if pb is None:
        return pa
    return (pa[0] + pb[0]) / 2.0, (pa[1] + pb[1]) / 2.0


def angle_at_joint(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> float | None:
    bax, bay = a[0] - b[0], a[1] - b[1]
    bcx, bcy = c[0] - b[0], c[1] - b[1]
    na, nc = math.hypot(bax, bay), math.hypot(bcx, bcy)
    if na < 1e-6 or nc < 1e-6:
        return None
    cos_val = max(-1.0, min(1.0, (bax * bcx + bay * bcy) / (na * nc)))
    return math.degrees(math.acos(cos_val))


def angle_from_downward(dx: float, dy: float) -> float | None:
    """相对图像向下方向 (0,1) 的夹角；0°≈手臂下垂，90°≈水平前伸。"""
    norm = math.hypot(dx, dy)
    if norm < 1e-6:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, dy / norm))))


def compute_side_angle_features(
    person: dict[str, Any], wrist_idx: int
) -> dict[str, float | None]:
    """只算伸进货框那一侧的手臂角度。

    左右聚合版（max/mean）会把没伸手那条胳膊的姿态混进来——实测 98% 的命中帧
    两条胳膊都可见，聚合等于常态性污染。
    """
    if int(wrist_idx) == WRIST_LEFT:
        sh_idx, el_idx, wr_idx = SHOULDER_LEFT, ELBOW_LEFT, WRIST_LEFT
    else:
        sh_idx, el_idx, wr_idx = SHOULDER_RIGHT, ELBOW_RIGHT, WRIST_RIGHT

    sh, el, wr = read_xy(person, sh_idx), read_xy(person, el_idx), read_xy(person, wr_idx)
    hip_c = _center(person, HIP_LEFT, HIP_RIGHT)

    out: dict[str, float | None] = {
        "arm_torso_angle_side": None,
        "elbow_angle_side": None,
        "wrist_elevation_angle_side": None,
    }
    if hip_c is not None and sh is not None and el is not None:
        ang = angle_at_joint(hip_c, sh, el)
        if ang is not None:
            out["arm_torso_angle_side"] = round(ang, 2)
    if sh is not None and el is not None and wr is not None:
        ang = angle_at_joint(sh, el, wr)
        if ang is not None:
            out["elbow_angle_side"] = round(ang, 2)
    if sh is not None and wr is not None:
        ang = angle_from_downward(wr[0] - sh[0], wr[1] - sh[1])
        if ang is not None:
            out["wrist_elevation_angle_side"] = round(ang, 2)
    return out


def compute_angle_features(person: dict[str, Any]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    hip_c = _center(person, HIP_LEFT, HIP_RIGHT)

    arm_torso: list[float] = []
    elbow_vals: list[float] = []
    wrist_elev: list[float] = []

    for _side, sh_idx, el_idx, wr_idx in SIDE_DEFS:
        sh = read_xy(person, sh_idx)
        el = read_xy(person, el_idx)
        wr = read_xy(person, wr_idx)

        if hip_c is not None and sh is not None and el is not None:
            ang = angle_at_joint(hip_c, sh, el)
            if ang is not None:
                arm_torso.append(ang)
        if sh is not None and el is not None and wr is not None:
            ang = angle_at_joint(sh, el, wr)
            if ang is not None:
                elbow_vals.append(ang)
        if sh is not None and wr is not None:
            ang = angle_from_downward(wr[0] - sh[0], wr[1] - sh[1])
            if ang is not None:
                wrist_elev.append(ang)

    if arm_torso:
        out["arm_torso_angle_max"] = round(max(arm_torso), 2)
    if elbow_vals:
        out["elbow_angle_mean"] = round(sum(elbow_vals) / len(elbow_vals), 2)
    if wrist_elev:
        out["wrist_elevation_angle_max"] = round(max(wrist_elev), 2)

    shk: list[float] = []
    for sh_idx, hip_idx, knee_idx in SHOULDER_HIP_KNEE_DEFS:
        sh, hip, knee = read_xy(person, sh_idx), read_xy(person, hip_idx), read_xy(person, knee_idx)
        if sh is None or hip is None or knee is None:
            continue
        ang = angle_at_joint(sh, hip, knee)
        if ang is not None:
            shk.append(ang)

    sh_c = _center(person, SHOULDER_LEFT, SHOULDER_RIGHT)
    kn_c = _center(person, KNEE_LEFT, KNEE_RIGHT)
    if sh_c is not None and hip_c is not None and kn_c is not None:
        ang = angle_at_joint(sh_c, hip_c, kn_c)
        if ang is not None:
            shk.append(ang)

    if shk:
        out["shoulder_hip_knee_angle_min"] = round(min(shk), 2)
        out["shoulder_hip_knee_angle_max"] = round(max(shk), 2)

    return out

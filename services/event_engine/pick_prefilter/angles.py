"""门控所需单帧角度特征（精简自 data-collector skeleton_angles）。"""

from __future__ import annotations

import math
from typing import Any

KPT_SCORE_MIN = 0.3

HIP_LEFT = 11
HIP_RIGHT = 12
SHOULDER_LEFT = 5
SHOULDER_RIGHT = 6
KNEE_LEFT = 13
KNEE_RIGHT = 14

ELBOW_ANGLE_DEFS = (
    ("left_elbow_angle", 5, 7, 9),
    ("right_elbow_angle", 6, 8, 10),
)

SHOULDER_HIP_KNEE_ANGLE_DEFS = (
    ("left_shoulder_hip_knee_angle", SHOULDER_LEFT, HIP_LEFT, KNEE_LEFT),
    ("right_shoulder_hip_knee_angle", SHOULDER_RIGHT, HIP_RIGHT, KNEE_RIGHT),
)


def _read_kpt(person: dict[str, Any], kpt_idx: int) -> tuple[float, float, float] | None:
    keypoints = person.get("keypoints") or []
    if kpt_idx >= len(keypoints):
        return None
    kp = keypoints[kpt_idx]
    if not isinstance(kp, (list, tuple)) or len(kp) < 2:
        return None
    score = float(kp[2]) if len(kp) > 2 else 0.0
    if score < KPT_SCORE_MIN:
        return None
    return float(kp[0]), float(kp[1]), score


def _read_xy(person: dict[str, Any], kpt_idx: int) -> tuple[float, float] | None:
    pt = _read_kpt(person, kpt_idx)
    if pt is None:
        return None
    return pt[0], pt[1]


def _angle_at_joint(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float | None:
    bax, bay = a[0] - b[0], a[1] - b[1]
    bcx, bcy = c[0] - b[0], c[1] - b[1]
    norm_ba = math.hypot(bax, bay)
    norm_bc = math.hypot(bcx, bcy)
    if norm_ba < 1e-6 or norm_bc < 1e-6:
        return None
    cos_val = (bax * bcx + bay * bcy) / (norm_ba * norm_bc)
    cos_val = max(-1.0, min(1.0, cos_val))
    return math.degrees(math.acos(cos_val))


def _angle_from_downward(dx: float, dy: float) -> float | None:
    norm = math.hypot(dx, dy)
    if norm < 1e-6:
        return None
    cos_val = max(-1.0, min(1.0, dy / norm))
    return math.degrees(math.acos(cos_val))


def _hip_center(person: dict[str, Any]) -> tuple[float, float] | None:
    lh = _read_xy(person, HIP_LEFT)
    rh = _read_xy(person, HIP_RIGHT)
    if lh is None and rh is None:
        return None
    if lh is None:
        return rh
    if rh is None:
        return lh
    return (lh[0] + rh[0]) / 2.0, (lh[1] + rh[1]) / 2.0


def _shoulder_center(person: dict[str, Any]) -> tuple[float, float] | None:
    ls = _read_xy(person, SHOULDER_LEFT)
    rs = _read_xy(person, SHOULDER_RIGHT)
    if ls is None and rs is None:
        return None
    if ls is None:
        return rs
    if rs is None:
        return ls
    return (ls[0] + rs[0]) / 2.0, (ls[1] + rs[1]) / 2.0


def _knee_center(person: dict[str, Any]) -> tuple[float, float] | None:
    lk = _read_xy(person, KNEE_LEFT)
    rk = _read_xy(person, KNEE_RIGHT)
    if lk is None and rk is None:
        return None
    if lk is None:
        return rk
    if rk is None:
        return lk
    return (lk[0] + rk[0]) / 2.0, (lk[1] + rk[1]) / 2.0


def compute_orientation_angles(person: dict[str, Any]) -> dict[str, float | None]:
    """triple90 所需：arm_torso / elbow_waist / wrist_elevation。"""
    out: dict[str, float | None] = {}
    hip_c = _hip_center(person)
    if hip_c is None:
        return out

    arm_torso: list[float] = []
    elbow_vals: list[float] = []
    wrist_elev: list[float] = []

    for side, sh_idx, el_idx, wr_idx in (("left", 5, 7, 9), ("right", 6, 8, 10)):
        sh = _read_xy(person, sh_idx)
        el = _read_xy(person, el_idx)
        wr = _read_xy(person, wr_idx)

        if sh is not None and el is not None:
            ang = _angle_at_joint(hip_c, sh, el)
            if ang is not None:
                out[f"{side}_arm_torso_angle"] = round(ang, 2)
                arm_torso.append(ang)

        if sh is not None and el is not None and wr is not None:
            ang = _angle_at_joint(sh, el, wr)
            if ang is not None:
                out[f"{side}_elbow_angle"] = round(ang, 2)
                elbow_vals.append(ang)

        if sh is not None and wr is not None:
            dx, dy = wr[0] - sh[0], wr[1] - sh[1]
            ang = _angle_from_downward(dx, dy)
            if ang is not None:
                out[f"{side}_wrist_elevation_angle"] = round(ang, 2)
                wrist_elev.append(ang)

    if arm_torso:
        out["arm_torso_angle_max"] = round(max(arm_torso), 2)
    if elbow_vals:
        out["elbow_angle_mean"] = round(sum(elbow_vals) / len(elbow_vals), 2)
    if wrist_elev:
        out["wrist_elevation_angle_max"] = round(max(wrist_elev), 2)

    return out


def compute_stance_angles(person: dict[str, Any]) -> dict[str, float | None]:
    """站立判定：shoulder_hip_knee_angle_min 等。"""
    out: dict[str, float | None] = {}
    shoulder_hip_knee_angles: list[float] = []

    for name, sh_idx, hip_idx, knee_idx in SHOULDER_HIP_KNEE_ANGLE_DEFS:
        sh = _read_xy(person, sh_idx)
        hip = _read_xy(person, hip_idx)
        knee = _read_xy(person, knee_idx)
        if sh is None or hip is None or knee is None:
            out[name] = None
            continue
        ang = _angle_at_joint(sh, hip, knee)
        out[name] = round(ang, 2) if ang is not None else None
        if ang is not None:
            shoulder_hip_knee_angles.append(float(ang))

    sh_c = _shoulder_center(person)
    hip_c = _hip_center(person)
    kn_c = _knee_center(person)
    if sh_c is not None and hip_c is not None and kn_c is not None:
        ang = _angle_at_joint(sh_c, hip_c, kn_c)
        out["center_shoulder_hip_knee_angle"] = round(ang, 2) if ang is not None else None
        if ang is not None:
            shoulder_hip_knee_angles.append(float(ang))

    if shoulder_hip_knee_angles:
        out["shoulder_hip_knee_angle_min"] = round(min(shoulder_hip_knee_angles), 2)
        out["shoulder_hip_knee_angle_max"] = round(max(shoulder_hip_knee_angles), 2)

    return out


def compute_prefilter_angle_features(person: dict[str, Any]) -> dict[str, float | None]:
    row: dict[str, float | None] = {}
    row.update(compute_orientation_angles(person))
    row.update(compute_stance_angles(person))
    return row

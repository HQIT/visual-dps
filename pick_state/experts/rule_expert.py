"""把现有速度+角度+站姿门控封装为可拆装 expert（逻辑与 DPS pick_prefilter 对齐）。"""

from __future__ import annotations

from typing import Any


def _f(row: dict[str, Any], key: str) -> float | None:
    v = row.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class RulePickExpert:
    """
    与线上门控语义对齐：
      block = speed_high AND NOT triple90 AND is_standing
      pick_ok = not block  （无法判定速度时 fail-open → pick_ok）
    score: pick_ok → 1.0 else 0.0（后续可改为软分数）
    """

    name = "rule_expert"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.speed_feature = str(cfg.get("speed_feature") or "ankle_max_speed_norm")
        self.speed_threshold = float(cfg.get("speed_threshold") or 0.08177)
        self.arm_torso_min = float(cfg.get("arm_torso_min") or 90.0)
        self.elbow_min = float(cfg.get("elbow_min") or 150.0)
        self.wrist_elevation_min = float(cfg.get("wrist_elevation_min") or 60.0)
        self.stance_feature = str(cfg.get("stance_feature") or "shoulder_hip_knee_angle_min")
        self.stance_threshold = float(cfg.get("stance_threshold") or 140.0)

    def reset(self) -> None:
        return

    def score(self, row: dict[str, Any]) -> tuple[float, dict[str, Any]]:
        speed = _f(row, self.speed_feature)
        arm = _f(row, "arm_torso_angle_max")
        elbow = _f(row, "elbow_angle_mean")
        wrist_el = _f(row, "wrist_elevation_angle_max")
        stance = _f(row, self.stance_feature)

        speed_high = speed is not None and speed > self.speed_threshold
        triple90 = (
            arm is not None
            and elbow is not None
            and wrist_el is not None
            and arm >= self.arm_torso_min
            and elbow >= self.elbow_min
            and wrist_el >= self.wrist_elevation_min
        )
        is_standing = stance is not None and stance >= self.stance_threshold
        # 速度未知：不 block（fail-open）
        block = bool(speed_high and (not triple90) and is_standing)
        pick_ok = not block
        detail = {
            "speed": speed,
            "speed_high": speed_high,
            "triple90": triple90,
            "is_standing": is_standing,
            "block": block,
            "pick_ok": pick_ok,
        }
        return (1.0 if pick_ok else 0.0), detail

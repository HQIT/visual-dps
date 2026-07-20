"""碰撞前置门控布尔逻辑（移植自 data-collector validate/export 脚本）。"""

from __future__ import annotations

from typing import Any

from services.event_engine.pick_prefilter.config import PickPrefilterConfig


def _float_or_none(val: Any) -> float | None:
    if val is None:
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if not (out == out):  # NaN
        return None
    return out


def _speed_high(row: dict[str, Any], *, speed_feature: str, speed_thr: float) -> bool:
    speed = _float_or_none(row.get(speed_feature))
    return speed is not None and speed > speed_thr


def _conds_met_and(row: dict[str, Any], conds: list[tuple[str, float]]) -> bool:
    hits = 0
    for feat, thr in conds:
        v = _float_or_none(row.get(feat))
        if v is not None and v >= thr:
            hits += 1
    return hits == len(conds)


def _multi_logic_gate_blocks(
    row: dict[str, Any],
    *,
    speed_feature: str,
    speed_thr: float,
    conds: list[tuple[str, float]],
) -> bool:
    """下肢超速且未满足 triple90 豁免 → block。"""
    if not _speed_high(row, speed_feature=speed_feature, speed_thr=speed_thr):
        return False
    if _conds_met_and(row, conds):
        return False
    return True


def _is_standing_row(row: dict[str, Any], *, stance_feat: str, stance_thr: float) -> bool:
    v = _float_or_none(row.get(stance_feat))
    if v is None:
        return True
    return v >= stance_thr


def evaluate_pick_prefilter_block(row: dict[str, Any], cfg: PickPrefilterConfig) -> bool:
    """返回 True 表示应跳过该人的手腕碰撞检测。"""
    triple_conds = [
        ("arm_torso_angle_max", cfg.arm_torso_min),
        ("elbow_angle_mean", cfg.elbow_min),
        ("wrist_elevation_angle_max", cfg.wrist_elevation_min),
    ]
    if not _multi_logic_gate_blocks(
        row,
        speed_feature=cfg.speed_feature,
        speed_thr=cfg.speed_threshold,
        conds=triple_conds,
    ):
        return False
    if cfg.stance_feature:
        if not _is_standing_row(
            row,
            stance_feat=cfg.stance_feature,
            stance_thr=cfg.stance_threshold,
        ):
            return False
    return True

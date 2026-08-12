"""手腕 ∩ 货框：除命中 token 外，同时输出深度/邻框等几何量（G 组基础）。"""

from __future__ import annotations

import math
from typing import Any

import cv2

from pick_state.features.geometry import WRIST_LEFT, WRIST_RIGHT

WRIST_INDICES = (WRIST_LEFT, WRIST_RIGHT)


def _raw_wrist(person: dict[str, Any], idx: int) -> tuple[float, float, float] | None:
    """绕开 geometry.KPT_SCORE_MIN 直接取手腕。

    触发门槛要能独立调低（漏报里有一批人手腕分数 0.19~0.29），
    但角度特征仍走 0.3，否则与训练时的特征口径不一致。
    """
    kpts = person.get("keypoints") or []
    if idx >= len(kpts):
        return None
    kp = kpts[idx]
    if not isinstance(kp, (list, tuple)) or len(kp) < 2:
        return None
    return float(kp[0]), float(kp[1]), float(kp[2]) if len(kp) > 2 else 0.0


class BoxTrigger:
    def __init__(self, boxes: list[Any], *, wrist_score_min: float = 0.3):
        self.boxes = boxes
        self.wrist_score_min = float(wrist_score_min)

    def hits_for_person(self, person: dict[str, Any]) -> list[dict[str, Any]]:
        """返回命中列表，按进框深度降序；depth_ratio 用有符号距离 / 内切半径。"""
        out: dict[str, dict[str, Any]] = {}

        for wrist_idx in WRIST_INDICES:
            pt = _raw_wrist(person, wrist_idx)
            if pt is None or pt[2] < self.wrist_score_min:
                continue
            wx, wy = pt[0], pt[1]

            for box in self.boxes:
                dist = cv2.pointPolygonTest(box.contour, (wx, wy), True)
                if dist < 0:
                    continue
                depth_ratio = min(1.0, dist / box.inradius)
                center_dist = math.hypot(wx - box.center[0], wy - box.center[1])
                prev = out.get(box.token)
                if prev is not None and prev["depth_ratio"] >= depth_ratio:
                    continue
                out[box.token] = {
                    "token": box.token,
                    "box_id": box.box_id,
                    "box": box,
                    "wrist_idx": wrist_idx,
                    "wrist_xy": (wx, wy),
                    "wrist_score": pt[2],
                    "depth_ratio": depth_ratio,
                    "center_dist": center_dist,
                    "box_center": box.center,
                }

        hits = sorted(out.values(), key=lambda h: h["depth_ratio"], reverse=True)
        for i, h in enumerate(hits):
            h["candidate_count"] = len(hits)
            h["margin_gap"] = (
                h["depth_ratio"] - hits[1]["depth_ratio"] if i == 0 and len(hits) > 1 else 0.0
            )
        return hits

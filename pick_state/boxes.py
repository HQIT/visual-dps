"""货框 duck-type：供 BoxTrigger / pair 特征使用（不依赖 collector）。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class BoxDef:
    box_id: str
    token: str
    contour: Any
    center: tuple[float, float]
    inradius: float
    layer: int | None = None
    column: int | None = None


def polygon_center_and_inradius(pts: np.ndarray) -> tuple[tuple[float, float], float]:
    cx = float(np.mean(pts[:, 0]))
    cy = float(np.mean(pts[:, 1]))
    n = len(pts)
    dists: list[float] = []
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        ex, ey = x2 - x1, y2 - y1
        seg_len = math.hypot(ex, ey)
        if seg_len < 1e-6:
            continue
        dists.append(abs(ex * (y1 - cy) - (x1 - cx) * ey) / seg_len)
    return (cx, cy), (min(dists) if dists else 1.0)


def box_from_polygon(box_id: str, polygon: list[list[float]], **extra: Any) -> BoxDef:
    pts = np.array(polygon, dtype=np.float64)
    center, inradius = polygon_center_and_inradius(pts)
    return BoxDef(
        box_id=str(box_id),
        token=f"Box_{box_id}",
        contour=np.int32(pts).reshape((-1, 1, 2)),
        center=center,
        inradius=max(1.0, float(inradius)),
        layer=extra.get("layer"),
        column=extra.get("column"),
    )

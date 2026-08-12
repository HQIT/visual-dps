"""短窗平滑：减轻 17 点抖动对速度/score 的影响。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class SmoothConfig:
    enabled: bool = True
    window_frames: int = 5
    method: str = "ema"  # ema | mean
    ema_alpha: float = 0.35


class ScalarSmoother:
    def __init__(self, cfg: SmoothConfig):
        self.cfg = cfg
        self._hist: deque[float] = deque(maxlen=max(1, int(cfg.window_frames)))
        self._ema: float | None = None

    def reset(self) -> None:
        self._hist.clear()
        self._ema = None

    def update(self, value: float | None) -> float | None:
        if value is None:
            return None
        if not self.cfg.enabled:
            return float(value)
        x = float(value)
        self._hist.append(x)
        if self.cfg.method == "mean":
            return sum(self._hist) / len(self._hist)
        alpha = min(1.0, max(1e-6, float(self.cfg.ema_alpha)))
        self._ema = x if self._ema is None else (alpha * x + (1.0 - alpha) * self._ema)
        return self._ema

"""动作门控：对人打「是否在做拣货动作」分，低于阈值则屏蔽该人所有货框告警。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np


class ActionGate:
    def __init__(self, cfg: dict[str, Any] | None = None):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled"))
        self.threshold = float(cfg.get("threshold", 0.30))
        self.window_frames = int(cfg.get("window_frames", 30))
        self.step = int(cfg.get("step", 2))
        self.model = None
        self.model_path = str(cfg.get("model_path") or "")
        if self.enabled:
            if not self.model_path:
                raise ValueError("action_gate.enabled 但未配置 model_path")
            path = Path(self.model_path)
            if not path.is_file():
                # 相对仓库根
                root = Path(__file__).resolve().parents[1]
                path = root / self.model_path
            if not path.is_file():
                raise FileNotFoundError(f"动作门控模型不存在: {self.model_path}")
            self.model = joblib.load(path)

    def score(self, feat: np.ndarray) -> float:
        if self.model is None:
            return 1.0
        x = np.asarray(feat, dtype=np.float64).reshape(1, -1)
        return float(self.model.predict_proba(x)[0, 1])

    def allow(self, feat: np.ndarray) -> tuple[bool, float]:
        """返回 (是否放行, 动作分)。未启用时始终放行。"""
        if not self.enabled or self.model is None:
            return True, 1.0
        p = self.score(feat)
        return p >= self.threshold, p

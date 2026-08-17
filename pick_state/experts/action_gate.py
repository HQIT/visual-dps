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
        self.backend = str(cfg.get("backend") or "sklearn").lower()
        self.model_path = str(cfg.get("model_path") or "")
        self.onnx_path = str(cfg.get("onnx_path") or "")

        self.model = None
        self._ort_session = None
        self._ort_input: str | None = None

        if not self.enabled:
            return

        if self.backend == "onnx":
            self._load_onnx(cfg)
        else:
            self._load_sklearn(cfg)

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[1]

    def _resolve_path(self, raw: str, *, label: str) -> Path:
        if not raw:
            raise ValueError(f"action_gate.enabled 但未配置 {label}")
        path = Path(raw)
        if not path.is_file():
            path = self._repo_root() / raw
        if not path.is_file():
            raise FileNotFoundError(f"动作门控 {label} 不存在: {raw}")
        return path

    def _default_onnx_path(self) -> Path:
        if self.onnx_path:
            return self._resolve_path(self.onnx_path, label="onnx_path")
        if self.model_path:
            p = Path(self.model_path)
            candidate = p.with_suffix(".onnx")
            if candidate.is_file():
                return candidate
            candidate = self._repo_root() / p.with_suffix(".onnx")
            if candidate.is_file():
                return candidate
        raise FileNotFoundError("action_gate backend=onnx 但未找到 .onnx 模型")

    def _load_sklearn(self, cfg: dict[str, Any]) -> None:
        path = self._resolve_path(self.model_path, label="model_path")
        self.model = joblib.load(path)

    def _load_onnx(self, cfg: dict[str, Any]) -> None:
        import onnxruntime as ort

        path = self._default_onnx_path()
        self._ort_session = ort.InferenceSession(
            str(path), providers=["CPUExecutionProvider"]
        )
        self._ort_input = self._ort_session.get_inputs()[0].name

    def score(self, feat: np.ndarray) -> float:
        if not self.enabled:
            return 1.0
        if self.backend == "onnx":
            return self._score_onnx(feat)
        return self._score_sklearn(feat)

    def _score_sklearn(self, feat: np.ndarray) -> float:
        if self.model is None:
            return 1.0
        x = np.asarray(feat, dtype=np.float64).reshape(1, -1)
        return float(self.model.predict_proba(x)[0, 1])

    def _score_onnx(self, feat: np.ndarray) -> float:
        if self._ort_session is None or self._ort_input is None:
            return 1.0
        x = np.asarray(feat, dtype=np.float32).reshape(1, -1)
        prob = self._ort_session.run(None, {self._ort_input: x})[1]
        return float(prob[0, 1])

    def allow(self, feat: np.ndarray) -> tuple[bool, float]:
        """返回 (是否放行, 动作分)。未启用时始终放行。"""
        if not self.enabled:
            return True, 1.0
        p = self.score(feat)
        return p >= self.threshold, p

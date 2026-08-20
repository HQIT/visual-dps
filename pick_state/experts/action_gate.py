"""动作门控：对人打「是否在做拣货动作」分，低于阈值则屏蔽该人所有货框告警。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from core.ort_runtime import build_action_gate_session_options, ort_session_summary

logger = logging.getLogger(__name__)

# action_gate 特征维（与 ActionSequenceTracker / FEATURE_DIM 一致）
_ACTION_GATE_FEATURE_DIM = 68


def _resolve_pipeline_config(path: str | Path) -> Path:
    p = Path(path)
    if p.is_file():
        return p
    root = Path(__file__).resolve().parents[1]
    for base in (Path.cwd(), root, root.parent):
        cand = base / p
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"pipeline 配置不存在: {path}")


def probe_action_gate_from_pipeline(config_path: str | Path) -> dict[str, Any]:
    """从 pipeline JSON 加载 action_gate 并试跑 ONNX/sklearn（供 worker-2 启动探针与脚本验收）。"""
    cfg_path = _resolve_pipeline_config(config_path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    pair_cfg = data.get("pair_state") or {}
    gate = ActionGate(pair_cfg.get("action_gate") or {})
    info = gate.runtime_info()
    info["pipeline_config"] = str(cfg_path)
    if not gate.enabled:
        info["probe_ok"] = True
        info["probe_note"] = "action_gate 未启用"
        return info
    feat = np.zeros(_ACTION_GATE_FEATURE_DIM, dtype=np.float64)
    try:
        score = gate.score(feat)
        info["probe_score"] = round(float(score), 6)
        info["probe_ok"] = gate.backend == "onnx" and gate._ort_session is not None
        if gate.backend == "sklearn":
            info["probe_ok"] = gate.model is not None
        info["probe_note"] = "试跑 score 成功"
    except Exception as exc:
        info["probe_ok"] = False
        info["probe_error"] = str(exc)
    return info


def format_action_gate_probe_line(info: dict[str, Any]) -> str:
    """单行启动日志，便于 docker logs | grep action_gate。"""
    if not info.get("enabled"):
        return f"action_gate 未启用 pipeline={info.get('pipeline_config', '?')}"
    backend = info.get("backend", "?")
    parts = [
        f"action_gate backend={backend}",
        f"pipeline={info.get('pipeline_config', '?')}",
    ]
    if backend == "onnx":
        parts.append(f"onnx={info.get('onnx_path', '?')}")
        parts.append(f"ort={info.get('onnxruntime_version', '?')}")
        providers = info.get("ort_providers") or []
        parts.append(f"providers={','.join(providers) if providers else '?'}")
        parts.append(f"session={'ready' if info.get('session_ready') else 'missing'}")
    elif backend == "sklearn":
        parts.append(f"joblib={info.get('model_path', '?')}")
    if "probe_score" in info:
        parts.append(f"probe_score={info['probe_score']}")
    parts.append(f"probe_ok={info.get('probe_ok')}")
    return " ".join(parts)


_SHARED_ACTION_GATES: dict[str, "ActionGate"] = {}


def get_shared_action_gate(cfg: dict[str, Any] | None) -> ActionGate:
    """全 worker 进程共享 action_gate（同配置只加载一份 ONNX Session）。"""
    normalized = cfg or {}
    key = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    gate = _SHARED_ACTION_GATES.get(key)
    if gate is None:
        gate = ActionGate(normalized)
        _SHARED_ACTION_GATES[key] = gate
        if gate.enabled:
            logger.info(
                "action_gate 共享实例已创建 backend=%s %s",
                gate.backend,
                format_action_gate_probe_line(gate.runtime_info()),
            )
    return gate


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
        self._resolved_onnx_path: str | None = None
        self._logged_first_onnx_infer = False

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
        self._resolved_onnx_path = str(path)
        sess_options = build_action_gate_session_options()
        self._ort_session = ort.InferenceSession(
            str(path),
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )
        self._ort_input = self._ort_session.get_inputs()[0].name
        providers = self._ort_session.get_providers()
        logger.info(
            "action_gate 已加载 backend=onnx path=%s ort=%s providers=%s input=%s %s",
            path,
            ort.__version__,
            providers,
            self._ort_input,
            ort_session_summary(sess_options),
        )

    def runtime_info(self) -> dict[str, Any]:
        """当前 action_gate 运行时状态（供日志 / 验收脚本）。"""
        info: dict[str, Any] = {
            "enabled": self.enabled,
            "backend": self.backend,
            "threshold": self.threshold,
        }
        if not self.enabled:
            return info
        if self.backend == "onnx":
            info["onnx_path"] = self._resolved_onnx_path or self.onnx_path
            info["session_ready"] = self._ort_session is not None
            info["ort_input"] = self._ort_input
            if self._ort_session is not None:
                info["ort_providers"] = list(self._ort_session.get_providers())
            try:
                import onnxruntime as ort

                info["onnxruntime_version"] = ort.__version__
            except Exception:
                info["onnxruntime_version"] = None
        else:
            info["model_path"] = self.model_path
            info["sklearn_loaded"] = self.model is not None
        return info

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
        if not self._logged_first_onnx_infer:
            self._logged_first_onnx_infer = True
            logger.info(
                "action_gate 首次 ONNX 推理完成 providers=%s score=%.4f",
                self._ort_session.get_providers(),
                float(prob[0, 1]),
            )
        return float(prob[0, 1])

    def allow(self, feat: np.ndarray) -> tuple[bool, float]:
        """返回 (是否放行, 动作分)。未启用时始终放行。"""
        if not self.enabled:
            return True, 1.0
        p = self.score(feat)
        return p >= self.threshold, p

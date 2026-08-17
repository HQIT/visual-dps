"""action_gate ONNX 推理与 sklearn 对齐。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pick_state.experts.action_gate import ActionGate

ROOT = Path(__file__).resolve().parents[1] / "pick_state"
JOBLIB = ROOT / "models/action_gate_v1/model.joblib"
ONNX = ROOT / "models/action_gate_v1/model.onnx"


pytestmark = pytest.mark.skipif(
    not JOBLIB.is_file() or not ONNX.is_file(),
    reason="缺少 action_gate 模型文件",
)


def test_onnx_runtime_parity_with_joblib():
    import joblib
    import onnxruntime as ort

    clf = joblib.load(JOBLIB)
    rng = np.random.default_rng(42)
    x64 = rng.standard_normal((300, int(clf.n_features_in_)), dtype=np.float64)
    x32 = x64.astype(np.float32)
    sk = clf.predict_proba(x64)[:, 1]

    sess = ort.InferenceSession(str(ONNX), providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0].name
    ort_p = sess.run(None, {inp: x32})[1][:, 1]
    assert float(np.max(np.abs(sk - ort_p))) < 1e-5


def test_action_gate_onnx_backend_matches_sklearn():
    rng = np.random.default_rng(7)
    feat = rng.standard_normal(68, dtype=np.float64)

    sk_gate = ActionGate(
        {
            "enabled": True,
            "backend": "sklearn",
            "threshold": 0.30,
            "model_path": "models/action_gate_v1/model.joblib",
        }
    )
    onnx_gate = ActionGate(
        {
            "enabled": True,
            "backend": "onnx",
            "threshold": 0.30,
            "model_path": "models/action_gate_v1/model.joblib",
            "onnx_path": "models/action_gate_v1/model.onnx",
        }
    )
    p_sk = sk_gate.score(feat)
    p_on = onnx_gate.score(feat)
    assert abs(p_sk - p_on) < 1e-5
    ok_sk, _ = sk_gate.allow(feat)
    ok_on, _ = onnx_gate.allow(feat)
    assert ok_sk == ok_on


def test_action_gate_onnx_auto_path_from_model_path():
    """model_path 同目录 .onnx 可自动解析。"""
    gate = ActionGate(
        {
            "enabled": True,
            "backend": "onnx",
            "threshold": 0.30,
            "model_path": "models/action_gate_v1/model.joblib",
        }
    )
    feat = np.zeros(68, dtype=np.float64)
    p = gate.score(feat)
    assert 0.0 <= p <= 1.0

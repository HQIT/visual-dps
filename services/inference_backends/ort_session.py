"""ONNX Runtime Session 默认项：限制 CPU 线程并关闭 busy spin。"""

from __future__ import annotations

import os

_ORT_PATCH_INSTALLED = False


def ort_thread_settings() -> tuple[int, int, bool]:
    """(intra_op, inter_op, allow_spinning) from env."""
    intra_raw = os.environ.get("INFERENCE_ORT_INTRA_THREADS", "2").strip()
    inter_raw = os.environ.get("INFERENCE_ORT_INTER_THREADS", "1").strip()
    spin_raw = os.environ.get("INFERENCE_ORT_ALLOW_SPINNING", "0").strip().lower()
    try:
        intra = max(1, int(intra_raw))
    except ValueError:
        intra = 2
    try:
        inter = max(1, int(inter_raw))
    except ValueError:
        inter = 1
    allow_spinning = spin_raw in ("1", "true", "yes", "on")
    return intra, inter, allow_spinning


def ort_container_env_defaults() -> dict[str, str]:
    """推理容器 env：ORT 线程 + OpenMP 被动等待（与 SessionOptions 对齐）。"""
    intra, inter, allow_spin = ort_thread_settings()
    return {
        "INFERENCE_ORT_INTRA_THREADS": str(intra),
        "INFERENCE_ORT_INTER_THREADS": str(inter),
        "INFERENCE_ORT_ALLOW_SPINNING": "1" if allow_spin else "0",
        "OMP_NUM_THREADS": str(intra),
        "OMP_WAIT_POLICY": "PASSIVE",
        "OPENBLAS_NUM_THREADS": str(intra),
    }


def install_ort_session_defaults() -> None:
    """Patch ort.InferenceSession once（rtmlib 建 Session 前调用）。"""
    global _ORT_PATCH_INSTALLED
    if _ORT_PATCH_INSTALLED:
        return

    import onnxruntime as ort

    intra, inter, allow_spinning = ort_thread_settings()
    orig = ort.InferenceSession

    def _patched(*args, sess_options=None, providers=None, provider_options=None, **kwargs):
        so = sess_options or ort.SessionOptions()
        if sess_options is None:
            so.intra_op_num_threads = intra
            so.inter_op_num_threads = inter
        if not allow_spinning:
            so.add_session_config_entry("session.intra_op.allow_spinning", "0")
            so.add_session_config_entry("session.inter_op.allow_spinning", "0")
        return orig(
            *args,
            sess_options=so,
            providers=providers,
            provider_options=provider_options,
            **kwargs,
        )

    ort.InferenceSession = _patched
    _ORT_PATCH_INSTALLED = True
    print(
        f"ℹ️ ORT SessionOptions: intra={intra} inter={inter} "
        f"allow_spinning={'1' if allow_spinning else '0'}"
    )

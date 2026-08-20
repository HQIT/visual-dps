"""ONNX Runtime / BLAS / OpenCV 线程默认值（多路 infer 避免 CPU 超订）。"""

from __future__ import annotations

import os
from typing import Any

# infer 容器启动时注入；未设置时默认每进程 1 线程
INFER_CPU_THREAD_ENV_DEFAULTS: dict[str, str] = {
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "OPENCV_NUM_THREADS": "1",
    "INFERENCE_ORT_INTRA_OP_THREADS": "1",
    "INFERENCE_ORT_INTER_OP_THREADS": "1",
}

# worker-2 action_gate 小模型默认同上，可单独覆盖 ACTION_GATE_ORT_*
ACTION_GATE_ORT_ENV_DEFAULTS: dict[str, str] = {
    "ACTION_GATE_ORT_INTRA_OP_THREADS": "1",
    "ACTION_GATE_ORT_INTER_OP_THREADS": "1",
}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def apply_cpu_thread_env_defaults(extra: dict[str, str] | None = None) -> None:
    """进程启动早期调用：仅在变量未设置时写入默认值。"""
    merged = dict(INFER_CPU_THREAD_ENV_DEFAULTS)
    if extra:
        merged.update(extra)
    for key, default in merged.items():
        if not os.environ.get(key, "").strip():
            os.environ[key] = default


def infer_thread_env_for_container() -> dict[str, str]:
    """UI 启动 infer 容器时写入 environment 字典。"""
    out: dict[str, str] = {}
    for key, default in INFER_CPU_THREAD_ENV_DEFAULTS.items():
        out[key] = os.environ.get(key, "").strip() or default
    return out


def build_ort_session_options(
    *,
    intra_env: str = "INFERENCE_ORT_INTRA_OP_THREADS",
    inter_env: str = "INFERENCE_ORT_INTER_OP_THREADS",
    intra_default: int = 1,
    inter_default: int = 1,
) -> Any:
    """构建 ORT SessionOptions（intra/inter 默认 1，顺序执行）。"""
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = _int_env(intra_env, intra_default)
    opts.inter_op_num_threads = _int_env(inter_env, inter_default)
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return opts


def build_action_gate_session_options() -> Any:
    return build_ort_session_options(
        intra_env="ACTION_GATE_ORT_INTRA_OP_THREADS",
        inter_env="ACTION_GATE_ORT_INTER_OP_THREADS",
        intra_default=int(ACTION_GATE_ORT_ENV_DEFAULTS["ACTION_GATE_ORT_INTRA_OP_THREADS"]),
        inter_default=int(ACTION_GATE_ORT_ENV_DEFAULTS["ACTION_GATE_ORT_INTER_OP_THREADS"]),
    )


def rtmlib_ort_providers(device: str) -> list:
    """与 rtmlib BaseTool 一致的 EP 列表。"""
    from rtmlib.tools.base import RTMLIB_SETTINGS

    backend = "onnxruntime"
    settings = RTMLIB_SETTINGS[backend]
    if (device not in settings) and ("cuda" in str(device or "").lower()):
        device_id = int(str(device).split(":")[-1])
        return [("CUDAExecutionProvider", {"device_id": device_id})]
    provider = settings.get(device, "CPUExecutionProvider")
    return [provider]


def rebind_rtmlib_ort_session(tool: Any, sess_options: Any) -> None:
    """rtmlib 未暴露 SessionOptions，加载后用受控线程重建 session。"""
    if getattr(tool, "backend", "") != "onnxruntime":
        return
    import onnxruntime as ort

    onnx_model = getattr(tool, "onnx_model", None)
    if not onnx_model:
        return
    providers = rtmlib_ort_providers(str(getattr(tool, "device", "cpu")))
    tool.session = ort.InferenceSession(
        path_or_bytes=onnx_model,
        sess_options=sess_options,
        providers=providers,
    )


def ort_session_summary(sess_options: Any) -> str:
    return (
        f"intra={sess_options.intra_op_num_threads} "
        f"inter={sess_options.inter_op_num_threads}"
    )

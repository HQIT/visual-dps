"""NVIDIA pip CUDA/cuDNN 路径与 ORT 预加载。

- LD_LIBRARY_PATH：docker run 注入（见 inference_container_service）
- preload_cudnn_libs：仅预加载 cuDNN（ctypes），ORT 可用且不与 torch/YOLO 冲突
"""

from __future__ import annotations

import ctypes
import glob
import os
import site

_PIP_NVIDIA_LIB_GLOB = "/usr/local/lib/python3.10/dist-packages/nvidia/*/lib"
_FALLBACK_NVIDIA_LIB_SUBDIRS = (
    "cudnn",
    "cublas",
    "cuda_runtime",
    "cuda_nvrtc",
    "cufft",
    "curand",
    "cusolver",
    "cusparse",
    "nccl",
    "nvtx",
    "cuda_cupti",
)
_CUDNN_LOADED = False


def _cudnn_lib_dirs() -> list[str]:
    dirs: list[str] = []
    for base in site.getsitepackages() + [site.getusersitepackages()]:
        if not base:
            continue
        lib = os.path.join(base, "nvidia", "cudnn", "lib")
        if os.path.isdir(lib):
            dirs.append(lib)
    if dirs:
        return dirs
    return ["/usr/local/lib/python3.10/dist-packages/nvidia/cudnn/lib"]


def preload_cudnn_libs() -> None:
    """在 import onnxruntime 之前调用；仅加载 cuDNN，避免预加载 cublas 等与 torch 冲突。"""
    global _CUDNN_LOADED
    if _CUDNN_LOADED:
        return
    mode = getattr(ctypes, "RTLD_GLOBAL", 0)
    for lib_dir in _cudnn_lib_dirs():
        for path in sorted(glob.glob(os.path.join(lib_dir, "*.so*"))):
            try:
                ctypes.CDLL(path, mode=mode)
            except OSError:
                pass
    _CUDNN_LOADED = True


def _nvidia_pip_lib_dirs() -> list[str]:
    dirs = sorted(glob.glob(_PIP_NVIDIA_LIB_GLOB))
    if dirs:
        return dirs
    base = "/usr/local/lib/python3.10/dist-packages/nvidia"
    return [os.path.join(base, sub, "lib") for sub in _FALLBACK_NVIDIA_LIB_SUBDIRS]


def nvidia_pip_lib_path(*, include_existing: bool = True) -> str:
    parts = _nvidia_pip_lib_dirs()
    parts.extend(["/usr/local/nvidia/lib", "/usr/local/nvidia/lib64"])
    if include_existing:
        existing = os.environ.get("LD_LIBRARY_PATH", "").strip()
        if existing:
            parts.append(existing)
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return ":".join(out)

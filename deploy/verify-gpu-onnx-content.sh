#!/usr/bin/env bash
# 校验 inference-lite-gpu-onnx 镜像内容（torch cu12 / ort / ultralytics≥8.4 / 无 cu13）
# 用法: ./deploy/verify-gpu-onnx-content.sh <image:tag>
# 环境: VERIFY_GPU_SKIP=1 仅检查 Python 包（无 GPU 时）
set -euo pipefail

IMAGE="${1:-}"
[[ -n "${IMAGE}" ]] || { echo "用法: $0 <image:tag>" >&2; exit 1; }

docker image inspect "${IMAGE}" >/dev/null 2>&1 || {
  echo "错误: 镜像不存在: ${IMAGE}" >&2
  exit 1
}

GPU_ARGS=()
if [[ "${VERIFY_GPU_SKIP:-0}" != "1" ]]; then
  if docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi -L >/dev/null 2>&1; then
    GPU_ARGS=(--gpus all)
  else
    echo "warn: 无 GPU，仅做包版本检查" >&2
    VERIFY_GPU_SKIP=1
  fi
fi

docker run --rm -i "${GPU_ARGS[@]}" -e "VERIFY_GPU_SKIP=${VERIFY_GPU_SKIP:-0}" "${IMAGE}" python3 - <<'PY'
import importlib.metadata as m
import sys

try:
    from services.nvidia_pip_cuda import preload_cudnn_libs
    preload_cudnn_libs()
except Exception:
    pass

import onnxruntime as ort
import torch
import ultralytics
from ultralytics.nn.modules.head import Pose26

for name in ("nvidia-cudnn-cu13", "nvidia-cuda-runtime-cu13"):
    try:
        m.version(name)
        print(f"FAIL: 残留 {name}", file=sys.stderr)
        sys.exit(1)
    except m.PackageNotFoundError:
        pass

ver = tuple(int(x) for x in ultralytics.__version__.split(".")[:3] if x.isdigit())
if ver < (8, 4, 0):
    print(f"FAIL: ultralytics 需 >=8.4.0（YOLO26 Pose26），实际 {ultralytics.__version__}", file=sys.stderr)
    sys.exit(1)

cuda_ver = str(torch.version.cuda or "")
if not cuda_ver.startswith("12"):
    print(f"FAIL: torch CUDA 应为 12.x，实际 {cuda_ver} ({torch.__version__})", file=sys.stderr)
    sys.exit(1)

print("OK: ultralytics", ultralytics.__version__, "Pose26", Pose26)
print("OK: torch", torch.__version__, "cuda", cuda_ver)
print("OK: onnxruntime", ort.__version__, "providers", ort.get_available_providers())

if __import__("os").environ.get("VERIFY_GPU_SKIP") == "1":
    sys.exit(0)

import numpy as np
from onnx import TensorProto, helper

X = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3, 32, 32])
Y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 8, 16, 16])
w = helper.make_tensor(
    "w", TensorProto.FLOAT, [8, 3, 3, 3],
    np.random.randn(8, 3, 3, 3).astype(np.float32).tobytes(), raw=True,
)
conv = helper.make_node("Conv", ["x", "w"], ["y"], kernel_shape=[3, 3], strides=[2, 2], pads=[1, 1, 1, 1])
graph = helper.make_graph([conv], "t", [X], [Y], [w])
# opset 11 + IR≤10：兼容 onnxruntime 1.20.x（不支持 IR 13 / opset 13 探针）
model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 11)])
if model.ir_version > 10:
    model.ir_version = 9

sess = ort.InferenceSession(
    model.SerializeToString(),
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)
out = sess.run(None, {"x": np.random.randn(1, 3, 32, 32).astype(np.float32)})[0]
if out.shape != (1, 8, 16, 16):
    raise RuntimeError(f"unexpected shape {out.shape}")
if "CUDAExecutionProvider" not in sess.get_providers():
    raise RuntimeError(f"CUDA EP 未启用: {sess.get_providers()}")
print("OK: CUDAExecutionProvider Conv", out.shape)
PY

#!/usr/bin/env bash
# 只更新 rtsp_capture.py（秒级），打 tag latest 供推理容器使用
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${INFERENCE_LITE_GPU_BASE:-visual-dps-inference-lite-gpu-base:latest}"
if ! docker image inspect "$BASE" >/dev/null 2>&1; then
  echo "未找到 base 镜像 $BASE"
  echo "  已有旧环境可引导: docker tag visual-dps-inference-lite-gpu:pre-pyav $BASE"
  echo "  或全量打 base: ./scripts/build-inference-lite-gpu-base.sh"
  exit 1
fi

docker build -f Dockerfile.inference-lite-gpu-pyav \
  --build-arg "INFERENCE_LITE_GPU_BASE=$BASE" \
  -t visual-dps-inference-lite-gpu:latest .
echo "OK: visual-dps-inference-lite-gpu:latest（仅 rtsp_capture.py）"

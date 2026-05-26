#!/usr/bin/env bash
# 应用层：COPY services/core/worker — 通常几秒～十几秒
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${INFERENCE_LITE_GPU_BASE:-visual-dps-inference-lite-gpu-base:latest}"
if ! docker image inspect "$BASE" >/dev/null 2>&1; then
  echo "未找到 base 镜像 $BASE"
  echo "  已有旧环境可引导: docker tag visual-dps-inference-lite-gpu:pre-pyav $BASE"
  echo "  或: ./scripts/build-inference-lite-gpu-base.sh"
  exit 1
fi

docker compose --profile build-only build visual-dps-inference-lite-gpu
echo "OK: visual-dps-inference-lite-gpu:latest（应用层，base=$BASE）"

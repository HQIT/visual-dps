#!/usr/bin/env bash
# 构建 MediaPipe 轻量推理镜像（本地测试平替，无需 GPU / PyTorch）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

echo "==> 构建 visual-dps-inference-lite ..."
docker compose --profile build-only build visual-dps-inference-lite

echo ""
echo "完成。启动 UI 并指定轻量推理镜像："
echo "  INFERENCE_IMAGE=visual-dps-inference-lite:latest INFERENCE_BACKEND=rtmpose_onnx \\"
echo "    docker compose --profile ui up -d visual-dps-ui"
echo ""
echo "或在 app_config.json / 摄像头配置中设置 models.backend 为 rtmpose_onnx 或 mmpose。"

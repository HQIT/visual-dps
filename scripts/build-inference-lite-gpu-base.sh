#!/usr/bin/env bash
# 重依赖基座：mmpose/mmcv/ffmpeg/模型 — 几个月才需要跑一次（耗时长）
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f docker/prebuild/jellyfin-ffmpeg7_7.1.3-6-jammy_amd64.deb ]]; then
  echo "缺少 Jellyfin FFmpeg jammy .deb (CUDA/NVDEC)，先执行: ./scripts/download-build-assets.sh"
  exit 1
fi
if [[ ! -f docker/prebuild/ffmpeg-master-latest-linux64-gpl.tar.xz ]]; then
  echo "缺少 FFmpeg 软解回退包，先执行: ./scripts/download-build-assets.sh"
  exit 1
fi

docker compose --profile build-only build visual-dps-inference-lite-gpu-base
echo "OK: visual-dps-inference-lite-gpu-base:latest"
echo "下一步日常改代码: ./scripts/build-inference-lite-gpu-image.sh"

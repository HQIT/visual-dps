#!/usr/bin/env bash
# 异地/本地完整构建（不含 gpu-base；base 请单独跑 build-inference-lite-gpu-base.sh）
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> [1/3] inference-lite-gpu 应用层..."
./scripts/build-inference-lite-gpu-image.sh

echo "==> [2/3] 前端 web/dist..."
(cd web && npm run build)

echo "==> [3/3] UI + event-worker 镜像..."
docker compose build visual-dps-ui visual-dps-event-worker

echo "OK. 启动: REDIS_PASSWORD=*** docker compose up -d"

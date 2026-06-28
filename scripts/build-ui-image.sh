#!/usr/bin/env bash
# 本地构建前端并打包 UI Docker 镜像（无需在 Docker 内拉取 node 镜像）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
# shellcheck disable=SC1091
source scripts/lib/load-build-env.sh
load_build_env "${ROOT}"
# shellcheck disable=SC1091
source scripts/lib/docker-image-tag.sh
# shellcheck disable=SC1091
source scripts/lib/sync-visual-dps-image-tag-env.sh
export VISUAL_DPS_IMAGE_TAG="${VISUAL_DPS_IMAGE_TAG:-$(visual_dps_image_tag)}"

echo "==> 构建前端 (web/dist)..."
(cd web && npm run build)

if [[ ! -f web/dist/index.html ]]; then
  echo "错误: web/dist/index.html 不存在，前端构建失败。" >&2
  exit 1
fi

echo "==> 构建 Docker 镜像 visual-dps-ui（VISUAL_DPS_IMAGE_TAG=${VISUAL_DPS_IMAGE_TAG}）..."
docker compose build visual-dps-ui visual-dps-event-worker

UI_REF="visual-dps-visual-dps-ui:${VISUAL_DPS_IMAGE_TAG}"
EVENT_REF="visual-dps-event-worker:${VISUAL_DPS_IMAGE_TAG}"
if [[ "${DOCKER_TAG_ALSO_LATEST:-0}" == "1" ]]; then
  docker tag "${UI_REF}" visual-dps-visual-dps-ui:latest
  docker tag "${EVENT_REF}" visual-dps-event-worker:latest
  echo "  另打标签: visual-dps-visual-dps-ui:latest、visual-dps-event-worker:latest"
fi

sync_visual_dps_image_tag_env "${ROOT}" "${VISUAL_DPS_IMAGE_TAG}"
echo "  UI:    ${UI_REF}"
echo "  Event: ${EVENT_REF}"

if [[ "${1:-}" == "--up" ]]; then
  if [[ -z "${REDIS_PASSWORD:-}" ]]; then
    echo "错误: 请先 export REDIS_PASSWORD=... 或在本目录创建 .env（可参考 .env.example）" >&2
    exit 1
  fi
  echo "==> 启动 compose 服务..."
  docker compose up -d redis mediamtx visual-dps-ui visual-dps-event-worker
  PORT="${UI_PORT:-8045}"
  echo "完成: http://127.0.0.1:${PORT}"
  echo "版本: curl -s http://127.0.0.1:${PORT}/api/version"
fi

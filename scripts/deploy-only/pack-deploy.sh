#!/usr/bin/env bash
# 在部署包根目录重打（从 visual-dps 仓库同步）
set -euo pipefail
ROOT="${VISUAL_DPS_ROOT:-/home/hqit/workspace/visual-dps}"
OUT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST_ROOT="${HOST_PROJECT_ROOT:-${OUT}/app}"
TAG="$(grep -E '^VISUAL_DPS_IMAGE_TAG=' "${OUT}/app/.env" 2>/dev/null | cut -d= -f2- || echo '20260629-test-from-4841de6a-ce4fe0a')"
exec "${ROOT}/scripts/pack-deploy-only.sh" -o "${OUT}" --tag "${TAG}" --host-root "${HOST_ROOT}"

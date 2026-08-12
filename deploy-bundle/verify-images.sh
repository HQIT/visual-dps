#!/usr/bin/env bash
# 校验目标机已 load 的 Docker 镜像（纯部署包，不含 tar）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="${SCRIPT_DIR}"
ENV_FILE="${PKG_ROOT}/app/.env"
SKIP_LITE_CPU=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-lite-cpu) SKIP_LITE_CPU=1; shift ;;
    -h|--help)
      cat <<'EOF'
用法: ./verify-images.sh [--skip-lite-cpu]

  从 app/.env 读取 VISUAL_DPS_IMAGE_TAG，检查本地镜像。
  GPU 部署可 --skip-lite-cpu（缺 CPU lite 时从旧 tag retag 或跳过）。
EOF
      exit 0
      ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

[[ -f "${ENV_FILE}" ]] || { echo "错误: 缺少 ${ENV_FILE}" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

TAG="${VISUAL_DPS_IMAGE_TAG:-}"
[[ -n "${TAG}" ]] || { echo "错误: VISUAL_DPS_IMAGE_TAG 未设置" >&2; exit 1; }

FAIL=0
check_img() {
  local img="$1"
  if docker image inspect "${img}" >/dev/null 2>&1; then
    echo "OK: ${img}"
  else
    echo "FAIL: 缺少 ${img}" >&2
    FAIL=1
  fi
}

echo "==> 校验镜像 tag=${TAG}"

check_img "redis:7"
check_img "bluenviron/mediamtx:1.11.3"
check_img "visual-dps-visual-dps-ui:${TAG}"
check_img "visual-dps-event-worker:${TAG}"
check_img "visual-dps-inference-lite-gpu:${TAG}"
check_img "visual-dps-inference-lite-gpu-onnx:${TAG}"

if [[ "${SKIP_LITE_CPU}" -eq 1 ]]; then
  echo "SKIP: visual-dps-inference-lite:${TAG} (--skip-lite-cpu)"
else
  check_img "visual-dps-inference-lite:${TAG}"
fi

if [[ "${FAIL}" -ne 0 ]]; then
  echo "" >&2
  echo "提示: CPU lite 可从旧 tag retag，例如:" >&2
  echo "  docker tag visual-dps-inference-lite:OLD visual-dps-inference-lite:${TAG}" >&2
  echo "或 GPU 场景: ./verify-images.sh --skip-lite-cpu" >&2
  exit 1
fi
echo "==> 镜像校验通过"

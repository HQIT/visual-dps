#!/usr/bin/env bash
# 校验目标机已 load 的 Docker 镜像（纯部署包，不含 tar）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="${SCRIPT_DIR}"
ENV_FILE="${PKG_ROOT}/app/.env"

export VERIFY_SKIP_LITE_CPU="${VERIFY_SKIP_LITE_CPU:-0}"
export VERIFY_SKIP_WORKER2="${VERIFY_SKIP_WORKER2:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-lite-cpu) VERIFY_SKIP_LITE_CPU=1; shift ;;
    --skip-worker-2) VERIFY_SKIP_WORKER2=1; shift ;;
    -h|--help)
      cat <<'EOF'
用法: ./verify-images.sh [--skip-lite-cpu] [--skip-worker-2]

  从 app/.env 读取 VISUAL_DPS_IMAGE_TAG，检查本地镜像。
  GPU 部署可 --skip-lite-cpu；旧包无 worker-2 时可 --skip-worker-2。
EOF
      exit 0
      ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

[[ -f "${ENV_FILE}" ]] || { echo "错误: 缺少 ${ENV_FILE}" >&2; exit 1; }
export VISUAL_DPS_PKG_ROOT="${PKG_ROOT}"
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

LIB="${PKG_ROOT}/scripts/lib/verify-deploy-images.sh"
if [[ ! -f "${LIB}" ]]; then
  LIB="$(cd "${SCRIPT_DIR}/../.." && pwd)/scripts/lib/verify-deploy-images.sh"
fi
# shellcheck disable=SC1090
source "${LIB}"

visual_dps_verify_deploy_images "$@"

#!/usr/bin/env bash
# 纯部署包校验（无 bundle.tar）
set -euo pipefail

PKG_ROOT="$(cd "${1:-.}" && pwd)"
FAIL=0

note_fail() { echo "FAIL: $*" >&2; FAIL=1; }
note_ok() { echo "OK: $*"; }

echo "==> 校验部署包: ${PKG_ROOT}"

for f in install.sh verify-images.sh app/docker-compose.deploy.yml app/.env app/app_config.json DEPLOY-0629.md PACKAGE_INFO.txt; do
  [[ -f "${PKG_ROOT}/${f}" ]] && note_ok "${f}" || note_fail "缺少 ${f}"
done

# 推理 bind mount 源码
# shellcheck disable=SC1091
source "${PKG_ROOT}/scripts/infer-bind-mounts.sh" 2>/dev/null \
  || source "$(dirname "${BASH_SOURCE[0]}")/infer-bind-mounts.sh"
check_infer_bind_mounts "${PKG_ROOT}/app" || FAIL=1

# shellcheck disable=SC1091
source "${PKG_ROOT}/app/deploy/check-model-weights.sh" 2>/dev/null \
  || source "${PKG_ROOT}/deploy/check-model-weights.sh"

if [[ -d "${PKG_ROOT}/weights" ]]; then
  note_ok "weights/ 目录"
  visual_dps_check_models_dir "${PKG_ROOT}/weights" || note_fail "weights 不完整"
  [[ -f "${PKG_ROOT}/weights/SHA256SUMS" ]] && note_ok "weights/SHA256SUMS" || note_fail "缺少 weights/SHA256SUMS"
else
  note_fail "缺少 weights/"
fi

TAG=""
if [[ -f "${PKG_ROOT}/app/.env" ]]; then
  TAG="$(grep -E '^VISUAL_DPS_IMAGE_TAG=' "${PKG_ROOT}/app/.env" | cut -d= -f2- || true)"
  [[ -n "${TAG}" ]] && note_ok "VISUAL_DPS_IMAGE_TAG=${TAG}" || note_fail "缺少 VISUAL_DPS_IMAGE_TAG"
  HPR="$(grep -E '^HOST_PROJECT_ROOT=' "${PKG_ROOT}/app/.env" | cut -d= -f2- || true)"
  [[ -n "${HPR}" ]] && note_ok "HOST_PROJECT_ROOT=${HPR}" || note_fail "缺少 HOST_PROJECT_ROOT"
fi

if [[ "${FAIL}" -ne 0 ]]; then
  echo "校验未通过" >&2
  exit 1
fi
echo "==> 全部校验通过"

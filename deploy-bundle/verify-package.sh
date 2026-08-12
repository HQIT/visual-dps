#!/usr/bin/env bash
# 校验分拆 tar 离线包（无 bundle.tar）
set -euo pipefail

PKG_ROOT="$(cd "${1:-.}" && pwd)"
FAIL=0

note_fail() { echo "FAIL: $*" >&2; FAIL=1; }
note_ok() { echo "OK: $*"; }

echo "==> 校验离线包: ${PKG_ROOT}"

for f in install.sh verify-images.sh scripts/load-split-images.sh \
  app/docker-compose.deploy.yml app/.env app/app_config.json \
  docker-images/images.manifest PACKAGE_INFO.txt OFFLINE-QUICKSTART.md; do
  [[ -f "${PKG_ROOT}/${f}" ]] && note_ok "${f}" || note_fail "缺少 ${f}"
done

# shellcheck disable=SC1091
source "${PKG_ROOT}/scripts/infer-bind-mounts.sh" 2>/dev/null \
  || source "$(dirname "${BASH_SOURCE[0]}")/infer-bind-mounts.sh"
check_infer_bind_mounts "${PKG_ROOT}/app" || note_fail "推理 bind mount 文件不完整"

MANIFEST="${PKG_ROOT}/docker-images/images.manifest"
if [[ -f "${MANIFEST}" ]]; then
  prev_tar=""
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line//$'\r'/}"
    [[ -z "${line}" || "${line}" =~ ^# ]] && continue
    case "${line}" in
      tar=*)
        prev_tar="${line#tar=}"
        [[ -f "${PKG_ROOT}/docker-images/${prev_tar}" ]] \
          && note_ok "docker-images/${prev_tar}" \
          || note_fail "缺少 docker-images/${prev_tar}"
        ;;
      image=*)
        prev_tar=""
        ;;
    esac
  done < "${MANIFEST}"
fi

# shellcheck disable=SC1091
source "${PKG_ROOT}/app/deploy/check-model-weights.sh" 2>/dev/null

if [[ -d "${PKG_ROOT}/weights" ]]; then
  note_ok "weights/ 目录"
  visual_dps_check_models_dir "${PKG_ROOT}/weights" || note_fail "weights 不完整"
  [[ -f "${PKG_ROOT}/weights/SHA256SUMS" ]] && note_ok "weights/SHA256SUMS" || note_fail "缺少 weights/SHA256SUMS"
else
  note_fail "缺少 weights/"
fi

if [[ "${FAIL}" -ne 0 ]]; then
  echo "校验未通过" >&2
  exit 1
fi
echo "==> 包结构校验通过"

#!/usr/bin/env bash
# 按 images.manifest 逐个 docker load（不合并 bundle）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMG_DIR="${PKG_ROOT}/docker-images"
MANIFEST="${IMG_DIR}/images.manifest"

[[ -f "${MANIFEST}" ]] || { echo "错误: 缺少 ${MANIFEST}" >&2; exit 1; }

LIB="${PKG_ROOT}/scripts/lib/docker-cmd.sh"
[[ -f "${LIB}" ]] || LIB="$(cd "${SCRIPT_DIR}/../lib" && pwd)/docker-cmd.sh"
# shellcheck disable=SC1090
source "${LIB}"

load_one() {
  local tar_file="$1"
  local image_ref="$2"
  local path="${IMG_DIR}/${tar_file}"
  [[ -f "${path}" ]] || { echo "错误: 缺少 ${path}" >&2; exit 1; }
  if docker_cmd image inspect "${image_ref}" >/dev/null 2>&1; then
    echo "SKIP (已存在): ${image_ref}"
    return 0
  fi
  echo "==> docker load -i ${tar_file}  (${image_ref})"
  docker_cmd load -i "${path}"
}

echo "==> 加载分拆镜像 (${MANIFEST})"
prev_tar=""
while IFS= read -r line || [[ -n "${line}" ]]; do
  line="${line//$'\r'/}"
  [[ -z "${line}" || "${line}" =~ ^# ]] && continue
  case "${line}" in
    tar=*)
      prev_tar="${line#tar=}"
      ;;
    image=*)
      [[ -n "${prev_tar}" ]] || { echo "错误: manifest 中 image= 前缺少 tar= 行: ${line}" >&2; exit 1; }
      load_one "${prev_tar}" "${line#image=}"
      prev_tar=""
      ;;
  esac
done < "${MANIFEST}"

[[ -z "${prev_tar}" ]] || { echo "错误: manifest 末尾 tar= 缺少对应 image=" >&2; exit 1; }
echo "==> 分拆镜像加载完成"

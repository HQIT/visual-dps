#!/usr/bin/env bash
# 将 VISUAL_DPS_IMAGE_TAG 与推理镜像引用写入项目 .env
set -euo pipefail

_sync_env_key() {
  local env_file="$1"
  local key="$2"
  local value="$3"
  if grep -q "^${key}=" "${env_file}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${env_file}"
  else
    printf '\n%s=%s\n' "${key}" "${value}" >> "${env_file}"
  fi
}

sync_visual_dps_image_tag_env() {
  local root="$1"
  local tag="$2"
  local env_file="${root}/.env"

  if [[ -z "${tag}" ]]; then
    echo "sync_visual_dps_image_tag_env: 空 tag" >&2
    return 1
  fi

  if [[ ! -f "${env_file}" ]]; then
    printf 'VISUAL_DPS_IMAGE_TAG=%s\n' "${tag}" > "${env_file}"
  fi

  _sync_env_key "${env_file}" "VISUAL_DPS_IMAGE_TAG" "${tag}"
  _sync_env_key "${env_file}" "INFERENCE_LITE_IMAGE" "visual-dps-inference-lite:${tag}"
  _sync_env_key "${env_file}" "INFERENCE_LITE_GPU_IMAGE" "visual-dps-inference-lite-gpu:${tag}"
  _sync_env_key "${env_file}" "INFERENCE_LITE_GPU_ONNX_IMAGE" "visual-dps-inference-lite-gpu-onnx:${tag}"

  echo "  已写入 ${env_file}: VISUAL_DPS_IMAGE_TAG=${tag}"
  echo "  已同步 INFERENCE_LITE_* → tag=${tag}"
}

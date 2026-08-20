#!/usr/bin/env bash
# 部署镜像校验（verify-images.sh / install.sh 共用）
# 依赖: 已 source app/.env（VISUAL_DPS_IMAGE_TAG、INFERENCE_LITE_*）
# 环境: VERIFY_SKIP_LITE_CPU=1 | VERIFY_SKIP_WORKER2=1

visual_dps_image_ref_from_env() {
  local env_val="${1:-}"
  local repo="$2"
  local tag="$3"
  if [[ -n "${env_val}" ]]; then
    echo "${env_val}"
  else
    echo "${repo}:${tag}"
  fi
}

visual_dps_verify_deploy_images() {
  local skip_lite_cpu="${VERIFY_SKIP_LITE_CPU:-0}"
  local skip_worker2="${VERIFY_SKIP_WORKER2:-0}"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-lite-cpu) skip_lite_cpu=1; shift ;;
      --skip-worker-2) skip_worker2=1; shift ;;
      *) echo "未知参数: $1" >&2; return 1 ;;
    esac
  done

  local tag="${VISUAL_DPS_IMAGE_TAG:-}"
  [[ -n "${tag}" ]] || { echo "错误: VISUAL_DPS_IMAGE_TAG 未设置" >&2; return 1; }

  local lite gpu onnx
  lite="$(visual_dps_image_ref_from_env "${INFERENCE_LITE_IMAGE:-}" "visual-dps-inference-lite" "${tag}")"
  gpu="$(visual_dps_image_ref_from_env "${INFERENCE_LITE_GPU_IMAGE:-}" "visual-dps-inference-lite-gpu" "${tag}")"
  onnx="$(visual_dps_image_ref_from_env "${INFERENCE_LITE_GPU_ONNX_IMAGE:-}" "visual-dps-inference-lite-gpu-onnx" "${tag}")"

  local lib_docker="${VISUAL_DPS_PKG_ROOT:-}/scripts/lib/docker-cmd.sh"
  if [[ -f "${lib_docker}" ]]; then
    # shellcheck disable=SC1090
    source "${lib_docker}"
  elif [[ -f "$(dirname "${BASH_SOURCE[0]}")/docker-cmd.sh" ]]; then
    # shellcheck disable=SC1091
    source "$(dirname "${BASH_SOURCE[0]}")/docker-cmd.sh"
  else
    docker_cmd() { docker "$@"; }
  fi

  local fail=0
  _vdpi_check() {
    local img="$1"
    if docker_cmd image inspect "${img}" >/dev/null 2>&1; then
      echo "OK: ${img}"
    else
      echo "FAIL: 缺少 ${img}" >&2
      fail=1
    fi
  }

  echo "==> 校验镜像 UI/Event tag=${tag}"

  _vdpi_check "redis:7"
  _vdpi_check "bluenviron/mediamtx:1.11.3"
  _vdpi_check "visual-dps-visual-dps-ui:${tag}"
  _vdpi_check "visual-dps-event-worker:${tag}"
  _vdpi_check "${gpu}"
  _vdpi_check "${onnx}"

  if [[ "${skip_lite_cpu}" -eq 1 ]]; then
    echo "SKIP: ${lite} (--skip-lite-cpu)"
  else
    _vdpi_check "${lite}"
  fi

  if [[ "${skip_worker2}" -eq 1 ]]; then
    echo "SKIP: visual-dps-event-worker-2:${tag} (--skip-worker-2)"
  else
    _vdpi_check "visual-dps-event-worker-2:${tag}"
  fi

  if [[ "${fail}" -ne 0 ]]; then
    echo "" >&2
    echo "提示: 推理镜像可从旧 tag retag，或 GPU 场景: ./verify-images.sh --skip-lite-cpu" >&2
    echo "      worker-2: ./scripts/build-event-worker-2-image.sh" >&2
    return 1
  fi
  echo "==> 镜像校验通过"
  return 0
}

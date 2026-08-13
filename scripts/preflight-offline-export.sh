#!/usr/bin/env bash
# 离线打包前预检：权重、镜像齐全、gpu-onnx 栈校验
# 用法: ./scripts/preflight-offline-export.sh --inference all|lite|gpu|gpu-onnx|base [--no-models]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

INFERENCE_MODE="all"
REQUIRE_MODELS=1
SKIP_WORKER2=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --inference) INFERENCE_MODE="$2"; shift 2 ;;
    --no-models) REQUIRE_MODELS=0; shift ;;
    --skip-worker-2) SKIP_WORKER2=1; shift ;;
    -h|--help)
      echo "用法: $0 --inference MODE [--no-models] [--skip-worker-2]"
      exit 0
      ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

# shellcheck disable=SC1091
source "${ROOT}/scripts/lib/load-build-env.sh"
load_build_env "${ROOT}"

set -a
# shellcheck disable=SC1090
[[ -f "${ROOT}/.env" ]] && source "${ROOT}/.env"
set +a

require_image() {
  docker image inspect "$1" >/dev/null 2>&1 || {
    echo "FAIL: 缺少镜像 $1" >&2
    exit 1
  }
  echo "OK: $1"
}

resolve_inference_repo_tag() {
  local repo="$1" preferred="${2:-}"
  if [[ -n "${preferred}" ]] && docker image inspect "${preferred}" >/dev/null 2>&1; then
    echo "${preferred}"
    return
  fi
  local tag="${VISUAL_DPS_IMAGE_TAG:-}"
  if [[ -n "${tag}" ]] && docker image inspect "${repo}:${tag}" >/dev/null 2>&1; then
    echo "${repo}:${tag}"
    return
  fi
  docker images --format '{{.Repository}}:{{.Tag}}' "${repo}" 2>/dev/null \
    | grep -v ':<none>' | grep -v ':latest' | sort | tail -n 1
}

resolve_repo_tag() {
  local repo="$1" preferred="${2:-}"
  if [[ -n "${preferred}" ]] && docker image inspect "${preferred}" >/dev/null 2>&1; then
    echo "${preferred}"
    return
  fi
  if docker image inspect "${repo}:latest" >/dev/null 2>&1; then
    echo "${repo}:latest"
    return
  fi
  docker images --format '{{.Repository}}:{{.Tag}}' "${repo}" 2>/dev/null \
    | grep -v ':<none>' | grep -v ':latest' | sort | tail -n 1
}

warn_latest_only() {
  local img="$1"
  if [[ "${img}" == *:latest ]]; then
    echo "WARN: ${img} 为 :latest，建议构建后使用日期 tag（见 AGENTS.md）" >&2
  fi
}

echo "==> 预检 inference=${INFERENCE_MODE}"

if [[ "${REQUIRE_MODELS}" -eq 1 ]]; then
  # shellcheck disable=SC1091
  source "${ROOT}/deploy/check-model-weights.sh"
  visual_dps_check_model_weights "${ROOT}/localdata" || {
    echo "FAIL: 权重不齐，请先 ./scripts/download-model-weights.sh" >&2
    exit 1
  }
  echo "OK: localdata/models 权重齐全"
fi

UI="$(resolve_repo_tag "visual-dps-visual-dps-ui" "visual-dps-visual-dps-ui:${VISUAL_DPS_IMAGE_TAG:-}")"
EV="$(resolve_repo_tag "visual-dps-event-worker" "visual-dps-event-worker:${VISUAL_DPS_IMAGE_TAG:-}")"
[[ -n "${UI}" && -n "${EV}" ]] || { echo "FAIL: 缺少 UI/Event 镜像" >&2; exit 1; }
require_image "redis:7"
require_image "bluenviron/mediamtx:1.11.3"
require_image "${UI}"
require_image "${EV}"
if [[ "${SKIP_WORKER2}" -eq 0 ]]; then
  EV2="$(resolve_repo_tag "visual-dps-event-worker-2" "visual-dps-event-worker-2:${VISUAL_DPS_IMAGE_TAG:-}")"
  [[ -n "${EV2}" ]] || { echo "FAIL: 缺少 event-worker-2 镜像（./scripts/build-event-worker-2-image.sh 或 --skip-worker-2）" >&2; exit 1; }
  require_image "${EV2}"
else
  echo "SKIP: visual-dps-event-worker-2 (--skip-worker-2)"
fi
warn_latest_only "${UI}"

ONNX_IMAGE=""
case "${INFERENCE_MODE}" in
  base) ;;
  lite)
    lite="$(resolve_inference_repo_tag "visual-dps-inference-lite" "${INFERENCE_LITE_IMAGE:-}")"
    [[ -n "${lite}" ]] || { echo "FAIL: 缺少 inference-lite" >&2; exit 1; }
    require_image "${lite}"
    ;;
  gpu)
    gpu="$(resolve_inference_repo_tag "visual-dps-inference-lite-gpu" "${INFERENCE_LITE_GPU_IMAGE:-}")"
    [[ -n "${gpu}" ]] || { echo "FAIL: 缺少 inference-lite-gpu" >&2; exit 1; }
    require_image "${gpu}"
    ;;
  gpu-onnx)
    ONNX_IMAGE="$(resolve_inference_repo_tag "visual-dps-inference-lite-gpu-onnx" "${INFERENCE_LITE_GPU_ONNX_IMAGE:-}")"
    [[ -n "${ONNX_IMAGE}" ]] || { echo "FAIL: 缺少 inference-lite-gpu-onnx" >&2; exit 1; }
    require_image "${ONNX_IMAGE}"
    warn_latest_only "${ONNX_IMAGE}"
    ;;
  all)
    lite="$(resolve_inference_repo_tag "visual-dps-inference-lite" "")"
    gpu="$(resolve_inference_repo_tag "visual-dps-inference-lite-gpu" "")"
    onnx="$(resolve_inference_repo_tag "visual-dps-inference-lite-gpu-onnx" "${INFERENCE_LITE_GPU_ONNX_IMAGE:-}")"
    [[ -n "${lite}" && -n "${gpu}" && -n "${onnx}" ]] || {
      echo "FAIL: 完整包需 lite + lite-gpu + lite-gpu-onnx" >&2
      exit 1
    }
    require_image "${lite}"
    require_image "${gpu}"
    require_image "${onnx}"
    ONNX_IMAGE="${onnx}"
    warn_latest_only "${onnx}"
    ;;
  *)
    echo "FAIL: 未知 --inference ${INFERENCE_MODE}" >&2
    exit 1
    ;;
esac

if [[ -n "${ONNX_IMAGE}" ]]; then
  echo "==> 校验 gpu-onnx 栈: ${ONNX_IMAGE}"
  VERIFY_GPU_SKIP="${VERIFY_GPU_SKIP:-0}" "${ROOT}/deploy/verify-gpu-onnx-content.sh" "${ONNX_IMAGE}"
fi

echo "==> 预检通过"

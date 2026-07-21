#!/usr/bin/env bash
# 离线包 install：先 load 分拆镜像，再安装权重并 compose up
set -euo pipefail

HOST_IP=""
WEIGHTS_DIR=""
STOP_INFER=0
SKIP_LOAD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST_IP="$2"; shift 2 ;;
    --weights-dir) WEIGHTS_DIR="$2"; shift 2 ;;
    --stop-infer) STOP_INFER=1; shift ;;
    --skip-load) SKIP_LOAD=1; shift ;;
    -h|--help)
      cat <<'EOF'
用法: ./install.sh [--host IP] [--weights-dir DIR] [--stop-infer] [--skip-load]

  默认从 docker-images/*.tar 逐个 docker load，再启动 compose。
  --skip-load  镜像已 load 时跳过（须 ./verify-images.sh --skip-lite-cpu 通过）
EOF
      exit 0
      ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="${SCRIPT_DIR}"
APP_DIR="${PKG_ROOT}/app"
COMPOSE_FILE="${APP_DIR}/docker-compose.deploy.yml"
[[ -f "${COMPOSE_FILE}" ]] || COMPOSE_FILE="${APP_DIR}/docker-compose.yml"

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "${COMPOSE_FILE}" "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose -f "${COMPOSE_FILE}" "$@"
  else
    echo "错误: 需要 docker compose 或 docker-compose" >&2
    exit 1
  fi
}

[[ -f "${APP_DIR}/.env" ]] || { echo "错误: 缺少 ${APP_DIR}/.env" >&2; exit 1; }

if [[ "${SKIP_LOAD}" -eq 0 ]]; then
  "${SCRIPT_DIR}/scripts/load-split-images.sh"
fi

if [[ -n "${HOST_IP}" ]]; then
  if grep -q '^MEDIAMTX_PUBLIC_HOST=' "${APP_DIR}/.env"; then
    sed -i "s|^MEDIAMTX_PUBLIC_HOST=.*|MEDIAMTX_PUBLIC_HOST=${HOST_IP}|" "${APP_DIR}/.env"
  else
    echo "MEDIAMTX_PUBLIC_HOST=${HOST_IP}" >> "${APP_DIR}/.env"
  fi
  echo "已设置 MEDIAMTX_PUBLIC_HOST=${HOST_IP}"
fi

if grep -q '^HOST_PROJECT_ROOT=/path/to/' "${APP_DIR}/.env" 2>/dev/null \
  || ! grep -q '^HOST_PROJECT_ROOT=' "${APP_DIR}/.env" 2>/dev/null; then
  sed -i "s|^HOST_PROJECT_ROOT=.*|HOST_PROJECT_ROOT=${APP_DIR}|" "${APP_DIR}/.env" 2>/dev/null \
    || echo "HOST_PROJECT_ROOT=${APP_DIR}" >> "${APP_DIR}/.env"
  echo "已设置 HOST_PROJECT_ROOT=${APP_DIR}"
fi

if grep -q '^REDIS_PASSWORD=change-me' "${APP_DIR}/.env" 2>/dev/null \
  || grep -q '^REDIS_PASSWORD=$' "${APP_DIR}/.env" 2>/dev/null; then
  echo "请先在 ${APP_DIR}/.env 中设置 REDIS_PASSWORD" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${APP_DIR}/.env" 2>/dev/null || true
set +a

HPR="${HOST_PROJECT_ROOT:-}"
# shellcheck disable=SC1091
source "${PKG_ROOT}/scripts/infer-bind-mounts.sh" 2>/dev/null \
  || source "${SCRIPT_DIR}/infer-bind-mounts.sh"
for need in "${INFER_BIND_MOUNT_FILES[@]}"; do
  [[ -f "${HPR}/${need}" ]] || { echo "错误: HOST_PROJECT_ROOT=${HPR} 缺少 ${need}" >&2; exit 1; }
done

if [[ "${INFERENCE_USE_GPU:-0}" == "1" && -n "${INFERENCE_LITE_GPU_ONNX_IMAGE:-}" ]]; then
  VSCRIPT="${APP_DIR}/deploy/verify-gpu-onnx-content.sh"
  if [[ -x "${VSCRIPT}" ]]; then
    echo "==> 校验 gpu-onnx: ${INFERENCE_LITE_GPU_ONNX_IMAGE}"
    VERIFY_GPU_SKIP="${VERIFY_GPU_SKIP:-0}" "${VSCRIPT}" "${INFERENCE_LITE_GPU_ONNX_IMAGE}" || {
      echo "错误: gpu-onnx 镜像未通过校验" >&2
      exit 1
    }
  fi
fi

install_weights() {
  local src="$1"
  local dest="${APP_DIR}/localdata/models"
  mkdir -p "${dest}/rtmpose_onnx" "${dest}/yolo_pose"
  echo "==> 安装权重 ${src} -> ${dest}"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "${src}/rtmpose_onnx/" "${dest}/rtmpose_onnx/"
    rsync -a "${src}/yolo_pose/" "${dest}/yolo_pose/"
  else
    cp -a "${src}/rtmpose_onnx/." "${dest}/rtmpose_onnx/"
    cp -a "${src}/yolo_pose/." "${dest}/yolo_pose/"
  fi
}

# shellcheck disable=SC1091
source "${APP_DIR}/deploy/check-model-weights.sh"

if [[ -z "${WEIGHTS_DIR}" && -d "${PKG_ROOT}/weights" ]]; then
  WEIGHTS_DIR="${PKG_ROOT}/weights"
fi

if [[ -n "${WEIGHTS_DIR}" && -d "${WEIGHTS_DIR}" ]]; then
  install_weights "${WEIGHTS_DIR}"
  visual_dps_check_model_weights "${APP_DIR}/localdata" || {
    echo "错误: 模型权重缺失或不完整" >&2
    exit 1
  }
  if [[ -f "${WEIGHTS_DIR}/SHA256SUMS" ]]; then
    echo "==> 校验 SHA256SUMS..."
    (cd "${WEIGHTS_DIR}" && sha256sum -c SHA256SUMS)
  fi
fi

REGEN_SCRIPT="${APP_DIR}/deploy/regenerate-mediamtx-config.sh"
if [[ -x "${REGEN_SCRIPT}" ]]; then
  "${REGEN_SCRIPT}" "${APP_DIR}"
fi

echo "==> 停止旧 compose 栈..."
cd "${APP_DIR}"
compose_cmd down 2>/dev/null || true

if [[ "${STOP_INFER}" -eq 1 ]]; then
  docker ps -a --format '{{.Names}}' | grep -E '^visual-dps-infer-' | xargs -r docker rm -f || true
fi

echo "==> 启动服务..."
compose_cmd up -d

set -a && source ./.env && set +a
HOST="${MEDIAMTX_PUBLIC_HOST:-127.0.0.1}"
PORT="${UI_PORT:-8045}"
sleep 3
echo ""
echo "完成。http://${HOST}:${PORT}/"
echo "镜像 tag: ${VISUAL_DPS_IMAGE_TAG:-}"
[[ -f "${PKG_ROOT}/BUILD_TAG.txt" ]] && cat "${PKG_ROOT}/BUILD_TAG.txt"
echo "版本: curl -s http://${HOST}:${PORT}/api/version"

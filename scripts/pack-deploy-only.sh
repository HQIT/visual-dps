#!/usr/bin/env bash
# 打纯部署包（不含 docker 镜像）：app + weights + install/verify 脚本
# 用法: ./scripts/pack-deploy-only.sh [-o ~/workspace/visual-dps-0629-deploy] [--host-root PATH]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

TAG="${VISUAL_DPS_IMAGE_TAG:-20260629-test-from-4841de6a-ce4fe0a}"
OUT="${HOME}/workspace/visual-dps-0629-deploy"
HOST_ROOT="/home/hqit/workspace/visual-dps-0629-deploy/app"
KEEP_EXISTING=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) OUT="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --host-root) HOST_ROOT="$2"; shift 2 ;;
    --keep-existing) KEEP_EXISTING=1; shift ;;
    -h|--help)
      cat <<'EOF'
用法: ./scripts/pack-deploy-only.sh [-o DIR] [--tag TAG] [--host-root PATH] [--keep-existing]

  --host-root  写入 app/.env 的 HOST_PROJECT_ROOT（153 上部署目录绝对路径）
  --keep-existing  不删除已有包目录（仅更新 app/weights）
EOF
      exit 0
      ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

PKG="${OUT}"
APP="${PKG}/app"
WEIGHTS="${PKG}/weights"

echo "==> 输出: ${PKG}"
echo "    tag: ${TAG}"
echo "    HOST_PROJECT_ROOT: ${HOST_ROOT}"

if [[ "${KEEP_EXISTING}" -eq 1 && -d "${PKG}" ]]; then
  echo "==> 保留已有包目录（--keep-existing）"
  mkdir -p "${APP}/localdata/json/cameras" "${APP}/localdata/logs" "${APP}/localdata/inference" "${APP}/localdata/frames" "${WEIGHTS}"
else
  rm -rf "${PKG}"
  mkdir -p "${APP}/localdata/json/cameras" "${APP}/localdata/logs" "${APP}/localdata/inference" "${APP}/localdata/frames" "${WEIGHTS}"
fi

# compose + 配置
cp "${ROOT}/docker-compose.yml" "${APP}/"
cp "${ROOT}/docker-compose.deploy.yml" "${APP}/"
cp "${ROOT}/app_config.json" "${APP}/"
cp "${ROOT}/version.json" "${APP}/"
cp -a "${ROOT}/deploy" "${APP}/"
[[ -f "${ROOT}/.env" ]] && cp "${ROOT}/.env" "${APP}/.env" || cp "${ROOT}/deploy/offline.env.example" "${APP}/.env"

patch_env() {
  local key="$1" val="$2"
  [[ -n "${val}" ]] || return 0
  if grep -q "^${key}=" "${APP}/.env" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${APP}/.env"
  else
    printf '\n%s=%s\n' "${key}" "${val}" >> "${APP}/.env"
  fi
}

patch_env "VISUAL_DPS_IMAGE_TAG" "${TAG}"
patch_env "INFERENCE_LITE_IMAGE" "visual-dps-inference-lite:${TAG}"
patch_env "INFERENCE_LITE_GPU_IMAGE" "visual-dps-inference-lite-gpu:${TAG}"
patch_env "INFERENCE_LITE_GPU_ONNX_IMAGE" "visual-dps-inference-lite-gpu-onnx:${TAG}"
patch_env "HOST_PROJECT_ROOT" "${HOST_ROOT}"
patch_env "TZ" "Asia/Shanghai"

# localdata（不含 models/logs/frames/inference）
RSYNC_EXCLUDES=(--exclude 'models/**' --exclude 'logs/**' --exclude 'frames/**' --exclude 'inference/**' --exclude 'upload/**' --exclude 'last_frame.jpg' --exclude 'json/annotation_*.json' --exclude 'mediamtx.yml')
if [[ -d "${ROOT}/localdata" ]]; then
  rsync -a "${RSYNC_EXCLUDES[@]}" "${ROOT}/localdata/" "${APP}/localdata/"
fi
[[ -f "${APP}/localdata/camera_ips.json" ]] || cp "${ROOT}/deploy/camera_ips.example.json" "${APP}/localdata/camera_ips.json"
rm -f "${APP}/localdata/mediamtx.yml"
[[ -f "${APP}/localdata/json/precise_boxes_new.json" ]] || echo '{}' > "${APP}/localdata/json/precise_boxes_new.json"

# 推理容器 bind mount 所需源码（UI 启 infer 时挂载宿主机路径）
mkdir -p "${APP}/core" "${APP}/services/inference_backends"
cp "${ROOT}/inference_worker.py" "${APP}/"
cp "${ROOT}/core/config.py" "${APP}/core/"
cp "${ROOT}/core/ort_runtime.py" "${APP}/core/"
for rel in \
  services/inference_service.py \
  services/hwaccel_probe.py \
  services/nvidia_pip_cuda.py \
  services/rtsp_capture.py \
  services/wall_clock.py \
  services/pipeline_log.py \
  services/pose_bus.py \
  services/runtime_config_service.py \
  services/inference_backends/__init__.py \
  services/inference_backends/model_registry.py \
  services/inference_backends/rtmpose_onnx_backend.py \
  services/inference_backends/onnx_assets.py \
  services/inference_backends/yolo_pose_backend.py
do
  mkdir -p "${APP}/$(dirname "${rel}")"
  cp "${ROOT}/${rel}" "${APP}/${rel}"
done

# 权重
# shellcheck disable=SC1091
source "${ROOT}/deploy/check-model-weights.sh"
echo "==> 检查源机权重..."
visual_dps_check_model_weights "${ROOT}/localdata"
echo "==> 复制权重 -> ${WEIGHTS}/"
rsync -a --delete "${ROOT}/localdata/models/rtmpose_onnx/" "${WEIGHTS}/rtmpose_onnx/"
rsync -a --delete "${ROOT}/localdata/models/yolo_pose/" "${WEIGHTS}/yolo_pose/"
find "${WEIGHTS}" \( -name '_source.zip' -o -name '*.part' \) -delete 2>/dev/null || true
chmod +x "${ROOT}/deploy/generate-weights-manifest.sh"
"${ROOT}/deploy/generate-weights-manifest.sh" "${WEIGHTS}"

# 部署脚本
mkdir -p "${PKG}/scripts/lib"
cp "${ROOT}/scripts/deploy-only/install.sh" "${PKG}/install.sh"
cp "${ROOT}/scripts/deploy-only/verify-package.sh" "${PKG}/verify-package.sh"
cp "${ROOT}/scripts/deploy-only/verify-images.sh" "${PKG}/verify-images.sh"
cp "${ROOT}/scripts/deploy-only/infer-bind-mounts.sh" "${PKG}/scripts/infer-bind-mounts.sh"
cp "${ROOT}/scripts/deploy-only/retag-infer-images.sh" "${PKG}/scripts/retag-infer-images.sh"
cp "${ROOT}/scripts/lib/docker-cmd.sh" "${PKG}/scripts/lib/docker-cmd.sh"
cp "${ROOT}/scripts/lib/install-deploy-stack.sh" "${PKG}/scripts/lib/install-deploy-stack.sh"
cp "${ROOT}/scripts/lib/verify-deploy-images.sh" "${PKG}/scripts/lib/verify-deploy-images.sh"
cp "${ROOT}/scripts/deploy-only/pack-deploy.sh" "${PKG}/pack-deploy.sh"
cp "${ROOT}/scripts/deploy-only/DEPLOY-0629.md" "${PKG}/DEPLOY-0629.md"
chmod +x "${PKG}/install.sh" "${PKG}/verify-package.sh" "${PKG}/verify-images.sh" "${PKG}/pack-deploy.sh" "${PKG}/scripts/retag-infer-images.sh"

GIT_HEAD="nogit"
git -C "${ROOT}" rev-parse HEAD >/dev/null 2>&1 && GIT_HEAD="$(git -C "${ROOT}" rev-parse HEAD)"

{
  echo "visual-dps deploy-only package"
  echo "package_layout: deploy-v1"
  echo "created: $(date -Iseconds)"
  echo "git: ${GIT_HEAD}"
  echo "image_tag: ${TAG}"
  echo "host_project_root: ${HOST_ROOT}"
  echo "weights: ok ($(du -sh "${WEIGHTS}" | awk '{print $1}'))"
  echo ""
  echo "install:"
  echo "  ./verify-images.sh [--skip-lite-cpu] [--skip-worker-2]"
  echo "  ./verify-package.sh"
  echo "  ./install.sh --host <IP> --stop-infer"
} > "${PKG}/PACKAGE_INFO.txt"

echo "==> 包内校验..."
"${PKG}/verify-package.sh" "${PKG}"

echo ""
echo "完成: ${PKG}"
du -sh "${PKG}" "${WEIGHTS}" "${APP}"

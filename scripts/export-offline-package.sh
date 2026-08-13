#!/usr/bin/env bash
# 打离线部署包：docker save + app 配置 + 独立 weights/（默认目录，不 gzip 整包）
#
# 用法见 .cursor/skills/visual-dps-offline-package/SKILL.md
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

INFERENCE_MODE="lite"
OUTPUT=""
ARCHIVE="none"       # none | tar | tar.gz
COMPRESS="pigz"      # gzip | pigz | zstd（仅 tar.gz）
SPLIT_SIZE=""
INCLUDE_MODELS=1
ENV_FILE=""
REBUILD_UI=0
ALLOW_DOWNLOAD_WEIGHTS=0
SKIP_WORKER2=0

usage() {
  cat <<'EOF'
打 Visual-DPS 离线包（默认产出目录，权重在 weights/，不进 gzip）

常用:
  ./scripts/download-model-weights.sh
  ./scripts/export-offline-complete.sh
  ./scripts/export-offline-package.sh --inference lite -o dist/my-pkg

选项:
  -o, --output DIR          输出目录（默认 dist/visual-dps-offline[-complete]-时间戳）
  --inference MODE          base | lite | gpu | gpu-onnx | all
  --rebuild-ui              打包前构建 UI + event-worker 镜像
  --skip-worker-2           离线包不含 event-worker-2 镜像
  --no-models               不打包 weights/
  --allow-download-weights  源机缺权重时联网补全（默认直接失败）
  --archive FORMAT          none（默认）| tar | tar.gz
  --compress TOOL           gzip | pigz | zstd（仅 --archive tar.gz，默认 pigz）
  --split SIZE              对外层归档分卷（如 2G）
  --env-file PATH           打入 app/.env 的模板
  SKIP_PREFLIGHT=1          跳过 preflight-offline-export.sh
  -h, --help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) OUTPUT="$2"; shift 2 ;;
    --inference) INFERENCE_MODE="$2"; shift 2 ;;
    --rebuild-ui) REBUILD_UI=1; shift ;;
    --skip-worker-2) SKIP_WORKER2=1; shift ;;
    --no-models) INCLUDE_MODELS=0; shift ;;
    --allow-download-weights) ALLOW_DOWNLOAD_WEIGHTS=1; shift ;;
    --archive) ARCHIVE="$2"; shift 2 ;;
    --compress) COMPRESS="$2"; shift 2 ;;
    --split) SPLIT_SIZE="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    # 兼容旧参数
    --no-tar) ARCHIVE="none"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 1 ;;
  esac
done

case "${ARCHIVE}" in
  none|tar|tar.gz) ;;
  *)
    echo "错误: --archive 须为 none | tar | tar.gz" >&2
    exit 1
    ;;
esac

# shellcheck disable=SC1091
source "${ROOT}/scripts/lib/load-build-env.sh"
load_build_env "${ROOT}"

if [[ "${REBUILD_UI}" -eq 1 ]]; then
  echo "==> 构建 UI + Event 镜像..."
  "${ROOT}/scripts/build-ui-image.sh"
fi

if [[ -z "${ENV_FILE}" ]]; then
  if [[ -f "${ROOT}/.env" ]]; then
    ENV_FILE="${ROOT}/.env"
  else
    ENV_FILE="${ROOT}/deploy/offline.env.example"
  fi
fi
[[ -f "${ENV_FILE}" ]] || { echo "错误: 找不到 env: ${ENV_FILE}" >&2; exit 1; }

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

TS="$(date +%Y%m%d-%H%M%S)"
if [[ "${INFERENCE_MODE}" == "all" ]]; then
  PKG_NAME="visual-dps-offline-complete-${TS}"
else
  PKG_NAME="visual-dps-offline-${TS}"
fi
PKG_DIR="${OUTPUT:-${ROOT}/dist/${PKG_NAME}}"
mkdir -p "${PKG_DIR}/docker-images" "${PKG_DIR}/app"

require_image() {
  local img="$1"
  docker image inspect "${img}" >/dev/null 2>&1 || {
    echo "错误: 本地缺少镜像 ${img}" >&2
    exit 1
  }
}

resolve_repo_tag() {
  local repo="$1"
  local preferred="${2:-}"
  if [[ -n "${preferred}" ]] && docker image inspect "${preferred}" >/dev/null 2>&1; then
    echo "${preferred}"
    return
  fi
  if docker image inspect "${repo}:latest" >/dev/null 2>&1; then
    echo "${repo}:latest"
    return
  fi
  docker images --format '{{.Repository}}:{{.Tag}}' "${repo}" 2>/dev/null | grep -v ':<none>' | grep -v ':latest' | sort | tail -n 1 || true
}

# 推理镜像与 UI 同 VISUAL_DPS_IMAGE_TAG；勿在 all 模式误用陈旧的 :latest
resolve_inference_repo_tag() {
  local repo="$1"
  local preferred="${2:-}"
  if [[ -n "${preferred}" ]] && docker image inspect "${preferred}" >/dev/null 2>&1; then
    echo "${preferred}"
    return
  fi
  local tag="${VISUAL_DPS_IMAGE_TAG:-}"
  if [[ -n "${tag}" ]] && docker image inspect "${repo}:${tag}" >/dev/null 2>&1; then
    echo "${repo}:${tag}"
    return
  fi
  resolve_repo_tag "${repo}" ""
}

IMAGES=()
if [[ "${SKIP_PREFLIGHT:-0}" != "1" ]]; then
  echo "==> 打包预检..."
  PREFLIGHT_ARGS=(--inference "${INFERENCE_MODE}")
  [[ "${INCLUDE_MODELS}" -eq 0 ]] && PREFLIGHT_ARGS+=(--no-models)
  [[ "${SKIP_WORKER2}" -eq 1 ]] && PREFLIGHT_ARGS+=(--skip-worker-2)
  "${ROOT}/scripts/preflight-offline-export.sh" "${PREFLIGHT_ARGS[@]}"
fi

echo "==> 收集镜像 (inference=${INFERENCE_MODE})..."
EXPORTED_UI_IMAGE="$(resolve_repo_tag "visual-dps-visual-dps-ui" "visual-dps-visual-dps-ui:${VISUAL_DPS_IMAGE_TAG:-}")"
EXPORTED_EVENT_IMAGE="$(resolve_repo_tag "visual-dps-event-worker" "visual-dps-event-worker:${VISUAL_DPS_IMAGE_TAG:-}")"
[[ -n "${EXPORTED_UI_IMAGE}" ]] || { echo "错误: 缺少 visual-dps-visual-dps-ui 镜像" >&2; exit 1; }
[[ -n "${EXPORTED_EVENT_IMAGE}" ]] || { echo "错误: 缺少 visual-dps-event-worker 镜像" >&2; exit 1; }
BASE_IMAGES=(
  "redis:7"
  "bluenviron/mediamtx:1.11.3"
  "${EXPORTED_UI_IMAGE}"
  "${EXPORTED_EVENT_IMAGE}"
)
for img in "${BASE_IMAGES[@]}"; do
  require_image "${img}"
  IMAGES+=("${img}")
  echo "  + ${img}"
done

if [[ "${SKIP_WORKER2}" -eq 0 ]]; then
  EXPORTED_EVENT2_IMAGE="$(resolve_repo_tag "visual-dps-event-worker-2" "visual-dps-event-worker-2:${VISUAL_DPS_IMAGE_TAG:-}")"
  [[ -n "${EXPORTED_EVENT2_IMAGE}" ]] || { echo "错误: 缺少 visual-dps-event-worker-2 镜像（可先 ./scripts/build-event-worker-2-image.sh 或 --skip-worker-2）" >&2; exit 1; }
  require_image "${EXPORTED_EVENT2_IMAGE}"
  IMAGES+=("${EXPORTED_EVENT2_IMAGE}")
  echo "  + ${EXPORTED_EVENT2_IMAGE}"
fi

case "${INFERENCE_MODE}" in
  base) ;;
  lite)
    lite="$(resolve_inference_repo_tag "visual-dps-inference-lite" "${INFERENCE_LITE_IMAGE:-}")"
    [[ -n "${lite}" ]] || { echo "错误: 缺少 visual-dps-inference-lite" >&2; exit 1; }
    require_image "${lite}"
    IMAGES+=("${lite}")
    EXPORTED_LITE_IMAGE="${lite}"
    echo "  + ${lite}"
    ;;
  gpu)
    gpu="$(resolve_inference_repo_tag "visual-dps-inference-lite-gpu" "${INFERENCE_LITE_GPU_IMAGE:-}")"
    [[ -n "${gpu}" ]] || { echo "错误: 缺少 visual-dps-inference-lite-gpu" >&2; exit 1; }
    require_image "${gpu}"
    IMAGES+=("${gpu}")
    EXPORTED_GPU_IMAGE="${gpu}"
    echo "  + ${gpu}"
    ;;
  gpu-onnx)
    onnx="$(resolve_inference_repo_tag "visual-dps-inference-lite-gpu-onnx" "${INFERENCE_LITE_GPU_ONNX_IMAGE:-}")"
    [[ -n "${onnx}" ]] || { echo "错误: 缺少 visual-dps-inference-lite-gpu-onnx" >&2; exit 1; }
    require_image "${onnx}"
    IMAGES+=("${onnx}")
    EXPORTED_ONNX_IMAGE="${onnx}"
    echo "  + ${onnx}"
    ;;
  all)
    lite="$(resolve_inference_repo_tag "visual-dps-inference-lite" "")"
    gpu="$(resolve_inference_repo_tag "visual-dps-inference-lite-gpu" "")"
    onnx="$(resolve_inference_repo_tag "visual-dps-inference-lite-gpu-onnx" "")"
    [[ -n "${lite}" && -n "${gpu}" && -n "${onnx}" ]] || {
      echo "错误: 完整包需要 lite、lite-gpu、lite-gpu-onnx 镜像" >&2
      exit 1
    }
    require_image "${lite}"
    require_image "${gpu}"
    require_image "${onnx}"
    IMAGES+=("${lite}" "${gpu}" "${onnx}")
    EXPORTED_LITE_IMAGE="${lite}"
    EXPORTED_GPU_IMAGE="${gpu}"
    EXPORTED_ONNX_IMAGE="${onnx}"
    echo "  + ${lite}"
    echo "  + ${gpu}"
    echo "  + ${onnx}"
    ;;
  *)
    echo "错误: 未知 --inference ${INFERENCE_MODE}" >&2
    exit 1
    ;;
esac

BUNDLE="${PKG_DIR}/docker-images/bundle.tar"
if [[ -f "${BUNDLE}" && "${FORCE_SAVE:-0}" != "1" ]]; then
  echo "==> 已有 bundle.tar，跳过 docker save（FORCE_SAVE=1 可强制重做）"
  echo "    $(du -h "${BUNDLE}" | awk '{print $1}')"
else
  echo "==> docker save -> ${BUNDLE}"
  docker save -o "${BUNDLE}" "${IMAGES[@]}"
  echo "    $(du -h "${BUNDLE}" | awk '{print $1}')"
fi

APP="${PKG_DIR}/app"
echo "==> 拷贝应用 -> ${APP}（不含 models）"
cp "${ROOT}/docker-compose.yml" "${APP}/"
cp "${ROOT}/docker-compose.deploy.yml" "${APP}/"
cp "${ROOT}/app_config.json" "${APP}/"
cp "${ROOT}/version.json" "${APP}/"
cp -a "${ROOT}/deploy" "${APP}/"
[[ -f "${ROOT}/docker-compose.override.yml" ]] && cp "${ROOT}/docker-compose.override.yml" "${APP}/"
cp "${ENV_FILE}" "${APP}/.env"
cp "${ROOT}/deploy/offline.env.example" "${APP}/deploy/" 2>/dev/null || true

patch_env() {
  local key="$1" val="$2"
  [[ -n "${val}" ]] || return 0
  if grep -q "^${key}=" "${APP}/.env" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${APP}/.env"
  else
    printf '\n%s=%s\n' "${key}" "${val}" >> "${APP}/.env"
  fi
}
patch_env "INFERENCE_LITE_IMAGE" "${EXPORTED_LITE_IMAGE:-}"
patch_env "INFERENCE_LITE_GPU_IMAGE" "${EXPORTED_GPU_IMAGE:-}"
patch_env "INFERENCE_LITE_GPU_ONNX_IMAGE" "${EXPORTED_ONNX_IMAGE:-}"
if [[ "${EXPORTED_UI_IMAGE}" == *:* ]]; then
  patch_env "VISUAL_DPS_IMAGE_TAG" "${EXPORTED_UI_IMAGE#*:}"
else
  grep -q '^VISUAL_DPS_IMAGE_TAG=' "${APP}/.env" 2>/dev/null || echo 'VISUAL_DPS_IMAGE_TAG=latest' >> "${APP}/.env"
fi

mkdir -p "${APP}/localdata/json/cameras" "${APP}/localdata/logs" "${APP}/localdata/inference" "${APP}/localdata/frames"
RSYNC_EXCLUDES=(--exclude 'models/**' --exclude 'logs/**' --exclude 'frames/**' --exclude 'inference/**' --exclude 'upload/**' --exclude 'last_frame.jpg' --exclude 'json/annotation_*.json' --exclude 'mediamtx.yml')
if [[ -d "${ROOT}/localdata" ]] && command -v rsync >/dev/null 2>&1; then
  rsync -a "${RSYNC_EXCLUDES[@]}" "${ROOT}/localdata/" "${APP}/localdata/"
fi
[[ -f "${APP}/localdata/camera_ips.json" ]] || cp "${ROOT}/deploy/camera_ips.example.json" "${APP}/localdata/camera_ips.json"
# 不打包源机 mediamtx.yml；目标机 install.sh 会按 .env 重新生成
rm -f "${APP}/localdata/mediamtx.yml"
[[ -f "${APP}/localdata/json/precise_boxes_new.json" ]] || echo '{}' > "${APP}/localdata/json/precise_boxes_new.json"

MODEL_CHECK_NOTE="models: excluded"
if [[ "${INCLUDE_MODELS}" -eq 1 ]]; then
  WEIGHTS="${PKG_DIR}/weights"
  mkdir -p "${WEIGHTS}"
  # shellcheck disable=SC1091
  source "${ROOT}/deploy/check-model-weights.sh"
  echo "==> 检查源机权重 localdata/models ..."
  if ! visual_dps_check_model_weights "${ROOT}/localdata"; then
    if [[ "${ALLOW_DOWNLOAD_WEIGHTS}" -eq 1 ]]; then
      echo "    尝试从 dist/ 已有离线包恢复（不联网）..."
      if chmod +x "${ROOT}/deploy/recover-model-weights.sh" 2>/dev/null; then
        "${ROOT}/deploy/recover-model-weights.sh" "${ROOT}" "${ROOT}/localdata" 2>/dev/null || true
      fi
    fi
    if ! visual_dps_check_model_weights "${ROOT}/localdata" 2>/dev/null; then
      if [[ "${ALLOW_DOWNLOAD_WEIGHTS}" -eq 1 ]]; then
        echo "    本地无法恢复，联网仅补缺失项..."
        "${ROOT}/scripts/download-model-weights.sh"
        visual_dps_check_model_weights "${ROOT}/localdata" || exit 1
      else
        echo "错误: 源机权重不齐。请先 ./scripts/download-model-weights.sh" >&2
        echo "      或 --allow-download-weights（会先搜 dist/ 再联网）" >&2
        exit 1
      fi
    fi
  fi
  echo "==> 复制权重 -> ${WEIGHTS}/（裸文件，不进 gzip）"
  rsync -a --delete \
    "${ROOT}/localdata/models/rtmpose_onnx/" "${WEIGHTS}/rtmpose_onnx/"
  rsync -a --delete \
    "${ROOT}/localdata/models/yolo_pose/" "${WEIGHTS}/yolo_pose/"
  find "${WEIGHTS}" \( -name '_source.zip' -o -name '*.part' \) -delete 2>/dev/null || true
  visual_dps_check_models_dir "${WEIGHTS}"
  chmod +x "${ROOT}/deploy/generate-weights-manifest.sh"
  "${ROOT}/deploy/generate-weights-manifest.sh" "${WEIGHTS}"
  _wsize="$(du -sh "${WEIGHTS}" | awk '{print $1}')"
  MODEL_CHECK_NOTE="weights: ok (${_wsize})"
fi

cp "${ROOT}/deploy/install.sh" "${PKG_DIR}/install.sh"
cp "${ROOT}/deploy/OFFLINE-QUICKSTART.md" "${PKG_DIR}/OFFLINE-QUICKSTART.md"
cp "${ROOT}/deploy/verify-package.sh" "${PKG_DIR}/verify-package.sh"
mkdir -p "${PKG_DIR}/scripts/lib"
cp "${ROOT}/scripts/lib/install-deploy-stack.sh" "${PKG_DIR}/scripts/lib/install-deploy-stack.sh"
chmod +x "${PKG_DIR}/install.sh" "${PKG_DIR}/verify-package.sh" "${APP}/deploy/"*.sh 2>/dev/null || true

GIT_HEAD="nogit"
git -C "${ROOT}" rev-parse HEAD >/dev/null 2>&1 && GIT_HEAD="$(git -C "${ROOT}" rev-parse HEAD)"
PKG_BASENAME="$(basename "${PKG_DIR}")"

{
  echo "visual-dps offline package"
  echo "package_layout: v2"
  echo "created: $(date -Iseconds)"
  echo "git: ${GIT_HEAD}"
  echo "inference_mode: ${INFERENCE_MODE}"
  echo "include_models: ${INCLUDE_MODELS}"
  echo "archive: ${ARCHIVE}"
  echo "${MODEL_CHECK_NOTE}"
  echo "env_source: ${ENV_FILE}"
  echo "images:"
  printf '  %s\n' "${IMAGES[@]}"
  echo ""
  echo "layout:"
  echo "  docker-images/bundle.tar   # docker save，勿 gzip"
  echo "  app/                       # compose + 配置（无 models）"
  echo "  weights/                   # 8 个权重 + SHA256SUMS"
  echo ""
  echo "install:"
  echo "  cd ${PKG_BASENAME} && ./verify-package.sh && ./install.sh --host <IP>"
} > "${PKG_DIR}/PACKAGE_INFO.txt"

echo "==> 包内校验..."
"${PKG_DIR}/verify-package.sh" "${PKG_DIR}"

ARCHIVE_PATH=""
if [[ "${ARCHIVE}" != "none" ]]; then
  case "${ARCHIVE}" in
    tar)
      ARCHIVE_PATH="${PKG_DIR}.tar"
      echo "==> 归档（未压缩） ${ARCHIVE_PATH}"
      tar -cf "${ARCHIVE_PATH}" -C "$(dirname "${PKG_DIR}")" "${PKG_BASENAME}"
      ;;
    tar.gz)
      ARCHIVE_PATH="${PKG_DIR}.tar.gz"
      echo "==> 归档（${COMPRESS}） ${ARCHIVE_PATH}"
      case "${COMPRESS}" in
        gzip) tar -czf "${ARCHIVE_PATH}" -C "$(dirname "${PKG_DIR}")" "${PKG_BASENAME}" ;;
        pigz)
          if command -v pigz >/dev/null 2>&1; then
            tar -I "pigz -1" -cf "${ARCHIVE_PATH}" -C "$(dirname "${PKG_DIR}")" "${PKG_BASENAME}"
          else
            echo "警告: 无 pigz，回退 gzip" >&2
            tar -czf "${ARCHIVE_PATH}" -C "$(dirname "${PKG_DIR}")" "${PKG_BASENAME}"
          fi
          ;;
        zstd)
          if command -v zstd >/dev/null 2>&1; then
            tar -I "zstd -1" -cf "${ARCHIVE_PATH}" -C "$(dirname "${PKG_DIR}")" "${PKG_BASENAME}"
          else
            echo "错误: 无 zstd" >&2
            exit 1
          fi
          ;;
        *)
          echo "错误: 未知 --compress ${COMPRESS}" >&2
          exit 1
          ;;
      esac
      ;;
  esac
  if [[ -n "${SPLIT_SIZE}" && -n "${ARCHIVE_PATH}" ]]; then
    echo "==> 分卷 ${SPLIT_SIZE}"
    split -b "${SPLIT_SIZE}" -d -a 3 "${ARCHIVE_PATH}" "${ARCHIVE_PATH}.part-"
  fi
fi

echo ""
echo "完成: ${PKG_DIR}"
[[ -n "${ARCHIVE_PATH}" ]] && echo "  归档: ${ARCHIVE_PATH} ($(du -h "${ARCHIVE_PATH}" | awk '{print $1}'))"
echo "  说明: 默认目录分发；bundle.tar 勿再 gzip。外传可加 --archive tar.gz --compress pigz"

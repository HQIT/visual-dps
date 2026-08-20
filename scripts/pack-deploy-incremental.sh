#!/usr/bin/env bash
# 增量离线包：app + weights + 指定业务镜像 tar（默认 UI + worker + worker-2，不含 infer/redis/mediamtx）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

TAG="${VISUAL_DPS_IMAGE_TAG:-20260817-feature-eventworker2-07082d2}"
OUT="${HOME}/workspace/visual-dps-0820-deploy"
HOST_ROOT="${OUT}/app"
PACKAGE_NAME="visual-dps-0820-deploy"
SKIP_SAVE=0
IMAGES=()

usage() {
  cat <<'EOF'
用法: ./scripts/pack-deploy-incremental.sh [-o DIR] [--tag TAG] [--host-root PATH] [--skip-save]

  输出 layout 同 visual-dps-0817-deploy（分拆 tar + app + weights），默认：
    - visual-dps-visual-dps-ui:<TAG>
    - visual-dps-event-worker:<TAG>
    - visual-dps-event-worker-2:<TAG>

  不含 DEPLOY-0629.md；含 OFFLINE-QUICKSTART.md、DEPLOY-EVENT-WORKER-2.md。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) OUT="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --host-root) HOST_ROOT="$2"; shift 2 ;;
    --skip-save) SKIP_SAVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

# 默认镜像列表（UI + worker-1 + worker-2）
DEFAULT_IMAGES=(
  "visual-dps-visual-dps-ui:${TAG}"
  "visual-dps-event-worker:${TAG}"
  "visual-dps-event-worker-2:${TAG}"
)
if [[ ${#IMAGES[@]} -eq 0 ]]; then
  IMAGES=("${DEFAULT_IMAGES[@]}")
fi

tar_name_for() {
  local img="$1"
  local safe="${img//\//__}"
  safe="${safe//:/--}"
  printf '%s.tar' "${safe}"
}

require_image() {
  local img="$1"
  docker image inspect "${img}" >/dev/null 2>&1 || {
    echo "错误: 本地缺少镜像 ${img}" >&2
    exit 1
  }
}

echo "==> 1/4 打 app + weights（pack-deploy-only，无 DEPLOY-0629）"
"${ROOT}/scripts/pack-deploy-only.sh" -o "${OUT}" --tag "${TAG}" --host-root "${HOST_ROOT}" --keep-existing
rm -f "${OUT}/DEPLOY-0629.md"

echo "==> 2/4 docker save 分拆 tar"
IMG_DIR="${OUT}/docker-images"
mkdir -p "${IMG_DIR}"
if [[ "${SKIP_SAVE}" -eq 0 ]]; then
  rm -f "${IMG_DIR}"/*.tar
fi

MANIFEST="${IMG_DIR}/images.manifest"
{
  echo "visual_dps_image_tag=${TAG}"
  echo "layout=split-tar-incremental-v1"
  echo "saved_at=$(date -Iseconds)"
  echo ""
} > "${MANIFEST}"

if [[ "${SKIP_SAVE}" -eq 0 ]]; then
  for img in "${IMAGES[@]}"; do
    require_image "${img}"
    tar_file="$(tar_name_for "${img}")"
    out_path="${IMG_DIR}/${tar_file}"
    echo "  save ${img} -> ${tar_file}"
    docker save -o "${out_path}" "${img}"
    du -h "${out_path}" | awk '{print "    " $1}'
    {
      echo "tar=${tar_file}"
      echo "image=${img}"
      echo ""
    } >> "${MANIFEST}"
  done
else
  echo "  跳过 docker save (--skip-save)"
fi

echo "==> 3/4 安装脚本与文档"
mkdir -p "${OUT}/scripts/lib"
cp "${ROOT}/scripts/deploy-only/load-split-images.sh" "${OUT}/scripts/load-split-images.sh"
cp "${ROOT}/scripts/deploy-only/retag-infer-images.sh" "${OUT}/scripts/retag-infer-images.sh"
cp "${ROOT}/scripts/deploy-only/infer-bind-mounts.sh" "${OUT}/scripts/infer-bind-mounts.sh"
cp "${ROOT}/scripts/lib/docker-cmd.sh" "${OUT}/scripts/lib/docker-cmd.sh"
cp "${ROOT}/scripts/lib/install-deploy-stack.sh" "${OUT}/scripts/lib/install-deploy-stack.sh"
cp "${ROOT}/scripts/lib/verify-deploy-images.sh" "${OUT}/scripts/lib/verify-deploy-images.sh"
cp "${ROOT}/scripts/deploy-only/verify-images.sh" "${OUT}/verify-images.sh"
cp "${ROOT}/scripts/deploy-only/install-with-images.sh" "${OUT}/install.sh"
cp "${ROOT}/scripts/deploy-only/verify-package-split.sh" "${OUT}/verify-package.sh"
cp "${ROOT}/docs/DEPLOY-EVENT-WORKER-2.md" "${OUT}/DEPLOY-EVENT-WORKER-2.md"
chmod +x "${OUT}/install.sh" "${OUT}/verify-package.sh" \
  "${OUT}/scripts/load-split-images.sh" "${OUT}/scripts/retag-infer-images.sh"

GIT_HEAD="nogit"
GIT_BRANCH=""
git -C "${ROOT}" rev-parse HEAD >/dev/null 2>&1 && GIT_HEAD="$(git -C "${ROOT}" rev-parse --short HEAD)"
git -C "${ROOT}" rev-parse --abbrev-ref HEAD >/dev/null 2>&1 && GIT_BRANCH="$(git -C "${ROOT}" rev-parse --abbrev-ref HEAD)"

{
  echo "VISUAL_DPS_IMAGE_TAG=${TAG}"
  echo "GIT_BRANCH=${GIT_BRANCH}"
  echo "GIT_COMMIT=${GIT_HEAD}"
  echo "BUILD_TIME=$(date -Iseconds)"
  echo "PACKAGE_KIND=incremental-ui-worker-worker2"
  echo "INFER_RETAG_OLD=20260817-feature-eventworker2-0b26d8a"
  echo "INFER_RETAG_OLD_ALT=20260813-feature-eventworker2-5e4f4fe"
  echo "INFER_RETAG_OLD_ALT2=20260727-test-from-4841de6a-85288b7"
} > "${OUT}/BUILD_TAG.txt"

QUICKSTART_TMPL="${ROOT}/scripts/deploy-only/OFFLINE-QUICKSTART-incremental-ui-worker2.md"
if [[ ! -f "${QUICKSTART_TMPL}" ]]; then
  echo "缺少 ${QUICKSTART_TMPL}" >&2
  exit 1
fi
sed \
  -e "s#__TAG__#${TAG}#g" \
  -e "s#__PACKAGE_NAME__#${PACKAGE_NAME}#g" \
  -e "s#__GIT_COMMIT__#${GIT_HEAD}#g" \
  -e "s#__GIT_BRANCH__#${GIT_BRANCH}#g" \
  -e "s#__BUILD_DATE__#$(date +%Y-%m-%d)#g" \
  "${QUICKSTART_TMPL}" > "${OUT}/OFFLINE-QUICKSTART.md"

{
  echo "visual-dps offline package (incremental split tar)"
  echo "package_layout: split-tar-incremental-v1"
  echo "created: $(date -Iseconds)"
  echo "git: ${GIT_HEAD} (${GIT_BRANCH})"
  echo "image_tag: ${TAG}"
  echo "host_project_root: ${HOST_ROOT}"
  echo ""
  echo "images (this package):"
  for img in "${IMAGES[@]}"; do
    echo "  ${img}"
  done
  echo ""
  echo "not included: redis, mediamtx, inference tars"
  echo "install:"
  echo "  ./verify-package.sh && ./scripts/load-split-images.sh"
  echo "  ./scripts/retag-infer-images.sh --skip-lite-cpu   # 自动 0817/0813/0727"
  echo "  # 或: ./scripts/retag-infer-images.sh --skip-lite-cpu <旧infer-tag>"
  echo "  ./install.sh --host <IP> --worker-2 --stop-infer"
} > "${OUT}/PACKAGE_INFO.txt"

rm -f "${OUT}/pack-deploy.sh"

echo "==> 4/4 包内校验"
"${OUT}/verify-package.sh" "${OUT}"

echo ""
echo "完成: ${OUT}"
du -sh "${OUT}" "${IMG_DIR}" "${OUT}/weights" "${OUT}/app" 2>/dev/null || true

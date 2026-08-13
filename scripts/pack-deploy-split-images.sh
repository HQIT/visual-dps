#!/usr/bin/env bash
# 打离线部署包：app + weights + 分拆 docker save（每镜像单独 tar，便于对比）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

TAG="${VISUAL_DPS_IMAGE_TAG:-20260720-test-from-4841de6a-234a98e}"
OUT="${HOME}/workspace/visual-dps-0720-deploy"
HOST_ROOT="${OUT}/app"
SKIP_SAVE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) OUT="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --host-root) HOST_ROOT="$2"; shift 2 ;;
    --skip-save) SKIP_SAVE=1; shift ;;
    -h|--help)
      cat <<'EOF'
用法: ./scripts/pack-deploy-split-images.sh [-o DIR] [--tag TAG] [--host-root PATH] [--skip-save]

  基于本地已有镜像，输出 layout 类似 visual-dps-0529，但 docker-images/ 为分拆 tar。
  默认 tag: 20260720-test-from-4841de6a-234a98e
EOF
      exit 0
      ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

require_image() {
  local img="$1"
  docker image inspect "${img}" >/dev/null 2>&1 || {
    echo "错误: 本地缺少镜像 ${img}" >&2
    exit 1
  }
}

IMAGES=(
  "redis:7|bluenviron/mediamtx:1.11.3"
  "visual-dps-visual-dps-ui:${TAG}"
  "visual-dps-event-worker:${TAG}"
  "visual-dps-event-worker-2:${TAG}"
  "visual-dps-inference-lite-gpu:${TAG}"
  "visual-dps-inference-lite-gpu-onnx:${TAG}"
)

tar_name_for() {
  local img="$1"
  local safe="${img//\//__}"
  safe="${safe//:/--}"
  safe="${safe//|/--}"
  printf '%s.tar' "${safe}"
}

echo "==> 1/3 打 app + weights（不含镜像 tar）"
"${ROOT}/scripts/pack-deploy-only.sh" -o "${OUT}" --tag "${TAG}" --host-root "${HOST_ROOT}"

IMG_DIR="${OUT}/docker-images"
mkdir -p "${IMG_DIR}"
rm -f "${IMG_DIR}"/*.tar

MANIFEST="${IMG_DIR}/images.manifest"
{
  echo "visual_dps_image_tag=${TAG}"
  echo "layout=split-tar"
  echo "saved_at=$(date -Iseconds)"
  echo ""
} > "${MANIFEST}"

if [[ "${SKIP_SAVE}" -eq 0 ]]; then
  echo "==> 2/3 docker save 分拆 tar -> ${IMG_DIR}/"
  for spec in "${IMAGES[@]}"; do
    tar_file="$(tar_name_for "${spec}")"
    out_path="${IMG_DIR}/${tar_file}"
    IFS='|' read -ra imgs <<< "${spec}"
    for img in "${imgs[@]}"; do
      require_image "${img}"
    done
    if [[ -f "${out_path}" ]]; then
      echo "  跳过已存在: ${tar_file}"
    else
      echo "  save ${spec//|/, } -> ${tar_file}"
      docker save -o "${out_path}" "${imgs[@]}"
      du -h "${out_path}" | awk '{print "    " $1}'
    fi
    for img in "${imgs[@]}"; do
      {
        echo "tar=${tar_file}"
        echo "image=${img}"
        echo ""
      } >> "${MANIFEST}"
    done
  done
else
  echo "==> 2/3 跳过 docker save (--skip-save)"
fi

echo "==> 3/3 安装分拆包脚本"
mkdir -p "${OUT}/scripts/lib"
cp "${ROOT}/scripts/deploy-only/load-split-images.sh" "${OUT}/scripts/load-split-images.sh"
cp "${ROOT}/scripts/deploy-only/retag-infer-images.sh" "${OUT}/scripts/retag-infer-images.sh"
cp "${ROOT}/scripts/deploy-only/infer-bind-mounts.sh" "${OUT}/scripts/infer-bind-mounts.sh"
cp "${ROOT}/scripts/lib/install-deploy-stack.sh" "${OUT}/scripts/lib/install-deploy-stack.sh"
cp "${ROOT}/scripts/lib/verify-deploy-images.sh" "${OUT}/scripts/lib/verify-deploy-images.sh"
cp "${ROOT}/scripts/deploy-only/verify-images.sh" "${OUT}/verify-images.sh"
cp "${ROOT}/scripts/deploy-only/install-with-images.sh" "${OUT}/install.sh"
cp "${ROOT}/scripts/deploy-only/verify-package-split.sh" "${OUT}/verify-package.sh"
chmod +x "${OUT}/install.sh" "${OUT}/verify-package.sh" "${OUT}/scripts/load-split-images.sh" "${OUT}/scripts/retag-infer-images.sh"

GIT_HEAD="nogit"
GIT_BRANCH=""
git -C "${ROOT}" rev-parse HEAD >/dev/null 2>&1 && GIT_HEAD="$(git -C "${ROOT}" rev-parse --short HEAD)"
git -C "${ROOT}" rev-parse --abbrev-ref HEAD >/dev/null 2>&1 && GIT_BRANCH="$(git -C "${ROOT}" rev-parse --abbrev-ref HEAD)"

{
  echo "VISUAL_DPS_IMAGE_TAG=${TAG}"
  echo "GIT_BRANCH=${GIT_BRANCH}"
  echo "GIT_COMMIT=${GIT_HEAD}"
  echo "BUILD_TIME=$(date -Iseconds)"
} > "${OUT}/BUILD_TAG.txt"

cat > "${OUT}/OFFLINE-QUICKSTART.md" <<EOF
# Visual-DPS 离线部署（0720 分拆镜像）

## 包内容

| 路径 | 说明 |
|------|------|
| \`docker-images/*.tar\` | 每镜像单独 \`docker save\`，见 \`images.manifest\` |
| \`app/\` | compose + 配置 + 推理 bind mount 源码 |
| \`weights/\` | 推理权重 |
| \`install.sh\` | load 分拆 tar + 权重 + compose up |

## 目标机（仅 Docker）

\`\`\`bash
cd visual-dps-0720-deploy
./verify-package.sh
# 修改 app/.env：REDIS_PASSWORD、MEDIAMTX_PUBLIC_HOST、HOST_PROJECT_ROOT=<本机 app 绝对路径>
./install.sh --host <局域网IP> --stop-infer
\`\`\`

镜像 tag: **${TAG}**

GPU 部署校验镜像：

\`\`\`bash
./verify-images.sh --skip-lite-cpu
\`\`\`
EOF

{
  echo "visual-dps offline package (split tar)"
  echo "package_layout: split-tar-v1"
  echo "created: $(date -Iseconds)"
  echo "git: ${GIT_HEAD} (${GIT_BRANCH})"
  echo "image_tag: ${TAG}"
  echo "host_project_root: ${HOST_ROOT}"
  echo ""
  echo "images (split tar, self-built + bases):"
  echo "  redis:7 + bluenviron/mediamtx:1.11.3 -> docker-images/bases-redis-mediamtx.tar"
  echo "  visual-dps-visual-dps-ui:${TAG}"
  echo "  visual-dps-event-worker:${TAG}"
  echo "  visual-dps-event-worker-2:${TAG}"
  echo "  visual-dps-inference-lite-gpu:${TAG}"
  echo "  visual-dps-inference-lite-gpu-onnx:${TAG}"
  echo ""
  echo "install:"
  echo "  ./verify-package.sh && ./install.sh --host <IP> --stop-infer"
} > "${OUT}/PACKAGE_INFO.txt"

echo "==> 包内校验..."
"${OUT}/verify-package.sh" "${OUT}"

echo ""
echo "完成: ${OUT}"
du -sh "${OUT}" "${IMG_DIR}" "${OUT}/weights" "${OUT}/app" 2>/dev/null || true

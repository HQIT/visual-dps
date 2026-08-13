#!/usr/bin/env bash
# 现场：推理镜像内容未变，仅将旧 tag retag 为新 tag，使 verify-images 通过
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PKG_ROOT}/app/.env"

OLD_TAG=""
NEW_TAG=""
SKIP_LITE_CPU=0

usage() {
  cat <<'EOF'
用法: ./scripts/retag-infer-images.sh [选项] [旧TAG] [新TAG]

  将目标机已有的推理镜像从旧 tag 复制为新 tag（docker tag，不重建镜像）。
  适用于增量包只升级 UI/Event，推理镜像本体未变、仅 .env 中 tag 变更的场景。

参数:
  旧TAG   源机/上一版 tag，默认 20260727-test-from-4841de6a-85288b7
  新TAG   本包 app/.env 中的 VISUAL_DPS_IMAGE_TAG；省略时自动从 app/.env 读取

选项:
  --skip-lite-cpu   跳过 visual-dps-inference-lite（CPU 推理镜像）
                    GPU 现场通常无此镜像，建议与 verify-images --skip-lite-cpu 一起使用
  -h, --help        显示本说明

处理的镜像（按顺序）:
  visual-dps-inference-lite              （可选；--skip-lite-cpu 时不处理）
  visual-dps-inference-lite-gpu          （GPU 现场必需）
  visual-dps-inference-lite-gpu-onnx     （GPU 现场必需）

行为:
  - 本地存在 旧TAG 镜像 → docker tag 为 新TAG，输出 OK
  - 本地已有 新TAG 镜像 → 输出 SKIP（已有）
  - 两者皆无 → lite 仅 WARN；gpu / gpu-onnx 记为 FAIL 并 exit 1

示例（0813 增量包 · GPU 现场）:
  cd visual-dps-0813-deploy
  ./scripts/retag-infer-images.sh --skip-lite-cpu 20260727-test-from-4841de6a-85288b7
  ./verify-images.sh --skip-lite-cpu

  # 显式指定新旧 tag
  ./scripts/retag-infer-images.sh --skip-lite-cpu \
    20260727-test-from-4841de6a-85288b7 20260813-feature-eventworker2-5e4f4fe
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-lite-cpu) SKIP_LITE_CPU=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "未知选项: $1（见 --help）" >&2; exit 1 ;;
    *)
      if [[ -z "${OLD_TAG}" ]]; then
        OLD_TAG="$1"
      elif [[ -z "${NEW_TAG}" ]]; then
        NEW_TAG="$1"
      else
        echo "参数过多: $1（见 --help）" >&2
        exit 1
      fi
      shift
      ;;
  esac
done

OLD_TAG="${OLD_TAG:-20260727-test-from-4841de6a-85288b7}"

if [[ -z "${NEW_TAG}" && -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  NEW_TAG="${VISUAL_DPS_IMAGE_TAG:-}"
fi

if [[ -z "${NEW_TAG}" ]]; then
  echo "错误: 未指定新 TAG，且 ${ENV_FILE} 中无 VISUAL_DPS_IMAGE_TAG" >&2
  echo "用法: $0 [--skip-lite-cpu] [旧TAG] [新TAG]  或见 --help" >&2
  exit 1
fi

if [[ "${OLD_TAG}" == "${NEW_TAG}" ]]; then
  echo "OLD 与 NEW 相同: ${NEW_TAG}，无需 retag"
  exit 0
fi

echo "==> 推理镜像 retag: ${OLD_TAG} -> ${NEW_TAG}"
[[ "${SKIP_LITE_CPU}" -eq 1 ]] && echo "    （跳过 CPU lite）"

repos=(
  visual-dps-inference-lite
  visual-dps-inference-lite-gpu
  visual-dps-inference-lite-gpu-onnx
)

fail=0
for repo in "${repos[@]}"; do
  if [[ "${repo}" == "visual-dps-inference-lite" && "${SKIP_LITE_CPU}" -eq 1 ]]; then
    echo "SKIP: ${repo} (--skip-lite-cpu)"
    continue
  fi

  old_ref="${repo}:${OLD_TAG}"
  new_ref="${repo}:${NEW_TAG}"

  if docker image inspect "${old_ref}" >/dev/null 2>&1; then
    docker tag "${old_ref}" "${new_ref}"
    echo "OK: ${old_ref} -> ${new_ref}"
  elif docker image inspect "${new_ref}" >/dev/null 2>&1; then
    echo "SKIP (已有): ${new_ref}"
  elif [[ "${repo}" == "visual-dps-inference-lite" ]]; then
    echo "WARN: 缺少 ${old_ref}（CPU lite 非 GPU 现场必需；verify 时用 --skip-lite-cpu）" >&2
  else
    echo "FAIL: 缺少 ${old_ref}，且不存在 ${new_ref}" >&2
    fail=1
  fi
done

if [[ "${fail}" -ne 0 ]]; then
  echo "" >&2
  echo "提示: 确认目标机曾部署含 gpu/gpu-onnx 的旧包，或 OLD_TAG 填写正确。" >&2
  echo "      GPU 现场可: $0 --skip-lite-cpu ${OLD_TAG}" >&2
  exit 1
fi

echo "==> retag 完成。请执行: ./verify-images.sh --skip-lite-cpu"

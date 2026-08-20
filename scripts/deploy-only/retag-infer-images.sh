#!/usr/bin/env bash
# 现场：推理镜像内容未变，仅将旧 tag retag 为新 tag，使 verify-images 通过
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PKG_ROOT}/app/.env"
BUILD_TAG_FILE="${PKG_ROOT}/BUILD_TAG.txt"

OLD_TAG=""
NEW_TAG=""
SKIP_LITE_CPU=0

usage() {
  cat <<'EOF'
用法: ./scripts/retag-infer-images.sh [选项] [旧TAG] [新TAG]

  将目标机已有的推理镜像从旧 tag 复制为新 tag（docker tag，不重建镜像）。
  适用于增量包只升级 UI/Event/worker-2，推理镜像本体未变、仅 .env 中 tag 变更的场景。

参数:
  旧TAG   可选；指定则只从该 tag retag
          省略则按 BUILD_TAG.txt + 内置列表依次尝试（0817 → 0813 → 0727）
  新TAG   本包 app/.env 中的 VISUAL_DPS_IMAGE_TAG；省略时自动从 app/.env 读取

选项:
  --skip-lite-cpu   跳过 visual-dps-inference-lite（CPU 推理镜像）
                    GPU 现场通常无此镜像，建议与 verify-images --skip-lite-cpu 一起使用
  -h, --help        显示本说明

处理的镜像:
  visual-dps-inference-lite              （可选；--skip-lite-cpu 时不处理）
  visual-dps-inference-lite-gpu          （GPU 现场必需）
  visual-dps-inference-lite-gpu-onnx     （GPU 现场必需）

行为:
  - 找到任一旧 TAG 镜像 → docker tag 为新 TAG，输出 OK
  - 新 TAG 已存在 → SKIP（已有）
  - gpu/gpu-onnx 皆无旧/新 → FAIL

示例（0820 增量 · 自 0817 升级，省略旧 TAG 自动探测）:
  cd visual-dps-0820-deploy
  ./scripts/retag-infer-images.sh --skip-lite-cpu
  ./verify-images.sh --skip-lite-cpu

示例（显式旧 TAG · 自 0813 升级）:
  ./scripts/retag-infer-images.sh --skip-lite-cpu 20260813-feature-eventworker2-5e4f4fe

示例（显式旧 TAG · 自 0817 升级）:
  ./scripts/retag-infer-images.sh --skip-lite-cpu 20260817-feature-eventworker2-0b26d8a

示例（0727 全量栈）:
  ./scripts/retag-infer-images.sh --skip-lite-cpu 20260727-test-from-4841de6a-85288b7
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

LIB="${PKG_ROOT}/scripts/lib/docker-cmd.sh"
[[ -f "${LIB}" ]] || LIB="$(cd "${SCRIPT_DIR}/../lib" && pwd)/docker-cmd.sh"
# shellcheck disable=SC1090
source "${LIB}"

# 构建旧 tag 候选列表（去重、排除 NEW_TAG）
OLD_TAG_CANDIDATES=()
_add_candidate() {
  local t="$1"
  [[ -z "${t}" || "${t}" == "${NEW_TAG}" ]] && return 0
  local c
  for c in "${OLD_TAG_CANDIDATES[@]}"; do
    [[ "${c}" == "${t}" ]] && return 0
  done
  OLD_TAG_CANDIDATES+=("${t}")
}

if [[ -n "${OLD_TAG}" ]]; then
  _add_candidate "${OLD_TAG}"
else
  if [[ -f "${BUILD_TAG_FILE}" ]]; then
    # shellcheck disable=SC1090
    source "${BUILD_TAG_FILE}" 2>/dev/null || true
    _add_candidate "${INFER_RETAG_OLD:-}"
    _add_candidate "${INFER_RETAG_OLD_ALT:-}"
    _add_candidate "${INFER_RETAG_OLD_ALT2:-}"
  fi
  # 0820 现场常见旧 tag（0817 增量 → 0820；0813 → 0820；0727 全量）
  _add_candidate "20260817-feature-eventworker2-0b26d8a"
  _add_candidate "20260813-feature-eventworker2-5e4f4fe"
  _add_candidate "20260727-test-from-4841de6a-85288b7"
fi

if [[ ${#OLD_TAG_CANDIDATES[@]} -eq 0 ]]; then
  echo "错误: 无可用旧 TAG 候选" >&2
  exit 1
fi

echo "==> 推理镜像 retag -> ${NEW_TAG}"
if [[ -n "${OLD_TAG}" ]]; then
  echo "    指定旧 TAG: ${OLD_TAG}"
else
  echo "    自动尝试旧 TAG: ${OLD_TAG_CANDIDATES[*]}"
fi
[[ "${SKIP_LITE_CPU}" -eq 1 ]] && echo "    （跳过 CPU lite）"

repos=(
  visual-dps-inference-lite
  visual-dps-inference-lite-gpu
  visual-dps-inference-lite-gpu-onnx
)

retag_repo() {
  local repo="$1"
  local new_ref="${repo}:${NEW_TAG}"

  if docker_cmd image inspect "${new_ref}" >/dev/null 2>&1; then
    echo "SKIP (已有): ${new_ref}"
    return 0
  fi

  local old_tag old_ref
  for old_tag in "${OLD_TAG_CANDIDATES[@]}"; do
    old_ref="${repo}:${old_tag}"
    if docker_cmd image inspect "${old_ref}" >/dev/null 2>&1; then
      docker_cmd tag "${old_ref}" "${new_ref}"
      echo "OK: ${old_ref} -> ${new_ref}"
      return 0
    fi
  done

  return 1
}

fail=0
for repo in "${repos[@]}"; do
  if [[ "${repo}" == "visual-dps-inference-lite" && "${SKIP_LITE_CPU}" -eq 1 ]]; then
    echo "SKIP: ${repo} (--skip-lite-cpu)"
    continue
  fi

  if retag_repo "${repo}"; then
    :
  elif [[ "${repo}" == "visual-dps-inference-lite" ]]; then
    echo "WARN: 缺少 ${repo} 旧/新 tag（CPU lite 非 GPU 现场必需；verify 时用 --skip-lite-cpu）" >&2
  else
    echo "FAIL: ${repo} 无旧 tag（${OLD_TAG_CANDIDATES[*]}）且无 ${repo}:${NEW_TAG}" >&2
    fail=1
  fi
done

if [[ "${fail}" -ne 0 ]]; then
  echo "" >&2
  echo "提示: 确认目标机已有 gpu/gpu-onnx 推理镜像（0817 / 0813 / 0727 任一 tag）。" >&2
  echo "      可显式指定: $0 --skip-lite-cpu <旧TAG>" >&2
  echo "      0817: 20260817-feature-eventworker2-0b26d8a" >&2
  echo "      0813: 20260813-feature-eventworker2-5e4f4fe" >&2
  echo "      0727: 20260727-test-from-4841de6a-85288b7" >&2
  exit 1
fi

echo "==> retag 完成。请执行: ./verify-images.sh --skip-lite-cpu"

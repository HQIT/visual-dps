#!/usr/bin/env bash
# 同一段 MP4 同时推到多路 MediaMTX path（cam2 cam3 … 各起一个 ffmpeg）
# 用法:
#   ./scripts/start-mp4-rtsp-multi.sh cam2 cam3 cam4 cam5
#   ./scripts/start-mp4-rtsp-multi.sh /path/to/video.mp4 cam2 cam3 cam4 cam5
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_VIDEO="${ROOT}/multi-samples.mp4"
DEFAULT_PATHS=(cam1 cam2 cam3 cam4 cam5 cam6 cam7 cam8)

if [[ $# -lt 1 ]]; then
  VIDEO="${DEFAULT_VIDEO}"
  PATHS=("${DEFAULT_PATHS[@]}")
elif [[ -f "${1}" ]]; then
  VIDEO="${1}"
  shift
  PATHS=("$@")
else
  VIDEO="${DEFAULT_VIDEO}"
  PATHS=("$@")
fi

if [[ ${#PATHS[@]} -eq 0 ]]; then
  echo "用法: $0 [视频文件] [path1 path2 ...]"
  echo "默认: $0   # 8 路 ${DEFAULT_PATHS[*]}"
  echo "示例: $0 /path/to/demo.mp4 cam1 cam2"
  exit 1
fi

if [[ ! -f "${VIDEO}" ]]; then
  echo "视频文件不存在: ${VIDEO}"
  exit 1
fi

for name in "${PATHS[@]}"; do
  echo ">>> 启动推流 ${name}"
  "${SCRIPT_DIR}/start-mp4-rtsp.sh" "${VIDEO}" "${name}"
done

echo ""
echo "已处理 ${#PATHS[@]} 路（${PATHS[*]}）。Dashboard 刷新后应显示在线。"

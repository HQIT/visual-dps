#!/usr/bin/env bash
# 停止 MP4 推流（按 path 的独立 pid 文件）
# 用法:
#   ./scripts/stop-mp4-rtsp.sh              # 停 cam1～cam8
#   ./scripts/stop-mp4-rtsp.sh all          # 同上
#   ./scripts/stop-mp4-rtsp.sh cam2 cam3
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RTSP_PORT="${MEDIAMTX_RTSP_PORT:-8554}"
if [[ -f "${ROOT}/.env" ]]; then
  val="$(grep -E '^MEDIAMTX_RTSP_PORT=' "${ROOT}/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d " '\"" || true)"
  [[ -n "${val}" ]] && RTSP_PORT="${val}"
fi

DEFAULT_PATHS=(cam1 cam2 cam3 cam4 cam5 cam6 cam7 cam8)

if [[ $# -eq 0 ]] || [[ "${1:-}" == "all" ]]; then
  PATHS=("${DEFAULT_PATHS[@]}")
else
  PATHS=("$@")
fi

stop_path() {
  local path="$1"
  local pidf="${SCRIPT_DIR}/.local/ffmpeg-mp4-rtsp-${path}.pid"
  local stopped=0

  if [[ -f "${pidf}" ]]; then
    local pid
    pid="$(cat "${pidf}")"
    if kill "${pid}" 2>/dev/null; then
      echo "已停止 ${path} (pid=${pid})"
      stopped=1
    fi
    sleep 0.2
    kill -9 "${pid}" 2>/dev/null || true
    rm -f "${pidf}"
  fi

  if pkill -f "ffmpeg.*${RTSP_PORT}/${path}" 2>/dev/null; then
    [[ "${stopped}" -eq 0 ]] && echo "已停止 ${path}（pkill ffmpeg）"
    stopped=1
  fi

  if [[ "${stopped}" -eq 0 ]]; then
    echo "${path}: 未在运行"
  fi
}

# 停止 cam9/cam10 等遗留推流（若存在）
for extra in cam9 cam10; do
  pkill -f "ffmpeg.*${RTSP_PORT}/${extra}" 2>/dev/null || true
  rm -f "${SCRIPT_DIR}/.local/ffmpeg-mp4-rtsp-${extra}.pid"
done

for path in "${PATHS[@]}"; do
  stop_path "${path}"
done

echo "完成。"

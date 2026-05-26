#!/usr/bin/env bash
# 将本地 MP4 循环推送到 compose 内 MediaMTX（每路 path 独立 ffmpeg 进程）
# 用法:
#   ./scripts/start-mp4-rtsp.sh
#   ./scripts/start-mp4-rtsp.sh /path/to/video.mp4 cam2
#   ./scripts/start-mp4-rtsp-multi.sh cam2 cam3 cam4 cam5
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MTX_CONTAINER="${MEDIAMTX_CONTAINER:-visual-dps-mediamtx}"

DEFAULT_VIDEO="${ROOT}/multi-samples.mp4"
VIDEO="${1:-${DEFAULT_VIDEO}}"
PATH_NAME="${2:-cam1}"

RTSP_PORT="${MEDIAMTX_RTSP_PORT:-8554}"
if [[ -f "${ROOT}/.env" ]]; then
  val="$(grep -E '^MEDIAMTX_RTSP_PORT=' "${ROOT}/.env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d " '\"" || true)"
  [[ -n "${val}" ]] && RTSP_PORT="${val}"
fi

RTSP_URL="rtsp://127.0.0.1:${RTSP_PORT}/${PATH_NAME}"
PID_FILE="${SCRIPT_DIR}/.local/ffmpeg-mp4-rtsp-${PATH_NAME}.pid"
LOG_FILE="${SCRIPT_DIR}/.local/ffmpeg-mp4-rtsp-${PATH_NAME}.log"

ensure_compose_mediamtx() {
  if ! command -v docker >/dev/null; then
    echo "未找到 docker，无法检查 MediaMTX 容器"
    exit 1
  fi
  if docker inspect -f '{{.State.Running}}' "${MTX_CONTAINER}" 2>/dev/null | grep -qx true; then
    return 0
  fi
  echo "MediaMTX 容器未运行 (${MTX_CONTAINER})"
  echo "请先执行: cd ${ROOT} && docker compose up -d mediamtx"
  exit 1
}

if [[ ! -f "${VIDEO}" ]]; then
  echo "视频文件不存在: ${VIDEO}"
  exit 1
fi

if ! command -v ffmpeg >/dev/null; then
  echo "未找到 ffmpeg，请先安装: sudo apt install ffmpeg"
  exit 1
fi

ensure_compose_mediamtx

if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
  echo "MP4 推流已在运行: ${PATH_NAME} (pid=$(cat "${PID_FILE}"))"
  echo "RTSP: ${RTSP_URL}"
  exit 0
fi

mkdir -p "${SCRIPT_DIR}/.local"

nohup ffmpeg -hide_banner -loglevel warning -re -stream_loop -1 \
  -i "${VIDEO}" \
  -vf "scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2" \
  -r 15 \
  -c:v libx264 -pix_fmt yuv420p -preset ultrafast -tune zerolatency \
  -b:v 800k -maxrate 800k -bufsize 1600k \
  -g 15 -f rtsp -rtsp_transport tcp \
  "${RTSP_URL}" \
  >"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"
sleep 2

if ! kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
  echo "推流启动失败 (${PATH_NAME})，日志:"
  tail -20 "${LOG_FILE}" || true
  exit 1
fi

echo "MP4 推流已启动: ${PATH_NAME} (pid=$(cat "${PID_FILE}"))"
echo "源文件: ${VIDEO}"
echo "RTSP: ${RTSP_URL}"
echo "日志: ${LOG_FILE}"

#!/usr/bin/env bash
# 在宿主机预下载 Docker 构建用的大文件（避免 build 时拉 GitHub）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${ROOT}/docker/prebuild"
PROXY="${GITHUB_PROXY_BASE:-https://ghfast.top}"

FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
FFMPEG_NAME="ffmpeg-master-latest-linux64-gpl.tar.xz"
FFMPEG_OUT="${OUT_DIR}/${FFMPEG_NAME}"

# 带 CUDA/NVDEC 的 FFmpeg（Jellyfin 发布 .deb）
JELLYFIN_TAG="v7.1.3-6"
JELLYFIN_JAMMY_NAME="jellyfin-ffmpeg7_7.1.3-6-jammy_amd64.deb"
JELLYFIN_BOOKWORM_NAME="jellyfin-ffmpeg7_7.1.3-6-bookworm_amd64.deb"
JELLYFIN_JAMMY_URL="https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/${JELLYFIN_TAG}/${JELLYFIN_JAMMY_NAME}"
JELLYFIN_BOOKWORM_URL="https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/${JELLYFIN_TAG}/${JELLYFIN_BOOKWORM_NAME}"
JELLYFIN_JAMMY_OUT="${OUT_DIR}/${JELLYFIN_JAMMY_NAME}"
JELLYFIN_BOOKWORM_OUT="${OUT_DIR}/${JELLYFIN_BOOKWORM_NAME}"

mirror_url() {
  local url="$1"
  case "$url" in
    https://github.com/*|http://github.com/*)
      if [[ -n "${PROXY}" ]]; then
        echo "${PROXY%/}/${url}"
        return
      fi
      ;;
  esac
  echo "$url"
}

mkdir -p "${OUT_DIR}"

if [[ -f "${FFMPEG_OUT}" ]] && [[ "$(stat -c%s "${FFMPEG_OUT}" 2>/dev/null || echo 0)" -gt 100000000 ]]; then
  echo "已存在: ${FFMPEG_OUT} ($(du -h "${FFMPEG_OUT}" | cut -f1))"
else
  DL="$(mirror_url "${FFMPEG_URL}")"
  echo "下载 ffmpeg: ${DL}"
  wget -c --timeout=60 --tries=5 -O "${FFMPEG_OUT}.part" "${DL}"
  mv "${FFMPEG_OUT}.part" "${FFMPEG_OUT}"
  echo "OK: ${FFMPEG_OUT} ($(du -h "${FFMPEG_OUT}" | cut -f1))"
fi

download_deb() {
  local url="$1" out="$2" label="$3"
  if [[ -f "${out}" ]] && [[ "$(stat -c%s "${out}" 2>/dev/null || echo 0)" -gt 10000000 ]]; then
    echo "已存在: ${out} ($(du -h "${out}" | cut -f1))"
    return 0
  fi
  local dl
  dl="$(mirror_url "${url}")"
  echo "下载 ${label}: ${dl}"
  wget -c --timeout=120 --tries=5 -O "${out}.part" "${dl}"
  mv "${out}.part" "${out}"
  echo "OK: ${out} ($(du -h "${out}" | cut -f1))"
}

download_deb "${JELLYFIN_JAMMY_URL}" "${JELLYFIN_JAMMY_OUT}" "jellyfin-ffmpeg jammy (推理 GPU 基座)"
download_deb "${JELLYFIN_BOOKWORM_URL}" "${JELLYFIN_BOOKWORM_OUT}" "jellyfin-ffmpeg bookworm (UI)"

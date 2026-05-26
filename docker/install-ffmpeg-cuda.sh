#!/bin/sh
# 安装带 CUDA/NVDEC 的 FFmpeg（Jellyfin .deb 优先），否则回退 BtbN 软解 tar.xz。
set -eu

JELLYFIN_DEB="${1:-/tmp/jellyfin-ffmpeg.deb}"
SOFT_TAR="${2:-/tmp/ffmpeg-soft.tar.xz}"
INSTALL_DIR="/usr/lib/jellyfin-ffmpeg"

min_size() {
  # shellcheck disable=SC2039
  [ -f "$1" ] && [ "$(stat -c%s "$1" 2>/dev/null || echo 0)" -gt "$2" ]
}

install_from_dir() {
  src_dir="$1"
  rm -rf "${INSTALL_DIR}"
  mkdir -p "${INSTALL_DIR}"
  # shellcheck disable=SC2039
  cp -a "${src_dir}/." "${INSTALL_DIR}/"
  ln -sf "${INSTALL_DIR}/ffmpeg" /usr/local/bin/ffmpeg
  ln -sf "${INSTALL_DIR}/ffprobe" /usr/local/bin/ffprobe
  if [ -d "${INSTALL_DIR}/lib" ]; then
    echo "${INSTALL_DIR}/lib" >/etc/ld.so.conf.d/jellyfin-ffmpeg.conf
    ldconfig 2>/dev/null || true
  fi
}

install_jellyfin_deb() {
  work="/tmp/jff-deb"
  rm -rf "${work}"
  mkdir -p "${work}"
  dpkg-deb -x "${JELLYFIN_DEB}" "${work}"
  ffbin="$(find "${work}" -type f -path '*/jellyfin-ffmpeg/ffmpeg' 2>/dev/null | head -1)"
  if [ -z "${ffbin}" ]; then
    ffbin="$(find "${work}" -type f -name ffmpeg 2>/dev/null | head -1)"
  fi
  if [ -z "${ffbin}" ]; then
    echo "install-ffmpeg-cuda: deb 内未找到 ffmpeg" >&2
    return 1
  fi
  install_from_dir "$(dirname "${ffbin}")"
  echo "install-ffmpeg-cuda: jellyfin-ffmpeg (NVDEC) -> ${INSTALL_DIR}"
  ffmpeg -hide_banner -hwaccels 2>/dev/null | head -8 || true
}

install_soft_tar() {
  work="/tmp/ff-soft-extract"
  rm -rf "${work}"
  mkdir -p "${work}"
  tar -xJf "${SOFT_TAR}" -C "${work}" --strip-components=1
  install -m755 "${work}/bin/ffmpeg" "${work}/bin/ffprobe" /usr/local/bin/ 2>/dev/null \
    || install -m755 "${work}/ffmpeg" "${work}/ffprobe" /usr/local/bin/
  echo "install-ffmpeg-cuda: WARNING 使用无 CUDA 的 FFmpeg 软解包" >&2
}

if min_size "${JELLYFIN_DEB}" 10000000; then
  install_jellyfin_deb
elif min_size "${SOFT_TAR}" 50000000; then
  install_soft_tar
else
  echo "install-ffmpeg-cuda: 缺少有效 FFmpeg 离线包 (.deb 或 .tar.xz)" >&2
  exit 1
fi

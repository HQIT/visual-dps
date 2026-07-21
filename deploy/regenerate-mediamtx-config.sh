#!/usr/bin/env bash
# 按 app/.env + camera_ips.json 重新生成 localdata/mediamtx.yml（与 UI「应用 MediaMTX 配置」一致）
# 用法: ./deploy/regenerate-mediamtx-config.sh [app目录，默认当前目录]
set -euo pipefail

APP_DIR="${1:-.}"
APP_DIR="$(cd "${APP_DIR}" && pwd)"
ENV_FILE="${APP_DIR}/.env"
LOCALDATA="${APP_DIR}/localdata"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "错误: 缺少 ${ENV_FILE}" >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a && source "${ENV_FILE}" && set +a

TAG="${VISUAL_DPS_IMAGE_TAG:-latest}"
UI_IMAGE="visual-dps-visual-dps-ui:${TAG}"
if ! docker image inspect "${UI_IMAGE}" >/dev/null 2>&1; then
  UI_IMAGE="visual-dps-visual-dps-ui:latest"
fi
if ! docker image inspect "${UI_IMAGE}" >/dev/null 2>&1; then
  echo "错误: 未找到 UI 镜像 visual-dps-visual-dps-ui:${TAG}（请先 docker load）" >&2
  exit 1
fi

mkdir -p "${LOCALDATA}/json/cameras"
[[ -f "${LOCALDATA}/camera_ips.json" ]] || {
  echo "错误: 缺少 ${LOCALDATA}/camera_ips.json" >&2
  exit 1
}

APP_CONFIG_MOUNT=()
if [[ -f "${APP_DIR}/app_config.json" ]]; then
  APP_CONFIG_MOUNT=(-v "${APP_DIR}/app_config.json:/app/app_config.json:ro")
fi

echo "==> 按 .env 生成 mediamtx.yml（镜像 ${UI_IMAGE}）..."
# 须 -i：否则 heredoc 留在宿主机 stdin，容器内 python3 - 读不到脚本
docker run --rm -i \
  -v "${LOCALDATA}:/app/localdata" \
  "${APP_CONFIG_MOUNT[@]}" \
  --env-file "${ENV_FILE}" \
  -e MEDIAMTX_CONFIG_PATH=/app/localdata/mediamtx.yml \
  -e CAMERA_IPS_FILE=/app/localdata/camera_ips.json \
  -e JSON_DIR=/app/localdata/json \
  "${UI_IMAGE}" \
  python3 - <<'PY'
import os
from urllib.parse import urlparse, urlunparse

from services.camera_store import apply_mediamtx, load_cameras, save_cameras

cam_file = os.environ["CAMERA_IPS_FILE"]
mtx_file = os.environ["MEDIAMTX_CONFIG_PATH"]
rtsp_port = int(os.environ.get("MEDIAMTX_RTSP_PORT", "8554"))
public_host = (os.environ.get("MEDIAMTX_PUBLIC_HOST") or "").strip().lower()
local_hosts = {
    "127.0.0.1",
    "localhost",
    "mediamtx",
    (os.environ.get("MEDIAMTX_RTSP_HOST") or "").strip().lower(),
    public_host,
    (os.environ.get("MEDIAMTX_INTERNAL_HOST") or "mediamtx").strip().lower(),
}

items = load_cameras(cam_file)
changed = False
for c in items:
    url = str(c.get("url") or "").strip()
    if not url.lower().startswith("rtsp://"):
        continue
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host not in local_hosts:
        continue
    path = p.path or f"/{c.get('path') or c.get('id') or ''}"
    port = p.port if p.port is not None else 8554
    if port == rtsp_port:
        continue
    c["url"] = urlunparse((p.scheme, f"{p.hostname}:{rtsp_port}", path, "", "", ""))
    changed = True

if changed:
    save_cameras(cam_file, items)
    print(f"已对齐 camera_ips.json 中本机 RTSP 端口 -> {rtsp_port}")

result = apply_mediamtx(cam_file, mtx_file)
print(f"mediamtx.yml 已更新 -> {mtx_file}")
print(f"MEDIAMTX_PUBLIC_HOST={os.environ.get('MEDIAMTX_PUBLIC_HOST', '')}")
PY

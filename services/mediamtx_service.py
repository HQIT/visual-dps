"""根据摄像头配置生成 MediaMTX paths 配置。"""

import json
import os
import re
import urllib.error
import urllib.request
from typing import List
from urllib.parse import quote, unquote, urlparse

MEDIAMTX_RTSP_HOST = os.environ.get("MEDIAMTX_RTSP_HOST", "127.0.0.1")
MEDIAMTX_INTERNAL_HOST = os.environ.get("MEDIAMTX_INTERNAL_HOST", "mediamtx")
MEDIAMTX_RTSP_PORT = int(os.environ.get("MEDIAMTX_RTSP_PORT", "8554"))
MEDIAMTX_HLS_PORT = int(os.environ.get("MEDIAMTX_HLS_PORT", "8888"))
MEDIAMTX_WEBRTC_PORT = int(os.environ.get("MEDIAMTX_WEBRTC_PORT", "8889"))
MEDIAMTX_PUBLIC_HOST = os.environ.get("MEDIAMTX_PUBLIC_HOST", "127.0.0.1")
MEDIAMTX_WEBRTC_ICE_PORT = int(os.environ.get("MEDIAMTX_WEBRTC_ICE_PORT", "8189"))
MEDIAMTX_API_URL = os.environ.get("MEDIAMTX_API_URL", "http://127.0.0.1:9997").rstrip("/")
MEDIAMTX_API_TIMEOUT = float(os.environ.get("MEDIAMTX_API_TIMEOUT", "3"))

SOURCE_V4L2 = "v4l2"
SOURCE_RTSP_PULL = "rtsp_pull"
SOURCE_PUBLISHER = "publisher"
SOURCE_EXTERNAL = "external"

MANAGED_SOURCE_TYPES = {SOURCE_V4L2, SOURCE_RTSP_PULL, SOURCE_PUBLISHER}


def yaml_path_key(path: str) -> str:
    """MediaMTX paths 下的 YAML 键；纯数字等须加引号，否则会被解析成整数而非字符串。"""
    slug = str(path or "").strip()
    if not slug:
        return '""'
    if slug.isdigit() or (slug and slug[0].isdigit()):
        escaped = slug.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if slug.lower() in ("true", "false", "yes", "no", "on", "off", "null", "~"):
        escaped = slug.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]*", slug):
        return slug
    escaped = slug.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def encode_rtsp_url_credentials(url: str) -> str:
    """RTSP URL 中密码含 , @ # 等须百分号编码，否则 FFmpeg/MediaMTX 认证失败。"""
    s = str(url or "").strip()
    if not s:
        return s
    lower = s.lower()
    if not (lower.startswith("rtsp://") or lower.startswith("rtsps://")):
        return s
    scheme_end = s.index("://") + 3
    rest = s[scheme_end:]
    if "@" not in rest:
        return s
    userinfo, authority = rest.rsplit("@", 1)
    if ":" not in userinfo:
        user_enc = quote(unquote(userinfo), safe="")
        return f"{s[:scheme_end]}{user_enc}@{authority}"
    user, passwd = userinfo.split(":", 1)
    user_enc = quote(unquote(user), safe="")
    pass_enc = quote(unquote(passwd), safe="")
    return f"{s[:scheme_end]}{user_enc}:{pass_enc}@{authority}"


def build_playback_url(path: str, host: str | None = None, port: int | None = None) -> str:
    h = host or MEDIAMTX_RTSP_HOST
    p = port or MEDIAMTX_RTSP_PORT
    slug = str(path or "").strip().strip("/")
    return f"rtsp://{h}:{p}/{slug}"


def build_hls_url(path: str, host: str | None = None, port: int | None = None) -> str:
    slug = str(path or "").strip().strip("/")
    if not slug:
        return ""
    h = host or MEDIAMTX_PUBLIC_HOST
    p = port or MEDIAMTX_HLS_PORT
    return f"http://{h}:{p}/{slug}/index.m3u8"


def build_webrtc_whep_url(path: str, host: str | None = None, port: int | None = None) -> str:
    slug = str(path or "").strip().strip("/")
    if not slug:
        return ""
    h = host or MEDIAMTX_PUBLIC_HOST
    p = port or MEDIAMTX_WEBRTC_PORT
    return f"http://{h}:{p}/{slug}/whep"


def build_camera_playback_urls(camera_id: str, path: str, *, proxied: bool = True) -> dict:
    """返回浏览器可访问的播放地址；默认走 UI 同源代理，避免跨域与 Docker 网络问题。"""
    slug = str(path or "").strip().strip("/")
    cid = str(camera_id or "").strip()
    if not slug or not cid:
        return {"hls": "", "whep": ""}
    if proxied:
        return {
            "hls": f"/api/cameras/{cid}/hls/index.m3u8",
            "whep": f"/api/cameras/{cid}/whep",
        }
    return {
        "hls": build_hls_url(slug),
        "whep": build_webrtc_whep_url(slug),
    }


def is_mediamtx_managed(camera: dict) -> bool:
    return camera.get("source_type") in MANAGED_SOURCE_TYPES and camera.get("enabled", True)


def _local_mediamtx_hosts() -> set[str]:
    return {
        MEDIAMTX_RTSP_HOST.lower(),
        MEDIAMTX_PUBLIC_HOST.lower(),
        MEDIAMTX_INTERNAL_HOST.lower(),
        "127.0.0.1",
        "localhost",
        "mediamtx",
    }


def is_local_mediamtx_rtsp_url(url: str, path: str = "") -> bool:
    """是否为指向本机 MediaMTX 的 RTSP 播放地址（可选校验 path 一致）。"""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() != "rtsp":
        return False
    host = (parsed.hostname or "").lower()
    port = parsed.port if parsed.port is not None else MEDIAMTX_RTSP_PORT
    if host not in _local_mediamtx_hosts() or port != MEDIAMTX_RTSP_PORT:
        return False
    slug = str(path or "").strip()
    if slug:
        return path_from_url(url) == slug
    return True


def is_mediamtx_playback_available(camera: dict) -> bool:
    """是否可走 MediaMTX 的 HLS/WebRTC（含 external 但 RTSP 指向本机 MediaMTX 路径）。"""
    if not camera.get("enabled", True):
        return False
    if camera.get("source_type") in MANAGED_SOURCE_TYPES:
        return True
    path = str(camera.get("path") or camera.get("id") or "").strip()
    url = str(camera.get("url") or "").strip()
    if not path or not url:
        return False
    return is_local_mediamtx_rtsp_url(url, path)


def path_from_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    parts = [p for p in parsed.path.split("/") if p]
    return parts[-1] if parts else ""


def mediamtx_path_ready(path: str) -> bool:
    """通过 MediaMTX Control API 判断 path 是否已有可读流（无需 UI 侧 RTSP 解码）。"""
    slug = str(path or "").strip().strip("/")
    if not slug or not MEDIAMTX_API_URL:
        return False
    try:
        data = _mediamtx_api("GET", f"/v3/paths/get/{slug}")
        return bool(data.get("ready"))
    except Exception:
        return False


def list_mediamtx_paths_summary() -> dict:
    """MediaMTX path 列表摘要，供服务总览使用。"""
    if not MEDIAMTX_API_URL:
        return {"ok": False, "message": "MediaMTX API 未配置", "paths": []}
    try:
        data = _mediamtx_api("GET", "/v3/paths/list?itemsPerPage=200")
        items = data.get("items") or []
        paths = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            paths.append(
                {
                    "name": name,
                    "ready": bool(item.get("ready")),
                    "source_type": item.get("source", {}).get("type")
                    if isinstance(item.get("source"), dict)
                    else item.get("source"),
                    "readers": len(item.get("readers") or []),
                }
            )
        paths.sort(key=lambda p: p["name"])
        return {"ok": True, "paths": paths}
    except Exception as exc:
        return {"ok": False, "message": str(exc), "paths": []}


def mediamtx_path_ready_for_camera(camera: dict) -> bool:
    if not is_mediamtx_playback_available(camera):
        return False
    slug = str(camera.get("path") or camera.get("id") or "").strip()
    return mediamtx_path_ready(slug)


def cameras_needing_mediamtx_paths(cameras: List[dict]) -> List[dict]:
    """需在 MediaMTX 上存在 path 的摄像头（托管源 + 指向本机 MTX 的 external）。"""
    out: List[dict] = []
    seen: set[str] = set()
    for cam in cameras:
        if not cam.get("enabled", True):
            continue
        path = str(cam.get("path") or cam.get("id") or "").strip()
        if not path or path in seen:
            continue
        if is_mediamtx_managed(cam) or is_mediamtx_playback_available(cam):
            seen.add(path)
            out.append(cam)
    return out


def generate_mediamtx_yaml(cameras: List[dict]) -> str:
    lines = [
        "# 由 visual-dps 根据摄像头配置自动生成，请勿手改（可在 Dashboard 管理）",
        "",
        "logLevel: info",
        "api: yes",
        "apiAddress: :9997",
        f"rtspAddress: :{MEDIAMTX_RTSP_PORT}",
        "rtmpAddress: ''",
        f"hlsAddress: :{MEDIAMTX_HLS_PORT}",
        "hlsVariant: mpegts",
        f"webrtcAddress: :{MEDIAMTX_WEBRTC_PORT}",
        "webrtcAllowOrigin: '*'",
        "# Docker：勿向浏览器通告容器网桥 IP，仅通告宿主机可访问地址",
        "webrtcIPsFromInterfaces: false",
        f"webrtcAdditionalHosts: ['{MEDIAMTX_PUBLIC_HOST}']",
        f"webrtcLocalUDPAddress: :{MEDIAMTX_WEBRTC_ICE_PORT}",
        f"webrtcLocalTCPAddress: :{MEDIAMTX_WEBRTC_ICE_PORT}",
        "srtAddress: ''",
        "",
        "# compose 内 UI 经 Docker 网桥访问 API，需放开私网段（默认仅 localhost 免鉴权）",
        "authInternalUsers:",
        "  - user: any",
        "    pass:",
        "    ips: []",
        "    permissions:",
        "      - action: publish",
        "        path:",
        "      - action: read",
        "        path:",
        "      - action: playback",
        "        path:",
        "  - user: any",
        "    pass:",
        "    ips: ['127.0.0.1', '::1', '172.16.0.0/12', '10.0.0.0/8', '192.168.0.0/16']",
        "    permissions:",
        "      - action: api",
        "      - action: metrics",
        "      - action: pprof",
        "",
        "paths:",
    ]

    mtx_paths = cameras_needing_mediamtx_paths(cameras)
    if not mtx_paths:
        lines.append("  # 暂无需要在 MediaMTX 注册的流路径")
        return "\n".join(lines) + "\n"

    for cam in mtx_paths:
        path = str(cam.get("path") or cam.get("id") or "").strip()
        if not path:
            continue
        source_type = cam.get("source_type")
        lines.append(f"  {yaml_path_key(path)}:")

        if source_type == SOURCE_EXTERNAL:
            lines.append("    source: publisher")
            lines.append("")
            continue

        if source_type == SOURCE_RTSP_PULL:
            pull_url = encode_rtsp_url_credentials(str(cam.get("pull_url") or "").strip())
            lines.append(f"    source: {pull_url}")
        elif source_type == SOURCE_PUBLISHER:
            lines.append("    source: publisher")
        elif source_type == SOURCE_V4L2:
            device = str(cam.get("device") or "/dev/video0").strip()
            video_size = str(cam.get("video_size") or "640x480").strip()
            framerate = int(cam.get("framerate") or 15)
            lines.append("    source: publisher")
            lines.append("    runOnInit: >")
            lines.append("      ffmpeg -hide_banner -loglevel warning")
            lines.append(f"      -f v4l2 -input_format mjpeg -video_size {video_size} -framerate {framerate}")
            lines.append(f"      -i {device}")
            lines.append(
                "      -c:v libx264 -pix_fmt yuv420p -preset ultrafast -tune zerolatency"
            )
            lines.append("      -b:v 800k -maxrate 800k -bufsize 1600k")
            lines.append(f"      -g {framerate} -f rtsp -rtsp_transport tcp")
            lines.append("      rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH")
            lines.append("    runOnInitRestart: yes")
        lines.append("")

    return "\n".join(lines)


def _v4l2_run_on_init(cam: dict) -> str:
    device = str(cam.get("device") or "/dev/video0").strip()
    video_size = str(cam.get("video_size") or "640x480").strip()
    framerate = int(cam.get("framerate") or 15)
    return (
        "ffmpeg -hide_banner -loglevel warning "
        f"-f v4l2 -input_format mjpeg -video_size {video_size} -framerate {framerate} "
        f"-i {device} "
        "-c:v libx264 -pix_fmt yuv420p -preset ultrafast -tune zerolatency "
        "-b:v 800k -maxrate 800k -bufsize 1600k "
        f"-g {framerate} -f rtsp -rtsp_transport tcp "
        "rtsp://127.0.0.1:$RTSP_PORT/$MTX_PATH"
    )


def camera_to_path_conf(cam: dict) -> dict:
    source_type = cam.get("source_type")
    if source_type == SOURCE_EXTERNAL:
        if is_mediamtx_playback_available(cam):
            return {"source": "publisher"}
        return {}
    if source_type == SOURCE_RTSP_PULL:
        return {"source": encode_rtsp_url_credentials(str(cam.get("pull_url") or "").strip())}
    if source_type == SOURCE_PUBLISHER:
        return {"source": "publisher"}
    if source_type == SOURCE_V4L2:
        return {
            "source": "publisher",
            "runOnInit": _v4l2_run_on_init(cam),
            "runOnInitRestart": True,
        }
    return {}


def _mediamtx_api(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{MEDIAMTX_API_URL}{path}"
    payload = None
    headers = {}
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=MEDIAMTX_API_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def reload_mediamtx_runtime(cameras: List[dict]) -> dict:
    """通过 Control API 热更新托管路径；不可用时静默跳过（仍依赖配置文件热重载）。"""
    mtx_paths = cameras_needing_mediamtx_paths(cameras)
    if not MEDIAMTX_API_URL:
        return {"reloaded": False, "skipped": True, "reason": "api_disabled"}

    try:
        listed = _mediamtx_api("GET", "/v3/config/paths/list?itemsPerPage=100")
        current_names = {
            str(item.get("name") or "").strip()
            for item in listed.get("items", [])
            if item.get("name")
        }
        desired_names = {str(c.get("path") or c.get("id") or "").strip() for c in mtx_paths}
        desired_names.discard("")

        for name in current_names:
            if name in desired_names or name in ("all_others", ""):
                continue
            try:
                _mediamtx_api("DELETE", f"/v3/config/paths/delete/{name}")
            except urllib.error.HTTPError:
                pass

        for cam in mtx_paths:
            name = str(cam.get("path") or cam.get("id") or "").strip()
            if not name:
                continue
            conf = camera_to_path_conf(cam)
            try:
                _mediamtx_api("POST", f"/v3/config/paths/replace/{name}", conf)
            except urllib.error.HTTPError as err:
                if err.code == 404:
                    _mediamtx_api("POST", f"/v3/config/paths/add/{name}", conf)
                else:
                    raise

        return {"reloaded": True, "paths": sorted(desired_names)}
    except Exception as exc:
        return {"reloaded": False, "skipped": True, "reason": str(exc)}


def _mediamtx_config_has_unquoted_numeric_paths(text: str) -> bool:
    """检测 paths 下未加引号的纯数字键（YAML 会解析为 int，导致 MediaMTX path 异常）。"""
    in_paths = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "paths:":
            in_paths = True
            continue
        if in_paths and line and not line.startswith(" "):
            in_paths = False
        if not in_paths:
            continue
        if re.match(r"^\d+:$", stripped):
            return True
    return False


def _mediamtx_config_needs_rewrite(config_path: str) -> bool:
    """旧版 apiAddress 绑定 127.0.0.1 时，compose 内其它容器无法访问 API。"""
    if not os.path.isfile(config_path):
        return True
    try:
        with open(config_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return True
    if _mediamtx_config_has_unquoted_numeric_paths(text):
        return True
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("apiAddress:"):
            continue
        if ":9997" in s and "127.0.0.1" not in s:
            return False
        return True
    return True


def write_mediamtx_config(config_path: str, cameras: List[dict]) -> str:
    os.makedirs(os.path.dirname(config_path) or ".", exist_ok=True)
    content = generate_mediamtx_yaml(cameras)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)
    return config_path


def ensure_mediamtx_config(config_path: str, cameras: List[dict]) -> dict | None:
    """启动时修正遗留 mediamtx.yml（仅写文件，不调用运行时 API）。"""
    if not _mediamtx_config_needs_rewrite(config_path):
        return None
    path = write_mediamtx_config(config_path, cameras)
    return {
        "rewritten": True,
        "config_path": path,
        "hint": "已修正 mediamtx.yml 的 apiAddress；若 MediaMTX 已在运行请 docker compose restart mediamtx",
    }


def sync_mediamtx_config(config_path: str, cameras: List[dict]) -> dict:
    path = write_mediamtx_config(config_path, cameras)
    reload = reload_mediamtx_runtime(cameras)
    mtx_paths = cameras_needing_mediamtx_paths(cameras)
    if reload.get("reloaded"):
        hint = "视频流设置已自动生效。"
    else:
        hint = "视频流配置已更新；若画面未变化，请稍候片刻。"
    return {
        "config_path": path,
        "managed_paths": [c.get("path") or c.get("id") for c in mtx_paths],
        "reload": reload,
        "reload_hint": hint,
    }

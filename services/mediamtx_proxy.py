"""将 HLS / WHEP 请求代理到本机 MediaMTX（供 UI 同源访问）。"""

from __future__ import annotations

import os
import re

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

MEDIAMTX_HLS_BASE = os.environ.get(
    "MEDIAMTX_HLS_BASE", f"http://127.0.0.1:{os.environ.get('MEDIAMTX_HLS_PORT', '8888')}"
).rstrip("/")
MEDIAMTX_WEBRTC_BASE = os.environ.get(
    "MEDIAMTX_WEBRTC_BASE",
    f"http://127.0.0.1:{os.environ.get('MEDIAMTX_WEBRTC_PORT', '8889')}",
).rstrip("/")
PROXY_TIMEOUT = float(os.environ.get("MEDIAMTX_PROXY_TIMEOUT", "15"))
MEDIAMTX_PUBLIC_HOST = os.environ.get("MEDIAMTX_PUBLIC_HOST", "127.0.0.1")
# 容器内网 / RFC1918，浏览器无法直连，需在 WHEP Answer 中改写为 MEDIAMTX_PUBLIC_HOST
_PRIVATE_IP_RE = re.compile(
    r"\b(?:10(?:\.\d{1,3}){3}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|192\.168(?:\.\d{1,3}){2})\b"
)


def _rewrite_webrtc_sdp(body: bytes) -> bytes:
    text = body.decode("utf-8", errors="replace")
    host = (MEDIAMTX_PUBLIC_HOST or "127.0.0.1").strip()
    if host not in ("127.0.0.1", "localhost", "::1"):
        # MediaMTX 常通告 127.0.0.1；远程浏览器必须改为宿主机 LAN IP
        text = text.replace("127.0.0.1", host).replace("localhost", host)
    if _PRIVATE_IP_RE.search(text):
        text = _PRIVATE_IP_RE.sub(host, text)
    return text.encode("utf-8")


def _rewrite_m3u8(body: bytes, camera_id: str, path_slug: str) -> bytes:
    text = body.decode("utf-8", errors="replace")
    prefix = f"/api/cameras/{camera_id}/hls/"
    # 绝对路径 /{path}/...
    text = re.sub(
        rf"(?m)^(/{re.escape(path_slug)}/)",
        prefix,
        text,
    )
    # 相对片段（不以 http 开头）
    lines = []
    for line in text.splitlines():
        if line and not line.startswith("#") and not line.startswith("http"):
            if not line.startswith("/"):
                line = f"{prefix}{line}"
        lines.append(line)
    return "\n".join(lines).encode("utf-8")


async def proxy_hls(camera_id: str, path_slug: str, subpath: str, request: Request) -> Response:
    target = f"{MEDIAMTX_HLS_BASE}/{path_slug}/{subpath}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
            upstream = await client.get(target)
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": f"无法连接 MediaMTX HLS（{MEDIAMTX_HLS_BASE}）：{exc}",
                "hint": "请确认 MediaMTX 已启动且 hlsAddress 已开启（8888）",
            },
        )

    content = upstream.content
    ctype = (upstream.headers.get("content-type") or "").lower()
    if "mpegurl" in ctype or subpath.endswith(".m3u8"):
        content = _rewrite_m3u8(content, camera_id, path_slug)

    headers = {}
    if ctype:
        headers["Content-Type"] = upstream.headers.get("content-type")
    return Response(content=content, status_code=upstream.status_code, headers=headers)


def _rewrite_whep_location(location: str, camera_id: str, path_slug: str) -> str:
    """将 MediaMTX 返回的会话 Location 改写为 UI 同源路径（供 Trickle ICE PATCH/DELETE）。"""
    loc = (location or "").strip()
    if not loc:
        return loc
    marker = f"/{path_slug}/whep/"
    idx = loc.find(marker)
    if idx >= 0:
        session_id = loc[idx + len(marker) :].split("?")[0].strip("/")
        if session_id:
            return f"/api/cameras/{camera_id}/whep/{session_id}"
    # 已是同源路径
    if loc.startswith(f"/api/cameras/{camera_id}/whep"):
        return loc
    return loc


async def proxy_whep(
    camera_id: str,
    path_slug: str,
    request: Request,
    session_suffix: str = "",
) -> Response:
    target = f"{MEDIAMTX_WEBRTC_BASE}/{path_slug}/whep"
    suffix = (session_suffix or "").strip().strip("/")
    if suffix:
        target = f"{target}/{suffix}"

    headers: dict[str, str] = {}
    for key in ("content-type", "if-match"):
        val = request.headers.get(key)
        if val:
            headers[key] = val

    body = None
    if request.method in ("POST", "PATCH"):
        body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=PROXY_TIMEOUT) as client:
            upstream = await client.request(request.method, target, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": f"无法连接 MediaMTX WebRTC（{MEDIAMTX_WEBRTC_BASE}）：{exc}",
                "hint": "请确认 MediaMTX 已启动且 webrtcAddress 已开启（8889），并重启 MediaMTX 加载新配置",
            },
        )

    out_headers: dict[str, str] = {}
    for key in ("content-type", "location", "etag", "link"):
        if key not in upstream.headers:
            continue
        val = upstream.headers[key]
        if key == "location":
            val = _rewrite_whep_location(val, camera_id, path_slug)
        out_headers[key] = val

    if request.method == "POST" and "content-type" not in out_headers:
        out_headers["Content-Type"] = "application/sdp"

    content = upstream.content
    ctype = (out_headers.get("content-type") or upstream.headers.get("content-type") or "").lower()
    if request.method == "POST" and upstream.is_success and "sdp" in ctype:
        content = _rewrite_webrtc_sdp(content)

    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=out_headers,
    )

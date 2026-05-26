"""摄像头地址管理、在线状态与抓帧服务。"""

import base64
import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

PROBE_TTL_SEC = max(5, int(os.environ.get("CAMERA_PROBE_TTL", "20")))
PROBE_MAX_WORKERS = max(1, int(os.environ.get("CAMERA_PROBE_MAX_WORKERS", "8")))

_camera_runtime: dict = {}


def normalize_rtsp_url(url: str) -> str:
    """宿主机配置多为 rtsp://127.0.0.1:8554/…；compose 内 UI/推理应连 mediamtx 服务名。"""
    rewritten = str(url or "")
    rtsp_host_rewrite = os.environ.get("RTSP_HOST_REWRITE", "").strip()
    if rtsp_host_rewrite:
        if "127.0.0.1" in rewritten:
            rewritten = rewritten.replace("127.0.0.1", rtsp_host_rewrite)
        if "localhost" in rewritten:
            rewritten = rewritten.replace("localhost", rtsp_host_rewrite)
        return rewritten
    mtx_host = os.environ.get("MEDIAMTX_INTERNAL_HOST", "").strip()
    if mtx_host:
        for local in ("127.0.0.1", "localhost"):
            rewritten = rewritten.replace(f"rtsp://{local}:", f"rtsp://{mtx_host}:")
    return rewritten


def camera_id_from_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def camera_thumbnail_path(frames_dir: str, camera_id: str) -> str:
    os.makedirs(frames_dir, exist_ok=True)
    return os.path.join(frames_dir, f"{camera_id}.jpg")


def get_camera_thumbnail_path(frames_dir: str, camera_id: str) -> str | None:
    path = camera_thumbnail_path(frames_dir, camera_id)
    return path if os.path.isfile(path) else None


def stable_camera_id(record: dict) -> str:
    return str(record.get("id") or record.get("path") or camera_id_from_url(record.get("url", "")))


def load_camera_ips(camera_ips_file: str) -> List[dict]:
    from services.camera_store import load_cameras

    return [{"name": c["name"], "url": c["url"]} for c in load_cameras(camera_ips_file)]


def save_camera_ips(camera_ips_file: str, items: List[dict]):
    from services.camera_store import save_camera_ips as _save

    _save(camera_ips_file, items)


def _decode_jpeg_payload(image: str) -> bytes:
    raw = str(image or "").strip()
    if not raw:
        raise ValueError("image is required")
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("invalid base64 image") from exc
    if len(data) < 128:
        raise ValueError("image too small")
    return data


def save_thumbnail_bytes(
    camera_id: str,
    jpeg_bytes: bytes,
    *,
    frames_dir: str,
    last_frame_file: str = "",
) -> str:
    thumb_path = camera_thumbnail_path(frames_dir, camera_id)
    with open(thumb_path, "wb") as f:
        f.write(jpeg_bytes)
    if last_frame_file:
        os.makedirs(os.path.dirname(last_frame_file) or ".", exist_ok=True)
        with open(last_frame_file, "wb") as f:
            f.write(jpeg_bytes)
    return thumb_path


def probe_camera_online(url: str, camera: dict | None = None) -> bool:
    from services.mediamtx_service import mediamtx_path_ready, mediamtx_path_ready_for_camera, path_from_url

    if camera and mediamtx_path_ready_for_camera(camera):
        return True
    slug = path_from_url(url)
    if slug and mediamtx_path_ready(slug):
        return True
    return False


def _update_runtime_status(camera_id: str, online: bool) -> dict:
    now = time.time()
    state = _camera_runtime.setdefault(camera_id, {})
    was_online = bool(state.get("online"))

    if online:
        if not was_online or state.get("online_since") is None:
            state["online_since"] = now
        state["online"] = True
        activity_seconds = int(now - state["online_since"])
    else:
        state["online"] = False
        state["online_since"] = None
        activity_seconds = 0

    state["last_check"] = now
    return {"online": online, "activity_seconds": activity_seconds, "last_check": now}


def _cached_camera_status(cid: str) -> dict | None:
    now = time.time()
    state = _camera_runtime.get(cid)
    if not state or not state.get("last_check"):
        return None
    if (now - float(state["last_check"])) >= PROBE_TTL_SEC:
        return None
    online = bool(state.get("online"))
    activity_seconds = 0
    if online and state.get("online_since"):
        activity_seconds = int(now - state["online_since"])
    return {
        "id": cid,
        "online": online,
        "activity_seconds": activity_seconds,
        "last_check": state["last_check"],
    }


def get_camera_status(
    url: str,
    *,
    force_probe: bool = False,
    camera_id: str | None = None,
    camera: dict | None = None,
) -> dict:
    cid = str(camera_id or "").strip() or camera_id_from_url(url.strip())
    if not force_probe:
        cached = _cached_camera_status(cid)
        if cached:
            return cached

    online = probe_camera_online(url, camera=camera)
    status = _update_runtime_status(cid, online)
    status["id"] = cid
    return status


def _probe_cameras_parallel(pending: list[tuple[int, dict]]) -> dict[int, dict]:
    if not pending:
        return {}
    statuses: dict[int, dict] = {}
    workers = min(PROBE_MAX_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(probe_camera_online, str(cam.get("url") or ""), cam): (index, cam)
            for index, cam in pending
        }
        for future in as_completed(future_map):
            index, cam = future_map[future]
            cid = stable_camera_id(cam)
            try:
                online = future.result()
            except Exception:
                online = False
            status = _update_runtime_status(cid, online)
            status["id"] = cid
            statuses[index] = status
    return statuses


def enrich_camera_items(
    items: List[dict],
    frames_dir: str,
    *,
    probe_online: bool = True,
    with_inference: bool = True,
) -> List[dict]:
    pending: list[tuple[int, dict]] = []
    status_by_index: dict[int, dict] = {}

    for index, item in enumerate(items):
        url = item.get("url") or ""
        cid = stable_camera_id(item)
        cached = _cached_camera_status(cid)
        if cached:
            status_by_index[index] = cached
        elif probe_online and url:
            pending.append((index, item))
        else:
            status_by_index[index] = {
                "id": cid,
                "online": False,
                "activity_seconds": 0,
                "last_check": time.time(),
            }

    if probe_online:
        status_by_index.update(_probe_cameras_parallel(pending))

    result = []
    for index, item in enumerate(items):
        cid = stable_camera_id(item)
        status = status_by_index.get(index) or {
            "online": False,
            "activity_seconds": 0,
        }
        thumb_path = camera_thumbnail_path(frames_dir, cid)
        last_frame_at = os.path.getmtime(thumb_path) if os.path.exists(thumb_path) else None
        result.append(
            {
                **item,
                "id": cid,
                "online": status["online"],
                "activity_seconds": status["activity_seconds"],
                "last_frame_at": last_frame_at,
                "has_thumbnail": os.path.exists(thumb_path),
                "mediamtx_managed": item.get("source_type") in ("v4l2", "rtsp_pull", "publisher"),
            }
        )
    if with_inference:
        from services.inference_container_service import attach_inference_status

        return attach_inference_status(result)
    return result


def list_cameras_with_status(
    camera_ips_file: str,
    frames_dir: str,
    with_inference: bool = True,
    *,
    probe_online: bool = True,
) -> List[dict]:
    from services.camera_store import load_cameras

    items = load_cameras(camera_ips_file)
    return enrich_camera_items(
        items,
        frames_dir,
        probe_online=probe_online,
        with_inference=with_inference,
    )


def resolve_camera_id_for_url(camera_ips_file: str, raw_url: str) -> str:
    from services.camera_store import load_cameras

    for rec in load_cameras(camera_ips_file):
        if rec.get("url") == raw_url:
            return stable_camera_id(rec)
    return camera_id_from_url(raw_url)


def capture_camera_frame(
    *,
    image: str = "",
    url: str = "",
    frames_dir: str,
    last_frame_file: str = "",
    camera_ips_file: str = "",
    camera_id: str = "",
):
    """保存缩略图：优先使用浏览器上传的 JPEG base64（B1），不再在 UI 侧拉 RTSP 解码。"""
    cid = str(camera_id or "").strip()
    if not cid and camera_ips_file and url:
        cid = resolve_camera_id_for_url(camera_ips_file, url)
    if not cid and url:
        cid = camera_id_from_url(url)

    if not str(image or "").strip():
        return {
            "error": "请从监控页预览（HLS/WebRTC）抓帧后上传，或使用图片文件",
        }

    try:
        jpeg_bytes = _decode_jpeg_payload(image)
    except ValueError as exc:
        return {"error": str(exc)}

    if not cid:
        return {"error": "camera_id is required"}

    thumb_path = save_thumbnail_bytes(
        cid,
        jpeg_bytes,
        frames_dir=frames_dir,
        last_frame_file=last_frame_file,
    )
    cam_record = None
    if camera_ips_file:
        from services.camera_store import load_cameras

        for rec in load_cameras(camera_ips_file):
            if stable_camera_id(rec) == cid:
                cam_record = rec
                break

    online = probe_camera_online(str(url or (cam_record or {}).get("url") or ""), camera=cam_record)
    status = _update_runtime_status(cid, online)

    return {
        "status": "success",
        "camera_id": cid,
        "image": base64.b64encode(jpeg_bytes).decode("utf-8"),
        "last_frame_at": os.path.getmtime(thumb_path),
        "online": status["online"],
        "activity_seconds": status["activity_seconds"],
    }


def get_last_frame_b64(last_frame_file: str):
    if not os.path.exists(last_frame_file):
        return {"error": "last frame not found"}
    with open(last_frame_file, "rb") as f:
        data = f.read()
    if not data:
        return {"error": "failed to read last frame"}
    return {"status": "success", "image": base64.b64encode(data).decode("utf-8")}

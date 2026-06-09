"""摄像头地址管理、在线状态与抓帧服务。"""

import base64
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

import cv2

from services.video_service import read_non_black_frame, resize_frame_to_height

RTSP_HOST_REWRITE = os.environ.get("RTSP_HOST_REWRITE", "")
PROBE_TTL_SEC = max(5, int(os.environ.get("CAMERA_PROBE_TTL", "20")))
PROBE_MAX_READS = max(1, int(os.environ.get("CAMERA_PROBE_MAX_READS", "2")))
PROBE_MAX_WORKERS = max(1, int(os.environ.get("CAMERA_PROBE_MAX_WORKERS", "8")))
_FFMPEG_CAPTURE_OPTS = os.environ.get(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;3000000|max_delay;500000",
)

_camera_runtime: dict = {}


def normalize_rtsp_url(url: str) -> str:
    """宿主机配置多为 rtsp://127.0.0.1:8554/…；compose 内 UI/推理应连 mediamtx 服务名。"""
    rewritten = str(url or "")
    if RTSP_HOST_REWRITE:
        if "127.0.0.1" in rewritten:
            rewritten = rewritten.replace("127.0.0.1", RTSP_HOST_REWRITE)
        if "localhost" in rewritten:
            rewritten = rewritten.replace("localhost", RTSP_HOST_REWRITE)
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


def stable_camera_id(record: dict) -> str:
    return str(record.get("id") or record.get("path") or camera_id_from_url(record.get("url", "")))


def load_camera_ips(camera_ips_file: str) -> List[dict]:
    from services.camera_store import load_cameras

    return [{"name": c["name"], "url": c["url"]} for c in load_cameras(camera_ips_file)]


def save_camera_ips(camera_ips_file: str, items: List[dict]):
    from services.camera_store import save_camera_ips as _save

    _save(camera_ips_file, items)


def frame_to_base64(frame) -> str | None:
    ok, encoded = cv2.imencode(".jpg", frame)
    if not ok:
        return None
    return base64.b64encode(encoded.tobytes()).decode("utf-8")


def save_last_frame(last_frame_file: str, frame):
    if not last_frame_file:
        return
    os.makedirs(os.path.dirname(last_frame_file) or ".", exist_ok=True)
    cv2.imwrite(last_frame_file, frame)


def probe_camera_online(url: str, max_reads: int | None = None) -> bool:
    _ = max_reads
    from services.rtsp_capture import read_rtsp_frame_once

    stream_url = normalize_rtsp_url(url)
    frame = read_rtsp_frame_once(stream_url, timeout_sec=6.0)
    return frame is not None


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


def get_camera_status(url: str, *, force_probe: bool = False, camera_id: str | None = None) -> dict:
    cid = str(camera_id or "").strip() or camera_id_from_url(url.strip())
    if not force_probe:
        cached = _cached_camera_status(cid)
        if cached:
            return cached

    online = probe_camera_online(url)
    status = _update_runtime_status(cid, online)
    status["id"] = cid
    return status


def _probe_cameras_parallel(pending: list[tuple[int, str, str]]) -> dict[int, dict]:
    if not pending:
        return {}
    statuses: dict[int, dict] = {}
    workers = min(PROBE_MAX_WORKERS, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(probe_camera_online, url): (index, cid, url) for index, url, cid in pending
        }
        for future in as_completed(future_map):
            index, cid, url = future_map[future]
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
    pending: list[tuple[int, str, str]] = []
    status_by_index: dict[int, dict] = {}

    for index, item in enumerate(items):
        url = item.get("url") or ""
        cid = stable_camera_id(item)
        cached = _cached_camera_status(cid)
        if cached:
            status_by_index[index] = cached
        elif probe_online and url:
            pending.append((index, url, cid))
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
                "mediamtx_managed": item.get("source_type") in ("rtsp_pull", "publisher"),
            }
        )
    if with_inference:
        from services.inference_container_service import attach_inference_status

        result = attach_inference_status(result)

    from services.camera_modes import is_edge_camera
    from services.edge_status import build_edge_runtime

    enriched = []
    for item in result:
        if is_edge_camera(item):
            edge_runtime = build_edge_runtime(str(item.get("id") or ""))
            inference = dict(item.get("inference") or {})
            inference["message"] = edge_runtime.get("display") or inference.get("message") or ""
            enriched.append({**item, "edge_runtime": edge_runtime, "inference": inference})
        else:
            enriched.append(item)
    return enriched


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

    raw = str(raw_url or "").strip()
    norm = normalize_rtsp_url(raw)
    for rec in load_cameras(camera_ips_file):
        u = str(rec.get("url") or "").strip()
        if u == raw or u == norm or normalize_rtsp_url(u) == norm:
            return stable_camera_id(rec)
    return camera_id_from_url(raw)


def capture_camera_frame(
    url: str,
    capture_height: int,
    frames_dir: str,
    last_frame_file: str = "",
    camera_ips_file: str = "",
):
    raw_url = str(url).strip()
    if not raw_url:
        return {"error": "url is required"}

    camera_id = (
        resolve_camera_id_for_url(camera_ips_file, raw_url)
        if camera_ips_file
        else camera_id_from_url(raw_url)
    )
    stream_url = normalize_rtsp_url(raw_url)
    from services.rtsp_capture import read_rtsp_frame_once

    frame = None
    for attempt in range(3):
        frame = read_rtsp_frame_once(stream_url, timeout_sec=10.0)
        if frame is not None:
            break
        if attempt < 2:
            time.sleep(0.35)
    if frame is None:
        _update_runtime_status(camera_id, False)
        return {"error": "failed to read frame from camera"}

    frame = resize_frame_to_height(frame, capture_height)
    thumb_path = camera_thumbnail_path(frames_dir, camera_id)
    cv2.imwrite(thumb_path, frame)
    save_last_frame(last_frame_file, frame)
    _update_runtime_status(camera_id, True)

    image_b64 = frame_to_base64(frame)
    if image_b64 is None:
        return {"error": "failed to encode frame"}

    runtime = _camera_runtime.get(camera_id, {})
    online_since = runtime.get("online_since")
    activity_seconds = int(time.time() - online_since) if online_since else 0

    return {
        "status": "success",
        "image": image_b64,
        "camera_id": camera_id,
        "last_frame_at": os.path.getmtime(thumb_path),
        "online": True,
        "activity_seconds": activity_seconds,
    }


def get_last_frame_b64(last_frame_file: str):
    if not os.path.exists(last_frame_file):
        return {"error": "last frame not found"}

    frame = cv2.imread(last_frame_file)
    if frame is None:
        return {"error": "failed to read last frame"}

    image_b64 = frame_to_base64(frame)
    if image_b64 is None:
        return {"error": "failed to encode frame"}

    return {"status": "success", "image": image_b64}


def get_camera_thumbnail_path(frames_dir: str, camera_id: str) -> str | None:
    path = camera_thumbnail_path(frames_dir, camera_id)
    if os.path.exists(path):
        return path
    return None

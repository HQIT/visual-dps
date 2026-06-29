"""摄像头与 MediaMTX 相关 API 路由注册。"""

import json
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from services.live_bus import get_snapshot, live_hub

from services.audit_service import audit_from_result
from services.camera_service import (
    capture_camera_frame,
    get_camera_status,
    get_camera_thumbnail_path,
    list_cameras_with_status,
    stable_camera_id,
)
from services.camera_stream_service import iter_mjpeg, mjpeg_media_type, stream_recently_active
from services.camera_store import (
    apply_mediamtx,
    create_camera,
    delete_camera,
    get_camera,
    load_camera_ips,
    load_cameras,
    update_camera,
)
from services.inference_container_service import (
    batch_start_inference_containers,
    batch_stop_inference_containers,
    get_inference_status,
    start_inference_container,
    stop_inference_container,
)
from services.mediamtx_proxy import proxy_hls, proxy_whep
from services.mediamtx_service import (
    build_camera_playback_urls,
    generate_mediamtx_yaml,
    is_mediamtx_managed,
    is_mediamtx_playback_available,
)
from services.stream_prefs import STREAM_HEIGHT_CHOICES, clamp_stream_height
from core.config import try_load_app_config
from services.annotation_service import (
    annotation_payload_for_api,
    load_camera_annotation,
    materialize_camera_annotation,
    save_camera_annotation,
)
from services.runtime_config_service import get_camera_settings_payload
from services.matrix_service import build_matrix_overview
from services.topology_service import build_topology_overview


def register_camera_routes(
    router: APIRouter,
    *,
    camera_ips_file: str,
    frames_dir: str,
    mediamtx_config_path: str,
    json_dir: str = "localdata/json",
    default_json_file: str = "localdata/json/precise_boxes_new.json",
    last_frame_file: str = "",
    capture_height: int = 480,
):
    @router.get("/cameras")
    async def list_cameras(probe: bool = True):
        items = list_cameras_with_status(
            camera_ips_file,
            frames_dir,
            probe_online=probe,
        )
        return {"status": "success", "items": items}

    @router.get("/matrix/overview")
    async def matrix_overview():
        return build_matrix_overview(
            camera_ips_file,
            json_dir,
            default_json_file,
            frames_dir=frames_dir,
        )

    @router.get("/topology/overview")
    async def topology_overview(probe: bool = False):
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: build_topology_overview(
                camera_ips_file,
                json_dir,
                default_json_file,
                probe=probe,
            ),
        )

    def _attach_list_items(result: dict, *, probe: bool = False) -> dict:
        if result.get("status") == "success":
            result["items"] = list_cameras_with_status(
                camera_ips_file,
                frames_dir,
                probe_online=probe,
            )
        return result

    @router.get("/cameras/{camera_id}")
    async def read_camera(camera_id: str, probe: bool = False, settings: bool = True):
        found = get_camera(camera_ips_file, camera_id)
        if found.get("error"):
            return found
        cam = dict(found["camera"])
        url = str(cam.get("url") or "").strip()
        cid = stable_camera_id(cam)
        if url:
            st = get_camera_status(url, force_probe=probe, camera_id=cid)
            cam["online"] = st["online"]
            cam["activity_seconds"] = st.get("activity_seconds", 0)
        else:
            cam["online"] = False
            cam["activity_seconds"] = 0
        thumb_path = get_camera_thumbnail_path(frames_dir, cid)
        cam["last_frame_at"] = os.path.getmtime(thumb_path) if thumb_path else None
        if not cam["online"]:
            if stream_recently_active(cid):
                cam["online"] = True
            elif cam["last_frame_at"] and (time.time() - cam["last_frame_at"]) < 120:
                cam["online"] = True
        cam["has_thumbnail"] = thumb_path is not None
        from services.inference_container_service import attach_inference_status

        enriched = attach_inference_status([cam])
        cam_out = enriched[0]
        if settings:
            settings_payload = get_camera_settings_payload(try_load_app_config(), cam_out)
            cam_out["settings"] = settings_payload["settings"]
            cam_out["effective_settings"] = settings_payload["effective_settings"]
            cam_out["global_defaults"] = settings_payload["global_defaults"]
        return {"status": "success", "camera": cam_out}

    @router.post("/cameras")
    async def create_camera_api(data: dict, request: Request):
        result = create_camera(camera_ips_file, mediamtx_config_path, data)
        if result.get("status") == "success" and result.get("camera"):
            cam = result["camera"]
            materialize_camera_annotation(cam.get("id") or cam.get("path"), json_dir, camera=cam)
        audit_from_result(request, "camera.create", "camera", data.get("path") or data.get("id", ""), result)
        return _attach_list_items(result, probe=False)

    @router.put("/cameras/{camera_id}")
    async def update_camera_api(camera_id: str, data: dict, request: Request):
        result = update_camera(camera_ips_file, mediamtx_config_path, camera_id, data)
        audit_from_result(request, "camera.update", "camera", camera_id, result)
        return _attach_list_items(result, probe=False)

    @router.delete("/cameras/{camera_id}")
    async def delete_camera_api(camera_id: str, request: Request):
        result = delete_camera(camera_ips_file, mediamtx_config_path, camera_id)
        audit_from_result(request, "camera.delete", "camera", camera_id, result)
        return _attach_list_items(result, probe=False)

    @router.get("/cameras/{camera_id}/annotation")
    async def read_camera_annotation(camera_id: str):
        found = get_camera(camera_ips_file, camera_id)
        if found.get("error"):
            return found
        result = load_camera_annotation(
            camera_id,
            json_dir,
            default_json_file,
            camera=found["camera"],
        )
        if result.get("error") == "annotation not found":
            materialize_camera_annotation(camera_id, json_dir, camera=found["camera"])
            result = load_camera_annotation(
                camera_id,
                json_dir,
                default_json_file,
                camera=found["camera"],
            )
        if result.get("status") == "success":
            from core.state import STATE

            STATE.json_path = str(result.get("json_path") or "")
        return annotation_payload_for_api(result)

    @router.post("/cameras/{camera_id}/annotation")
    async def write_camera_annotation(camera_id: str, data: dict, request: Request):
        found = get_camera(camera_ips_file, camera_id)
        if found.get("error"):
            audit_from_result(request, "annotation.save", "camera", camera_id, found)
            return found
        result = save_camera_annotation(data, camera_id, json_dir)
        audit_from_result(request, "annotation.save", "camera", camera_id, result)
        return result

    @router.get("/cameras/{camera_id}/inference")
    async def read_camera_inference(camera_id: str):
        return {"status": "success", "inference": get_inference_status(camera_id)}

    @router.get("/cameras/{camera_id}/inference/live")
    async def read_camera_inference_live(camera_id: str):
        snap = get_snapshot(camera_id)
        if snap:
            return {
                "status": "success",
                "deprecated": True,
                "hint": "请使用 GET /api/cameras/{id}/live/stream (SSE)",
                "collisions": snap.get("collisions") or [],
                "alarm_collisions": snap.get("alarm_collisions") or [],
                "skeletons": snap.get("skeletons") or [],
                "infer_width": int(snap.get("infer_width") or 0),
                "infer_height": int(snap.get("infer_height") or 0),
            }
        return {
            "status": "success",
            "deprecated": True,
            "collisions": [],
            "alarm_collisions": [],
            "skeletons": [],
            "infer_width": 0,
            "infer_height": 0,
        }

    @router.get("/cameras/{camera_id}/live/stream")
    async def camera_live_stream(camera_id: str):
        async def event_generator():
            async for chunk in live_hub.subscribe_sse(camera_id):
                yield chunk

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/cameras/{camera_id}/inference/start")
    async def start_camera_inference(camera_id: str, request: Request):
        found = get_camera(camera_ips_file, camera_id)
        if found.get("error"):
            audit_from_result(request, "inference.start", "camera", camera_id, found)
            return found
        result = start_inference_container(found["camera"], request=request)
        audit_from_result(request, "inference.start", "camera", camera_id, result)
        return result

    @router.post("/cameras/{camera_id}/inference/stop")
    async def stop_camera_inference(camera_id: str, request: Request):
        result = stop_inference_container(camera_id, request=request)
        audit_from_result(request, "inference.stop", "camera", camera_id, result)
        return result

    @router.post("/inference/start-all")
    async def start_all_inference(request: Request):
        result = batch_start_inference_containers(camera_ips_file, request=request)
        audit_from_result(request, "inference.start_all", "system", "", result)
        return result

    @router.post("/inference/stop-all")
    async def stop_all_inference(request: Request):
        result = batch_stop_inference_containers(camera_ips_file, request=request)
        audit_from_result(request, "inference.stop_all", "system", "", result)
        return result

    @router.post("/mediamtx/apply")
    async def mediamtx_apply(request: Request):
        result = apply_mediamtx(camera_ips_file, mediamtx_config_path)
        audit_from_result(request, "camera.stream_apply", "system", "", result)
        return result

    @router.get("/mediamtx/config")
    async def mediamtx_config_preview():
        items = load_cameras(camera_ips_file)
        return {
            "status": "success",
            "config_path": mediamtx_config_path,
            "yaml": generate_mediamtx_yaml(items),
            "items": items,
        }

    @router.get("/cameras/{camera_id}/playback")
    async def camera_playback(camera_id: str):
        found = get_camera(camera_ips_file, camera_id)
        if found.get("error"):
            return JSONResponse(status_code=404, content=found)
        cam = found["camera"]
        path = str(cam.get("path") or cam.get("id") or "").strip()
        managed = is_mediamtx_managed(cam)
        playback_ok = is_mediamtx_playback_available(cam)
        urls = build_camera_playback_urls(camera_id, path) if playback_ok else {}
        return {
            "status": "success",
            "camera_id": camera_id,
            "path": path,
            "mediamtx_managed": managed,
            "mediamtx_playback": playback_ok,
            "heights": list(STREAM_HEIGHT_CHOICES),
            "formats": {
                "mjpeg": {"available": True},
                "hls": {"available": bool(urls.get("hls")), "url": urls.get("hls") or ""},
                "webrtc": {"available": bool(urls.get("whep")), "url": urls.get("whep") or ""},
            },
        }

    @router.post("/cameras/{camera_id}/whep")
    async def camera_whep_proxy(camera_id: str, request: Request):
        found = get_camera(camera_ips_file, camera_id)
        if found.get("error"):
            return JSONResponse(status_code=404, content=found)
        cam = found["camera"]
        if not is_mediamtx_playback_available(cam):
            return JSONResponse(
                status_code=400,
                content={"error": "该摄像头未由 MediaMTX 托管，无法使用 WebRTC"},
            )
        path = str(cam.get("path") or cam.get("id") or "").strip()
        return await proxy_whep(path, request)

    @router.get("/cameras/{camera_id}/hls/{subpath:path}")
    async def camera_hls_proxy(camera_id: str, subpath: str, request: Request):
        found = get_camera(camera_ips_file, camera_id)
        if found.get("error"):
            return JSONResponse(status_code=404, content=found)
        cam = found["camera"]
        if not is_mediamtx_playback_available(cam):
            return JSONResponse(
                status_code=400,
                content={"error": "该摄像头未由 MediaMTX 托管，无法使用 HLS"},
            )
        path = str(cam.get("path") or cam.get("id") or "").strip()
        return await proxy_hls(camera_id, path, subpath, request)

    @router.get("/cameras/{camera_id}/stream")
    async def camera_mjpeg_stream(camera_id: str, height: int | None = None):
        found = get_camera(camera_ips_file, camera_id)
        if found.get("error"):
            return JSONResponse(status_code=404, content=found)
        url = str(found["camera"].get("url") or "").strip()
        if not url:
            return JSONResponse(status_code=400, content={"error": "请填写视频流地址"})
        stream_height = clamp_stream_height(height if height is not None else capture_height)
        return StreamingResponse(
            iter_mjpeg(camera_id, url, stream_height),
            media_type=mjpeg_media_type(),
        )

    @router.get("/cameras/{camera_id}/thumbnail")
    async def camera_thumbnail(camera_id: str):
        thumb_path = get_camera_thumbnail_path(frames_dir, camera_id)
        if not thumb_path:
            return JSONResponse(status_code=404, content={"error": "thumbnail not found"})
        return FileResponse(thumb_path, media_type="image/jpeg")

    @router.get("/camera_ips")
    async def get_camera_ips():
        items = load_camera_ips(camera_ips_file)
        return {"status": "success", "items": items}

    @router.post("/camera_ips")
    async def add_camera_ip(data: dict, request: Request):
        url = str(data.get("url", "")).strip()
        name = str(data.get("name", "")).strip()
        if not url:
            return {"error": "url is required"}
        result = create_camera(
            camera_ips_file,
            mediamtx_config_path,
            {"name": name or url, "url": url, "source_type": "external"},
        )
        audit_from_result(request, "camera.create", "camera", url, result)
        return result

    @router.delete("/camera_ips")
    async def delete_camera_ip(data: dict, request: Request):
        from services.camera_store import load_cameras as _load
        from services.mediamtx_service import path_from_url

        url = str(data.get("url", "")).strip()
        if not url:
            return {"error": "url is required"}
        cid = path_from_url(url)
        for c in _load(camera_ips_file):
            if c.get("url") == url:
                cid = c["id"]
                break
        result = delete_camera(camera_ips_file, mediamtx_config_path, cid)
        audit_from_result(request, "camera.delete", "camera", cid, result)
        return result

    @router.post("/cameras/{camera_id}/capture")
    async def camera_capture_frame(camera_id: str):
        import asyncio

        found = get_camera(camera_ips_file, camera_id)
        if found.get("error"):
            return JSONResponse(status_code=404, content=found)
        url = str(found["camera"].get("url") or "").strip()
        if not url:
            return JSONResponse(status_code=400, content={"error": "请填写视频流地址"})
        return await asyncio.to_thread(
            capture_camera_frame,
            url=url,
            capture_height=capture_height,
            frames_dir=frames_dir,
            last_frame_file=last_frame_file,
            camera_ips_file=camera_ips_file,
        )

    @router.post("/get_camera_frame")
    async def get_camera_frame(data: dict, request: Request):
        import asyncio

        url = str(data.get("url", "")).strip()
        if not url:
            return {"error": "url is required"}
        return await asyncio.to_thread(
            capture_camera_frame,
            url=url,
            capture_height=capture_height,
            frames_dir=frames_dir,
            last_frame_file=last_frame_file,
            camera_ips_file=camera_ips_file,
        )

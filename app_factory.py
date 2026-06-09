"""FastAPI 应用工厂与路由绑定模块。"""

import json
import os

from fastapi import APIRouter, FastAPI, File, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse

from core.auth_middleware import AuthMiddleware
from core.auth_settings import load_auth_settings
from core.config import load_app_config
from core.spa_static import mount_spa, register_spa_fallback
from services.admin_routes import register_admin_routes
from services.auth_routes import register_auth_routes
from services.auth_service import ensure_users_file
from services.log_store import init_log_db
from core.state import STATE
from services.annotation_service import flatten_annotation_boxes, load_annotation, save_annotation
from services.callback_reporter import CollisionCallbackReporter
from services.camera_routes import register_camera_routes
from services.edge_routes import register_edge_routes
from services.camera_service import get_last_frame_b64
from services.camera_store import load_cameras
from services.inference_service import InferenceService
from services.live_bus import live_hub
from services.version_routes import register_version_routes
from services.video_service import get_first_frame_b64, handle_video_upload, initialize_source_from_config


def create_app():
    app_config = load_app_config()
    paths = app_config["paths"]
    video_cfg = app_config["video"]

    initialize_source_from_config(
        app_config=app_config,
        json_dir=paths["json_dir"],
        default_json_file=paths["default_json_file"],
    )
    callback_reporter = CollisionCallbackReporter(app_config.get("reporting", {}))
    inference_service = InferenceService(app_config, STATE)
    app = FastAPI()
    api_router = APIRouter(prefix="/api")
    auth_settings = load_auth_settings(app_config)
    register_auth_routes(api_router, app_config)
    register_version_routes(api_router)
    register_admin_routes(api_router, app_config)
    app.add_middleware(AuthMiddleware, lambda: auth_settings)

    @app.on_event("startup")
    async def startup_event():
        init_log_db()
        if auth_settings["enabled"] and auth_settings["local"]["enabled"]:
            ensure_users_file(auth_settings["local"]["users_file"])
        await callback_reporter.start()
        await live_hub.start()
        if STATE.source_type == "stream" and STATE.video_path:
            await inference_service.start_inference()

    @app.on_event("shutdown")
    async def shutdown_event():
        await live_hub.stop()
        await callback_reporter.stop()

    frames_dir = os.path.join(paths["base_localdata_dir"], "frames")
    mediamtx_config_path = os.environ.get(
        "MEDIAMTX_CONFIG_PATH",
        os.path.join(paths["base_localdata_dir"], "mediamtx.yml"),
    )
    frontend_dist = os.environ.get("FRONTEND_DIST", os.path.join("web", "dist"))
    spa_enabled = os.path.isfile(os.path.join(frontend_dist, "index.html"))
    if spa_enabled:
        mount_spa(app, frontend_dist)
    else:
        dashboard_html = os.path.join(paths["templates_dir"], "dashboard.html")

        @app.get("/")
        async def read_dashboard():
            return FileResponse(dashboard_html)

        @app.get("/monitor")
        async def read_monitor():
            return FileResponse(paths["index_html"])

        @app.get("/annotate")
        async def read_annotate():
            return FileResponse(paths["annotation_html"])

    @api_router.post("/upload_video")
    async def upload_video(file: UploadFile = File(...)):
        return await handle_video_upload(
            file=file,
            upload_dir=paths["upload_dir"],
            upload_480p_dir=paths["upload_480p_dir"],
            json_dir=paths["json_dir"],
            counter_file=paths["counter_file"],
            base_localdata_dir=paths["base_localdata_dir"],
            transcode_height=int(video_cfg["transcode_height"]),
        )

    @api_router.get("/get_first_frame")
    async def get_first_frame():
        return get_first_frame_b64(
            STATE.video_path,
            capture_height=int(video_cfg["capture_height"]),
        )

    @api_router.get("/runtime_state")
    async def runtime_state():
        debug_cfg = app_config.get("debug-info", {})
        debug_visual_enabled = isinstance(debug_cfg, dict) and bool(debug_cfg.get("enabled", False))
        return {
            "status": "success",
            "source_type": STATE.source_type,
            "source_url": STATE.source_url,
            "is_inferencing": STATE.is_inferencing,
            "json_path": STATE.json_path,
            "debug_visual_enabled": debug_visual_enabled,
        }

    register_camera_routes(
        api_router,
        camera_ips_file=paths["camera_ips_file"],
        frames_dir=frames_dir,
        mediamtx_config_path=mediamtx_config_path,
        json_dir=paths["json_dir"],
        default_json_file=paths["default_json_file"],
        last_frame_file=paths["last_frame_file"],
        capture_height=int(video_cfg["capture_height"]),
    )

    register_edge_routes(
        api_router,
        camera_ips_file=paths["camera_ips_file"],
        app_config=app_config,
        json_dir=paths["json_dir"],
        default_json_file=paths["default_json_file"],
        callback_reporter=callback_reporter,
    )

    @api_router.get("/last_frame")
    async def last_frame():
        return get_last_frame_b64(paths["last_frame_file"])

    @api_router.post("/save_annotation")
    async def save_annotation_api(data: dict):
        return save_annotation(data, paths["default_json_file"], paths["json_dir"])

    @api_router.get("/annotation")
    async def get_annotation():
        return load_annotation(paths["default_json_file"], paths["json_dir"])

    @api_router.post("/start_inference")
    async def start_inference():
        return await inference_service.start_inference()

    @api_router.get("/get_current_annotation")
    async def get_current_annotation():
        result = load_annotation(paths["default_json_file"], paths["json_dir"])
        if result.get("status") != "success":
            return {"status": "error", "message": result.get("error", "annotation not found"), "json_path": result.get("json_path")}

        config_data = result["data"]
        boxes = flatten_annotation_boxes(config_data)
        source_info = config_data.get("source_info", {}) if isinstance(config_data, dict) else {}
        if not isinstance(source_info, dict):
            source_info = {}

        return {
            "status": "success",
            "json_path": result["json_path"],
            "boxes": boxes,
            "shelves": config_data.get("shelves", []) if isinstance(config_data, dict) else [],
            "grid_shape": config_data.get("grid_shape", []) if isinstance(config_data, dict) else [],
            "source_info": source_info,
        }

    @api_router.get("/callback_report/{event_id}")
    async def get_callback_report(event_id: str):
        rec = callback_reporter.get_record(event_id)
        if rec is None:
            return {"status": "not_found", "event_id": event_id}
        return {"status": "ok", "record": rec}

    @app.websocket("/ws/inference")
    async def websocket_inference(websocket: WebSocket):
        await inference_service.websocket_inference(websocket)

    app.include_router(api_router)

    if spa_enabled:
        register_spa_fallback(app, frontend_dist)

    return app, app_config

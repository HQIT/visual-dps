"""本地开发服务：React SPA + RTSP 抓帧（不含 AI 推理）。"""

import os

from fastapi import APIRouter, FastAPI

from core.auth_middleware import AuthMiddleware
from core.auth_settings import load_auth_settings
from core.spa_static import mount_spa, register_spa_fallback
from services.admin_routes import register_admin_routes
from services.auth_routes import register_auth_routes
from services.auth_service import ensure_users_file
from services.log_store import init_log_db
from services.annotation_service import flatten_annotation_boxes, load_annotation, save_annotation
from services.callback_reporter import CollisionCallbackReporter
from services.camera_routes import register_camera_routes
from services.camera_store import load_cameras
from services.live_bus import live_hub
from services.mediamtx_service import ensure_mediamtx_config
from core.config import try_load_app_config
from services.edge_routes import register_edge_routes
from services.version_routes import register_version_routes

app = FastAPI(title="visual-dps-dev")
api_router = APIRouter(prefix="/api")

AUTH_SETTINGS = load_auth_settings(None)
register_auth_routes(api_router, None)
register_version_routes(api_router)
register_admin_routes(api_router, None)
app.add_middleware(AuthMiddleware, lambda: AUTH_SETTINGS)


@app.on_event("startup")
async def auth_startup():
    init_log_db()
    if AUTH_SETTINGS["enabled"] and AUTH_SETTINGS["local"]["enabled"]:
        ensure_users_file(AUTH_SETTINGS["local"]["users_file"])
    mtx_fix = ensure_mediamtx_config(
        os.environ.get("MEDIAMTX_CONFIG_PATH", "localdata/mediamtx.yml"),
        load_cameras(os.environ.get("CAMERA_IPS_FILE", "localdata/camera_ips.json")),
    )
    if mtx_fix:
        print(f"ℹ️ {mtx_fix['hint']}", flush=True)
    await _callback_reporter.start()
    await live_hub.start()


@app.on_event("shutdown")
async def auth_shutdown():
    await live_hub.stop()
    await _callback_reporter.stop()

BASE_DIR = os.environ.get("BASE_DIR", ".")
JSON_DIR = os.environ.get("JSON_DIR", "localdata/json")
DEFAULT_JSON = os.environ.get("DEFAULT_JSON", "localdata/json/precise_boxes_new.json")
LAST_FRAME = os.environ.get("LAST_FRAME", "localdata/last_frame.jpg")
CAPTURE_HEIGHT = int(os.environ.get("CAPTURE_HEIGHT", "480"))
CAMERA_IPS_FILE = os.environ.get("CAMERA_IPS_FILE", "localdata/camera_ips.json")
FRAMES_DIR = os.environ.get("FRAMES_DIR", "localdata/frames")
MEDIAMTX_CONFIG_PATH = os.environ.get("MEDIAMTX_CONFIG_PATH", "localdata/mediamtx.yml")
FRONTEND_DIST = os.environ.get("FRONTEND_DIST", os.path.join(BASE_DIR, "web", "dist"))

mount_spa(app, FRONTEND_DIST)

register_camera_routes(
    api_router,
    camera_ips_file=CAMERA_IPS_FILE,
    frames_dir=FRAMES_DIR,
    mediamtx_config_path=MEDIAMTX_CONFIG_PATH,
    json_dir=JSON_DIR,
    default_json_file=DEFAULT_JSON,
    last_frame_file=LAST_FRAME,
    capture_height=CAPTURE_HEIGHT,
)

_app_config = try_load_app_config() or {}
_callback_reporter = CollisionCallbackReporter(_app_config.get("reporting", {}))

register_edge_routes(
    api_router,
    camera_ips_file=CAMERA_IPS_FILE,
    app_config=_app_config,
    json_dir=JSON_DIR,
    default_json_file=DEFAULT_JSON,
    callback_reporter=_callback_reporter,
)


@api_router.get("/last_frame")
async def last_frame():
    from services.camera_service import get_last_frame_b64

    return get_last_frame_b64(LAST_FRAME)


@api_router.post("/save_annotation")
async def save_annotation_api(data: dict):
    return save_annotation(data, DEFAULT_JSON, JSON_DIR)


@api_router.get("/annotation")
async def get_annotation():
    return load_annotation(DEFAULT_JSON, JSON_DIR)


@api_router.get("/get_current_annotation")
async def get_current_annotation():
    result = load_annotation(DEFAULT_JSON, JSON_DIR)
    if result.get("status") != "success":
        return {
            "status": "error",
            "message": result.get("error", "annotation not found"),
            "json_path": result.get("json_path"),
        }
    config = result["data"]
    source_info = config.get("source_info", {}) if isinstance(config, dict) else {}
    return {
        "status": "success",
        "json_path": result.get("json_path"),
        "boxes": flatten_annotation_boxes(config),
        "shelves": config.get("shelves", []) if isinstance(config, dict) else [],
        "grid_shape": config.get("grid_shape", []) if isinstance(config, dict) else [],
        "shelf_corners": config.get("shelf_corners", []) if isinstance(config, dict) else [],
        "annotation_size": config.get("annotation_size") if isinstance(config, dict) else None,
        "source_info": source_info if isinstance(source_info, dict) else {},
    }


@api_router.get("/runtime_state")
async def runtime_state():
    items = load_cameras(CAMERA_IPS_FILE)
    source_url = items[0]["url"] if items else "rtsp://127.0.0.1:8554/cam"
    return {
        "status": "success",
        "source_type": "stream",
        "source_url": source_url,
        "is_inferencing": False,
        "json_path": "",
        "debug_visual_enabled": False,
    }


app.include_router(api_router)

register_spa_fallback(app, FRONTEND_DIST)

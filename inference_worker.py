"""单路摄像头推理 Worker（独立 Docker 容器入口）。"""

import os


def _preload_cuda_dlls_early() -> None:
    """GPU 容器：预加载 cuDNN 供 ORT；LD_LIBRARY_PATH 由 docker run 注入。"""
    if os.environ.get("INFERENCE_USE_GPU", "").strip().lower() not in ("1", "true", "yes"):
        return
    try:
        from services.nvidia_pip_cuda import preload_cudnn_libs

        preload_cudnn_libs()
        import onnxruntime as ort

        if hasattr(ort, "preload_dlls"):
            try:
                ort.preload_dlls(cuda=True, cudnn=True)
            except TypeError:
                ort.preload_dlls()
    except Exception:
        pass


_preload_cuda_dlls_early()

import asyncio
import json
import signal
import time

from core.config import load_app_config
from core.state import STATE
from services.camera_service import normalize_rtsp_url
from services.inference_backends import resolve_backend_name
from services.inference_service import InferenceService


def _apply_inference_env_overrides(app_config: dict) -> None:
    """容器环境变量覆盖推理参数（来自全局默认 + 摄像头个性化）。"""
    mapping = {
        "INFERENCE_FRAME_RATE": ("inference", "frame_rate", int),
        "INFERENCE_HEIGHT": ("inference", "height", int),
        "INFERENCE_POSE_FRAME_INTERVAL": ("inference", "pose_frame_interval", int),
    }
    for env_key, (section, key, typ) in mapping.items():
        raw = os.environ.get(env_key, "").strip()
        if not raw:
            continue
        try:
            app_config.setdefault(section, {})[key] = typ(raw)
        except (TypeError, ValueError):
            pass
    raw_dbg = os.environ.get("INFERENCE_DEBUG_VISUAL", "").strip().lower()
    if raw_dbg in ("0", "1", "true", "false", "yes", "no", "on", "off"):
        app_config.setdefault("debug-info", {})["enabled"] = raw_dbg in ("1", "true", "yes", "on")
    raw_backend = os.environ.get("INFERENCE_BACKEND", "").strip()
    if raw_backend:
        app_config.setdefault("models", {})["backend"] = raw_backend


def _status_path(base_dir: str, camera_id: str) -> str:
    os.makedirs(os.path.join(base_dir, "inference"), exist_ok=True)
    return os.path.join(base_dir, "inference", f"{camera_id}.status.json")


_STATUS_PRESERVE_KEYS = (
    "collisions",
    "alarm_collisions",
    "backend",
    "skeletons",
    "infer_width",
    "infer_height",
)


def write_status(base_dir: str, camera_id: str, state: str, message: str = "", extra: dict | None = None):
    path = _status_path(base_dir, camera_id)
    existing: dict = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = {}

    payload = {
        "camera_id": camera_id,
        "state": state,
        "message": message,
        "updated_at": time.time(),
        "pid": os.getpid(),
    }
    if extra:
        payload.update(extra)
    for key in _STATUS_PRESERVE_KEYS:
        if key not in payload and key in existing:
            payload[key] = existing[key]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


async def _run_worker():
    camera_id = os.environ.get("INFERENCE_CAMERA_ID", "").strip()
    stream_url = normalize_rtsp_url(os.environ.get("INFERENCE_STREAM_URL", "").strip())
    if not camera_id or not stream_url:
        raise SystemExit("INFERENCE_CAMERA_ID and INFERENCE_STREAM_URL are required")

    app_config = load_app_config()
    _apply_inference_env_overrides(app_config)
    backend = resolve_backend_name(app_config)
    base_dir = app_config["paths"]["base_localdata_dir"]
    json_path = os.environ.get("INFERENCE_JSON_PATH", "").strip() or app_config["paths"]["default_json_file"]

    STATE.source_type = "stream"
    STATE.video_path = stream_url
    STATE.source_url = stream_url
    STATE.json_path = json_path
    STATE.is_inferencing = False
    STATE.upload_id = 0
    STATE.upload_tag = f"infer_{camera_id}"

    write_status(
        base_dir,
        camera_id,
        "starting",
        f"正在加载模型（{backend}）…",
        {"stream_url": stream_url, "json_path": json_path, "backend": backend},
    )

    service = InferenceService(app_config, STATE)

    stopping = False

    def _handle_stop(*_args):
        nonlocal stopping
        stopping = True
        STATE.is_inferencing = False
        if service._background_task and not service._background_task.done():
            service._background_task.cancel()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    result = await service.start_inference()
    write_status(
        base_dir,
        camera_id,
        "running",
        f"推理已启动 ({result.get('mode', '')})",
        {"stream_url": stream_url, "started_at": time.time()},
    )

    while not stopping:
        if service._background_task and service._background_task.done():
            exc = service._background_task.exception()
            if exc:
                write_status(base_dir, camera_id, "error", str(exc))
                break
            if not STATE.is_inferencing:
                write_status(base_dir, camera_id, "stopped", "推理任务已结束")
                break
        write_status(
            base_dir,
            camera_id,
            "running",
            "",
            {"stream_url": stream_url, "is_inferencing": STATE.is_inferencing},
        )
        await asyncio.sleep(3)

    if not stopping:
        write_status(base_dir, camera_id, "stopped", "正常退出")
    else:
        write_status(base_dir, camera_id, "stopped", "收到停止信号")


def main():
    try:
        asyncio.run(_run_worker())
    except Exception as exc:
        camera_id = os.environ.get("INFERENCE_CAMERA_ID", "unknown")
        try:
            app_config = load_app_config()
            base_dir = app_config["paths"]["base_localdata_dir"]
        except Exception:
            base_dir = "localdata"
        write_status(base_dir, camera_id, "error", str(exc))
        raise


if __name__ == "__main__":
    main()

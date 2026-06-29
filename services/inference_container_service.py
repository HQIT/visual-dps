"""按摄像头启停独立推理 Docker 容器。"""

import json
import os
import time

from core.config import load_app_config
from services.camera_service import normalize_rtsp_url
from services.inference_backends import resolve_backend_name
from services.inference_backends.model_registry import (
    BACKEND_RTMPOSE_ONNX,
    LITE_BACKEND_FAMILIES,
    resolve_det_variant,
    resolve_model_preset,
)

_LITE_BACKENDS = LITE_BACKEND_FAMILIES
from services.annotation_service import ensure_camera_annotation_file
from services.runtime_config_service import get_effective_settings

INFERENCE_CONTAINER_PREFIX = os.environ.get("INFERENCE_CONTAINER_PREFIX", "visual-dps-infer-")
INFERENCE_IMAGE = os.environ.get("INFERENCE_IMAGE", "").strip()
INFERENCE_LITE_IMAGE = os.environ.get("INFERENCE_LITE_IMAGE", "").strip()
INFERENCE_LITE_GPU_IMAGE = os.environ.get("INFERENCE_LITE_GPU_IMAGE", "").strip()
INFERENCE_LITE_GPU_ONNX_IMAGE = os.environ.get("INFERENCE_LITE_GPU_ONNX_IMAGE", "").strip()
HOST_PROJECT_ROOT = os.environ.get("HOST_PROJECT_ROOT", "").strip()
INFERENCE_JSON_PATH = os.environ.get(
    "INFERENCE_JSON_PATH",
    "localdata/json/precise_boxes_new.json",
)
INFERENCE_STATUS_DIR = os.environ.get("INFERENCE_STATUS_DIR", "localdata/inference")

# infer 镜像内 pip nvidia 库路径（UI 容器未必安装，用固定路径 + fallback）
_INFER_NVIDIA_LIB_GLOB = "/usr/local/lib/python3.10/dist-packages/nvidia/*/lib"
_INFER_NVIDIA_LIB_FALLBACK = (
    "cudnn",
    "cublas",
    "cuda_runtime",
    "cuda_nvrtc",
    "cufft",
    "curand",
    "cusolver",
    "cusparse",
    "nccl",
    "nvtx",
    "cuda_cupti",
)


def _infer_gpu_ld_library_path() -> str:
    import glob

    parts = sorted(glob.glob(_INFER_NVIDIA_LIB_GLOB))
    if not parts:
        base = "/usr/local/lib/python3.10/dist-packages/nvidia"
        parts = [os.path.join(base, sub, "lib") for sub in _INFER_NVIDIA_LIB_FALLBACK]
    parts.extend(["/usr/local/nvidia/lib", "/usr/local/nvidia/lib64"])
    return ":".join(parts)


def resolve_inference_json_rel(camera_id: str, json_dir: str = "localdata/json") -> str:
    default = INFERENCE_JSON_PATH
    if default.startswith("/app/"):
        default = default[len("/app/") :]
    cam_rel = camera_annotation_path(json_dir, camera_id)
    if not cam_rel:
        return default
    if HOST_PROJECT_ROOT:
        host_path = os.path.abspath(os.path.join(HOST_PROJECT_ROOT, cam_rel))
        if os.path.isfile(host_path):
            return cam_rel
    elif os.path.isfile(cam_rel):
        return cam_rel
    return cam_rel


def _docker_client():
    try:
        import docker
    except ImportError as exc:
        raise RuntimeError("未安装 docker SDK，无法管理推理容器") from exc
    try:
        return docker.from_env()
    except Exception as exc:
        raise RuntimeError(f"无法连接 Docker: {exc}") from exc


def container_name(camera_id: str) -> str:
    return f"{INFERENCE_CONTAINER_PREFIX}{camera_id}"


def _status_file(camera_id: str) -> str:
    return os.path.join(INFERENCE_STATUS_DIR, f"{camera_id}.status.json")


def _read_worker_status(camera_id: str) -> dict | None:
    path = _status_file(camera_id)
    if not os.path.exists(path):
        return None
    try:
        return json.loads(open(path, "r", encoding="utf-8").read())
    except (json.JSONDecodeError, OSError):
        return None


def _map_docker_status(
    docker_status: str,
    exit_code: int | None = None,
    state_error: str = "",
) -> str:
    if str(state_error or "").strip():
        return "error"
    status = (docker_status or "").lower()
    if status == "running":
        return "running"
    if status in ("created", "restarting"):
        return "starting"
    if status == "paused":
        return "paused"
    if status == "exited":
        if exit_code not in (None, 0):
            return "error"
        return "stopped"
    if status in ("dead", "removing"):
        return "error"
    return "stopped"


def _inference_containers_by_camera_id() -> dict[str, object]:
    out: dict[str, object] = {}
    try:
        client = _docker_client()
        for container in client.containers.list(
            all=True, filters={"label": "visual-dps.role=inference"}
        ):
            labels = container.labels or {}
            cid = str(labels.get("visual-dps.camera_id") or "").strip()
            if cid:
                out[cid] = container
    except Exception:
        pass
    return out


def _compose_inference_status(
    camera_id: str, container=None, *, fetch_error_logs: bool = False
) -> dict:
    name = container_name(camera_id)
    worker = _read_worker_status(camera_id) or {}
    base = {
        "camera_id": camera_id,
        "container_name": name,
        "status": "stopped",
        "container_id": "",
        "message": worker.get("message", ""),
        "started_at": worker.get("started_at"),
        "updated_at": worker.get("updated_at"),
        "stream_url": worker.get("stream_url", ""),
        "backend": worker.get("backend", ""),
        "rtm_det": worker.get("rtm_det", ""),
    }

    if container is None:
        if worker.get("state") in ("running", "starting"):
            base["status"] = worker.get("state", "stopped")
        return base

    try:
        state = container.attrs.get("State", {}) or {}
        exit_code = state.get("ExitCode")
        state_error = str(state.get("Error") or "").strip()
        mapped = _map_docker_status(container.status, exit_code, state_error)
        base.update(
            {
                "status": mapped,
                "container_id": container.id[:12],
                "docker_status": container.status,
                "exit_code": exit_code,
            }
        )
        if mapped == "running" and worker.get("state") == "running":
            base["message"] = worker.get("message") or "推理运行中"
        elif mapped == "error":
            if state_error:
                base["message"] = state_error[:240]
            elif fetch_error_logs:
                try:
                    tail = container.logs(tail=20).decode("utf-8", errors="replace").strip()
                    if tail:
                        base["message"] = tail.splitlines()[-1][:240]
                except Exception:
                    base["message"] = "检测服务异常退出，请重试"
            else:
                base["message"] = "检测服务异常退出，请重试"
        return base
    except Exception as exc:
        base["status"] = "error"
        base["message"] = str(exc)
        return base


def get_inference_status(camera_id: str) -> dict:
    name = container_name(camera_id)
    try:
        client = _docker_client()
        container = client.containers.get(name)
        container.reload()
        return _compose_inference_status(camera_id, container, fetch_error_logs=True)
    except Exception as exc:
        if "No such container" in str(exc) or "not found" in str(exc).lower():
            return _compose_inference_status(camera_id, None)
        out = _compose_inference_status(camera_id, None)
        out["status"] = "error"
        out["message"] = str(exc)
        return out


def attach_inference_status(cameras: list[dict]) -> list[dict]:
    docker_map = _inference_containers_by_camera_id()
    result = []
    for cam in cameras:
        cid = str(cam.get("id") or "")
        container = docker_map.get(cid) if cid else None
        inference = (
            _compose_inference_status(cid, container)
            if cid
            else {"status": "stopped"}
        )
        result.append({**cam, "inference": inference})
    return result


def _host_bind(rel_path: str, container_subpath: str | None = None, read_only: bool = False) -> str:
    if not HOST_PROJECT_ROOT:
        raise RuntimeError("未配置 HOST_PROJECT_ROOT，无法挂载卷启动推理容器")
    host_path = os.path.abspath(os.path.join(HOST_PROJECT_ROOT, rel_path))
    container_path = container_subpath or f"/app/{rel_path}"
    suffix = ":ro" if read_only else ""
    return f"{host_path}:{container_path}{suffix}"


def _image_exists(client, image: str) -> bool:
    import docker

    if not str(image or "").strip():
        return False
    try:
        client.images.get(image)
        return True
    except docker.errors.ImageNotFound:
        return False


def _first_local_image(client, repo: str) -> str:
    """本地已构建镜像任选一 tag（优先非 latest 的 dated tag）。"""
    import docker

    repo = str(repo or "").strip()
    if not repo:
        return ""
    try:
        found = client.images.list(name=repo)
    except docker.errors.DockerException:
        return ""
    tags: list[str] = []
    for img in found:
        tags.extend(img.tags or [])
    candidates = [t for t in tags if t.startswith(f"{repo}:")]
    if not candidates:
        return ""
    dated = [t for t in candidates if ":latest" not in t]
    return sorted(dated or candidates)[-1]


def _resolve_inference_image(client, backend_family: str) -> tuple[str, str]:
    """返回 (镜像名, 后端族)。GPU 优先 lite-gpu-onnx；否则 lite / lite-gpu。"""
    backend = backend_family
    use_gpu = os.environ.get("INFERENCE_USE_GPU", "0") == "1"
    lite = INFERENCE_LITE_IMAGE or _first_local_image(client, "visual-dps-inference-lite")
    lite_gpu = INFERENCE_LITE_GPU_IMAGE or _first_local_image(client, "visual-dps-inference-lite-gpu")
    lite_gpu_onnx = INFERENCE_LITE_GPU_ONNX_IMAGE or _first_local_image(
        client, "visual-dps-inference-lite-gpu-onnx"
    )

    if backend in _LITE_BACKENDS and use_gpu:
        if _image_exists(client, lite_gpu_onnx):
            return lite_gpu_onnx, backend
        if _image_exists(client, lite_gpu):
            return lite_gpu, backend

    explicit = INFERENCE_IMAGE
    if explicit:
        if _image_exists(client, explicit):
            return explicit, backend
        if _image_exists(client, lite):
            return lite, backend
        return explicit, backend

    if backend in _LITE_BACKENDS:
        if _image_exists(client, lite):
            return lite, backend
        if use_gpu and _image_exists(client, lite_gpu_onnx):
            return lite_gpu_onnx, backend
        if use_gpu and _image_exists(client, lite_gpu):
            return lite_gpu, backend
        return lite or "visual-dps-inference-lite:unknown", backend

    if _image_exists(client, lite):
        return lite, BACKEND_RTMPOSE_ONNX
    if use_gpu and lite_gpu_onnx:
        return lite_gpu_onnx, BACKEND_RTMPOSE_ONNX
    return lite or "visual-dps-inference-lite:unknown", BACKEND_RTMPOSE_ONNX


def _stream_url_for_container(url: str) -> str:
    """将配置里的本机 RTSP 地址改写为 compose 内 mediamtx 服务名。"""
    return normalize_rtsp_url(url)


def start_inference_container(camera: dict, request=None) -> dict:
    import docker

    camera_id = str(camera.get("id") or camera.get("path") or "").strip()
    if not camera_id:
        return {"error": "摄像头信息不完整"}

    stream_url = str(camera.get("url") or "").strip()
    if not stream_url:
        return {"error": "请填写视频流地址"}

    name = container_name(camera_id)
    client = _docker_client()

    try:
        old = client.containers.get(name)
        if old.status == "running":
            return {"status": "success", "inference": get_inference_status(camera_id), "message": "已在运行"}
        old.remove(force=True)
    except docker.errors.NotFound:
        pass

    app_config = load_app_config()
    paths = app_config.get("paths", {})
    json_dir = str(paths.get("json_dir", "localdata/json"))
    default_json = str(paths.get("default_json_file", INFERENCE_JSON_PATH))
    json_rel = ensure_camera_annotation_file(camera_id, json_dir, default_json, camera=camera)
    if not os.path.isfile(json_rel) and HOST_PROJECT_ROOT:
        host_json = os.path.join(HOST_PROJECT_ROOT, json_rel)
        if not os.path.isfile(host_json):
            json_rel = resolve_inference_json_rel(camera_id, json_dir)

    binds = [
        _host_bind("localdata"),
        _host_bind("app_config.json", read_only=True),
    ]
    # 须在宿主机挂载（UI 在容器内时 isfile(HOST_PROJECT_ROOT/...) 恒为 False）
    if HOST_PROJECT_ROOT:
        binds.append(_host_bind("core/config.py", read_only=True))
        for rel in (
            "inference_worker.py",
            "services/hwaccel_probe.py",
            "services/nvidia_pip_cuda.py",
            "services/rtsp_capture.py",
            "services/inference_backends/__init__.py",
            "services/inference_backends/model_registry.py",
            "services/inference_backends/rtmpose_onnx_backend.py",
        ):
            binds.append(_host_bind(rel, read_only=True))
    effective = get_effective_settings(app_config, camera)
    preset = resolve_model_preset(app_config, overrides=effective)
    infer_image, _family = _resolve_inference_image(client, preset.family)
    use_gpu = os.environ.get("INFERENCE_USE_GPU", "0") == "1"
    rtsp_backend = os.environ.get("INFERENCE_RTSP_CAPTURE_BACKEND", "").strip()
    if not rtsp_backend:
        # auto：有 NVDEC（libnvcuvid）时用 ffmpeg CUDA，否则探测回退 opencv
        rtsp_backend = "auto" if use_gpu else "opencv"
    env = {
        "INFERENCE_CAMERA_ID": camera_id,
        "INFERENCE_STREAM_URL": _stream_url_for_container(stream_url),
        "INFERENCE_JSON_PATH": f"/app/{json_rel}",
        "INFERENCE_BACKEND": preset.id,
        "INFERENCE_USE_GPU": "1" if use_gpu else "0",
        "MEDIAMTX_INTERNAL_HOST": os.environ.get("MEDIAMTX_INTERNAL_HOST", "mediamtx"),
        "INFERENCE_FRAME_RATE": str(effective.get("inference.frame_rate", 15)),
        "INFERENCE_HEIGHT": str(effective.get("inference.height", 480)),
        "INFERENCE_POSE_FRAME_INTERVAL": str(effective.get("inference.pose_frame_interval", 3)),
        "INFERENCE_DEBUG_VISUAL": "1" if effective.get("debug-info.enabled") else "0",
        "RTSP_CAPTURE_BACKEND": rtsp_backend,
        "RTSP_FRAME_BUFFER_TTL_SEC": os.environ.get("RTSP_FRAME_BUFFER_TTL_SEC", "1").strip() or "1",
        # 与 event-worker 的 POSE_DELIVERY=stream 对齐（推理 XADD pose:stream）
        "POSE_DELIVERY": os.environ.get("POSE_DELIVERY", "stream").strip() or "stream",
        "POSE_STREAM_KEY": os.environ.get("POSE_STREAM_KEY", "pose:stream"),
        "POSE_STREAM_GROUP": os.environ.get("POSE_STREAM_GROUP", "event-workers"),
        "POSE_STREAM_MAXLEN": os.environ.get("POSE_STREAM_MAXLEN", "2000"),
    }
    tz = os.environ.get("TZ", "Asia/Shanghai").strip() or "Asia/Shanghai"
    env["TZ"] = tz
    if preset.family == BACKEND_RTMPOSE_ONNX:
        env["INFERENCE_RTM_DET"] = resolve_det_variant(app_config, overrides=effective)
    if use_gpu:
        env["LD_LIBRARY_PATH"] = _infer_gpu_ld_library_path()

    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        env["REDIS_URL"] = redis_url
    elif os.environ.get("REDIS_PASSWORD", "").strip():
        redis_host = os.environ.get("REDIS_HOST", "redis").strip() or "redis"
        redis_port = os.environ.get("REDIS_PORT", "6379").strip() or "6379"
        env["REDIS_PASSWORD"] = os.environ["REDIS_PASSWORD"]
        env["REDIS_HOST"] = redis_host
        env["REDIS_PORT"] = redis_port

    import docker

    device_requests = []
    if use_gpu:
        device_requests.append(docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]]))

    run_kwargs: dict = {
        "image": infer_image,
        "name": name,
        "detach": True,
        "environment": env,
        "volumes": binds,
        "extra_hosts": {"host.docker.internal": "host-gateway"},
        "device_requests": device_requests or None,
        "command": ["python", "inference_worker.py"],
        "labels": {"visual-dps.role": "inference", "visual-dps.camera_id": camera_id},
    }
    docker_network = os.environ.get("DOCKER_NETWORK", "").strip()
    if docker_network:
        run_kwargs["network"] = docker_network

    def _run_container(kwargs: dict):
        return client.containers.run(**kwargs)

    def _cleanup_stale(name: str) -> None:
        try:
            stale = client.containers.get(name)
            if stale.status != "running":
                stale.remove(force=True)
        except docker.errors.NotFound:
            pass

    try:
        container = _run_container(run_kwargs)
    except Exception as exc:
        err = str(exc)
        err_lower = err.lower()
        gpu_fail = device_requests and any(
            token in err_lower
            for token in ("gpu", "nvidia", "device driver", "capabilities")
        )
        if gpu_fail:
            _cleanup_stale(name)
            cpu_kwargs = {**run_kwargs, "device_requests": None}
            try:
                container = _run_container(cpu_kwargs)
            except Exception as retry_exc:
                err = str(retry_exc)
            else:
                exc = None
        if exc is not None:
            if isinstance(exc, docker.errors.ImageNotFound) or "No such image" in err:
                return {
                    "error": (
                        "未找到推理 Docker 镜像。本地请先执行: "
                        "./scripts/build-inference-lite-image.sh"
                    )
                }
            if "HOST_PROJECT_ROOT" in err:
                return {"error": "未配置 HOST_PROJECT_ROOT，无法挂载数据目录启动检测"}
            return {"error": f"启动检测失败: {err[:200]}"}

    os.makedirs(INFERENCE_STATUS_DIR, exist_ok=True)
    with open(_status_file(camera_id), "w", encoding="utf-8") as f:
        json.dump(
            {
                "camera_id": camera_id,
                "state": "starting",
                "message": "正在启动",
                "updated_at": time.time(),
                "stream_url": stream_url,
                "backend": preset.id,
                "rtm_det": resolve_det_variant(app_config, overrides=effective),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    from services.event_service import record_event

    record_event(
        "inference.started",
        camera_id=camera_id,
        summary="检测已启动",
        detail={"stream_url": stream_url},
    )
    return {
        "status": "success",
        "container_id": container.id[:12],
        "container_name": name,
        "inference": get_inference_status(camera_id),
    }


def stop_inference_container(camera_id: str, request=None) -> dict:
    import docker

    name = container_name(camera_id)
    client = _docker_client()
    try:
        container = client.containers.get(name)
        container.stop(timeout=15)
        container.remove()
    except docker.errors.NotFound:
        pass
    except Exception as exc:
        return {"error": "停止检测失败，请稍后重试"}

    status_path = _status_file(camera_id)
    if os.path.exists(status_path):
        try:
            data = json.loads(open(status_path, "r", encoding="utf-8").read())
            data["state"] = "stopped"
            data["message"] = "已手动停止"
            data["updated_at"] = time.time()
            with open(status_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    from services.event_service import record_event

    record_event("inference.stopped", camera_id=camera_id, summary="检测已停止")
    return {"status": "success", "inference": get_inference_status(camera_id)}


def batch_start_inference_containers(camera_ips_file: str, request=None) -> dict:
    """批量启动已启用且配置了流地址的摄像头推理容器。"""
    from services.camera_store import load_cameras

    cameras = load_cameras(camera_ips_file)
    items: list[dict] = []
    started = skipped = failed = 0

    for cam in cameras:
        camera_id = str(cam.get("id") or cam.get("path") or "").strip()
        if not camera_id:
            continue
        if not cam.get("enabled", True):
            items.append(
                {"camera_id": camera_id, "status": "skipped", "message": "摄像头已禁用"}
            )
            skipped += 1
            continue
        if not str(cam.get("url") or "").strip():
            items.append(
                {"camera_id": camera_id, "status": "skipped", "message": "未配置视频流地址"}
            )
            skipped += 1
            continue

        result = start_inference_container(cam, request=request)
        if result.get("error"):
            items.append(
                {"camera_id": camera_id, "status": "failed", "message": str(result["error"])}
            )
            failed += 1
        elif str(result.get("message") or "") == "已在运行":
            items.append({"camera_id": camera_id, "status": "skipped", "message": "已在运行"})
            skipped += 1
        else:
            items.append(
                {
                    "camera_id": camera_id,
                    "status": "success",
                    "inference": result.get("inference"),
                }
            )
            started += 1

    return {
        "status": "success",
        "total": len(cameras),
        "started": started,
        "skipped": skipped,
        "failed": failed,
        "items": items,
    }


def batch_stop_inference_containers(camera_ips_file: str, request=None) -> dict:
    """批量停止全部摄像头推理容器。"""
    from services.camera_store import load_cameras

    cameras = load_cameras(camera_ips_file)
    items: list[dict] = []
    stopped = skipped = failed = 0

    for cam in cameras:
        camera_id = str(cam.get("id") or cam.get("path") or "").strip()
        if not camera_id:
            continue
        infer_status = str(get_inference_status(camera_id).get("status") or "stopped")
        if infer_status not in ("running", "starting", "paused", "error"):
            items.append({"camera_id": camera_id, "status": "skipped", "message": "未在运行"})
            skipped += 1
            continue

        result = stop_inference_container(camera_id, request=request)
        if result.get("error"):
            items.append(
                {"camera_id": camera_id, "status": "failed", "message": str(result["error"])}
            )
            failed += 1
        else:
            items.append({"camera_id": camera_id, "status": "success"})
            stopped += 1

    return {
        "status": "success",
        "total": len(cameras),
        "stopped": stopped,
        "skipped": skipped,
        "failed": failed,
        "items": items,
    }



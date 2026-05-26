"""按摄像头启停独立推理 Docker 容器。"""

import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from core.config import load_app_config
from services.camera_service import normalize_rtsp_url
from services.inference_backends import (
    BACKEND_MMPose,
    BACKEND_RTMPOSE_ONNX,
    _LITE_BACKENDS,
    resolve_backend_name,
)
from services.annotation_service import ensure_camera_annotation_file
from services.runtime_config_service import get_effective_settings

INFERENCE_CONTAINER_PREFIX = os.environ.get("INFERENCE_CONTAINER_PREFIX", "visual-dps-infer-")
INFERENCE_IMAGE = os.environ.get("INFERENCE_IMAGE", "visual-dps-inference:latest")
INFERENCE_LITE_IMAGE = os.environ.get("INFERENCE_LITE_IMAGE", "visual-dps-inference-lite:latest")
INFERENCE_LITE_GPU_IMAGE = os.environ.get(
    "INFERENCE_LITE_GPU_IMAGE", "visual-dps-inference-lite-gpu:latest"
)
HOST_PROJECT_ROOT = os.environ.get("HOST_PROJECT_ROOT", "").strip()
INFERENCE_JSON_PATH = os.environ.get(
    "INFERENCE_JSON_PATH",
    "localdata/json/precise_boxes_new.json",
)
INFERENCE_STATUS_DIR = os.environ.get("INFERENCE_STATUS_DIR", "localdata/inference")


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


def _calc_cpu_percent_from_stats(stats: dict) -> float:
    try:
        cpu_stats = stats.get("cpu_stats") or {}
        precpu = stats.get("precpu_stats") or {}
        cpu_usage = float(cpu_stats.get("cpu_usage", {}).get("total_usage", 0))
        pre_cpu = float(precpu.get("cpu_usage", {}).get("total_usage", 0))
        system = float(cpu_stats.get("system_cpu_usage", 0)) - float(
            precpu.get("system_cpu_usage", 0)
        )
        if system <= 0:
            return 0.0
        online_cpus = len(cpu_stats.get("cpu_usage", {}).get("percpu_usage") or []) or 1
        return (cpu_usage - pre_cpu) / system * online_cpus * 100.0
    except Exception:
        return 0.0


_STATS_TIMEOUT_SEC = 1.2
_DOCKER_STATS_CACHE: dict[str, dict] = {}
_DOCKER_STATS_CACHE_AT = 0.0
_DOCKER_STATS_CACHE_TTL = 5.0


def _container_image_label(container) -> str:
    """仅用 attrs，避免访问 container.image 触发已删除镜像的 404。"""
    attrs = getattr(container, "attrs", None) or {}
    if isinstance(attrs, dict):
        cfg_img = (attrs.get("Config") or {}).get("Image")
        if cfg_img:
            return str(cfg_img)
        image_id = str(attrs.get("Image") or "")
        if image_id.startswith("sha256:"):
            return image_id[7:19]
        if image_id:
            return image_id[:12]
    return "—"


def _parse_docker_mem_usage(raw: str) -> tuple[float | None, float | None]:
    """'398.4MiB / 31.24GiB' -> (used_mb, limit_mb)"""
    text = str(raw or "").strip()
    if not text or "/" not in text:
        return None, None

    def _to_mb(part: str) -> float | None:
        part = part.strip()
        m = re.match(r"^([\d.]+)\s*([KMG]i?B)$", part, re.I)
        if not m:
            return None
        val = float(m.group(1))
        unit = m.group(2).upper()
        if unit.startswith("G"):
            return val * 1024
        if unit.startswith("M"):
            return val
        if unit.startswith("K"):
            return val / 1024
        return val / (1024 * 1024)

    used_s, limit_s = text.split("/", 1)
    return _to_mb(used_s), _to_mb(limit_s)


def _stats_row_from_container(container, *, timeout_sec: float = _STATS_TIMEOUT_SEC) -> dict:
    if not container or getattr(container, "status", "") != "running":
        return {}
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(container.stats, stream=False)
            raw = fut.result(timeout=timeout_sec)
        mem = raw.get("memory_stats") or {}
        usage = int(mem.get("usage") or 0)
        limit = int(mem.get("limit") or 0)
        out = {
            "cpu_percent": round(_calc_cpu_percent_from_stats(raw), 1),
            "memory_mb": round(usage / (1024 * 1024), 1),
        }
        if limit:
            out["memory_limit_mb"] = round(limit / (1024 * 1024), 1)
        return out
    except (FuturesTimeoutError, Exception):
        return {}


def _batch_docker_stats_by_name(*, timeout_sec: float = 4.0) -> dict[str, dict]:
    """批量容器 CPU/内存：优先 docker stats CLI，否则并行 docker-py（带超时）。"""
    global _DOCKER_STATS_CACHE, _DOCKER_STATS_CACHE_AT
    now = time.time()
    if _DOCKER_STATS_CACHE and (now - _DOCKER_STATS_CACHE_AT) < _DOCKER_STATS_CACHE_TTL:
        return _DOCKER_STATS_CACHE

    out: dict[str, dict] = {}
    try:
        proc = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                name = parts[0].strip().lstrip("/")
                cpu_raw = parts[1].strip().rstrip("%")
                used_mb, limit_mb = _parse_docker_mem_usage(parts[2])
                row: dict = {}
                try:
                    row["cpu_percent"] = round(float(cpu_raw), 1)
                except ValueError:
                    pass
                if used_mb is not None:
                    row["memory_mb"] = round(used_mb, 1)
                if limit_mb is not None:
                    row["memory_limit_mb"] = round(limit_mb, 1)
                if row:
                    out[name] = row
    except Exception:
        pass

    if not out:
        try:
            client = _docker_client()
            targets = [
                c
                for c in client.containers.list()
                if c.status == "running"
                and (c.name or "").lstrip("/").startswith("visual-dps-")
            ]
            if targets:
                workers = min(8, len(targets))
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(_stats_row_from_container, c): c.name.lstrip("/")
                        for c in targets
                    }
                    for fut in futures:
                        name = futures[fut]
                        try:
                            row = fut.result(timeout=timeout_sec)
                            if row:
                                out[name] = row
                        except Exception:
                            pass
        except Exception:
            pass

    _DOCKER_STATS_CACHE = out
    _DOCKER_STATS_CACHE_AT = now
    return out


def _container_stats_snapshot(
    container, *, timeout_sec: float = _STATS_TIMEOUT_SEC, stats_map: dict | None = None
) -> dict:
    """Docker cgroup 快照（CPU%、内存 MB）；超时则跳过避免 API 挂死。"""
    if not container or getattr(container, "status", "") != "running":
        return {}
    name = (getattr(container, "name", "") or "").lstrip("/")
    if stats_map is not None:
        return dict(stats_map.get(name) or {})
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(container.stats, stream=False)
            raw = fut.result(timeout=timeout_sec)
        mem = raw.get("memory_stats") or {}
        usage = int(mem.get("usage") or 0)
        limit = int(mem.get("limit") or 0)
        out = {
            "cpu_percent": round(_calc_cpu_percent_from_stats(raw), 1),
            "memory_mb": round(usage / (1024 * 1024), 1),
        }
        if limit:
            out["memory_limit_mb"] = round(limit / (1024 * 1024), 1)
        return out
    except (FuturesTimeoutError, Exception):
        return {}


def _metrics_from_worker_status(worker: dict | None) -> dict:
    if not isinstance(worker, dict):
        return {}
    stats = worker.get("stats")
    if not isinstance(stats, dict):
        return {}
    keys = (
        "fps",
        "frame_idx",
        "cpu_percent",
        "memory_mb",
        "gpu_memory_mb",
        "gpu_memory_used_mb",
        "gpu_util_percent",
    )
    return {k: stats[k] for k in keys if stats.get(k) is not None}


def _merge_inference_metrics(
    worker: dict | None, container, *, cgroup_stats: bool = True
) -> dict:
    """进程内 stats + 可选容器 cgroup 统计。"""
    metrics = _metrics_from_worker_status(worker)
    cgroup = _container_stats_snapshot(container) if cgroup_stats else {}
    if cgroup:
        metrics["container_cpu_percent"] = cgroup.get("cpu_percent")
        metrics["container_memory_mb"] = cgroup.get("memory_mb")
        if cgroup.get("memory_limit_mb") is not None:
            metrics["container_memory_limit_mb"] = cgroup["memory_limit_mb"]
    return metrics


def list_compose_stack_containers(
    stats_map: dict[str, dict] | None = None,
) -> list[dict]:
    """列出 visual-dps compose 栈容器及资源占用（不含按路推理 worker）。"""
    rows: list[dict] = []
    if stats_map is None:
        stats_map = _batch_docker_stats_by_name()
    try:
        client = _docker_client()
        for container in client.containers.list(all=True):
            name = (container.name or "").lstrip("/")
            if not name.startswith("visual-dps-"):
                continue
            if (container.labels or {}).get("visual-dps.role") == "inference":
                continue
            state = container.attrs.get("State") or {}
            row = {
                "name": name,
                "status": _map_docker_status(
                    container.status,
                    state.get("ExitCode"),
                    state.get("Error", ""),
                ),
                "image": _container_image_label(container),
            }
            # UI 容器内无 docker CLI；仅用 batch 结果，避免 docker-py stats 阻塞整站 API
            row.update(stats_map.get(name, {}))
            rows.append(row)
    except Exception as exc:
        rows.append({"name": "docker", "status": "error", "message": str(exc)})
    rows.sort(key=lambda r: r.get("name") or "")
    return rows


_INFERENCE_DOCKER_CACHE: dict[str, object] = {}
_INFERENCE_DOCKER_CACHE_AT = 0.0
_INFERENCE_DOCKER_CACHE_TTL = 3.0


def _invalidate_inference_docker_cache() -> None:
    global _INFERENCE_DOCKER_CACHE, _INFERENCE_DOCKER_CACHE_AT
    _INFERENCE_DOCKER_CACHE = {}
    _INFERENCE_DOCKER_CACHE_AT = 0.0


def _inference_containers_by_camera_id() -> dict[str, object]:
    global _INFERENCE_DOCKER_CACHE, _INFERENCE_DOCKER_CACHE_AT
    now = time.time()
    if _INFERENCE_DOCKER_CACHE and (now - _INFERENCE_DOCKER_CACHE_AT) < _INFERENCE_DOCKER_CACHE_TTL:
        return _INFERENCE_DOCKER_CACHE

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
    _INFERENCE_DOCKER_CACHE = out
    _INFERENCE_DOCKER_CACHE_AT = now
    return out


def _compose_inference_status(
    camera_id: str,
    container=None,
    *,
    fetch_error_logs: bool = False,
    cgroup_stats: bool = True,
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
        "metrics": _merge_inference_metrics(worker, container, cgroup_stats=cgroup_stats),
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
        base["metrics"] = _merge_inference_metrics(
            worker, container, cgroup_stats=cgroup_stats
        )
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


def attach_inference_status(cameras: list[dict], *, cgroup_stats: bool = False) -> list[dict]:
    docker_map = _inference_containers_by_camera_id()
    result = []
    for cam in cameras:
        cid = str(cam.get("id") or "")
        container = docker_map.get(cid) if cid else None
        inference = (
            _compose_inference_status(cid, container, cgroup_stats=cgroup_stats)
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

    try:
        client.images.get(image)
        return True
    except docker.errors.ImageNotFound:
        return False


def _resolve_inference_image(client, backend: str) -> tuple[str, str]:
    """返回 (镜像名, 实际后端)。优先配置项，缺失时回退到 lite 镜像。"""
    explicit = os.environ.get("INFERENCE_IMAGE", "").strip()
    if explicit:
        if _image_exists(client, explicit):
            eff = backend
            if (
                backend == BACKEND_MMPose
                and "lite" in explicit.lower()
                and "gpu" not in explicit.lower()
            ):
                eff = BACKEND_RTMPOSE_ONNX
            return explicit, eff
        if _image_exists(client, INFERENCE_LITE_IMAGE):
            fallback = BACKEND_RTMPOSE_ONNX if backend == BACKEND_MMPose else backend
            return INFERENCE_LITE_IMAGE, fallback
        return explicit, backend

    use_gpu = os.environ.get("INFERENCE_USE_GPU", "0") == "1"
    gpu_lite_backends = _LITE_BACKENDS | {BACKEND_MMPose}
    if backend in gpu_lite_backends:
        if use_gpu and _image_exists(client, INFERENCE_LITE_GPU_IMAGE):
            return INFERENCE_LITE_GPU_IMAGE, backend
        if backend in _LITE_BACKENDS and _image_exists(client, INFERENCE_LITE_IMAGE):
            return INFERENCE_LITE_IMAGE, backend
        if use_gpu:
            return INFERENCE_LITE_GPU_IMAGE, backend
        if backend in _LITE_BACKENDS:
            return INFERENCE_LITE_IMAGE, backend

    if _image_exists(client, INFERENCE_IMAGE):
        return INFERENCE_IMAGE, BACKEND_MMPose
    if _image_exists(client, INFERENCE_LITE_IMAGE):
        fallback = BACKEND_RTMPOSE_ONNX if backend == BACKEND_MMPose else backend
        return INFERENCE_LITE_IMAGE, fallback
    return INFERENCE_IMAGE, BACKEND_MMPose


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
        _invalidate_inference_docker_cache()
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
    effective = get_effective_settings(app_config, camera)
    backend = resolve_backend_name(app_config, overrides=effective)
    infer_image, backend = _resolve_inference_image(client, backend)
    env = {
        "INFERENCE_CAMERA_ID": camera_id,
        "INFERENCE_STREAM_URL": _stream_url_for_container(stream_url),
        "INFERENCE_JSON_PATH": f"/app/{json_rel}",
        "INFERENCE_BACKEND": backend,
        "MEDIAMTX_INTERNAL_HOST": os.environ.get("MEDIAMTX_INTERNAL_HOST", "mediamtx"),
        "INFERENCE_FRAME_RATE": str(effective.get("inference.frame_rate", 15)),
        "INFERENCE_HEIGHT": str(effective.get("inference.height", 480)),
        "INFERENCE_POSE_FRAME_INTERVAL": str(effective.get("inference.pose_frame_interval", 3)),
        "INFERENCE_DEBUG_VISUAL": "1" if effective.get("debug-info.enabled") else "0",
        "POSE_DELIVERY": os.environ.get("POSE_DELIVERY", "stream").strip() or "stream",
        "POSE_STREAM_KEY": os.environ.get("POSE_STREAM_KEY", "pose:stream"),
        "POSE_STREAM_GROUP": os.environ.get("POSE_STREAM_GROUP", "event-workers"),
        "POSE_STREAM_MAXLEN": os.environ.get("POSE_STREAM_MAXLEN", "2000"),
    }
    for key in ("RTSP_DECODE_PROFILE", "RTSP_HWACCEL_DEVICE", "FFMPEG_BIN"):
        val = os.environ.get(key, "").strip()
        if val:
            env[key] = val

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
    use_gpu = os.environ.get("INFERENCE_USE_GPU", "0") == "1"
    if use_gpu:
        env["INFERENCE_USE_GPU"] = "1"
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
                "backend": backend,
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
    _invalidate_inference_docker_cache()
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
    _invalidate_inference_docker_cache()
    return {"status": "success", "inference": get_inference_status(camera_id)}



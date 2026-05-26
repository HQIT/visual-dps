"""基础设施服务总览（非货位/碰撞矩阵）。"""

from __future__ import annotations

from typing import Any

from services.camera_store import load_cameras
from services.inference_container_service import (
    _batch_docker_stats_by_name,
    _inference_containers_by_camera_id,
    _metrics_from_worker_status,
    _read_worker_status,
    _map_docker_status,
    container_name,
    list_compose_stack_containers,
)
from services.mediamtx_service import (
    is_mediamtx_playback_available,
    list_mediamtx_paths_summary,
)
from services.pose_bus import redis_url


def _host_metrics() -> dict[str, Any]:
    try:
        import psutil

        vm = psutil.virtual_memory()
        return {
            "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
            "memory_percent": round(vm.percent, 1),
            "memory_used_mb": round(vm.used / (1024 * 1024), 1),
            "memory_total_mb": round(vm.total / (1024 * 1024), 1),
        }
    except Exception:
        return {}


def _ping_redis() -> dict[str, Any]:
    url = redis_url()
    if not url:
        return {"ok": False, "message": "未配置 Redis"}
    try:
        import redis

        client = redis.from_url(url, socket_connect_timeout=2)
        client.ping()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def _mtx_ready_by_path(mtx: dict) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for item in mtx.get("paths") or []:
        if isinstance(item, dict) and item.get("name"):
            out[str(item["name"])] = bool(item.get("ready"))
    return out


def _light_inference_row(
    cam: dict,
    *,
    docker_map: dict,
    docker_stats: dict[str, dict],
) -> dict:
    cid = str(cam.get("id") or cam.get("path") or "").strip()
    worker = _read_worker_status(cid) or {}
    container = docker_map.get(cid)
    name = container_name(cid)

    if container is None:
        status = worker.get("state") if worker.get("state") in ("running", "starting") else "stopped"
        inf_status = str(status or "stopped")
    else:
        state = container.attrs.get("State") or {}
        inf_status = _map_docker_status(
            container.status,
            state.get("ExitCode"),
            str(state.get("Error") or ""),
        )

    metrics = _metrics_from_worker_status(worker)
    cgroup = docker_stats.get(name, {})
    if cgroup.get("cpu_percent") is not None:
        metrics["container_cpu_percent"] = cgroup["cpu_percent"]
    if cgroup.get("memory_mb") is not None:
        metrics["container_memory_mb"] = cgroup["memory_mb"]
    if cgroup.get("memory_limit_mb") is not None:
        metrics["container_memory_limit_mb"] = cgroup["memory_limit_mb"]

    return {
        "status": inf_status,
        "backend": worker.get("backend") or "",
        "container": name,
        "message": worker.get("message") or "",
        "metrics": metrics,
    }


def build_services_overview(
    camera_ips_file: str,
    frames_dir: str,
    *,
    probe_online: bool = False,
) -> dict[str, Any]:
    # 1) 轻量：Redis / MediaMTX 列表（各 1 次）
    redis = _ping_redis()
    mtx = list_mediamtx_paths_summary()
    mtx_ready = _mtx_ready_by_path(mtx)

    # 2) Docker：一次 list + 一次 stats 缓存（避免 list_cameras + 多次 stats）
    docker_stats = _batch_docker_stats_by_name()
    docker_map = _inference_containers_by_camera_id()

    # 3) 摄像头：不 probe、不 attach_inference（省 1s+ docker list）
    items = load_cameras(camera_ips_file)

    cam_rows = []
    for cam in items:
        cid = str(cam.get("id") or cam.get("path") or "").strip()
        slug = str(cam.get("path") or cid).strip()
        stream_ready = None
        if is_mediamtx_playback_available(cam):
            stream_ready = mtx_ready.get(slug, False)
        cam_rows.append(
            {
                "id": cid,
                "name": cam.get("name") or cid,
                "enabled": bool(cam.get("enabled", True)),
                "online": cam.get("online"),
                "stream_ready": stream_ready,
                "inference": _light_inference_row(
                    cam, docker_map=docker_map, docker_stats=docker_stats
                ),
            }
        )

    return {
        "status": "success",
        "host": _host_metrics(),
        "stack": list_compose_stack_containers(stats_map=docker_stats),
        "redis": redis,
        "mediamtx": mtx,
        "cameras": cam_rows,
        "generated_at": int(__import__("time").time()),
    }

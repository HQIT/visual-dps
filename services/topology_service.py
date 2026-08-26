"""服务拓扑：聚合 MediaMTX / Docker 推理 / Redis / 推流可达性。"""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Any
from urllib.parse import urlparse

from services.annotation_service import load_camera_annotation
from services.camera_service import normalize_rtsp_url
from services.inference_container_service import (
    _compose_inference_status,
    _docker_client,
    _inference_containers_by_camera_id,
    container_name,
)
from services.mediamtx_service import (
    MEDIAMTX_API_URL,
    MEDIAMTX_INTERNAL_HOST,
    MEDIAMTX_RTSP_HOST,
    MEDIAMTX_RTSP_PORT,
    _mediamtx_api,
    build_camera_playback_urls,
)
from services.event_engine.sharding import (
    logical_shard_id,
    worker_owned_shard_ids,
    worker_owned_stream_keys,
)
from services.pose_bus import (
    POSE_STREAM_GROUP,
    all_pose_stream_keys,
    get_pose_snapshot,
    list_recent_pose_frames,
    stream_key_for_camera,
)

POLL_RECOMMENDED_MS = 10000
_SCHEMA = 1
_TOPOLOGY_POSE_STALE_SEC = max(
    1.0, float(os.environ.get("TOPOLOGY_POSE_STALE_SEC", "3"))
)

_COMPOSE_CONTAINERS = (
    ("visual-dps-mediamtx", "mediamtx", "mediamtx"),
    ("visual-dps-redis", "redis", "redis"),
    ("visual-dps-event-worker-2", "event_worker", "event-worker-2"),
    ("visual-dps-event-worker-2-b", "event_worker", "event-worker-2-b"),
    ("visual-dps-event-worker", "event_worker", "event-worker"),
    ("visual-dps-ui", "ui", "ui"),
)

# 先 worker-2（含 dual），再 worker-1；拓扑按实际在跑的实例展示
_EVENT_WORKER_SPECS = (
    ("visual-dps-event-worker-2", "event-worker-2"),
    ("visual-dps-event-worker-2-b", "event-worker-2-b"),
    ("visual-dps-event-worker", "event-worker"),
)


def _health_ok_warn_error(ok: bool, warn: bool = False) -> str:
    if ok:
        return "ok"
    if warn:
        return "warn"
    return "error"


def _rtsp_parts(url: str) -> tuple[str, int, str]:
    try:
        p = urlparse(str(url or "").strip())
        host = (p.hostname or "").lower()
        port = p.port if p.port is not None else 8554
        path = (p.path or "").strip("/")
        return host, port, path
    except Exception:
        return "", 8554, ""


def _hosts_equivalent(a: str, b: str) -> bool:
    a = (a or "").lower()
    b = (b or "").lower()
    if not a or not b:
        return a == b
    if a == b:
        return True
    local = {
        "127.0.0.1",
        "localhost",
        (MEDIAMTX_RTSP_HOST or "").lower(),
        (MEDIAMTX_INTERNAL_HOST or "").lower(),
        "mediamtx",
    }
    return a in local and b in local


def _probe_rtsp(url: str, timeout_sec: float = 3.0) -> dict:
    url = str(url or "").strip()
    if not url:
        return {"reachable": False, "latency_ms": None, "error": "empty url"}
    ffprobe = os.environ.get("FFPROBE_BIN", "ffprobe").strip() or "ffprobe"
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-rtsp_transport",
                "tcp",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        latency = int((time.monotonic() - started) * 1000)
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return {"reachable": True, "latency_ms": latency, "error": ""}
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = err[-1][:200] if err else f"exit {proc.returncode}"
        return {"reachable": False, "latency_ms": latency, "error": msg}
    except subprocess.TimeoutExpired:
        return {"reachable": False, "latency_ms": None, "error": "timeout"}
    except FileNotFoundError:
        return {"reachable": False, "latency_ms": None, "error": "ffprobe not found"}
    except Exception as exc:
        return {"reachable": False, "latency_ms": None, "error": str(exc)[:200]}


def _container_network(container) -> tuple[str, str]:
    try:
        nets = (container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
        for data in nets.values():
            ip = str(data.get("IPAddress") or "").strip()
            if ip:
                hostname = str(container.name or "")
                return hostname, ip
    except Exception:
        pass
    return str(getattr(container, "name", "") or ""), ""


def _container_ports(container) -> list[dict]:
    out: list[dict] = []
    try:
        bindings = (container.attrs.get("NetworkSettings") or {}).get("Ports") or {}
        for container_port, hosts in (bindings or {}).items():
            if not container_port:
                continue
            proto = "tcp"
            cport = container_port
            if "/" in container_port:
                cport, proto = container_port.split("/", 1)
            name = "other"
            cp = int(cport) if str(cport).isdigit() else None
            if cp == 8554:
                name = "rtsp"
            elif cp == 9997:
                name = "api"
            elif cp == 8888:
                name = "hls"
            elif cp == 8889:
                name = "webrtc"
            elif cp == 6379:
                name = "redis"
            elif cp == 8045:
                name = "http"
            host_port = None
            bind = ""
            if hosts and isinstance(hosts, list) and hosts[0]:
                host_port = hosts[0].get("HostPort")
                bind = str(hosts[0].get("HostIp") or "")
            out.append(
                {
                    "name": name,
                    "protocol": proto,
                    "host_port": int(host_port) if host_port and str(host_port).isdigit() else None,
                    "container_port": cp,
                    "bind": bind,
                }
            )
    except Exception:
        pass
    return out


def _get_compose_container(name: str):
    try:
        return _docker_client().containers.get(name)
    except Exception:
        return None


def _container_env_map(container) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for item in (container.attrs.get("Config") or {}).get("Env") or []:
            if not isinstance(item, str) or "=" not in item:
                continue
            key, value = item.split("=", 1)
            out[key] = value
    except Exception:
        pass
    return out


def _discover_event_workers() -> list[dict[str, Any]]:
    """返回实际存在的 event-worker 容器；worker-2 在跑时忽略已退出的 worker-1。"""
    found: list[dict[str, Any]] = []
    for container_name, node_id in _EVENT_WORKER_SPECS:
        container = _get_compose_container(container_name)
        if container is None:
            continue
        env = _container_env_map(container)
        host, ip = _container_network(container)
        found.append(
            {
                "container_name": container_name,
                "node_id": node_id,
                "container": container,
                "running": container.status == "running",
                "env": env,
                "shards": worker_owned_shard_ids(env),
                "stream_keys": worker_owned_stream_keys(env),
                "consumer_name": env.get("EVENT_WORKER_CONSUMER_NAME")
                or env.get("HOSTNAME")
                or container_name,
                "host": host,
                "ip": ip,
            }
        )

    worker2_running = any(
        w["running"] and w["container_name"].startswith("visual-dps-event-worker-2")
        for w in found
    )
    if worker2_running:
        found = [
            w
            for w in found
            if w["container_name"] != "visual-dps-event-worker" or w["running"]
        ]
    return found


def _event_worker_for_camera(
    workers: list[dict[str, Any]], camera_id: str
) -> dict[str, Any] | None:
    if not workers:
        return None
    sid = logical_shard_id(camera_id)
    for worker in workers:
        if sid in (worker.get("shards") or []):
            return worker
    running = [w for w in workers if w.get("running")]
    return running[0] if running else workers[0]


def _mediamtx_paths_map() -> tuple[dict[str, dict], bool, str]:
    if not MEDIAMTX_API_URL:
        return {}, False, "api_disabled"
    try:
        listed = _mediamtx_api("GET", "/v3/paths/list?itemsPerPage=200")
        items = listed.get("items") or []
        return (
            {str(it.get("name") or "").strip(): it for it in items if it.get("name")},
            True,
            "",
        )
    except Exception as exc:
        return {}, False, str(exc)


def _infer_gpu_meta(container, infer_status: dict) -> dict:
    requested = os.environ.get("INFERENCE_USE_GPU", "").strip() in ("1", "true", "yes")
    warning = ""
    available = False
    if container is not None:
        try:
            env_list = container.attrs.get("Config", {}).get("Env") or []
            for item in env_list:
                if item.startswith("INFERENCE_USE_GPU="):
                    requested = item.split("=", 1)[1].strip() in ("1", "true", "yes")
                    break
            tail = container.logs(tail=40).decode("utf-8", errors="replace")
            if "NVIDIA Driver was not detected" in tail:
                warning = "NVIDIA Driver was not detected"
            elif "CUDA failure" in tail or "CUDA driver" in tail:
                warning = "CUDA unavailable in container"
            elif requested and infer_status.get("status") == "running":
                available = True
        except Exception:
            pass
    return {"requested": requested, "available": available, "warning": warning}


def _node(
    *,
    id: str,
    kind: str,
    label: str,
    health: str,
    host: str = "",
    hostname: str = "",
    ip: str = "",
    ports: list | None = None,
    meta: dict | None = None,
) -> dict:
    return {
        "id": id,
        "kind": kind,
        "label": label,
        "health": health,
        "host": host,
        "hostname": hostname,
        "ip": ip,
        "ports": ports or [],
        "meta": meta or {},
    }


def _edge(
    *,
    id: str,
    from_id: str,
    to_id: str,
    direction: str,
    protocol: str,
    endpoint: str,
    health: str,
    role: str = "",
    meta: dict | None = None,
) -> dict:
    return {
        "id": id,
        "from": from_id,
        "to": to_id,
        "direction": direction,
        "protocol": protocol,
        "role": role,
        "endpoint": endpoint,
        "health": health,
        "meta": meta or {},
    }


def _issue(code: str, severity: str, message: str, hint: str = "", camera_id: str = "") -> dict:
    out = {"code": code, "severity": severity, "message": message, "hint": hint}
    if camera_id:
        out["camera_id"] = camera_id
    return out


def _assess_pose_status(
    camera_id: str,
    *,
    generated_at: float,
    infer_running: bool,
    mtx_ready: bool,
) -> dict[str, Any]:
    """根据 Redis pose 快照与 stream 最近帧判断发布是否新鲜、是否冻结在旧画面。"""
    snap = get_pose_snapshot(camera_id) if infer_running else None
    ts = float(snap["ts"]) if snap and snap.get("ts") is not None else None
    age_sec = round(generated_at - ts, 2) if ts is not None else None
    publishing = age_sec is not None and age_sec <= _TOPOLOGY_POSE_STALE_SEC

    frozen = False
    recent_frame_delta: int | None = None
    recent = list_recent_pose_frames(camera_id, limit=6) if infer_running else []
    if len(recent) >= 2:
        newest, older = recent[0], recent[1]
        fi_n = newest.get("frame_idx")
        fi_o = older.get("frame_idx")
        if fi_n is not None and fi_o is not None:
            try:
                recent_frame_delta = int(fi_n) - int(fi_o)
            except (TypeError, ValueError):
                recent_frame_delta = None
            if recent_frame_delta and (newest.get("persons") or []) == (older.get("persons") or []):
                frozen = True

    expect_pose = bool(infer_running and mtx_ready)
    return {
        "snapshot_present": snap is not None,
        "last_ts_age_sec": age_sec,
        "frame_idx": snap.get("frame_idx") if snap else None,
        "publishing": publishing,
        "frozen": frozen,
        "recent_frame_delta": recent_frame_delta,
        "expect_pose": expect_pose,
    }


def build_topology_overview(
    camera_ips_file: str,
    json_dir: str,
    default_json_file: str,
    *,
    probe: bool = True,
) -> dict:
    from services.camera_store import load_cameras

    generated_at = time.time()
    issues: list[dict] = []
    paths_out: list[dict] = []
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    docker_ok = True
    try:
        _docker_client().ping()
    except Exception as exc:
        docker_ok = False
        issues.append(
            _issue(
                "DOCKER_UNAVAILABLE",
                "error",
                "无法连接 Docker",
                str(exc)[:240],
            )
        )

    mtx_paths, mtx_api_ok, mtx_api_err = _mediamtx_paths_map()
    if not mtx_api_ok:
        issues.append(
            _issue(
                "MTX_API_UNAVAILABLE",
                "warn" if mtx_api_err == "api_disabled" else "error",
                "MediaMTX Control API 不可用",
                mtx_api_err[:240] or MEDIAMTX_API_URL,
            )
        )

    infer_map = _inference_containers_by_camera_id() if docker_ok else {}

    mtx_container = _get_compose_container("visual-dps-mediamtx") if docker_ok else None
    redis_container = _get_compose_container("visual-dps-redis") if docker_ok else None
    ui_container = _get_compose_container("visual-dps-ui") if docker_ok else None
    event_workers = _discover_event_workers() if docker_ok else []

    mtx_host, mtx_ip = _container_network(mtx_container) if mtx_container else ("mediamtx", "")
    mtx_ports = _container_ports(mtx_container) if mtx_container else []
    mtx_ready_any = any(bool(p.get("ready")) for p in mtx_paths.values())

    nodes["mtx:compose"] = _node(
        id="mtx:compose",
        kind="mediamtx",
        label="MediaMTX (compose)",
        health=_health_ok_warn_error(mtx_api_ok, warn=mtx_api_ok and not mtx_ready_any),
        host="visual-dps-mediamtx",
        hostname=mtx_host or MEDIAMTX_INTERNAL_HOST,
        ip=mtx_ip,
        ports=mtx_ports
        or [
            {
                "name": "rtsp",
                "protocol": "tcp",
                "host_port": MEDIAMTX_RTSP_PORT,
                "container_port": 8554,
                "bind": "0.0.0.0",
            }
        ],
        meta={"api_url": MEDIAMTX_API_URL},
    )

    redis_host, redis_ip = _container_network(redis_container) if redis_container else ("redis", "")
    redis_health = "ok" if redis_container and redis_container.status == "running" else "unknown"
    if docker_ok and not redis_container:
        redis_health = "error"
        issues.append(_issue("REDIS_UNREACHABLE", "error", "未找到 Redis 容器 visual-dps-redis"))
    nodes["redis"] = _node(
        id="redis",
        kind="redis",
        label="Redis",
        health=redis_health,
        host="visual-dps-redis",
        hostname=redis_host,
        ip=redis_ip,
        ports=_container_ports(redis_container) if redis_container else [],
    )

    running_workers = [w for w in event_workers if w["running"]]
    if docker_ok and not running_workers:
        issues.append(
            _issue(
                "EVENT_WORKER_DOWN",
                "error",
                "未找到运行中的 event-worker（worker-2 / worker-1）",
            )
        )
        if not event_workers:
            nodes["event-worker"] = _node(
                id="event-worker",
                kind="event_worker",
                label="event-worker (未运行)",
                health="error",
                host="",
            )
    for worker in event_workers:
        node_health = "ok" if worker["running"] else "error"
        if worker["running"] is False:
            issues.append(
                _issue(
                    "EVENT_WORKER_DOWN",
                    "error",
                    f"{worker['container_name']} 状态: {worker['container'].status}",
                )
            )
        shard_meta = worker["shards"]
        if shard_meta:
            shard_label = f"{shard_meta[0]}-{shard_meta[-1]}" if len(shard_meta) > 1 else str(shard_meta[0])
        else:
            shard_label = "none"
        nodes[worker["node_id"]] = _node(
            id=worker["node_id"],
            kind="event_worker",
            label=worker["container_name"],
            health=node_health,
            host=worker["container_name"],
            hostname=worker["host"],
            ip=worker["ip"],
            meta={
                "shards": shard_meta,
                "shard_label": shard_label,
                "consumer_name": worker["consumer_name"],
                "stream_keys": worker["stream_keys"],
            },
        )

    ui_host, ui_ip = _container_network(ui_container) if ui_container else ("", "")
    nodes["ui"] = _node(
        id="ui",
        kind="ui",
        label="visual-dps-ui",
        health="ok" if ui_container and ui_container.status == "running" else "unknown",
        host="visual-dps-ui",
        hostname=ui_host,
        ip=ui_ip,
        ports=_container_ports(ui_container) if ui_container else [],
    )

    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    redis_masked = re.sub(r"://([^:@/]+):([^@/]+)@", r"://\1:***@", redis_url)

    cameras = load_cameras(camera_ips_file)
    for cam in cameras:
        if not cam.get("enabled", True):
            continue
        cid = str(cam.get("id") or cam.get("path") or "").strip()
        if not cid:
            continue
        cam_name = str(cam.get("name") or cid)
        source_type = str(cam.get("source_type") or "publisher")
        path_name = str(cam.get("path") or cid).strip()
        playback_url = normalize_rtsp_url(str(cam.get("url") or ""))
        pull_url = str(cam.get("pull_url") or "").strip()
        playback_urls = build_camera_playback_urls(cid, path_name, proxied=True)

        ann = load_camera_annotation(cid, json_dir, default_json_file, camera=cam)
        ann_url = ""
        if ann and isinstance(ann.get("config"), dict):
            si = ann["config"].get("source_info") or {}
            if isinstance(si, dict):
                ann_url = str(si.get("camera_url") or "").strip()

        mtx_item = mtx_paths.get(path_name) or {}
        mtx_ready = bool(mtx_item.get("ready"))
        mtx_available = bool(mtx_item.get("available", mtx_ready))
        mtx_source = mtx_item.get("source")
        mtx_readers = mtx_item.get("readers") or []

        container = infer_map.get(cid)
        infer_status = (
            _compose_inference_status(cid, container) if cid else {"status": "stopped"}
        )
        infer_stream = normalize_rtsp_url(
            str(infer_status.get("stream_url") or playback_url or "")
        )
        if container is not None and not infer_stream:
            try:
                for item in container.attrs.get("Config", {}).get("Env") or []:
                    if item.startswith("INFERENCE_STREAM_URL="):
                        infer_stream = normalize_rtsp_url(item.split("=", 1)[1])
                        break
            except Exception:
                pass

        infer_host, infer_ip = _container_network(container) if container else ("", "")
        gpu_meta = _infer_gpu_meta(container, infer_status)

        infer_stream_probe = {"reachable": False, "latency_ms": None, "error": ""}
        playback_probe = {"reachable": False, "latency_ms": None, "error": ""}
        external_hint = ""
        if probe:
            if infer_stream:
                infer_stream_probe = _probe_rtsp(infer_stream)
            if playback_url and playback_url != infer_stream:
                playback_probe = _probe_rtsp(playback_url)
            elif playback_url:
                playback_probe = infer_stream_probe

            if (
                not mtx_ready
                and not infer_stream_probe.get("reachable")
                and playback_probe.get("reachable")
                and playback_url
            ):
                ph, _, _ = _rtsp_parts(playback_url)
                ih, _, _ = _rtsp_parts(infer_stream)
                if ph and ih and not _hosts_equivalent(ph, ih):
                    external_hint = playback_url

        path_issues: list[str] = []
        path_health = "ok"

        if not mtx_ready:
            path_issues.append("MTX_PATH_NOT_READY")
            path_health = "error"
            issues.append(
                _issue(
                    "MTX_PATH_NOT_READY",
                    "error",
                    f"MediaMTX path {path_name} 无推流（ready=false）",
                    f"请将推流指向 compose MediaMTX（如 rtsp://{MEDIAMTX_RTSP_HOST}:{MEDIAMTX_RTSP_PORT}/{path_name}）",
                    camera_id=cid,
                )
            )

        if infer_status.get("status") == "running" and probe and not infer_stream_probe.get(
            "reachable"
        ):
            path_issues.append("INFER_NO_FRAMES")
            path_health = "error"
            issues.append(
                _issue(
                    "INFER_NO_FRAMES",
                    "error",
                    f"推理容器运行中但拉流失败：{infer_stream_probe.get('error', '')[:120]}",
                    "检查 INFERENCE_STREAM_URL 与 MediaMTX 是否有 publisher",
                    camera_id=cid,
                )
            )

        if external_hint or (
            playback_probe.get("reachable")
            and not infer_stream_probe.get("reachable")
            and infer_stream
        ):
            path_issues.append("INFER_STREAM_MISMATCH")
            path_health = "error"
            issues.append(
                _issue(
                    "INFER_STREAM_MISMATCH",
                    "error",
                    "推理拉流地址无画面，但配置播放地址或其它主机有流",
                    f"配置/播放: {playback_url or '—'}；infer: {infer_stream or '—'}"
                    + (f"；检测到: {external_hint}" if external_hint else ""),
                    camera_id=cid,
                )
            )

        if ann_url and infer_stream:
            ah, _, _ = _rtsp_parts(ann_url)
            ih, _, _ = _rtsp_parts(infer_stream)
            if ah and ih and not _hosts_equivalent(ah, ih):
                path_issues.append("ANNOTATION_URL_MISMATCH")
                if path_health == "ok":
                    path_health = "warn"
                issues.append(
                    _issue(
                        "ANNOTATION_URL_MISMATCH",
                        "warn",
                        "标注来源地址与推理流地址主机不一致",
                        f"annotation: {ann_url}；infer: {infer_stream}",
                        camera_id=cid,
                    )
                )

        if gpu_meta.get("requested") and gpu_meta.get("warning"):
            path_issues.append("INFER_GPU_UNAVAILABLE")
            if path_health == "ok":
                path_health = "warn"
            issues.append(
                _issue(
                    "INFER_GPU_UNAVAILABLE",
                    "warn",
                    "推理容器未获得 GPU",
                    gpu_meta.get("warning", ""),
                    camera_id=cid,
                )
            )

        infer_running = infer_status.get("status") in ("running", "starting")
        pose_status = _assess_pose_status(
            cid,
            generated_at=generated_at,
            infer_running=infer_running,
            mtx_ready=mtx_ready,
        )
        last_pose_age_sec = pose_status.get("last_ts_age_sec")

        if pose_status.get("expect_pose"):
            if pose_status.get("frozen"):
                path_issues.append("INFER_POSE_FROZEN")
                path_health = "error"
                issues.append(
                    _issue(
                        "INFER_POSE_FROZEN",
                        "error",
                        "有推流且推理在跑，但 pose 关键点不随帧变化",
                        "多为 infer 仍消费 RTSP 缓冲旧帧；请重启该路推理容器（visual-dps-infer-"
                        f"{cid}）或先停推流等待缓冲 TTL 后再恢复",
                        camera_id=cid,
                    )
                )
            elif not pose_status.get("publishing"):
                path_issues.append("POSE_STALE")
                if path_health == "ok":
                    path_health = "warn"
                issues.append(
                    _issue(
                        "POSE_STALE",
                        "warn",
                        f"推理运行中但 Redis 无新鲜 pose（>{_TOPOLOGY_POSE_STALE_SEC:.0f}s 未更新）",
                        "检查 infer 是否拉流成功、RTSP_FRAME_BUFFER_TTL 是否已过期清帧",
                        camera_id=cid,
                    )
                )

        pose_edge_health = "unknown"
        if infer_running:
            if pose_status.get("frozen"):
                pose_edge_health = "error"
            elif pose_status.get("publishing"):
                pose_edge_health = "ok"
            elif pose_status.get("expect_pose"):
                pose_edge_health = "warn"
            else:
                pose_edge_health = "unknown"

        src_health = "ok" if mtx_ready else ("warn" if playback_probe.get("reachable") else "error")
        src_id = f"source:{cid}"
        nodes[src_id] = _node(
            id=src_id,
            kind="source",
            label=f"源 · {cam_name}",
            health=src_health,
            host="external" if source_type == "external" else "host",
            hostname=_rtsp_parts(playback_url or pull_url)[0] or "—",
            meta={"source_type": source_type, "camera_id": cid},
        )

        infer_id = f"infer:{cid}"
        docker_status = infer_status.get("status", "stopped")
        if docker_status == "running":
            if path_health == "error":
                infer_node_health = "error"
            elif path_health == "warn":
                infer_node_health = "warn"
            else:
                infer_node_health = "ok"
        elif docker_status == "starting":
            infer_node_health = "error" if path_health == "error" else "warn"
        elif docker_status == "error":
            infer_node_health = "error"
        elif path_health in ("error", "warn"):
            # 容器未起但整路已有告警/错误（如 MTX 无流）→ 与抽屉 path.health 一致
            infer_node_health = path_health
        else:
            infer_node_health = "unknown"

        nodes[infer_id] = _node(
            id=infer_id,
            kind="inference",
            label=infer_status.get("container_name") or container_name(cid),
            health=infer_node_health,
            host=infer_status.get("container_name") or container_name(cid),
            hostname=infer_host,
            ip=infer_ip,
            meta={
                "backend": infer_status.get("backend", ""),
                "docker_status": infer_status.get("docker_status", ""),
                "camera_id": cid,
            },
        )

        pub_endpoint = playback_url or f"rtsp://{MEDIAMTX_RTSP_HOST}:{MEDIAMTX_RTSP_PORT}/{path_name}"
        edges.append(
            _edge(
                id=f"e:{cid}:source->mtx",
                from_id=src_id,
                to_id="mtx:compose",
                direction="push",
                protocol="rtsp",
                role="publish",
                endpoint=pub_endpoint,
                health=_health_ok_warn_error(mtx_ready, warn=playback_probe.get("reachable")),
                meta={"mtx_ready": mtx_ready},
            )
        )

        edges.append(
            _edge(
                id=f"e:{cid}:mtx->infer",
                from_id="mtx:compose",
                to_id=infer_id,
                direction="pull",
                protocol="rtsp",
                role="play",
                endpoint=infer_stream or f"rtsp://{MEDIAMTX_INTERNAL_HOST}:{MEDIAMTX_RTSP_PORT}/{path_name}",
                health=_health_ok_warn_error(bool(infer_stream_probe.get("reachable"))),
                meta={"probe_error": infer_stream_probe.get("error", "")},
            )
        )

        if infer_running:
            edges.append(
                _edge(
                    id=f"e:{cid}:infer->redis",
                    from_id=infer_id,
                    to_id="redis",
                    direction="push",
                    protocol="redis_stream",
                    role="pose",
                    endpoint=f"{redis_masked} · XADD {stream_key_for_camera(cid)}",
                    health=pose_edge_health,
                    meta={
                        "pose_age_sec": last_pose_age_sec,
                        "pose_frozen": bool(pose_status.get("frozen")),
                        "logical_shard": logical_shard_id(cid),
                        "stream_key": stream_key_for_camera(cid),
                    },
                )
            )

        edges.append(
            _edge(
                id=f"e:ui->mtx:{cid}",
                from_id="ui",
                to_id="mtx:compose",
                direction="pull",
                protocol="http",
                role="preview",
                endpoint=playback_urls.get("hls") or "",
                health=_health_ok_warn_error(mtx_ready),
                meta={"camera_id": cid},
            )
        )

        owner = _event_worker_for_camera(event_workers, cid)
        paths_out.append(
            {
                "camera_id": cid,
                "camera_name": cam_name,
                "source_type": source_type,
                "configured": {
                    "playback_url": playback_url,
                    "pull_url": pull_url,
                    "annotation_camera_url": ann_url,
                    "mtx_path": path_name,
                    "hls": playback_urls.get("hls") or "",
                    "whep": playback_urls.get("whep") or "",
                },
                "runtime": {
                    "infer_stream_url": infer_stream,
                    "infer_stream_probe": infer_stream_probe,
                    "external_publish_hint": external_hint,
                    "pose": pose_status,
                },
                "mediamtx": {
                    "path": path_name,
                    "ready": mtx_ready,
                    "available": mtx_available,
                    "source": mtx_source,
                    "readers": mtx_readers,
                    "bytes_received": int(mtx_item.get("bytesReceived") or 0),
                    "bytes_sent": int(mtx_item.get("bytesSent") or 0),
                },
                "inference": {
                    **infer_status,
                    "hostname": infer_host,
                    "ip": infer_ip,
                    "gpu": gpu_meta,
                    "pose_publish": {
                        "delivery": os.environ.get("POSE_DELIVERY", "stream"),
                        "stream_key": stream_key_for_camera(cid),
                        "logical_shard": logical_shard_id(cid),
                        "stream_keys": all_pose_stream_keys(),
                        "group": POSE_STREAM_GROUP,
                    },
                },
                "event": {
                    "worker_container": (owner["container_name"] if owner else ""),
                    "consumer_group": POSE_STREAM_GROUP,
                    "consumer_name": (owner["consumer_name"] if owner else ""),
                    "redis_url": redis_masked,
                    "last_pose_age_sec": last_pose_age_sec,
                    "logical_shard": logical_shard_id(cid),
                    "stream_key": stream_key_for_camera(cid),
                },
                "health": path_health,
                "issues": path_issues,
            }
        )

    if paths_out:
        for worker in event_workers:
            keys = worker.get("stream_keys") or []
            key_preview = ",".join(keys[:4])
            if len(keys) > 4:
                key_preview += f",…(+{len(keys) - 4})"
            edges.append(
                _edge(
                    id=f"e:redis->{worker['node_id']}",
                    from_id="redis",
                    to_id=worker["node_id"],
                    direction="pull",
                    protocol="redis_stream",
                    role="event",
                    endpoint=f"XREADGROUP {POSE_STREAM_GROUP} · {key_preview or '—'}",
                    health="ok" if worker["running"] else "error",
                    meta={
                        "shards": worker.get("shards") or [],
                        "container": worker["container_name"],
                    },
                )
            )

    # 去重 issues（同 code+camera_id）
    seen: set[tuple[str, str]] = set()
    deduped_issues: list[dict] = []
    for it in issues:
        key = (it.get("code", ""), it.get("camera_id", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped_issues.append(it)

    redis_pose_ok = any(
        (p.get("runtime") or {}).get("pose", {}).get("snapshot_present")
        for p in paths_out
    )

    return {
        "schema": _SCHEMA,
        "status": "success",
        "generated_at": generated_at,
        "poll_recommended_ms": POLL_RECOMMENDED_MS,
        "capabilities": {
            "docker": docker_ok,
            "mediamtx_api": mtx_api_ok,
            "redis_info": redis_pose_ok,
        },
        "graph": {"nodes": list(nodes.values()), "edges": edges},
        "paths": paths_out,
        "issues": deduped_issues,
    }

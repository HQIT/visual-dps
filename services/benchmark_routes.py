"""离线评测 API：上传、对齐预览、启动推理、回放数据。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

import cv2
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from core.config import load_app_config
from services.annotation_service import (
    annotation_payload_for_api,
    camera_annotation_path,
    load_camera_annotation,
)
from services.benchmark_export import render_event_export_csv, safe_export_filename
from services.benchmark_store import (
    create_run,
    delete_run,
    get_run,
    init_benchmark_db,
    list_alarms,
    list_pose_frames,
    list_runs,
    reset_run_for_rerun,
    run_video_path,
    update_run_inference_params,
)
from services.inference_backends.model_registry import normalize_backend_setting, resolve_model_preset
from services.runtime_config_service import get_public_settings
from services.video_service import get_first_frame_b64


class BenchmarkRerunBody(BaseModel):
    backend: str = Field("", max_length=64)
    pose_frame_interval: int = Field(0, ge=0, le=120)
    alarm_min_consecutive_frames: int = Field(0, ge=0, le=120)
    alarm_cooldown_frames: int = Field(-1, ge=-1, le=600)


def _default_alarm_settings(app_config: dict) -> tuple[int, int]:
    items = get_public_settings(app_config).get("items") or {}
    infer_cfg = app_config.get("inference", {}) or {}
    if "inference.alarm_min_consecutive_frames" in items:
        alarm_min = int(items["inference.alarm_min_consecutive_frames"])
    else:
        alarm_min = int(infer_cfg.get("alarm_min_consecutive_frames", 3) or 3)
    if "inference.alarm_cooldown_frames" in items:
        alarm_cooldown = int(items["inference.alarm_cooldown_frames"])
    else:
        raw = infer_cfg.get("alarm_cooldown_frames")
        alarm_cooldown = int(raw if raw is not None else 0)
    return max(1, alarm_min), max(0, alarm_cooldown)


def _parse_alarm_params(
    app_config: dict,
    *,
    alarm_min: int | None = None,
    alarm_cooldown: int | None = None,
) -> tuple[int, int]:
    default_min, default_cooldown = _default_alarm_settings(app_config)
    if alarm_min is not None and int(alarm_min) >= 1:
        out_min = max(1, min(120, int(alarm_min)))
    else:
        out_min = default_min
    if alarm_cooldown is not None and int(alarm_cooldown) >= 0:
        out_cooldown = max(0, min(600, int(alarm_cooldown)))
    else:
        out_cooldown = default_cooldown
    return out_min, out_cooldown


def _run_config_snapshot(
    app_config: dict,
    pose_interval: int,
    *,
    alarm_min: int | None = None,
    alarm_cooldown: int | None = None,
) -> dict[str, Any]:
    effective = _effective_inference_settings(app_config)
    min_frames, cooldown_frames = _parse_alarm_params(
        app_config,
        alarm_min=alarm_min,
        alarm_cooldown=alarm_cooldown,
    )
    return {
        "inference.frame_rate": effective.get("frame_rate"),
        "inference.height": effective.get("height"),
        "inference.pose_frame_interval": pose_interval,
        "inference.alarm_min_consecutive_frames": min_frames,
        "inference.alarm_cooldown_frames": cooldown_frames,
    }


def _effective_inference_settings(app_config: dict) -> dict[str, Any]:
    """合并 app_config.json 与 runtime_config.json 的有效推理参数。"""
    items = get_public_settings(app_config).get("items") or {}
    return {
        "backend": str(items.get("models.backend") or "").strip(),
        "frame_rate": items.get("inference.frame_rate"),
        "height": items.get("inference.height"),
        "pose_frame_interval": items.get("inference.pose_frame_interval"),
    }


def _project_root() -> str:
    return os.environ.get("HOST_PROJECT_ROOT", "").strip() or os.getcwd()


def _probe_video(path: str) -> dict[str, Any]:
    cap = cv2.VideoCapture(path)
    if cap.isOpened():
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        except Exception:
            pass
    if not cap.isOpened():
        return {"error": "无法打开视频"}
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
    return {
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "duration_sec": round(duration, 3),
    }


def _spawn_benchmark_runner(run_id: str) -> None:
    root = _project_root()
    env = os.environ.copy()
    env["PYTHONPATH"] = root if root not in env.get("PYTHONPATH", "") else env.get("PYTHONPATH", root)
    log_path = os.path.join(root, "localdata", "benchmark", run_id, "runner.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")
    log_file.write(f"\n--- spawn {time.strftime('%Y-%m-%d %H:%M:%S')} run={run_id} ---\n")
    log_file.flush()
    subprocess.Popen(
        [sys.executable, os.path.join(root, "benchmark_runner.py"), "--run-id", run_id],
        cwd=root,
        env=env,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )


def register_benchmark_routes(router: APIRouter, app_config: dict | None = None) -> None:
    app_config = app_config or load_app_config()
    paths = app_config.get("paths", {})
    json_dir = str(paths.get("json_dir", "localdata/json"))
    default_json = str(paths.get("default_json_file", "localdata/json/precise_boxes_new.json"))
    capture_height = int(app_config.get("video", {}).get("capture_height", 480) or 480)

    @router.get("/benchmark/runs")
    async def benchmark_runs_list(
        page: int = 1,
        page_size: int = 20,
        camera_id: str = "",
        status: str = "",
    ):
        init_benchmark_db()
        return list_runs(page=page, page_size=page_size, camera_id=camera_id, status=status)

    @router.get("/benchmark/runs/{run_id}")
    async def benchmark_run_detail(run_id: str):
        init_benchmark_db()
        run = get_run(run_id)
        if not run:
            return {"status": "error", "error": "run 不存在"}
        run["alarms"] = list_alarms(run_id)
        return {"status": "success", "run": run}

    @router.get("/benchmark/runs/{run_id}/frames")
    async def benchmark_run_frames(run_id: str, from_sec: float | None = None, to_sec: float | None = None):
        init_benchmark_db()
        if not get_run(run_id):
            return {"status": "error", "error": "run 不存在"}
        frames = list_pose_frames(run_id, from_sec=from_sec, to_sec=to_sec)
        return {"status": "success", "items": frames}

    @router.get("/benchmark/runs/{run_id}/alarms")
    async def benchmark_run_alarms(run_id: str):
        init_benchmark_db()
        if not get_run(run_id):
            return {"status": "error", "error": "run 不存在"}
        return {"status": "success", "items": list_alarms(run_id)}

    @router.get("/benchmark/runs/{run_id}/export")
    async def benchmark_run_export(run_id: str):
        init_benchmark_db()
        run = get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run 不存在")
        frames = list_pose_frames(run_id)
        fps = float(run.get("video_fps") or 25.0)
        payload = render_event_export_csv(frames, fallback_fps=fps)
        filename = safe_export_filename(str(run.get("title") or ""), run_id)
        return StreamingResponse(
            iter([payload]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.get("/benchmark/runs/{run_id}/video")
    async def benchmark_run_video(run_id: str):
        init_benchmark_db()
        run = get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="run 不存在")
        path = run_video_path(run_id)
        if not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="视频不存在")
        return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))

    @router.delete("/benchmark/runs/{run_id}")
    async def benchmark_run_delete(run_id: str):
        init_benchmark_db()
        if not delete_run(run_id):
            return {"status": "error", "error": "run 不存在"}
        return {"status": "success"}

    @router.post("/benchmark/preview")
    async def benchmark_preview(
        camera_id: str = Form(...),
        file: UploadFile = File(...),
    ):
        cid = str(camera_id or "").strip()
        if not cid:
            return {"status": "error", "error": "请选择摄像头标注"}
        ann = load_camera_annotation(cid, json_dir, default_json)
        if ann.get("error"):
            return {"status": "error", "error": ann.get("error", "标注不存在")}

        suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            with open(tmp.name, "wb") as out:
                shutil.copyfileobj(file.file, out)
            probe = _probe_video(tmp.name)
            if probe.get("error"):
                return {"status": "error", "error": probe["error"]}
            frame_resp = get_first_frame_b64(tmp.name, capture_height=None)
            if frame_resp.get("error"):
                return {"status": "error", "error": frame_resp["error"]}
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        ann_payload = annotation_payload_for_api(ann)
        ann_size = ann_payload.get("annotation_size") or {}
        aw = int(ann_size.get("width") or 0)
        ah = int(ann_size.get("height") or 0)
        vw = int(probe.get("width") or 0)
        vh = int(probe.get("height") or 0)
        size_match = aw > 0 and ah > 0 and vw == aw and vh == ah
        aspect_match = aw > 0 and ah > 0 and abs((vw / max(vh, 1)) - (aw / max(ah, 1))) < 0.02

        return {
            "status": "success",
            "camera_id": cid,
            "video": probe,
            "annotation_size": ann_size,
            "size_match": size_match,
            "aspect_match": aspect_match,
            "alignment_hint": (
                "分辨率与标注一致"
                if size_match
                else ("宽高比接近，坐标会缩放" if aspect_match else "分辨率与标注不一致，请确认机位或重新标定")
            ),
            "first_frame": frame_resp.get("image"),
            "annotation": ann_payload,
        }

    @router.post("/benchmark/runs")
    async def benchmark_create_run(
        camera_id: str = Form(...),
        file: UploadFile = File(...),
        title: str = Form(""),
        backend: str = Form(""),
        pose_frame_interval: int = Form(0),
        alarm_min_consecutive_frames: int = Form(0),
        alarm_cooldown_frames: int = Form(-1),
        start: bool = Form(True),
    ):
        init_benchmark_db()
        cid = str(camera_id or "").strip()
        if not cid:
            return {"status": "error", "error": "请选择摄像头标注"}

        ann = load_camera_annotation(cid, json_dir, default_json)
        if ann.get("error"):
            return {"status": "error", "error": ann.get("error", "标注不存在")}
        ann_path = str(ann.get("json_path") or camera_annotation_path(json_dir, cid))
        if not os.path.isfile(ann_path):
            return {"status": "error", "error": f"标注文件不存在: {ann_path}"}

        backend_id = str(backend or "").strip()
        if backend_id:
            try:
                backend_id = normalize_backend_setting(backend_id)
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}
        else:
            backend_id = resolve_model_preset(app_config).id

        suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            with open(tmp.name, "wb") as out:
                shutil.copyfileobj(file.file, out)
            probe = _probe_video(tmp.name)
            if probe.get("error"):
                return {"status": "error", "error": probe["error"]}

            ann_data = ann.get("data") if isinstance(ann.get("data"), dict) else {}
            ann_size = ann_data.get("annotation_size") if isinstance(ann_data, dict) else {}
            effective = _effective_inference_settings(app_config)
            try:
                interval_raw = int(pose_frame_interval or 0)
            except (TypeError, ValueError):
                interval_raw = 0
            if interval_raw > 0:
                pose_interval = max(1, min(120, interval_raw))
            else:
                infer_cfg = app_config.get("inference", {}) or {}
                pose_interval = max(
                    1,
                    int(effective.get("pose_frame_interval") or infer_cfg.get("pose_frame_interval", 3) or 3),
                )

            alarm_min_raw = int(alarm_min_consecutive_frames or 0)
            alarm_cooldown_raw = int(alarm_cooldown_frames if alarm_cooldown_frames is not None else -1)
            alarm_min_arg = alarm_min_raw if alarm_min_raw >= 1 else None
            alarm_cooldown_arg = alarm_cooldown_raw if alarm_cooldown_raw >= 0 else None

            run = create_run(
                camera_id=cid,
                title=title or (file.filename or f"bench-{cid}"),
                backend=backend_id,
                video_src_path=tmp.name,
                annotation_src_path=ann_path,
                video_width=int(probe.get("width") or 0),
                video_height=int(probe.get("height") or 0),
                video_fps=float(probe.get("fps") or 0),
                duration_sec=float(probe.get("duration_sec") or 0),
                annotation_width=int((ann_size or {}).get("width") or 0),
                annotation_height=int((ann_size or {}).get("height") or 0),
                config=_run_config_snapshot(
                    app_config,
                    pose_interval,
                    alarm_min=alarm_min_arg,
                    alarm_cooldown=alarm_cooldown_arg,
                ),
            )
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        if start:
            _spawn_benchmark_runner(run["id"])

        return {"status": "success", "run": run, "started": bool(start)}

    @router.post("/benchmark/runs/{run_id}/rerun")
    async def benchmark_rerun_run(run_id: str, body: BenchmarkRerunBody | None = None):
        init_benchmark_db()
        run = get_run(run_id)
        if not run:
            return {"status": "error", "error": "run 不存在"}
        if run.get("status") == "running":
            return {"status": "error", "error": "任务运行中，请稍后再试"}
        video_path = run_video_path(run_id)
        ann_path = str(run.get("annotation_path") or "")
        if not os.path.isfile(video_path):
            return {"status": "error", "error": "视频文件不存在，无法重跑"}
        if not ann_path or not os.path.isfile(ann_path):
            return {"status": "error", "error": "标注快照不存在，无法重跑"}

        req = body or BenchmarkRerunBody()
        backend_id = str(req.backend or run.get("backend") or "").strip()
        if not backend_id:
            effective = _effective_inference_settings(app_config)
            backend_id = str(effective.get("backend") or "").strip()
        if backend_id:
            try:
                backend_id = normalize_backend_setting(backend_id)
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        if int(req.pose_frame_interval or 0) > 0:
            pose_interval = max(1, min(120, int(req.pose_frame_interval)))
        else:
            run_cfg = run.get("config") if isinstance(run.get("config"), dict) else {}
            pose_interval = max(
                1,
                int(run_cfg.get("inference.pose_frame_interval") or 1),
            )

        run_cfg = run.get("config") if isinstance(run.get("config"), dict) else {}
        if int(req.alarm_min_consecutive_frames or 0) >= 1:
            alarm_min_arg = int(req.alarm_min_consecutive_frames)
        elif run_cfg.get("inference.alarm_min_consecutive_frames") is not None:
            alarm_min_arg = int(run_cfg["inference.alarm_min_consecutive_frames"])
        else:
            alarm_min_arg = None

        if int(req.alarm_cooldown_frames) >= 0:
            alarm_cooldown_arg = int(req.alarm_cooldown_frames)
        elif run_cfg.get("inference.alarm_cooldown_frames") is not None:
            alarm_cooldown_arg = int(run_cfg["inference.alarm_cooldown_frames"])
        else:
            alarm_cooldown_arg = None

        if not update_run_inference_params(
            run_id,
            backend=backend_id,
            config=_run_config_snapshot(
                app_config,
                pose_interval,
                alarm_min=alarm_min_arg,
                alarm_cooldown=alarm_cooldown_arg,
            ),
        ):
            return {"status": "error", "error": "更新评测参数失败"}
        if not reset_run_for_rerun(run_id):
            return {"status": "error", "error": "重置任务失败"}
        _spawn_benchmark_runner(run_id)
        run = get_run(run_id)
        return {
            "status": "success",
            "run_id": run_id,
            "run": run,
            "message": "已按指定参数重新启动评测",
        }

    @router.post("/benchmark/runs/{run_id}/start")
    async def benchmark_start_run(run_id: str):
        init_benchmark_db()
        run = get_run(run_id)
        if not run:
            return {"status": "error", "error": "run 不存在"}
        if run.get("status") == "running":
            return {"status": "success", "message": "已在运行"}
        _spawn_benchmark_runner(run_id)
        return {"status": "success", "run_id": run_id}

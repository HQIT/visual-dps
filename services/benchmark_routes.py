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
from fastapi.responses import FileResponse

from core.config import load_app_config
from services.annotation_service import (
    annotation_payload_for_api,
    camera_annotation_path,
    load_camera_annotation,
)
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
                config={
                    "inference.frame_rate": effective.get("frame_rate"),
                    "inference.height": effective.get("height"),
                    "inference.pose_frame_interval": pose_interval,
                },
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
    async def benchmark_rerun_run(run_id: str):
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
        effective = _effective_inference_settings(app_config)
        backend_id = str(effective.get("backend") or run.get("backend") or "").strip()
        if backend_id:
            try:
                backend_id = normalize_backend_setting(backend_id)
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}
        pose_interval = max(1, int(effective.get("pose_frame_interval") or 1))
        if not update_run_inference_params(
            run_id,
            backend=backend_id,
            config={
                "inference.frame_rate": effective.get("frame_rate"),
                "inference.height": effective.get("height"),
                "inference.pose_frame_interval": pose_interval,
            },
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
            "message": "已按当前参数重新启动评测",
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

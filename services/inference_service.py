"""检测 / 姿态推理流程以及 WebSocket 推流逻辑。"""

import asyncio
import base64
import json
import os
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import cv2
import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

try:
    import psutil
except Exception:
    psutil = None

try:
    import torch
except Exception:
    torch = None

from services.event_bus import get_event_snapshot
from services.inference_backends import create_inference_backend, resolve_backend_name
from services.pipeline_log import get_inference_logger, log_pipeline_stage, reload_process_logging
from services.rtsp_capture import open_rtsp_capture, read_latest_frame


def _snapshot_stream_frame(cap):
    """取 RTSP 最新帧快照（副本）；后台线程持续刷新缓冲。"""
    return read_latest_frame(cap)


def _parse_cuda_device_index(device_name: str) -> int:
    name = (device_name or "").strip().lower()
    if name.startswith("cuda:"):
        try:
            return int(name.split(":", 1)[1])
        except Exception:
            return 0
    return 0


def _collect_resource_debug_line(device_name: str) -> str:
    parts = []

    if psutil is not None:
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            parts.append(f"cpu={cpu_percent:.1f}%")
        except Exception:
            parts.append("cpu=n/a")

        try:
            proc_mem_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
            parts.append(f"proc_mem={proc_mem_mb:.1f}MB")
        except Exception:
            parts.append("proc_mem=n/a")

        try:
            sys_mem_percent = psutil.virtual_memory().percent
            parts.append(f"sys_mem={sys_mem_percent:.1f}%")
        except Exception:
            parts.append("sys_mem=n/a")
    else:
        parts.append("cpu=n/a(psutil_missing)")

    gpu_info_ready = False
    if torch is not None and torch.cuda.is_available():
        gpu_idx = _parse_cuda_device_index(device_name)
        try:
            alloc_mb = torch.cuda.memory_allocated(gpu_idx) / (1024 * 1024)
            reserved_mb = torch.cuda.memory_reserved(gpu_idx) / (1024 * 1024)
            parts.append(f"gpu_mem_alloc={alloc_mb:.1f}MB")
            parts.append(f"gpu_mem_reserved={reserved_mb:.1f}MB")
            gpu_info_ready = True
        except Exception:
            pass

        try:
            smi = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                    "-i",
                    str(gpu_idx),
                ],
                capture_output=True,
                text=True,
                timeout=0.4,
                check=False,
            )
            if smi.returncode == 0 and smi.stdout.strip():
                cols = [c.strip() for c in smi.stdout.strip().splitlines()[0].split(",")]
                if len(cols) >= 3:
                    parts.append(f"gpu_util={cols[0]}%")
                    parts.append(f"gpu_mem={cols[1]}/{cols[2]}MB")
                    gpu_info_ready = True
        except Exception:
            pass

    if not gpu_info_ready:
        parts.append("gpu=n/a")

    return " ".join(parts)


def _compute_infer_resolution(source_w: int, source_h: int, target_height: int):
    target_h = max(120, int(target_height))
    if source_h <= target_h:
        infer_w = source_w
        infer_h = source_h
        resize_needed = False
    else:
        infer_h = target_h
        infer_w = int(round(source_w * (infer_h / float(source_h))))
        infer_w = max(2, infer_w - (infer_w % 2))
        infer_h = max(2, infer_h - (infer_h % 2))
        resize_needed = True
    return infer_w, infer_h, resize_needed


class InferenceService:
    def __init__(self, app_config: dict, state, callback_reporter=None):
        self.app_config = app_config
        self.state = state
        # callback_reporter 已迁至 event worker；保留参数避免旧调用方报错
        _ = callback_reporter
        self._perception_backend = None
        self._background_task = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")
        self._runtime_config_path = os.environ.get("RUNTIME_CONFIG_FILE", "localdata/runtime_config.json")
        self._runtime_config_mtime: float | None = None

    def debug_visualization_enabled(self) -> bool:
        debug_cfg = self.app_config.get("debug-info", {})
        return isinstance(debug_cfg, dict) and bool(debug_cfg.get("enabled", False))

    def _refresh_pipeline_log_if_needed(self) -> None:
        path = self._runtime_config_path
        try:
            mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
        except OSError:
            mtime = 0.0
        if self._runtime_config_mtime == mtime:
            return
        self._runtime_config_mtime = mtime
        reload_process_logging(self.app_config)

    def _backend(self):
        if self._perception_backend is None:
            backend_name = resolve_backend_name(self.app_config)
            get_inference_logger().info(f"ℹ️ 推理后端: {backend_name}")
            self._perception_backend = create_inference_backend(self.app_config, self._executor)
        return self._perception_backend

    def ensure_models_loaded(self):
        self._backend().ensure_loaded()

    async def _run_detection(self, frame):
        return await self._backend().detect_bboxes(frame)

    async def _run_pose(self, frame, bboxes):
        return await self._backend().estimate_pose(frame, bboxes)

    async def start_inference(self):
        self.ensure_models_loaded()
        if self.state.is_inferencing:
            return {"status": "success", "mode": "running"}

        self.state.is_inferencing = True

        if self.state.source_type == "stream":
            if self._background_task is None or self._background_task.done():
                self._background_task = asyncio.create_task(self.websocket_inference(_NullWebSocket()))
            return {"status": "success", "mode": "headless"}

        return {"status": "success", "mode": "visual"}

    async def websocket_inference(self, websocket: WebSocket):
        is_null_ws = isinstance(websocket, _NullWebSocket)
        visualization_enabled = self.debug_visualization_enabled() and (not is_null_ws)

        if (not is_null_ws) and self._background_task is not None and (not self._background_task.done()):
            self.state.is_inferencing = False
            try:
                await asyncio.wait_for(self._background_task, timeout=2.0)
            except Exception:
                self._background_task.cancel()
            finally:
                self._background_task = None
            self.state.is_inferencing = True

        if not visualization_enabled and (not is_null_ws):
            await websocket.accept()
            await websocket.send_json({
                "status": "debug-visual-disabled",
                "message": "debug-info.enabled=false，仅执行后台推理与回调，不推送前端可视化帧",
            })
            await websocket.close()
            return

        await websocket.accept()
        cap = None
        infer_log = get_inference_logger()

        if self.state.source_type == "stream" and visualization_enabled and not self.state.is_inferencing:
            self.ensure_models_loaded()
            self.state.is_inferencing = True
            infer_log.info("✅ 已在网络流模式自动启动推理会话")

        json_file_path = self.state.json_path or self.app_config["paths"]["default_json_file"]

        if not os.path.exists(json_file_path):
            infer_log.warning(f"⚠️ [警告] 无法启动推理：未找到配置文件 {json_file_path}，请先完成标注！")
            await websocket.close()
            return

        with open(json_file_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)

        source_info = config_data.get("source_info", {}) if isinstance(config_data, dict) else {}
        if not isinstance(source_info, dict):
            source_info = {}
        if self.state.source_type == "stream":
            marked_camera_url = str(source_info.get("camera_url", "") or "").strip()
            if marked_camera_url and marked_camera_url != (self.state.source_url or ""):
                infer_log.warning(
                    "⚠️ [警告] 当前流地址与标注来源摄像头不一致: "
                    f"stream={self.state.source_url} annotation_camera={marked_camera_url}"
                )

        is_stream = self.state.source_type == "stream"
        if is_stream:
            stream_buffer_size = int(self.app_config["inference"].get("stream_buffer_size", 1))
            cap = open_rtsp_capture(self.state.video_path, buffer_size=stream_buffer_size)
            infer_log.info("ℹ️ RTSP 采帧：后台线程刷新最新帧，推理仅消费快照副本")
        else:
            cap = cv2.VideoCapture(self.state.video_path)

        video_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        stream_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        stream_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if stream_w <= 0 or stream_h <= 0:
            infer_log.warning("⚠️ [警告] 无法读取当前视频分辨率，停止本次推理")
            self.state.is_inferencing = False
            await websocket.close()
            if cap is not None:
                cap.release()
            return

        infer_cfg = self.app_config.get("inference", {})
        infer_w, infer_h, resize_needed = _compute_infer_resolution(
            stream_w,
            stream_h,
            int(infer_cfg.get("height", 480) or 480),
        )

        frame_rate = float(infer_cfg.get("frame_rate", 15) or 15)
        frame_rate = max(1.0, frame_rate)
        frame_period_sec = 1.0 / frame_rate

        pose_frame_interval = int(infer_cfg.get("pose_frame_interval", 3) or 3)
        pose_frame_interval = max(1, pose_frame_interval)

        preview_max_width = int(infer_cfg.get("preview_max_width", 640) or 640)
        preview_jpeg_quality = int(infer_cfg.get("preview_jpeg_quality", 60) or 60)

        debug_cfg = self.app_config.get("debug-info", {})
        if not isinstance(debug_cfg, dict):
            debug_cfg = {}
        debug_enabled = bool(debug_cfg.get("enabled", False))
        try:
            debug_interval_frames = int(debug_cfg.get("interval_frames", 30))
        except Exception:
            debug_interval_frames = 30
        debug_interval_frames = max(1, debug_interval_frames)

        infer_log.info(
            f"ℹ️ 推理参数: source={stream_w}x{stream_h} height={infer_h} "
            f"frame_rate={frame_rate} pose_frame_interval={pose_frame_interval} "
            f"resize={'on' if resize_needed else 'off'}"
        )

        start_time = time.time()
        frame_count = 0
        inference_tick = 0
        last_frame_started_at = time.monotonic()
        last_config_check_at = time.monotonic()

        cached_bboxes = np.empty((0, 4), dtype=np.float32)
        cached_skeletons_data = []
        cached_collisions = []
        cached_alarm_collisions = []
        inference_camera_id = os.environ.get("INFERENCE_CAMERA_ID", "").strip()
        headless_stream = is_stream and is_null_ws
        infer_run_id = uuid.uuid4().hex[:12]

        try:
            while cap.isOpened() and self.state.is_inferencing:
                loop_started_at = time.monotonic()
                run_pose = (inference_tick % pose_frame_interval == 0)

                if headless_stream and not run_pose:
                    inference_tick += 1
                    elapsed_skip = time.monotonic() - loop_started_at
                    sleep_skip = frame_period_sec - elapsed_skip
                    if sleep_skip > 0:
                        await asyncio.sleep(sleep_skip)
                    continue

                captured_at = 0.0
                if is_stream:
                    ret, frame, captured_at = await asyncio.get_running_loop().run_in_executor(
                        self._executor, _snapshot_stream_frame, cap
                    )
                else:
                    ret, frame = await asyncio.get_running_loop().run_in_executor(
                        self._executor, cap.read
                    )

                inference_tick += 1

                if not ret or frame is None:
                    if is_stream:
                        elapsed_wait = time.monotonic() - loop_started_at
                        sleep_wait = frame_period_sec - elapsed_wait
                        if sleep_wait > 0:
                            await asyncio.sleep(sleep_wait)
                        continue
                    infer_log.info("✅ 视频推理完成，停止当前会话")
                    self.state.is_inferencing = False
                    break

                frame_count += 1
                if frame_count == 1 or frame_count % 300 == 0 or (time.monotonic() - last_config_check_at) >= 30.0:
                    last_config_check_at = time.monotonic()
                    self._refresh_pipeline_log_if_needed()
                if resize_needed:
                    raw_frame = cv2.resize(frame, (infer_w, infer_h), interpolation=cv2.INTER_AREA)
                else:
                    raw_frame = frame

                det_ms: float | None = None
                pose_ms: float | None = None
                if run_pose or not headless_stream:
                    det_started = time.monotonic()
                    cached_bboxes = await self._run_detection(raw_frame)
                    det_ms = round((time.monotonic() - det_started) * 1000.0, 1)

                if run_pose:
                    if headless_stream and inference_camera_id:
                        log_pipeline_stage(
                            "rtsp_frame",
                            camera_id=inference_camera_id,
                            frame_idx=frame_count,
                            run_id=infer_run_id,
                            captured_at=round(captured_at, 3) if captured_at > 0 else None,
                        )

                    skeletons_data = []

                    if len(cached_bboxes) > 0:
                        pose_started = time.monotonic()
                        pose_batch = await self._run_pose(raw_frame, cached_bboxes)
                        pose_ms = round((time.monotonic() - pose_started) * 1000.0, 1)
                        kpts_all = pose_batch.keypoints
                        scores_all = pose_batch.keypoint_scores

                        for p_idx in range(pose_batch.num_persons):
                            person_pts = []
                            for k in range(kpts_all.shape[1]):
                                person_pts.append([
                                    float(kpts_all[p_idx][k][0]),
                                    float(kpts_all[p_idx][k][1]),
                                    float(scores_all[p_idx][k]),
                                ])
                            skeletons_data.append({
                                "person_id": p_idx,
                                "keypoints": person_pts,
                            })

                    cached_skeletons_data = skeletons_data

                    if is_null_ws and inference_camera_id:
                        from services.pose_bus import publish_pose_frame

                        log_pipeline_stage(
                            "infer_pose_done",
                            camera_id=inference_camera_id,
                            frame_idx=frame_count,
                            run_id=infer_run_id,
                            persons=len(skeletons_data),
                            det_ms=det_ms,
                            pose_ms=pose_ms,
                        )
                        publish_pose_frame(
                            inference_camera_id,
                            frame_idx=frame_count,
                            persons=skeletons_data,
                            infer_width=infer_w,
                            infer_height=infer_h,
                            run_id=infer_run_id,
                        )

                skeletons_data = cached_skeletons_data
                event_snap = get_event_snapshot(inference_camera_id) if inference_camera_id else None
                if event_snap:
                    active_collisions = list(event_snap.get("collisions") or [])
                    alarm_collisions = list(event_snap.get("alarm_collisions") or [])
                    if event_snap.get("skeletons"):
                        skeletons_data = list(event_snap.get("skeletons") or skeletons_data)
                    cached_collisions = active_collisions
                    cached_alarm_collisions = alarm_collisions
                else:
                    active_collisions = cached_collisions
                    alarm_collisions = cached_alarm_collisions
                report_event_ids = []

                elapsed = time.time() - start_time
                current_fps = frame_count / elapsed if elapsed > 0 else 0

                video_time_sec = frame_count / video_fps
                m, s = int(video_time_sec // 60), int(video_time_sec % 60)
                ms = int((video_time_sec - int(video_time_sec)) * 1000)
                formatted_time = f"{m:02d}:{s:02d}.{ms:03d}"

                if debug_enabled and (frame_count % debug_interval_frames == 0):
                    resource_line = _collect_resource_debug_line(self.app_config["models"].get("device", ""))
                    infer_log.info(
                        f"[DEBUG-INFO] frame={frame_count} fps={round(current_fps, 1)} "
                        f"video_time={formatted_time} {resource_line}"
                    )

                if visualization_enabled:
                    target_w = min(preview_max_width, infer_w)
                    if target_w < infer_w:
                        target_h = int(infer_h * (target_w / infer_w))
                        frame_for_encode = cv2.resize(raw_frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
                    else:
                        frame_for_encode = raw_frame

                    _, buffer = cv2.imencode(
                        ".jpg",
                        frame_for_encode,
                        [int(cv2.IMWRITE_JPEG_QUALITY), preview_jpeg_quality],
                    )
                    b64_str = base64.b64encode(buffer).decode("utf-8")

                    payload = {
                        "image": b64_str,
                        "orig_width": infer_w,
                        "skeletons": skeletons_data,
                        "collisions": active_collisions,
                        "alarm_collisions": alarm_collisions,
                        "callback_event_ids": [],
                        "annotation_source": source_info,
                        "stats": {
                            "fps": round(current_fps, 1),
                            "video_time": formatted_time,
                            "frame_idx": frame_count,
                        },
                    }

                    await websocket.send_json(payload)

                elapsed_loop = time.monotonic() - loop_started_at
                # headless：降频由 skip tick 的 frame_period sleep 承担；pose tick 只睡 1 个 tick，
                # 勿再乘 pose_frame_interval（否则与 skip sleep 双重 pacing）。
                sleep_sec = frame_period_sec - elapsed_loop
                if sleep_sec > 0:
                    await asyncio.sleep(sleep_sec)

        except WebSocketDisconnect:
            if not is_null_ws:
                infer_log.info("前端连接断开")
        finally:
            self.state.is_inferencing = False
            if cap is not None:
                cap.release()

            if (not is_null_ws) and self.state.source_type == "stream":
                self.state.is_inferencing = True
                if self._background_task is None or self._background_task.done():
                    self._background_task = asyncio.create_task(self.websocket_inference(_NullWebSocket()))


class _NullWebSocket:
    async def accept(self):
        return

    async def send_json(self, _payload):
        return

    async def close(self):
        return

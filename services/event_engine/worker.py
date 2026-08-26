"""Redis 姿态消费 → 碰撞事件 → 发布 event + Java 回调。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis

from services.annotation_service import camera_annotation_path
from services.box_identity import parse_collision_token
from services.event_bus import publish_event_frame
from services.event_engine.annotation_boxes import load_scaled_boxes
from services.event_engine.collision import CollisionProcessor
from services.event_engine.pick_prefilter.service import PickPrefilterGate
from services.event_engine.event_log import (
    PrefilterLogEntry,
    collision_log_enabled,
    event_log_context_from_pose,
    format_event_log_line,
    prefilter_log_enabled,
)
from services.event_engine.sharding import owns_camera, shard_label, worker_owned_stream_keys
from services.pipeline_log import (
    get_collision_logger,
    get_prefilter_logger,
    log_pipeline_stage,
    reload_process_logging,
)
from services.pose_bus import (
    POSE_CHANNEL_PREFIX,
    POSE_STREAM_GROUP,
    default_consumer_name,
    ensure_pose_stream_groups,
    pose_delivery_mode,
    redis_url,
)
from services.runtime_config_service import (
    DEFAULT_PATH,
    get_collision_prefilter_section,
    get_merged_inference_section,
)

logger = logging.getLogger(__name__)


class _CameraContext:
    def __init__(
        self,
        json_path: str,
        json_mtime: float = 0.0,
        processor: CollisionProcessor | None = None,
        prefilter: PickPrefilterGate | None = None,
        infer_w: int = 0,
        infer_h: int = 0,
    ):
        self.json_path = json_path
        self.json_mtime = json_mtime
        self.processor = processor
        self.prefilter = prefilter
        self.infer_w = infer_w
        self.infer_h = infer_h
        self.last_frame_idx = -1


class EventRedisWorker:
    def __init__(self, app_config: dict, callback_reporter=None):
        self.app_config = app_config
        self.callback_reporter = callback_reporter
        self._json_dir = (
            os.environ.get("JSON_DIR", "").strip()
            or str(app_config.get("paths", {}).get("json_dir", "localdata/json"))
        )
        self._runtime_config_path = os.environ.get("RUNTIME_CONFIG_FILE", DEFAULT_PATH)
        self._runtime_settings_mtime: float | None = None
        self._contexts: dict[str, _CameraContext] = {}
        infer_cfg = get_merged_inference_section(app_config, self._runtime_config_path)
        self._apply_runtime_settings(infer_cfg)
        try:
            self._runtime_settings_mtime = (
                os.path.getmtime(self._runtime_config_path)
                if os.path.isfile(self._runtime_config_path)
                else 0.0
            )
        except OSError:
            self._runtime_settings_mtime = 0.0
        self._delivery = pose_delivery_mode()
        self._owned_stream_keys = worker_owned_stream_keys()
        self._consumer_name = default_consumer_name()
        self._listener_task: asyncio.Task | None = None
        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None

    def _apply_runtime_settings(self, infer_cfg: dict) -> None:
        self._alarm_min = max(1, int(infer_cfg.get("alarm_min_consecutive_frames", 3) or 3))
        if "alarm_cooldown_frames" in infer_cfg:
            self._alarm_cooldown = max(0, int(infer_cfg["alarm_cooldown_frames"]))
        else:
            self._alarm_cooldown = 12
        self._video_fps = float(infer_cfg.get("frame_rate", 15) or 15)
        self._pose_frame_interval = max(1, int(infer_cfg.get("pose_frame_interval", 1) or 1))
        self._prefilter_section = get_collision_prefilter_section(
            self.app_config,
            self._runtime_config_path,
        )
        for ctx in self._contexts.values():
            proc = ctx.processor
            if proc is not None:
                proc.alarm_min_consecutive_frames = self._alarm_min
                proc.alarm_cooldown_frames = self._alarm_cooldown
                proc.video_fps = self._video_fps
            self._sync_prefilter(ctx)

    def _sync_prefilter(self, ctx: _CameraContext) -> None:
        section = self._prefilter_section
        if not section.get("enabled"):
            ctx.prefilter = None
            return
        if ctx.prefilter is None:
            ctx.prefilter = PickPrefilterGate.from_config(
                section,
                infer_width=ctx.infer_w,
                infer_height=ctx.infer_h,
                video_fps=self._video_fps,
                pose_frame_interval=self._pose_frame_interval,
            )
            return
        ctx.prefilter.apply_config(
            section,
            infer_width=ctx.infer_w,
            infer_height=ctx.infer_h,
            video_fps=self._video_fps,
            pose_frame_interval=self._pose_frame_interval,
        )

    def _refresh_runtime_settings_if_needed(self) -> None:
        path = self._runtime_config_path
        try:
            mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
        except OSError:
            mtime = 0.0
        if self._runtime_settings_mtime == mtime:
            return
        self._runtime_settings_mtime = mtime
        infer_cfg = get_merged_inference_section(self.app_config, path)
        self._apply_runtime_settings(infer_cfg)
        reload_process_logging(self.app_config)

    def _resolve_json_path(self, camera_id: str) -> str:
        rel = camera_annotation_path(self._json_dir, camera_id)
        if rel.startswith("/"):
            return rel
        base = os.environ.get("HOST_PROJECT_ROOT", "").strip()
        if base:
            host = os.path.abspath(os.path.join(base, rel))
            if os.path.isfile(host):
                return host
        if os.path.isfile(rel):
            return os.path.abspath(rel)
        return rel

    def _get_processor(self, camera_id: str, infer_w: int, infer_h: int) -> CollisionProcessor | None:
        self._refresh_runtime_settings_if_needed()
        json_path = self._resolve_json_path(camera_id)
        ctx = self._contexts.get(camera_id)
        mtime = os.path.getmtime(json_path) if os.path.isfile(json_path) else 0.0

        if (
            ctx is None
            or ctx.json_path != json_path
            or ctx.json_mtime != mtime
            or ctx.infer_w != infer_w
            or ctx.infer_h != infer_h
        ):
            boxes = load_scaled_boxes(json_path, infer_w, infer_h) if infer_w > 0 and infer_h > 0 else []
            if not boxes:
                logger.warning("event worker: no boxes for camera=%s path=%s", camera_id, json_path)
            processor = CollisionProcessor(
                boxes,
                alarm_min_consecutive_frames=self._alarm_min,
                alarm_cooldown_frames=self._alarm_cooldown,
                video_fps=self._video_fps,
            )
            prefilter = None
            if self._prefilter_section.get("enabled"):
                prefilter = PickPrefilterGate.from_config(
                    self._prefilter_section,
                    infer_width=infer_w,
                    infer_height=infer_h,
                    video_fps=self._video_fps,
                    pose_frame_interval=self._pose_frame_interval,
                )
            ctx = _CameraContext(
                json_path=json_path,
                json_mtime=mtime,
                processor=processor,
                prefilter=prefilter,
                infer_w=infer_w,
                infer_h=infer_h,
            )
            self._contexts[camera_id] = ctx
        else:
            self._sync_prefilter(ctx)

        return ctx.processor

    def _maybe_reset_on_frame_regression(
        self,
        camera_id: str,
        ctx: _CameraContext,
        frame_idx: int,
    ) -> None:
        """方案 A：frame_idx 回退视为 infer 重启，清空碰撞会话状态。"""
        if ctx.last_frame_idx < 0 or frame_idx >= ctx.last_frame_idx:
            return
        proc = ctx.processor
        if proc is None:
            return
        proc.reset_infer_session()
        if ctx.prefilter is not None:
            ctx.prefilter.reset_session()
        logger.info(
            "event worker: infer frame_idx regression camera=%s frame=%s last=%s; reset collision session",
            camera_id,
            frame_idx,
            ctx.last_frame_idx,
        )

    def _log_event_frame(
        self,
        pose: dict,
        collisions: list,
        alarm_collisions: list,
        prefilter_logs: list[PrefilterLogEntry] | None = None,
    ) -> None:
        if not collision_log_enabled() and not prefilter_log_enabled():
            return

        ctx = event_log_context_from_pose(pose, self._video_fps)

        if prefilter_log_enabled():
            prefilter_logger = get_prefilter_logger()
            for entry in prefilter_logs or []:
                decision = entry.decision
                tag = "FILTERED" if decision.blocked else "PASS"
                prefilter_logger.info(
                    format_event_log_line(
                        "PREFILTER",
                        tag,
                        ctx,
                        track=decision.track_id,
                        hits=entry.hits,
                        alarms=[],
                        speed_feature=decision.speed_feature,
                        speed_value=decision.speed_value,
                        speed_threshold=decision.speed_threshold,
                        ankle_max_speed=decision.ankle_max_speed,
                        ankle_max_speed_norm=decision.ankle_max_speed_norm,
                        filtered=decision.blocked,
                    )
                )

        if not collision_log_enabled():
            return

        collision_logger = get_collision_logger()
        if collisions:
            collision_logger.info(
                format_event_log_line(
                    "COLLISION",
                    "HIT",
                    ctx,
                    hits=collisions,
                    alarms=[],
                )
            )
        if alarm_collisions:
            collision_logger.info(
                format_event_log_line(
                    "COLLISION",
                    "ALARM",
                    ctx,
                    hits=collisions,
                    alarms=alarm_collisions,
                )
            )

    async def start(self) -> None:
        if self._listener_task and not self._listener_task.done():
            return
        if self._delivery == "stream":
            self._listener_task = asyncio.create_task(self._stream_loop(), name="event-redis-stream")
        else:
            self._listener_task = asyncio.create_task(self._pubsub_loop(), name="event-redis-pubsub")

    async def stop(self) -> None:
        task = self._listener_task
        self._listener_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._pubsub is not None:
            try:
                await self._pubsub.close()
            except Exception:
                pass
            self._pubsub = None
        if self._redis is not None:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None

    async def _stream_loop(self) -> None:
        block_ms = max(500, int(os.environ.get("POSE_STREAM_BLOCK_MS", "2000")))
        stream_keys = self._owned_stream_keys
        if not stream_keys:
            logger.error("EventRedisWorker: no owned pose streams configured")
            return
        while True:
            try:
                await asyncio.to_thread(ensure_pose_stream_groups, stream_keys)
                self._redis = aioredis.from_url(redis_url(), decode_responses=True)
                logger.info(
                    "EventRedisWorker stream consumer=%s group=%s streams=%s (%s)",
                    self._consumer_name,
                    POSE_STREAM_GROUP,
                    stream_keys,
                    shard_label(),
                )
                read_streams = {key: ">" for key in stream_keys}
                while True:
                    messages = await self._redis.xreadgroup(
                        POSE_STREAM_GROUP,
                        self._consumer_name,
                        read_streams,
                        count=1,
                        block=block_ms,
                    )
                    if not messages:
                        continue
                    for stream_name, items in messages:
                        for msg_id, fields in items:
                            payload = fields.get("payload") if isinstance(fields, dict) else None
                            if payload:
                                await self._handle_pose_payload(payload)
                            await self._redis.xack(stream_name, POSE_STREAM_GROUP, msg_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("EventRedisWorker stream error: %s; retry in 2s", exc)
                await asyncio.sleep(2)
            finally:
                if self._redis is not None:
                    try:
                        await self._redis.close()
                    except Exception:
                        pass
                    self._redis = None

    async def _pubsub_loop(self) -> None:
        pattern = f"{POSE_CHANNEL_PREFIX}*"
        while True:
            try:
                self._redis = aioredis.from_url(redis_url(), decode_responses=True)
                self._pubsub = self._redis.pubsub()
                await self._pubsub.psubscribe(pattern)
                logger.info(
                    "EventRedisWorker pubsub %s (%s)",
                    pattern,
                    shard_label(),
                )
                async for message in self._pubsub.listen():
                    if message.get("type") != "pmessage":
                        continue
                    payload = message.get("data")
                    if payload:
                        await self._handle_pose_payload(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("EventRedisWorker pubsub error: %s; retry in 2s", exc)
                await asyncio.sleep(2)
            finally:
                if self._pubsub is not None:
                    try:
                        await self._pubsub.close()
                    except Exception:
                        pass
                    self._pubsub = None
                if self._redis is not None:
                    try:
                        await self._redis.close()
                    except Exception:
                        pass
                    self._redis = None

    async def _handle_pose_payload(self, payload: str) -> None:
        try:
            pose = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(pose, dict) or pose.get("kind") != "pose":
            return

        camera_id = str(pose.get("camera_id") or "").strip()
        if not camera_id:
            return
        if not owns_camera(camera_id):
            return

        infer_w = int(pose.get("infer_width") or 0)
        infer_h = int(pose.get("infer_height") or 0)
        frame_idx = int(pose.get("frame_idx") or 0)
        run_id = str(pose.get("run_id") or "").strip()
        log_pipeline_stage(
            "worker_received",
            camera_id=camera_id,
            frame_idx=frame_idx,
            run_id=run_id or None,
            persons=len(pose.get("persons") or []),
        )
        processor = self._get_processor(camera_id, infer_w, infer_h)
        ctx = self._contexts.get(camera_id)
        if processor is None or ctx is None:
            return

        self._maybe_reset_on_frame_regression(camera_id, ctx, frame_idx)
        ctx.last_frame_idx = frame_idx

        worker_started = time.monotonic()
        result = await asyncio.to_thread(processor.process, pose, ctx.prefilter)
        worker_ms = round((time.monotonic() - worker_started) * 1000.0, 1)
        frame_idx = int(result.get("frame_idx") or pose.get("frame_idx") or 0)
        collisions = result.get("collisions") or []
        alarm_collisions = result.get("alarm_collisions") or []
        prefilter_logs = result.get("prefilter_logs") or []
        skeletons = result.get("skeletons")

        log_pipeline_stage(
            "worker_done",
            camera_id=camera_id,
            frame_idx=frame_idx,
            run_id=run_id or None,
            worker_ms=worker_ms,
            hits=len(collisions),
            alarms=len(alarm_collisions),
        )

        self._log_event_frame(pose, collisions, alarm_collisions, prefilter_logs)

        published = await asyncio.to_thread(
            publish_event_frame,
            camera_id,
            frame_idx=frame_idx,
            collisions=collisions,
            alarm_collisions=alarm_collisions,
            skeletons=skeletons,
        )
        log_pipeline_stage(
            "event_published",
            camera_id=camera_id,
            frame_idx=frame_idx,
            run_id=run_id or None,
            published=published,
            hits=len(collisions),
            alarms=len(alarm_collisions),
        )

        if self.callback_reporter and alarm_collisions:
            upload_tag = f"infer_{camera_id}"
            video_time_sec = frame_idx / self._video_fps
            for collision in alarm_collisions:
                shelf_code, box_id = parse_collision_token(collision)
                if not box_id:
                    continue
                log_pipeline_stage(
                    "callback_enqueued",
                    camera_id=camera_id,
                    frame_idx=frame_idx,
                    run_id=run_id or None,
                    box_id=box_id,
                    collision=collision,
                    sample=False,
                )
                self.callback_reporter.enqueue_pick_finished(
                    box_id=box_id,
                    frame_idx=frame_idx,
                    video_time_sec=video_time_sec,
                    upload_tag=upload_tag,
                    shelf_code=shelf_code or None,
                )

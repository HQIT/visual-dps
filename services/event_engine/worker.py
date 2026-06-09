"""Redis 姿态消费 → 碰撞事件 → 发布 event + Java 回调。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis

from services.annotation_service import camera_annotation_path
from services.runtime_config_service import DEFAULT_PATH, get_merged_inference_config, runtime_config_mtime
from services.box_identity import parse_collision_token
from services.event_bus import publish_event_frame
from services.event_engine.annotation_boxes import load_scaled_boxes
from services.event_engine.collision import CollisionParams, CollisionProcessor
from services.event_engine.sharding import owns_camera, shard_config, shard_label
from services.pose_bus import (
    POSE_CHANNEL_PREFIX,
    POSE_STREAM_GROUP,
    POSE_STREAM_KEY,
    default_consumer_name,
    ensure_pose_stream_group,
    pose_delivery_mode,
    redis_url,
)

logger = logging.getLogger(__name__)


class _CameraContext:
    def __init__(
        self,
        json_path: str,
        json_mtime: float = 0.0,
        processor: CollisionProcessor | None = None,
        infer_w: int = 0,
        infer_h: int = 0,
    ):
        self.json_path = json_path
        self.json_mtime = json_mtime
        self.processor = processor
        self.infer_w = infer_w
        self.infer_h = infer_h
        self.last_stat_ts = 0.0


class EventRedisWorker:
    def __init__(self, app_config: dict, callback_reporter=None):
        self.app_config = app_config
        self.callback_reporter = callback_reporter
        self._json_dir = (
            os.environ.get("JSON_DIR", "").strip()
            or str(app_config.get("paths", {}).get("json_dir", "localdata/json"))
        )
        infer_cfg = app_config.get("inference", {}) or {}
        self._runtime_config_path = os.environ.get("RUNTIME_CONFIG_FILE", DEFAULT_PATH)
        self._runtime_config_mtime = 0.0
        self._collision_params = CollisionParams.from_config(
            get_merged_inference_config(app_config, self._runtime_config_path)
        )
        self._runtime_config_mtime = runtime_config_mtime(self._runtime_config_path)
        self._video_fps = float(infer_cfg.get("frame_rate", 15) or 15)
        self._json_stat_interval = max(0.0, float(os.environ.get("JSON_STAT_INTERVAL_SEC", "1.5") or 1.5))
        self._delivery = pose_delivery_mode()
        self._shard_count, self._shard_index = shard_config()
        self._consumer_name = default_consumer_name()
        self._contexts: dict[str, _CameraContext] = {}
        self._listener_task: asyncio.Task | None = None
        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._last_pending_claim_at = 0.0
        self._camera_ips_file = (
            os.environ.get("CAMERA_IPS_FILE", "").strip()
            or str(app_config.get("paths", {}).get("camera_ips_file", "localdata/camera_ips.json"))
        )

    def _edge_collision_enabled(self, camera_id: str) -> bool:
        from services.camera_modes import edge_expects_collision
        from services.camera_store import get_camera

        found = get_camera(self._camera_ips_file, camera_id)
        if found.get("error"):
            return False
        return edge_expects_collision(found.get("camera"))

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

    def _maybe_reload_collision_params(self) -> None:
        mtime = runtime_config_mtime(self._runtime_config_path)
        if mtime == self._runtime_config_mtime:
            return
        self._runtime_config_mtime = mtime
        self._collision_params = CollisionParams.from_config(
            get_merged_inference_config(self.app_config, self._runtime_config_path)
        )
        for ctx in self._contexts.values():
            if ctx.processor is not None:
                ctx.processor.update_params(self._collision_params)
        logger.info("EventRedisWorker collision params reloaded from runtime config")

    def _get_processor(self, camera_id: str, infer_w: int, infer_h: int) -> CollisionProcessor | None:
        self._maybe_reload_collision_params()
        json_path = self._resolve_json_path(camera_id)
        ctx = self._contexts.get(camera_id)

        # 新建：立即创建 processor。
        if ctx is None or ctx.processor is None:
            mtime = os.path.getmtime(json_path) if os.path.isfile(json_path) else 0.0
            boxes = load_scaled_boxes(json_path, infer_w, infer_h) if infer_w > 0 and infer_h > 0 else []
            if not boxes:
                logger.warning("event worker: no boxes for camera=%s path=%s", camera_id, json_path)
            processor = CollisionProcessor(boxes, params=self._collision_params, video_fps=self._video_fps)
            ctx = _CameraContext(
                json_path=json_path,
                json_mtime=mtime,
                processor=processor,
                infer_w=infer_w,
                infer_h=infer_h,
            )
            ctx.last_stat_ts = time.monotonic()
            self._contexts[camera_id] = ctx
            return processor

        # 推理分辨率变化：必须重算 boxes（路径/mtime 走节流）。
        size_changed = ctx.infer_w != infer_w or ctx.infer_h != infer_h
        now = time.monotonic()
        need_stat = size_changed or (now - ctx.last_stat_ts >= self._json_stat_interval)
        if not need_stat:
            return ctx.processor

        ctx.last_stat_ts = now
        mtime = os.path.getmtime(json_path) if os.path.isfile(json_path) else 0.0
        if ctx.json_path == json_path and ctx.json_mtime == mtime and not size_changed:
            return ctx.processor

        boxes = load_scaled_boxes(json_path, infer_w, infer_h) if infer_w > 0 and infer_h > 0 else []
        if not boxes:
            logger.warning("event worker: no boxes for camera=%s path=%s", camera_id, json_path)
        # 热替换 ROI，保留命中/告警/跟踪累积，避免重标定丢状态。
        ctx.processor.update_boxes(boxes)
        ctx.json_path = json_path
        ctx.json_mtime = mtime
        ctx.infer_w = infer_w
        ctx.infer_h = infer_h
        return ctx.processor

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

    async def _process_stream_entry(self, msg_id: str, fields: dict | None) -> None:
        try:
            payload = fields.get("payload") if isinstance(fields, dict) else None
            if payload:
                await self._handle_pose_payload(payload)
        except Exception:
            logger.exception("EventRedisWorker failed msg_id=%s", msg_id)
        finally:
            if self._redis is not None:
                try:
                    await self._redis.xack(POSE_STREAM_KEY, POSE_STREAM_GROUP, msg_id)
                except Exception as exc:
                    logger.warning("EventRedisWorker xack failed msg_id=%s: %s", msg_id, exc)

    async def _reclaim_pending_messages(self) -> None:
        if self._redis is None:
            return
        min_idle_ms = max(1000, int(os.environ.get("POSE_STREAM_CLAIM_IDLE_MS", "60000")))
        batch = max(1, int(os.environ.get("POSE_STREAM_CLAIM_COUNT", "32")))
        start_id = "0-0"
        reclaimed = 0
        while True:
            try:
                result = await self._redis.xautoclaim(
                    POSE_STREAM_KEY,
                    POSE_STREAM_GROUP,
                    self._consumer_name,
                    min_idle_time=min_idle_ms,
                    start_id=start_id,
                    count=batch,
                )
            except Exception as exc:
                logger.warning("EventRedisWorker xautoclaim failed: %s", exc)
                return
            next_id = result[0] if isinstance(result, (list, tuple)) and result else "0-0"
            entries = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
            if not entries:
                break
            for msg_id, fields in entries:
                await self._process_stream_entry(msg_id, fields)
                reclaimed += 1
            start_id = next_id or "0-0"
            if len(entries) < batch:
                break
        if reclaimed:
            logger.info("EventRedisWorker reclaimed %s pending pose messages", reclaimed)

    async def _maybe_reclaim_pending_messages(self) -> None:
        interval_sec = max(5, int(os.environ.get("POSE_STREAM_CLAIM_INTERVAL_SEC", "30")))
        now = time.monotonic()
        if now - self._last_pending_claim_at < interval_sec:
            return
        self._last_pending_claim_at = now
        await self._reclaim_pending_messages()

    async def _stream_loop(self) -> None:
        block_ms = max(500, int(os.environ.get("POSE_STREAM_BLOCK_MS", "2000")))
        while True:
            try:
                await asyncio.to_thread(ensure_pose_stream_group)
                self._redis = aioredis.from_url(redis_url(), decode_responses=True)
                self._last_pending_claim_at = 0.0
                logger.info(
                    "EventRedisWorker stream consumer=%s group=%s key=%s",
                    self._consumer_name,
                    POSE_STREAM_GROUP,
                    POSE_STREAM_KEY,
                )
                await self._reclaim_pending_messages()
                while True:
                    await self._maybe_reclaim_pending_messages()
                    messages = await self._redis.xreadgroup(
                        POSE_STREAM_GROUP,
                        self._consumer_name,
                        {POSE_STREAM_KEY: ">"},
                        count=1,
                        block=block_ms,
                    )
                    if not messages:
                        continue
                    for _stream, items in messages:
                        for msg_id, fields in items:
                            await self._process_stream_entry(msg_id, fields)
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
                        try:
                            await self._handle_pose_payload(payload)
                        except Exception:
                            logger.exception("EventRedisWorker pubsub payload failed")
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
        if self._delivery != "stream" and not owns_camera(
            camera_id, self._shard_count, self._shard_index
        ):
            return

        if self._edge_collision_enabled(camera_id):
            return

        infer_w = int(pose.get("infer_width") or 0)
        infer_h = int(pose.get("infer_height") or 0)
        processor = self._get_processor(camera_id, infer_w, infer_h)
        if processor is None:
            return

        result = await asyncio.to_thread(processor.process, pose)
        frame_idx = int(result.get("frame_idx") or pose.get("frame_idx") or 0)
        collisions = result.get("collisions") or []
        alarm_collisions = result.get("alarm_collisions") or []
        skeletons = result.get("skeletons")

        await asyncio.to_thread(
            publish_event_frame,
            camera_id,
            frame_idx=frame_idx,
            collisions=collisions,
            alarm_collisions=alarm_collisions,
            skeletons=skeletons,
        )

        if self.callback_reporter and alarm_collisions:
            upload_tag = f"infer_{camera_id}"
            video_time_sec = frame_idx / self._video_fps
            for collision in alarm_collisions:
                shelf_code, box_id = parse_collision_token(collision)
                if not box_id:
                    continue
                self.callback_reporter.enqueue_pick_finished(
                    box_id=box_id,
                    frame_idx=frame_idx,
                    video_time_sec=video_time_sec,
                    upload_tag=upload_tag,
                    shelf_code=shelf_code or None,
                )

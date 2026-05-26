"""Redis 姿态消费 → 碰撞事件 → 发布 event + Java 回调。"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import redis.asyncio as aioredis

from services.annotation_service import camera_annotation_path
from services.box_identity import parse_collision_token
from services.event_bus import publish_event_frame
from services.event_engine.annotation_boxes import load_scaled_boxes
from services.event_engine.collision import CollisionProcessor
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


class EventRedisWorker:
    def __init__(self, app_config: dict, callback_reporter=None):
        self.app_config = app_config
        self.callback_reporter = callback_reporter
        self._json_dir = (
            os.environ.get("JSON_DIR", "").strip()
            or str(app_config.get("paths", {}).get("json_dir", "localdata/json"))
        )
        infer_cfg = app_config.get("inference", {}) or {}
        self._alarm_min = int(infer_cfg.get("alarm_min_consecutive_frames", 3) or 3)
        self._alarm_cooldown = int(infer_cfg.get("alarm_cooldown_frames", 12) or 12)
        try:
            self._alarm_min_sec = float(infer_cfg.get("alarm_min_consecutive_sec", 0) or 0)
        except (TypeError, ValueError):
            self._alarm_min_sec = 0.0
        try:
            self._alarm_cooldown_sec = float(infer_cfg.get("alarm_cooldown_sec", 0) or 0)
        except (TypeError, ValueError):
            self._alarm_cooldown_sec = 0.0
        self._video_fps = float(infer_cfg.get("frame_rate", 15) or 15)
        self._delivery = pose_delivery_mode()
        self._shard_count, self._shard_index = shard_config()
        self._consumer_name = default_consumer_name()
        self._contexts: dict[str, _CameraContext] = {}
        self._listener_task: asyncio.Task | None = None
        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None

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
                alarm_min_consecutive_sec=self._alarm_min_sec,
                alarm_cooldown_sec=self._alarm_cooldown_sec,
                video_fps=self._video_fps,
            )
            if self._alarm_min_sec > 0:
                mode_note = f"time_trigger={self._alarm_min_sec:.2f}s"
                if self._alarm_cooldown_sec > 0:
                    mode_note += f" time_cooldown={self._alarm_cooldown_sec:.2f}s"
                else:
                    mode_note += f" frame_cooldown={self._alarm_cooldown}"
                logger.info("event worker alarm gate camera=%s %s", camera_id, mode_note)
            ctx = _CameraContext(
                json_path=json_path,
                json_mtime=mtime,
                processor=processor,
                infer_w=infer_w,
                infer_h=infer_h,
            )
            self._contexts[camera_id] = ctx

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

    async def _stream_loop(self) -> None:
        block_ms = max(500, int(os.environ.get("POSE_STREAM_BLOCK_MS", "2000")))
        while True:
            try:
                await asyncio.to_thread(ensure_pose_stream_group)
                self._redis = aioredis.from_url(redis_url(), decode_responses=True)
                logger.info(
                    "EventRedisWorker stream consumer=%s group=%s key=%s",
                    self._consumer_name,
                    POSE_STREAM_GROUP,
                    POSE_STREAM_KEY,
                )
                while True:
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
                            payload = fields.get("payload") if isinstance(fields, dict) else None
                            if payload:
                                await self._handle_pose_payload(payload)
                            await self._redis.xack(POSE_STREAM_KEY, POSE_STREAM_GROUP, msg_id)
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
        if self._delivery != "stream" and not owns_camera(
            camera_id, self._shard_count, self._shard_index
        ):
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

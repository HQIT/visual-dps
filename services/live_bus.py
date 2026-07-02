"""Redis 直播 overlay：合并 pose + event → UI SSE。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import redis as sync_redis
import redis.asyncio as aioredis

from services.event_bus import EVENT_CHANNEL_PREFIX, get_event_snapshot
from services.pipeline_latency import (
    build_live_trace,
    compact_for_sse,
    skeleton_source_for_merge,
    sse_payload_enabled,
)
from services.pipeline_latency_log import append_jsonl
from services.pose_bus import POSE_CHANNEL_PREFIX, get_pose_snapshot

logger = logging.getLogger(__name__)

LIVE_SCHEMA_VERSION = 2
SSE_QUEUE_MAX = max(8, int(os.environ.get("LIVE_SSE_QUEUE_MAX", "32")))


def redis_url() -> str:
    explicit = os.environ.get("REDIS_URL", "").strip()
    if explicit:
        return explicit
    password = os.environ.get("REDIS_PASSWORD", "").strip()
    host = os.environ.get("REDIS_HOST", "redis").strip() or "redis"
    port = os.environ.get("REDIS_PORT", "6379").strip() or "6379"
    if password:
        return f"redis://:{password}@{host}:{port}/0"
    return f"redis://{host}:{port}/0"


def merge_live_frame(pose: dict[str, Any] | None, event: dict[str, Any] | None) -> dict[str, Any]:
    """合并姿态与事件为监控页 SSE 载荷。"""
    pose = pose if isinstance(pose, dict) else {}
    event = event if isinstance(event, dict) else {}
    pose_ts = float(pose.get("ts") or 0)
    event_ts = float(event.get("ts") or 0)
    ts = max(pose_ts, event_ts) or time.time()
    skeletons, skeleton_source = skeleton_source_for_merge(pose, event)
    frame_idx = int(pose.get("frame_idx") or event.get("frame_idx") or 0)
    merged: dict[str, Any] = {
        "schema": LIVE_SCHEMA_VERSION,
        "ts": ts,
        "infer_width": int(pose.get("infer_width") or 0),
        "infer_height": int(pose.get("infer_height") or 0),
        "frame_idx": frame_idx,
        "skeletons": list(skeletons),
        "collisions": list(event.get("collisions") or []),
        "alarm_collisions": list(event.get("alarm_collisions") or []),
    }
    if skeleton_source != "none":
        merged["skeleton_source"] = skeleton_source

    live_trace = build_live_trace(pose, event)
    if live_trace is not None:
        append_jsonl(str(pose.get("camera_id") or event.get("camera_id") or ""), live_trace)
        if sse_payload_enabled():
            merged["latency_trace"] = compact_for_sse(live_trace)
    return merged


def get_snapshot(camera_id: str) -> dict[str, Any] | None:
    """合并后的最新 overlay（供 SSE 首包与兼容 API）。"""
    pose = get_pose_snapshot(camera_id)
    event = get_event_snapshot(camera_id)
    if not pose and not event:
        return None
    return merge_live_frame(pose, event)


def format_sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


class LiveHub:
    """订阅 pose:live:* 与 event:live:*，合并后 fan-out 给 SSE。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = {}
        self._pose_cache: dict[str, dict[str, Any]] = {}
        self._event_cache: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._listener_task: asyncio.Task | None = None
        self._redis: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None

    async def start(self) -> None:
        if self._listener_task and not self._listener_task.done():
            return
        self._listener_task = asyncio.create_task(self._redis_listen_loop(), name="live-redis-listener")

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

    def _camera_id_from_pose_channel(self, channel: str) -> str:
        if channel.startswith(POSE_CHANNEL_PREFIX):
            return channel[len(POSE_CHANNEL_PREFIX) :]
        return channel

    def _camera_id_from_event_channel(self, channel: str) -> str:
        if channel.startswith(EVENT_CHANNEL_PREFIX):
            return channel[len(EVENT_CHANNEL_PREFIX) :]
        return channel

    async def _redis_listen_loop(self) -> None:
        patterns = [f"{POSE_CHANNEL_PREFIX}*", f"{EVENT_CHANNEL_PREFIX}*"]
        while True:
            try:
                self._redis = aioredis.from_url(redis_url(), decode_responses=True)
                self._pubsub = self._redis.pubsub()
                for pattern in patterns:
                    await self._pubsub.psubscribe(pattern)
                logger.info("LiveHub subscribed to Redis patterns %s", patterns)
                async for message in self._pubsub.listen():
                    mtype = message.get("type")
                    if mtype != "pmessage":
                        continue
                    channel = message.get("channel") or ""
                    payload = message.get("data")
                    if not payload:
                        continue
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue

                    if channel.startswith(POSE_CHANNEL_PREFIX):
                        camera_id = self._camera_id_from_pose_channel(channel)
                        async with self._lock:
                            self._pose_cache[camera_id] = data
                            pose = self._pose_cache.get(camera_id)
                            event = self._event_cache.get(camera_id)
                    elif channel.startswith(EVENT_CHANNEL_PREFIX):
                        camera_id = self._camera_id_from_event_channel(channel)
                        async with self._lock:
                            self._event_cache[camera_id] = data
                            pose = self._pose_cache.get(camera_id)
                            event = self._event_cache.get(camera_id)
                    else:
                        continue

                    merged = await asyncio.to_thread(merge_live_frame, pose, event)
                    out = json.dumps(merged, ensure_ascii=False, separators=(",", ":"))
                    await self._broadcast(camera_id, out)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("LiveHub Redis listener error: %s; retry in 2s", exc)
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

    async def _broadcast(self, camera_id: str, payload: str) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(camera_id, ()))
        for queue in queues:
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(payload)
                except asyncio.QueueFull:
                    pass

    async def subscribe_sse(self, camera_id: str) -> AsyncIterator[str]:
        cid = str(camera_id or "").strip()
        if not cid:
            return
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=SSE_QUEUE_MAX)
        async with self._lock:
            self._subscribers.setdefault(cid, set()).add(queue)

        try:
            snap = await asyncio.to_thread(get_snapshot, cid)
            if snap:
                yield format_sse("frame", json.dumps(snap, ensure_ascii=False, separators=(",", ":")))
            yield format_sse("ready", json.dumps({"camera_id": cid, "ts": time.time()}))
            while True:
                payload = await queue.get()
                yield format_sse("frame", payload)
        finally:
            async with self._lock:
                subs = self._subscribers.get(cid)
                if subs:
                    subs.discard(queue)
                    if not subs:
                        self._subscribers.pop(cid, None)


live_hub = LiveHub()

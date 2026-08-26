"""事件 Worker 水平扩展：camera → 固定 logical shard → 动态 worker 区间。"""

from __future__ import annotations

import os
import zlib


def camera_shard_id(camera_id: str) -> int:
    cid = str(camera_id or "").strip()
    if not cid:
        return 0
    return zlib.crc32(cid.encode("utf-8")) & 0xFFFFFFFF


def logical_shard_count() -> int:
    """全局 logical shard 数量（部署后尽量不变）。"""
    try:
        count = int(os.environ.get("POSE_LOGICAL_SHARD_COUNT", "16") or "16")
    except ValueError:
        count = 16
    return max(1, count)


def logical_shard_id(camera_id: str) -> int:
    """camera_id 固定映射到 logical shard（与 worker 数量无关）。"""
    n = logical_shard_count()
    if n <= 1:
        return 0
    return camera_shard_id(camera_id) % n


def stream_key_prefix() -> str:
    explicit = os.environ.get("POSE_STREAM_KEY", "").strip()
    if explicit:
        return explicit.rstrip(":")
    prefix = os.environ.get("POSE_STREAM_KEY_PREFIX", "pose:stream").strip()
    return prefix or "pose:stream"


def stream_key_for_shard(shard_id: int) -> str:
    """logical shard 对应的 Redis Stream 键名。"""
    n = logical_shard_count()
    prefix = stream_key_prefix()
    if n <= 1:
        return prefix
    return f"{prefix}:{int(shard_id)}"


def stream_key_for_camera(camera_id: str) -> str:
    return stream_key_for_shard(logical_shard_id(camera_id))


def shard_config() -> tuple[int, int]:
    """旧变量：worker 实例序号 (shard_count, shard_index)，用于 pubsub 或未配区间时的 fallback。"""
    try:
        count = int(os.environ.get("EVENT_WORKER_SHARD_COUNT", "1") or "1")
    except ValueError:
        count = 1
    try:
        index = int(os.environ.get("EVENT_WORKER_SHARD_INDEX", "0") or "0")
    except ValueError:
        index = 0
    count = max(1, count)
    index = max(0, min(index, count - 1))
    return count, index


def worker_owned_shard_ids(environ: dict[str, str] | None = None) -> list[int]:
    """本 worker 负责的 logical shard 列表（shard → worker 动态映射）。"""
    env = environ if environ is not None else os.environ
    n = logical_shard_count()

    start_s = str(env.get("EVENT_WORKER_SHARD_START", "") or "").strip()
    end_s = str(env.get("EVENT_WORKER_SHARD_END", "") or "").strip()
    if start_s != "" and end_s != "":
        try:
            start = int(start_s)
            end = int(end_s)
        except ValueError:
            start, end = 0, n - 1
        start = max(0, min(start, n - 1))
        end = max(start, min(end, n - 1))
        return list(range(start, end + 1))

    ids_s = str(env.get("EVENT_WORKER_SHARD_IDS", "") or "").strip()
    if ids_s:
        out: list[int] = []
        for part in ids_s.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                sid = int(part)
            except ValueError:
                continue
            if 0 <= sid < n:
                out.append(sid)
        return sorted(set(out))

    if environ is None:
        worker_count, worker_index = shard_config()
        if worker_count > 1 and n > 1:
            return [s for s in range(n) if s % worker_count == worker_index]

    return list(range(n))


def worker_owned_stream_keys(environ: dict[str, str] | None = None) -> list[str]:
    return [stream_key_for_shard(s) for s in worker_owned_shard_ids(environ)]


def owns_camera(
    camera_id: str,
    shard_count: int | None = None,
    shard_index: int | None = None,
) -> bool:
    """该实例是否负责此 camera_id。"""
    cid = str(camera_id or "").strip()
    if not cid:
        return False

    n = logical_shard_count()
    if n > 1:
        sid = logical_shard_id(cid)
        owned = set(worker_owned_shard_ids())
        if shard_count is not None and shard_index is not None:
            wc = max(1, int(shard_count))
            wi = max(0, min(int(shard_index), wc - 1))
            owned = {s for s in range(n) if s % wc == wi}
        return sid in owned

    count, index = shard_config()
    if shard_count is not None:
        count = max(1, int(shard_count))
    if shard_index is not None:
        index = max(0, min(int(shard_index), count - 1))
    if count <= 1:
        return True
    return (camera_shard_id(cid) % count) == index


def shard_label() -> str:
    n = logical_shard_count()
    owned = worker_owned_shard_ids()
    if n <= 1:
        count, index = shard_config()
        if count <= 1:
            return "shard=all"
        return f"shard={index}/{count}"
    if len(owned) == n:
        return f"logical_shards=all/{n}"
    if len(owned) == 1:
        return f"logical_shard={owned[0]}/{n}"
    return f"logical_shards={owned[0]}-{owned[-1]}/{n}"

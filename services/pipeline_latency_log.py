"""全链路延迟 JSONL 日志输出。"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

_lock = threading.Lock()
_summary_counter = 0


def log_dir() -> str:
    raw = os.environ.get("PIPELINE_LATENCY_LOG_DIR", "localdata/logs/latency").strip()
    return raw or "localdata/logs/latency"


def stderr_summary_enabled() -> bool:
    return os.environ.get("PIPELINE_LATENCY_LOG_STDERR", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def append_jsonl(camera_id: str, record: dict[str, Any]) -> None:
    from services.pipeline_latency import enabled

    if not enabled() or not isinstance(record, dict):
        return

    cid = str(camera_id or record.get("camera_id") or "unknown").strip() or "unknown"
    out = dict(record)
    out["logged_at"] = time.time()

    os.makedirs(log_dir(), exist_ok=True)
    path = os.path.join(log_dir(), f"{cid}.jsonl")
    line = json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n"

    with _lock:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)

    if stderr_summary_enabled():
        _maybe_print_summary(out)


def _maybe_print_summary(record: dict[str, Any]) -> None:
    global _summary_counter
    _summary_counter += 1
    every = max(1, int(os.environ.get("PIPELINE_LATENCY_STDERR_EVERY", "30")))
    if _summary_counter % every != 0:
        return

    lat = record.get("latency_ms") or {}
    meta = record.get("meta") or {}
    parts: list[str] = []
    for key in ("snap_to_pose", "worker_process", "snap_to_merge"):
        value = lat.get(key)
        if value is not None:
            parts.append(f"{key}={value}ms")
    print(
        f"[LATENCY] cam={record.get('camera_id')} frame={record.get('frame_idx')} "
        f"{' '.join(parts)} skeleton={meta.get('skeleton_source', '?')} "
        f"fi_delta={meta.get('frame_idx_delta', '?')}",
        flush=True,
    )

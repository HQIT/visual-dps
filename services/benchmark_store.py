"""离线评测 run 持久化（SQLite + 本地视频/标注快照）。"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from typing import Any

BENCHMARK_ROOT = os.environ.get("BENCHMARK_ROOT", "localdata/benchmark")
# 独立库：避免与 root/Docker 创建的 visual_dps.db 权限冲突
DEFAULT_BENCHMARK_DB_PATH = os.environ.get(
    "BENCHMARK_DB_PATH",
    os.path.join(BENCHMARK_ROOT, "benchmark.db"),
)
_lock = threading.Lock()

_RUN_STATUSES = frozenset({"pending", "running", "finished", "error", "cancelled"})


def _connect(db_path: str = DEFAULT_BENCHMARK_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_benchmark_db(db_path: str = DEFAULT_BENCHMARK_DB_PATH) -> None:
    os.makedirs(BENCHMARK_ROOT, exist_ok=True)
    with _lock:
        conn = _connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    id TEXT PRIMARY KEY,
                    camera_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    backend TEXT NOT NULL DEFAULT '',
                    video_path TEXT NOT NULL DEFAULT '',
                    annotation_path TEXT NOT NULL DEFAULT '',
                    video_width INTEGER NOT NULL DEFAULT 0,
                    video_height INTEGER NOT NULL DEFAULT 0,
                    video_fps REAL NOT NULL DEFAULT 0,
                    duration_sec REAL NOT NULL DEFAULT 0,
                    annotation_width INTEGER NOT NULL DEFAULT 0,
                    annotation_height INTEGER NOT NULL DEFAULT 0,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    message TEXT NOT NULL DEFAULT '',
                    pose_frame_count INTEGER NOT NULL DEFAULT 0,
                    alarm_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_bench_runs_created ON benchmark_runs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_bench_runs_camera ON benchmark_runs(camera_id);
                CREATE INDEX IF NOT EXISTS idx_bench_runs_status ON benchmark_runs(status);

                CREATE TABLE IF NOT EXISTS benchmark_pose_frames (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    frame_idx INTEGER NOT NULL,
                    video_time_sec REAL NOT NULL,
                    infer_width INTEGER NOT NULL DEFAULT 0,
                    infer_height INTEGER NOT NULL DEFAULT 0,
                    persons_json TEXT NOT NULL DEFAULT '[]',
                    collisions_json TEXT NOT NULL DEFAULT '[]',
                    alarm_collisions_json TEXT NOT NULL DEFAULT '[]',
                    latency_ms_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_bench_pose_run_time ON benchmark_pose_frames(run_id, video_time_sec);
                CREATE INDEX IF NOT EXISTS idx_bench_pose_run_frame ON benchmark_pose_frames(run_id, frame_idx);

                CREATE TABLE IF NOT EXISTS benchmark_alarms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    frame_idx INTEGER NOT NULL,
                    video_time_sec REAL NOT NULL,
                    hits_json TEXT NOT NULL DEFAULT '[]',
                    alarms_json TEXT NOT NULL DEFAULT '[]',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_bench_alarm_run_time ON benchmark_alarms(run_id, video_time_sec);
                """
            )
            conn.commit()
        finally:
            conn.close()


def run_dir(run_id: str) -> str:
    return os.path.join(BENCHMARK_ROOT, run_id)


def run_video_path(run_id: str) -> str:
    return os.path.join(run_dir(run_id), "video.mp4")


def run_annotation_snapshot_path(run_id: str) -> str:
    return os.path.join(run_dir(run_id), "annotation.json")


def new_run_id() -> str:
    return f"bench-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def create_run(
    *,
    camera_id: str,
    title: str = "",
    backend: str = "",
    video_src_path: str,
    annotation_src_path: str,
    video_width: int = 0,
    video_height: int = 0,
    video_fps: float = 0,
    duration_sec: float = 0,
    annotation_width: int = 0,
    annotation_height: int = 0,
    config: dict | None = None,
    db_path: str = DEFAULT_BENCHMARK_DB_PATH,
) -> dict[str, Any]:
    run_id = new_run_id()
    dest_dir = run_dir(run_id)
    os.makedirs(dest_dir, exist_ok=True)
    dest_video = run_video_path(run_id)
    shutil.copy2(video_src_path, dest_video)
    dest_ann = run_annotation_snapshot_path(run_id)
    if os.path.isfile(annotation_src_path):
        shutil.copy2(annotation_src_path, dest_ann)
    now = time.time()
    row = {
        "id": run_id,
        "camera_id": str(camera_id or "").strip(),
        "title": str(title or "").strip() or os.path.basename(video_src_path),
        "status": "pending",
        "backend": str(backend or "").strip(),
        "video_path": dest_video,
        "annotation_path": dest_ann if os.path.isfile(dest_ann) else annotation_src_path,
        "video_width": int(video_width or 0),
        "video_height": int(video_height or 0),
        "video_fps": float(video_fps or 0),
        "duration_sec": float(duration_sec or 0),
        "annotation_width": int(annotation_width or 0),
        "annotation_height": int(annotation_height or 0),
        "config_json": json.dumps(config or {}, ensure_ascii=False),
        "message": "",
        "pose_frame_count": 0,
        "alarm_count": 0,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
    }
    with _lock:
        conn = _connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO benchmark_runs (
                    id, camera_id, title, status, backend, video_path, annotation_path,
                    video_width, video_height, video_fps, duration_sec,
                    annotation_width, annotation_height, config_json, message,
                    pose_frame_count, alarm_count, created_at, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["camera_id"],
                    row["title"],
                    row["status"],
                    row["backend"],
                    row["video_path"],
                    row["annotation_path"],
                    row["video_width"],
                    row["video_height"],
                    row["video_fps"],
                    row["duration_sec"],
                    row["annotation_width"],
                    row["annotation_height"],
                    row["config_json"],
                    row["message"],
                    0,
                    0,
                    row["created_at"],
                    None,
                    None,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    return _run_row_to_dict(row)


def update_run_status(
    run_id: str,
    status: str,
    *,
    message: str = "",
    db_path: str = DEFAULT_BENCHMARK_DB_PATH,
) -> None:
    if status not in _RUN_STATUSES:
        status = "error"
    now = time.time()
    with _lock:
        conn = _connect(db_path)
        try:
            if status == "running":
                conn.execute(
                    "UPDATE benchmark_runs SET status=?, message=?, started_at=? WHERE id=?",
                    (status, message, now, run_id),
                )
            elif status in ("finished", "error", "cancelled"):
                conn.execute(
                    "UPDATE benchmark_runs SET status=?, message=?, finished_at=? WHERE id=?",
                    (status, message, now, run_id),
                )
            else:
                conn.execute(
                    "UPDATE benchmark_runs SET status=?, message=? WHERE id=?",
                    (status, message, run_id),
                )
            conn.commit()
        finally:
            conn.close()


def increment_run_counters(
    run_id: str,
    *,
    pose_delta: int = 0,
    alarm_delta: int = 0,
    db_path: str = DEFAULT_BENCHMARK_DB_PATH,
) -> None:
    if pose_delta == 0 and alarm_delta == 0:
        return
    with _lock:
        conn = _connect(db_path)
        try:
            conn.execute(
                """
                UPDATE benchmark_runs SET
                    pose_frame_count = pose_frame_count + ?,
                    alarm_count = alarm_count + ?
                WHERE id=?
                """,
                (pose_delta, alarm_delta, run_id),
            )
            conn.commit()
        finally:
            conn.close()


def save_pose_frame(
    run_id: str,
    *,
    frame_idx: int,
    video_time_sec: float,
    infer_width: int,
    infer_height: int,
    persons: list,
    collisions: list | None = None,
    alarm_collisions: list | None = None,
    latency_ms: dict | None = None,
    db_path: str = DEFAULT_BENCHMARK_DB_PATH,
) -> None:
    with _lock:
        conn = _connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO benchmark_pose_frames (
                    run_id, frame_idx, video_time_sec, infer_width, infer_height,
                    persons_json, collisions_json, alarm_collisions_json, latency_ms_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(frame_idx),
                    float(video_time_sec),
                    int(infer_width),
                    int(infer_height),
                    json.dumps(list(persons or []), ensure_ascii=False),
                    json.dumps(list(collisions or []), ensure_ascii=False),
                    json.dumps(list(alarm_collisions or []), ensure_ascii=False),
                    json.dumps(latency_ms or {}, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    increment_run_counters(run_id, pose_delta=1, db_path=db_path)


def save_alarm(
    run_id: str,
    *,
    frame_idx: int,
    video_time_sec: float,
    hits: list,
    alarms: list,
    detail: dict | None = None,
    db_path: str = DEFAULT_BENCHMARK_DB_PATH,
) -> None:
    with _lock:
        conn = _connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO benchmark_alarms (
                    run_id, frame_idx, video_time_sec, hits_json, alarms_json, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(frame_idx),
                    float(video_time_sec),
                    json.dumps(list(hits or []), ensure_ascii=False),
                    json.dumps(list(alarms or []), ensure_ascii=False),
                    json.dumps(detail or {}, ensure_ascii=False),
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    increment_run_counters(run_id, alarm_delta=1, db_path=db_path)


def _parse_json_field(raw: Any, default):
    if raw is None:
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _run_row_to_dict(row: dict | sqlite3.Row) -> dict[str, Any]:
    if not isinstance(row, dict):
        row = dict(row)
    out = dict(row)
    out["config"] = _parse_json_field(out.pop("config_json", "{}"), {})
    for key in ("created_at", "started_at", "finished_at"):
        ts = out.get(key)
        if ts:
            out[f"{key}_iso"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    return out


def get_run(run_id: str, db_path: str = DEFAULT_BENCHMARK_DB_PATH) -> dict[str, Any] | None:
    with _lock:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT * FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return _run_row_to_dict(row)


def list_runs(
    *,
    page: int = 1,
    page_size: int = 20,
    camera_id: str = "",
    status: str = "",
    db_path: str = DEFAULT_BENCHMARK_DB_PATH,
) -> dict[str, Any]:
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    offset = (page - 1) * page_size
    where = ["1=1"]
    params: list[Any] = []
    if camera_id.strip():
        where.append("camera_id = ?")
        params.append(camera_id.strip())
    if status.strip():
        where.append("status = ?")
        params.append(status.strip())
    w = " AND ".join(where)
    with _lock:
        conn = _connect(db_path)
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM benchmark_runs WHERE {w}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM benchmark_runs WHERE {w} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
        finally:
            conn.close()
    return {
        "status": "success",
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [_run_row_to_dict(r) for r in rows],
    }


def list_pose_frames(
    run_id: str,
    *,
    from_sec: float | None = None,
    to_sec: float | None = None,
    db_path: str = DEFAULT_BENCHMARK_DB_PATH,
) -> list[dict[str, Any]]:
    where = ["run_id = ?"]
    params: list[Any] = [run_id]
    if from_sec is not None:
        where.append("video_time_sec >= ?")
        params.append(float(from_sec))
    if to_sec is not None:
        where.append("video_time_sec <= ?")
        params.append(float(to_sec))
    w = " AND ".join(where)
    with _lock:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                f"SELECT * FROM benchmark_pose_frames WHERE {w} ORDER BY video_time_sec ASC",
                params,
            ).fetchall()
        finally:
            conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["persons"] = _parse_json_field(item.pop("persons_json", "[]"), [])
        item["collisions"] = _parse_json_field(item.pop("collisions_json", "[]"), [])
        item["alarm_collisions"] = _parse_json_field(item.pop("alarm_collisions_json", "[]"), [])
        item["latency_ms"] = _parse_json_field(item.pop("latency_ms_json", "{}"), {})
        out.append(item)
    return out


def list_alarms(run_id: str, db_path: str = DEFAULT_BENCHMARK_DB_PATH) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect(db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM benchmark_alarms WHERE run_id=? ORDER BY video_time_sec ASC",
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
    out = []
    for row in rows:
        item = dict(row)
        item["hits"] = _parse_json_field(item.pop("hits_json", "[]"), [])
        item["alarms"] = _parse_json_field(item.pop("alarms_json", "[]"), [])
        item["detail"] = _parse_json_field(item.pop("detail_json", "{}"), {})
        out.append(item)
    return out


def update_run_inference_params(
    run_id: str,
    *,
    backend: str = "",
    config: dict | None = None,
    db_path: str = DEFAULT_BENCHMARK_DB_PATH,
) -> bool:
    """重跑前同步当前有效推理参数到 run 记录。"""
    cfg = config if isinstance(config, dict) else {}
    with _lock:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT id FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return False
            conn.execute(
                """
                UPDATE benchmark_runs SET
                    backend=?,
                    config_json=?
                WHERE id=?
                """,
                (
                    str(backend or "").strip(),
                    json.dumps(cfg, ensure_ascii=False),
                    run_id,
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def reset_run_for_rerun(run_id: str, db_path: str = DEFAULT_BENCHMARK_DB_PATH) -> bool:
    """清空 pose/告警结果并重置计数，供同 run 重跑。"""
    with _lock:
        conn = _connect(db_path)
        try:
            row = conn.execute("SELECT id FROM benchmark_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM benchmark_pose_frames WHERE run_id=?", (run_id,))
            conn.execute("DELETE FROM benchmark_alarms WHERE run_id=?", (run_id,))
            conn.execute(
                """
                UPDATE benchmark_runs SET
                    status='pending',
                    message='',
                    pose_frame_count=0,
                    alarm_count=0,
                    started_at=NULL,
                    finished_at=NULL
                WHERE id=?
                """,
                (run_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def delete_run(run_id: str, db_path: str = DEFAULT_BENCHMARK_DB_PATH) -> bool:
    with _lock:
        conn = _connect(db_path)
        try:
            cur = conn.execute("DELETE FROM benchmark_runs WHERE id=?", (run_id,))
            conn.commit()
            deleted = cur.rowcount > 0
        finally:
            conn.close()
    dest = run_dir(run_id)
    if os.path.isdir(dest):
        shutil.rmtree(dest, ignore_errors=True)
    return deleted

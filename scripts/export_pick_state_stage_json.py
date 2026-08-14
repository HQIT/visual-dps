#!/usr/bin/env python3
"""导出 pick_state 分阶段耗时 JSON（容器内运行）。"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("PICK_STATE_STAGE_PROFILE", "1")

import redis  # noqa: E402

from services.event_engine.annotation_boxes import load_scaled_boxes  # noqa: E402
from services.event_engine.pick_state_processor import PickStateProcessor  # noqa: E402

STAGES = [
    "track_ms",
    "feature_ms",
    "box_ms",
    "pair_temporal_ms",
    "action_track_ms",
    "pair_score_ms",
    "action_gate_ms",
    "box_gate_ms",
    "alarm_ms",
    "pick_total_ms",
    "worker_ms",
]


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def _summary(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 2),
        "p50": round(_percentile(vals, 50), 2),
        "p95": round(_percentile(vals, 95), 2),
        "max": round(max(vals), 2),
    }


def main() -> int:
    cameras = [
        c.strip()
        for c in os.environ.get("CAMERAS", "cam1,cam2,cam3,cam4,cam5,cam6,cam7,cam8").split(",")
        if c.strip()
    ]
    limit = int(os.environ.get("LIMIT_PER_CAM", "150"))
    redis_url = os.environ.get("REDIS_URL", "redis://:visual-dps-local@127.0.0.1:6379/0")
    client = redis.from_url(redis_url, decode_responses=True)
    rows = client.xrevrange("pose:stream", count=max(limit * len(cameras) * 8, 6000))

    by_cam: dict[str, list[dict]] = {c: [] for c in cameras}
    for _eid, fields in rows:
        raw = (fields or {}).get("payload")
        if not raw:
            continue
        try:
            pose = json.loads(raw)
        except json.JSONDecodeError:
            continue
        cid = str(pose.get("camera_id") or "")
        if cid not in by_cam:
            continue
        bucket = by_cam[cid]
        if len(bucket) >= limit:
            continue
        fi = int(pose.get("frame_idx") or 0)
        if bucket and fi >= bucket[-1].get("frame_idx", 0):
            continue
        bucket.append(pose)
    for cid in cameras:
        by_cam[cid] = sorted(by_cam[cid], key=lambda p: int(p.get("frame_idx") or 0))

    all_s: dict[str, list[float]] = defaultdict(list)
    hot_s: dict[str, list[float]] = defaultdict(list)
    per_cam: dict[str, dict] = {}
    hot_frames = 0
    frames = 0

    for cam in cameras:
        poses = by_cam[cam]
        if not poses:
            continue
        s0 = poses[0]
        iw = int(s0.get("infer_width") or 640)
        ih = int(s0.get("infer_height") or 360)
        jpath = f"/app/localdata/json/cameras/{cam}.json"
        boxes = load_scaled_boxes(jpath, iw, ih)
        proc = PickStateProcessor(
            boxes,
            config_path="pick_state/configs/pipeline.v5_gated.json",
            video_fps=15.0,
            infer_width=iw,
            infer_height=ih,
            record_id=cam,
        )
        cam_hot: dict[str, list[float]] = defaultdict(list)
        nh = 0
        for pose in poses:
            t0 = time.perf_counter()
            out = proc.process(pose)
            wall = (time.perf_counter() - t0) * 1000.0
            st = out.get("stage_timings") or {}
            if not st:
                continue
            frames += 1
            st = dict(st)
            st["worker_ms"] = round(wall, 2)
            is_hot = (
                int(st.get("n_hits") or 0) > 0
                or int(st.get("n_action_gate") or 0) > 0
                or int(st.get("n_picking") or 0) > 0
            )
            if is_hot:
                hot_frames += 1
                nh += 1
                for key in ("pick_total_ms", "action_gate_ms", "box_ms", "feature_ms"):
                    cam_hot[key].append(float(st.get(key) or 0.0))
            for key in STAGES:
                val = st.get(key)
                if isinstance(val, (int, float)):
                    all_s[key].append(float(val))
                    if is_hot:
                        hot_s[key].append(float(val))
        per_cam[cam] = {
            "hot_frames": nh,
            "hot_p95": {k: round(_percentile(v, 95), 2) for k, v in cam_hot.items()},
        }

    hot_mean = statistics.mean(hot_s["pick_total_ms"]) if hot_s.get("pick_total_ms") else 0.0
    share = {
        k: round(statistics.mean(hot_s[k]) / hot_mean * 100, 1)
        for k in STAGES
        if k not in ("pick_total_ms", "worker_ms") and hot_s.get(k) and hot_mean
    }

    stream_meta: dict[str, int] = {}
    try:
        groups = client.xinfo_groups("pose:stream")
        for item in groups:
            if isinstance(item, dict):
                name = item.get("name")
                if name in ("lag", "pending", "consumers"):
                    stream_meta[name] = int(item.get("value", 0))
    except Exception:
        pass
    client.close()

    payload = {
        "meta": {
            "sampled_frames": frames,
            "hot_frames": hot_frames,
            "poses_per_cam": {c: len(by_cam[c]) for c in cameras},
            "stream": stream_meta,
        },
        "all": {k: _summary(v) for k, v in all_s.items()},
        "hot": {k: _summary(v) for k, v in hot_s.items()},
        "hot_share_pct": share,
        "per_cam_hot_p95": per_cam,
    }
    out_path = os.environ.get("OUT", "/app/localdata/stage-stats-export.json")
    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

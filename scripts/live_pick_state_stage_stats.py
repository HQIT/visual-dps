#!/usr/bin/env python3
"""从 Redis pose:stream 重放最近帧，统计 pick_state 分阶段耗时。"""
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


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://:visual-dps-local@127.0.0.1:6379/0")


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def _load_boxes(cam: str, infer_w: int, infer_h: int) -> list:
    jpath = Path(f"/app/localdata/json/cameras/{cam}.json")
    if not jpath.is_file():
        jpath = Path("/app/localdata/json/precise_boxes_new.json")
    return load_scaled_boxes(str(jpath), infer_w, infer_h)


def main() -> int:
    cameras = [c.strip() for c in os.environ.get("CAMERAS", "cam1,cam2,cam3,cam4,cam5,cam6,cam7,cam8").split(",") if c.strip()]
    limit = int(os.environ.get("LIMIT_PER_CAM", "120"))
    client = redis.from_url(_redis_url(), decode_responses=True)
    rows = client.xrevrange("pose:stream", count=max(limit * len(cameras) * 8, 3000))
    client.close()

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
    hot_frames = 0
    frames = 0

    for cam in cameras:
        poses = by_cam[cam]
        if not poses:
            print(f"WARN: {cam} 无 pose 样本", file=sys.stderr)
            continue
        s0 = poses[0]
        proc = PickStateProcessor(
            _load_boxes(cam, int(s0.get("infer_width") or 640), int(s0.get("infer_height") or 360)),
            config_path="pick_state/configs/pipeline.v5_gated.json",
            video_fps=15.0,
            infer_width=int(s0.get("infer_width") or 640),
            infer_height=int(s0.get("infer_height") or 360),
            record_id=cam,
        )
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
            for key in STAGES:
                val = st.get(key)
                if isinstance(val, (int, float)):
                    all_s[key].append(float(val))
                    if is_hot:
                        hot_s[key].append(float(val))

    counts = {c: len(by_cam[c]) for c in cameras}
    print(f"sampled_frames={frames} hot_frames={hot_frames} poses_per_cam={counts}")
    print("\n== ALL frames ==")
    for key in STAGES:
        vals = all_s[key]
        if not vals:
            continue
        print(
            f"{key:18s} n={len(vals):4d} mean={statistics.mean(vals):6.2f} "
            f"p50={_percentile(vals, 50):6.2f} p95={_percentile(vals, 95):6.2f} max={max(vals):6.2f}"
        )
    print("\n== HOT (n_hits>0 or n_action_gate>0) ==")
    for key in STAGES:
        vals = hot_s[key]
        if not vals:
            continue
        print(
            f"{key:18s} n={len(vals):4d} mean={statistics.mean(vals):6.2f} "
            f"p50={_percentile(vals, 50):6.2f} p95={_percentile(vals, 95):6.2f} max={max(vals):6.2f}"
        )
    if hot_s.get("pick_total_ms"):
        hot_mean = statistics.mean(hot_s["pick_total_ms"])
        print("\n== share of pick_total_ms (HOT mean) ==")
        for key in STAGES:
            if key in ("pick_total_ms", "worker_ms") or not hot_s.get(key):
                continue
            print(f"  {key:18s} {statistics.mean(hot_s[key]) / hot_mean * 100:5.1f}%")
    return 0 if frames else 1


if __name__ == "__main__":
    raise SystemExit(main())

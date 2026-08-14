#!/usr/bin/env python3
"""离线剖析 pick_state：从 Redis 或 JSONL 读 pose，对比 gated/smoke 每帧耗时与 cProfile 热点。"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
import pstats
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.event_engine.pick_state_processor import PickStateProcessor


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://:visual-dps-local@127.0.0.1:6379/0")


def fetch_poses_from_redis(
    cameras: list[str], *, limit_per_cam: int = 80, stream_key: str = "pose:stream"
) -> dict[str, list[dict]]:
    import redis as sync_redis

    client = sync_redis.from_url(_redis_url(), decode_responses=True)
    rows = client.xrevrange(stream_key, count=max(limit_per_cam * len(cameras) * 4, 500))
    client.close()
    want = set(cameras)
    out: dict[str, list[dict]] = {c: [] for c in cameras}
    for _eid, fields in rows:
        raw = (fields or {}).get("payload")
        if not raw:
            continue
        try:
            pose = json.loads(raw)
        except json.JSONDecodeError:
            continue
        cid = str(pose.get("camera_id") or "")
        if cid not in want:
            continue
        bucket = out[cid]
        if len(bucket) >= limit_per_cam:
            continue
        fi = int(pose.get("frame_idx") or 0)
        if bucket and fi >= bucket[-1].get("frame_idx", 0):
            continue
        bucket.append(pose)
    for cid in cameras:
        out[cid] = sorted(out[cid], key=lambda p: int(p.get("frame_idx") or 0))
    return out


def load_boxes(json_path: Path) -> list:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("boxes") or data.get("data") or [])
    if isinstance(data, list):
        return data
    return []


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, int(len(s) * p / 100.0))
    return s[i]


def bench_config(
    config_path: Path,
    poses_by_cam: dict[str, list[dict]],
    boxes: list,
    *,
    profile_top: int = 15,
) -> dict:
    all_ms: list[float] = []
    per_cam_ms: dict[str, list[float]] = {}
    prof = cProfile.Profile()
    processors: dict[str, PickStateProcessor] = {}

    for cam, poses in poses_by_cam.items():
        if not poses:
            continue
        sample = poses[0]
        proc = PickStateProcessor(
            boxes,
            config_path=config_path,
            video_fps=15.0,
            infer_width=int(sample.get("infer_width") or 640),
            infer_height=int(sample.get("infer_height") or 360),
            record_id=cam,
        )
        processors[cam] = proc
        cam_ms: list[float] = []
        for pose in poses:
            prof.enable()
            t0 = time.perf_counter()
            proc.process(pose)
            elapsed = (time.perf_counter() - t0) * 1000.0
            prof.disable()
            cam_ms.append(elapsed)
            all_ms.append(elapsed)
        per_cam_ms[cam] = cam_ms

    stream = io.StringIO()
    ps = pstats.Stats(prof, stream=stream)
    ps.sort_stats("cumulative")
    ps.print_stats(profile_top)

    return {
        "config": str(config_path),
        "frames": len(all_ms),
        "total_ms_sum": round(sum(all_ms), 1),
        "per_frame_ms": {
            "p50": round(_percentile(all_ms, 50), 2),
            "p95": round(_percentile(all_ms, 95), 2),
            "max": round(max(all_ms) if all_ms else 0.0, 2),
            "mean": round(statistics.mean(all_ms) if all_ms else 0.0, 2),
        },
        "per_cam_mean_ms": {c: round(statistics.mean(v), 2) for c, v in per_cam_ms.items() if v},
        "cprofile_top": stream.getvalue(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="pick_state gated/smoke 离线 profiling")
    ap.add_argument(
        "--config",
        action="append",
        required=True,
        help="pipeline json，可多次指定 gated+smoke",
    )
    ap.add_argument(
        "--boxes-json",
        default="localdata/json/precise_boxes_new.json",
        help="货框标注 JSON",
    )
    ap.add_argument("--cameras", default="cam1,cam2,cam3,cam4,cam5,cam6,cam7,cam8")
    ap.add_argument("--limit-per-cam", type=int, default=60)
    ap.add_argument("--poses-json", help="已导出的 pose JSON（跳过 Redis）")
    ap.add_argument("--out", help="结果 JSON 路径")
    args = ap.parse_args()

    cameras = [c.strip() for c in args.cameras.split(",") if c.strip()]
    boxes_path = Path(args.boxes_json)
    if not boxes_path.is_file():
        print(f"缺少货框文件: {boxes_path}", file=sys.stderr)
        return 1
    boxes = load_boxes(boxes_path)

    if args.poses_json:
        poses_by_cam = json.loads(Path(args.poses_json).read_text(encoding="utf-8"))
    else:
        poses_by_cam = fetch_poses_from_redis(cameras, limit_per_cam=args.limit_per_cam)
        missing = [c for c in cameras if not poses_by_cam.get(c)]
        if missing:
            print(f"警告: 以下 camera 未采到 pose: {missing}", file=sys.stderr)

    results = []
    for cfg in args.config:
        path = Path(cfg)
        if not path.is_file():
            path = ROOT / cfg
        print(f"\n==> profiling {path.name} ...")
        row = bench_config(path, poses_by_cam, boxes)
        results.append(row)
        pf = row["per_frame_ms"]
        print(
            f"  frames={row['frames']} mean={pf['mean']}ms p50={pf['p50']} p95={pf['p95']} max={pf['max']}"
        )

    payload = {"poses_counts": {c: len(v) for c, v in poses_by_cam.items()}, "results": results}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\n已写入 {args.out}")
    else:
        print("\n" + text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

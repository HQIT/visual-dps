#!/usr/bin/env python3
"""分析 pipeline latency JSONL 日志，输出各段 p50/p95。"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def _load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            records.append(row)
    return records


def _dedupe_by_frame(records: list[dict]) -> list[dict]:
    """同一 frame_idx 保留 logged_at 最大的一条（较完整）。"""
    best: dict[int, dict] = {}
    for row in records:
        fi = int(row.get("frame_idx") or 0)
        logged = float(row.get("logged_at") or 0)
        prev = best.get(fi)
        if prev is None or logged >= float(prev.get("logged_at") or 0):
            best[fi] = row
    return [best[k] for k in sorted(best)]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * pct))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def _summarize(values: list[float]) -> str:
    if not values:
        return "n=0"
    return (
        f"n={len(values)} p50={statistics.median(values):.1f}ms "
        f"p95={_percentile(values, 0.95):.1f}ms max={max(values):.1f}ms"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="分析 latency JSONL")
    parser.add_argument("jsonl", nargs="?", default="localdata/logs/latency")
    parser.add_argument("--camera", default="", help="摄像头 id；省略则分析目录下全部")
    args = parser.parse_args()

    target = Path(args.jsonl)
    files: list[Path] = []
    if target.is_dir():
        if args.camera:
            files = [target / f"{args.camera}.jsonl"]
        else:
            files = sorted(target.glob("*.jsonl"))
    elif target.is_file():
        files = [target]
    else:
        print(f"未找到: {target}", file=sys.stderr)
        return 1

    if not files:
        print("无 JSONL 文件", file=sys.stderr)
        return 1

    for path in files:
        if not path.is_file():
            print(f"\n== {path.name} ==")
            print("  文件不存在")
            continue
        raw = _load_records(path)
        records = _dedupe_by_frame(raw)
        print(f"\n== {path.name} ==")
        print(f"  原始行数={len(raw)}  去重后 frame 数={len(records)}")
        if not records:
            continue

        latency_keys = sorted(
            {
                key
                for row in records
                for key in (row.get("latency_ms") or {}).keys()
            }
        )
        buckets: dict[str, list[float]] = defaultdict(list)
        for row in records:
            lat = row.get("latency_ms") or {}
            for key in latency_keys:
                value = lat.get(key)
                if value is not None:
                    buckets[key].append(float(value))

        print("  [各段延迟]")
        for key in latency_keys:
            print(f"    {key}: {_summarize(buckets[key])}")

        src_count: dict[str, int] = defaultdict(int)
        delta_vals: list[float] = []
        for row in records:
            meta = row.get("meta") or {}
            src_count[str(meta.get("skeleton_source") or "?")] += 1
            if meta.get("frame_idx_delta") is not None:
                delta_vals.append(float(meta["frame_idx_delta"]))
        print(f"  skeleton_source 分布: {dict(src_count)}")
        if delta_vals:
            print(
                f"  pose-event frame_idx_delta: p50={statistics.median(delta_vals):.0f} "
                f"max={max(delta_vals):.0f}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""从 [PIPELINE] worker_done 日志聚合 pick_state 分阶段耗时 p50/p95。"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

STAGE_KEYS = (
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
)

KV_RE = re.compile(r"(\w+)=([^\s]+)")


def _parse_line(line: str) -> dict[str, str | float | int] | None:
    if "stage=worker_done" not in line:
        return None
    out: dict[str, str | float | int] = {}
    for key, val in KV_RE.findall(line):
        if key in ("camera", "stage", "run_id", "time"):
            out[key] = val
            continue
        try:
            if "." in val:
                out[key] = float(val)
            else:
                out[key] = int(val)
        except ValueError:
            out[key] = val
    return out if out else None


def _percentile(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, int(len(s) * p / 100.0))
    return s[i]


def aggregate(rows: list[dict]) -> dict:
    all_stages: dict[str, list[float]] = defaultdict(list)
    hot: dict[str, list[float]] = defaultdict(list)
    by_cam: dict[str, list[dict]] = defaultdict(list)

    for row in rows:
        cam = str(row.get("camera") or "—")
        by_cam[cam].append(row)
        is_hot = int(row.get("n_hits") or 0) > 0 or int(row.get("n_action_gate") or 0) > 0
        for key in STAGE_KEYS:
            val = row.get(key)
            if isinstance(val, (int, float)):
                all_stages[key].append(float(val))
                if is_hot:
                    hot[key].append(float(val))

    def _summary(vals: list[float]) -> dict[str, float]:
        if not vals:
            return {"n": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
        return {
            "n": len(vals),
            "mean": round(statistics.mean(vals), 2),
            "p50": round(_percentile(vals, 50), 2),
            "p95": round(_percentile(vals, 95), 2),
            "max": round(max(vals), 2),
        }

    return {
        "frames": len(rows),
        "hot_frames": sum(
            1
            for r in rows
            if int(r.get("n_hits") or 0) > 0 or int(r.get("n_action_gate") or 0) > 0
        ),
        "all": {k: _summary(v) for k, v in sorted(all_stages.items())},
        "hot_path": {k: _summary(v) for k, v in sorted(hot.items())},
        "by_camera": {
            cam: {
                "frames": len(items),
                "pick_total_ms_p95": round(
                    _percentile(
                        [float(x["pick_total_ms"]) for x in items if isinstance(x.get("pick_total_ms"), (int, float))],
                        95,
                    ),
                    2,
                ),
            }
            for cam, items in sorted(by_cam.items())
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="聚合 worker_done 分阶段耗时")
    ap.add_argument("log", nargs="?", help="日志文件；缺省读 stdin")
    ap.add_argument("--hot-only", action="store_true", help="只打印热路径汇总")
    args = ap.parse_args()

    text = Path(args.log).read_text(encoding="utf-8") if args.log else sys.stdin.read()
    rows = [r for line in text.splitlines() if (r := _parse_line(line))]
    if not rows:
        print("未解析到 stage=worker_done 行", file=sys.stderr)
        return 1

    report = aggregate(rows)
    section = "hot_path" if args.hot_only else "all"
    print(f"frames={report['frames']} hot_frames={report['hot_frames']}")
    print(f"\n== {section} ==")
    for key, stats in report[section].items():
        if stats["n"] == 0:
            continue
        print(
            f"  {key:18s} n={stats['n']:4d} mean={stats['mean']:6.2f} "
            f"p50={stats['p50']:6.2f} p95={stats['p95']:6.2f} max={stats['max']:6.2f}"
        )
    if not args.hot_only:
        print("\n== by_camera pick_total_ms p95 ==")
        for cam, info in report["by_camera"].items():
            print(f"  {cam}: frames={info['frames']} pick_total_p95={info['pick_total_ms_p95']}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

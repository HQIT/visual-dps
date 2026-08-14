#!/usr/bin/env python3
"""8 路 worker-2 稳态采样：Redis lag、worker CPU、pose/event frame_gap。"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def _run(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")


def _redis_lines(raw: str) -> list[str]:
    return [ln.strip() for ln in raw.splitlines() if ln.strip() and "Warning" not in ln]


def redis_groups(redis_pw: str) -> dict:
    raw = _run(
        ["docker", "exec", "visual-dps-redis", "redis-cli", "-a", redis_pw, "XINFO", "GROUPS", "pose:stream"]
    )
    out: dict[str, str | int] = {}
    lines = _redis_lines(raw)
    for i, ln in enumerate(lines):
        if ln in ("name", "consumers", "pending", "lag") and i + 1 < len(lines):
            val = lines[i + 1]
            try:
                out[ln] = int(val)
            except ValueError:
                out[ln] = val
    return out


def redis_snapshot_lag(redis_pw: str, cam: str) -> dict:
    def _get(key: str) -> dict | None:
        raw = _run(["docker", "exec", "visual-dps-redis", "redis-cli", "-a", redis_pw, "GET", key])
        lines = _redis_lines(raw)
        if not lines:
            return None
        line = lines[-1]
        if line == "(nil)":
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    pose = _get(f"pose:snapshot:{cam}")
    event = _get(f"event:snapshot:{cam}")
    now = time.time()
    row: dict = {"camera_id": cam}
    if pose:
        row["pose_fi"] = int(pose.get("frame_idx") or 0)
        row["pose_age_ms"] = round((now - float(pose.get("ts") or now)) * 1000)
    if event:
        row["event_fi"] = int(event.get("frame_idx") or 0)
        row["event_age_ms"] = round((now - float(event.get("ts") or now)) * 1000)
    if pose and event:
        row["frame_gap"] = int(row["pose_fi"]) - int(row["event_fi"])
    return row


def worker_cpu() -> float | None:
    raw = _run(
        [
            "docker",
            "stats",
            "visual-dps-event-worker-2",
            "--no-stream",
            "--format",
            "{{.CPUPerc}}",
        ]
    )
    line = raw.strip().replace("%", "")
    try:
        return float(line)
    except ValueError:
        return None


def sample_once(redis_pw: str, cameras: list[str]) -> dict:
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    groups = redis_groups(redis_pw)
    cams = [redis_snapshot_lag(redis_pw, c) for c in cameras]
    gaps = [c["frame_gap"] for c in cams if "frame_gap" in c]
    return {
        "ts": ts,
        "stream": groups,
        "worker_cpu_pct": worker_cpu(),
        "cameras": cams,
        "frame_gap_max": max(gaps) if gaps else None,
        "frame_gap_avg": round(sum(gaps) / len(gaps), 2) if gaps else None,
    }


def summarize(samples: list[dict]) -> dict:
    lags = [s["stream"].get("lag") for s in samples if isinstance(s.get("stream"), dict)]
    lags = [int(x) for x in lags if isinstance(x, int)]
    cpus = [s["worker_cpu_pct"] for s in samples if isinstance(s.get("worker_cpu_pct"), (int, float))]
    gap_max = [s.get("frame_gap_max") for s in samples if s.get("frame_gap_max") is not None]
    gap_avg = [s.get("frame_gap_avg") for s in samples if s.get("frame_gap_avg") is not None]

    def _stats(vals: list[float | int]) -> dict:
        if not vals:
            return {}
        s = sorted(vals)
        return {
            "min": s[0],
            "max": s[-1],
            "avg": round(sum(s) / len(s), 2),
            "p95": s[min(len(s) - 1, int(len(s) * 0.95))],
        }

    return {
        "samples": len(samples),
        "lag": _stats(lags),
        "worker_cpu_pct": _stats([float(x) for x in cpus]),
        "frame_gap_max": _stats([float(x) for x in gap_max]),
        "frame_gap_avg": _stats([float(x) for x in gap_avg]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="gated / smoke")
    ap.add_argument("--duration-sec", type=int, default=300)
    ap.add_argument("--interval-sec", type=float, default=30.0)
    ap.add_argument("--redis-password", default="visual-dps-local")
    ap.add_argument("--cameras", default="cam1,cam2,cam3,cam4,cam5,cam6,cam7,cam8")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cameras = [c.strip() for c in args.cameras.split(",") if c.strip()]
    samples: list[dict] = []
    end = time.time() + args.duration_sec
    print(f"[{args.label}] 采样 {args.duration_sec}s，间隔 {args.interval_sec}s ...")
    while time.time() < end:
        row = sample_once(args.redis_password, cameras)
        samples.append(row)
        lag = row.get("stream", {}).get("lag")
        cpu = row.get("worker_cpu_pct")
        gmax = row.get("frame_gap_max")
        print(f"  {row['ts']} lag={lag} cpu={cpu}% frame_gap_max={gmax}")
        time.sleep(args.interval_sec)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "label": args.label,
        "duration_sec": args.duration_sec,
        "interval_sec": args.interval_sec,
        "summary": summarize(samples),
        "samples": samples,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"汇总: {json.dumps(payload['summary'], ensure_ascii=False)}")
    print(f"已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

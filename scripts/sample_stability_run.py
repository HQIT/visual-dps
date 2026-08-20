#!/usr/bin/env python3
"""稳定性连续观测：Redis lag、worker/infer/GPU 资源（输出 JSON，可配合 render 生成 md）。"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))


def bash(cmd: str) -> str:
    return subprocess.check_output(["bash", "-lc", cmd], text=True, stderr=subprocess.STDOUT)


def host_snapshot() -> dict:
    cpu_model = bash("grep 'model name' /proc/cpuinfo | head -1 | cut -d: -f2").strip()
    cpu_logical = int(bash("nproc").strip())
    mem = bash("free -g | awk '/Mem:/ {print $2, $3}'").strip().split()
    return {
        "cpu_model": cpu_model,
        "cpu_logical": cpu_logical,
        "ram_total_gib": float(mem[0]) if mem else None,
        "ram_used_gib": float(mem[1]) if len(mem) > 1 else None,
    }


def _parse_redis_groups(raw: str) -> dict:
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    out: dict = {}
    for i, ln in enumerate(lines):
        if i + 1 < len(lines) and ln in ("name", "consumers", "pending", "lag", "entries-read", "last-delivered-id"):
            val = lines[i + 1]
            try:
                out[ln.replace("-", "_")] = int(val)
            except ValueError:
                out[ln.replace("-", "_")] = val
    return out


def redis_metrics() -> dict:
    raw = bash(
        f"set -a && source '{ROOT}/.env' && set +a && "
        'docker exec visual-dps-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning XINFO GROUPS pose:stream'
    )
    groups = _parse_redis_groups(raw)
    xlen = int(
        bash(
            f"set -a && source '{ROOT}/.env' && set +a && "
            'docker exec visual-dps-redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning XLEN pose:stream'
        ).strip()
    )
    return {
        "ts": time.time(),
        "xlen": xlen,
        "entries_read": int(groups.get("entries_read", 0) or 0),
        "lag": int(groups.get("lag", 0) or 0),
        "pending": int(groups.get("pending", 0) or 0),
        "consumers": int(groups.get("consumers", 0) or 0),
    }


def _parse_docker_cpu_mem(raw: str) -> tuple[float, float]:
    cpu_sum = 0.0
    mem_sum = 0.0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        cpu_s = parts[1].replace("%", "").strip()
        mem_s = parts[2].split("/")[0].strip() if len(parts) > 2 else "0"
        try:
            cpu_sum += float(cpu_s)
        except ValueError:
            pass
        m = re.match(r"([\d.]+)\s*(\w+)", mem_s)
        if m:
            val = float(m.group(1))
            unit = m.group(2).lower()
            if unit == "gib":
                mem_sum += val * 1024
            elif unit == "mib":
                mem_sum += val
            elif unit == "kib":
                mem_sum += val / 1024
    return cpu_sum, mem_sum


def infer_stats() -> dict:
    raw = bash("docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' 2>/dev/null | grep visual-dps-infer || true")
    cpu, mem = _parse_docker_cpu_mem(raw)
    names = [ln.split("\t", 1)[0] for ln in raw.splitlines() if ln.strip()]
    return {"count": len(names), "names": sorted(names), "cpu_sum": round(cpu, 2), "mem_sum_mib": round(mem, 1)}


def container_cpu_mem(name: str) -> tuple[float | None, float | None]:
    raw = bash(f"docker stats --no-stream --format '{{{{.CPUPerc}}}}\t{{{{.MemUsage}}}}' {name} 2>/dev/null || true").strip()
    if not raw:
        return None, None
    parts = raw.split("\t")
    cpu = None
    mem = None
    try:
        cpu = float(parts[0].replace("%", "").strip())
    except (ValueError, IndexError):
        pass
    if len(parts) > 1:
        m = re.match(r"([\d.]+)\s*(\w+)", parts[1].split("/")[0].strip())
        if m:
            val = float(m.group(1))
            unit = m.group(2).lower()
            mem = val * 1024 if unit == "gib" else val
    return cpu, mem


def base_stack_cpu() -> float:
    raw = bash(
        "docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}' 2>/dev/null | "
        "grep -E 'visual-dps-ui|visual-dps-redis|mediamtx|visual-dps-event-worker$' || true"
    )
    total = 0.0
    for line in raw.splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 2:
            try:
                total += float(parts[1].replace("%", ""))
            except ValueError:
                pass
    return round(total, 2)


def gpu_metrics() -> dict | None:
    try:
        util, used, total = bash(
            "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits 2>/dev/null | head -1"
        ).strip().split(", ")
    except Exception:
        return None
    compute_procs = 0
    infer_vram = 0.0
    try:
        apps = bash("nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits 2>/dev/null || true")
        for line in apps.splitlines():
            line = line.strip()
            if not line:
                continue
            compute_procs += 1
            parts = line.split(", ")
            if len(parts) >= 2:
                try:
                    infer_vram += float(parts[1])
                except ValueError:
                    pass
    except Exception:
        pass
    return {
        "gpu_util_pct": int(float(util)),
        "vram_used_mib": int(float(used)),
        "vram_total_mib": int(float(total)),
        "compute_procs": compute_procs,
        "infer_vram_sum_mib": round(infer_vram, 1),
    }


def stack_snapshot() -> dict:
    infer = infer_stats()
    tag = ""
    try:
        tag = bash("docker inspect visual-dps-infer-cam1 --format '{{.Config.Image}}' 2>/dev/null || true").strip()
        if ":" in tag:
            tag = tag.split(":", 1)[1]
    except Exception:
        pass
    return {
        "active_infer_routes": infer.get("names", []),
        "infer_count": infer.get("count", 0),
        "image_tag": tag,
        "backend": "rtmpose_m",
        "event_worker": "visual-dps-event-worker-2",
        "notes": [
            "ORT 线程优化已启用（INFERENCE_ORT_* / OMP_NUM_THREADS 默认 1）",
            "worker-2 action_gate 共享 ONNX Session",
        ],
    }


def _stats(vals: list[float | int]) -> dict:
    if not vals:
        return {}
    s = sorted(vals)
    n = len(s)
    p95_i = min(n - 1, int(n * 0.95))
    return {
        "min": s[0],
        "max": s[-1],
        "avg": round(sum(s) / n, 2),
        "median": round(statistics.median(s), 2),
        "p95": s[p95_i],
        "last": vals[-1],
        "samples": [int(x) if isinstance(x, int) or float(x).is_integer() else x for x in vals],
    }


def stability_verdict(lag_stats: dict, infer_counts: list[int], duration_label: str) -> dict:
    lag_avg = lag_stats.get("avg", 999)
    lag_max = lag_stats.get("max", 999)
    stable = (
        infer_counts
        and min(infer_counts) == max(infer_counts)
        and lag_avg <= 10
        and lag_max <= 20
    )
    notes = []
    if lag_max > 20:
        notes.append(f"lag 峰值 {lag_max} 超过 20")
    if lag_avg > 10:
        notes.append(f"lag 均值 {lag_avg} 超过 10")
    if infer_counts and min(infer_counts) != max(infer_counts):
        notes.append("infer 路数在观测期内变化")
    return {
        "stable": stable,
        "summary": "稳定" if stable else "需关注",
        "criteria": f"{duration_label} 内 infer 路数不变、lag 均值≤10 且峰值≤20",
        "notes": notes,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-sec", type=int, default=600)
    ap.add_argument("--interval-sec", type=float, default=10.0)
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    args = ap.parse_args()

    start = datetime.now(TZ)
    stamp = start.strftime("%Y%m%d-%H%M")
    out_path = Path(args.out)
    if str(out_path).endswith(".json") and "stability" in out_path.name and stamp not in out_path.name:
        pass

    host = bash("hostname").strip()
    host_info = host_snapshot()
    stack = stack_snapshot()
    raw_samples: list[dict] = []

    print(f"采样 {args.duration_sec}s，间隔 {args.interval_sec}s → {out_path}")
    end = time.time() + args.duration_sec
    idx = 0
    while time.time() < end:
        idx += 1
        t0 = time.time()
        redis = redis_metrics()
        infer = infer_stats()
        w_cpu, w_mem = container_cpu_mem("visual-dps-event-worker-2")
        gpu = gpu_metrics()
        row = {
            "index": idx,
            "elapsed_sec": round(time.time() - start.timestamp(), 1),
            "redis": redis,
            "gpu": gpu,
            "infer_count": infer["count"],
            "infer_cpu_sum": infer["cpu_sum"],
            "infer_mem_sum_mib": infer["mem_sum_mib"],
            "worker2_cpu": w_cpu,
            "worker2_mem_mib": round(w_mem, 1) if w_mem is not None else None,
            "base_cpu_sum": base_stack_cpu(),
        }
        raw_samples.append(row)
        print(
            f"  [{idx}] lag={redis['lag']} pending={redis['pending']} "
            f"worker={w_cpu}% infer_cpu={infer['cpu_sum']}% infer_n={infer['count']}"
        )
        sleep_s = args.interval_sec - (time.time() - t0)
        if sleep_s > 0:
            time.sleep(sleep_s)

    end = datetime.now(TZ)
    lags = [s["redis"]["lag"] for s in raw_samples]
    pending = [s["redis"]["pending"] for s in raw_samples]
    entries = [s["redis"]["entries_read"] for s in raw_samples]
    delta_entries = entries[-1] - entries[0] if len(entries) >= 2 else 0
    elapsed = max(1.0, raw_samples[-1]["elapsed_sec"] - raw_samples[0]["elapsed_sec"]) if raw_samples else 1.0
    consume_rate = round(delta_entries / elapsed, 2)

    worker_cpus = [s["worker2_cpu"] for s in raw_samples if s.get("worker2_cpu") is not None]
    infer_cpus = [s["infer_cpu_sum"] for s in raw_samples]
    infer_counts = [s["infer_count"] for s in raw_samples]
    gpu_utils = [s["gpu"]["gpu_util_pct"] for s in raw_samples if s.get("gpu")]
    gpu_vram = [s["gpu"]["vram_used_mib"] for s in raw_samples if s.get("gpu")]

    lag_stats = _stats(lags)
    duration_label = f"{args.duration_sec // 60}min" if args.duration_sec % 60 == 0 else f"{args.duration_sec}s"
    verdict = stability_verdict(lag_stats, infer_counts, duration_label)

    lag_avg = lag_stats.get("avg", 0) or 0
    lag_max = lag_stats.get("max", 0) or 0
    rate = consume_rate or 1
    latency = {
        "queue_delay_ms_at_avg_lag": round(lag_avg / rate * 1000, 1) if rate else None,
        "queue_delay_ms_at_max_lag": round(lag_max / rate * 1000, 1) if rate else None,
        "pose_interval_ms": 133.3,
        "e2e_typical_ms": round(lag_avg / rate * 1000 + 133.3, 1) if rate else None,
    }

    payload = {
        "observation": {
            "started_at": start.isoformat(),
            "ended_at": end.isoformat(),
            "duration_sec": args.duration_sec,
            "interval_sec": args.interval_sec,
            "sample_count": len(raw_samples),
            "host": host,
            "host_snapshot": host_info,
        },
        "stack": stack,
        "redis_pose_stream": {
            "key": "pose:stream",
            "group": "event-workers",
            "lag": lag_stats,
            "pending": _stats(pending),
            "xlen_last": raw_samples[-1]["redis"]["xlen"] if raw_samples else None,
            "consume_rate_msg_per_s": consume_rate,
            "entries_read_delta": delta_entries,
            "entries_read_total": entries[-1] if entries else None,
        },
        "worker2": {
            "cpu_pct": _stats([float(x) for x in worker_cpus]),
            "mem_mib_last": raw_samples[-1].get("worker2_mem_mib") if raw_samples else None,
        },
        "infer": {
            "count": _stats(infer_counts),
            "cpu_sum_pct": _stats([float(x) for x in infer_cpus]),
            "mem_sum_mib_last": raw_samples[-1].get("infer_mem_sum_mib") if raw_samples else None,
        },
        "gpu": {
            "util_pct": _stats([float(x) for x in gpu_utils]) if gpu_utils else {},
            "vram_used_mib": _stats([float(x) for x in gpu_vram]) if gpu_vram else {},
            "compute_procs_last": raw_samples[-1]["gpu"]["compute_procs"] if raw_samples and raw_samples[-1].get("gpu") else None,
            "infer_vram_sum_mib_last": raw_samples[-1]["gpu"]["infer_vram_sum_mib"] if raw_samples and raw_samples[-1].get("gpu") else None,
        },
        "latency_estimate": latency,
        "stability_verdict": verdict,
        "raw_samples": raw_samples,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完成: lag_avg={lag_stats.get('avg')} worker_cpu_avg={payload['worker2']['cpu_pct'].get('avg')} rate={consume_rate} msg/s")
    print(f"已写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

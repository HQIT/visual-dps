#!/usr/bin/env python3
"""探测 Visual-DPS 各段延迟（无需碰撞触发，仅需推理在跑）。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request

REDIS_PW = "visual-dps-local"
CAMERA = "test_camera"
UI_PORT = 8046
SAMPLES = 8
INTERVAL = 0.4


def redis(*args: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "visual-dps-redis", "redis-cli", "-a", REDIS_PW, *args],
        capture_output=True,
        text=True,
    )
    return r.stdout


def latest_pose() -> dict | None:
    raw = redis("XREVRANGE", "pose:stream", "+", "-", "COUNT", "50")
    parts = [p for p in raw.split("\n") if p.strip() and not p.startswith("Warning")]
    for i, line in enumerate(parts):
        if line.strip() == "payload" and i + 1 < len(parts):
            try:
                data = json.loads(parts[i + 1])
            except json.JSONDecodeError:
                continue
            if str(data.get("camera_id") or "") == CAMERA:
                return data
    return None


def snapshot_pose() -> dict | None:
    raw = redis("GET", f"pose:snapshot:{CAMERA}")
    line = raw.strip().split("\n")[-1]
    if not line or line == "(nil)":
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def event_snapshot() -> dict | None:
    raw = redis("GET", f"event:snapshot:{CAMERA}")
    line = raw.strip().split("\n")[-1]
    if not line or line == "(nil)":
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def mediamtx_path() -> dict:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:9997/v3/paths/get/{CAMERA}", timeout=3
        ) as resp:
            return json.load(resp)
    except Exception as exc:
        return {"error": str(exc)}


def stream_info() -> dict:
    xlen = redis("XLEN", "pose:stream").strip().split()[-1]
    groups_raw = redis("XINFO", "GROUPS", "pose:stream")
    lag = pending = consumers = "?"
    lines = [l for l in groups_raw.splitlines() if l.strip() and not l.startswith("Warning")]
    for i, line in enumerate(lines):
        if line.strip() == "lag" and i + 1 < len(lines):
            lag = lines[i + 1].strip()
        if line.strip() == "pending" and i + 1 < len(lines):
            pending = lines[i + 1].strip()
        if line.strip() == "consumers" and i + 1 < len(lines):
            consumers = lines[i + 1].strip()
    return {"xlen": xlen, "lag": lag, "pending": pending, "consumers": consumers}


def main() -> int:
    print("=" * 60)
    print("Visual-DPS 链路延迟探测")
    print(f"camera={CAMERA}  samples={SAMPLES} interval={INTERVAL}s")
    print("说明: 无需人为碰撞；有 pose 即可测推理→Redis→worker 段")
    print("=" * 60)

    mtx = mediamtx_path()
    si = stream_info()
    print("\n[MediaMTX]")
    if "error" in mtx:
        print(f"  不可达: {mtx['error']}")
    else:
        print(f"  ready={mtx.get('ready')} tracks={mtx.get('tracks')} readers={len(mtx.get('readers') or [])}")
        print(f"  bytesReceived={mtx.get('bytesReceived')} bytesSent={mtx.get('bytesSent')}")

    print("\n[Redis pose:stream]")
    print(f"  XLEN={si['xlen']} (maxlen≈2000)  group_lag={si['lag']} pending={si['pending']} consumers={si['consumers']}")

    publish_lags: list[float] = []
    frame_rates: list[float] = []
    prev: tuple[float, int] | None = None

    print("\n[采样] wall_clock - pose_ts = 推理发布延迟 (ms)")
    for n in range(SAMPLES):
        now = time.time()
        pose = latest_pose()
        if not pose:
            print(f"  #{n+1}: 无 {CAMERA} pose（推理未跑或未写 Redis）")
            time.sleep(INTERVAL)
            continue
        ts = float(pose.get("ts") or 0)
        lag_ms = (now - ts) * 1000 if ts else -1
        publish_lags.append(lag_ms)
        fi = int(pose.get("frame_idx") or 0)
        persons = len(pose.get("persons") or [])
        if prev:
            dt = now - prev[0]
            df = fi - prev[1]
            if dt > 0 and df >= 0:
                frame_rates.append(df / dt)
        prev = (now, fi)
        print(
            f"  #{n+1}: frame={fi} persons={persons} "
            f"publish_lag_ms={lag_ms:.0f} infer={pose.get('infer_width')}x{pose.get('infer_height')}"
        )
        time.sleep(INTERVAL)

    snap = snapshot_pose()
    ev = event_snapshot()
    print("\n[快照对齐]")
    if snap:
        snap_lag = (time.time() - float(snap.get("ts") or 0)) * 1000
        print(f"  pose:snapshot lag_ms={snap_lag:.0f} frame={snap.get('frame_idx')}")
    else:
        print("  pose:snapshot: 无")
    if ev:
        ev_lag = (time.time() - float(ev.get("ts") or 0)) * 1000
        print(
            f"  event:snapshot lag_ms={ev_lag:.0f} frame={ev.get('frame_idx')} "
            f"hits={len(ev.get('collisions') or [])} alarms={len(ev.get('alarm_collisions') or [])}"
        )
    else:
        print("  event:snapshot: 无（无碰撞时正常）")

    print("\n[汇总]")
    if publish_lags:
        publish_lags.sort()
        mid = publish_lags[len(publish_lags) // 2]
        print(f"  推理→Redis 发布延迟 median={mid:.0f}ms  min={min(publish_lags):.0f}ms max={max(publish_lags):.0f}ms")
    else:
        print("  推理→Redis: 无数据")
    if frame_rates:
        avg_fps = sum(frame_rates) / len(frame_rates)
        print(f"  推理出帧率(由 frame_idx 估算) avg={avg_fps:.1f} fps  (目标 app_config frame_rate=15)")
    if si["lag"] not in ("?", "0", 0):
        print(f"  ⚠ worker 消费滞后 lag={si['lag']}（>0 表示在处理旧帧）")
    else:
        print(f"  worker 消费 lag={si['lag']}（0=跟得上）")

    print("\n[未在本脚本测量的段]")
    print("  • WebRTC 视频延迟: 需在浏览器对比 WHEP 画面与 SSE 骨架（双通路）")
    print("  • 碰撞检测延迟: 需有人进入 ROI 才有 event:snapshot / [COLLISION] 日志")
    print("  • captured_at: 当前 pose 未携带，无法精确测采帧时刻")
    return 0


if __name__ == "__main__":
    sys.exit(main())

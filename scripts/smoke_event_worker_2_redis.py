#!/usr/bin/env python3
"""往独立 pose stream 灌合成帧，轮询 event:snapshot 验收 worker-2。

默认连容器网内 Redis；在宿主机可：
  docker run --rm --network visual-dps-internal \\
    -e REDIS_URL=redis://:visual-dps-local@redis:6379/0 \\
    -e POSE_STREAM_KEY=pose:stream:ew2-smoke \\
    -v \"$PWD\":/app -w /app visual-dps-event-worker-2:latest \\
    python scripts/smoke_event_worker_2_redis.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import redis as sync_redis

from services.event_bus import get_event_snapshot, snapshot_key_for
from services.pose_bus import build_pose_frame, ensure_pose_stream_group, redis_url, stream_key_for_camera


CAMERA_ID = "ew2-smoke"
IW, IH = 852, 480


def _person(wx: float, wy: float) -> dict:
    kpts = [[0.0, 0.0, 0.0] for _ in range(17)]
    kpts[5] = [wx - 40, wy - 80, 0.9]
    kpts[6] = [wx + 40, wy - 80, 0.9]
    kpts[7] = [wx - 20, wy - 40, 0.9]
    kpts[8] = [wx + 20, wy - 40, 0.9]
    kpts[9] = [wx, wy, 0.9]
    kpts[10] = [wx + 10, wy + 5, 0.85]
    kpts[11] = [wx - 30, wy + 60, 0.8]
    kpts[12] = [wx + 30, wy + 60, 0.8]
    kpts[13] = [wx - 30, wy + 120, 0.8]
    kpts[14] = [wx + 30, wy + 120, 0.8]
    kpts[15] = [wx - 30, wy + 160, 0.7]
    kpts[16] = [wx + 30, wy + 160, 0.7]
    return {"person_id": 1, "person_track_id": 1, "keypoints": kpts}


def _xadd_pose(client: sync_redis.Redis, frame_idx: int, inside: bool) -> str:
    wx, wy = (400.0, 270.0) if inside else (100.0, 100.0)
    frame = build_pose_frame(
        camera_id=CAMERA_ID,
        frame_idx=frame_idx,
        persons=[_person(wx, wy)],
        infer_width=IW,
        infer_height=IH,
    )
    payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    stream_key = stream_key_for_camera(CAMERA_ID)
    return client.xadd(stream_key, {"payload": payload})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--poll", type=float, default=0.4)
    args = ap.parse_args()

    url = redis_url()
    stream_key = stream_key_for_camera(CAMERA_ID)
    print(f"[smoke] redis={url} stream={stream_key} camera={CAMERA_ID}")
    client = sync_redis.from_url(url, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        print(f"[smoke] redis ping failed: {exc}", file=sys.stderr)
        return 2

    ensure_pose_stream_group(stream_key, client=client)

    # 清掉旧 snapshot，避免误判
    client.delete(snapshot_key_for(CAMERA_ID))

    n = max(4, int(args.frames))
    half = n // 2
    for i in range(1, n + 1):
        mid = _xadd_pose(client, i, inside=(i > half))
        print(f"[smoke] xadd frame={i} inside={i > half} id={mid}")

    deadline = time.time() + float(args.timeout)
    last = None
    while time.time() < deadline:
        snap = get_event_snapshot(CAMERA_ID)
        last = snap
        if snap and (snap.get("alarm_collisions") or []):
            print("[smoke] OK alarm_collisions=", snap.get("alarm_collisions"))
            print("[smoke] snapshot=", json.dumps(snap, ensure_ascii=False)[:500])
            return 0
        if snap:
            print(
                f"[smoke] wait frame={snap.get('frame_idx')} "
                f"hits={snap.get('collisions')} alarms={snap.get('alarm_collisions')}"
            )
        time.sleep(float(args.poll))

    print("[smoke] FAIL timeout; last=", last, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

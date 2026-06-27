"""离线评测事件导出（CSV，Excel 可直接打开）。"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from services.box_identity import parse_collision_token
from services.video_time import format_video_time, resolve_video_time


def _yes_no(flag: bool) -> str:
    return "是" if flag else "否"


def build_event_export_rows(
    frames: list[dict[str, Any]],
    *,
    fallback_fps: float = 25.0,
) -> list[dict[str, Any]]:
    """按视频时间展开碰撞/告警，每个货框一行。"""
    rows: list[dict[str, Any]] = []
    fps = float(fallback_fps or 25.0)

    for frame in frames or []:
        hits = frame.get("collisions") if isinstance(frame.get("collisions"), list) else []
        alarms = frame.get("alarm_collisions") if isinstance(frame.get("alarm_collisions"), list) else []
        if not hits and not alarms:
            continue

        hit_set = {str(t).strip() for t in hits if str(t).strip()}
        alarm_set = {str(t).strip() for t in alarms if str(t).strip()}
        tokens = sorted(hit_set | alarm_set)

        video_sec, video_label = resolve_video_time(frame, fallback_fps=fps)
        frame_idx = int(frame.get("frame_idx") or 0)

        for token in tokens:
            shelf_code, box_id = parse_collision_token(token)
            rows.append(
                {
                    "video_time_sec": round(video_sec, 3),
                    "video_time": video_label,
                    "frame_idx": frame_idx,
                    "box_id": box_id or token,
                    "shelf_code": shelf_code,
                    "collision": _yes_no(token in hit_set),
                    "alarm": _yes_no(token in alarm_set),
                }
            )

    rows.sort(key=lambda r: (r["video_time_sec"], r["frame_idx"], r["box_id"]))
    return rows


def render_event_export_csv(
    frames: list[dict[str, Any]],
    *,
    fallback_fps: float = 25.0,
) -> bytes:
    """生成 UTF-8 BOM CSV，便于 Excel 正确识别中文。"""
    rows = build_event_export_rows(frames, fallback_fps=fallback_fps)
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(["视频时间(秒)", "视频时间", "帧号", "box_id", "货架", "碰撞", "告警"])
    for row in rows:
        writer.writerow(
            [
                row["video_time_sec"],
                row["video_time"],
                row["frame_idx"],
                row["box_id"],
                row["shelf_code"],
                row["collision"],
                row["alarm"],
            ]
        )
    return buf.getvalue().encode("utf-8")


def safe_export_filename(title: str, run_id: str, suffix: str = "events.csv") -> str:
    base = str(title or run_id or "benchmark").strip()
    base = re.sub(r'[\\/:*?"<>|\s]+', "_", base).strip("._")
    if not base:
        base = "benchmark"
    rid = re.sub(r'[\\/:*?"<>|\s]+', "_", str(run_id or "").strip())
    name = f"{base}_{rid}_{suffix}" if rid else f"{base}_{suffix}"
    return name[:180]

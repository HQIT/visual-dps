"""全局货位矩阵：汇总各摄像头标注网格与实时碰撞状态。"""

from __future__ import annotations

import time
from typing import Any

from services.annotation_service import flatten_annotation_boxes, load_camera_annotation
from services.box_identity import box_collision_token
from services.camera_service import list_cameras_with_status
from services.event_bus import get_event_snapshot
from services.inference_container_service import attach_inference_status


def _cell_state(
    roi_key: str,
    collisions: set[str],
    alarms: set[str],
    infer_running: bool,
) -> str:
    if roi_key and roi_key in alarms:
        return "alarm"
    if roi_key and roi_key in collisions:
        return "hit"
    if infer_running and roi_key:
        return "monitoring"
    if roi_key:
        return "configured"
    return "empty"


def _grid_dims(boxes: list[dict], grid_shape: list) -> tuple[int, int]:
    rows = cols = 0
    if isinstance(grid_shape, list) and len(grid_shape) >= 2:
        try:
            rows = max(0, int(grid_shape[0]))
            cols = max(0, int(grid_shape[1]))
        except (TypeError, ValueError):
            rows = cols = 0
    for box in boxes:
        try:
            layer = int(box.get("layer") or 0)
            column = int(box.get("column") or 0)
        except (TypeError, ValueError):
            continue
        if layer > 0:
            rows = max(rows, layer)
        if column > 0:
            cols = max(cols, column)
    return rows, cols


def _shelves_from_config(config_data: dict) -> list[dict]:
    if not isinstance(config_data, dict):
        return []

    shelves_out: list[dict] = []
    shelves_raw = config_data.get("shelves")
    if isinstance(shelves_raw, list) and shelves_raw:
        for shelf in shelves_raw:
            if not isinstance(shelf, dict):
                continue
            shelf_code = str(shelf.get("shelf_code") or "").strip()
            if not shelf_code:
                continue
            boxes = [
                dict(b, shelf_code=str(b.get("shelf_code") or shelf_code).strip() or shelf_code)
                for b in (shelf.get("boxes") or [])
                if isinstance(b, dict)
            ]
            shelves_out.append(
                {
                    "shelf_code": shelf_code,
                    "shelf_name": str(shelf.get("shelf_name") or "").strip(),
                    "grid_shape": shelf.get("grid_shape") if isinstance(shelf.get("grid_shape"), list) else [],
                    "boxes": boxes,
                }
            )
        if shelves_out:
            return shelves_out

    flat = flatten_annotation_boxes(config_data)
    if not flat:
        return []

    grouped: dict[str, dict] = {}
    for box in flat:
        if not isinstance(box, dict):
            continue
        shelf_code = str(box.get("shelf_code") or "").strip() or "DEFAULT"
        grouped.setdefault(
            shelf_code,
            {
                "shelf_code": shelf_code,
                "shelf_name": "",
                "grid_shape": config_data.get("grid_shape") if isinstance(config_data.get("grid_shape"), list) else [],
                "boxes": [],
            },
        )
        grouped[shelf_code]["boxes"].append(box)
    return list(grouped.values())


def _build_shelf_matrix(
    shelf: dict,
    *,
    collisions: set[str],
    alarms: set[str],
    infer_running: bool,
) -> dict[str, Any]:
    boxes = [b for b in (shelf.get("boxes") or []) if isinstance(b, dict)]
    rows, cols = _grid_dims(boxes, shelf.get("grid_shape") or [])
    box_map: dict[tuple[int, int], dict] = {}
    unplaced: list[dict] = []

    for box in boxes:
        try:
            layer = int(box.get("layer") or 0)
            column = int(box.get("column") or 0)
        except (TypeError, ValueError):
            layer = column = 0
        if layer > 0 and column > 0:
            box_map[(layer, column)] = box
        else:
            unplaced.append(box)

    cells: list[dict] = []
    if rows > 0 and cols > 0:
        for layer in range(1, rows + 1):
            for column in range(1, cols + 1):
                box = box_map.get((layer, column))
                if box:
                    roi_key = box_collision_token(box)
                    cells.append(
                        {
                            "layer": layer,
                            "column": column,
                            "box_id": str(box.get("box_id") or box.get("id") or ""),
                            "roi_key": roi_key,
                            "state": _cell_state(roi_key, collisions, alarms, infer_running),
                        }
                    )
                else:
                    cells.append(
                        {
                            "layer": layer,
                            "column": column,
                            "box_id": "",
                            "roi_key": "",
                            "state": "empty",
                        }
                    )

    for box in unplaced:
        roi_key = box_collision_token(box)
        cells.append(
            {
                "layer": int(box.get("layer") or 0) or None,
                "column": int(box.get("column") or 0) or None,
                "box_id": str(box.get("box_id") or box.get("id") or ""),
                "roi_key": roi_key,
                "state": _cell_state(roi_key, collisions, alarms, infer_running),
                "unplaced": True,
            }
        )

    return {
        "shelf_code": shelf.get("shelf_code") or "",
        "shelf_name": shelf.get("shelf_name") or "",
        "grid_shape": [rows, cols] if rows > 0 and cols > 0 else [],
        "cells": cells,
        "box_count": len(boxes),
    }


def build_matrix_overview(
    camera_ips_file: str,
    json_dir: str,
    default_json_file: str,
    *,
    frames_dir: str = "",
) -> dict[str, Any]:
    items = list_cameras_with_status(camera_ips_file, frames_dir, probe_online=False)
    enriched = {
        c.get("id"): c
        for c in attach_inference_status(items, cgroup_stats=False)
        if c.get("id")
    }

    cameras_out: list[dict] = []
    for cam in items:
        cid = str(cam.get("id") or cam.get("path") or "").strip()
        if not cid:
            continue

        infer = (enriched.get(cid) or {}).get("inference") or {}
        infer_status = str(infer.get("status") or "stopped")
        infer_running = infer_status in ("running", "starting")

        ann = load_camera_annotation(cid, json_dir, default_json_file, camera=cam)
        config_data = ann.get("data") if isinstance(ann.get("data"), dict) else {}
        shelves_raw = _shelves_from_config(config_data)

        event = get_event_snapshot(cid) or {}
        collisions = {str(x).strip() for x in (event.get("collisions") or []) if str(x).strip()}
        alarms = {str(x).strip() for x in (event.get("alarm_collisions") or []) if str(x).strip()}

        shelves = [
            _build_shelf_matrix(
                shelf,
                collisions=collisions,
                alarms=alarms,
                infer_running=infer_running,
            )
            for shelf in shelves_raw
        ]
        box_count = sum(s.get("box_count", 0) for s in shelves)

        cameras_out.append(
            {
                "id": cid,
                "name": str(cam.get("name") or cid),
                "online": bool(cam.get("online")),
                "inference": {
                    "status": infer_status,
                    "message": infer.get("message") or "",
                },
                "live": {
                    "ts": float(event.get("ts") or 0),
                    "frame_idx": int(event.get("frame_idx") or 0),
                    "collisions": sorted(collisions),
                    "alarm_collisions": sorted(alarms),
                },
                "annotation_found": ann.get("status") == "success" and box_count > 0,
                "box_count": box_count,
                "shelves": shelves,
            }
        )

    return {
        "status": "success",
        "updated_at": time.time(),
        "cameras": cameras_out,
    }

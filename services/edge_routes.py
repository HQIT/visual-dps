"""Edge Node REST ingest（status / pose / events → 中心 Redis）。"""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from core.edge_settings import load_edge_settings
from services.annotation_service import annotation_payload_for_api, load_camera_annotation
from services.camera_modes import (
    edge_expects_collision,
    edge_expects_pose,
    get_edge_capabilities,
    is_edge_camera,
)
from services.camera_store import get_camera
from services.edge_auth import verify_edge_bearer
from services.edge_event_ingest import ingest_edge_event_frame
from services.edge_status import EDGE_STATES, EDGE_STATUS_SCHEMA, publish_edge_status
from services.event_bus import EVENT_SCHEMA_VERSION
from services.pose_bus import POSE_SCHEMA_VERSION, publish_pose_frame

POSE_KIND = "pose"
EVENT_KIND = "event"


class EdgeKeypoint(BaseModel):
    x: float
    y: float
    score: float = Field(default=0.0, ge=0.0)


class EdgePerson(BaseModel):
    person_id: int = 0
    keypoints: list[list[float]] = Field(default_factory=list)

    @field_validator("keypoints")
    @classmethod
    def validate_keypoints(cls, value: list[list[float]]) -> list[list[float]]:
        out: list[list[float]] = []
        for kp in value:
            if not isinstance(kp, (list, tuple)) or len(kp) < 2:
                continue
            score = float(kp[2]) if len(kp) > 2 else 0.0
            out.append([float(kp[0]), float(kp[1]), score])
        return out


class EdgePoseIngestBody(BaseModel):
    schema: int = Field(default=POSE_SCHEMA_VERSION)
    kind: str = POSE_KIND
    ts: float | None = None
    camera_id: str | None = None
    frame_idx: int = Field(..., ge=0)
    infer_width: int = Field(..., gt=0)
    infer_height: int = Field(..., gt=0)
    persons: list[EdgePerson] = Field(default_factory=list)
    skeletons: list[EdgePerson] | None = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if str(value or "").strip().lower() != POSE_KIND:
            raise ValueError('kind 必须为 "pose"')
        return POSE_KIND


class EdgeStatusBody(BaseModel):
    schema: int = Field(default=EDGE_STATUS_SCHEMA)
    ts: float | None = None
    state: str = Field(default="running")
    message: str = Field(default="", max_length=512)
    agent_version: str = Field(default="", max_length=64)
    metrics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("state")
    @classmethod
    def validate_state(cls, value: str) -> str:
        state = str(value or "running").strip().lower()
        if state not in EDGE_STATES:
            raise ValueError(f'state 必须为 {", ".join(sorted(EDGE_STATES))}')
        return state


class EdgeEventIngestBody(BaseModel):
    schema: int = Field(default=EVENT_SCHEMA_VERSION)
    kind: str = EVENT_KIND
    ts: float | None = None
    frame_idx: int = Field(..., ge=0)
    collisions: list[str] = Field(default_factory=list)
    alarm_collisions: list[str] = Field(default_factory=list)
    skeletons: list[EdgePerson] | None = None

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if str(value or "").strip().lower() != EVENT_KIND:
            raise ValueError('kind 必须为 "event"')
        return EVENT_KIND


def _persons_payload(body: EdgePoseIngestBody) -> list[dict[str, Any]]:
    if body.persons:
        source = body.persons
    elif body.skeletons:
        source = body.skeletons
    else:
        return []
    out: list[dict[str, Any]] = []
    for person in source:
        out.append(
            {
                "person_id": int(person.person_id),
                "keypoints": list(person.keypoints),
            }
        )
    return out


def register_edge_routes(
    router: APIRouter,
    *,
    camera_ips_file: str,
    app_config: dict | None = None,
    json_dir: str | None = None,
    default_json_file: str | None = None,
    callback_reporter=None,
) -> None:
    settings_holder = {"settings": load_edge_settings(app_config)}
    json_dir_resolved = (
        str(json_dir or "").strip()
        or str((app_config or {}).get("paths", {}).get("json_dir", "localdata/json"))
    )
    default_json_resolved = (
        str(default_json_file or "").strip()
        or str((app_config or {}).get("paths", {}).get("default_json_file", "localdata/json/precise_boxes_new.json"))
    )

    def edge_settings() -> dict:
        return settings_holder["settings"]

    def _require_edge_auth(request: Request) -> dict[str, Any]:
        return verify_edge_bearer(request, edge_settings())

    def _require_edge_camera(camera_id: str) -> dict:
        cid = str(camera_id or "").strip()
        if not cid:
            raise HTTPException(status_code=400, detail="camera_id 无效")
        found = get_camera(camera_ips_file, cid)
        if found.get("error"):
            raise HTTPException(status_code=404, detail="未找到该摄像头")
        cam = found["camera"]
        if not is_edge_camera(cam):
            raise HTTPException(status_code=400, detail="该摄像头未配置为边缘推理（inference_mode=edge）")
        if cam.get("enabled") is False:
            raise HTTPException(status_code=403, detail="该摄像头已禁用")
        return cam

    @router.post("/edge/v1/cameras/{camera_id}/status")
    async def ingest_edge_status(camera_id: str, body: EdgeStatusBody, request: Request):
        _require_edge_auth(request)
        cam = _require_edge_camera(camera_id)

        payload = {
            "schema": int(body.schema),
            "ts": float(body.ts or time.time()),
            "state": body.state,
            "message": str(body.message or "").strip(),
            "agent_version": str(body.agent_version or "").strip(),
            "capabilities": get_edge_capabilities(cam),
            "metrics": body.metrics if isinstance(body.metrics, dict) else {},
        }
        ok = publish_edge_status(camera_id, payload)
        if not ok:
            raise HTTPException(status_code=503, detail="状态写入失败，请检查中心 Redis")

        return {
            "status": "accepted",
            "camera_id": camera_id,
            "state": body.state,
            "ts": payload["ts"],
        }

    @router.post("/edge/v1/cameras/{camera_id}/pose")
    async def ingest_edge_pose(camera_id: str, body: EdgePoseIngestBody, request: Request):
        _require_edge_auth(request)
        cam = _require_edge_camera(camera_id)
        if not edge_expects_pose(cam):
            raise HTTPException(status_code=400, detail="该摄像头未启用边缘 pose 能力")

        persons = _persons_payload(body)
        ok = publish_pose_frame(
            camera_id,
            frame_idx=int(body.frame_idx),
            persons=persons,
            infer_width=int(body.infer_width),
            infer_height=int(body.infer_height),
        )
        if not ok:
            raise HTTPException(status_code=503, detail="姿态写入失败，请检查中心 Redis")

        return {
            "status": "accepted",
            "camera_id": camera_id,
            "frame_idx": int(body.frame_idx),
            "ts": float(body.ts or time.time()),
            "persons": len(persons),
        }

    @router.get("/edge/v1/cameras/{camera_id}/annotation")
    async def read_edge_annotation(camera_id: str, request: Request):
        _require_edge_auth(request)
        cam = _require_edge_camera(camera_id)
        loaded = load_camera_annotation(
            camera_id,
            json_dir_resolved,
            default_json_resolved,
            camera=cam,
        )
        if loaded.get("status") != "success":
            raise HTTPException(status_code=404, detail=loaded.get("error") or "annotation not found")
        json_path = str(loaded.get("json_path") or "")
        revision = ""
        updated_at = None
        if json_path and os.path.isfile(json_path):
            updated_at = os.path.getmtime(json_path)
            revision = str(int(updated_at))
        payload = annotation_payload_for_api(loaded)
        payload["camera_id"] = camera_id
        payload["revision"] = revision
        payload["updated_at"] = updated_at
        return payload

    @router.post("/edge/v1/cameras/{camera_id}/events")
    async def ingest_edge_events(camera_id: str, body: EdgeEventIngestBody, request: Request):
        _require_edge_auth(request)
        cam = _require_edge_camera(camera_id)
        if not edge_expects_collision(cam):
            raise HTTPException(status_code=400, detail="该摄像头未启用边缘 collision 能力")

        skeletons = None
        if body.skeletons:
            skeletons = [
                {"person_id": int(p.person_id), "keypoints": list(p.keypoints)}
                for p in body.skeletons
            ]

        frame = {
            "schema": int(body.schema),
            "kind": EVENT_KIND,
            "ts": float(body.ts or time.time()),
            "camera_id": camera_id,
            "frame_idx": int(body.frame_idx),
            "collisions": list(body.collisions or []),
            "alarm_collisions": list(body.alarm_collisions or []),
        }
        if skeletons is not None:
            frame["skeletons"] = skeletons

        ok = ingest_edge_event_frame(
            camera_id,
            frame=frame,
            camera=cam,
            app_config=app_config,
            callback_reporter=callback_reporter,
        )
        if not ok:
            raise HTTPException(status_code=503, detail="事件写入失败，请检查中心 Redis")

        return {
            "status": "accepted",
            "camera_id": camera_id,
            "frame_idx": int(body.frame_idx),
            "ts": frame["ts"],
            "alarm_collisions": len(body.alarm_collisions or []),
        }

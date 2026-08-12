"""PoseFrame → pick_state pipeline → collisions / alarm_collisions（对齐 CollisionProcessor 出口）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from pick_state.boxes import BoxDef, polygon_center_and_inradius
from pick_state.features.bank import FeatureBank
from pick_state.pipeline.box_trigger import BoxTrigger
from pick_state.pipeline.runner import PickStatePipeline, load_pipeline_config
from pick_state.pipeline.types import FrameContext
from services.box_identity import box_collision_token
from services.event_engine.collision import PersonTrackAssigner

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "pick_state" / "configs" / "pipeline.v5_gated.json"


def annotation_boxes_to_boxdefs(boxes: list) -> list[BoxDef]:
    out: list[BoxDef] = []
    for box in boxes or []:
        if not isinstance(box, dict):
            continue
        token = box_collision_token(box)
        box_id = str(box.get("box_id") or box.get("id") or "").strip()
        if not token or not box_id:
            continue
        contour = box.get("orig_contour")
        if contour is None:
            pts = box.get("video_polygon") or []
            if len(pts) < 3:
                continue
            arr = np.asarray(pts, dtype=np.float64)
            contour = np.int32(arr).reshape((-1, 1, 2))
            center, inradius = polygon_center_and_inradius(arr)
        else:
            arr = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
            center, inradius = polygon_center_and_inradius(arr)
        out.append(
            BoxDef(
                box_id=box_id,
                token=token,
                contour=contour,
                center=center,
                inradius=max(1.0, float(inradius)),
                layer=box.get("layer"),
                column=box.get("column"),
            )
        )
    return out


class PickStateProcessor:
    """消费 PoseFrame，输出与 CollisionProcessor 相同键的结果字典。"""

    def __init__(
        self,
        boxes: list,
        *,
        config_path: str | Path | None = None,
        video_fps: float = 15.0,
        infer_width: int = 0,
        infer_height: int = 0,
        record_id: str = "",
    ):
        path = Path(config_path or _DEFAULT_CONFIG)
        self.config_path = path
        self.config = load_pipeline_config(path)
        self.pipeline = PickStatePipeline(self.config)
        self.video_fps = max(1.0, float(video_fps))
        # 兼容 EventRedisWorker._apply_runtime_settings 对 alarm_* 的赋值
        self.alarm_min_consecutive_frames = int(
            (self.config.get("alarm") or {}).get("min_consecutive_frames", 3)
        )
        self.alarm_cooldown_frames = int(
            (self.config.get("alarm") or {}).get("cooldown_frames", 0)
        )
        self.person_assigner = PersonTrackAssigner(max_match_dist=220.0, stale_sec=1.2)
        self.record_id = record_id or "live"
        self.infer_width = int(infer_width)
        self.infer_height = int(infer_height)
        self._set_boxes(boxes)
        if self.infer_width > 0 and self.infer_height > 0:
            self._configure()

    def _set_boxes(self, boxes: list) -> None:
        self.boxes = boxes
        self.boxdefs = annotation_boxes_to_boxdefs(boxes)
        wrist_min = float((self.config.get("box_trigger") or {}).get("wrist_score_min", 0.15))
        self.trigger = BoxTrigger(self.boxdefs, wrist_score_min=wrist_min)

    def _configure(self) -> None:
        self.bank = FeatureBank(
            infer_width=self.infer_width,
            infer_height=self.infer_height,
            video_fps=self.video_fps,
        )
        self.pipeline.configure_dims(
            infer_width=self.infer_width,
            infer_height=self.infer_height,
            video_fps=self.video_fps,
        )
        self.pipeline.reset_session()
        self.bank.reset()

    def reset_infer_session(self) -> None:
        self.person_assigner.tracks.clear()
        self.person_assigner.next_id = 1
        if hasattr(self, "bank"):
            self.bank.reset()
        self.pipeline.reset_session()

    def _ensure_track_ids(self, pose_frame: dict) -> list[dict]:
        frame_idx = int(pose_frame.get("frame_idx") or 0)
        now_ts = frame_idx / self.video_fps if self.video_fps > 0 else 0.0
        persons = pose_frame.get("persons") or pose_frame.get("skeletons") or []
        out: list[dict] = []
        used: set[int] = set()
        for person in persons:
            if not isinstance(person, dict):
                continue
            skel = dict(person)
            keypoints = skel.get("keypoints") or []
            if "person_track_id" in skel and skel["person_track_id"] is not None:
                out.append(skel)
                continue
            if len(keypoints) < 11:
                out.append(skel)
                continue

            def _pt(i: int) -> tuple[float, float, float]:
                kp = keypoints[i]
                return float(kp[0]), float(kp[1]), float(kp[2]) if len(kp) > 2 else 0.0

            lx, ly, ls = _pt(5)
            rx, ry, rs = _pt(6)
            if ls > 0.2 and rs > 0.2:
                ax, ay = (lx + rx) / 2.0, (ly + ry) / 2.0
            else:
                xs = [float(k[0]) for k in keypoints if len(k) >= 2]
                ys = [float(k[1]) for k in keypoints if len(k) >= 2]
                ax = sum(xs) / len(xs) if xs else 0.0
                ay = sum(ys) / len(ys) if ys else 0.0
            tid = self.person_assigner.assign(ax, ay, now_ts=now_ts, occupied_track_ids=used)
            skel["person_track_id"] = tid
            out.append(skel)
        return out

    def process(self, pose_frame: dict, prefilter: Any = None) -> dict:
        del prefilter  # pick_state 自带门控，忽略硬规则 prefilter
        frame_idx = int(pose_frame.get("frame_idx") or 0)
        persons = self._ensure_track_ids(pose_frame)
        frame = {"frame_idx": frame_idx, "persons": persons}

        if not hasattr(self, "bank"):
            iw = int(pose_frame.get("infer_width") or self.infer_width or 0)
            ih = int(pose_frame.get("infer_height") or self.infer_height or 0)
            if iw > 0 and ih > 0:
                self.infer_width, self.infer_height = iw, ih
                self._configure()
            else:
                return {
                    "collisions": [],
                    "alarm_collisions": [],
                    "skeletons": persons,
                    "frame_idx": frame_idx,
                    "prefilter_logs": [],
                }

        rows = self.bank.rows_for_frame(frame)
        by_tid = {str(p.get("person_track_id")): p for p in persons}
        for r in rows:
            if "_person" not in r:
                tid = str(r.get("person_track_id") or "")
                if tid in by_tid:
                    r["_person"] = by_tid[tid]

        ctx = FrameContext(record_id=self.record_id, frame_idx=frame_idx)
        result = self.pipeline.process_frame(
            ctx,
            feature_rows=rows,
            box_trigger=self.trigger,
            infer_height=self.infer_height,
        )
        return {
            "collisions": list(result.box_hits or []),
            "alarm_collisions": list(result.alarm_hits or []),
            "skeletons": persons,
            "frame_idx": frame_idx,
            "prefilter_logs": [],
        }

"""手腕 vs 货框碰撞检测与连续帧报警门控。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import cv2

from services.box_identity import box_collision_token
from services.event_engine.event_log import PrefilterLogEntry


class CollisionPrefilterProtocol(Protocol):
    def evaluate(self, pose_frame: dict, track_id: int, person: dict): ...

    def should_block(self, pose_frame: dict, track_id: int, person: dict) -> bool: ...


@dataclass
class TrackState:
    abs_x: float
    abs_y: float
    ts_sec: float


class PersonTrackAssigner:
    def __init__(self, max_match_dist=220.0, stale_sec=1.2):
        self.max_match_dist = max_match_dist
        self.stale_sec = stale_sec
        self.next_id = 1
        self.tracks: dict[int, TrackState] = {}

    def _cleanup(self, now_ts: float):
        dead_keys = [k for k, st in self.tracks.items() if now_ts - st.ts_sec > self.stale_sec]
        for k in dead_keys:
            self.tracks.pop(k, None)

    def assign(self, abs_x: float, abs_y: float, now_ts: float, occupied_track_ids=None) -> int:
        self._cleanup(now_ts)
        occupied = occupied_track_ids if occupied_track_ids is not None else set()

        best_tid = None
        best_dist = 1e9
        for tid, st in self.tracks.items():
            if tid in occupied:
                continue
            dist = math.hypot(abs_x - st.abs_x, abs_y - st.abs_y)
            if dist < best_dist:
                best_dist = dist
                best_tid = tid

        if best_tid is None or best_dist > self.max_match_dist:
            tid = self.next_id
            self.next_id += 1
            self.tracks[tid] = TrackState(abs_x=abs_x, abs_y=abs_y, ts_sec=now_ts)
            occupied.add(tid)
            return tid

        self.tracks[best_tid] = TrackState(abs_x=abs_x, abs_y=abs_y, ts_sec=now_ts)
        occupied.add(best_tid)
        return best_tid


class CollisionProcessor:
    """消费 PoseFrame，输出碰撞 token 与报警 token。"""

    def __init__(
        self,
        boxes: list,
        *,
        alarm_min_consecutive_frames: int = 3,
        alarm_cooldown_frames: int = 12,
        video_fps: float = 25.0,
    ):
        self.boxes = boxes
        self.alarm_min_consecutive_frames = max(1, int(alarm_min_consecutive_frames))
        self.alarm_cooldown_frames = max(0, int(alarm_cooldown_frames))
        self.video_fps = max(1.0, float(video_fps))
        self.person_assigner = PersonTrackAssigner(max_match_dist=220.0, stale_sec=1.2)
        self._box_consecutive_hits: dict[str, int] = {}
        self._box_last_alarm_frame: dict[str, int] = {}

    def reset_infer_session(self) -> None:
        """infer 重启导致 frame_idx 回绕时清空会话内状态。"""
        self._box_consecutive_hits.clear()
        self._box_last_alarm_frame.clear()
        self.person_assigner.tracks.clear()
        self.person_assigner.next_id = 1

    @staticmethod
    def _wrist_collision_tokens(keypoints: list, boxes: list) -> list[str]:
        """手腕进框时返回碰撞 token（与是否被门控无关）。"""
        tokens: list[str] = []
        for kpt_idx in (9, 10):
            if len(keypoints) <= kpt_idx:
                continue
            kp = keypoints[kpt_idx]
            if len(kp) < 3 or float(kp[2]) <= 0.3:
                continue
            wx, wy = float(kp[0]), float(kp[1])
            for box in boxes:
                contour = box.get("orig_contour")
                if contour is None:
                    continue
                if cv2.pointPolygonTest(contour, (wx, wy), False) >= 0:
                    token = box_collision_token(box)
                    if token:
                        tokens.append(token)
                    break
        return list(set(tokens))

    def process(
        self,
        pose_frame: dict,
        prefilter: CollisionPrefilterProtocol | None = None,
    ) -> dict:
        """返回 collisions、alarm_collisions、带 track 的 skeletons（供 SSE 合并）。"""
        frame_idx = int(pose_frame.get("frame_idx") or 0)
        now_ts = frame_idx / self.video_fps if self.video_fps > 0 else 0.0
        persons = pose_frame.get("persons") or pose_frame.get("skeletons") or []

        active_collisions: list[str] = []
        skeletons_data = []
        used_track_ids: set[int] = set()
        prefilter_logs: list[PrefilterLogEntry] = []

        for person in persons:
            if not isinstance(person, dict):
                continue
            p_idx = int(person.get("person_id", len(skeletons_data)))
            keypoints = person.get("keypoints") or []
            if len(keypoints) < 11:
                skeletons_data.append(person)
                continue

            def _pt(i):
                kp = keypoints[i]
                return float(kp[0]), float(kp[1]), float(kp[2]) if len(kp) > 2 else 0.0

            lx, ly, ls = _pt(5)
            rx, ry, rs = _pt(6)
            if ls > 0.2 and rs > 0.2:
                anchor_x = (lx + rx) / 2.0
                anchor_y = (ly + ry) / 2.0
            else:
                xs = [float(k[0]) for k in keypoints if len(k) >= 2]
                ys = [float(k[1]) for k in keypoints if len(k) >= 2]
                anchor_x = sum(xs) / len(xs) if xs else 0.0
                anchor_y = sum(ys) / len(ys) if ys else 0.0

            person_track_id = self.person_assigner.assign(
                anchor_x, anchor_y, now_ts=now_ts, occupied_track_ids=used_track_ids
            )
            skel = dict(person)
            skel["person_track_id"] = person_track_id
            skeletons_data.append(skel)

            decision = None
            if prefilter is not None:
                decision = prefilter.evaluate(pose_frame, person_track_id, person)

            collision_tokens = self._wrist_collision_tokens(keypoints, self.boxes)
            if prefilter is not None and decision is not None and collision_tokens:
                prefilter_logs.append(PrefilterLogEntry(decision=decision, hits=collision_tokens))

            if decision is not None and decision.blocked:
                continue

            for token in collision_tokens:
                active_collisions.append(token)

        active_collisions = list(set(active_collisions))
        current_tokens = set(active_collisions)

        for token in list(self._box_consecutive_hits.keys()):
            if token not in current_tokens:
                self._box_consecutive_hits[token] = 0

        alarm_collisions: list[str] = []
        for token in current_tokens:
            self._box_consecutive_hits[token] = self._box_consecutive_hits.get(token, 0) + 1
            last_alarm = self._box_last_alarm_frame.get(token, -10**9)
            if (
                self._box_consecutive_hits[token] >= self.alarm_min_consecutive_frames
                and frame_idx - last_alarm >= self.alarm_cooldown_frames
            ):
                alarm_collisions.append(token)
                self._box_last_alarm_frame[token] = frame_idx

        return {
            "collisions": active_collisions,
            "alarm_collisions": alarm_collisions,
            "skeletons": skeletons_data,
            "frame_idx": frame_idx,
            "prefilter_logs": prefilter_logs,
        }

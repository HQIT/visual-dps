"""手腕 vs 货框碰撞检测与连续帧报警门控。

实验性改进（dev-collision-roi）：
- 软边界容差：手部点到 ROI 的有符号距离 >= -margin 即命中，抗边界抖动。
- 前臂外推：腕点 + 肘→腕方向外推点，缓解手伸入货格时腕点被遮挡/漂移。
- M-of-N 滑窗门控：窗口内命中 >= M 触发，替代硬清零连续帧，容忍偶发漏检。
- 真实时间戳：优先用 pose 帧的 ts，推理帧率波动时跟踪/门控不失真。
- 加权躯干 anchor：肩+髋按置信度加权，低置信度时跟踪更稳。
- 卡尔曼预测 + 全局贪心匹配：减少密集/交叉场景下的 ID 跳变。
- (track_id, token) 维度门控：多人先后取同一货位互不抢占。

阈值集中在 CollisionParams（来源 app_config.inference.collision，env 兜底）。
"""

from __future__ import annotations

import math
import os
from collections import deque
from dataclasses import dataclass

import cv2

from services.box_identity import box_collision_token


def _first_float(*vals, default: float) -> float:
    for v in vals:
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return float(default)


def _first_int(*vals, default: int) -> int:
    return int(_first_float(*vals, default=float(default)))


def _first_bool(*vals, default: bool) -> bool:
    for v in vals:
        if v is None:
            continue
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            if not v.strip():
                continue
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    return default


def _env(name: str):
    return os.environ.get(name)


@dataclass
class CollisionParams:
    """碰撞/告警/跟踪可调参数（提取自配置，便于前端未来配置）。"""

    min_consecutive_frames: int = 3
    cooldown_frames: int = 6
    window_frames: int = 6
    wrist_conf: float = 0.3
    elbow_conf: float = 0.3
    forearm_extend_ratio: float = 0.4
    boundary_margin_ratio: float = 0.12
    boundary_margin_min_px: float = 8.0
    track_max_match_dist: float = 220.0
    track_stale_sec: float = 1.2
    per_track_gating: bool = True

    @classmethod
    def from_config(cls, infer_cfg: dict | None) -> "CollisionParams":
        cfg = infer_cfg if isinstance(infer_cfg, dict) else {}
        coll = cfg.get("collision") if isinstance(cfg.get("collision"), dict) else {}

        min_consec = _first_int(
            coll.get("min_consecutive_frames"),
            cfg.get("alarm_min_consecutive_frames"),
            default=3,
        )
        min_consec = max(1, min_consec)
        window = _first_int(
            coll.get("window_frames"),
            _env("COLLISION_ALARM_WINDOW_FRAMES"),
            default=min_consec * 2,
        )
        return cls(
            min_consecutive_frames=min_consec,
            cooldown_frames=max(
                1,
                _first_int(
                    coll.get("cooldown_frames"),
                    cfg.get("alarm_cooldown_frames"),
                    default=6,
                ),
            ),
            window_frames=max(min_consec, window),
            wrist_conf=_first_float(coll.get("wrist_conf"), _env("COLLISION_WRIST_CONF"), default=0.3),
            elbow_conf=_first_float(coll.get("elbow_conf"), _env("COLLISION_ELBOW_CONF"), default=0.3),
            forearm_extend_ratio=_first_float(
                coll.get("forearm_extend_ratio"), _env("COLLISION_FOREARM_EXTEND_RATIO"), default=0.4
            ),
            boundary_margin_ratio=_first_float(
                coll.get("boundary_margin_ratio"), _env("COLLISION_BOUNDARY_MARGIN_RATIO"), default=0.12
            ),
            boundary_margin_min_px=_first_float(
                coll.get("boundary_margin_min_px"), _env("COLLISION_BOUNDARY_MARGIN_MIN_PX"), default=8.0
            ),
            track_max_match_dist=_first_float(
                coll.get("track_max_match_dist"), _env("COLLISION_TRACK_MAX_DIST"), default=220.0
            ),
            track_stale_sec=_first_float(
                coll.get("track_stale_sec"), _env("COLLISION_TRACK_STALE_SEC"), default=1.2
            ),
            per_track_gating=_first_bool(
                coll.get("per_track_gating"), _env("COLLISION_PER_TRACK_GATING"), default=True
            ),
        )


@dataclass
class TrackState:
    x: float
    y: float
    vx: float
    vy: float
    ts_sec: float


class PersonTrackAssigner:
    """恒速卡尔曼式预测 + 全局贪心匹配，减少 ID 跳变。"""

    def __init__(self, max_match_dist: float = 220.0, stale_sec: float = 1.2, vel_alpha: float = 0.5):
        self.max_match_dist = float(max_match_dist)
        self.stale_sec = float(stale_sec)
        self.vel_alpha = float(vel_alpha)
        self.next_id = 1
        self.tracks: dict[int, TrackState] = {}

    def _cleanup(self, now_ts: float) -> None:
        dead = [k for k, st in self.tracks.items() if now_ts - st.ts_sec > self.stale_sec]
        for k in dead:
            self.tracks.pop(k, None)

    def _predict(self, st: TrackState, now_ts: float) -> tuple[float, float]:
        dt = max(0.0, now_ts - st.ts_sec)
        return st.x + st.vx * dt, st.y + st.vy * dt

    def assign_batch(self, detections: list[tuple[float, float]], now_ts: float) -> list[int]:
        """对一帧内所有 anchor 做全局匹配，返回与 detections 对齐的 track_id 列表。"""
        self._cleanup(now_ts)
        result: list[int | None] = [None] * len(detections)

        preds = {tid: self._predict(st, now_ts) for tid, st in self.tracks.items()}
        pairs: list[tuple[float, int, int]] = []
        for di, (dx, dy) in enumerate(detections):
            for tid, (px, py) in preds.items():
                dist = math.hypot(dx - px, dy - py)
                if dist <= self.max_match_dist:
                    pairs.append((dist, di, tid))
        pairs.sort(key=lambda p: p[0])

        used_det: set[int] = set()
        used_tid: set[int] = set()
        for dist, di, tid in pairs:
            if di in used_det or tid in used_tid:
                continue
            used_det.add(di)
            used_tid.add(tid)
            result[di] = tid
            self._update_track(tid, detections[di], now_ts)

        for di, tid in enumerate(result):
            if tid is None:
                new_tid = self.next_id
                self.next_id += 1
                dx, dy = detections[di]
                self.tracks[new_tid] = TrackState(x=dx, y=dy, vx=0.0, vy=0.0, ts_sec=now_ts)
                result[di] = new_tid
        return result  # type: ignore[return-value]

    def _update_track(self, tid: int, det: tuple[float, float], now_ts: float) -> None:
        st = self.tracks[tid]
        dt = now_ts - st.ts_sec
        dx, dy = det
        if dt > 1e-3:
            vx = (dx - st.x) / dt
            vy = (dy - st.y) / dt
            st.vx = (1 - self.vel_alpha) * st.vx + self.vel_alpha * vx
            st.vy = (1 - self.vel_alpha) * st.vy + self.vel_alpha * vy
        st.x, st.y, st.ts_sec = dx, dy, now_ts


# COCO17 躯干点（肩、髋）用于 anchor 加权
_TORSO_IDX = (5, 6, 11, 12)


class CollisionProcessor:
    """消费 PoseFrame，输出碰撞 token 与报警 token。"""

    def __init__(
        self,
        boxes: list,
        *,
        params: CollisionParams | None = None,
        video_fps: float = 25.0,
        # 向后兼容的旧关键字（无 params 时使用）
        alarm_min_consecutive_frames: int | None = None,
        alarm_cooldown_frames: int | None = None,
        alarm_window_frames: int | None = None,
    ):
        if params is None:
            params = CollisionParams()
            if alarm_min_consecutive_frames is not None:
                params.min_consecutive_frames = max(1, int(alarm_min_consecutive_frames))
            if alarm_cooldown_frames is not None:
                params.cooldown_frames = max(1, int(alarm_cooldown_frames))
            if alarm_window_frames is not None:
                params.window_frames = max(params.min_consecutive_frames, int(alarm_window_frames))
            else:
                params.window_frames = max(params.window_frames, params.min_consecutive_frames * 2)
        self.params = params
        self.boxes = boxes
        self.video_fps = max(1.0, float(video_fps))

        self.person_assigner = PersonTrackAssigner(
            max_match_dist=params.track_max_match_dist,
            stale_sec=params.track_stale_sec,
        )
        # 门控键：per_track 时 (track_id, token)，否则 (None, token)
        self._hit_history: dict[tuple, deque] = {}
        self._last_alarm_frame: dict[tuple, int] = {}

    def update_params(self, params: CollisionParams) -> None:
        """热更新阈值（保留 ROI / 跟踪 / 命中累积）。"""
        self.params = params
        self.person_assigner.max_match_dist = params.track_max_match_dist
        self.person_assigner.stale_sec = params.track_stale_sec

    def update_boxes(self, boxes: list) -> None:
        """热替换 ROI，保留命中/告警/跟踪状态（重标定不丢累积）。"""
        self.boxes = boxes

    def _resolve_now_ts(self, pose_frame: dict, frame_idx: int) -> float:
        ts = pose_frame.get("ts")
        if ts is not None:
            try:
                return float(ts)
            except (TypeError, ValueError):
                pass
        return frame_idx / self.video_fps if self.video_fps > 0 else 0.0

    def _anchor(self, kp_at) -> tuple[float, float, float]:
        """置信度加权躯干点作为 anchor；返回 (x, y, 肩宽)。"""
        sx = sy = wsum = 0.0
        for idx in _TORSO_IDX:
            x, y, s = kp_at(idx)
            if s <= 0.0:
                continue
            sx += x * s
            sy += y * s
            wsum += s
        lx, ly, ls = kp_at(5)
        rx, ry, rs = kp_at(6)
        shoulder_width = math.hypot(lx - rx, ly - ry) if (ls > 0.2 and rs > 0.2) else 0.0
        if wsum > 0:
            return sx / wsum, sy / wsum, shoulder_width
        return 0.0, 0.0, shoulder_width

    def _collect_hand_points(self, kp_at) -> list[tuple[float, float]]:
        """腕点 + 肘→腕外推点，作为碰撞候选手部落点。"""
        points: list[tuple[float, float]] = []
        for elbow_idx, wrist_idx in ((7, 9), (8, 10)):
            ex, ey, es = kp_at(elbow_idx)
            wx, wy, ws = kp_at(wrist_idx)
            if ws < self.params.wrist_conf:
                continue
            points.append((wx, wy))
            if es >= self.params.elbow_conf:
                points.append(
                    (
                        wx + self.params.forearm_extend_ratio * (wx - ex),
                        wy + self.params.forearm_extend_ratio * (wy - ey),
                    )
                )
        return points

    def _nearest_box_token(self, px: float, py: float, margin: float) -> str:
        """每个手部点只命中距离最近且在容差内的单个 ROI。"""
        best_token = ""
        best_dist = margin + 1.0
        for box in self.boxes:
            contour = box.get("orig_contour")
            if contour is None:
                continue
            dist = cv2.pointPolygonTest(contour, (px, py), True)
            if dist >= -margin and dist < best_dist:
                token = box_collision_token(box)
                if token:
                    best_dist = dist
                    best_token = token
        return best_token

    @staticmethod
    def _kp_getter(keypoints):
        def kp_at(i):
            if i >= len(keypoints):
                return 0.0, 0.0, 0.0
            kp = keypoints[i]
            return float(kp[0]), float(kp[1]), float(kp[2]) if len(kp) > 2 else 0.0

        return kp_at

    def process(self, pose_frame: dict) -> dict:
        """返回 collisions、alarm_collisions、带 track 的 skeletons（供 SSE 合并）。"""
        frame_idx = int(pose_frame.get("frame_idx") or 0)
        now_ts = self._resolve_now_ts(pose_frame, frame_idx)
        persons = pose_frame.get("persons") or pose_frame.get("skeletons") or []

        # 阶段一：解析每个 person 的 anchor / 手部点，批量分配 track。
        parsed: list[dict] = []
        anchors: list[tuple[float, float]] = []
        for person in persons:
            if not isinstance(person, dict):
                continue
            keypoints = person.get("keypoints") or []
            if len(keypoints) < 11:
                parsed.append({"person": person, "valid": False})
                continue
            kp_at = self._kp_getter(keypoints)
            ax, ay, shoulder_width = self._anchor(kp_at)
            parsed.append(
                {
                    "person": person,
                    "valid": True,
                    "shoulder_width": shoulder_width,
                    "hand_points": self._collect_hand_points(kp_at),
                }
            )
            anchors.append((ax, ay))

        track_ids = self.person_assigner.assign_batch(anchors, now_ts) if anchors else []

        # 阶段二：碰撞判定，按 (track, token) 记录命中。
        skeletons_data: list = []
        current_pairs: set[tuple] = set()
        active_tokens: set[str] = set()
        ai = 0
        for item in parsed:
            person = item["person"]
            if not item["valid"]:
                skeletons_data.append(person)
                continue
            track_id = track_ids[ai] if ai < len(track_ids) else -1
            ai += 1
            skel = dict(person)
            skel["person_track_id"] = track_id
            skeletons_data.append(skel)

            hand_points = item["hand_points"]
            if not hand_points:
                continue
            margin = max(
                self.params.boundary_margin_min_px,
                self.params.boundary_margin_ratio * item["shoulder_width"],
            )
            gate_track = track_id if self.params.per_track_gating else None
            for px, py in hand_points:
                token = self._nearest_box_token(px, py, margin)
                if token:
                    active_tokens.add(token)
                    current_pairs.add((gate_track, token))

        alarm_collisions = self._gate(current_pairs, frame_idx)
        return {
            "collisions": list(active_tokens),
            "alarm_collisions": alarm_collisions,
            "skeletons": skeletons_data,
            "frame_idx": frame_idx,
        }

    def _gate(self, current_pairs: set[tuple], frame_idx: int) -> list[str]:
        """M-of-N 滑窗 + 冷却，按 (track, token) 维度门控。"""
        alarm: list[str] = []
        window = self.params.window_frames
        min_hits = self.params.min_consecutive_frames
        for key in set(self._hit_history.keys()) | current_pairs:
            hist = self._hit_history.get(key)
            if hist is None:
                hist = deque(maxlen=window)
                self._hit_history[key] = hist
            hist.append(1 if key in current_pairs else 0)

            window_hits = sum(hist)
            if key not in current_pairs:
                if window_hits == 0:
                    self._hit_history.pop(key, None)
                continue

            if window_hits >= min_hits:
                last_alarm = self._last_alarm_frame.get(key, -(10**9))
                if frame_idx - last_alarm >= self.params.cooldown_frames:
                    alarm.append(key[1])
                    self._last_alarm_frame[key] = frame_idx
        return alarm

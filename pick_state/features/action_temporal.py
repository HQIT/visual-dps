"""人级时序动作特征：只看过去窗口内的骨架，不看货框。

供动作门控训练与线上推理共用，避免 scripts 与 pipeline 两套口径。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

import numpy as np

from pick_state.features.geometry import WRIST_LEFT, WRIST_RIGHT, read_xy
PERSON_FEATURE_KEYS = [
    "ankle_max_speed_norm",
    "arm_torso_angle_max",
    "elbow_angle_mean",
    "wrist_elevation_angle_max",
    "shoulder_hip_knee_angle_min",
]

SEQ_KEYS = [*PERSON_FEATURE_KEYS, "wl_x", "wl_y", "wr_x", "wr_y"]
N_BASE_STATS = 7
N_TRAJ = 4  # 左右手腕 path + net/path
# 特征维 = len(SEQ_KEYS)*7 + 4 + 1(覆盖率)
FEATURE_DIM = len(SEQ_KEYS) * N_BASE_STATS + N_TRAJ + 1

# 超过 window 后仍保留 track 的缓冲帧数（便于窗口边缘仍可取到历史）
STALE_TRACK_MARGIN = 15


def person_seq_vector(
    row: dict[str, Any], person: dict[str, Any], *, infer_height: int
) -> list[float]:
    vec = [row.get(k) for k in PERSON_FEATURE_KEYS]
    for idx in (WRIST_LEFT, WRIST_RIGHT):
        pt = read_xy(person, idx)
        vec += [None, None] if pt is None else [pt[0] / infer_height, pt[1] / infer_height]
    return [np.nan if v is None else float(v) for v in vec]


def _stats(seq: np.ndarray) -> list[float]:
    v = seq[~np.isnan(seq)]
    if len(v) == 0:
        return [np.nan] * N_BASE_STATS
    last = float(v[-1])
    if len(v) == 1:
        return [last, last, 0.0, last, last, 0.0, 0.0]
    slope = float(np.polyfit(np.arange(len(v)), v, 1)[0])
    half = len(v) // 2
    return [
        last,
        float(v.mean()),
        float(v.std()),
        float(v.min()),
        float(v.max()),
        slope,
        float(v[half:].mean() - v[:half].mean()),
    ]


def window_features(
    frames: dict[int, list[float]], t: int, win: int, step: int
) -> tuple[list[float], float]:
    keys = [k for k in range(t - win, t + 1, step) if k in frames]
    n_slots = len(range(t - win, t + 1, step))
    if not keys:
        return [np.nan] * (FEATURE_DIM - 1), 0.0
    seq = np.array([frames[k] for k in keys], dtype=np.float64)
    feats: list[float] = []
    for j in range(len(SEQ_KEYS)):
        feats += _stats(seq[:, j])
    traj: list[float] = []
    for base in (5, 7):
        xy = seq[:, base : base + 2]
        ok = ~np.isnan(xy).any(axis=1)
        if ok.sum() < 2:
            traj += [np.nan, np.nan]
            continue
        pts = xy[ok]
        d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        path = float(d.sum())
        net = float(np.linalg.norm(pts[-1] - pts[0]))
        traj += [path, net / path if path > 1e-6 else 0.0]
    feats += traj
    cov = len(keys) / max(1, n_slots)
    return feats, cov


class ActionSequenceTracker:
    """逐人保留历史骨架，按帧取过去窗口特征。

    线上优化（lazy + 裁剪）：
    - 仅对「本帧进框」或「窗口内曾进框」的 track 写入历史；
    - 每帧丢弃窗口外的旧 frame_idx，长期无更新的 track 整段删除。
    """

    def __init__(self, *, window_frames: int = 30, step: int = 2, infer_height: int = 1):
        self.window_frames = max(1, int(window_frames))
        self.step = max(1, int(step))
        self.infer_height = max(1, int(infer_height))
        self._hist: dict[str, dict[int, list[float]]] = defaultdict(dict)
        self._last_active: dict[str, int] = {}

    def reset(self) -> None:
        self._hist.clear()
        self._last_active.clear()

    def _warm_tracks(self, frame_idx: int) -> set[str]:
        """窗口内仍有历史可取的 track（即使本帧未进框也继续补帧）。"""
        return {
            track
            for track, last in self._last_active.items()
            if frame_idx - last <= self.window_frames
        }

    def _prune(self, frame_idx: int) -> None:
        min_keep = frame_idx - self.window_frames - self.step
        stale_after = self.window_frames + STALE_TRACK_MARGIN
        for track in list(self._hist.keys()):
            hist = self._hist[track]
            for fi in list(hist.keys()):
                if fi < min_keep:
                    del hist[fi]
            last = self._last_active.get(track, frame_idx)
            if not hist or frame_idx - last > stale_after:
                del self._hist[track]
                self._last_active.pop(track, None)

    def update(
        self,
        frame_idx: int,
        feature_rows: list[dict[str, Any]],
        *,
        track_ids: Iterable[str] | None = None,
    ) -> None:
        """写入本帧序列点。

        track_ids 非空时只更新这些 track 及仍在窗口内的 warm track；
        为 None 时更新 feature_rows 中的全部 track（离线构序列兼容）。
        """
        frame_idx = int(frame_idx)
        by_track: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
        for row in feature_rows:
            person = row.get("_person")
            if not isinstance(person, dict):
                continue
            track = str(row.get("person_track_id") or person.get("person_track_id") or "0")
            by_track[track] = (row, person)

        if track_ids is None:
            targets = set(by_track.keys())
        else:
            targets = set(track_ids) | self._warm_tracks(frame_idx)

        for track in targets:
            item = by_track.get(track)
            if item is None:
                continue
            row, person = item
            self._hist[track][frame_idx] = person_seq_vector(
                row, person, infer_height=self.infer_height
            )
            self._last_active[track] = frame_idx

        self._prune(frame_idx)

    def features(self, frame_idx: int, track_id: str) -> np.ndarray:
        frames = self._hist.get(str(track_id)) or {}
        feat, cov = window_features(frames, int(frame_idx), self.window_frames, self.step)
        return np.asarray(feat + [cov], dtype=np.float64)

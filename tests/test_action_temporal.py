"""ActionSequenceTracker：lazy 更新与历史裁剪。"""

from __future__ import annotations

import numpy as np

from pick_state.features.action_temporal import ActionSequenceTracker, window_features


def _row(track: str, frame_idx: int, *, ankle: float = 0.1) -> dict:
    person = {
        "person_track_id": track,
        "keypoints": [[100.0 + i, 200.0 + i, 0.9] for i in range(17)],
    }
    return {
        "person_track_id": track,
        "frame_idx": frame_idx,
        "ankle_max_speed_norm": ankle,
        "arm_torso_angle_max": 70.0,
        "elbow_angle_mean": 130.0,
        "wrist_elevation_angle_max": 55.0,
        "shoulder_hip_knee_angle_min": 140.0,
        "_person": person,
    }


def test_lazy_update_skips_unrelated_tracks():
    tr = ActionSequenceTracker(window_frames=10, step=1, infer_height=480)
    tr.update(1, [_row("1", 1), _row("2", 1)], track_ids={"1"})
    tr.update(2, [_row("1", 2), _row("2", 2)], track_ids={"1"})
    assert "1" in tr._hist
    assert "2" not in tr._hist


def test_warm_track_keeps_updating_after_hit_stops():
    tr = ActionSequenceTracker(window_frames=5, step=1, infer_height=480)
    for fi in range(1, 4):
        tr.update(fi, [_row("1", fi)], track_ids={"1"})
    tr.update(4, [_row("1", 4)], track_ids=set())
    assert 4 in tr._hist["1"]


def test_prune_drops_old_frames_and_stale_tracks():
    tr = ActionSequenceTracker(window_frames=5, step=1, infer_height=480)
    for fi in range(0, 40, 2):
        tr.update(fi, [_row("1", fi)], track_ids={"1"})
    hist = tr._hist.get("1") or {}
    assert all(fi >= 38 - 5 - 1 for fi in hist)
    tr.update(200, [_row("2", 200)], track_ids={"2"})
    assert "1" not in tr._hist


def test_full_update_when_track_ids_none():
    tr = ActionSequenceTracker(window_frames=10, step=1, infer_height=480)
    tr.update(1, [_row("1", 1), _row("2", 1)], track_ids=None)
    assert "1" in tr._hist and "2" in tr._hist


def test_frame_lazy_skips_cold_tracks_without_hits():
    tr = ActionSequenceTracker(window_frames=10, step=1, infer_height=480)
    tr.update(1, [_row("1", 1), _row("2", 1)], track_ids={"1"})
    tr.update(2, [_row("1", 2), _row("2", 2)], track_ids=set())
    assert 2 in tr._hist["1"]
    assert "2" not in tr._hist


def test_window_features_unchanged():
    frames = {i: [float(i)] * 9 for i in range(0, 11, 2)}
    feat, cov = window_features(frames, 10, win=10, step=2)
    assert len(feat) + 1 == 9 * 7 + 4 + 1
    assert 0.0 < cov <= 1.0
    assert not np.isnan(feat[0])

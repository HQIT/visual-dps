"""FeatureBank：按帧输出每个 track 的特征行。"""

from __future__ import annotations

from typing import Any

from pick_state.features.geometry import compute_angle_features
from pick_state.features.velocity import VelocityTracker


class FeatureBank:
    def __init__(self, *, infer_width: int, infer_height: int, video_fps: float = 15.0):
        self.velocity = VelocityTracker(
            infer_width=infer_width, infer_height=infer_height, video_fps=video_fps
        )

    def reset(self) -> None:
        self.velocity.reset()

    def rows_for_frame(self, frame: dict[str, Any]) -> list[dict[str, Any]]:
        frame_idx = int(frame.get("frame_idx") or 0)
        ts = float(frame.get("timestamp_sec") or 0.0)
        rows: list[dict[str, Any]] = []

        for person in frame.get("persons") or []:
            if not isinstance(person, dict):
                continue
            track_id = int(person.get("person_track_id") or 0)
            row: dict[str, Any] = {
                "frame_idx": frame_idx,
                "person_track_id": track_id,
                "person_id": person.get("person_id"),
            }
            row.update(
                self.velocity.update(
                    person, track_id=track_id, frame_idx=frame_idx, timestamp_sec=ts
                )
            )
            row.update(compute_angle_features(person))
            row["_person"] = person
            rows.append(row)

        return rows

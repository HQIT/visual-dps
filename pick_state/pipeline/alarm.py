"""连续帧告警：语义对齐 collector / DPS CollisionProcessor 的 alarm 逻辑。"""

from __future__ import annotations


class AlarmTracker:
    def __init__(self, *, min_consecutive_frames: int = 3, cooldown_frames: int = 0):
        self.min_consecutive_frames = max(1, int(min_consecutive_frames))
        self.cooldown_frames = max(0, int(cooldown_frames))
        self._consecutive: dict[str, int] = {}
        self._last_alarm: dict[str, int] = {}

    def reset(self) -> None:
        self._consecutive.clear()
        self._last_alarm.clear()

    def dwell(self, token: str) -> int:
        return self._consecutive.get(token, 0)

    def step(self, tokens: list[str], frame_idx: int) -> list[str]:
        current = set(tokens)
        for token in list(self._consecutive):
            if token not in current:
                self._consecutive[token] = 0

        alarms: list[str] = []
        for token in current:
            self._consecutive[token] = self._consecutive.get(token, 0) + 1
            last = self._last_alarm.get(token, -(10**9))
            if (
                self._consecutive[token] >= self.min_consecutive_frames
                and frame_idx - last >= self.cooldown_frames
            ):
                alarms.append(token)
                self._last_alarm[token] = frame_idx
        return sorted(alarms)

"""Scenario clock: maps wall time to scenario time with play, pause, speed and seek.

The server owns the clock so every screen and, later, every agent sees the same moment.
"""
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class ClockState:
    sim_time: datetime
    playing: bool
    speed: float
    start: datetime
    end: datetime


class ScenarioClock:
    MIN_SPEED, MAX_SPEED = 0.1, 600.0

    def __init__(self, start: datetime, end: datetime, speed: float = 60.0,
                 wall: Callable[[], float] | None = None):
        if end <= start:
            raise ValueError("clock end must be after start")
        self.start, self.end = start, end
        self._wall = wall or time.monotonic
        self._lock = threading.Lock()
        self._anchor_sim = start
        self._anchor_wall = self._wall()
        self._speed = self._clamp(speed)
        self._playing = False

    def _clamp(self, speed: float) -> float:
        return max(self.MIN_SPEED, min(self.MAX_SPEED, float(speed)))

    def _now_locked(self) -> datetime:
        if not self._playing:
            return self._anchor_sim
        elapsed = (self._wall() - self._anchor_wall) * self._speed
        t = self._anchor_sim + timedelta(seconds=elapsed)
        if t >= self.end:
            # Stop at the end instead of running past the scenario.
            self._anchor_sim, self._anchor_wall, self._playing = self.end, self._wall(), False
            return self.end
        return t

    def _rebase_locked(self) -> None:
        self._anchor_sim = self._now_locked()
        self._anchor_wall = self._wall()

    def state(self) -> ClockState:
        with self._lock:
            return ClockState(self._now_locked(), self._playing, self._speed, self.start, self.end)

    def play(self) -> ClockState:
        with self._lock:
            self._rebase_locked()
            if self._anchor_sim >= self.end:
                self._anchor_sim = self.start
            self._playing = True
        return self.state()

    def pause(self) -> ClockState:
        with self._lock:
            self._rebase_locked()
            self._playing = False
        return self.state()

    def set_speed(self, speed: float) -> ClockState:
        with self._lock:
            self._rebase_locked()
            self._speed = self._clamp(speed)
        return self.state()

    def seek(self, t: datetime) -> ClockState:
        with self._lock:
            self._anchor_sim = min(max(t, self.start), self.end)
            self._anchor_wall = self._wall()
        return self.state()

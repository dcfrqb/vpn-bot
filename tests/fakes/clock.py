"""Controllable clock for tests: monotonic seconds + aware UTC now."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class FakeClock:
    def __init__(self, start: datetime = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)):
        self._now = start
        self._mono = 1000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    __call__ = monotonic  # usable as Scheduler(clock=FakeClock())

    def advance(self, seconds: float = 0, **kwargs) -> None:
        delta = timedelta(seconds=seconds, **kwargs)
        self._now += delta
        self._mono += delta.total_seconds()

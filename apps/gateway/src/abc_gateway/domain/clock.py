"""Time as an injected dependency.

Domain code never calls ``datetime.now()`` directly. Budget windows, rolling
runaway detection and session expiry are all time-dependent, and a test that
cannot control the clock either cannot exercise a month rollover at all or has
to sleep to do it. Injecting the clock makes those tests deterministic and
instant.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Source of the current instant."""

    def now(self) -> datetime:
        """The current time, always timezone-aware and in UTC."""
        ...


class SystemClock:
    """The real clock."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """A clock that only moves when a test moves it.

    Thread-safe, because concurrency tests read it from several threads while
    the main thread may advance it.
    """

    __slots__ = ("_lock", "_now")

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware start instant")
        self._now = start.astimezone(UTC)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, delta: timedelta) -> datetime:
        with self._lock:
            self._now = self._now + delta
            return self._now

    def set(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware instant")
        with self._lock:
            self._now = instant.astimezone(UTC)

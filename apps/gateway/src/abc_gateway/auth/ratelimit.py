"""Tier 1 of the login rate limit: a per-IP, in-process flood guard.

Two tiers exist on purpose, at different layers:

* **This module** -- per-IP, in-process, checked *before* the user lookup and
  before Argon2 runs. Its job is to make a flood cheap to reject (a dict
  lookup, not 64 MiB of hashing), not to be the thing that actually bounds
  guess rate against one account.
* **The durable per-account counter** (``repo.record_login_failure``) --
  that is the tier that actually bounds guess rate, because it is shared
  across instances. An attacker rotating source IPs defeats any per-process
  counter the instant there is more than one gateway task; it cannot defeat a
  counter keyed by the account and stored in the repository.

Time comes from the injected ``Clock`` so tests never sleep.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

from ..domain.clock import Clock


@dataclass
class _Bucket:
    count: int
    window_started_epoch: float


class LoginThrottle:
    """A fixed-window attempt counter per client IP.

    The bucket table is capped and LRU-evicted: an unbounded dict keyed by
    attacker-controlled IP addresses is itself a memory-exhaustion vector.
    """

    def __init__(
        self,
        clock: Clock,
        *,
        limit: int,
        window_seconds: int,
        max_tracked: int = 50_000,
    ) -> None:
        self._clock = clock
        self._limit = limit
        self._window_seconds = window_seconds
        self._max_tracked = max_tracked
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        """Whether ``ip`` may attempt a login right now, per this tier alone."""
        now = self._clock.now().timestamp()
        with self._lock:
            bucket = self._buckets.get(ip)
            if bucket is None or now - bucket.window_started_epoch >= self._window_seconds:
                return True
            return bucket.count < self._limit

    def record(self, ip: str) -> None:
        """Count one attempt from ``ip`` toward its window."""
        now = self._clock.now().timestamp()
        with self._lock:
            bucket = self._buckets.get(ip)
            if bucket is None or now - bucket.window_started_epoch >= self._window_seconds:
                bucket = _Bucket(count=0, window_started_epoch=now)
            bucket.count += 1
            self._buckets[ip] = bucket
            self._buckets.move_to_end(ip)
            while len(self._buckets) > self._max_tracked:
                self._buckets.popitem(last=False)

    def retry_after_seconds(self, ip: str) -> int:
        now = self._clock.now().timestamp()
        with self._lock:
            bucket = self._buckets.get(ip)
            if bucket is None:
                return 0
            remaining = self._window_seconds - (now - bucket.window_started_epoch)
            return max(0, int(remaining) + 1)

    def tracked_count(self) -> int:
        """Test/observability hook: how many IPs currently have a bucket."""
        with self._lock:
            return len(self._buckets)

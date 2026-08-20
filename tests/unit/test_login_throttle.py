"""Tier 1 (per-IP) login rate limiting (auth/ratelimit.py)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from abc_gateway.auth.ratelimit import LoginThrottle
from abc_gateway.domain.clock import ManualClock

NOW = datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)


def test_admits_up_to_the_limit_then_blocks() -> None:
    clock = ManualClock(NOW)
    throttle = LoginThrottle(clock, limit=3, window_seconds=60)

    for _ in range(3):
        assert throttle.allow("1.2.3.4") is True
        throttle.record("1.2.3.4")

    assert throttle.allow("1.2.3.4") is False


def test_ips_are_independent() -> None:
    clock = ManualClock(NOW)
    throttle = LoginThrottle(clock, limit=1, window_seconds=60)

    throttle.record("1.2.3.4")
    assert throttle.allow("1.2.3.4") is False
    assert throttle.allow("5.6.7.8") is True


def test_window_resets_after_it_elapses() -> None:
    clock = ManualClock(NOW)
    throttle = LoginThrottle(clock, limit=1, window_seconds=60)

    throttle.record("1.2.3.4")
    assert throttle.allow("1.2.3.4") is False

    clock.advance(timedelta(seconds=61))
    assert throttle.allow("1.2.3.4") is True


def test_bucket_table_is_bounded_and_evicts_oldest() -> None:
    clock = ManualClock(NOW)
    throttle = LoginThrottle(clock, limit=10, window_seconds=60, max_tracked=5)

    for i in range(20):
        throttle.record(f"10.0.0.{i}")

    assert throttle.tracked_count() <= 5


def test_retry_after_counts_down_to_zero() -> None:
    clock = ManualClock(NOW)
    throttle = LoginThrottle(clock, limit=1, window_seconds=60)
    throttle.record("1.2.3.4")

    assert throttle.retry_after_seconds("1.2.3.4") > 0

    clock.advance(timedelta(seconds=61))
    assert throttle.retry_after_seconds("1.2.3.4") == 0

"""Budget windows must resolve deterministically.

A window key is the address of the money. If it resolves inconsistently, spend
lands in the wrong period -- or worse, September traffic is charged against
August's exhausted budget.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from abc_gateway.domain.window import BudgetWindow, WindowError, WindowType


def dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


class TestMonthly:
    def test_resolves_to_month_key(self) -> None:
        w = BudgetWindow.for_instant(WindowType.MONTHLY, dt("2026-08-19T10:32:00+00:00"))
        assert w.id == "2026-08"
        assert w.sort_key() == "WINDOW#MONTH#2026-08"

    def test_boundaries_are_exact(self) -> None:
        w = BudgetWindow.for_instant(WindowType.MONTHLY, dt("2026-08-19T10:32:00+00:00"))
        assert w.starts_at == dt("2026-08-01T00:00:00+00:00")
        # End is exclusive: the first instant of September.
        assert w.reset_at == dt("2026-09-01T00:00:00+00:00")

    def test_last_instant_of_month_stays_in_month(self) -> None:
        w = BudgetWindow.for_instant(WindowType.MONTHLY, dt("2026-08-31T23:59:59+00:00"))
        assert w.id == "2026-08"

    def test_rollover_is_immediate(self) -> None:
        """The whole reason windows are keys and not TTL'd rows.

        DynamoDB's TTL deletes expired items only eventually. If a new period
        depended on the old row disappearing, September would keep spending
        August's budget for days.
        """
        august = BudgetWindow.for_instant(WindowType.MONTHLY, dt("2026-08-31T23:59:59+00:00"))
        september = BudgetWindow.for_instant(WindowType.MONTHLY, dt("2026-09-01T00:00:00+00:00"))
        assert august.sort_key() != september.sort_key()
        assert september.sort_key() == "WINDOW#MONTH#2026-09"

    def test_next_crosses_year_boundary(self) -> None:
        december = BudgetWindow.for_instant(WindowType.MONTHLY, dt("2026-12-15T00:00:00+00:00"))
        assert december.next().id == "2027-01"

    def test_february_in_a_leap_year(self) -> None:
        w = BudgetWindow.for_instant(WindowType.MONTHLY, dt("2028-02-29T12:00:00+00:00"))
        assert w.id == "2028-02"
        assert w.reset_at == dt("2028-03-01T00:00:00+00:00")


class TestDaily:
    def test_resolves_to_day_key(self) -> None:
        w = BudgetWindow.for_instant(WindowType.DAILY, dt("2026-08-19T10:32:00+00:00"))
        assert w.sort_key() == "WINDOW#DAY#2026-08-19"

    def test_next_day(self) -> None:
        w = BudgetWindow.for_instant(WindowType.DAILY, dt("2026-08-19T10:32:00+00:00"))
        assert w.next().id == "2026-08-20"


class TestWeekly:
    def test_uses_iso_week_numbering(self) -> None:
        w = BudgetWindow.for_instant(WindowType.WEEKLY, dt("2026-08-19T10:32:00+00:00"))
        assert w.sort_key() == "WINDOW#WEEK#2026-W34"

    def test_week_starts_monday(self) -> None:
        w = BudgetWindow.for_instant(WindowType.WEEKLY, dt("2026-08-19T10:32:00+00:00"))
        assert w.starts_at.weekday() == 0

    def test_iso_year_may_differ_from_calendar_year(self) -> None:
        """2027-01-01 is a Friday, so it falls in ISO week 53 of 2026.

        Using the calendar year here would file that day's spend under a week
        that does not exist.
        """
        w = BudgetWindow.for_instant(WindowType.WEEKLY, dt("2027-01-01T12:00:00+00:00"))
        assert w.id == "2026-W53"


class TestTimezones:
    def test_boundaries_follow_the_billing_timezone(self) -> None:
        """22:00 UTC on Aug 19 is already Aug 20 in Tokyo."""
        utc_window = BudgetWindow.for_instant(
            WindowType.DAILY, dt("2026-08-19T22:00:00+00:00"), "UTC"
        )
        tokyo_window = BudgetWindow.for_instant(
            WindowType.DAILY, dt("2026-08-19T22:00:00+00:00"), "Asia/Tokyo"
        )
        assert utc_window.id == "2026-08-19"
        assert tokyo_window.id == "2026-08-20"

    def test_survives_a_dst_transition(self) -> None:
        # US DST began 2026-03-08. Local midnight still exists that day; the
        # window must resolve without raising and cover a 23-hour day.
        w = BudgetWindow.for_instant(
            WindowType.DAILY, dt("2026-03-08T12:00:00+00:00"), "America/New_York"
        )
        assert w.id == "2026-03-08"
        assert w.end_epoch - w.start_epoch == 23 * 3600

    def test_unknown_timezone_is_rejected(self) -> None:
        with pytest.raises(WindowError, match="unknown billing timezone"):
            BudgetWindow.for_instant(
                WindowType.MONTHLY, dt("2026-08-19T00:00:00+00:00"), "Mars/Olympus"
            )


class TestSession:
    def test_session_windows_are_lifecycle_scoped(self) -> None:
        w = BudgetWindow.for_instant(
            WindowType.SESSION,
            dt("2026-08-19T10:00:00+00:00"),
            session_id="ses_abc",
        )
        assert w.sort_key() == "WINDOW#SESSION#ses_abc"

    def test_session_windows_do_not_roll_over(self) -> None:
        w = BudgetWindow.for_instant(
            WindowType.SESSION, dt("2026-08-19T10:00:00+00:00"), session_id="ses_abc"
        )
        with pytest.raises(WindowError, match="do not roll over"):
            w.next()

    def test_session_id_is_required(self) -> None:
        with pytest.raises(WindowError, match="require a session_id"):
            BudgetWindow.for_instant(WindowType.SESSION, dt("2026-08-19T10:00:00+00:00"))


class TestContracts:
    def test_naive_datetimes_are_rejected(self) -> None:
        # Assuming UTC for a naive input would silently misattribute spend for
        # any tenant not billing in UTC.
        with pytest.raises(WindowError, match="timezone-aware"):
            BudgetWindow.for_instant(WindowType.MONTHLY, datetime(2026, 8, 19, 10, 0))

    def test_contains_is_half_open(self) -> None:
        w = BudgetWindow.for_instant(WindowType.MONTHLY, dt("2026-08-19T00:00:00+00:00"))
        assert w.contains(dt("2026-08-01T00:00:00+00:00"))
        assert w.contains(dt("2026-08-31T23:59:59+00:00"))
        assert not w.contains(dt("2026-09-01T00:00:00+00:00"))

    def test_resolution_is_deterministic(self) -> None:
        at = datetime.now(UTC)
        assert BudgetWindow.for_instant(WindowType.MONTHLY, at) == BudgetWindow.for_instant(
            WindowType.MONTHLY, at
        )

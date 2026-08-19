"""Budget windows.

A budget window resolves an instant in time to a deterministic storage key, so
that "which budget does this request spend from?" is answered by pure
computation rather than by asking the database what still exists.

That distinction matters more than it first appears. DynamoDB's TTL deletes
expired items only *eventually* -- AWS documents a lag that can run to days --
so a design that waits for last month's budget item to disappear before starting
a new month would keep enforcing September traffic against August's exhausted
budget. Making the window part of the primary key removes the question entirely:
at midnight on the first, requests simply address a different item.

TTL may still garbage-collect historical windows. It must never decide whether a
request is financially authorised.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

#: Session windows are lifecycle-scoped, not calendar-scoped. They need an end
#: instant for the shared abstraction, so they get one far past any plausible
#: session: year 9999.
_SESSION_END_EPOCH: Final[int] = 253_402_300_799

DEFAULT_BILLING_TZ: Final[str] = "UTC"


class WindowType(StrEnum):
    """Kinds of budget window.

    The stored value is the short form used in sort keys, so ``MONTHLY`` keys as
    ``WINDOW#MONTH#2026-08``.
    """

    DAILY = "DAY"
    WEEKLY = "WEEK"
    MONTHLY = "MONTH"
    SESSION = "SESSION"


class WindowError(ValueError):
    """Raised when a window cannot be resolved."""


@dataclass(frozen=True, slots=True)
class BudgetWindow:
    """A resolved budget period.

    Attributes:
        type: The window kind.
        id: The deterministic period identifier (``2026-08``, ``2026-W34``,
            ``2026-08-19``, or a session id).
        start_epoch: Inclusive start, seconds since the Unix epoch.
        end_epoch: Exclusive end, seconds since the Unix epoch.
        tz: IANA timezone the period boundaries were computed in.
    """

    type: WindowType
    id: str
    start_epoch: int
    end_epoch: int
    tz: str = DEFAULT_BILLING_TZ

    # -- resolution ---------------------------------------------------------

    @classmethod
    def for_instant(
        cls,
        window_type: WindowType,
        at: datetime,
        tz: str = DEFAULT_BILLING_TZ,
        *,
        session_id: str | None = None,
        session_start: datetime | None = None,
    ) -> BudgetWindow:
        """Resolve the window containing ``at``.

        Args:
            window_type: Which kind of window to resolve.
            at: The instant to resolve. Must be timezone-aware; a naive datetime
                is rejected rather than silently assumed to be UTC, because
                guessing here would silently misattribute spend near midnight.
            tz: IANA billing timezone. Period boundaries are the tenant's local
                midnight, not UTC midnight.
            session_id: Required for ``SESSION`` windows.
            session_start: Optional opening instant for ``SESSION`` windows.
        """
        if at.tzinfo is None:
            raise WindowError("`at` must be timezone-aware")

        if window_type is WindowType.SESSION:
            if not session_id:
                raise WindowError("SESSION windows require a session_id")
            start = int((session_start or at).timestamp())
            return cls(
                type=WindowType.SESSION,
                id=session_id,
                start_epoch=start,
                end_epoch=_SESSION_END_EPOCH,
                tz=tz,
            )

        zone = _zone(tz)
        local = at.astimezone(zone)

        match window_type:
            case WindowType.DAILY:
                start_date = local.date()
                end_date = start_date + timedelta(days=1)
                window_id = start_date.isoformat()
            case WindowType.WEEKLY:
                # ISO-8601 weeks: Monday-start, and the year is the ISO year,
                # which legitimately differs from the calendar year in late
                # December / early January.
                iso = local.isocalendar()
                start_date = date.fromisocalendar(iso.year, iso.week, 1)
                end_date = start_date + timedelta(days=7)
                window_id = f"{iso.year:04d}-W{iso.week:02d}"
            case WindowType.MONTHLY:
                start_date = local.date().replace(day=1)
                last_day = calendar.monthrange(start_date.year, start_date.month)[1]
                end_date = start_date + timedelta(days=last_day)
                window_id = f"{start_date.year:04d}-{start_date.month:02d}"
            case _:  # pragma: no cover - exhaustive over WindowType
                raise WindowError(f"unsupported window type: {window_type}")

        return cls(
            type=window_type,
            id=window_id,
            start_epoch=_local_midnight_epoch(start_date, zone),
            end_epoch=_local_midnight_epoch(end_date, zone),
            tz=tz,
        )

    # -- keys and boundaries ------------------------------------------------

    def sort_key(self) -> str:
        """The DynamoDB sort key for this window, e.g. ``WINDOW#MONTH#2026-08``."""
        return f"WINDOW#{self.type.value}#{self.id}"

    @property
    def reset_at(self) -> datetime:
        """The instant this window stops accepting spend."""
        return datetime.fromtimestamp(self.end_epoch, tz=UTC)

    @property
    def starts_at(self) -> datetime:
        return datetime.fromtimestamp(self.start_epoch, tz=UTC)

    def contains(self, at: datetime) -> bool:
        if at.tzinfo is None:
            raise WindowError("`at` must be timezone-aware")
        return self.start_epoch <= int(at.timestamp()) < self.end_epoch

    def next(self) -> BudgetWindow:
        """The window immediately following this one.

        Session windows have no successor -- they end when the session closes,
        not on a schedule.
        """
        if self.type is WindowType.SESSION:
            raise WindowError("SESSION windows do not roll over")
        # One second past the end lands unambiguously inside the next period,
        # including across a DST transition.
        return BudgetWindow.for_instant(
            self.type,
            datetime.fromtimestamp(self.end_epoch + 1, tz=UTC),
            self.tz,
        )

    def __str__(self) -> str:
        return self.sort_key()


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except Exception as exc:
        raise WindowError(f"unknown billing timezone: {tz!r}") from exc


def _local_midnight_epoch(day: date, zone: ZoneInfo) -> int:
    """Epoch seconds for local midnight on ``day`` in ``zone``.

    On a spring-forward DST boundary local midnight may not exist; ``fold`` and
    zoneinfo's normalisation resolve that to the first real instant of the day,
    which is the behaviour a billing period wants.
    """
    return int(datetime(day.year, day.month, day.day, tzinfo=zone).timestamp())

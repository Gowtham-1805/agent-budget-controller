"""Threshold alerts.

The 80% warning is a durable state transition, not a periodic poll. A cron job
asking "is anything above 80% yet?" warns late by up to its own interval and
re-warns on every pass; flipping a persisted ``warning_80_sent`` flag under a
condition warns at the exact crossing and, because the flip is conditional,
exactly one of several concurrent reconciliations can win it.

Alert identity is derived deterministically from the scope, window and kind, so
the storage layer enforces exactly-once independently of the flag. Both
mechanisms are in place because the consequence of getting this wrong -- a
pager firing repeatedly at 3am, or not firing at all -- is felt by a person.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .money import Money
from .scopes import ScopeRef
from .window import BudgetWindow


class AlertKind(StrEnum):
    """What happened."""

    WARN_80 = "WARN_80"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    SESSION_CLOSED = "SESSION_CLOSED"
    MODEL_SUBSTITUTED = "MODEL_SUBSTITUTED"
    RUNAWAY_DETECTED = "RUNAWAY_DETECTED"
    AGENT_PAUSED = "AGENT_PAUSED"
    AGENT_RESUMED = "AGENT_RESUMED"
    #: Actual provider cost exceeded what was reserved. Always a bug or a
    #: provider defect -- never routine -- so it pages rather than merely
    #: appearing on a dashboard.
    OVERAGE = "OVERAGE"
    PENDING_BACKLOG = "PENDING_BACKLOG"


@dataclass(frozen=True, slots=True)
class AlertEvent:
    """A durable, deduplicated notification."""

    kind: AlertKind
    scope: ScopeRef
    window: BudgetWindow | None
    occurred_at: datetime
    tenant_id: str
    threshold_percent: int | None = None
    limit: Money | None = None
    spent: Money | None = None
    reserved: Money | None = None
    triggering_reservation_id: str | None = None
    detail: dict[str, str] = field(default_factory=dict)

    def sort_key(self) -> str:
        """Deterministic storage sort key.

        Because this is derived rather than random, a conditional put on it is
        itself an exactly-once guarantee: a duplicate attempt collides instead
        of creating a second alert.
        """
        window_part = self.window.sort_key().removeprefix("WINDOW#") if self.window else "LIFETIME"
        return f"ALERT#{window_part}#{self.kind.value}"

    @property
    def event_id(self) -> str:
        """Stable identifier used to dedupe downstream publication."""
        material = f"{self.tenant_id}|{self.scope.key()}|{self.sort_key()}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def utilization_percent(committed: Money, limit: Money) -> int:
    """Committed spend as a whole-number percentage of the limit.

    Integer arithmetic throughout -- this feeds a threshold comparison, and a
    float here would make the boundary case ambiguous.
    """
    if limit.nano <= 0:
        return 0
    return (committed.nano * 100) // limit.nano


def threshold_floor(limit: Money, percent: int) -> Money:
    """The *remaining* balance at which utilization reaches ``percent``.

    Expressed as a floor on remaining rather than a ceiling on committed
    because the stored counter the condition can actually read is
    ``remaining_nano``. At an 80% threshold on a $50 limit this returns $10:
    once remaining drops to or below $10, 80% has been consumed.
    """
    return limit - limit.pct_floor(percent)

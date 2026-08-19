"""Session lifecycle.

A session budget differs from a team or agent budget in that it never resets --
it is scoped to a conversation, not to a calendar period. When it runs out the
session is finished, and the correct behaviour is to close it rather than to
keep rejecting requests one at a time.

There is a wording trap in the usual statement of the requirement ("close the
session when it exceeds $2"). A correct hard controller never lets a session
exceed its cap at all. It closes either when reconciliation lands exactly on the
limit, or when the *next* request's worst-case cost would not fit -- before that
request reaches a provider. Those are the two closure paths below, and both are
tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .money import Money


class SessionStatus(StrEnum):
    """Lifecycle state of a session."""

    OPEN = "OPEN"
    #: Closed because its budget is spent or the next request could not fit.
    CLOSED_BUDGET = "CLOSED_BUDGET"
    CLOSED_USER = "CLOSED_USER"
    CLOSED_ADMIN = "CLOSED_ADMIN"
    EXPIRED = "EXPIRED"


CLOSED_STATUSES: frozenset[SessionStatus] = frozenset(
    {
        SessionStatus.CLOSED_BUDGET,
        SessionStatus.CLOSED_USER,
        SessionStatus.CLOSED_ADMIN,
        SessionStatus.EXPIRED,
    }
)


class SessionCloseReason(StrEnum):
    BUDGET_EXHAUSTED = "budget_exhausted"
    REMAINING_BELOW_VIABLE = "remaining_below_viable"
    USER_REQUESTED = "user_requested"
    ADMIN_REQUESTED = "admin_requested"
    TTL_EXPIRED = "ttl_expired"


@dataclass(frozen=True, slots=True)
class Session:
    """A governed conversation with its own non-resetting budget."""

    session_id: str
    tenant_id: str
    team_id: str
    agent_id: str
    status: SessionStatus
    opened_at: datetime
    expires_at: datetime
    limit: Money
    #: The smallest remaining balance still worth keeping the session open for.
    #: Once the balance drops below this, the session is closed rather than left
    #: open to reject every subsequent request individually.
    min_viable: Money
    closed_at: datetime | None = None
    close_reason: SessionCloseReason | None = None
    closed_by: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status is SessionStatus.OPEN

    def is_expired_at(self, at: datetime) -> bool:
        return at >= self.expires_at

    def should_close_on_remaining(self, remaining: Money) -> SessionCloseReason | None:
        """Decide whether a post-reconcile balance ends the session.

        Returns the reason to close, or None to leave it open. Separating
        "exhausted" from "below viable" preserves a genuinely useful
        distinction for the caller: the session is over either way, but the
        second case means some balance was left stranded.
        """
        if remaining <= Money.zero():
            return SessionCloseReason.BUDGET_EXHAUSTED
        if remaining < self.min_viable:
            return SessionCloseReason.REMAINING_BELOW_VIABLE
        return None

    def require_open(self) -> None:
        if not self.is_open:
            raise ValueError(f"session {self.session_id} is {self.status}")

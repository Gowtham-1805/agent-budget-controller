"""Typed enforcement errors.

Every rejection carries a stable machine-readable code and, where a budget was
the cause, names the exact scope and dimension that bound the request. A client
that receives "429 Too Many Requests" cannot tell ordinary rate limiting from
financial exhaustion; one that receives ``budget_exhausted`` with the blocking
scope, the remaining balance and the reset instant can decide whether to back
off, escalate, or route elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .money import Money
from .scopes import ScopeRef
from .tokens import TokenVector
from .window import BudgetWindow


class DenialCode(StrEnum):
    """Why a request was refused authorization."""

    BUDGET_EXHAUSTED = "budget_exhausted"
    TOKEN_QUOTA_EXCEEDED = "token_quota_exceeded"
    #: The request alone is larger than the scope's entire limit, so it could
    #: never succeed regardless of current spend. Distinct from exhaustion
    #: because the remedy is different: shrink the request, not wait for reset.
    EXCEEDS_WINDOW_LIMIT = "exceeds_window_limit"
    AGENT_PAUSED = "agent_paused"
    SESSION_CLOSED = "session_closed"
    SESSION_EXPIRED = "session_expired"
    NO_ELIGIBLE_MODEL = "no_eligible_model"


class ReleaseReason(StrEnum):
    """Why a reservation was returned to its budgets without being spent."""

    PROVIDER_NOT_INVOKED = "provider_not_invoked"
    PROVIDER_REJECTED = "provider_rejected"
    ROUTING_ABANDONED = "routing_abandoned"
    SWEEPER_PREDISPATCH = "sweeper_predispatch"
    OPERATOR_NOT_BILLED = "operator_not_billed"


class PendingReason(StrEnum):
    """Why a reservation is held pending rather than settled."""

    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_AMBIGUOUS = "provider_ambiguous"
    SWEEPER_ORPHANED = "sweeper_orphaned"


@dataclass(frozen=True, slots=True)
class Denial:
    """A structured authorization refusal."""

    code: DenialCode
    blocking_scope: ScopeRef | None = None
    window: BudgetWindow | None = None
    limit: Money | None = None
    committed: Money | None = None
    reserved: Money | None = None
    available: Money | None = None
    requested: Money | None = None
    remaining_tokens: TokenVector | None = None
    requested_tokens: TokenVector | None = None
    #: Which token dimension(s) bound the request, when the code is
    #: TOKEN_QUOTA_EXCEEDED.
    token_dimensions: tuple[str, ...] = ()
    #: Every scope that rejected, not just the reported one.
    all_blocking: tuple[ScopeRef, ...] = ()
    #: Set when this denial also caused the session to be closed.
    session_closed: bool = False
    detail: str | None = None

    @property
    def retryable(self) -> bool:
        """A budget denial is deterministic: retrying changes nothing."""
        return False


class GatewayError(Exception):
    """Base class for all gateway-raised errors."""


class AuthorizationDenied(GatewayError):
    """The request was refused before any provider was contacted.

    Raising this is the mechanism that guarantees a blocked request costs
    nothing: the provider adapter is only reached after a grant is returned.
    """

    def __init__(self, denial: Denial) -> None:
        self.denial = denial
        super().__init__(f"{denial.code}: {denial.blocking_scope or 'request'}")


class TransientContention(GatewayError):
    """The budget store could not settle the transaction in time.

    Distinct from a denial: nothing was decided, and the caller may retry.
    """

    def __init__(self, attempts: int, detail: str | None = None) -> None:
        self.attempts = attempts
        super().__init__(detail or f"transaction contention after {attempts} attempts")


class PlanBugError(GatewayError):
    """The transaction we built was structurally invalid.

    This is never a user-facing denial. It means the gateway generated a
    malformed plan -- duplicate keys, a bad expression -- and it should page an
    operator rather than be reported as "budget exceeded".
    """


class IdempotencyConflict(GatewayError):
    """An idempotency key was reused with a different request body."""

    def __init__(self, key_hash: str) -> None:
        self.key_hash = key_hash
        super().__init__("idempotency key reused with a different request")


class RequestInFlight(GatewayError):
    """A logically identical request is still being processed."""

    def __init__(self, reservation_id: str, unresolved: bool = False) -> None:
        self.reservation_id = reservation_id
        self.unresolved = unresolved
        super().__init__(f"request {reservation_id} is still in flight")


class IdempotentReplay(GatewayError):
    """A completed request was retried; return the stored response verbatim.

    Deliberately an exception rather than a return value: it unwinds the whole
    invocation path, which is what guarantees the provider is not contacted a
    second time for a request that already settled.
    """

    def __init__(
        self,
        reservation_id: str,
        http_status: int,
        response: dict[str, object] | None = None,
    ) -> None:
        self.reservation_id = reservation_id
        self.http_status = http_status
        self.response = response or {}
        super().__init__(f"idempotent replay of {reservation_id}")

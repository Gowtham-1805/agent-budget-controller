"""The request reservation and its state machine.

A reservation is the record of money and tokens held against every applicable
budget while one request is in flight. It exists so that the answer to "how much
could this request still cost us?" is durable, and survives the gateway process
dying halfway through.

The state machine's most important property is what it refuses to do. When a
provider call ends ambiguously -- a read timeout, a connection reset after the
request was already on the wire -- the reservation is *not* released. The
provider may well have generated and billed the completion; releasing the hold
would hand that money back to the budget and let it be spent a second time. So
ambiguity resolves to RECONCILE_PENDING, which keeps the money encumbered until
evidence arrives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .ledger import CostBreakdown
from .money import Money
from .scopes import ScopeDelta, ScopeRef
from .tokens import TokenVector
from .window import BudgetWindow


class ReservationState(StrEnum):
    """Lifecycle of a reservation."""

    RESERVED = "RESERVED"
    #: Provably not billed. Budgets got their capacity back.
    RELEASED = "RELEASED"
    #: Provider reported usage; reserved capacity became committed spend.
    RECONCILED = "RECONCILED"
    #: Outcome unknown. Capacity stays held.
    RECONCILE_PENDING = "RECONCILE_PENDING"
    #: A pending record was later settled by evidence or by operator decision.
    RECONCILE_RESOLVED = "RECONCILE_RESOLVED"


TERMINAL_STATES: frozenset[ReservationState] = frozenset(
    {
        ReservationState.RELEASED,
        ReservationState.RECONCILED,
        ReservationState.RECONCILE_RESOLVED,
    }
)

_ALLOWED_TRANSITIONS: dict[ReservationState, frozenset[ReservationState]] = {
    ReservationState.RESERVED: frozenset(
        {
            ReservationState.RELEASED,
            ReservationState.RECONCILED,
            ReservationState.RECONCILE_PENDING,
        }
    ),
    ReservationState.RECONCILE_PENDING: frozenset(
        {
            # Evidence arrived, or the operator ruled. Note that PENDING can
            # still reach RELEASED, but only through an explicit operator
            # decision that the provider did not bill -- never automatically.
            ReservationState.RECONCILED,
            ReservationState.RECONCILE_RESOLVED,
            ReservationState.RELEASED,
        }
    ),
    ReservationState.RELEASED: frozenset(),
    ReservationState.RECONCILED: frozenset(),
    ReservationState.RECONCILE_RESOLVED: frozenset(),
}


def can_transition(current: ReservationState, target: ReservationState) -> bool:
    return target in _ALLOWED_TRANSITIONS[current]


class DispatchState(StrEnum):
    """Whether the provider has been contacted.

    Flipped to DISPATCHED immediately before the socket write. This single extra
    write is what lets a sweeper distinguish a gateway that crashed *before*
    reaching the provider -- whose reservation is provably safe to release --
    from one that crashed after, whose reservation must be held pending.
    Without it, every crashed request would encumber its budget forever.
    """

    PRE_DISPATCH = "PRE_DISPATCH"
    DISPATCHED = "DISPATCHED"


@dataclass(frozen=True, slots=True)
class ReservedScope:
    """The exact amount held against one scope, recorded for later reversal.

    Stored on the reservation rather than recomputed at reconcile time. Policy
    can change between reserve and reconcile, and reversing a hold using
    today's policy instead of the amount actually held would silently corrupt
    the counters.
    """

    scope: ScopeRef
    partition_suffix: str
    sort_key: str
    cost: Money
    tokens: TokenVector

    @classmethod
    def from_delta(cls, delta: ScopeDelta) -> ReservedScope:
        partition, sort_key = delta.storage_key()
        return cls(
            scope=delta.scope,
            partition_suffix=partition,
            sort_key=sort_key,
            cost=delta.cost,
            tokens=delta.tokens,
        )


@dataclass(frozen=True, slots=True)
class RequestReservation:
    """A durable hold placed on every applicable budget for one request."""

    reservation_id: str
    tenant_id: str
    team_id: str
    agent_id: str
    session_id: str | None

    state: ReservationState
    dispatch_state: DispatchState

    provider: str
    requested_model: str
    effective_model: str

    reserved_cost: Money
    reserved_tokens: TokenVector
    preflight_input_tokens: int
    bounded_max_output_tokens: int
    #: The preflight estimate, split by billing bucket. Retained alongside the
    #: actual cost so the ledger can show what was authorised next to what was
    #: really spent -- the two are different questions and operators need both.
    estimated_cost: CostBreakdown

    scopes: tuple[ReservedScope, ...]
    price_catalog_version: str

    created_at: datetime
    expires_at: datetime

    idempotency_key_hash: str | None = None
    request_fingerprint: str | None = None
    attempt: int = 1

    provider_request_id: str | None = None
    reconciled_at: datetime | None = None
    released_at: datetime | None = None
    pending_since: datetime | None = None

    actual_cost: Money | None = None
    actual_tokens: TokenVector | None = None

    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_outstanding(self) -> bool:
        """True while this reservation still encumbers budget capacity."""
        return self.state in (
            ReservationState.RESERVED,
            ReservationState.RECONCILE_PENDING,
        )

    def scope_refs(self) -> tuple[ScopeRef, ...]:
        return tuple(s.scope for s in self.scopes)

    def window_for(self, scope: ScopeRef) -> str | None:
        for reserved in self.scopes:
            if reserved.scope == scope:
                return reserved.sort_key
        return None

    def require_transition(self, target: ReservationState) -> None:
        if not can_transition(self.state, target):
            raise ValueError(
                f"illegal reservation transition {self.state} -> {target} for {self.reservation_id}"
            )


@dataclass(frozen=True, slots=True)
class ReservationGrant:
    """The result of a successful authorization.

    Holding one of these is the only thing that entitles the caller to invoke a
    provider.
    """

    reservation_id: str
    reserved_cost: Money
    reserved_tokens: TokenVector
    effective_model: str
    provider: str
    scopes: tuple[ScopeDelta, ...]
    price_catalog_version: str
    bounded_max_output_tokens: int
    #: True when the preferred model's allocation was exhausted and routing fell
    #: back. Surfaced in response headers so substitution is never silent.
    substituted: bool = False
    requested_model: str | None = None
    estimated_savings: Money | None = None

    def window_of(self, scope: ScopeRef) -> BudgetWindow | None:
        for delta in self.scopes:
            if delta.scope == scope:
                return delta.window
        return None

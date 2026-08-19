"""Backend-agnostic transaction plans.

The engine never speaks DynamoDB. It builds a *plan* -- an ordered list of typed
slots describing what must be checked and what must change -- and hands it to a
repository. ``DynamoBudgetRepository`` compiles a plan into ``TransactWriteItems``;
``InMemoryBudgetRepository`` executes the same plan under an evaluate-all-then-
commit-all discipline.

That symmetry is what makes the test strategy work: one contract suite runs
against both backends, so a behaviour proven against the fast in-memory backend
is a behaviour the real one must also exhibit.

Slot *order* is load-bearing. DynamoDB reports transaction cancellations as a
list positionally aligned with the actions submitted, so the only way to answer
"which scope rejected this request?" is to map reason index N back to slot N.
Reordering slots silently corrupts every error message the system produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ..domain.errors import DenialCode, PendingReason, ReleaseReason
from ..domain.ledger import UsageLedgerEntry
from ..domain.money import Money
from ..domain.reservation import RequestReservation
from ..domain.scopes import ScopeLimits, ScopeRef
from ..domain.tokens import TokenVector


class SlotKind(StrEnum):
    """What a slot in a plan does."""

    SCOPE_RESERVE = "SCOPE_RESERVE"
    SCOPE_RECONCILE = "SCOPE_RECONCILE"
    SCOPE_RELEASE = "SCOPE_RELEASE"
    SCOPE_PENDING = "SCOPE_PENDING"
    AGENT_STATUS_CHECK = "AGENT_STATUS_CHECK"
    SESSION_STATUS_CHECK = "SESSION_STATUS_CHECK"
    IDEMPOTENCY_PUT = "IDEMPOTENCY_PUT"
    IDEMPOTENCY_UPDATE = "IDEMPOTENCY_UPDATE"
    RESERVATION_PUT = "RESERVATION_PUT"
    RESERVATION_UPDATE = "RESERVATION_UPDATE"
    LEDGER_PUT = "LEDGER_PUT"


@dataclass(frozen=True, slots=True)
class ItemKey:
    """Identifies one item, independent of table-key formatting."""

    table: str
    partition_suffix: str
    sort_key: str

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.table, self.partition_suffix, self.sort_key)


@dataclass(frozen=True, slots=True)
class ScopeSeed:
    """Values used to create a budget-window item that does not exist yet.

    Windows are created lazily, on the first request that touches them. The
    seed carries the limits so that creation and the limit check happen in the
    same conditional write -- there is no read-then-create step for a race to
    slip through.
    """

    limits: ScopeLimits
    policy_version: str
    window_start_epoch: int
    window_end_epoch: int
    scope_type: str
    scope_id: str
    window_type: str
    window_id: str
    tenant_id: str
    housekeeping_ttl_epoch: int


@dataclass(frozen=True, slots=True)
class ScopeReserveSlot:
    """Hold ``cost`` and ``tokens`` against one scope, if they fit."""

    key: ItemKey
    scope: ScopeRef
    cost: Money
    tokens: TokenVector
    seed: ScopeSeed
    kind: SlotKind = SlotKind.SCOPE_RESERVE


@dataclass(frozen=True, slots=True)
class ScopeSettleSlot:
    """Convert a hold into settled spend, or return it unspent.

    ``reserved_*`` is what was held; ``actual_*`` is what was really used. For a
    release, actual is zero. For a reconcile, actual is provider-reported. The
    difference flows back to ``remaining``.
    """

    key: ItemKey
    scope: ScopeRef
    reserved_cost: Money
    reserved_tokens: TokenVector
    actual_cost: Money
    actual_tokens: TokenVector
    kind: SlotKind = SlotKind.SCOPE_RECONCILE

    @property
    def overage(self) -> Money:
        """Actual spend beyond what was reserved.

        Should always be zero. When it is not, a provider exceeded the hard
        output cap we sent it, and the excess is recorded rather than hidden.
        """
        excess = self.actual_cost - self.reserved_cost
        return excess if excess > Money.zero() else Money.zero()


@dataclass(frozen=True, slots=True)
class ScopePendingSlot:
    """Mark a hold as unresolved without releasing it.

    Only ``pending`` moves. ``remaining``, ``reserved`` and ``committed`` are
    untouched, so the money stays encumbered while we do not know whether the
    provider billed us.
    """

    key: ItemKey
    scope: ScopeRef
    reserved_cost: Money
    kind: SlotKind = SlotKind.SCOPE_PENDING


@dataclass(frozen=True, slots=True)
class StatusCheckSlot:
    """Assert an item's status without modifying it.

    Agent and session status are checked here, inside the transaction, rather
    than by a read beforehand. A prior read leaves a window in which a pause can
    land and one more request still gets through -- and a runaway agent is
    exactly the situation where that window gets exercised.
    """

    key: ItemKey
    expected_status: str
    failure_code: DenialCode
    #: When set, the item must also not have expired before this instant.
    not_expired_after_epoch: int | None = None
    expired_code: DenialCode | None = None
    kind: SlotKind = SlotKind.AGENT_STATUS_CHECK


@dataclass(frozen=True, slots=True)
class IdempotencyPutSlot:
    """Claim an idempotency key, failing if it is already taken."""

    key: ItemKey
    reservation_id: str
    request_fingerprint: str
    ttl_epoch: int
    kind: SlotKind = SlotKind.IDEMPOTENCY_PUT


@dataclass(frozen=True, slots=True)
class ReservationPutSlot:
    """Persist the reservation record itself."""

    key: ItemKey
    reservation: RequestReservation
    kind: SlotKind = SlotKind.RESERVATION_PUT


@dataclass(frozen=True, slots=True)
class ReservationUpdateSlot:
    """Transition a reservation, conditional on its current state.

    The condition is what makes settlement idempotent: a duplicate reconcile
    finds the reservation already RECONCILED and fails its condition instead of
    double-counting the spend.
    """

    key: ItemKey
    reservation_id: str
    expected_state: str
    new_state: str
    at: datetime
    actual_cost: Money | None = None
    actual_tokens: TokenVector | None = None
    provider_request_id: str | None = None
    release_reason: ReleaseReason | None = None
    pending_reason: PendingReason | None = None
    kind: SlotKind = SlotKind.RESERVATION_UPDATE


@dataclass(frozen=True, slots=True)
class LedgerPutSlot:
    """Append one immutable ledger entry."""

    key: ItemKey
    entry: UsageLedgerEntry
    kind: SlotKind = SlotKind.LEDGER_PUT


Slot = (
    ScopeReserveSlot
    | ScopeSettleSlot
    | ScopePendingSlot
    | StatusCheckSlot
    | IdempotencyPutSlot
    | ReservationPutSlot
    | ReservationUpdateSlot
    | LedgerPutSlot
)


class PlanValidationError(Exception):
    """A plan is structurally invalid and must not be submitted."""


@dataclass(frozen=True, slots=True)
class TransactionPlan:
    """An ordered, validated set of slots applied atomically."""

    slots: tuple[Slot, ...]
    client_request_token: str
    description: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    #: DynamoDB's own ceiling. Exceeding it is a configuration problem that
    #: should surface long before a request is in flight.
    MAX_ACTIONS: int = 100

    def validate(self) -> None:
        """Reject structurally impossible plans before any I/O.

        The duplicate-key check matters most. DynamoDB forbids two actions
        against the same item in one transaction and answers with a
        ``ValidationException`` that says nothing about which item -- useless at
        3am. Catching it here, with the offending key named, turns an
        unactionable runtime error into a test failure during development.
        """
        if not self.slots:
            raise PlanValidationError("plan has no slots")
        if len(self.slots) > self.MAX_ACTIONS:
            raise PlanValidationError(
                f"plan has {len(self.slots)} actions, exceeding the "
                f"{self.MAX_ACTIONS}-action transaction limit"
            )
        seen: dict[tuple[str, str, str], int] = {}
        for index, slot in enumerate(self.slots):
            item = slot.key.as_tuple()
            if item in seen:
                raise PlanValidationError(
                    f"slots {seen[item]} and {index} both act on item {item}; "
                    f"a transaction may touch each item at most once"
                )
            seen[item] = index

    def slot_at(self, index: int) -> Slot:
        """Map a cancellation-reason index back to the slot that produced it."""
        try:
            return self.slots[index]
        except IndexError as exc:
            raise PlanValidationError(
                f"cancellation reason index {index} has no matching slot "
                f"(plan has {len(self.slots)})"
            ) from exc

    def scope_slots(self) -> tuple[ScopeReserveSlot, ...]:
        return tuple(s for s in self.slots if isinstance(s, ScopeReserveSlot))


@dataclass(frozen=True, slots=True)
class SlotFailure:
    """One slot whose condition evaluated false."""

    index: int
    slot: Slot
    code: str
    #: Pre-image of the item, when the backend returned one. This is what lets
    #: the API say "you needed $0.04 and had $0.01" instead of "a condition
    #: failed".
    item: dict[str, object] | None = None


class TransactionDenied(Exception):
    """One or more conditions genuinely evaluated false.

    Authoritative and terminal: retrying evaluates identically.
    """

    def __init__(self, failures: list[SlotFailure]) -> None:
        self.failures = failures
        super().__init__(f"transaction denied by {len(failures)} condition(s)")


class TransactionConflict(Exception):
    """The transaction lost a race and may succeed if retried.

    Distinct from :class:`TransactionDenied`: nothing was decided.
    """

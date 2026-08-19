"""Repository protocols.

The write path is deliberately a single method. Every mutation the enforcement
engine performs -- authorize, reconcile, release, mark-pending -- is a
:class:`TransactionPlan`, so there is exactly one place where atomicity is
implemented per backend, and exactly one place a bug in it could hide.

Threshold flips and session closures are separate methods rather than plans
because they are conditional *attempts*: losing the race is the normal outcome
for all but one of several concurrent reconcilers, and expressing that as a
boolean return keeps it out of the exception path.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.agent import AgentState
from ..domain.alerts import AlertEvent
from ..domain.ledger import UsageLedgerEntry
from ..domain.money import Money
from ..domain.policy import AgentPolicy, BudgetPolicy
from ..domain.reservation import RequestReservation
from ..domain.scopes import ScopeRef
from ..domain.session import Session, SessionCloseReason, SessionStatus
from ..domain.state import BudgetState
from ..domain.window import BudgetWindow
from .plans import TransactionPlan


@runtime_checkable
class BudgetRepository(Protocol):
    """Atomic budget state, reservations and ledger."""

    async def apply(self, plan: TransactionPlan) -> None:
        """Apply every slot atomically, or none of them.

        Raises:
            TransactionDenied: a condition evaluated false. Terminal.
            TransactionConflict: lost a race; the caller may retry.
            PlanBugError: the plan was structurally invalid.
        """
        ...

    async def try_flip_threshold(
        self,
        scope: ScopeRef,
        window: BudgetWindow,
        *,
        threshold_percent: int,
        remaining_floor: Money,
        alert: AlertEvent,
    ) -> bool:
        """Flip a threshold flag and record its alert, exactly once.

        Returns True if this caller performed the flip, False if another
        concurrent reconciliation got there first. False is a normal outcome,
        not an error -- it is precisely what stops N concurrent reconcilers
        emitting N warnings.
        """
        ...

    async def try_close_session(
        self,
        tenant_id: str,
        session_id: str,
        *,
        status: SessionStatus,
        reason: SessionCloseReason,
        closed_by: str | None = None,
        alert: AlertEvent | None = None,
    ) -> bool:
        """Close an OPEN session. Returns False if it was already closed."""
        ...

    async def get_budget_state(
        self,
        scope: ScopeRef,
        window: BudgetWindow,
        *,
        consistent: bool = True,
    ) -> BudgetState | None:
        """Read counters. Consistent by default, because eventually-consistent
        reads of a financial counter are misleading in exactly the moments that
        matter."""
        ...

    async def get_reservation(
        self, tenant_id: str, reservation_id: str
    ) -> RequestReservation | None: ...

    async def mark_dispatched(self, tenant_id: str, reservation_id: str) -> None:
        """Flag that the provider is about to be contacted.

        Lets the stale-reservation sweeper distinguish a crash before the
        provider was reached (safe to release) from one after (must be held).
        """
        ...

    async def find_reservation_by_idempotency_key(
        self, tenant_id: str, key_hash: str
    ) -> RequestReservation | None: ...

    async def get_session(self, tenant_id: str, session_id: str) -> Session | None: ...

    async def put_session(self, session: Session) -> None: ...

    async def get_agent_state(self, tenant_id: str, agent_id: str) -> AgentState | None: ...

    async def put_agent_state(self, state: AgentState) -> None: ...

    async def set_agent_status(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        expected_status: str | None,
        new_status: str,
        reason: str,
        actor: str,
        source_event_id: str | None = None,
    ) -> bool:
        """Transition an agent's status conditionally.

        The optional ``expected_status`` makes an automatic pause idempotent:
        a duplicate stream delivery finds the agent already paused and returns
        False rather than emitting a second pause event.
        """
        ...

    async def get_agent_policy(self, tenant_id: str, agent_id: str) -> AgentPolicy | None: ...

    async def put_agent_policy(self, policy: AgentPolicy) -> None: ...

    async def put_budget_policy(self, tenant_id: str, policy: BudgetPolicy) -> None: ...

    async def get_budget_policy(self, tenant_id: str, scope: ScopeRef) -> BudgetPolicy | None: ...

    async def query_ledger(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        since_epoch: int | None = None,
        until_epoch: int | None = None,
        limit: int = 100,
    ) -> tuple[UsageLedgerEntry, ...]: ...

    async def health_check(self) -> bool:
        """Whether the store is reachable. Used by /readyz, never billable."""
        ...

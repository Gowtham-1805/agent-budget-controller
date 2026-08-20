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

from typing import Any, Protocol, runtime_checkable

from ..auth.identity import ApiKeyRecord
from ..domain.agent import AgentState
from ..domain.alerts import AlertEvent
from ..domain.auth_session import AuthSession
from ..domain.ledger import UsageLedgerEntry
from ..domain.money import Money
from ..domain.policy import AgentPolicy, BudgetPolicy
from ..domain.reservation import RequestReservation
from ..domain.scopes import ScopeRef, ScopeType
from ..domain.session import Session, SessionCloseReason, SessionStatus
from ..domain.state import BudgetState
from ..domain.user import UserRecord
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

    async def list_agent_policies(self, tenant_id: str) -> tuple[AgentPolicy, ...]:
        """List all configured agent policies for a tenant."""
        ...

    async def list_budget_policies(
        self, tenant_id: str, scope_type: ScopeType | None = None
    ) -> tuple[BudgetPolicy, ...]:
        """List all configured budget policies for a tenant (e.g. teams)."""
        ...

    async def list_sessions(
        self, tenant_id: str, agent_id: str | None = None, limit: int = 100
    ) -> tuple[Session, ...]:
        """List sessions for a tenant, optionally filtered by agent."""
        ...

    async def list_alerts(self, tenant_id: str, limit: int = 100) -> tuple[AlertEvent, ...]:
        """List recent alert events for a tenant."""
        ...

    async def get_audit_events(self, tenant_id: str) -> tuple[dict[str, Any], ...]:
        """Fetch audit log events for a tenant."""
        ...

    async def get_runaway_events(self, tenant_id: str, agent_id: str) -> tuple[Any, ...]:
        """Fetch runaway trip events for an agent."""
        ...

    async def health_check(self) -> bool:
        """Whether the store is reachable. Used by /readyz, never billable."""
        ...


@runtime_checkable
class CredentialRepository(Protocol):
    """Human users, their sessions, login throttling, and agent API keys.

    Deliberately *not* routed through :class:`TransactionPlan`. Every write
    here is a single-item conditional operation (email uniqueness, session
    revocation, a failure counter increment) -- exactly the shape already
    proven by ``claim_rolling_entry`` and ``set_agent_status``. Adding new
    ``SlotKind`` members and touching the positionally-aligned slot machinery
    in ``repo/plans.py`` for atomicity this doesn't need would risk
    misattributing a *budget* denial to the wrong scope over a login feature.

    Implemented directly on both ``InMemoryBudgetRepository`` and
    ``DynamoBudgetRepository`` -- the same pattern the rolling-spend and audit
    methods already use, structurally proven identical by
    ``tests/contract/test_credential_contract.py``.
    """

    # -- users ---------------------------------------------------------

    async def create_user(self, user: UserRecord) -> bool:
        """Create a new user. False if the email is already taken."""
        ...

    async def get_user(self, tenant_id: str, user_id: str) -> UserRecord | None: ...

    async def get_user_by_email_hash(self, email_hash: str) -> UserRecord | None: ...

    async def put_user(self, user: UserRecord) -> None:
        """Update an existing user (password, role, status, rehash)."""
        ...

    async def list_users(self, tenant_id: str) -> tuple[UserRecord, ...]: ...

    # -- durable login throttle (tier 2; see auth/ratelimit.py for tier 1) --

    async def record_login_failure(
        self,
        email_hash: str,
        *,
        at_epoch: int,
        window_seconds: int,
        lockout_threshold: int,
        lockout_base_seconds: int,
        lockout_cap_seconds: int,
    ) -> tuple[int, int]:
        """Increment the durable per-account failure counter, atomically.

        Resets to 1 if the previous window has fully elapsed. Once the count
        reaches ``lockout_threshold``, extends the lockout with a capped
        exponential backoff (``lockout_base_seconds * 2**(count - threshold)``,
        never beyond ``lockout_cap_seconds``) in the same write. Returns
        ``(new_count, locked_until_epoch)`` -- 0 for the latter if not locked.

        Durable and shared across instances -- this is the tier that actually
        bounds guess rate, because an attacker rotating source IPs cannot
        evade a counter keyed by the account the way they can a per-IP one.
        """
        ...

    async def clear_login_failures(self, email_hash: str) -> None: ...

    async def get_login_lockout(self, email_hash: str) -> tuple[int, int]:
        """``(failure_count, locked_until_epoch)`` for the current window."""
        ...

    # -- sessions --------------------------------------------------------

    async def put_auth_session(self, session: AuthSession) -> None: ...

    async def get_auth_session(self, token_hash: str) -> AuthSession | None: ...

    async def touch_auth_session(self, token_hash: str, *, last_seen_epoch: int) -> None: ...

    async def revoke_auth_session(self, token_hash: str) -> bool:
        """Revoke one session. False if it was already revoked or unknown."""
        ...

    async def revoke_user_sessions(
        self, tenant_id: str, user_id: str, *, except_token_hash: str | None = None
    ) -> int:
        """Revoke every live session for a user. Returns the count revoked."""
        ...

    # -- agent API keys ----------------------------------------------------
    #
    # Persisting these (rather than the in-process-only registry
    # ``auth/identity.py`` used before) fixes a real bug: a key minted via
    # ``POST /v1/agents/{id}/keys`` previously died on the next restart.

    async def put_api_key(self, record: ApiKeyRecord) -> None: ...

    async def get_api_key_by_hash(self, key_hash: str) -> ApiKeyRecord | None: ...

    async def list_api_keys(self, tenant_id: str) -> tuple[ApiKeyRecord, ...]: ...

    async def has_any_credential(self) -> bool:
        """Whether at least one admin key or user exists.

        Used by ``Container.readiness()``, which previously read
        ``len(identity)`` against an in-memory dict -- a check that stops
        meaning anything once credentials are repository-backed. This must
        stay a cheap, bounded check (e.g. ``Limit=1``), never an unbounded
        scan on every ``/readyz`` probe.
        """
        ...

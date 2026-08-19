"""In-memory budget repository.

Implements the same protocol as the DynamoDB backend with the same semantics,
so the contract suite runs against both. It is used for unit tests, property
tests, and the interleaving-based concurrency proof -- none of which need a
network, a container, or an AWS account.
"""

from __future__ import annotations

from ...domain.agent import AgentState, AgentStatus
from ...domain.alerts import AlertEvent
from ...domain.ledger import UsageLedgerEntry
from ...domain.money import Money
from ...domain.policy import AgentPolicy, BudgetPolicy
from ...domain.reservation import RequestReservation
from ...domain.scopes import ScopeRef
from ...domain.session import Session, SessionCloseReason, SessionStatus
from ...domain.state import BudgetState
from ...domain.window import BudgetWindow
from .. import attributes as A
from .. import keys
from ..items import budget_state_from_item
from ..plans import TransactionPlan
from ._txn import MemoryStore


class InMemoryBudgetRepository:
    """A complete, thread-safe budget store held in process memory."""

    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store or MemoryStore()
        self._sessions: dict[tuple[str, str], Session] = {}
        self._agent_policies: dict[tuple[str, str], AgentPolicy] = {}
        self._budget_policies: dict[tuple[str, str], BudgetPolicy] = {}
        self._ledger: list[UsageLedgerEntry] = []
        self._alerts: list[AlertEvent] = []
        self._runaway_events: list = []
        self._audit: list[dict] = []

    # -- the single write path ---------------------------------------------

    async def apply(self, plan: TransactionPlan) -> None:
        self.store.execute(plan)
        # The ledger index is a read-model projection; the authoritative record
        # is the item the transaction wrote.
        for slot in plan.slots:
            entry = getattr(slot, "entry", None)
            if isinstance(entry, UsageLedgerEntry):
                self._ledger.append(entry)

    # -- conditional one-shot transitions ----------------------------------

    async def try_flip_threshold(
        self,
        scope: ScopeRef,
        window: BudgetWindow,
        *,
        threshold_percent: int,
        remaining_floor: Money,
        alert: AlertEvent,
    ) -> bool:
        """Flip the threshold flag if and only if it is still unset.

        The flag lives on the same item several reconcilers may be touching, so
        the flip is the serialisation point: exactly one caller observes False
        and sets it. Everyone else gets False back and does nothing, which is
        how N concurrent crossings produce one warning.
        """
        attribute = A.WARNING_80_SENT if threshold_percent < 100 else A.WARNING_100_SENT
        key = keys.budget_state_key(alert.tenant_id, scope, window).as_tuple()

        with self.store._lock:
            item = self.store._items.get(key)
            if item is None or item.get(attribute, False):
                return False
            if int(item.get(A.REMAINING_NANO, 0)) > remaining_floor.nano:
                return False  # threshold not actually crossed
            item[attribute] = True
            self._alerts.append(alert)
            return True

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
        from dataclasses import replace

        key = keys.session_meta_key(tenant_id, session_id).as_tuple()
        with self.store._lock:
            item = self.store._items.get(key)
            session = self._sessions.get((tenant_id, session_id))
            if session is None or item is None:
                return False
            # The item is the authority here, not the record: it is what the
            # authorization transaction condition-checks, so the OPEN->closed
            # transition must be decided on it. Updating only the record would
            # leave closed sessions still accepting traffic.
            if item.get(A.STATUS) != SessionStatus.OPEN.value:
                return False
            item[A.STATUS] = status.value
            item["close_reason"] = reason.value
            self._sessions[(tenant_id, session_id)] = replace(
                session, status=status, close_reason=reason, closed_by=closed_by
            )
            if alert is not None:
                self._alerts.append(alert)
            return True

    # -- reads --------------------------------------------------------------

    async def get_budget_state(
        self,
        scope: ScopeRef,
        window: BudgetWindow,
        *,
        consistent: bool = True,
        tenant_id: str = "default",
    ) -> BudgetState | None:
        item = self.store.get(keys.budget_state_key(tenant_id, scope, window).as_tuple())
        if item is None:
            return None
        return budget_state_from_item(item, scope, window)

    async def get_reservation(
        self, tenant_id: str, reservation_id: str
    ) -> RequestReservation | None:
        item = self.store.get(keys.reservation_key(tenant_id, reservation_id).as_tuple())
        if item is None:
            return None
        reservation: RequestReservation = item["reservation"]
        # The transaction may have transitioned state after the record was
        # written, so the stored state attribute wins over the snapshot.
        from dataclasses import replace

        from ...domain.reservation import ReservationState

        return replace(reservation, state=ReservationState(item[A.STATE]))

    async def mark_dispatched(self, tenant_id: str, reservation_id: str) -> None:
        """Flag that the provider is about to be contacted.

        Unconditional and non-transactional on purpose: it sits directly in the
        request's hot path, and its only consumer is the sweeper, which treats
        a missing flag conservatively anyway.
        """
        key = keys.reservation_key(tenant_id, reservation_id).as_tuple()
        with self.store._lock:
            item = self.store._items.get(key)
            if item is not None:
                item[A.DISPATCH_STATE] = "DISPATCHED"

    async def find_reservation_by_idempotency_key(
        self, tenant_id: str, key_hash: str
    ) -> RequestReservation | None:
        item = self.store.get(keys.idempotency_key(tenant_id, key_hash).as_tuple())
        if item is None:
            return None
        return await self.get_reservation(tenant_id, item["reservation_id"])

    async def get_idempotency_record(self, tenant_id: str, key_hash: str) -> dict | None:
        return self.store.get(keys.idempotency_key(tenant_id, key_hash).as_tuple())

    async def get_session(self, tenant_id: str, session_id: str) -> Session | None:
        return self._sessions.get((tenant_id, session_id))

    async def put_session(self, session: Session) -> None:
        self._sessions[(session.tenant_id, session.session_id)] = session
        # Status also lives as an item so it can be condition-checked inside an
        # authorization transaction.
        self.store.put(
            keys.session_meta_key(session.tenant_id, session.session_id).as_tuple(),
            {
                A.ENTITY_TYPE: A.E_SESSION,
                A.STATUS: session.status.value,
                A.EXPIRES_AT_EPOCH: int(session.expires_at.timestamp()),
                "agent_id": session.agent_id,
                "team_id": session.team_id,
            },
        )

    async def get_agent_state(self, tenant_id: str, agent_id: str) -> AgentState | None:
        item = self.store.get(keys.agent_state_key(tenant_id, agent_id).as_tuple())
        if item is None:
            return None
        return AgentState(
            agent_id=agent_id,
            tenant_id=tenant_id,
            team_id=item.get("team_id", ""),
            status=AgentStatus(item[A.STATUS]),
            review_required=bool(item.get("review_required", False)),
            pause_reason=item.get("pause_reason"),
            pause_source_event_id=item.get("pause_source_event_id"),
        )

    async def put_agent_state(self, state: AgentState) -> None:
        self.store.put(
            keys.agent_state_key(state.tenant_id, state.agent_id).as_tuple(),
            {
                A.ENTITY_TYPE: A.E_AGENT_STATE,
                A.STATUS: state.status.value,
                "team_id": state.team_id,
                "review_required": state.review_required,
                "pause_reason": state.pause_reason,
                "pause_source_event_id": state.pause_source_event_id,
            },
        )

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
        """Conditionally transition an agent.

        ``expected_status`` is what makes an automatic pause idempotent under
        at-least-once stream delivery: the second delivery finds the agent
        already paused, fails the condition, and returns False instead of
        emitting a duplicate pause.
        """
        key = keys.agent_state_key(tenant_id, agent_id).as_tuple()
        with self.store._lock:
            item = self.store._items.get(key)
            if item is None:
                return False
            if expected_status is not None and item.get(A.STATUS) != expected_status:
                return False
            if item.get(A.STATUS) == new_status:
                return False
            item[A.STATUS] = new_status
            item["pause_reason"] = reason
            item["review_required"] = new_status in {
                AgentStatus.PAUSED_RUNAWAY.value,
                AgentStatus.PAUSED_ADMIN.value,
            }
            item["actor"] = actor
            if source_event_id:
                item["pause_source_event_id"] = source_event_id
            return True

    async def get_agent_policy(self, tenant_id: str, agent_id: str) -> AgentPolicy | None:
        return self._agent_policies.get((tenant_id, agent_id))

    async def put_agent_policy(self, policy: AgentPolicy) -> None:
        self._agent_policies[(policy.tenant_id, policy.agent_id)] = policy

    async def get_budget_policy(self, tenant_id: str, scope: ScopeRef) -> BudgetPolicy | None:
        return self._budget_policies.get((tenant_id, scope.key()))

    async def put_budget_policy(self, tenant_id: str, policy: BudgetPolicy) -> None:
        scope = ScopeRef(policy.scope_type, policy.scope_id)
        self._budget_policies[(tenant_id, scope.key())] = policy

    async def query_ledger(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        since_epoch: int | None = None,
        until_epoch: int | None = None,
        limit: int = 100,
    ) -> tuple[UsageLedgerEntry, ...]:
        results = [e for e in self._ledger if e.tenant_id == tenant_id]
        if agent_id:
            results = [e for e in results if e.agent_id == agent_id]
        if since_epoch is not None:
            results = [e for e in results if e.created_at.timestamp() >= since_epoch]
        if until_epoch is not None:
            results = [e for e in results if e.created_at.timestamp() < until_epoch]
        results.sort(key=lambda e: e.created_at)
        return tuple(results[:limit])

    # -- rolling spend (runaway detection) ---------------------------------

    async def claim_rolling_entry(self, tenant_id: str, entry_id: str, *, ttl_epoch: int) -> bool:
        """Claim a ledger entry for counting, exactly once.

        Returns False if it has already been counted. This is what makes the
        detector safe under at-least-once stream delivery: a redelivered entry
        cannot inflate the rolling total and manufacture a runaway that never
        happened.
        """
        key = keys.rolling_mark_key(tenant_id, entry_id).as_tuple()
        with self.store._lock:
            if key in self.store._items:
                return False
            self.store._items[key] = {
                A.ENTITY_TYPE: A.E_ROLLING_MARK,
                "entry_id": entry_id,
                "ttl_epoch": ttl_epoch,
            }
            return True

    async def add_rolling_spend(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        bucket: str,
        amount: Money,
        ttl_epoch: int,
    ) -> None:
        key = keys.rolling_bucket_key(tenant_id, agent_id, bucket).as_tuple()
        with self.store._lock:
            item = self.store._items.get(key)
            if item is None:
                item = {
                    A.ENTITY_TYPE: A.E_ROLLING_BUCKET,
                    "spend_nano": 0,
                    "event_count": 0,
                    "ttl_epoch": ttl_epoch,
                }
                self.store._items[key] = item
            item["spend_nano"] = int(item["spend_nano"]) + amount.nano
            item["event_count"] = int(item["event_count"]) + 1

    async def sum_rolling_spend(
        self, tenant_id: str, agent_id: str, *, buckets: list[str]
    ) -> Money:
        total = 0
        for bucket in buckets:
            item = self.store.get(keys.rolling_bucket_key(tenant_id, agent_id, bucket).as_tuple())
            if item:
                total += int(item["spend_nano"])
        return Money(total)

    async def record_runaway_event(self, event, alert: AlertEvent | None = None) -> None:
        if event is not None:
            self._runaway_events.append(event)
        if alert is not None:
            self._alerts.append(alert)

    async def record_alert(self, alert: AlertEvent) -> None:
        self._alerts.append(alert)

    async def get_runaway_events(self, tenant_id: str, agent_id: str) -> tuple:
        return tuple(
            e for e in self._runaway_events if e.tenant_id == tenant_id and e.agent_id == agent_id
        )

    async def record_audit_event(self, event: dict) -> None:
        """Append an administrative audit record.

        Kept permanently. The value of an audit trail is that it can answer
        "who resumed this agent, when, and why" months later.
        """
        self._audit.append(event)

    async def get_audit_events(self, tenant_id: str) -> tuple[dict, ...]:
        return tuple(e for e in self._audit if e.get("tenant_id") == tenant_id)

    async def health_check(self) -> bool:
        return True

    # -- test helpers -------------------------------------------------------

    def alerts(self, kind: str | None = None) -> tuple[AlertEvent, ...]:
        if kind is None:
            return tuple(self._alerts)
        return tuple(a for a in self._alerts if a.kind.value == kind)

    def ledger_entries(self, kind: str | None = None) -> tuple[UsageLedgerEntry, ...]:
        if kind is None:
            return tuple(self._ledger)
        return tuple(e for e in self._ledger if e.kind.value == kind)

    def reset(self) -> None:
        self.store.clear()
        self._sessions.clear()
        self._agent_policies.clear()
        self._budget_policies.clear()
        self._ledger.clear()
        self._alerts.clear()
        self._runaway_events.clear()
        self._audit.clear()

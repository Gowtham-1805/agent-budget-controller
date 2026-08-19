"""The atomic budget engine.

This module owns the one guarantee the whole system exists to provide: a request
cannot reach a provider unless its worst-case cost has been reserved atomically
from every applicable scope.

The lifecycle is strictly ordered, and the order is the point:

    count -> bound -> estimate -> RESERVE -> invoke -> reconcile

Never the inverse. "Call the provider, then add up the bill" is observability,
not enforcement -- by the time the number is known the money is already spent.

Three subtleties are worth reading the code for:

* **Denials are terminal, conflicts are retryable.** A failed condition means
  the budget genuinely did not fit; retrying evaluates identically and merely
  burns latency. A transaction conflict means the write lost a race and decided
  nothing. Confusing the two either hides real exhaustion behind retries or
  gives up on a request that would have succeeded.
* **Reconciliation cannot be refused.** Recording what a provider actually
  charged us is not conditional on it fitting. The settle path has no budget
  condition, only an accounting sanity check.
* **Ambiguity holds the money.** A read timeout does not prove the provider
  didn't bill us, so it never releases the reservation.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from ulid import ULID

from ..domain.errors import (
    AuthorizationDenied,
    Denial,
    DenialCode,
    IdempotencyConflict,
    IdempotentReplay,
    PendingReason,
    PlanBugError,
    ReleaseReason,
    RequestInFlight,
    TransientContention,
)
from ..domain.ledger import (
    BudgetDecision,
    CostBreakdown,
    LedgerKind,
    UsageLedgerEntry,
)
from ..domain.money import Money
from ..domain.reservation import (
    DispatchState,
    RequestReservation,
    ReservationGrant,
    ReservationState,
    ReservedScope,
)
from ..domain.scopes import ScopeDelta, ScopeRef, outermost
from ..domain.session import SessionStatus
from ..domain.tokens import TokenVector
from ..domain.usage import NormalizedUsage, ProviderUsage
from ..pricing.catalog import ModelPrice
from ..repo import attributes as A
from ..repo import keys
from ..repo.items import to_int
from ..repo.plans import (
    IdempotencyPutSlot,
    ItemKey,
    LedgerPutSlot,
    PlanValidationError,
    ReservationPutSlot,
    ReservationUpdateSlot,
    ScopePendingSlot,
    ScopeReserveSlot,
    ScopeSeed,
    ScopeSettleSlot,
    Slot,
    SlotFailure,
    StatusCheckSlot,
    TransactionConflict,
    TransactionDenied,
    TransactionPlan,
)

#: A reservation older than this with no outcome is swept. Generous, because
#: sweeping a request that is merely slow would release money the provider is
#: still busy spending.
DEFAULT_RESERVATION_TTL = timedelta(minutes=20)
DEFAULT_IDEMPOTENCY_TTL = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded, jittered backoff for transaction conflicts.

    Full jitter rather than fixed backoff: several gateway tasks contending for
    the same hot budget item would otherwise retry in lockstep and keep
    colliding.
    """

    max_attempts: int = 5
    base_delay_ms: int = 20
    max_delay_ms: int = 250
    total_budget_ms: int = 1200

    def delay_seconds(self, attempt: int) -> float:
        ceiling = min(self.max_delay_ms, self.base_delay_ms * (2**attempt))
        return random.uniform(0, ceiling) / 1000.0


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    """Everything needed to authorize one governed call."""

    tenant_id: str
    team_id: str
    agent_id: str
    session_id: str | None
    provider: str
    requested_model: str
    effective_model: str
    price: ModelPrice
    price_catalog_version: str
    preflight_input_tokens: int
    #: The hard ceiling that will actually be sent to the provider. Not an
    #: estimate -- if this is not enforced on the wire, the reservation bounds
    #: nothing.
    bounded_max_output_tokens: int
    scopes: tuple[ScopeDelta, ...]
    estimated_cost: CostBreakdown
    now: datetime
    idempotency_key: str | None = None
    request_fingerprint: str = ""
    check_agent_status: bool = True
    check_session_status: bool = True
    substituted: bool = False
    estimated_savings: Money | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def reserved_cost(self) -> Money:
        return self.estimated_cost.total

    @property
    def reserved_tokens(self) -> TokenVector:
        return TokenVector(
            input=self.preflight_input_tokens,
            output=self.bounded_max_output_tokens,
            total=self.preflight_input_tokens + self.bounded_max_output_tokens,
        )


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Outcome of settling a reservation against real usage."""

    reservation_id: str
    actual_cost: Money
    actual_cost_breakdown: CostBreakdown
    actual_tokens: TokenVector
    reserved_cost: Money
    ledger_entry: UsageLedgerEntry
    overage: Money = field(default_factory=Money.zero)

    @property
    def had_overage(self) -> bool:
        return self.overage > Money.zero()


class BudgetEngine:
    """Reserve, settle, release. The financial authority."""

    def __init__(
        self,
        repository,
        *,
        retry_policy: RetryPolicy | None = None,
        reservation_ttl: timedelta = DEFAULT_RESERVATION_TTL,
    ) -> None:
        self.repo = repository
        self.retry = retry_policy or RetryPolicy()
        self.reservation_ttl = reservation_ttl

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    async def reserve(self, request: ReservationRequest) -> ReservationGrant:
        """Atomically hold this request's worst-case cost across every scope.

        Returning a grant is the *only* thing that entitles a caller to invoke a
        provider. Every failure path raises instead.

        Raises:
            AuthorizationDenied: a budget, quota, or status check refused it.
            TransientContention: contention outstanding after the retry budget.
        """
        reservation_id = str(ULID())
        plan = self._build_authorization_plan(request, reservation_id)

        try:
            plan.validate()
        except PlanValidationError as exc:
            # A malformed plan is our bug, not the caller's. Surfacing it as a
            # budget denial would send an operator hunting a spend problem that
            # does not exist.
            raise PlanBugError(str(exc)) from exc

        await self._apply_with_retry(plan, request)

        return ReservationGrant(
            reservation_id=reservation_id,
            reserved_cost=request.reserved_cost,
            reserved_tokens=request.reserved_tokens,
            effective_model=request.effective_model,
            provider=request.provider,
            scopes=request.scopes,
            price_catalog_version=request.price_catalog_version,
            bounded_max_output_tokens=request.bounded_max_output_tokens,
            substituted=request.substituted,
            requested_model=request.requested_model,
            estimated_savings=request.estimated_savings,
        )

    async def _apply_with_retry(self, plan: TransactionPlan, request: ReservationRequest) -> None:
        elapsed_ms = 0.0
        for attempt in range(self.retry.max_attempts):
            try:
                await self.repo.apply(plan)
                return
            except TransactionDenied as denied:
                # An idempotency collision is not a budget decision -- it means
                # this logical request already exists -- so it is resolved
                # against the stored record rather than reported as a denial.
                if _only_idempotency_failed(denied):
                    await self._resolve_idempotency(request)
                # Otherwise terminal: the condition was evaluated and was false.
                raise AuthorizationDenied(self._decode_denial(denied, plan, request)) from denied
            except TransactionConflict:
                if attempt == self.retry.max_attempts - 1:
                    break
                delay = self.retry.delay_seconds(attempt)
                elapsed_ms += delay * 1000
                if elapsed_ms > self.retry.total_budget_ms:
                    break
                await asyncio.sleep(delay)
        raise TransientContention(self.retry.max_attempts)

    async def _resolve_idempotency(self, request: ReservationRequest) -> None:
        """Decide what a repeated idempotency key means, and raise accordingly.

        Three genuinely different situations share one symptom, and conflating
        them is how a retry turns into a double charge or a wrong answer:

        * **Same request, already finished** -- return the stored response and
          do not call the provider again.
        * **Same request, still running** -- tell the caller to wait rather than
          starting a second one.
        * **Different request, same key** -- a client bug. Returning the first
          request's response would be actively wrong, so it is refused.
        """
        assert request.idempotency_key is not None
        key_hash = hash_idempotency_key(request.idempotency_key)
        record = await self.repo.get_idempotency_record(request.tenant_id, key_hash)
        if record is None:
            # The claim vanished between the transaction and this read; treat it
            # as contention and let the caller retry.
            raise TransactionConflict("idempotency record disappeared")

        stored_fingerprint = record.get("request_fingerprint") or ""
        if (
            request.request_fingerprint
            and stored_fingerprint
            and stored_fingerprint != request.request_fingerprint
        ):
            raise IdempotencyConflict(key_hash)

        reservation_id = str(record.get("reservation_id", ""))
        reservation = await self.repo.get_reservation(request.tenant_id, reservation_id)
        if reservation is None:
            raise TransactionConflict("idempotent reservation not found")

        if reservation.state is ReservationState.RECONCILE_PENDING:
            raise RequestInFlight(reservation_id, unresolved=True)
        if reservation.is_terminal:
            raise IdempotentReplay(
                reservation_id,
                http_status=200,
                response={
                    "reservation_id": reservation_id,
                    "effective_model": reservation.effective_model,
                    "state": reservation.state.value,
                },
            )
        raise RequestInFlight(reservation_id)

    # ------------------------------------------------------------------
    # Plan construction
    # ------------------------------------------------------------------

    def _build_authorization_plan(
        self, request: ReservationRequest, reservation_id: str
    ) -> TransactionPlan:
        slots: list[Slot] = []

        # Scope slots first, in hierarchy order, so cancellation-reason indices
        # map back to scopes predictably.
        for delta in request.scopes:
            # Only the sort key comes from the delta; the partition key is built
            # by the storage layer, which owns the tenant prefix format.
            _, sort_key = delta.storage_key()
            slots.append(
                ScopeReserveSlot(
                    key=ItemKey(
                        keys.TABLE_CORE,
                        keys.budget_pk(request.tenant_id, delta.scope),
                        sort_key,
                    ),
                    scope=delta.scope,
                    cost=delta.cost,
                    tokens=delta.tokens,
                    seed=ScopeSeed(
                        limits=delta.limits,
                        policy_version=delta.policy_version,
                        window_start_epoch=delta.window.start_epoch,
                        window_end_epoch=delta.window.end_epoch,
                        scope_type=delta.scope.type.value,
                        scope_id=delta.scope.id,
                        window_type=delta.window.type.value,
                        window_id=delta.window.id,
                        tenant_id=request.tenant_id,
                        housekeeping_ttl_epoch=delta.window.end_epoch + 400 * 86_400,
                    ),
                )
            )

        # Status checks live on separate items from the counters above, which is
        # what makes checking them inside this same transaction legal.
        if request.check_agent_status:
            slots.append(
                StatusCheckSlot(
                    key=keys.agent_state_key(request.tenant_id, request.agent_id),
                    expected_status="ACTIVE",
                    failure_code=DenialCode.AGENT_PAUSED,
                )
            )

        if request.check_session_status and request.session_id:
            slots.append(
                StatusCheckSlot(
                    key=keys.session_meta_key(request.tenant_id, request.session_id),
                    expected_status=SessionStatus.OPEN.value,
                    failure_code=DenialCode.SESSION_CLOSED,
                    not_expired_after_epoch=int(request.now.timestamp()),
                    expired_code=DenialCode.SESSION_EXPIRED,
                )
            )

        if request.idempotency_key:
            slots.append(
                IdempotencyPutSlot(
                    key=keys.idempotency_key(
                        request.tenant_id, hash_idempotency_key(request.idempotency_key)
                    ),
                    reservation_id=reservation_id,
                    request_fingerprint=request.request_fingerprint,
                    ttl_epoch=int((request.now + DEFAULT_IDEMPOTENCY_TTL).timestamp()),
                )
            )

        slots.append(
            ReservationPutSlot(
                key=keys.reservation_key(request.tenant_id, reservation_id),
                reservation=self._build_reservation(request, reservation_id),
            )
        )

        return TransactionPlan(
            slots=tuple(slots),
            client_request_token=reservation_id,
            description=f"authorize {request.agent_id} -> {request.effective_model}",
        )

    def _build_reservation(
        self, request: ReservationRequest, reservation_id: str
    ) -> RequestReservation:
        return RequestReservation(
            reservation_id=reservation_id,
            tenant_id=request.tenant_id,
            team_id=request.team_id,
            agent_id=request.agent_id,
            session_id=request.session_id,
            state=ReservationState.RESERVED,
            dispatch_state=DispatchState.PRE_DISPATCH,
            provider=request.provider,
            requested_model=request.requested_model,
            effective_model=request.effective_model,
            reserved_cost=request.reserved_cost,
            reserved_tokens=request.reserved_tokens,
            preflight_input_tokens=request.preflight_input_tokens,
            bounded_max_output_tokens=request.bounded_max_output_tokens,
            estimated_cost=request.estimated_cost,
            # The exact per-scope amounts, recorded so settlement reverses what
            # was actually held rather than recomputing from policy that may
            # have changed in the meantime.
            scopes=tuple(ReservedScope.from_delta(d) for d in request.scopes),
            price_catalog_version=request.price_catalog_version,
            created_at=request.now,
            expires_at=request.now + self.reservation_ttl,
            idempotency_key_hash=(
                hash_idempotency_key(request.idempotency_key) if request.idempotency_key else None
            ),
            request_fingerprint=request.request_fingerprint or None,
            metadata=dict(request.metadata),
        )

    # ------------------------------------------------------------------
    # Denial decoding
    # ------------------------------------------------------------------

    def _decode_denial(
        self,
        denied: TransactionDenied,
        plan: TransactionPlan,
        request: ReservationRequest,
    ) -> Denial:
        """Turn failed slots into an error that names the binding constraint.

        Without the pre-image the backend returns alongside each failure, the
        best this could say is "a condition failed". With it, the response can
        state which scope, which dimension, how much was available and how much
        was needed -- the difference between an actionable error and a shrug.
        """
        status_failures: list[SlotFailure] = []
        scope_failures: list[SlotFailure] = []
        idempotency_failure: SlotFailure | None = None

        for failure in denied.failures:
            match failure.slot:
                case StatusCheckSlot():
                    status_failures.append(failure)
                case ScopeReserveSlot():
                    scope_failures.append(failure)
                case IdempotencyPutSlot():
                    idempotency_failure = failure
                case _:
                    pass

        # Status wins: a paused agent or closed session is a more fundamental
        # answer than "and also the budget was tight".
        if status_failures:
            return self._decode_status_denial(status_failures[0], request)

        if scope_failures:
            return self._decode_scope_denial(scope_failures, request)

        if idempotency_failure is not None:
            # Resolved by the caller against the stored record; from the
            # engine's perspective the transaction simply did not apply.
            raise TransactionConflict("idempotency key already claimed")

        raise PlanBugError(
            f"transaction denied with no interpretable failure: {[f.code for f in denied.failures]}"
        )

    def _decode_status_denial(self, failure: SlotFailure, request: ReservationRequest) -> Denial:
        slot = failure.slot
        assert isinstance(slot, StatusCheckSlot)
        item = failure.item or {}
        actual_status = str(item.get(A.STATUS, "UNKNOWN"))

        if slot.failure_code is DenialCode.AGENT_PAUSED:
            return Denial(
                code=DenialCode.AGENT_PAUSED,
                blocking_scope=ScopeRef.agent(request.agent_id),
                detail=str(item.get("pause_reason") or f"agent status is {actual_status}"),
            )

        # A session that is open but past its expiry is a different problem from
        # one that was closed, and the caller's remedy differs.
        expired = (
            slot.not_expired_after_epoch is not None and actual_status == SessionStatus.OPEN.value
        )
        code = DenialCode.SESSION_EXPIRED if expired else DenialCode.SESSION_CLOSED
        return Denial(
            code=code,
            blocking_scope=(ScopeRef.session(request.session_id) if request.session_id else None),
            detail=f"session status is {actual_status}",
            session_closed=not expired,
        )

    def _decode_scope_denial(
        self, failures: list[SlotFailure], request: ReservationRequest
    ) -> Denial:
        blocking = tuple(f.slot.scope for f in failures if isinstance(f.slot, ScopeReserveSlot))
        primary_scope = outermost(blocking)
        primary = next(
            f
            for f in failures
            if isinstance(f.slot, ScopeReserveSlot) and f.slot.scope == primary_scope
        )
        slot = primary.slot
        assert isinstance(slot, ScopeReserveSlot)
        item = primary.item or {}

        window = next(
            (d.window for d in request.scopes if d.scope == slot.scope),
            None,
        )

        remaining_money = Money(to_int(item.get(A.REMAINING_NANO, 0), A.REMAINING_NANO))
        remaining_tokens = TokenVector(
            input=max(0, to_int(item.get(A.REMAINING_INPUT, 0), A.REMAINING_INPUT)),
            output=max(0, to_int(item.get(A.REMAINING_OUTPUT, 0), A.REMAINING_OUTPUT)),
            total=max(0, to_int(item.get(A.REMAINING_TOTAL, 0), A.REMAINING_TOTAL)),
        )

        # Money and tokens are independent constraints. Reporting "budget
        # exhausted" when the caller actually ran out of output tokens sends
        # them to raise the wrong limit.
        money_short = remaining_money < slot.cost
        token_dimensions = slot.tokens.exceeded_dimensions(remaining_tokens)

        code = (
            DenialCode.BUDGET_EXHAUSTED
            if money_short or not token_dimensions
            else DenialCode.TOKEN_QUOTA_EXCEEDED
        )

        return Denial(
            code=code,
            blocking_scope=slot.scope,
            window=window,
            limit=Money(to_int(item.get(A.LIMIT_NANO, 0), A.LIMIT_NANO)),
            committed=Money(to_int(item.get(A.COMMITTED_NANO, 0), A.COMMITTED_NANO)),
            reserved=Money(to_int(item.get(A.RESERVED_NANO, 0), A.RESERVED_NANO)),
            available=remaining_money if remaining_money > Money.zero() else Money.zero(),
            requested=slot.cost,
            remaining_tokens=remaining_tokens,
            requested_tokens=slot.tokens,
            token_dimensions=token_dimensions,
            all_blocking=blocking,
        )

    # ------------------------------------------------------------------
    # Settlement
    # ------------------------------------------------------------------

    async def mark_dispatched(self, tenant_id: str, reservation_id: str) -> None:
        """Record that the provider is about to be contacted.

        One cheap unconditional write, issued immediately before the socket
        write. It is what lets a sweeper tell a gateway that crashed *before*
        reaching the provider -- whose reservation is provably safe to release
        -- from one that crashed after, whose reservation must be held. Without
        it every crashed request would encumber its budget until an operator
        intervened.
        """
        await self.repo.mark_dispatched(tenant_id, reservation_id)

    async def reconcile(
        self,
        tenant_id: str,
        reservation_id: str,
        usage: ProviderUsage,
        price: ModelPrice,
        now: datetime,
    ) -> ReconcileResult:
        """Settle a reservation against provider-reported usage.

        Pricing uses the catalog version pinned on the *reservation*, not
        whatever is active now. A price change between reserve and reconcile
        must not alter what this request cost.
        """
        reservation = await self._load(tenant_id, reservation_id)
        reservation.require_transition(ReservationState.RECONCILED)

        normalized = NormalizedUsage.from_provider(usage)
        breakdown = price.price_actual(normalized)
        actual_cost = breakdown.total
        actual_tokens = normalized.to_vector()

        entry = self._build_ledger_entry(
            reservation,
            kind=LedgerKind.USAGE,
            actual_cost=breakdown,
            actual_tokens=actual_tokens,
            normalized=normalized,
            provider_request_id=usage.provider_request_id,
            now=now,
            sequence=0,
        )

        slots: list[Slot] = [
            ReservationUpdateSlot(
                key=keys.reservation_key(tenant_id, reservation_id),
                reservation_id=reservation_id,
                expected_state=ReservationState.RESERVED.value,
                new_state=ReservationState.RECONCILED.value,
                at=now,
                actual_cost=actual_cost,
                actual_tokens=actual_tokens,
                provider_request_id=usage.provider_request_id,
            )
        ]
        slots.extend(
            self._settle_slots(reservation, actual_cost=actual_cost, actual_tokens=actual_tokens)
        )
        slots.append(
            LedgerPutSlot(
                key=self._ledger_key(reservation, entry, now, sequence=0),
                entry=entry,
            )
        )

        overage = actual_cost - reservation.reserved_cost
        if overage > Money.zero():
            # A provider exceeded the hard output cap we sent it. Recorded as
            # its own immutable entry rather than folded into the usage entry,
            # so the anomaly is visible in the ledger rather than averaged away.
            overage_entry = replace(
                entry,
                entry_id=UsageLedgerEntry.make_entry_id(reservation_id, 1),
                kind=LedgerKind.OVERAGE,
                actual_total_cost=overage,
            )
            slots.append(
                LedgerPutSlot(
                    key=self._ledger_key(reservation, overage_entry, now, sequence=1),
                    entry=overage_entry,
                )
            )

        plan = TransactionPlan(
            slots=tuple(slots),
            client_request_token=f"rec-{reservation_id}",
            description=f"reconcile {reservation_id}",
        )
        plan.validate()
        await self.repo.apply(plan)

        return ReconcileResult(
            reservation_id=reservation_id,
            actual_cost=actual_cost,
            actual_cost_breakdown=breakdown,
            actual_tokens=actual_tokens,
            reserved_cost=reservation.reserved_cost,
            ledger_entry=entry,
            overage=overage if overage > Money.zero() else Money.zero(),
        )

    async def release(
        self,
        tenant_id: str,
        reservation_id: str,
        reason: ReleaseReason,
        now: datetime,
    ) -> None:
        """Return a reservation to its budgets, unspent.

        Only legitimate when the provider provably did not bill us. Anything
        ambiguous goes to :meth:`mark_pending` instead.
        """
        reservation = await self._load(tenant_id, reservation_id)
        reservation.require_transition(ReservationState.RELEASED)

        entry = self._build_ledger_entry(
            reservation,
            kind=LedgerKind.RELEASE,
            actual_cost=CostBreakdown.zero(),
            actual_tokens=TokenVector.zero(),
            normalized=None,
            provider_request_id=None,
            now=now,
            sequence=0,
        )

        slots: list[Slot] = [
            ReservationUpdateSlot(
                key=keys.reservation_key(tenant_id, reservation_id),
                reservation_id=reservation_id,
                expected_state=reservation.state.value,
                new_state=ReservationState.RELEASED.value,
                at=now,
                release_reason=reason,
            )
        ]
        slots.extend(
            self._settle_slots(
                reservation, actual_cost=Money.zero(), actual_tokens=TokenVector.zero()
            )
        )
        slots.append(LedgerPutSlot(key=self._ledger_key(reservation, entry, now, 0), entry=entry))

        plan = TransactionPlan(
            slots=tuple(slots),
            client_request_token=f"rel-{reservation_id}",
            description=f"release {reservation_id}",
        )
        plan.validate()
        await self.repo.apply(plan)

    async def mark_pending(
        self,
        tenant_id: str,
        reservation_id: str,
        reason: PendingReason,
        now: datetime,
        provider_request_id: str | None = None,
    ) -> None:
        """Hold a reservation whose outcome is unknown.

        The money stays encumbered. ``remaining``, ``reserved`` and
        ``committed`` are all untouched; only ``pending`` moves, and it is a
        reporting subset of ``reserved`` rather than an extra deduction.

        This is the conservative reading of an ambiguous provider outcome, and
        it is the right one: if the provider may have generated and billed a
        completion, releasing the hold would let that same money be spent a
        second time.
        """
        reservation = await self._load(tenant_id, reservation_id)
        reservation.require_transition(ReservationState.RECONCILE_PENDING)

        # Provisionally recorded at the full reserved amount, so the runaway
        # detector and dashboards err toward over-reporting our exposure.
        entry = self._build_ledger_entry(
            reservation,
            kind=LedgerKind.PENDING_ASSUMED,
            actual_cost=CostBreakdown(output_cost=reservation.reserved_cost),
            actual_tokens=reservation.reserved_tokens,
            normalized=None,
            provider_request_id=provider_request_id,
            now=now,
            sequence=0,
        )

        slots: list[Slot] = [
            ReservationUpdateSlot(
                key=keys.reservation_key(tenant_id, reservation_id),
                reservation_id=reservation_id,
                expected_state=ReservationState.RESERVED.value,
                new_state=ReservationState.RECONCILE_PENDING.value,
                at=now,
                pending_reason=reason,
                provider_request_id=provider_request_id,
            )
        ]
        for scope in reservation.scopes:
            slots.append(
                ScopePendingSlot(
                    key=ItemKey(
                        keys.TABLE_CORE, self._scope_pk(reservation, scope), scope.sort_key
                    ),
                    scope=scope.scope,
                    reserved_cost=scope.cost,
                )
            )
        slots.append(LedgerPutSlot(key=self._ledger_key(reservation, entry, now, 0), entry=entry))

        plan = TransactionPlan(
            slots=tuple(slots),
            client_request_token=f"pend-{reservation_id}",
            description=f"mark pending {reservation_id}",
        )
        plan.validate()
        await self.repo.apply(plan)

    # ------------------------------------------------------------------
    # Settlement helpers
    # ------------------------------------------------------------------

    def _settle_slots(
        self,
        reservation: RequestReservation,
        *,
        actual_cost: Money,
        actual_tokens: TokenVector,
    ) -> list[Slot]:
        """Build one settle slot per scope the reservation actually held.

        Driven off the reservation's recorded scope vector rather than
        re-resolved from policy: policy may have changed since the hold was
        placed, and reversing a hold by any amount other than the one taken
        would corrupt the counters.

        The actual cost is apportioned across scopes in the same proportion it
        was reserved, which for the normal case -- every scope holding the same
        amount -- is simply the actual cost at each scope.
        """
        slots: list[Slot] = []
        for scope in reservation.scopes:
            share = _apportion(actual_cost, scope.cost, reservation.reserved_cost)
            token_share = TokenVector(
                input=_apportion_int(
                    actual_tokens.input, scope.tokens.input, reservation.reserved_tokens.input
                ),
                output=_apportion_int(
                    actual_tokens.output, scope.tokens.output, reservation.reserved_tokens.output
                ),
                total=_apportion_int(
                    actual_tokens.total, scope.tokens.total, reservation.reserved_tokens.total
                ),
            )
            slots.append(
                ScopeSettleSlot(
                    key=ItemKey(
                        keys.TABLE_CORE, self._scope_pk(reservation, scope), scope.sort_key
                    ),
                    scope=scope.scope,
                    reserved_cost=scope.cost,
                    reserved_tokens=scope.tokens,
                    actual_cost=share,
                    actual_tokens=token_share,
                )
            )
        return slots

    def _scope_pk(self, reservation: RequestReservation, scope: ReservedScope) -> str:
        return keys.budget_pk(reservation.tenant_id, scope.scope)

    def _ledger_key(
        self,
        reservation: RequestReservation,
        entry: UsageLedgerEntry,
        now: datetime,
        sequence: int,
    ) -> ItemKey:
        return keys.ledger_key(
            reservation.tenant_id,
            reservation.agent_id,
            year_month=now.strftime("%Y-%m"),
            created_at_micros=int(now.timestamp() * 1_000_000),
            reservation_id=reservation.reservation_id,
            sequence=sequence,
        )

    def _build_ledger_entry(
        self,
        reservation: RequestReservation,
        *,
        kind: LedgerKind,
        actual_cost: CostBreakdown,
        actual_tokens: TokenVector,
        normalized: NormalizedUsage | None,
        provider_request_id: str | None,
        now: datetime,
        sequence: int,
    ) -> UsageLedgerEntry:
        decision = (
            BudgetDecision.SUBSTITUTED_PREFERRED_MODEL_BUDGET
            if reservation.requested_model != reservation.effective_model
            else BudgetDecision.ALLOWED
        )
        return UsageLedgerEntry(
            entry_id=UsageLedgerEntry.make_entry_id(reservation.reservation_id, sequence),
            kind=kind,
            reservation_id=reservation.reservation_id,
            tenant_id=reservation.tenant_id,
            team_id=reservation.team_id,
            agent_id=reservation.agent_id,
            session_id=reservation.session_id,
            provider=reservation.provider,
            requested_model=reservation.requested_model,
            effective_model=reservation.effective_model,
            decision=decision,
            preflight_input_tokens=reservation.preflight_input_tokens,
            reserved_output_tokens=reservation.bounded_max_output_tokens,
            estimated_cost=reservation.estimated_cost,
            estimated_max_cost=reservation.reserved_cost,
            reserved_cost=reservation.reserved_cost,
            actual_tokens=actual_tokens,
            actual_cached_input_tokens=normalized.cached_input_tokens if normalized else 0,
            actual_reasoning_tokens=normalized.reasoning_tokens if normalized else 0,
            actual_cost=actual_cost,
            actual_total_cost=actual_cost.total,
            price_catalog_version=reservation.price_catalog_version,
            created_at=reservation.created_at,
            completed_at=now,
            provider_request_id=provider_request_id,
            scope_keys=tuple(s.scope.key() for s in reservation.scopes),
        )

    async def _load(self, tenant_id: str, reservation_id: str) -> RequestReservation:
        reservation: RequestReservation | None = await self.repo.get_reservation(
            tenant_id, reservation_id
        )
        if reservation is None:
            raise PlanBugError(f"reservation {reservation_id} not found")
        return reservation


def _apportion(actual: Money, scope_share: Money, total_reserved: Money) -> Money:
    """Split an actual cost across scopes in proportion to what each held.

    In the common case every scope holds the identical amount, so each scope
    settles the full actual cost -- which is correct: the same dollar is
    counted once against the team, once against the agent, and once against the
    session, because it was authorised against all three.
    """
    if total_reserved.nano == 0 or scope_share == total_reserved:
        return actual
    return actual.mul_div_ceil(scope_share.nano, total_reserved.nano)


def _apportion_int(actual: int, scope_share: int, total_reserved: int) -> int:
    if total_reserved == 0 or scope_share == total_reserved:
        return actual
    return -((-actual * scope_share) // total_reserved)


def _only_idempotency_failed(denied: TransactionDenied) -> bool:
    """True when the idempotency claim was the sole failing condition."""
    return bool(denied.failures) and all(
        isinstance(f.slot, IdempotencyPutSlot) for f in denied.failures
    )


def hash_idempotency_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def fingerprint_request(*parts: str) -> str:
    """Stable digest of a request's meaningful content.

    Compared on idempotent replay so that reusing a key with a *different* body
    is caught and rejected rather than silently returning the wrong cached
    response.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()

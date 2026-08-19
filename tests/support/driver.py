"""A test driver that runs the complete governed-request lifecycle.

Exists so acceptance tests can read as scenarios -- "spend $0.08, then assert
exactly one warning" -- rather than as twenty lines of plumbing repeated per
test. It runs the same sequence the API layer does:

    count -> bound -> estimate -> reserve -> invoke -> reconcile -> effects
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from abc_gateway.domain.alerts import AlertEvent
from abc_gateway.domain.errors import AuthorizationDenied, Denial, PendingReason, ReleaseReason
from abc_gateway.domain.money import Money
from abc_gateway.domain.policy import AgentPolicy, BudgetPolicy
from abc_gateway.domain.reservation import ReservationGrant
from abc_gateway.domain.session import Session
from abc_gateway.engine.budget_engine import BudgetEngine, ReconcileResult
from abc_gateway.engine.effects import EffectOutcome, SettlementEffects
from abc_gateway.engine.routing import RoutingEngine
from abc_gateway.providers.base import (
    ChatMessage,
    ChatRequest,
    FailedAmbiguous,
    FailedBilled,
    FailedNotBilled,
    Succeeded,
    Timeouts,
)


@dataclass
class CallResult:
    """Everything one governed call produced."""

    grant: ReservationGrant | None = None
    denial: Denial | None = None
    reconcile: ReconcileResult | None = None
    effects: EffectOutcome | None = None
    attempts: tuple = ()
    provider_invoked: bool = False
    warnings: tuple[AlertEvent, ...] = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.grant is not None

    @property
    def substituted(self) -> bool:
        return bool(self.grant and self.grant.substituted)

    @property
    def effective_model(self) -> str | None:
        return self.grant.effective_model if self.grant else None

    @property
    def actual_cost(self) -> Money:
        return self.reconcile.actual_cost if self.reconcile else Money.zero()


class Driver:
    """Drives governed calls end to end against the in-memory stack."""

    def __init__(
        self,
        *,
        repo,
        catalog,
        provider,
        tenant_id: str,
        team_id: str,
    ) -> None:
        self.repo = repo
        self.catalog = catalog
        self.provider = provider
        self.tenant_id = tenant_id
        self.team_id = team_id
        self.engine = BudgetEngine(repo)
        self.router = RoutingEngine(self.engine, catalog)
        self.effects = SettlementEffects(repo)

    async def call(
        self,
        *,
        agent: AgentPolicy,
        team: BudgetPolicy,
        session: Session | None = None,
        now: datetime,
        prompt: str = "hello",
        max_output_tokens: int | None = None,
        tools: tuple = (),
        requires_vision: bool = False,
        idempotency_key: str | None = None,
    ) -> CallResult:
        chat = ChatRequest(
            messages=(ChatMessage("user", prompt),),
            model=agent.routing.preferred.model,
            max_output_tokens=max_output_tokens,
            tools=tools,
            requires_vision=requires_vision,
        )

        try:
            outcome = await self.router.route_and_reserve(
                agent_policy=agent,
                team_policy=team,
                session=session,
                chat=chat,
                adapter=self.provider,
                tenant_id=self.tenant_id,
                team_id=self.team_id,
                now=now,
                idempotency_key=idempotency_key,
            )
        except AuthorizationDenied as exc:
            # The whole point: a denied request never reaches the provider.
            reason = None
            if session is not None:
                # Closure path B -- the next request would not fit -- runs
                # synchronously so the rejection already reports the session as
                # closed rather than inviting a pointless retry.
                reason = await self.effects.close_session_on_denial(
                    exc.denial,
                    tenant_id=self.tenant_id,
                    session_id=session.session_id,
                    min_viable=agent.session_min_viable,
                    now=now,
                )
            return CallResult(
                denial=exc.denial,
                effects=EffectOutcome(
                    session_closed=reason is not None, session_close_reason=reason
                ),
            )

        grant = outcome.grant
        await self.engine.mark_dispatched(self.tenant_id, grant.reservation_id)

        provider_outcome = await self.provider.invoke(
            chat,
            grant.effective_model,
            max_output_tokens=grant.bounded_max_output_tokens,
            timeouts=Timeouts(),
            correlation_id=grant.reservation_id,
        )

        result = CallResult(grant=grant, attempts=outcome.attempts, provider_invoked=True)

        match provider_outcome:
            case Succeeded(usage=usage) | FailedBilled(usage=usage):
                result.reconcile = await self.engine.reconcile(
                    self.tenant_id,
                    grant.reservation_id,
                    usage,
                    self.catalog.get(grant.provider, grant.effective_model),
                    now,
                )
            case FailedNotBilled():
                # Proven not billed: the hold goes straight back.
                await self.engine.release(
                    self.tenant_id,
                    grant.reservation_id,
                    ReleaseReason.PROVIDER_REJECTED,
                    now,
                )
                return result
            case FailedAmbiguous(provider_request_id=prid):
                # Unknown outcome: the money stays encumbered.
                await self.engine.mark_pending(
                    self.tenant_id,
                    grant.reservation_id,
                    PendingReason.PROVIDER_TIMEOUT,
                    now,
                    provider_request_id=prid,
                )
                return result

        reservation = await self.repo.get_reservation(self.tenant_id, grant.reservation_id)
        result.effects = await self.effects.apply(
            reservation,
            warning_percent=agent.budget.warning_percent,
            session_min_viable=agent.session_min_viable,
            now=now,
        )
        result.warnings = result.effects.warnings_emitted
        return result

    async def spend(
        self,
        *,
        agent: AgentPolicy,
        team: BudgetPolicy,
        now: datetime,
        times: int,
        session: Session | None = None,
    ) -> list[CallResult]:
        """Make ``times`` sequential calls, stopping at the first denial."""
        results = []
        for index in range(times):
            result = await self.call(
                agent=agent,
                team=team,
                session=session,
                now=now,
                idempotency_key=f"seq-{agent.agent_id}-{index}",
            )
            results.append(result)
            if not result.allowed:
                break
        return results

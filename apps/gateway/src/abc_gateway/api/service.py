"""The governed inference service.

This is where the lifecycle actually runs, in the one order that makes
enforcement possible:

    count -> bound -> estimate -> reserve -> invoke -> reconcile

Everything before ``reserve`` is preparation; nothing before it can spend money.
Everything after ``invoke`` is bookkeeping that must complete even when the call
failed. The provider is reached at exactly one point in this file, and only ever
while holding a grant.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from ..auth.identity import Principal, verify_session_ownership
from ..domain.clock import Clock
from ..domain.errors import (
    AuthorizationDenied,
    PendingReason,
    ReleaseReason,
)
from ..domain.ledger import BudgetDecision
from ..domain.money import Money
from ..engine.budget_engine import BudgetEngine, fingerprint_request
from ..engine.effects import SettlementEffects
from ..engine.routing import RoutingEngine
from ..observability.logging import get_logger, log_request_decision, request_id_var
from ..observability.telemetry import TelemetrySink, TraceContext, metrics
from ..providers.base import (
    ChatRequest,
    FailedAmbiguous,
    FailedBilled,
    FailedNotBilled,
    ProviderAdapter,
    Succeeded,
    Timeouts,
)

logger = get_logger("abc_gateway.service")


@dataclass(frozen=True, slots=True)
class InferenceResult:
    """The outcome of one governed call, ready to be rendered as a response."""

    request_id: str
    content: str
    requested_model: str
    effective_model: str
    provider: str
    decision: BudgetDecision
    input_tokens: int
    output_tokens: int
    estimated_cost: Money
    actual_cost: Money
    substituted: bool
    estimated_savings: Money | None
    latency_ms: float

    def headers(self) -> dict[str, str]:
        """Response headers that make a governance decision visible.

        Substitution especially: an application owner whose output quality
        changed needs to discover that governance rerouted the traffic, not
        conclude their prompt regressed.
        """
        headers = {
            "X-Budget-Decision": self.decision.value,
            "X-Budget-Requested-Model": self.requested_model,
            "X-Budget-Effective-Model": self.effective_model,
            "X-Request-Id": self.request_id,
            "X-Budget-Actual-Cost-Usd": self.actual_cost.to_usd_str(),
        }
        if self.substituted:
            headers["X-Budget-Scope"] = "preferred_model_allocation"
            if self.estimated_savings is not None:
                headers["X-Budget-Estimated-Savings-Usd"] = self.estimated_savings.to_usd_str()
        return headers


class InferenceService:
    """Runs governed provider calls."""

    def __init__(
        self,
        *,
        repository,
        engine: BudgetEngine,
        router: RoutingEngine,
        effects: SettlementEffects,
        # Typed as the Protocol, not `object`: the whole point of the adapter
        # abstraction is that this layer can call `invoke` without knowing
        # which provider it is talking to.
        adapters: dict[str, ProviderAdapter],
        catalog,
        clock: Clock,
        telemetry: TelemetrySink,
        timeouts: Timeouts,
    ) -> None:
        self.repo = repository
        self.engine = engine
        self.router = router
        self.effects = effects
        self.adapters = adapters
        self.catalog = catalog
        self.clock = clock
        self.telemetry = telemetry
        self.timeouts = timeouts

    async def invoke(
        self,
        *,
        principal: Principal,
        chat: ChatRequest,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        request_id: str,
    ) -> InferenceResult:
        """Authorize, invoke, and settle one request."""
        started = time.perf_counter()
        request_id_var.set(request_id)
        now = self.clock.now()

        agent_policy = await self.repo.get_agent_policy(principal.tenant_id, principal.agent_id)
        if agent_policy is None:
            raise LookupError(f"no policy configured for agent {principal.agent_id}")

        from ..domain.scopes import ScopeRef

        team_policy = await self.repo.get_budget_policy(
            principal.tenant_id, ScopeRef.team(principal.team_id)
        )
        if team_policy is None:
            raise LookupError(f"no budget configured for team {principal.team_id}")

        session = None
        if session_id:
            session = await self.repo.get_session(principal.tenant_id, session_id)
            # A session id is a correlation handle, not an authorisation.
            verify_session_ownership(principal, session)

        adapter = self.adapters.get(agent_policy.routing.preferred.provider)
        if adapter is None:
            raise LookupError(
                f"provider {agent_policy.routing.preferred.provider!r} is not configured"
            )

        fingerprint = fingerprint_request(
            agent_policy.routing.preferred.model,
            chat.system or "",
            *(m.content for m in chat.messages),
        )

        # ---- reserve -----------------------------------------------------
        # Nothing above this line can spend money; nothing below it runs
        # without a grant.
        try:
            outcome = await self.router.route_and_reserve(
                agent_policy=agent_policy,
                team_policy=team_policy,
                session=session,
                chat=chat,
                adapter=adapter,
                tenant_id=principal.tenant_id,
                team_id=principal.team_id,
                now=now,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
            )
        except AuthorizationDenied as denied:
            await self._on_denied(denied, principal, session, agent_policy, now, request_id)
            raise

        grant = outcome.grant
        decision = (
            BudgetDecision.SUBSTITUTED_PREFERRED_MODEL_BUDGET
            if grant.substituted
            else BudgetDecision.ALLOWED
        )

        # ---- invoke ------------------------------------------------------
        await self.engine.mark_dispatched(principal.tenant_id, grant.reservation_id)
        effective_adapter = self.adapters[grant.provider]

        provider_outcome = await effective_adapter.invoke(
            chat,
            grant.effective_model,
            max_output_tokens=grant.bounded_max_output_tokens,
            timeouts=self.timeouts,
            correlation_id=grant.reservation_id,
        )

        # ---- reconcile ---------------------------------------------------
        return await self._settle(
            provider_outcome,
            grant=grant,
            principal=principal,
            agent_policy=agent_policy,
            decision=decision,
            request_id=request_id,
            now=now,
            started=started,
        )

    async def _settle(
        self,
        provider_outcome,
        *,
        grant,
        principal: Principal,
        agent_policy,
        decision: BudgetDecision,
        request_id: str,
        now: datetime,
        started: float,
    ) -> InferenceResult:
        tenant = principal.tenant_id
        price = self.catalog.get(grant.provider, grant.effective_model)
        latency_ms = (time.perf_counter() - started) * 1000

        match provider_outcome:
            # Succeeded and FailedBilled are settled identically: in both cases
            # the provider generated tokens and charged us. The only difference
            # is whether there is usable content to return -- a content filter
            # costs exactly as much as a successful completion.
            case Succeeded() | FailedBilled():
                usage = provider_outcome.usage
                content = getattr(provider_outcome, "content", "")
                result = await self.engine.reconcile(
                    tenant, grant.reservation_id, usage, price, now
                )
                reservation = await self.repo.get_reservation(tenant, grant.reservation_id)
                await self.effects.apply(
                    reservation,
                    warning_percent=agent_policy.budget.warning_percent,
                    session_min_viable=agent_policy.session_min_viable,
                    now=now,
                )
                self._emit(
                    grant,
                    principal,
                    decision,
                    request_id,
                    latency_ms,
                    input_tokens=result.actual_tokens.input,
                    output_tokens=result.actual_tokens.output,
                    actual_cost=result.actual_cost,
                )
                return InferenceResult(
                    request_id=request_id,
                    content=content or "",
                    requested_model=grant.requested_model or grant.effective_model,
                    effective_model=grant.effective_model,
                    provider=grant.provider,
                    decision=decision,
                    input_tokens=result.actual_tokens.input,
                    output_tokens=result.actual_tokens.output,
                    estimated_cost=grant.reserved_cost,
                    actual_cost=result.actual_cost,
                    substituted=grant.substituted,
                    estimated_savings=grant.estimated_savings,
                    latency_ms=latency_ms,
                )

            case FailedNotBilled(error=error):
                # Proven unbilled: the hold goes straight back.
                await self.engine.release(
                    tenant, grant.reservation_id, ReleaseReason.PROVIDER_REJECTED, now
                )
                metrics.increment("provider_rejections_total")
                raise ProviderRejected(error, request_id)

            case FailedAmbiguous(error=error, provider_request_id=prid):
                # Unknown outcome: keep the money encumbered. Releasing here
                # would let the same dollars be spent twice if the provider did
                # in fact bill us.
                await self.engine.mark_pending(
                    tenant,
                    grant.reservation_id,
                    PendingReason.PROVIDER_TIMEOUT,
                    now,
                    provider_request_id=prid,
                )
                metrics.increment("provider_ambiguous_total")
                logger.warning(
                    "provider.ambiguous_outcome",
                    request_id=request_id,
                    reservation_id=grant.reservation_id,
                    error=error,
                )
                raise ProviderUnresolved(error, request_id, grant.reservation_id)

            case _:  # pragma: no cover
                raise RuntimeError(f"unhandled provider outcome: {provider_outcome!r}")

    async def _on_denied(
        self,
        denied: AuthorizationDenied,
        principal: Principal,
        session,
        agent_policy,
        now: datetime,
        request_id: str,
    ) -> None:
        """Record a refusal, and close the session if it is finished."""
        metrics.increment(
            "budget_rejections_total",
            scope=(
                denied.denial.blocking_scope.type.value
                if denied.denial.blocking_scope
                else "unknown"
            ),
        )
        if session is not None:
            await self.effects.close_session_on_denial(
                denied.denial,
                tenant_id=principal.tenant_id,
                session_id=session.session_id,
                min_viable=agent_policy.session_min_viable,
                now=now,
            )
        logger.info(
            "budget.request.denied",
            request_id=request_id,
            tenant_id=principal.tenant_id,
            agent_id=principal.agent_id,
            code=denied.denial.code.value,
            blocking_scope=(
                denied.denial.blocking_scope.key() if denied.denial.blocking_scope else None
            ),
            # Proof, in the log, that a denial costs nothing.
            provider_invoked=False,
        )

    def _emit(
        self,
        grant,
        principal: Principal,
        decision: BudgetDecision,
        request_id: str,
        latency_ms: float,
        *,
        input_tokens: int,
        output_tokens: int,
        actual_cost: Money,
    ) -> None:
        log_request_decision(
            logger,
            event="budget.request.reconciled",
            request_id=request_id,
            tenant_id=principal.tenant_id,
            team_id=principal.team_id,
            agent_id=principal.agent_id,
            session_id=None,
            provider=grant.provider,
            requested_model=grant.requested_model or grant.effective_model,
            effective_model=grant.effective_model,
            decision=decision.value,
            actual_input_tokens=input_tokens,
            actual_output_tokens=output_tokens,
            reserved=grant.reserved_cost,
            actual_cost=actual_cost,
            latency_ms=latency_ms,
        )
        # Fire-and-forget: a telemetry failure must not affect this request.
        self.telemetry.emit(
            TraceContext(
                request_id=request_id,
                tenant_id=principal.tenant_id,
                team_id=principal.team_id,
                agent_id=principal.agent_id,
                session_id=None,
                requested_model=grant.requested_model or grant.effective_model,
                effective_model=grant.effective_model,
                provider=grant.provider,
                budget_decision=decision.value,
            ),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=grant.reserved_cost,
            actual_cost=actual_cost,
            latency_ms=latency_ms,
        )
        if grant.substituted:
            metrics.increment("budget_substitutions_total")


class ProviderRejected(Exception):
    """The provider refused the request, provably without charging us."""

    def __init__(self, error: str, request_id: str) -> None:
        self.error = error
        self.request_id = request_id
        super().__init__(error)


class ProviderUnresolved(Exception):
    """The provider's outcome is unknown; the reservation is still held."""

    def __init__(self, error: str, request_id: str, reservation_id: str) -> None:
        self.error = error
        self.request_id = request_id
        self.reservation_id = reservation_id
        super().__init__(error)


def utcnow() -> datetime:
    return datetime.now(UTC)

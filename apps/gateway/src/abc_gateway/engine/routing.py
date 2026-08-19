"""Capability- and price-aware model routing under budget pressure.

When a model's own allocation is exhausted, the gateway may fall back to a
cheaper model. Three rules govern that, and each exists because violating it
turns a cost control into a different kind of bug:

* **Fallback may only escape the allocation.** If the denial came from the team,
  agent, or session scope, the chain aborts immediately. Those scopes are in
  every candidate's transaction anyway, so a fallback could not spend past them
  even if it tried -- but aborting early gives the caller the truthful error
  ("your agent budget is gone") instead of a misleading one ("no model was
  available").
* **Cheaper is not the same as interchangeable.** A fallback whose context
  window is too small for the prompt, or that cannot accept the request's
  tools, does not save money -- it fails, or silently returns something worse.
  Capabilities are checked before price.
* **"Cheaper" is verified, not assumed.** Configuration drifts and prices
  change. The estimated worst-case cost of the actual request is computed for
  both models and compared. A "fallback" that is more expensive for this
  particular request is refused, which is the case a naive implementation gets
  wrong when a long prompt meets a model with cheap output and dear input.

Substitution is never silent: the decision, both model names, and the estimated
saving travel back with the response.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..domain.errors import AuthorizationDenied, Denial, DenialCode
from ..domain.money import Money
from ..domain.policy import AgentPolicy, BudgetPolicy, ModelCandidate
from ..domain.reservation import ReservationGrant
from ..domain.scopes import ScopeType
from ..domain.session import Session
from ..domain.tokens import TokenVector
from ..pricing.catalog import ModelCapabilities, ModelPrice, PriceCatalog
from ..providers.base import ChatRequest
from .budget_engine import BudgetEngine, ReservationRequest
from .scope_resolver import check_fits_in_pristine_window, resolve_scopes


@dataclass(frozen=True, slots=True)
class RoutingAttempt:
    """One candidate considered, and why it was or was not used."""

    model: str
    provider: str
    eligible: bool
    reason: str
    estimated_cost: Money | None = None


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    grant: ReservationGrant
    attempts: tuple[RoutingAttempt, ...]


class NoEligibleModel(AuthorizationDenied):
    """Every candidate in the chain was ineligible."""


class RoutingEngine:
    """Chooses a model, then reserves against it."""

    def __init__(
        self,
        engine: BudgetEngine,
        catalog: PriceCatalog,
        *,
        default_safety_bps: int = 200,
    ) -> None:
        self.engine = engine
        self.catalog = catalog
        self.default_safety_bps = default_safety_bps

    async def route_and_reserve(
        self,
        *,
        agent_policy: AgentPolicy,
        team_policy: BudgetPolicy,
        session: Session | None,
        chat: ChatRequest,
        adapter,
        tenant_id: str,
        team_id: str,
        now: datetime,
        idempotency_key: str | None = None,
        request_fingerprint: str = "",
    ) -> RoutingOutcome:
        chain = agent_policy.routing.chain()
        preferred = chain[0]
        attempts: list[RoutingAttempt] = []

        preferred_price = self.catalog.get(preferred.provider, preferred.model)
        input_tokens = await adapter.count_input_tokens(chat, preferred.model)
        preferred_estimate = self._estimate(
            preferred_price, chat, input_tokens, agent_policy, adapter, preferred.model
        )

        last_denial: Denial | None = None
        max_attempts = min(len(chain), agent_policy.routing.max_attempts)

        for position, candidate in enumerate(chain[:max_attempts]):
            eligibility = self._check_eligibility(
                candidate=candidate,
                preferred=preferred,
                policy=agent_policy,
                chat=chat,
                adapter=adapter,
                position=position,
                preferred_estimate=preferred_estimate.total,
                input_tokens=input_tokens,
            )
            attempts.append(eligibility)
            if not eligibility.eligible:
                continue

            price = self.catalog.get(candidate.provider, candidate.model)
            bounded_output = adapter.bound_max_output_tokens(
                chat, candidate.model, agent_policy.default_max_output_tokens
            )
            # Recount for the candidate: tokenization is model-specific, and
            # reserving against the preferred model's count would be wrong.
            candidate_input = (
                input_tokens
                if candidate.model == preferred.model
                else await adapter.count_input_tokens(chat, candidate.model)
            )
            estimate = price.estimate_worst_case(
                input_tokens=candidate_input,
                max_output_tokens=bounded_output,
                safety_bps=(
                    0 if price.capabilities.supports_hard_output_cap else self.default_safety_bps
                ),
            )
            tokens = TokenVector(
                input=candidate_input,
                output=bounded_output,
                total=candidate_input + bounded_output,
            )
            scopes = resolve_scopes(
                agent_policy=agent_policy,
                team_policy=team_policy,
                session=session,
                provider=candidate.provider,
                model=candidate.model,
                cost=estimate.total,
                tokens=tokens,
                now=now,
            )

            # A request larger than a scope's entire limit can never succeed.
            # Saying so plainly beats reporting "budget exhausted" against a
            # budget that has not been touched.
            impossible = check_fits_in_pristine_window(scopes)
            if impossible is not None:
                last_denial = Denial(
                    code=DenialCode.EXCEEDS_WINDOW_LIMIT,
                    blocking_scope=impossible.scope,
                    window=impossible.window,
                    limit=impossible.limits.limit,
                    requested=impossible.cost,
                    detail="request exceeds this scope's entire limit",
                )
                continue

            request = ReservationRequest(
                tenant_id=tenant_id,
                team_id=team_id,
                agent_id=agent_policy.agent_id,
                session_id=session.session_id if session else None,
                provider=candidate.provider,
                requested_model=preferred.model,
                effective_model=candidate.model,
                price=price,
                price_catalog_version=self.catalog.version,
                preflight_input_tokens=candidate_input,
                bounded_max_output_tokens=bounded_output,
                scopes=scopes,
                estimated_cost=estimate,
                now=now,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                check_session_status=session is not None,
                substituted=position > 0,
                estimated_savings=(
                    preferred_estimate.total - estimate.total if position > 0 else None
                ),
            )

            try:
                grant = await self.engine.reserve(request)
            except AuthorizationDenied as exc:
                last_denial = exc.denial
                if not self._may_try_next(exc.denial, agent_policy):
                    # Team, agent, or session is out. No model helps.
                    raise
                continue

            return RoutingOutcome(grant=grant, attempts=tuple(attempts))

        raise NoEligibleModel(
            last_denial
            or Denial(
                code=DenialCode.NO_ELIGIBLE_MODEL,
                detail="no model in the routing chain was eligible",
            )
        )

    # ------------------------------------------------------------------

    def _may_try_next(self, denial: Denial, policy: AgentPolicy) -> bool:
        """Whether falling back to another model is legitimate.

        Only when the *sole* blocking scope is a model allocation. An exhausted
        allocation is a routing signal; an exhausted parent budget is a full
        stop, and treating it as a routing signal would be the exact "spend past
        the cap on a cheaper model" bug the hierarchy exists to prevent.
        """
        if not policy.routing.allow_fallback:
            return False
        if denial.code in (
            DenialCode.AGENT_PAUSED,
            DenialCode.SESSION_CLOSED,
            DenialCode.SESSION_EXPIRED,
        ):
            return False
        blocking = denial.all_blocking or (
            (denial.blocking_scope,) if denial.blocking_scope else ()
        )
        if not blocking:
            return False
        return all(s.type is ScopeType.MODEL_ALLOCATION for s in blocking)

    def _check_eligibility(
        self,
        *,
        candidate: ModelCandidate,
        preferred: ModelCandidate,
        policy: AgentPolicy,
        chat: ChatRequest,
        adapter,
        position: int,
        preferred_estimate: Money,
        input_tokens: int,
    ) -> RoutingAttempt:
        if position == 0:
            return RoutingAttempt(candidate.model, candidate.provider, True, "preferred")

        if policy.routing.require_same_provider and candidate.provider != preferred.provider:
            # Crossing providers changes data residency, tool semantics and
            # response shape at once -- rarely what "use something cheaper"
            # was meant to authorise.
            return RoutingAttempt(candidate.model, candidate.provider, False, "different_provider")

        if not self.catalog.has(candidate.provider, candidate.model):
            return RoutingAttempt(candidate.model, candidate.provider, False, "no_price_entry")

        price = self.catalog.get(candidate.provider, candidate.model)
        incompatible = _capability_gap(price.capabilities, chat, input_tokens)
        if incompatible:
            return RoutingAttempt(
                candidate.model, candidate.provider, False, f"incompatible:{incompatible}"
            )

        bounded = adapter.bound_max_output_tokens(
            chat, candidate.model, policy.default_max_output_tokens
        )
        estimate = price.estimate_worst_case(input_tokens, bounded).total
        if estimate >= preferred_estimate:
            # Configured as a fallback, but not actually cheaper for *this*
            # request. Routing to it would spend more, not less.
            return RoutingAttempt(
                candidate.model, candidate.provider, False, "not_cheaper", estimate
            )

        return RoutingAttempt(
            candidate.model, candidate.provider, True, "cheaper_and_compatible", estimate
        )

    def _estimate(
        self,
        price: ModelPrice,
        chat: ChatRequest,
        input_tokens: int,
        policy: AgentPolicy,
        adapter,
        model: str,
    ):
        bounded = adapter.bound_max_output_tokens(chat, model, policy.default_max_output_tokens)
        return price.estimate_worst_case(
            input_tokens=input_tokens,
            max_output_tokens=bounded,
            safety_bps=(
                0 if price.capabilities.supports_hard_output_cap else self.default_safety_bps
            ),
        )


def _capability_gap(
    capabilities: ModelCapabilities, chat: ChatRequest, input_tokens: int
) -> str | None:
    """Name the first capability the candidate lacks, or None if it fits."""
    if input_tokens > capabilities.max_context_tokens:
        return "context_length"
    if chat.tools and not capabilities.supports_tools:
        return "tools"
    if chat.requires_structured_output and not capabilities.supports_structured_output:
        return "structured_output"
    if chat.requires_vision and not capabilities.supports_vision:
        return "vision"
    return None

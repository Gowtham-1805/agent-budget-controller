"""Turn policy into the vector of scopes a request must fit inside.

The output is deliberately a *deduplicated* mapping rather than a list. Walking
the policy graph can legitimately reach the same (scope, window) pair twice --
a model allocation configured on a monthly window re-reaches the agent's monthly
window, for instance. DynamoDB rejects a transaction that touches one item
twice, and it does so with a ``ValidationException`` that names nothing useful,
so colliding deltas are merged here instead.
"""

from __future__ import annotations

from datetime import datetime

from ..domain.money import Money
from ..domain.policy import AgentPolicy, BudgetPolicy
from ..domain.scopes import ScopeDelta, ScopeLimits, ScopeRef
from ..domain.session import Session
from ..domain.tokens import TokenVector
from ..domain.window import BudgetWindow, WindowType


class ScopeResolutionError(ValueError):
    """The scope vector could not be built."""


def resolve_scopes(
    *,
    agent_policy: AgentPolicy,
    team_policy: BudgetPolicy,
    session: Session | None,
    provider: str,
    model: str,
    cost: Money,
    tokens: TokenVector,
    now: datetime,
) -> tuple[ScopeDelta, ...]:
    """Build the full set of scopes this request must fit inside.

    Order is TEAM, AGENT, SESSION, MODEL_ALLOCATION -- outermost first. That
    ordering carries through to the transaction, so when several conditions
    fail at once the outermost failure is the one reported, and it is also what
    decides whether a cheaper fallback is legal: an exhausted allocation can be
    routed around, an exhausted team cannot.
    """
    deltas: dict[tuple[str, str], ScopeDelta] = {}

    def add(scope: ScopeRef, window: BudgetWindow, limits: ScopeLimits, version: str) -> None:
        delta = ScopeDelta(
            scope=scope,
            window=window,
            cost=cost,
            tokens=tokens,
            limits=limits,
            policy_version=version,
        )
        key = delta.storage_key()
        existing = deltas.get(key)
        deltas[key] = existing.merge(delta) if existing else delta

    # -- team ---------------------------------------------------------------
    add(
        ScopeRef.team(team_policy.scope_id),
        BudgetWindow.for_instant(team_policy.window_type, now, team_policy.billing_tz),
        team_policy.scope_limits(),
        team_policy.version,
    )

    # -- agent --------------------------------------------------------------
    agent_budget = agent_policy.budget
    add(
        ScopeRef.agent(agent_policy.agent_id),
        BudgetWindow.for_instant(agent_budget.window_type, now, agent_budget.billing_tz),
        agent_budget.scope_limits(),
        agent_budget.version,
    )

    # -- session ------------------------------------------------------------
    # A session budget never resets, so its window is lifecycle-scoped.
    if session is not None and agent_policy.session_budget is not None:
        add(
            ScopeRef.session(session.session_id),
            BudgetWindow.for_instant(
                WindowType.SESSION,
                now,
                agent_budget.billing_tz,
                session_id=session.session_id,
                session_start=session.opened_at,
            ),
            ScopeLimits(limit=agent_policy.session_budget, tokens=TokenVector.unlimited()),
            agent_budget.version,
        )

    # -- model allocation ---------------------------------------------------
    # Present only when the agent configures a sub-budget for this model. Its
    # parents are already in the vector, which is exactly why exhausting an
    # allocation cannot be used to escape them.
    allocation = agent_policy.routing.allocation_for(provider, model)
    if allocation is not None:
        add(
            ScopeRef.model_allocation(agent_policy.agent_id, provider, model),
            BudgetWindow.for_instant(allocation.window_type, now, agent_budget.billing_tz),
            ScopeLimits(limit=allocation.limit, tokens=allocation.tokens),
            agent_budget.version,
        )

    return tuple(deltas.values())


def check_fits_in_pristine_window(
    deltas: tuple[ScopeDelta, ...],
) -> ScopeDelta | None:
    """Find a scope whose entire limit is smaller than this one request.

    Checked in Python rather than in the transaction because a condition
    expression cannot compare two stored values against each other. Answering
    it up front turns a permanently impossible request into a clear
    "this request is larger than the budget" instead of a confusing
    "budget exhausted" reported against a budget that has not been touched.
    """
    for delta in deltas:
        if delta.cost > delta.limits.limit:
            return delta
        if not delta.tokens.fits_within(delta.limits.tokens):
            return delta
    return None

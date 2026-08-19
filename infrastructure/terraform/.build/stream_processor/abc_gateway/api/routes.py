"""HTTP routes: control plane and data plane.

The data plane mirrors OpenAI's chat-completions shape on purpose. The goal is
that an existing agent adopts governance by changing its base URL, not by
rewriting its LLM integration -- a controller that requires a rewrite is a
controller nobody turns on.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from ulid import ULID

from ..auth.identity import Principal
from ..domain.agent import RunawayPolicy
from ..domain.money import Money
from ..domain.policy import (
    AgentPolicy,
    BudgetPolicy,
    ModelAllocationPolicy,
    ModelCandidate,
    RoutingPolicy,
)
from ..domain.scopes import ScopeRef, ScopeType
from ..domain.session import Session, SessionCloseReason, SessionStatus
from ..domain.state import BudgetState
from ..domain.tokens import TokenVector
from ..domain.window import BudgetWindow, WindowType
from ..providers.base import ChatMessage, ChatRequest
from . import schemas as S
from .deps import Container, get_container, get_principal

router = APIRouter()
admin_router = APIRouter(prefix="/v1/admin", tags=["admin"])
control_router = APIRouter(prefix="/v1", tags=["control-plane"])
data_router = APIRouter(prefix="/v1", tags=["data-plane"])


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/healthz", response_model=S.HealthResponse, tags=["ops"])
async def healthz() -> S.HealthResponse:
    """Liveness: the process is running.

    Intentionally checks nothing else. A liveness probe that fails when a
    dependency is down gets the container killed and restarted, which fixes
    nothing and turns a partial outage into a crash loop.
    """
    from .. import __version__

    return S.HealthResponse(status="ok", version=__version__)


@router.get("/readyz", response_model=S.ReadinessResponse, tags=["ops"])
async def readyz(
    response: Response, container: Container = Depends(get_container)
) -> S.ReadinessResponse:
    """Readiness: this instance is safe to govern traffic."""
    ready, checks, detail = await container.readiness()
    if not ready:
        response.status_code = 503
    return S.ReadinessResponse(
        status="ready" if ready else "not_ready", checks=checks, detail=detail
    )


# ---------------------------------------------------------------------------
# Data plane
# ---------------------------------------------------------------------------


@data_router.post("/chat/completions")
async def chat_completions(
    request: Request,
    body: S.ChatCompletionRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    """A governed chat completion.

    The provider is reached only if the request's worst-case cost was
    successfully reserved from every applicable budget first.
    """
    request_id = request.state.request_id

    chat = ChatRequest(
        messages=tuple(
            ChatMessage(role=m.role, content=m.content) for m in body.messages if m.role != "system"
        ),
        model=body.model or "",
        max_output_tokens=body.output_ceiling(),
        temperature=body.temperature,
        tools=tuple(body.tools or ()),
        system=next((m.content for m in body.messages if m.role == "system"), None),
    )

    result = await container.service.invoke(
        principal=principal,
        chat=chat,
        session_id=body.session_id,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )

    payload = S.ChatCompletionResponse(
        id=request_id,
        model=result.effective_model,
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.content},
                "finish_reason": "stop",
            }
        ],
        usage=S.Usage(
            prompt_tokens=result.input_tokens,
            completion_tokens=result.output_tokens,
            total_tokens=result.input_tokens + result.output_tokens,
        ),
        budget=S.BudgetMetadata(
            decision=result.decision.value,
            requested_model=result.requested_model,
            effective_model=result.effective_model,
            substituted=result.substituted,
            estimated_cost_usd=result.estimated_cost.to_usd_str(),
            actual_cost_usd=result.actual_cost.to_usd_str(),
            estimated_savings_usd=(
                result.estimated_savings.to_usd_str() if result.estimated_savings else None
            ),
        ),
    )

    from fastapi.responses import JSONResponse

    return JSONResponse(content=payload.model_dump(), headers=result.headers())


@data_router.post("/responses")
async def responses(
    request: Request,
    body: S.ChatCompletionRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    """Alias for the chat-completions path, for clients using /v1/responses."""
    return await chat_completions(request, body, principal, container, idempotency_key)


# ---------------------------------------------------------------------------
# Control plane: teams and agents
# ---------------------------------------------------------------------------


@control_router.post("/teams", status_code=201)
async def create_team(
    body: S.CreateTeamRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    principal.require_admin()
    policy = _budget_policy(ScopeType.TEAM, body.team_id, body.budget)
    await container.repository.put_budget_policy(principal.tenant_id, policy)
    return {"team_id": body.team_id, "limit_usd": policy.limit.to_usd_str()}


@control_router.put("/teams/{team_id}/budget")
async def update_team_budget(
    team_id: str,
    body: S.BudgetSpec,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    principal.require_admin()
    policy = _budget_policy(ScopeType.TEAM, team_id, body)
    await container.repository.put_budget_policy(principal.tenant_id, policy)
    return {"team_id": team_id, "limit_usd": policy.limit.to_usd_str()}


@control_router.post("/agents", status_code=201)
async def create_agent(
    body: S.CreateAgentRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    principal.require_admin()
    policy = _agent_policy(principal.tenant_id, body)
    await container.repository.put_agent_policy(policy)

    from ..domain.agent import AgentState, AgentStatus

    await container.repository.put_agent_state(
        AgentState(
            agent_id=body.agent_id,
            tenant_id=principal.tenant_id,
            team_id=body.team_id,
            status=AgentStatus.ACTIVE,
        )
    )
    return {"agent_id": body.agent_id, "limit_usd": policy.budget.limit.to_usd_str()}


@control_router.put("/agents/{agent_id}/budget")
async def update_agent_budget(
    agent_id: str,
    body: S.BudgetSpec,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    principal.require_admin()
    existing = await container.repository.get_agent_policy(principal.tenant_id, agent_id)
    if existing is None:
        raise LookupError(f"unknown agent: {agent_id}")

    from dataclasses import replace

    updated = replace(existing, budget=_budget_policy(ScopeType.AGENT, agent_id, body))
    await container.repository.put_agent_policy(updated)
    return {"agent_id": agent_id, "limit_usd": updated.budget.limit.to_usd_str()}


@control_router.post("/agents/{agent_id}/keys", status_code=201)
async def issue_agent_key(
    agent_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    """Mint an API credential bound to one agent.

    This is how governance identity actually gets established. The returned key
    maps server-side to exactly this tenant/team/agent, so whoever holds it can
    spend that agent's budget and no other -- there is no request field a caller
    could set to change which budget they draw from.

    The secret is returned exactly once and only its hash is retained; there is
    no endpoint that can read it back.
    """
    principal.require_admin()
    policy = await container.repository.get_agent_policy(principal.tenant_id, agent_id)
    if policy is None:
        raise LookupError(f"unknown agent: {agent_id}")

    raw_key = f"abc_{uuid4().hex}{uuid4().hex}"
    record = container.identity.register_raw(
        raw_key,
        tenant_id=principal.tenant_id,
        team_id=policy.team_id,
        agent_id=agent_id,
    )
    return {
        "agent_id": agent_id,
        "key_id": record.key_id,
        # Shown once. Only the hash is stored.
        "api_key": raw_key,
    }


@control_router.put("/agents/{agent_id}/routing-policy")
async def update_routing_policy(
    agent_id: str,
    body: S.RoutingSpec,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    principal.require_admin()
    existing = await container.repository.get_agent_policy(principal.tenant_id, agent_id)
    if existing is None:
        raise LookupError(f"unknown agent: {agent_id}")

    from dataclasses import replace

    updated = replace(existing, routing=_routing_policy(body))
    await container.repository.put_agent_policy(updated)
    return {"agent_id": agent_id, "preferred_model": body.preferred_model}


# ---------------------------------------------------------------------------
# Control plane: sessions
# ---------------------------------------------------------------------------


@control_router.post("/sessions", status_code=201, response_model=S.SessionResponse)
async def create_session(
    body: S.CreateSessionRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.SessionResponse:
    policy = await container.repository.get_agent_policy(principal.tenant_id, principal.agent_id)
    if policy is None:
        raise LookupError(f"no policy configured for agent {principal.agent_id}")
    if policy.session_budget is None:
        raise LookupError(f"agent {principal.agent_id} has no session budget configured")

    now = container.clock.now()
    session = Session(
        session_id=body.session_id or f"ses_{ULID()}",
        tenant_id=principal.tenant_id,
        # Bound to the authenticated agent, so it cannot later be used by
        # another one.
        team_id=principal.team_id,
        agent_id=principal.agent_id,
        status=SessionStatus.OPEN,
        opened_at=now,
        expires_at=now + timedelta(seconds=body.ttl_seconds),
        limit=policy.session_budget,
        min_viable=policy.session_min_viable or Money.zero(),
    )
    await container.repository.put_session(session)
    return _session_response(session, committed=Money.zero())


@control_router.get("/sessions/{session_id}", response_model=S.SessionResponse)
async def get_session(
    session_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.SessionResponse:
    from ..auth.identity import verify_session_ownership

    session = await container.repository.get_session(principal.tenant_id, session_id)
    verify_session_ownership(principal, session)

    state = await container.repository.get_budget_state(
        ScopeRef.session(session_id),
        BudgetWindow.for_instant(
            WindowType.SESSION,
            container.clock.now(),
            session_id=session_id,
            session_start=session.opened_at,
        ),
        tenant_id=principal.tenant_id,
    )
    return _session_response(session, committed=state.committed if state else Money.zero())


@control_router.post("/sessions/{session_id}/close", response_model=S.SessionResponse)
async def close_session(
    session_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.SessionResponse:
    from ..auth.identity import verify_session_ownership

    session = await container.repository.get_session(principal.tenant_id, session_id)
    verify_session_ownership(principal, session)

    await container.repository.try_close_session(
        principal.tenant_id,
        session_id,
        status=SessionStatus.CLOSED_USER,
        reason=SessionCloseReason.USER_REQUESTED,
        closed_by=principal.key_id,
    )
    updated = await container.repository.get_session(principal.tenant_id, session_id)
    return _session_response(updated, committed=Money.zero())


# ---------------------------------------------------------------------------
# Control plane: budgets and ledger
# ---------------------------------------------------------------------------


@control_router.get("/budgets/{scope}/{scope_id}", response_model=S.BudgetStateResponse)
async def get_budget(
    scope: str,
    scope_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
    window: str = Query(default="MONTHLY"),
) -> S.BudgetStateResponse:
    scope_type = ScopeType[scope.upper()] if scope.upper() in ScopeType.__members__ else None
    if scope_type is None:
        raise LookupError(f"unknown scope: {scope}")

    resolved = BudgetWindow.for_instant(WindowType[window.upper()], container.clock.now())
    state = await container.repository.get_budget_state(
        ScopeRef(scope_type, scope_id), resolved, tenant_id=principal.tenant_id
    )
    if state is None:
        raise LookupError(f"no budget state for {scope}/{scope_id} in {resolved}")
    return _budget_response(state)


@control_router.get("/ledger")
async def get_ledger(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
) -> dict[str, Any]:
    # An admin credential has no ledger of its own, so `principal.agent_id`
    # (its bootstrap identity) is meaningless here. Silently substituting it
    # produced a real bug: an admin querying the ledger with no `agent_id`
    # got a confident, empty `{"entries": []}` instead of the tenant's actual
    # history -- indistinguishable from "nothing was spent". Failing loudly
    # is safer than answering a different question than the one that was asked.
    target_agent_id = agent_id if agent_id else (None if principal.is_admin else principal.agent_id)
    if target_agent_id is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail="agent_id query parameter is required for admin credentials",
        )

    entries = await container.repository.query_ledger(
        principal.tenant_id,
        agent_id=target_agent_id,
        limit=limit,
    )
    return {
        "entries": [
            S.LedgerEntryResponse(
                entry_id=e.entry_id,
                request_id=e.reservation_id,
                agent_id=e.agent_id,
                session_id=e.session_id,
                provider=e.provider,
                requested_model=e.requested_model,
                effective_model=e.effective_model,
                decision=e.decision.value,
                kind=e.kind.value,
                preflight_input_tokens=e.preflight_input_tokens,
                reserved_output_tokens=e.reserved_output_tokens,
                actual_input_tokens=e.actual_tokens.input,
                actual_output_tokens=e.actual_tokens.output,
                actual_cached_input_tokens=e.actual_cached_input_tokens,
                actual_reasoning_tokens=e.actual_reasoning_tokens,
                estimated_max_cost_usd=e.estimated_max_cost.to_usd_str(),
                actual_total_cost_usd=e.actual_total_cost.to_usd_str(),
                price_catalog_version=e.price_catalog_version,
                created_at=e.created_at.isoformat(),
                completed_at=e.completed_at.isoformat() if e.completed_at else None,
            ).model_dump()
            for e in entries
        ]
    }


# ---------------------------------------------------------------------------
# Admin: human review
# ---------------------------------------------------------------------------


@admin_router.post("/agents/{agent_id}/pause")
async def pause_agent(
    agent_id: str,
    body: S.AdminActionRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.AuditRecordResponse:
    principal.require_admin()
    record = await container.review.pause(
        principal.tenant_id,
        agent_id,
        actor=body.actor or principal.key_id,
        reason=body.reason,
        now=container.clock.now(),
    )
    return S.AuditRecordResponse(**record.as_dict())


@admin_router.post("/agents/{agent_id}/resume")
async def resume_agent(
    agent_id: str,
    body: S.AdminActionRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.AuditRecordResponse:
    """Return a paused agent to service.

    A reason is mandatory: resuming a runaway without recording why is how the
    same loop ships twice.
    """
    principal.require_admin()
    record = await container.review.resume(
        principal.tenant_id,
        agent_id,
        actor=body.actor or principal.key_id,
        reason=body.reason,
        now=container.clock.now(),
    )
    return S.AuditRecordResponse(**record.as_dict())


@admin_router.get("/agents/{agent_id}/runaway-events")
async def runaway_events(
    agent_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    principal.require_admin()
    events = await container.review.runaway_events(principal.tenant_id, agent_id)
    return {"agent_id": agent_id, "events": [_event_dict(e) for e in events]}


@admin_router.get("/audit")
async def audit_log(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    principal.require_admin()
    return {"events": list(await container.repository.get_audit_events(principal.tenant_id))}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _budget_policy(scope_type: ScopeType, scope_id: str, spec: S.BudgetSpec) -> BudgetPolicy:
    tokens = TokenVector.unlimited()
    if spec.tokens:
        unlimited = TokenVector.unlimited()
        tokens = TokenVector(
            input=spec.tokens.max_input_tokens or unlimited.input,
            output=spec.tokens.max_output_tokens or unlimited.output,
            total=spec.tokens.max_total_tokens or unlimited.total,
        )
    return BudgetPolicy(
        scope_type=scope_type,
        scope_id=scope_id,
        # Parsed from a decimal string: exact, and never a float.
        limit=Money.from_usd_str(spec.amount_usd),
        window_type=WindowType[spec.window],
        tokens=tokens,
        warning_percent=spec.warning_percent,
        billing_tz=spec.billing_tz,
    )


def _routing_policy(spec: S.RoutingSpec) -> RoutingPolicy:
    return RoutingPolicy(
        preferred=ModelCandidate(spec.provider, spec.preferred_model, 32_000),
        fallbacks=tuple(ModelCandidate(spec.provider, m, 32_000) for m in spec.fallback_models),
        allocations=tuple(
            ModelAllocationPolicy(
                provider=a.provider,
                model=a.model,
                limit=Money.from_usd_str(a.amount_usd),
            )
            for a in spec.allocations
        ),
        require_same_provider=spec.require_same_provider,
        allow_fallback=spec.allow_fallback,
    )


def _agent_policy(tenant_id: str, body: S.CreateAgentRequest) -> AgentPolicy:
    return AgentPolicy(
        agent_id=body.agent_id,
        team_id=body.team_id,
        tenant_id=tenant_id,
        budget=_budget_policy(ScopeType.AGENT, body.agent_id, body.budget),
        routing=_routing_policy(body.routing),
        session_budget=(
            Money.from_usd_str(body.session_budget_usd) if body.session_budget_usd else None
        ),
        session_min_viable=(
            Money.from_usd_str(body.session_min_viable_usd) if body.session_min_viable_usd else None
        ),
        default_max_output_tokens=body.default_max_output_tokens,
        runaway=RunawayPolicy(
            monthly_budget_percent=body.runaway.monthly_budget_percent,
            interval_minutes=body.runaway.interval_minutes,
            enabled=body.runaway.enabled,
        ),
    )


def _budget_response(state: BudgetState) -> S.BudgetStateResponse:
    return S.BudgetStateResponse(
        scope_type=state.scope.type.value,
        scope_id=state.scope.id,
        window=state.window.sort_key(),
        limit_usd=state.limit.to_usd_str(),
        committed_usd=state.committed.to_usd_str(),
        reserved_usd=state.reserved.to_usd_str(),
        pending_usd=state.pending.to_usd_str(),
        available_usd=state.available.to_usd_str(),
        overage_usd=state.overage.to_usd_str(),
        utilization_percent=state.utilization_percent,
        effective_utilization_percent=state.effective_utilization_percent,
        warning_sent=state.warning_80_sent,
        open_reservations=state.open_reservations,
        reset_at=state.window.reset_at.isoformat() if state.window.end_epoch else None,
        input_tokens=state.committed_tokens.input,
        output_tokens=state.committed_tokens.output,
    )


def _session_response(session: Session, committed: Money) -> S.SessionResponse:
    return S.SessionResponse(
        session_id=session.session_id,
        agent_id=session.agent_id,
        status=session.status.value,
        limit_usd=session.limit.to_usd_str(),
        committed_usd=committed.to_usd_str(),
        available_usd=(session.limit - committed).to_usd_str(),
        close_reason=session.close_reason.value if session.close_reason else None,
    )


def _event_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    return {
        "event_id": event.event_id,
        "agent_id": event.agent_id,
        "detected_at": event.detected_at.isoformat(),
        "rolling_spend_usd": event.rolling_spend.to_usd_str(),
        "threshold_usd": event.threshold.to_usd_str(),
        "interval_minutes": event.interval_minutes,
    }

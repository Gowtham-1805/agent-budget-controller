"""HTTP routes: control plane and data plane.

The data plane mirrors OpenAI's chat-completions shape on purpose. The goal is
that an existing agent adopts governance by changing its base URL, not by
rewriting its LLM integration -- a controller that requires a rewrite is a
controller nobody turns on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from ulid import ULID

from ..auth.identity import Principal
from ..domain.agent import AgentStatus, RunawayPolicy
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
from ..domain.user import Role
from ..domain.window import BudgetWindow, WindowType
from ..observability.logging import get_logger
from ..providers.base import ChatMessage, ChatRequest
from . import schemas as S
from .deps import Container, get_container, get_principal

logger = get_logger("abc_gateway.routes")

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
    principal.require_agent()
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
    principal.require_role(Role.OPERATOR)
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
    principal.require_role(Role.OPERATOR)
    policy = _budget_policy(ScopeType.TEAM, team_id, body)
    await container.repository.put_budget_policy(principal.tenant_id, policy)
    return {"team_id": team_id, "limit_usd": policy.limit.to_usd_str()}


@control_router.post("/agents", status_code=201)
async def create_agent(
    body: S.CreateAgentRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> dict[str, Any]:
    principal.require_role(Role.OPERATOR)
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
    principal.require_role(Role.OPERATOR)
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
    # Persist through to the repository as well as the in-process cache: an
    # in-memory-only registration is silently lost on the next restart or
    # rolling deploy, which previously meant every minted key had a hidden
    # expiry nobody configured.
    await container.identity.persist(record)
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
    principal.require_role(Role.OPERATOR)
    existing = await container.repository.get_agent_policy(principal.tenant_id, agent_id)
    if existing is None:
        raise LookupError(f"unknown agent: {agent_id}")

    from dataclasses import replace

    updated = replace(existing, routing=_routing_policy(body))
    await container.repository.put_agent_policy(updated)
    return {"agent_id": agent_id, "preferred_model": body.preferred_model}


@control_router.get("/teams", response_model=list[S.TeamSummaryResponse])
async def list_teams(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> list[S.TeamSummaryResponse]:
    principal.require_role(Role.VIEWER)
    team_policies = await container.repository.list_budget_policies(
        principal.tenant_id, ScopeType.TEAM
    )
    agent_policies = await container.repository.list_agent_policies(principal.tenant_id)
    all_sessions = await container.repository.list_sessions(principal.tenant_id, limit=500)

    summaries: list[S.TeamSummaryResponse] = []
    for policy in team_policies:
        window = BudgetWindow.for_instant(policy.window_type, container.clock.now())
        state = await container.repository.get_budget_state(
            ScopeRef.team(policy.scope_id), window, tenant_id=principal.tenant_id
        )
        team_agents = [p for p in agent_policies if p.team_id == policy.scope_id]
        agent_summaries = [
            await _build_agent_summary(principal.tenant_id, ap, container)
            for ap in team_agents
        ]
        active_sessions = len(
            [s for s in all_sessions if s.team_id == policy.scope_id and s.status == SessionStatus.OPEN]
        )

        summaries.append(
            S.TeamSummaryResponse(
                team_id=policy.scope_id,
                limit_usd=policy.limit.to_usd_str(),
                committed_usd=state.committed.to_usd_str() if state else "0.000000",
                reserved_usd=state.reserved.to_usd_str() if state else "0.000000",
                available_usd=state.available.to_usd_str() if state else policy.limit.to_usd_str(),
                utilization_percent=state.utilization_percent if state else 0,
                warning_threshold_percent=policy.warning_percent,
                window_type=policy.window_type.value,
                agent_count=len(team_agents),
                active_sessions=active_sessions,
                agents=agent_summaries,
            )
        )
    return summaries


@control_router.get("/teams/{team_id}", response_model=S.TeamSummaryResponse)
async def get_team(
    team_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.TeamSummaryResponse:
    principal.require_role(Role.VIEWER)
    policy = await container.repository.get_budget_policy(
        principal.tenant_id, ScopeRef.team(team_id)
    )
    if policy is None:
        raise LookupError(f"unknown team: {team_id}")

    agent_policies = await container.repository.list_agent_policies(principal.tenant_id)
    team_agents = [p for p in agent_policies if p.team_id == team_id]
    agent_summaries = [
        await _build_agent_summary(principal.tenant_id, ap, container)
        for ap in team_agents
    ]
    all_sessions = await container.repository.list_sessions(principal.tenant_id, limit=500)
    active_sessions = len(
        [s for s in all_sessions if s.team_id == team_id and s.status == SessionStatus.OPEN]
    )

    window = BudgetWindow.for_instant(policy.window_type, container.clock.now())
    state = await container.repository.get_budget_state(
        ScopeRef.team(team_id), window, tenant_id=principal.tenant_id
    )

    return S.TeamSummaryResponse(
        team_id=team_id,
        limit_usd=policy.limit.to_usd_str(),
        committed_usd=state.committed.to_usd_str() if state else "0.000000",
        reserved_usd=state.reserved.to_usd_str() if state else "0.000000",
        available_usd=state.available.to_usd_str() if state else policy.limit.to_usd_str(),
        utilization_percent=state.utilization_percent if state else 0,
        warning_threshold_percent=policy.warning_percent,
        window_type=policy.window_type.value,
        agent_count=len(team_agents),
        active_sessions=active_sessions,
        agents=agent_summaries,
    )


@control_router.get("/agents", response_model=list[S.AgentSummaryResponse])
async def list_agents(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> list[S.AgentSummaryResponse]:
    principal.require_role(Role.VIEWER)
    agent_policies = await container.repository.list_agent_policies(principal.tenant_id)
    return [
        await _build_agent_summary(principal.tenant_id, p, container)
        for p in agent_policies
    ]


@control_router.get("/agents/{agent_id}", response_model=S.AgentSummaryResponse)
async def get_agent(
    agent_id: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.AgentSummaryResponse:
    principal.require_role(Role.VIEWER)
    policy = await container.repository.get_agent_policy(principal.tenant_id, agent_id)
    if policy is None:
        raise LookupError(f"unknown agent: {agent_id}")
    return await _build_agent_summary(principal.tenant_id, policy, container)


@control_router.get("/events", response_model=list[S.EventItemResponse])
async def list_events(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
    limit: int = Query(default=100, le=500),
) -> list[S.EventItemResponse]:
    principal.require_role(Role.VIEWER)
    alerts = await container.repository.list_alerts(principal.tenant_id, limit=limit)
    audits = await container.repository.get_audit_events(principal.tenant_id)

    items: list[S.EventItemResponse] = []
    for a in alerts:
        severity = "danger" if "100" in a.kind.value or "RUNAWAY" in a.kind.value else "warn"
        scope_parts = a.scope.id.split(":") if ":" in a.scope.id else [a.scope.type.value, a.scope.id]
        team_id = scope_parts[1] if a.scope.type == ScopeType.TEAM else None
        agent_id = scope_parts[1] if a.scope.type == ScopeType.AGENT else None

        items.append(
            S.EventItemResponse(
                event_id=a.event_id,
                kind=a.kind.value,
                severity=severity,
                title=f"{a.kind.value.replace('_', ' ').title()}",
                description=f"Budget event on {a.scope.key()}: spend {a.spent.to_usd_str() if a.spent else 'N/A'} of {a.limit.to_usd_str() if a.limit else 'N/A'}",
                team_id=team_id,
                agent_id=agent_id,
                amount_usd=a.spent.to_usd_str() if a.spent else None,
                threshold_percent=a.threshold_percent,
                occurred_at=a.occurred_at.isoformat(),
                metadata=a.detail,
            )
        )

    for aud in audits:
        items.append(
            S.EventItemResponse(
                event_id=f"audit_{aud.get('timestamp', '')}_{aud.get('action', '')}",
                kind="AUDIT_ACTION",
                severity="info",
                title=f"Admin {aud.get('action', '').replace('.', ' ').title()}",
                description=f"Action on {aud.get('target', '')}: {aud.get('reason', '')}",
                agent_id=aud.get("target") if aud.get("target") else None,
                occurred_at=aud.get("timestamp", datetime.now(UTC).isoformat()),
                actor=aud.get("actor"),
                metadata=aud,
            )
        )

    items.sort(key=lambda i: i.occurred_at, reverse=True)
    return items[:limit]


@control_router.post("/playground/chat", response_model=S.PlaygroundRunResponse)
async def playground_chat(
    body: S.PlaygroundRunRequest,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.PlaygroundRunResponse:
    """Execute a test chat request with real-time step-by-step governance tracing."""
    principal.require_role(Role.OPERATOR)
    policy = await container.repository.get_agent_policy(principal.tenant_id, body.agent_id)
    if policy is None:
        raise LookupError(f"unknown agent: {body.agent_id}")

    agent_state = await container.repository.get_agent_state(principal.tenant_id, body.agent_id)
    request_id = f"play_{uuid4().hex[:12]}"
    steps: list[S.PlaygroundLifecycleStep] = []

    # Step 1: Authentication
    steps.append(
        S.PlaygroundLifecycleStep(
            step_number=1,
            name="Authentication & Identity",
            description="Verified operator admin principal and tenant binding.",
            status="completed",
            details={"tenant_id": principal.tenant_id, "actor": principal.key_id},
        )
    )

    # Step 2: Policy & Routing Resolution
    steps.append(
        S.PlaygroundLifecycleStep(
            step_number=2,
            name="Policy & Routing Resolution",
            description=f"Resolved agent policy for '{body.agent_id}'. Preferred: {policy.routing.preferred.model}.",
            status="completed",
            details={
                "team_id": policy.team_id,
                "preferred_model": policy.routing.preferred.model,
                "fallbacks": [f.model for f in policy.routing.fallbacks],
                "monthly_limit_usd": policy.budget.limit.to_usd_str(),
            },
        )
    )

    # Check paused state
    if agent_state and agent_state.status in (AgentStatus.PAUSED_ADMIN, AgentStatus.PAUSED_RUNAWAY):
        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=3,
                name="Status Check",
                description=f"Agent is paused: {agent_state.status.value}. Reason: {agent_state.pause_reason or 'No reason provided'}.",
                status="blocked",
                details={"status": agent_state.status.value, "pause_reason": agent_state.pause_reason},
            )
        )
        return S.PlaygroundRunResponse(
            request_id=request_id,
            agent_id=body.agent_id,
            decision="AGENT_PAUSED",
            status="423 Locked",
            requested_model=policy.routing.preferred.model,
            effective_model=policy.routing.preferred.model,
            substituted=False,
            preflight_input_tokens=0,
            actual_input_tokens=0,
            reserved_output_tokens=0,
            actual_output_tokens=0,
            total_tokens=0,
            estimated_cost_usd="0.000000",
            actual_cost_usd="0.000000",
            response_text=f"Request blocked: Agent '{body.agent_id}' is currently {agent_state.status.value} ({agent_state.pause_reason}).",
            lifecycle_steps=steps,
            blocked=True,
            block_reason=f"AGENT_PAUSED: {agent_state.pause_reason}",
            provider_calls_made=0,
        )

    # Step 3: Input Token Counting / Estimation
    prompt_tokens = max(1, len(body.prompt.split()) * 2)
    steps.append(
        S.PlaygroundLifecycleStep(
            step_number=3,
            name="Preflight Input-Token Counting",
            description=f"Metered prompt preflight: {prompt_tokens} input tokens.",
            status="completed",
            details={"preflight_input_tokens": prompt_tokens},
        )
    )

    # Step 4: Output Bounding
    output_ceiling = body.max_output_tokens or policy.default_max_output_tokens
    steps.append(
        S.PlaygroundLifecycleStep(
            step_number=4,
            name="Output-Token Bounding",
            description=f"Applied hard output ceiling of {output_ceiling} tokens.",
            status="completed",
            details={"max_output_tokens": output_ceiling},
        )
    )

    # Step 5: Cost Estimation & Step 6: Atomic Reservation
    agent_principal = Principal(
        key_id=f"play_{body.agent_id}",
        tenant_id=principal.tenant_id,
        team_id=policy.team_id,
        agent_id=body.agent_id,
        is_admin=False,
    )

    chat = ChatRequest(
        messages=(ChatMessage(role="user", content=body.prompt),),
        model=body.model or "",
        max_output_tokens=output_ceiling,
        temperature=0.7,
    )

    from ..domain.errors import AuthorizationDenied, DenialCode
    from .service import ProviderRejected, ProviderUnresolved

    try:
        result = await container.service.invoke(
            principal=agent_principal,
            chat=chat,
            session_id=body.session_id,
            request_id=request_id,
        )

        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=5,
                name="Worst-Case Cost Estimation",
                description=f"Estimated max exposure: ${result.estimated_cost.to_usd_str()}.",
                status="completed",
                details={"estimated_cost_usd": result.estimated_cost.to_usd_str()},
            )
        )

        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=6,
                name="Atomic Budget Reservation",
                description=f"Atomically locked ${result.estimated_cost.to_usd_str()} across Agent, Team, and Session budgets.",
                status="completed",
                details={"reserved_usd": result.estimated_cost.to_usd_str()},
            )
        )

        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=7,
                name="Model Selection & Routing",
                description=(
                    f"Selected model '{result.effective_model}' "
                    + (f"(Substituted from '{result.requested_model}' due to budget pressure)" if result.substituted else "(Preferred model used)")
                ),
                status="completed",
                details={
                    "requested_model": result.requested_model,
                    "effective_model": result.effective_model,
                    "substituted": result.substituted,
                },
            )
        )

        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=8,
                name="Provider Inference",
                description=f"Inference returned {result.output_tokens} completion tokens.",
                status="completed",
                details={"output_tokens": result.output_tokens},
            )
        )

        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=9,
                name="Token Metering & Actual Cost Calculation",
                description=f"Calculated actual total cost: ${result.actual_cost.to_usd_str()} ({result.input_tokens} in / {result.output_tokens} out).",
                status="completed",
                details={
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "actual_cost_usd": result.actual_cost.to_usd_str(),
                },
            )
        )

        diff = result.estimated_cost - result.actual_cost
        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=10,
                name="Reconciliation & Ledger Recording",
                description=f"Reconciled reservation: returned ${diff.to_usd_str()} unused hold back to available balance.",
                status="completed",
                details={
                    "credited_back_usd": diff.to_usd_str(),
                    "actual_total_cost_usd": result.actual_cost.to_usd_str(),
                },
            )
        )

        return S.PlaygroundRunResponse(
            request_id=request_id,
            agent_id=body.agent_id,
            decision=result.decision.value,
            status="200 OK",
            requested_model=result.requested_model,
            effective_model=result.effective_model,
            substituted=result.substituted,
            routing_reason="BUDGET_PRESSURE" if result.substituted else None,
            preflight_input_tokens=prompt_tokens,
            actual_input_tokens=result.input_tokens,
            reserved_output_tokens=output_ceiling,
            actual_output_tokens=result.output_tokens,
            total_tokens=result.input_tokens + result.output_tokens,
            estimated_cost_usd=result.estimated_cost.to_usd_str(),
            actual_cost_usd=result.actual_cost.to_usd_str(),
            estimated_savings_usd=result.estimated_savings.to_usd_str() if result.estimated_savings else None,
            response_text=result.content,
            lifecycle_steps=steps,
            blocked=False,
            provider_calls_made=1,
        )

    except AuthorizationDenied as exc:
        denial = exc.denial
        is_paused = denial.code == DenialCode.AGENT_PAUSED
        status_code = "423 Locked" if is_paused else "429 Too Many Requests"
        step_title = "Agent State Verification" if is_paused else "Atomic Budget Reservation"

        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=6,
                name=step_title,
                description=f"AUTHORIZATION DENIED: {denial.code.value} on {denial.blocking_scope.key() if denial.blocking_scope else 'policy'}. Provider was NEVER contacted.",
                status="blocked",
                details={
                    "code": denial.code.value,
                    "scope": denial.blocking_scope.key() if denial.blocking_scope else "N/A",
                    "available_usd": denial.available.to_usd_str() if denial.available else "0.000000",
                    "requested_usd": denial.requested.to_usd_str() if denial.requested else "0.000000",
                },
            )
        )
        for i in range(7, 11):
            steps.append(
                S.PlaygroundLifecycleStep(
                    step_number=i,
                    name=f"Stage {i}",
                    description="Skipped due to pre-inference financial authorization denial.",
                    status="pending",
                )
            )

        return S.PlaygroundRunResponse(
            request_id=request_id,
            agent_id=body.agent_id,
            decision=denial.code.value.upper(),
            status=status_code,
            requested_model=policy.routing.preferred.model,
            effective_model=policy.routing.preferred.model,
            substituted=False,
            preflight_input_tokens=prompt_tokens,
            actual_input_tokens=0,
            reserved_output_tokens=output_ceiling,
            actual_output_tokens=0,
            total_tokens=0,
            estimated_cost_usd="0.000000",
            actual_cost_usd="0.000000",
            response_text=f"REQUEST BLOCKED ({status_code} {denial.code.value.upper()}): {denial.detail or denial.code.value}\nZero provider spend was incurred.",
            lifecycle_steps=steps,
            blocked=True,
            block_reason=f"{denial.code.value}: {denial.detail or denial.blocking_scope}",
            provider_calls_made=0,
        )
    except ProviderRejected as exc:
        # FailedNotBilled: providers/classify.py's explicit allow-list proved the
        # provider did not charge us, so the engine already released the hold.
        err_msg = exc.error
        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=8,
                name="Provider Inference",
                description=f"PROVIDER REJECTED (proven not billed): {err_msg}",
                status="blocked",
                details={"error": err_msg},
            )
        )
        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=10,
                name="Reconciliation & Ledger Recording",
                description="Reservation released — the provider proved it did not bill this request.",
                status="completed",
            )
        )

        return S.PlaygroundRunResponse(
            request_id=request_id,
            agent_id=body.agent_id,
            decision="PROVIDER_REJECTED",
            status="502 Bad Gateway",
            requested_model=policy.routing.preferred.model,
            effective_model=policy.routing.preferred.model,
            substituted=False,
            preflight_input_tokens=prompt_tokens,
            actual_input_tokens=0,
            reserved_output_tokens=output_ceiling,
            actual_output_tokens=0,
            total_tokens=0,
            estimated_cost_usd="0.000000",
            actual_cost_usd="0.000000",
            response_text=(
                f"PROVIDER REJECTED: {err_msg}\n\n"
                "Zero spend was billed — the reservation was released.\n\n"
                "Tip: If you are using OpenAI/Anthropic/Bedrock, make sure a valid API key is set in 'Providers' settings. "
                "For local testing without live API keys, select an agent routed to the 'test' provider."
            ),
            lifecycle_steps=steps,
            blocked=True,
            block_reason=f"Provider rejected (not billed): {err_msg}",
            provider_calls_made=1,
        )

    except ProviderUnresolved as exc:
        # FailedAmbiguous (rule 3): the outcome is unknown -- a read timeout does
        # not prove the provider didn't bill us. The engine already marked the
        # reservation RECONCILE_PENDING and deliberately did NOT release it, so
        # this response must not claim the money was returned or that spend was
        # zero -- either claim would misrepresent held funds as available.
        err_msg = exc.error
        reservation = await container.repository.get_reservation(
            principal.tenant_id, exc.reservation_id
        )
        held_usd = reservation.reserved_cost.to_usd_str() if reservation else "unknown"
        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=8,
                name="Provider Inference",
                description=f"PROVIDER OUTCOME UNKNOWN: {err_msg}",
                status="blocked",
                details={"error": err_msg, "reservation_id": exc.reservation_id},
            )
        )
        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=10,
                name="Reconciliation & Ledger Recording",
                description=(
                    f"Outcome ambiguous (e.g. a timeout) -- ${held_usd} remains HELD as "
                    "RECONCILE_PENDING, not released, because the provider's outcome cannot "
                    "be proven. It requires manual reconciliation."
                ),
                status="pending",
            )
        )

        return S.PlaygroundRunResponse(
            request_id=request_id,
            agent_id=body.agent_id,
            decision="PROVIDER_AMBIGUOUS",
            status="202 Accepted (Pending Reconciliation)",
            requested_model=policy.routing.preferred.model,
            effective_model=policy.routing.preferred.model,
            substituted=False,
            preflight_input_tokens=prompt_tokens,
            actual_input_tokens=0,
            reserved_output_tokens=output_ceiling,
            actual_output_tokens=0,
            total_tokens=0,
            estimated_cost_usd=held_usd,
            actual_cost_usd="pending",
            response_text=(
                f"PROVIDER OUTCOME UNKNOWN: {err_msg}\n\n"
                f"${held_usd} remains held as RECONCILE_PENDING because the provider's outcome "
                "could not be confirmed -- this request may still be billed. It has NOT been "
                "released and requires manual reconciliation."
            ),
            lifecycle_steps=steps,
            blocked=True,
            block_reason=f"Provider outcome ambiguous, reservation held pending: {err_msg}",
            provider_calls_made=1,
        )

    except Exception:
        # Anything else (e.g. a missing provider/policy LookupError) happens
        # before or outside the reserve/invoke/reconcile lifecycle -- log the
        # real detail server-side, but never hand a raw exception string to the
        # browser: it can leak internal library/stack detail.
        logger.exception(
            "playground.unexpected_error",
            request_id=request_id,
            agent_id=body.agent_id,
            tenant_id=principal.tenant_id,
        )
        steps.append(
            S.PlaygroundLifecycleStep(
                step_number=8,
                name="Provider Inference",
                description="UNEXPECTED ERROR before dispatch. See server logs for detail.",
                status="blocked",
                details={"request_id": request_id},
            )
        )

        return S.PlaygroundRunResponse(
            request_id=request_id,
            agent_id=body.agent_id,
            decision="INTERNAL_ERROR",
            status="500 Internal Server Error",
            requested_model=policy.routing.preferred.model,
            effective_model=policy.routing.preferred.model,
            substituted=False,
            preflight_input_tokens=prompt_tokens,
            actual_input_tokens=0,
            reserved_output_tokens=output_ceiling,
            actual_output_tokens=0,
            total_tokens=0,
            estimated_cost_usd="0.000000",
            actual_cost_usd="0.000000",
            response_text=(
                "An unexpected error occurred before the request could be dispatched. "
                f"No provider was contacted. Reference request_id={request_id} in the server logs."
            ),
            lifecycle_steps=steps,
            blocked=True,
            block_reason=f"Internal error (request_id={request_id})",
            provider_calls_made=0,
        )


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


@control_router.get("/sessions", response_model=list[S.SessionResponse])
async def list_sessions(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
) -> list[S.SessionResponse]:
    principal.require_role(Role.VIEWER)
    sessions = await container.repository.list_sessions(
        principal.tenant_id, agent_id=agent_id, limit=limit
    )
    responses = []
    for s in sessions:
        state = await container.repository.get_budget_state(
            ScopeRef.session(s.session_id),
            BudgetWindow.for_instant(
                WindowType.SESSION,
                container.clock.now(),
                session_id=s.session_id,
                session_start=s.opened_at,
            ),
            tenant_id=principal.tenant_id,
        )
        responses.append(_session_response(s, committed=state.committed if state else Money.zero()))
    return responses


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
    principal.require_role(Role.VIEWER)
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
# Admin: Provider configuration and catalog
# ---------------------------------------------------------------------------


@admin_router.get("/providers", response_model=list[S.ProviderConfigResponse])
async def list_providers(
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> list[S.ProviderConfigResponse]:
    """List all supported providers with masked metadata and status."""
    principal.require_role(Role.VIEWER)
    if not container.registry:
        return []
    return [_provider_response(cfg, container.catalog) for cfg in container.registry.list_providers()]


@admin_router.get("/providers/{provider}", response_model=S.ProviderConfigResponse)
async def get_provider_config(
    provider: str,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.ProviderConfigResponse:
    """Get configuration metadata for a single provider. Never returns raw secrets."""
    principal.require_role(Role.VIEWER)
    if not container.registry:
        raise LookupError("provider registry is not initialized")
    cfg = container.registry.get_provider(provider)
    if not cfg:
        raise LookupError(f"unknown provider: {provider}")
    return _provider_response(cfg, container.catalog)


@admin_router.put("/providers/{provider}", response_model=S.ProviderConfigResponse)
async def update_provider_config(
    provider: str,
    body: S.ProviderUpdateRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.ProviderConfigResponse:
    """Create or update provider configuration. Secrets are stored securely."""
    principal.require_admin()
    if not container.registry:
        raise LookupError("provider registry is not initialized")

    prev = container.registry.get_provider(provider)
    prev_configured = prev.configured if prev else False

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        updated = container.registry.update_provider(provider, updates)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=str(exc)) from exc

    action = "provider.configured" if not prev_configured and updated.configured else "provider.updated"
    await container.repository.record_audit_event({
        "tenant_id": principal.tenant_id,
        "actor": principal.key_id or "admin",
        "action": action,
        "target": provider,
        "previous_state": "configured" if prev_configured else "unconfigured",
        "new_state": "configured" if updated.configured else "unconfigured",
        "reason": f"Provider {provider} updated (enabled={updated.enabled}, model={updated.default_model})",
        "timestamp": container.clock.now().isoformat(),
        "request_id": getattr(request.state, "request_id", None),
    })

    return _provider_response(updated, container.catalog)


@admin_router.post("/providers/{provider}/enable", response_model=S.ProviderConfigResponse)
async def enable_provider(
    provider: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.ProviderConfigResponse:
    """Enable a configured provider for inference routing."""
    principal.require_admin()
    if not container.registry:
        raise LookupError("provider registry is not initialized")

    try:
        updated = container.registry.enable_provider(provider)
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await container.repository.record_audit_event({
        "tenant_id": principal.tenant_id,
        "actor": principal.key_id or "admin",
        "action": "provider.enabled",
        "target": provider,
        "previous_state": "disabled",
        "new_state": "enabled",
        "reason": f"Provider {provider} enabled by administrator",
        "timestamp": container.clock.now().isoformat(),
        "request_id": getattr(request.state, "request_id", None),
    })

    return _provider_response(updated, container.catalog)


@admin_router.post("/providers/{provider}/disable", response_model=S.ProviderConfigResponse)
async def disable_provider(
    provider: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.ProviderConfigResponse:
    """Disable a provider so it cannot receive inference traffic."""
    principal.require_admin()
    if not container.registry:
        raise LookupError("provider registry is not initialized")

    updated = container.registry.disable_provider(provider)

    await container.repository.record_audit_event({
        "tenant_id": principal.tenant_id,
        "actor": principal.key_id or "admin",
        "action": "provider.disabled",
        "target": provider,
        "previous_state": "enabled",
        "new_state": "disabled",
        "reason": f"Provider {provider} disabled by administrator",
        "timestamp": container.clock.now().isoformat(),
        "request_id": getattr(request.state, "request_id", None),
    })

    return _provider_response(updated, container.catalog)


@admin_router.post("/providers/{provider}/test", response_model=S.ProviderTestResponse)
async def test_provider_connection(
    provider: str,
    request: Request,
    body: S.ProviderTestRequest | None = None,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> S.ProviderTestResponse:
    """Test connection and credentials for a provider without billable calls."""
    principal.require_admin()
    if not container.registry:
        raise LookupError("provider registry is not initialized")

    model = body.model if body else None
    result = await container.registry.test_connection(provider, model)

    await container.repository.record_audit_event({
        "tenant_id": principal.tenant_id,
        "actor": principal.key_id or "admin",
        "action": "provider.connection_tested",
        "target": provider,
        "previous_state": "untested",
        "new_state": result.status.value,
        "reason": f"Test connection result: {result.status.value} ({result.message})",
        "timestamp": container.clock.now().isoformat(),
        "request_id": getattr(request.state, "request_id", None),
    })

    return S.ProviderTestResponse(
        provider=result.provider,
        status=result.status.value,
        model=result.model,
        authentication=result.authentication,
        checked_at=result.checked_at.isoformat(),
        message=result.message,
        error_type=result.error_type,
    )


@admin_router.get("/catalog/models", response_model=list[S.CatalogModelResponse])
async def list_catalog_models(
    provider: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> list[S.CatalogModelResponse]:
    """List all models registered in the price catalog with pricing and capabilities."""
    principal.require_role(Role.VIEWER)
    if not container.registry:
        return []
    models = container.registry.get_catalog_models(provider)
    return [S.CatalogModelResponse(**m) for m in models]


def _provider_response(cfg: Any, catalog: Any) -> S.ProviderConfigResponse:
    models = [entry.model for entry in catalog.models_for(cfg.provider)]
    return S.ProviderConfigResponse(
        provider=cfg.provider,
        display_name=cfg.display_name,
        enabled=cfg.enabled,
        configured=cfg.configured,
        default_model=cfg.default_model,
        auth_type=cfg.auth_type.value,
        masked_api_key=cfg.masked_api_key,
        region=cfg.region,
        base_url=cfg.base_url,
        organization_id=cfg.organization_id,
        test_params=dict(cfg.test_params),
        connection_status=cfg.connection_status.value,
        last_tested_at=cfg.last_tested_at.isoformat() if cfg.last_tested_at else None,
        last_error=cfg.last_error,
        is_production_ready=cfg.is_production_ready,
        available_models=sorted(models),
    )


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


async def _build_agent_summary(
    tenant_id: str,
    policy: AgentPolicy,
    container: Container,
) -> S.AgentSummaryResponse:
    state = await container.repository.get_agent_state(tenant_id, policy.agent_id)
    budget_window = BudgetWindow.for_instant(policy.budget.window_type, container.clock.now())
    budget_state = await container.repository.get_budget_state(
        ScopeRef.agent(policy.agent_id),
        budget_window,
        tenant_id=tenant_id,
    )
    limit_usd = policy.budget.limit.to_usd_str()
    committed_usd = budget_state.committed.to_usd_str() if budget_state else "0.000000"
    reserved_usd = budget_state.reserved.to_usd_str() if budget_state else "0.000000"
    available_usd = budget_state.available.to_usd_str() if budget_state else limit_usd
    utilization_percent = budget_state.utilization_percent if budget_state else 0
    effective_utilization_percent = budget_state.effective_utilization_percent if budget_state else 0
    warning_sent = budget_state.warning_80_sent if budget_state else False
    input_tokens = budget_state.committed_tokens.input if budget_state else 0
    output_tokens = budget_state.committed_tokens.output if budget_state else 0

    return S.AgentSummaryResponse(
        agent_id=policy.agent_id,
        team_id=policy.team_id,
        status=state.status.value if state else "ACTIVE",
        limit_usd=limit_usd,
        committed_usd=committed_usd,
        reserved_usd=reserved_usd,
        available_usd=available_usd,
        utilization_percent=utilization_percent,
        effective_utilization_percent=effective_utilization_percent,
        warning_sent=warning_sent,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        preferred_model=policy.routing.preferred.model,
        fallback_models=[f.model for f in policy.routing.fallbacks],
        substitution_enabled=policy.routing.allow_fallback,
        substitution_threshold_percent=90,
        session_budget_usd=policy.session_budget.to_usd_str() if policy.session_budget else None,
        pause_reason=state.pause_reason if state else None,
        review_required=state.review_required if state else False,
        window_type=policy.budget.window_type.value,
        default_max_output_tokens=policy.default_max_output_tokens,
    )


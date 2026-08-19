"""Request and response contracts.

Money crosses the API as a decimal *string*, never a JSON number. JSON numbers
are IEEE-754 doubles in most clients, so "50.00" survives a round trip intact
while 50.00 may not -- and a budget limit that drifts in transit is worse than
useless.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible request shape.

    Deliberately mirrors the provider's own schema so an existing agent can
    switch to the gateway by changing its base URL rather than rewriting its
    integration.
    """

    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[Message]
    #: The client's preferred ceiling. The gateway lowers it to whatever policy
    #: allows and sends *that* to the provider -- omitting it does not mean
    #: "unbounded", it means "use the policy cap".
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    temperature: float | None = None
    tools: list[dict[str, Any]] | None = None
    session_id: str | None = None

    def output_ceiling(self) -> int | None:
        return self.max_completion_tokens or self.max_tokens


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class BudgetMetadata(BaseModel):
    """Governance decision, returned in the body as well as in headers."""

    decision: str
    requested_model: str
    effective_model: str
    substituted: bool
    estimated_cost_usd: str
    actual_cost_usd: str
    estimated_savings_usd: str | None = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    model: str
    choices: list[dict[str, Any]]
    usage: Usage
    budget: BudgetMetadata


# ---------------------------------------------------------------------------
# Control plane
# ---------------------------------------------------------------------------


class TokenBudget(BaseModel):
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_total_tokens: int | None = None


class BudgetSpec(BaseModel):
    #: A decimal string, e.g. "500.00". Never a float.
    amount_usd: str = Field(examples=["500.00"])
    window: Literal["DAILY", "WEEKLY", "MONTHLY"] = "MONTHLY"
    warning_percent: int = 80
    billing_tz: str = "UTC"
    tokens: TokenBudget | None = None


class CreateTeamRequest(BaseModel):
    team_id: str
    budget: BudgetSpec


class ModelAllocationSpec(BaseModel):
    """A sub-budget of the agent, earmarked for one model.

    This is what makes "fall back to a cheaper model when the preferred one is
    exhausted" coherent: exhausting the allocation leaves genuine agent capacity
    for the fallback, whereas exhausting the agent budget leaves none.
    """

    provider: str
    model: str
    amount_usd: str


class RoutingSpec(BaseModel):
    provider: str
    preferred_model: str
    fallback_models: list[str] = Field(default_factory=list)
    allocations: list[ModelAllocationSpec] = Field(default_factory=list)
    require_same_provider: bool = True
    allow_fallback: bool = True


class RunawaySpec(BaseModel):
    monthly_budget_percent: int = 20
    interval_minutes: int = 60
    enabled: bool = True


class CreateAgentRequest(BaseModel):
    agent_id: str
    team_id: str
    budget: BudgetSpec
    routing: RoutingSpec
    session_budget_usd: str | None = None
    session_min_viable_usd: str | None = None
    default_max_output_tokens: int = 4096
    runaway: RunawaySpec = Field(default_factory=RunawaySpec)


class CreateSessionRequest(BaseModel):
    session_id: str | None = None
    ttl_seconds: int = 86_400


class SessionResponse(BaseModel):
    session_id: str
    agent_id: str
    status: str
    limit_usd: str
    committed_usd: str
    available_usd: str
    close_reason: str | None = None


class BudgetStateResponse(BaseModel):
    """Counters, with committed and reserved kept distinct.

    The distinction is what makes concurrency visible: a scope can be at 60%
    settled and 95% effective, meaning nearly all the remaining budget is
    already promised to requests currently in flight.
    """

    scope_type: str
    scope_id: str
    window: str
    limit_usd: str
    committed_usd: str
    reserved_usd: str
    pending_usd: str
    available_usd: str
    overage_usd: str
    utilization_percent: int
    effective_utilization_percent: int
    warning_sent: bool
    open_reservations: int
    reset_at: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class LedgerEntryResponse(BaseModel):
    entry_id: str
    request_id: str
    agent_id: str
    session_id: str | None
    provider: str
    requested_model: str
    effective_model: str
    decision: str
    kind: str
    preflight_input_tokens: int
    reserved_output_tokens: int
    actual_input_tokens: int
    actual_output_tokens: int
    actual_cached_input_tokens: int
    actual_reasoning_tokens: int
    estimated_max_cost_usd: str
    actual_total_cost_usd: str
    price_catalog_version: str
    created_at: str
    completed_at: str | None


class AdminActionRequest(BaseModel):
    #: Required, not optional. Resuming a runaway agent without recording why is
    #: how the same loop ships twice.
    reason: str = Field(min_length=1)
    actor: str | None = None


class AuditRecordResponse(BaseModel):
    actor: str
    action: str
    target: str
    previous_state: str
    new_state: str
    reason: str
    timestamp: str


class ErrorBody(BaseModel):
    type: str
    message: str
    scope: str | None = None
    scope_id: str | None = None
    limit_usd: str | None = None
    committed_usd: str | None = None
    reserved_usd: str | None = None
    available_usd: str | None = None
    requested_usd: str | None = None
    token_dimensions: list[str] | None = None
    reset_at: str | None = None
    request_id: str | None = None
    session_closed: bool | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, bool]
    detail: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Admin: Provider configuration and catalog
# ---------------------------------------------------------------------------


class ProviderConfigResponse(BaseModel):
    provider: str
    display_name: str
    enabled: bool
    configured: bool
    default_model: str
    auth_type: str
    masked_api_key: str | None = None
    region: str | None = None
    base_url: str | None = None
    organization_id: str | None = None
    test_params: dict[str, Any] = Field(default_factory=dict)
    connection_status: str
    last_tested_at: str | None = None
    last_error: str | None = None
    is_production_ready: bool = True
    available_models: list[str] = Field(default_factory=list)


class ProviderUpdateRequest(BaseModel):
    enabled: bool | None = None
    default_model: str | None = None
    api_key: str | None = None
    region: str | None = None
    base_url: str | None = None
    organization_id: str | None = None
    test_params: dict[str, Any] | None = None


class ProviderTestRequest(BaseModel):
    model: str | None = None


class ProviderTestResponse(BaseModel):
    provider: str
    status: str
    model: str
    authentication: str
    checked_at: str
    message: str
    error_type: str | None = None


class CatalogModelResponse(BaseModel):
    provider: str
    model: str
    status: str
    input_per_million: str
    output_per_million: str
    cached_input_per_million: str
    max_context_tokens: int
    max_output_tokens: int
    supports_tools: bool
    supports_structured_output: bool
    supports_vision: bool
    supports_reasoning: bool
    preflight_token_counting: bool
    catalog_version: str


# ---------------------------------------------------------------------------
# Control Plane: Teams, Agents, Sessions, Events, Playground
# ---------------------------------------------------------------------------


class AgentSummaryResponse(BaseModel):
    agent_id: str
    team_id: str
    status: str
    limit_usd: str
    committed_usd: str
    reserved_usd: str
    available_usd: str = "0.000000"
    utilization_percent: int = 0
    effective_utilization_percent: int = 0
    warning_sent: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    preferred_model: str = ""
    fallback_models: list[str] = Field(default_factory=list)
    substitution_enabled: bool = True
    substitution_threshold_percent: int = 90
    session_budget_usd: str | None = None
    pause_reason: str | None = None
    review_required: bool = False
    window_type: str = "MONTHLY"
    default_max_output_tokens: int = 4096


class TeamSummaryResponse(BaseModel):
    team_id: str
    limit_usd: str
    committed_usd: str
    reserved_usd: str
    available_usd: str
    utilization_percent: int = 0
    warning_threshold_percent: int = 80
    window_type: str = "MONTHLY"
    agent_count: int = 0
    active_sessions: int = 0
    agents: list[AgentSummaryResponse] = Field(default_factory=list)


class EventItemResponse(BaseModel):
    event_id: str
    kind: str
    severity: str
    title: str
    description: str
    team_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    amount_usd: str | None = None
    threshold_percent: int | None = None
    occurred_at: str
    actor: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlaygroundLifecycleStep(BaseModel):
    step_number: int
    name: str
    description: str
    status: str  #: "completed", "running", "pending", "blocked"
    details: dict[str, Any] = Field(default_factory=dict)


class PlaygroundRunRequest(BaseModel):
    agent_id: str
    prompt: str = Field(min_length=1)
    session_id: str | None = None
    model: str | None = None
    max_output_tokens: int = 500


class PlaygroundRunResponse(BaseModel):
    request_id: str
    agent_id: str
    decision: str
    status: str
    requested_model: str
    effective_model: str
    substituted: bool
    routing_reason: str | None = None
    preflight_input_tokens: int
    actual_input_tokens: int
    reserved_output_tokens: int
    actual_output_tokens: int
    total_tokens: int
    estimated_cost_usd: str
    actual_cost_usd: str
    estimated_savings_usd: str | None = None
    response_text: str
    lifecycle_steps: list[PlaygroundLifecycleStep] = Field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None
    provider_calls_made: int = 1


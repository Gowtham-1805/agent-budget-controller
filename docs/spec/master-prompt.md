# Agent Budget Controller — Claude Code Master Implementation Prompt

## Purpose

You are the **lead staff engineer and implementation agent** responsible for building a production-ready **Agent Budget Controller**.

Your job is not to produce a design document, slide deck, demo script, or prototype. Your job is to **implement a working production-oriented system in the repository**, deploy it to AWS where credentials are available, integrate at least one real LLM provider, and prove the required success criteria with automated tests and end-to-end validation.

Work autonomously.

Inspect the current repository first, preserve useful existing work, and then implement the missing components.

Do not stop after scaffolding.

Do not replace hard implementation problems with TODO comments.

Do not create fake implementations for core financial authorization logic.

Mocks are allowed for local development, unit tests, concurrency tests, and failure-injection tests, but **mocks alone do not satisfy final production acceptance**.

If credentials or external services are unavailable, still implement the full integration surface and clearly report which production acceptance checks remain unverified. Do not claim success for anything that was not actually tested.

---

# 1. Challenge Being Implemented

## Context

An enterprise gives its engineering team an LLM budget. The team has multiple agents running across multiple products. One agent can enter a recursive loop and make tens of thousands of API calls before anyone notices.

Post-hoc billing dashboards do not solve this problem.

The system must enforce AI spend limits **before requests reach the provider**.

## Challenge

Build an **agent-level budget controller** that enforces token and monetary spend limits per agent, per team, per session, and per time window, rejecting or rerouting requests before the applicable budget is exceeded.

## Required Capabilities

The implementation must include:

- team budgets
- agent budgets
- session budgets
- monetary budgets
- token budgets
- time-window budgets
- token preflight metering
- estimated-cost metering
- actual provider-usage reconciliation
- concurrent request handling
- hard budget enforcement
- 80% budget warnings
- session automatic closure
- realtime dashboard
- model substitution on budget pressure
- persistent state
- usable API
- logging and error handling
- health checks
- real LLM provider integration
- AWS deployment
- runaway-agent detector
- human pause/resume review workflow
- production-quality infrastructure as code
- comprehensive automated testing

---

# 2. Core Financial Invariant

This is the most important invariant in the entire system:

```text
For every governed budget scope S:

committed_S + reserved_S <= limit_S
```

This must remain true under concurrent traffic.

A request MUST NOT reach the LLM provider until its worst-case authorized exposure has successfully been reserved from every applicable scope.

The architecture must follow:

```text
REQUEST
   ↓
Authenticate identity
   ↓
Resolve team / agent / session / budget window
   ↓
Validate agent/session status
   ↓
Preflight input tokens
   ↓
Bound maximum output tokens
   ↓
Calculate estimated worst-case request exposure
   ↓
ATOMICALLY RESERVE BUDGET
   ↓
   ├── reservation denied → NO PROVIDER CALL
   │
   └── reservation accepted
            ↓
        invoke provider
            ↓
       provider usage
            ↓
     calculate actual cost
            ↓
     reconcile reservation
            ↓
       immutable ledger
            ↓
       threshold events
            ↓
       realtime updates
```

Never implement:

```text
call provider
then
check/update budget
```

Always implement:

```text
count
→ bound
→ estimate
→ reserve
→ invoke
→ reconcile
```

---

# 3. Product Positioning

This project is not merely an LLM cost dashboard.

It is a:

> **financial authorization gateway for AI inference**

The custom gateway and transactional budget store are the financial source of truth.

Observability systems such as Langfuse are secondary and asynchronous.

The strongest technical guarantee is:

```text
A request cannot reach the provider unless its worst-case authorized money
AND token exposure fits atomically inside every applicable budget.
```

---

# 4. Required Technology Stack

Use this stack unless the existing repository already contains a clearly superior compatible implementation.

## Backend

- Python 3.12+
- FastAPI
- Pydantic v2
- asyncio
- boto3 / aioboto3 where appropriate
- integer fixed-point money arithmetic
- Decimal only for price conversion/display

## Critical Budget State

- Amazon DynamoDB
- `TransactWriteItems`
- conditional updates
- DynamoDB Streams

## Runtime

- Docker
- AWS ECS Fargate
- Application Load Balancer

## Event Processing

- AWS Lambda
- DynamoDB Streams

## Dashboard

- React / Next.js
- TypeScript
- realtime updates

## Realtime Transport

- API Gateway WebSocket

## Observability

- Langfuse
- OpenTelemetry
- CloudWatch

Langfuse MUST NOT be the enforcement source of truth.

If Langfuse is down, budget enforcement must continue.

## Infrastructure

- Terraform

## CI/CD

- GitHub Actions
- ECR
- ECS deployment

## Providers

Implement provider abstraction supporting:

1. Amazon Bedrock
2. OpenAI

Design so Anthropic can be added cleanly.

If practical, implement Anthropic as a third provider.

---

# 5. Repository Structure

Target approximately:

```text
agent-budget-controller/
│
├── apps/
│   ├── gateway/
│   │   ├── api/
│   │   ├── auth/
│   │   ├── budgets/
│   │   ├── windows/
│   │   ├── metering/
│   │   ├── routing/
│   │   ├── providers/
│   │   │   ├── base.py
│   │   │   ├── openai.py
│   │   │   ├── bedrock.py
│   │   │   └── anthropic.py
│   │   ├── pricing/
│   │   ├── runaway/
│   │   ├── ledger/
│   │   ├── sessions/
│   │   ├── observability/
│   │   ├── config/
│   │   └── main.py
│   │
│   ├── stream_processor/
│   └── dashboard/
│
├── infrastructure/
│   └── terraform/
│       ├── networking/
│       ├── ecs/
│       ├── dynamodb/
│       ├── alb/
│       ├── lambda/
│       ├── websocket/
│       ├── cloudfront/
│       ├── iam/
│       ├── secrets/
│       └── monitoring/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── concurrency/
│   ├── failure_injection/
│   └── e2e/
│
├── pricing/
│   └── catalog.json
│
├── scripts/
├── docker/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── Makefile
└── README.md
```

Adapt intelligently to the current repository.

Do not duplicate or remove useful existing work unnecessarily.

---

# 6. Money Representation

Never use binary floating point for enforcement.

Use:

```text
1 USD = 1,000,000,000 nano-USD
```

Core fields:

```text
limit_nano_usd
committed_nano_usd
reserved_nano_usd
available_nano_usd
```

Authorization condition:

```text
committed_nano_usd
+ reserved_nano_usd
+ request_reservation_nano_usd
<= limit_nano_usd
```

Use integers for comparisons.

Use Decimal only when:

- parsing price catalog values
- converting provider pricing
- producing human-readable dollar output

---

# 7. Token Governance

The system must support monetary limits and token limits independently.

Support:

```text
limit_usd
max_total_tokens
max_input_tokens
max_output_tokens
```

Track separately:

```text
preflight_input_tokens
reserved_output_tokens

actual_input_tokens
actual_cached_input_tokens
actual_output_tokens
actual_reasoning_tokens

estimated_input_cost_nano_usd
estimated_output_cost_nano_usd
estimated_tool_cost_nano_usd
estimated_max_cost_nano_usd

actual_input_cost_nano_usd
actual_output_cost_nano_usd
actual_tool_cost_nano_usd
actual_total_cost_nano_usd
```

Provider-specific token detail fields must be normalized into mutually exclusive billing buckets.

Do not double-count cached tokens when the provider reports cached input as a subset of total input.

A token quota must be able to independently reject a request even when dollar budget remains.

---

# 8. Preflight Estimated-Cost Metering

The original requirement explicitly asks to record token count and estimated cost.

For every governed request attempt, persist the preflight estimate:

```text
preflight_input_tokens
effective_max_output_tokens
reserved_output_tokens

estimated_input_cost_nano_usd
estimated_output_cost_nano_usd
estimated_tool_cost_nano_usd
estimated_max_cost_nano_usd
```

The dashboard and ledger must distinguish:

```text
ESTIMATED / AUTHORIZED EXPOSURE
```

from:

```text
ACTUAL RECONCILED COST
```

The financial reservation may be based on the estimated maximum exposure, but the data model must preserve both concepts clearly.

---

# 9. Output Bounding

Before inference, output size is unknown.

Therefore the gateway MUST enforce a hard output ceiling.

Conceptually:

```python
effective_max_output = min(
    client_requested_max_output,
    policy_max_output,
    model_max_output,
)
```

If the client omits an output-token maximum:

- inject the policy maximum
- use that value for reservation
- ensure the provider request uses that cap

Clients must never be able to bypass spend protection by omitting an output limit.

The reservation must include worst-case output exposure.

---

# 10. Budget Window Abstraction

Do not hard-code the budget engine exclusively to monthly windows.

Create a first-class `BudgetWindow` abstraction.

Support at minimum:

```text
DAILY
WEEKLY
MONTHLY
SESSION
```

MONTHLY is mandatory for the challenge examples.

A calendar budget window must resolve deterministically to:

```text
window_start
window_end
window_key
reset_at
```

Examples:

```text
WINDOW#DAY#2026-08-19
WINDOW#WEEK#2026-W34
WINDOW#MONTH#2026-08
```

Session budgets are lifecycle-based and do not periodically reset.

Authorization logic must operate against a resolved window object rather than contain monthly-specific logic.

---

# 11. Provider Adapter Interface

Create a clean provider abstraction.

Conceptually:

```python
class ProviderAdapter(Protocol):

    async def count_input_tokens(
        self,
        request: LLMRequest,
    ) -> TokenEstimate:
        ...

    async def invoke(
        self,
        request: LLMRequest,
    ) -> ProviderResponse:
        ...

    def extract_usage(
        self,
        response: ProviderResponse,
    ) -> Usage:
        ...

    def capabilities(
        self,
        model: str,
    ) -> ModelCapabilities:
        ...
```

Provider-specific behavior must remain outside the core budget engine.

The budget engine must not contain provider-specific token parsing logic.

---

# 12. Versioned Price Catalog

Pricing must be a first-class subsystem.

Do not scatter constants such as:

```python
PRICE_PER_TOKEN = ...
```

through business logic.

Use a versioned catalog.

Example:

```json
{
  "provider": "provider_name",
  "model": "model_identifier",
  "effective_from": "ISO_TIMESTAMP",
  "currency": "USD",
  "input_per_million": "...",
  "cached_input_per_million": "...",
  "output_per_million": "...",
  "cache_write_multiplier": "...",
  "long_context_threshold": null,
  "long_context_input_multiplier": null,
  "long_context_output_multiplier": null,
  "source_version": "...",
  "status": "ACTIVE"
}
```

Do not assume model names or prices are permanently stable.

Every ledger entry must record the exact price catalog version used.

Historical spend must not silently change when current provider pricing changes.

---

# 13. Budget Hierarchy

Support:

```text
TEAM
 ↓
AGENT
 ↓
SESSION
 ↓
MODEL ALLOCATION
 ↓
REQUEST
```

An inference request is legal only if it fits every mandatory parent scope.

Example:

```yaml
team:
  id: engineering
  budget:
    amount_usd: "500.00"
    window: monthly
    warning_percent: 80

agents:
  - id: code-review-agent
    team: engineering

    budget:
      amount_usd: "50.00"
      window: monthly

    token_budget:
      max_input_tokens: 4000000
      max_output_tokens: 1000000
      max_total_tokens: 5000000

    session_budget:
      amount_usd: "2.00"

    routing:
      provider: openai
      preferred_model: configured-premium-model

      model_allocations:
        configured-premium-model:
          amount_usd: "40.00"
          window: monthly

      fallback_models:
        - configured-cheaper-model

    runaway_detection:
      monthly_budget_percent: 20
      interval_minutes: 60
      action: pause
```

---

# 14. Atomic Reservation

Implement authorization using DynamoDB transactional writes.

One authorization transaction should atomically validate/update:

```text
TEAM budget
AGENT budget
SESSION budget
MODEL allocation when applicable
REQUEST reservation record
```

Conditions must enforce:

```text
team available >= reservation
agent available >= reservation
session available >= reservation
model available >= reservation

team token quotas fit
agent token quotas fit
session token quotas fit if configured

agent.status == ACTIVE
session.status == OPEN
```

If any condition fails:

- cancel the whole transaction
- do not modify partial scopes
- do not invoke the provider

Handle transaction conflicts with bounded exponential backoff.

Never permit concurrency to break:

```text
committed + reserved <= limit
```

---

# 15. Request Reservation State Machine

Implement durable request states:

```text
RESERVED
   │
   ├── provider definitely not invoked
   │        ↓
   │     RELEASED
   │
   ├── provider completed
   │        ↓
   │     RECONCILED
   │
   └── provider outcome ambiguous
            ↓
       RECONCILE_PENDING
```

Do not assume a network timeout means the provider did not charge.

For ambiguous outcomes:

- do not prematurely release the reservation
- transition to `RECONCILE_PENDING`
- preserve auditability
- provide an operator reconciliation path
- track upstream attempts

---

# 16. Idempotency

Support:

```http
Idempotency-Key: <uuid>
```

Persist:

```text
tenant_id + idempotency_key → RequestReservation
```

Retrying the same logical request must not reserve or charge twice.

Do not rely solely on short-lived DynamoDB transaction client tokens for public API idempotency.

---

# 17. Actual Usage Reconciliation

After provider completion:

1. extract provider-reported usage
2. normalize usage classes
3. calculate actual token quantities
4. calculate actual cost
5. atomically reconcile:

```text
reserved -= request_reservation
committed += actual_cost
```

6. update token counters
7. write immutable usage ledger
8. evaluate warnings
9. evaluate session closure
10. publish events
11. publish observability telemetry

After settled calls:

```text
outstanding reservation count = 0
```

except intentionally retained `RECONCILE_PENDING` records.

---

# 18. Immutable Usage Ledger

Every completed request must generate an immutable ledger record.

Example:

```json
{
  "request_id": "...",
  "team_id": "...",
  "agent_id": "...",
  "session_id": "...",

  "provider": "...",
  "requested_model": "...",
  "effective_model": "...",
  "decision": "...",

  "preflight_input_tokens": 0,
  "reserved_output_tokens": 0,

  "actual_input_tokens": 0,
  "actual_cached_input_tokens": 0,
  "actual_output_tokens": 0,
  "actual_reasoning_tokens": 0,

  "estimated_input_cost_nano_usd": 0,
  "estimated_output_cost_nano_usd": 0,
  "estimated_tool_cost_nano_usd": 0,
  "estimated_max_cost_nano_usd": 0,

  "actual_input_cost_nano_usd": 0,
  "actual_output_cost_nano_usd": 0,
  "actual_tool_cost_nano_usd": 0,
  "actual_total_cost_nano_usd": 0,

  "reserved_nano_usd": 0,
  "price_catalog_version": "...",

  "created_at": "...",
  "completed_at": "..."
}
```

The ledger must support:

- audit
- incident analysis
- spend reconstruction
- model-routing evidence
- pricing-version evidence
- reconciliation debugging

---

# 19. 80% Warning

Do not implement the warning as only a periodic cron job.

After reconciliation calculate:

```text
utilization = committed / limit
```

When state changes from:

```text
< 80%
```

to:

```text
>= 80%
```

atomically transition:

```text
warning_80_sent:
false → true
```

Emit exactly one durable threshold event.

Concurrency must not generate duplicate warnings.

Also expose:

```text
effective_utilization =
(committed + reserved) / limit
```

for realtime operator visibility.

---

# 20. Hard Cap

If the next reservation cannot fit, return a machine-readable rejection.

Example:

```json
{
  "error": {
    "type": "budget_exhausted",
    "scope": "agent",
    "scope_id": "...",
    "limit_usd": "...",
    "committed_usd": "...",
    "reserved_usd": "...",
    "available_usd": "...",
    "reset_at": "...",
    "request_id": "..."
  }
}
```

HTTP `429` is acceptable.

Most important property:

```text
budget rejection
      ↓
NO PROVIDER API CALL
```

---

# 21. Exact 100% Hard-Cap Acceptance Test

In addition to race-condition tests, implement an exact literal hard-cap test.

Configure:

```text
agent limit = $1.00
```

Using deterministic metering/provider behavior, drive:

```text
committed == limit
reserved == 0
available == 0
```

Then send one additional request.

Expected:

```text
HTTP 429
error.type = budget_exhausted
```

Provider invocation count must not increase.

This test explicitly proves:

> Hard block fires at 100 percent consumed.

---

# 22. Session Lifecycle

Implement:

```text
OPEN
CLOSED_BUDGET
CLOSED_USER
CLOSED_ADMIN
EXPIRED
```

The controller must never deliberately permit governed session spend beyond its configured cap.

Session closure must support both cases:

## Case A — Exact Cap Reached

If reconciliation causes:

```text
committed_session >= session_limit
```

then atomically transition:

```text
OPEN → CLOSED_BUDGET
```

## Case B — Next Request Would Exceed Cap

If:

```text
committed + reserved + requested_reservation > limit
```

then:

- reject before provider
- atomically transition to `CLOSED_BUDGET`

Subsequent requests must return:

```text
HTTP 429
type = session_closed
reason = budget_exhausted
```

and must not invoke the provider.

Test both cases.

---

# 23. Model Allocation vs Parent Budget

Do not confuse:

```text
AGENT TOTAL BUDGET
```

with:

```text
PREFERRED MODEL ALLOCATION
```

Example:

```text
Agent total budget       = $50
Preferred-model budget   = $40
Fallback capacity        = remaining parent capacity
```

When preferred-model allocation is exhausted:

1. verify team capacity remains
2. verify agent capacity remains
3. verify session capacity remains
4. evaluate fallback chain
5. validate provider compatibility
6. validate capability compatibility
7. count input for fallback if needed
8. calculate fallback estimated cost
9. atomically reserve fallback exposure
10. invoke fallback

Never use fallback to bypass an exhausted team, agent, or session budget.

---

# 24. Capability-Aware Routing

Never substitute based only on price.

Validate:

- same provider when required by policy
- modality
- context length
- output limit
- tools
- structured output
- reasoning requirements
- compliance
- region/data residency
- policy allowlist

Expose:

```text
requested_model
effective_model
```

and a decision such as:

```text
SUBSTITUTED_PREFERRED_MODEL_BUDGET
```

Expose response metadata/headers where appropriate:

```text
X-Budget-Decision
X-Budget-Requested-Model
X-Budget-Effective-Model
X-Budget-Scope
```

Do not silently substitute.

---

# 25. Verify the Fallback Is Actually Cheaper

A configured fallback is not automatically considered cheaper.

For the actual request, calculate estimated worst-case cost for both preferred and fallback models using the active versioned price catalog.

For budget-pressure substitution require:

```text
fallback.provider == preferred.provider
```

and:

```text
fallback capability-compatible == true
```

and:

```text
fallback_estimated_max_cost < preferred_estimated_max_cost
```

Persist:

```text
preferred_estimated_cost_nano_usd
fallback_estimated_cost_nano_usd
estimated_savings_nano_usd
```

Add an acceptance test proving the chosen fallback is genuinely cheaper for the routed request.

---

# 26. Runaway-Agent Circuit Breaker

Implement the deterministic bonus rule:

```text
If an agent consumes >20% of its monthly budget
inside a rolling 60-minute window:
    flag it
    pause it
    require human review
```

Use a rolling 60-minute window rather than fixed clock-hour buckets.

When threshold is crossed:

```text
agent.status = PAUSED_RUNAWAY
review_required = true
```

Emit a durable event.

Every inference request checks agent status before provider invocation.

Once paused:

```text
request
   ↓
agent PAUSED
   ↓
reject
   ↓
NO PROVIDER CALL
```

---

# 27. Idempotent Runaway/Event Processing

DynamoDB Stream consumers must be idempotent.

Use stable event IDs such as:

```text
usage_event_id = request_id + reconciliation_version
```

Duplicate stream delivery must not:

- double-count spend
- duplicate warnings
- duplicate runaway pauses
- duplicate dashboard state

The dashboard event projection must also tolerate duplicate/out-of-order events.

---

# 28. Human Review Workflow

Implement:

```http
POST /v1/admin/agents/{agent_id}/pause
POST /v1/admin/agents/{agent_id}/resume
GET  /v1/admin/agents/{agent_id}/runaway-events
```

Administrative changes must produce permanent audit records containing:

```text
actor
action
reason
previous_state
new_state
timestamp
request_id if applicable
```

Do not require manual DynamoDB editing.

---

# 29. Identity and Security

Do not trust an agent to identify itself using an arbitrary client header.

Never rely only on:

```text
X-Agent-ID: some-agent
```

from an untrusted caller.

Map trusted credentials server-side:

```text
API key / JWT subject / workload identity
                  ↓
tenant
                  ↓
team
                  ↓
agent
```

Verify that a supplied session belongs to the authenticated agent.

Provider secrets remain server-side.

Use:

- AWS Secrets Manager
- ECS task IAM roles
- KMS where appropriate
- least privilege IAM
- encrypted transport
- structured audit logs

Prompt/response persistence should be disabled or redacted by default.

Budget governance usually requires metadata, usage, pricing, identity, and routing information, not raw prompt content.

---

# 30. API Surface

Implement the control plane:

```text
POST   /v1/teams
PUT    /v1/teams/{team}/budget

POST   /v1/agents
PUT    /v1/agents/{agent}/budget
PUT    /v1/agents/{agent}/routing-policy

POST   /v1/sessions
GET    /v1/sessions/{session}
POST   /v1/sessions/{session}/close

GET    /v1/budgets
GET    /v1/budgets/{scope}/{id}
GET    /v1/ledger

POST   /v1/admin/agents/{agent}/pause
POST   /v1/admin/agents/{agent}/resume
GET    /v1/admin/agents/{agent}/runaway-events

GET    /healthz
GET    /readyz
```

Implement provider-compatible data-plane endpoints, starting with:

```text
POST /v1/responses
POST /v1/chat/completions
```

Design for:

```text
/anthropic/v1/messages
/bedrock/converse
```

The goal is to allow an existing agent to primarily change its base URL rather than rewrite the complete LLM integration.

---

# 31. Health and Readiness

`/healthz` means:

```text
process is alive
```

`/readyz` means:

```text
instance is safe to govern traffic
```

Readiness must verify at minimum:

- price catalog loaded
- budget store reachable
- required configuration loaded
- provider adapters initialized
- required identity configuration loaded

Do not make a billable model call during health checks.

---

# 32. Langfuse Integration

Integrate Langfuse asynchronously.

Propagate stable identifiers and metadata:

```text
request_id
session_id
agent_id
team_id
requested_model
effective_model
budget_decision
input_tokens
output_tokens
estimated_cost
actual_cost
latency
errors
```

Langfuse responsibilities:

- traces
- session grouping
- token visibility
- cost visibility
- routing evidence
- requested vs effective model analysis
- latency analysis
- debugging
- evaluation evidence

Langfuse is NOT allowed to become a hard dependency of financial authorization.

If Langfuse becomes unavailable:

```text
budget enforcement continues
provider gateway continues
telemetry failure is recorded
```

---

# 33. CloudWatch and Structured Logging

Every request must have a stable request ID.

Use structured JSON logs.

Include:

```text
request_id
team_id
agent_id
session_id
provider
requested_model
effective_model
budget_decision

preflight_input_tokens
reserved_output_tokens
actual_input_tokens
actual_output_tokens

estimated_max_cost_nano_usd
reserved_nano_usd
actual_total_cost_nano_usd

latency_ms
status
```

Recommended metrics:

```text
budget_requests_total
budget_rejections_total
budget_substitutions_total
budget_warning_events_total
budget_reservation_conflicts_total
budget_reservations_outstanding
budget_reconciliation_failures_total

llm_cost_usd_total
llm_input_tokens_total
llm_output_tokens_total
llm_request_latency_seconds

runaway_agent_pauses_total
active_sessions
closed_budget_sessions_total
```

---

# 34. DynamoDB Budget Reset Semantics

Do not use DynamoDB TTL to decide whether a budget has reset.

Use deterministic window keys.

Examples:

```text
BUDGET#TEAM#engineering / WINDOW#MONTH#2026-08
BUDGET#TEAM#engineering / WINDOW#MONTH#2026-09
```

At the boundary, requests immediately use the new window key.

TTL may later delete historical state.

TTL must never determine authorization.

---

# 35. Region Strategy

Use a single authoritative budget-write region for the first production deployment.

Use Multi-AZ infrastructure within that region.

Do not allow active-active independent budget mutation across AWS regions unless a correct home-region ownership protocol is implemented.

Document the decision and tradeoff.

---

# 36. Realtime Dashboard

Build a usable admin dashboard.

Per team and agent show:

```text
LIMIT
COMMITTED
RESERVED / IN-FLIGHT
AVAILABLE
UTILIZATION
EFFECTIVE UTILIZATION

STATUS
WARNING

REQUESTS LAST HOUR
SPEND LAST HOUR

INPUT TOKENS
OUTPUT TOKENS
RESERVED OUTPUT TOKENS

ESTIMATED COST
ACTUAL COST
INPUT COST
OUTPUT COST

RUNAWAY THRESHOLD

PRIMARY MODEL
PRIMARY MODEL ALLOCATION
FALLBACK MODEL
SUBSTITUTIONS
```

## Overview

Display:

- total teams
- total agents
- active sessions
- spend today
- reserved exposure
- blocked requests
- paused agents
- warning count
- substitution count

## Agents

Display:

- status
- budget progress
- hourly spend
- token usage
- model
- fallback activity
- runaway state

## Sessions

Display:

- lifecycle state
- budget usage
- token usage
- request count
- close reason

## Events

Display:

- 80% warnings
- hard-cap blocks
- model substitutions
- session closures
- runaway pauses
- manual pauses
- resumes

## Ledger

Filter/search by:

- request ID
- team
- agent
- session
- provider
- requested model
- effective model
- decision
- date/time

Use WebSockets for live updates.

On reconnect, fetch current authoritative state from the backend rather than assuming every event was received.

---

# 37. Local Development

Provide a reliable local development mode.

Support:

```text
docker-compose up
```

or equivalent.

Provide local components such as:

- DynamoDB Local or compatible local backend
- deterministic fake provider
- configurable fake input token count
- configurable fake output usage
- configurable fake cost
- configurable fake latency
- configurable provider rejection
- configurable ambiguous timeout
- fake clock/time controls for runaway testing

The fake provider MUST record invocation count.

Tests must be able to assert:

```text
blocked requests did not invoke provider
```

The complete test suite must be runnable without spending real API money.

---

# 38. Required Automated Tests

Testing is a primary deliverable.

Do not merely test HTTP status codes.

Test financial invariants.

---

# 39. Concurrent Three-Agent Test

Configure:

```text
Team Engineering: $1.00

Agent A: $0.50
Agent B: $0.50
Agent C: $0.50
```

Send concurrent governed requests.

Assert:

```text
for every scope:
committed + reserved <= limit
```

Also assert:

```text
team committed =
sum(actual governed usage under the team)

agent committed =
sum(actual governed usage for that agent)
```

After all settled requests:

```text
outstanding reservations = 0
```

except any intentionally retained ambiguous pending records.

---

# 40. Critical Hard-Cap Race Test

Configure:

```text
remaining budget = $0.05
reservation per request = $0.04
concurrent requests = 10
```

Expected:

```text
1 authorized
9 rejected
```

NOT:

```text
10 authorized
then dashboard reports overspend
```

Assert provider invocation count increases by exactly `1`.

This is a mandatory acceptance test.

---

# 41. Token Accounting Test

Assert:

- preflight input count persisted
- gateway-controlled output reservation persisted
- actual input usage persisted
- actual output usage persisted
- cached usage normalized
- reasoning usage normalized where provided
- no token double counting
- estimated input/output cost persisted
- actual input/output cost persisted
- total actual cost reconciles correctly
- token quota independently rejects a request while USD remains
- blocked token-quota request does not invoke provider

---

# 42. 80% Warning Test

Configure:

```text
limit = $0.10
warning = 80%
```

Produce:

```text
$0.079 → no warning
$0.081 → exactly one warning
$0.090 → still exactly one warning
```

Also run concurrent reconciliation near the threshold.

Still require exactly one durable warning event.

---

# 43. Exact 100% Test

Configure deterministic behavior so:

```text
limit = $1.00
committed = $1.00
reserved = $0
available = $0
```

Send one more request.

Assert:

```text
HTTP 429
type = budget_exhausted
provider invocation count unchanged
```

---

# 44. Session Closure Test

Test both session closure paths.

## Exact-Cap Path

Drive:

```text
committed_session == session_limit
```

Assert:

```text
session.status == CLOSED_BUDGET
```

## Would-Exceed Path

Leave some capacity but make the next request require more reservation than remains.

Assert:

```text
request rejected
session.status == CLOSED_BUDGET
provider invocation count unchanged
```

For subsequent requests:

```text
HTTP 429
type = session_closed
reason = budget_exhausted
```

---

# 45. Model Substitution Test

Configure:

```text
agent total budget       = $0.10
preferred-model budget   = $0.02
fallback                 = cheaper compatible same-provider model
```

Exhaust only preferred-model allocation.

Next request must produce:

```text
requested_model != effective_model
decision = SUBSTITUTED_PREFERRED_MODEL_BUDGET
```

Verify:

- same provider
- fallback capability-compatible
- fallback estimated cost < preferred estimated cost
- actual provider adapter receives fallback model

Then exhaust parent agent budget.

Next request must be hard-blocked.

Fallback must not bypass the parent cap.

---

# 46. Runaway Test

Configure:

```text
monthly agent budget = $10
20% threshold = $2
window = rolling 60 minutes
```

Generate more than $2 of reconciled spend within the rolling window.

Assert:

```text
agent.status = PAUSED_RUNAWAY
review_required = true
runaway event emitted
```

Subsequent model requests:

```text
rejected before provider
```

Then call authorized resume API.

Verify permanent audit event.

---

# 47. Failure-Injection Tests

Implement at minimum:

| Failure | Required Behavior |
|---|---|
| Crash before reservation | No state change |
| Crash after reservation before provider | Reservation recovered/released when outcome is certain |
| Provider deterministic rejection | Reservation released |
| Provider success | Actual usage reconciled |
| Provider timeout with ambiguous outcome | Reservation not prematurely released |
| Duplicate client request | No duplicate logical charge |
| Concurrent transaction conflict | Retry safely |
| Duplicate DynamoDB Stream event | No duplicate alert/spend |
| Dashboard reconnect | State reconstructed from database |
| Gateway process restart | Budget state preserved |
| Monthly boundary | New budget window used immediately |
| Expired TTL item still present | Does not affect current authorization |
| Langfuse unavailable | Enforcement still works |
| WebSocket unavailable | Enforcement still works |
| Dashboard unavailable | Enforcement still works |

---

# 48. Mandatory Real LLM Provider Integration

Mocks are permitted for testing and development.

Mocks do NOT satisfy final production acceptance.

At least ONE real LLM provider must be integrated and exercised end-to-end through the deployed gateway.

Preferred order:

1. Amazon Bedrock using ECS task IAM role
2. OpenAI using a key from AWS Secrets Manager

The final validation path must be:

```text
client
→ deployed Agent Budget Gateway
→ preflight metering
→ budget authorization
→ real provider
→ provider-reported token usage
→ actual cost reconciliation
→ immutable ledger
```

Persist actual provider-reported usage.

If credentials are genuinely unavailable:

- complete the real integration code
- complete secret/IAM wiring
- complete the live E2E test harness
- mark `LIVE_PROVIDER_E2E = NOT VERIFIED`
- do not claim full production acceptance

---

# 49. Mandatory AWS Deployment Acceptance Gate

The project is NOT complete merely because:

- Docker works locally
- Terraform validates
- Terraform plans
- unit tests pass

For production acceptance, deploy the system to AWS when credentials are available.

At minimum the deployed environment must include:

- ECS/Fargate Agent Budget Gateway
- Application Load Balancer or equivalent API entry
- DynamoDB persistent budget state
- DynamoDB Streams
- Lambda event processor
- deployed dashboard
- realtime update mechanism
- CloudWatch logging
- Secrets Manager
- IAM roles
- Terraform-managed infrastructure

After deployment, run a cloud end-to-end smoke test.

The completion report must provide evidence of:

1. deployed `/healthz` succeeding
2. deployed `/readyz` succeeding
3. persistent budget state in DynamoDB
4. one governed real-provider inference request
5. one rejected budget request
6. rejected request causing zero upstream provider invocation
7. CloudWatch logs containing correlated request ID
8. dashboard displaying updated budget state
9. state surviving gateway restart/redeploy

Terraform validation alone is NOT sufficient for Definition of Done.

---

# 50. AWS-Hosted Workload Demonstration

Provide a minimal AWS-hosted caller workload where practical.

Preferred:

- ECS/Fargate load-test/client task
- or Lambda where appropriate

It must call the deployed Agent Budget Gateway instead of calling the model provider directly.

Use it to demonstrate at least three logical agents making concurrent governed requests.

The goal is to prove that the controller can govern an AWS-hosted enterprise AI workload and is not merely a developer-laptop proxy.

If this cannot be deployed due to missing AWS credentials, provide the IaC/task definition and mark the cloud workload validation as unverified.

---

# 51. Terraform

Create Terraform for at minimum:

- networking/VPC requirements
- ECS cluster
- ECS service
- ECS task definition
- ECR
- ALB
- DynamoDB tables
- DynamoDB Streams
- Lambda stream processor
- API Gateway WebSocket
- S3
- CloudFront
- IAM roles and policies
- Secrets Manager references
- CloudWatch log groups
- CloudWatch alarms
- security groups
- KMS where appropriate

Use variables for:

```text
AWS region
environment
domain configuration
container image
provider secret names
Langfuse configuration
budget table names
ledger table names
event table names
```

Never commit secrets.

---

# 52. Docker

Create production-ready Docker configuration.

Requirements:

- small image
- pinned/deterministic dependency installation
- non-root runtime where practical
- production FastAPI/Uvicorn configuration
- health-check support
- clean signal handling
- no secrets baked into image

Provide Docker Compose for local development.

---

# 53. CI/CD

Implement GitHub Actions that can:

1. install dependencies
2. lint
3. type-check
4. run unit tests
5. run integration tests appropriate for CI
6. run concurrency tests appropriate for CI
7. build Docker image
8. optionally push to ECR
9. deploy ECS when configured
10. optionally run deployed smoke tests

CI must fail if the core financial invariant tests fail.

---

# 54. README

Create a comprehensive `README.md`.

Include:

## What It Does

Explain that this is a financial authorization control plane, not just observability.

## Architecture

Include Mermaid diagrams.

## Core Invariant

```text
committed + reserved <= limit
```

## Request Lifecycle

Document:

```text
count
→ bound
→ estimate
→ reserve
→ invoke
→ reconcile
```

## Local Setup

Provide exact commands.

## Environment Variables

Document all required variables.

## Running Tests

Provide exact commands.

## API Examples

Provide curl examples for:

- create team
- set team budget
- create agent
- set agent budget
- set routing policy
- create session
- model request
- fetch budget
- fetch ledger
- pause
- resume

## AWS Deployment

Provide Terraform commands.

## Live Provider Setup

Explain Bedrock and/or OpenAI configuration.

## Demo Scenario

Document how to demonstrate:

1. normal request
2. 80% warning
3. exact 100% hard block
4. preferred-model allocation exhaustion
5. cheaper same-provider fallback
6. session closure
7. runaway agent pause
8. paused-agent rejection
9. manual resume
10. realtime dashboard update

---

# 55. Implementation Sequence

Execute in this order.

## Phase 1 — Repository Audit

- inspect repository
- identify reusable work
- identify missing requirements
- create implementation checklist
- preserve useful existing functionality

## Phase 2 — Core Domain

Implement:

- fixed-point money types
- token usage types
- budget policy
- budget state
- budget windows
- session state
- agent state
- request reservation
- pricing models
- usage normalization
- ledger models

Write unit tests immediately.

## Phase 3 — Local Persistence/Test Infrastructure

Implement repository interfaces and deterministic local/testing backends.

Then implement DynamoDB repository.

## Phase 4 — Atomic Budget Engine

Implement:

- reserve
- reconcile
- release
- pending reconciliation

Build concurrency tests before proceeding.

The hard-cap race test must pass here.

## Phase 5 — Provider Adapters

Implement fake provider first.

Then implement:

- Bedrock
- OpenAI

Then Anthropic if feasible.

Implement provider token counting, capability descriptions, invocation, usage extraction, and error handling.

## Phase 6 — Gateway APIs

Implement:

- authentication
- governance identity resolution
- team API
- agent API
- session API
- budget API
- ledger API
- provider-compatible inference endpoints

## Phase 7 — Warning / Session / Routing

Implement:

- 80% warning transition
- exact hard block
- session closure
- preferred-model allocation
- cheaper same-provider fallback
- fallback price verification
- capability validation

## Phase 8 — Runaway Detector

Implement:

- stream consumer
- rolling 60-minute spend calculation
- idempotent processing
- automatic pause
- human resume
- audit records

## Phase 9 — Observability

Add:

- request IDs
- structured logging
- metrics
- OpenTelemetry
- Langfuse

Ensure Langfuse failure cannot break enforcement.

## Phase 10 — Dashboard

Build realtime admin UI.

## Phase 11 — AWS Infrastructure

Implement Terraform and deployment configuration.

## Phase 12 — Production Deployment

When AWS credentials are available:

- deploy infrastructure
- deploy gateway
- deploy dashboard
- configure provider credentials/IAM
- run live provider call
- run deployed rejection test
- inspect CloudWatch logs
- verify persistence

## Phase 13 — Production Validation

Run:

- unit tests
- integration tests
- concurrency tests
- failure injection
- frontend checks
- local E2E
- deployed E2E
- real provider E2E
- AWS-hosted three-agent demonstration when possible

Fix failures before declaring completion.

---

# 56. Development Rules

## Rule 1

Do not merely describe implementation.

Implement it.

## Rule 2

Do not stop after scaffolding.

## Rule 3

Do not replace core financial logic with TODOs.

## Rule 4

Core financial logic must have automated tests.

## Rule 5

Never weaken:

```text
committed + reserved <= limit
```

for convenience.

## Rule 6

Do not put Langfuse, dashboard, WebSocket, or asynchronous analytics in the hard authorization path.

## Rule 7

Do not use floating point for money enforcement.

## Rule 8

Do not call real providers from ordinary unit tests.

## Rule 9

Blocked requests must demonstrably cause zero upstream provider calls.

## Rule 10

Do not hard-code secrets.

## Rule 11

Do not hard-code volatile model prices throughout business logic.

## Rule 12

Do not silently substitute models.

## Rule 13

Do not use TTL for budget reset.

## Rule 14

Do not blindly release ambiguous provider reservations.

## Rule 15

Do not trust client-provided agent identity without server-side authorization.

## Rule 16

Prefer maintainable production-quality code over clever abstractions.

## Rule 17

Run tests after meaningful milestones and fix failures.

## Rule 18

Do not claim production success unless the corresponding production validation actually ran.

---

# 57. Challenge-to-Implementation Acceptance Matrix

The final solution must explicitly prove every challenge requirement.

| Challenge Requirement | Required Proof |
|---|---|
| Team budget | Automated test + API + dashboard |
| Agent budget | Automated test + API + dashboard |
| Session budget | Automated test + automatic close |
| Per time window | BudgetWindow abstraction + monthly test |
| Token metering | Persisted preflight + actual usage |
| Estimated cost | Persisted preflight estimated cost |
| Realtime totals | DynamoDB state + realtime dashboard |
| 80% warning | Exactly-one warning test |
| 100% hard block | Exact-cap test + zero provider calls |
| Session closes | Exact-cap + would-exceed tests |
| Model substitution | Same-provider cheaper fallback test |
| Three concurrent agents | Concurrency test |
| Persistent state | DynamoDB + restart test |
| Usable API | FastAPI control/data plane |
| Logging | CloudWatch structured logs |
| Error handling | Typed errors + failure tests |
| Health check | `/healthz` |
| Readiness | `/readyz` |
| Real provider | Live E2E |
| AWS deployment | Deployed cloud smoke test |
| Runaway >20%/hour | Rolling-60m test |
| Human review | Pause/resume + audit record |

---

# 58. Minimum Production Bar

The project must have all of these functioning together:

- cloud-hosted inference gateway
- AWS deployment
- real LLM provider integration
- atomic preauthorization
- persistent state
- actual reconciliation
- explicit token governance
- explicit estimated-cost tracking
- hard output bounds
- real concurrency tests
- exact 80% warning
- exact 100% hard-cap block
- price-aware same-provider fallback
- session closure
- runaway circuit breaker
- human review
- realtime UI
- Langfuse observability
- CloudWatch/OpenTelemetry
- operational controls
- health/readiness
- audit records
- Terraform
- CI/CD
- restart persistence
- failure-injection validation

---

# 59. Definition of Done

Do NOT declare the project complete until all applicable items below are true.

## Core Gateway

- FastAPI gateway runs
- control-plane API works
- data-plane inference API works
- trusted identity mapping works

## Budgets

- team budgets work
- agent budgets work
- session budgets work
- time-window abstraction works
- USD enforcement works
- input-token quota works
- output-token quota works
- total-token quota works

## Metering

- preflight input tokens recorded
- output bound enforced
- estimated input cost recorded
- estimated output cost recorded
- estimated maximum cost recorded
- actual provider usage recorded
- actual input cost recorded
- actual output cost recorded
- actual total cost recorded

## Financial Enforcement

- reservations occur before inference
- reservations are atomic across scopes
- concurrent invariant holds
- exact 80% warning fires once
- exact 100% hard block works
- blocked requests invoke provider zero times
- ambiguous provider failures retain reservation safely

## Sessions

- exact-cap session closure works
- would-exceed session closure works
- subsequent calls rejected before provider

## Routing

- preferred-model allocation works
- fallback is same-provider where required
- fallback is capability-compatible
- fallback is verified cheaper
- fallback cannot bypass parent budgets
- routing decision is auditable

## Runaway Control

- rolling 60-minute detector works
- >20% monthly spend causes pause
- paused agent cannot invoke provider
- human resume works
- audit record written

## Persistence and Reliability

- budget state survives process restart
- budget state survives ECS restart/redeploy
- idempotency prevents duplicate charge
- duplicate stream events are safe
- monthly window rollover works
- TTL presence does not affect current authorization

## Dashboard

- current spend vs limit visible
- committed vs reserved visible
- estimated vs actual cost visible
- active agents visible
- active teams visible
- sessions visible
- warnings visible
- substitutions visible
- pauses visible
- ledger searchable
- realtime updates work

## Observability

- structured request-ID logs work
- CloudWatch integration works
- OpenTelemetry works
- Langfuse integration works asynchronously
- Langfuse outage does not break enforcement

## Infrastructure

- Docker local environment works
- Terraform validates
- Terraform plan succeeds
- AWS deployment succeeds when credentials are available
- deployed health check succeeds
- deployed readiness check succeeds
- deployed state persists in DynamoDB
- deployed dashboard works
- CloudWatch logs receive gateway events

## Provider Integration

- at least one real provider adapter works
- at least one live provider request passes through deployed gateway
- provider usage reconciles into ledger

If live credentials are unavailable:

- integration must still be fully implemented
- production E2E test harness must exist
- status must be reported as NOT VERIFIED
- project must not claim full production acceptance

## Testing

- unit tests pass
- integration tests pass
- concurrency tests pass
- exact 80% warning test passes
- exact 100% hard-cap test passes
- session tests pass
- fallback test passes
- runaway test passes
- failure-injection tests pass

---

# 60. Final Demo Scenario

Provide a reproducible production demo sequence.

## Step 1 — Initial Dashboard

Show:

```text
Engineering Team
Limit: $500

Agent 01: $31 / $50
Agent 02: $39 / $50
Agent 03: $12 / $50
```

## Step 2 — Concurrent Traffic

Start at least three agents concurrently.

Show committed and reserved values changing in realtime.

## Step 3 — 80% Warning

Drive one agent from below 80% to above 80%.

Show:

```text
WARNING — 80% BUDGET CONSUMED
```

Verify warning occurs once.

## Step 4 — Preferred Model Allocation Exhausted

Show:

```text
preferred allocation = exhausted
```

## Step 5 — Cheaper Same-Provider Fallback

Show:

```text
requested_model: premium-model
effective_model: cheaper-model
decision: SUBSTITUTED_PREFERRED_MODEL_BUDGET
```

Also show estimated cost difference.

## Step 6 — Exact Hard Cap

Reach exact limit.

Send another request.

Show:

```text
HTTP 429
budget_exhausted
```

Prove provider invocation did not occur.

## Step 7 — Session Closure

Consume a session budget.

Show:

```text
SESSION CLOSED — BUDGET EXHAUSTED
```

## Step 8 — Runaway Agent

Drive more than 20% monthly budget inside rolling 60 minutes.

Show:

```text
RUNAWAY SPEND DETECTED
STATUS: PAUSED_RUNAWAY
HUMAN REVIEW REQUIRED
```

## Step 9 — Paused Agent Rejection

Send a request from paused agent.

Show rejection before provider.

Show another healthy agent still operating.

## Step 10 — Resume

Call admin resume.

Show audit record.

## Step 11 — Observability

Show:

- CloudWatch request logs
- request ID correlation
- Langfuse trace/session
- dashboard state

---

# 61. Completion Report

When implementation is finished, return a concise engineering completion report.

Use these sections:

## Implemented

List completed capabilities.

## Architecture

Summarize final deployed architecture.

## Important Files

List important files/modules.

## Core Invariant

Explain precisely how:

```text
committed + reserved <= limit
```

remains true under concurrency.

## Tests

List test commands and results.

## Exact Challenge Results

Report:

```text
Three-agent concurrency: PASS / FAIL / NOT VERIFIED
80% warning: PASS / FAIL / NOT VERIFIED
100% hard block: PASS / FAIL / NOT VERIFIED
Session closure: PASS / FAIL / NOT VERIFIED
Cheaper model substitution: PASS / FAIL / NOT VERIFIED
Runaway detector: PASS / FAIL / NOT VERIFIED
```

## Local Run

Give exact commands.

## AWS Deployment

Give exact commands and deployed endpoints where applicable.

## Live Provider

State:

```text
Provider:
Model:
Live E2E:
Actual usage reconciled:
```

## Cloud Validation

State:

```text
AWS deployment: VERIFIED / NOT VERIFIED
Deployed health: VERIFIED / NOT VERIFIED
Deployed readiness: VERIFIED / NOT VERIFIED
DynamoDB persistence: VERIFIED / NOT VERIFIED
CloudWatch request logs: VERIFIED / NOT VERIFIED
Dashboard: VERIFIED / NOT VERIFIED
```

## Required Secrets

List secret names/environment variables only.

Do not expose values.

## Remaining Limitations

Be explicit.

Never claim a capability was verified if it was not actually tested.

---

# 62. Final Instruction to Claude Code

Start now.

First inspect the repository.

Then create an implementation checklist mapped to the challenge acceptance matrix.

Then implement the system incrementally.

Do not stop at architecture or scaffolding.

Prioritize correctness of the financial authorization path over dashboard polish.

The most important success condition is:

```text
A request cannot reach an LLM provider unless its worst-case authorized
money AND token exposure fits atomically inside every applicable budget.

Under concurrent traffic:

committed + reserved <= limit

must always remain true.
```

The final submission must be a working production-oriented system, not merely a localhost prototype.

Where AWS credentials and provider credentials are available:

- deploy it
- invoke it
- test it
- prove it

Where credentials are unavailable:

- implement the complete integration
- provide the deployment/test path
- explicitly mark verification gaps
- never claim unverified success

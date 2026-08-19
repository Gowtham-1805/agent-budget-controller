# Agent Budget Controller: Deep Research and Production Architecture

> **Revision — August 19, 2026:** strengthened with explicit input/output token governance, Langfuse observability integration, and an "already existing vs. developing now" competitive-positioning section.

## Executive assessment

The **Agent Budget Controller** is a strong production-readiness problem because the difficult part is not displaying LLM spend after the fact; it is making a **real-time authorization decision before every model invocation while multiple agents are spending from overlapping budgets concurrently**.

The right mental model is therefore not “LLM cost dashboard.” It is closer to a **financial authorization gateway for AI inference**:

```text
                 ┌─────────────────────────────┐
                 │     Enterprise Agents       │
                 │ Agent A   Agent B   Agent C │
                 └──────────────┬──────────────┘
                                │
                         Every LLM request
                                │
                                ▼
                  ┌──────────────────────────┐
                  │  Agent Budget Gateway    │
                  │                          │
                  │ Authentication           │
                  │ Token preflight          │
                  │ Cost estimation          │
                  │ Budget reservation       │
                  │ Model routing            │
                  │ Runaway-agent check      │
                  └────────────┬─────────────┘
                               │
             ┌─────────────────┼─────────────────┐
             ▼                 ▼                 ▼
          OpenAI           Anthropic        AWS Bedrock
             │                 │                 │
             └─────────────────┼─────────────────┘
                               │
                        Actual token usage
                               │
                               ▼
                  ┌──────────────────────────┐
                  │ Reconcile reservation    │
                  │ Immutable usage ledger   │
                  │ Team / Agent / Session   │
                  │ budget state             │
                  └────────────┬─────────────┘
                               │
                     DynamoDB Streams
                               │
                ┌──────────────┴──────────────┐
                ▼                             ▼
          Alert processor              Realtime dashboard
```

A crucial market finding is that, as of **August 18, 2026**, provider-level spending controls have improved considerably. OpenAI now supports monthly hard spend limits at organization and project level, returning `429` after tracked spend reaches a hard limit. OpenAI explicitly notes, however, that enforcement is **not instantaneous**, so some usage may be recorded above the configured amount. Those controls are also organization/project controls, not the agent/team/session hierarchy required by this challenge. citeturn18search1turn18search0 Anthropic likewise imposes organization-level monthly spend caps and allows customers to configure their own lower spend limit. citeturn16search0

The gateway ecosystem is already surprisingly close to this challenge. LiteLLM documents personal, team, team-member, and **agent budgets**, including session-level dollar and iteration caps; its current documentation also describes budget windows and model-budget fallbacks. citeturn21search0turn21search3 Portkey supports pre-provider usage policies including cost/token budgets, weekly/monthly periods, per-user-per-model limits, provider limits, workspace limits, and threshold notifications. citeturn21search1turn21search4turn21search9 Helicone offers request- and cost-based rate limits and cost alerts. citeturn21search2turn21search5

That means a submission consisting merely of **“proxy + counters + dashboard” is not differentiated enough**. The winning technical angle should be:

> **Strict, hierarchical, reservation-before-inference budget enforcement with atomic concurrency control, provider-aware cost calculation, model-aware fallback, session closure, and a runaway-agent circuit breaker.**

The proposed implementation would satisfy the challenge as follows:

| Requirement | Production mechanism |
|---|---|
| Team budget | Durable monthly budget state |
| Agent budget | Durable monthly child budget |
| Session budget | Non-resetting session budget |
| Concurrent agents | Atomic multi-item reservation transaction |
| 80% warning | One-time threshold state transition + event |
| 100% cap | Pre-inference reservation rejection |
| Exact metering | Provider usage object after completion |
| Cost prediction | Provider-native token count + capped output |
| Session closure | Atomic `OPEN → CLOSED` transition |
| Model substitution | Per-model allocation + compatible fallback chain |
| Dashboard | DynamoDB Streams → Lambda → WebSocket |
| Runaway detector | Rolling 60-minute spend detector → `PAUSED` |
| Human review | Admin pause/resume API + audit event |

The most important design decision is this:

**Never implement the critical path as “call the LLM, then add the bill.” Implement it as “reserve enough budget, call the LLM, then reconcile actual cost.”**

That distinction is what turns the system from observability into enforcement.

## What already exists versus what this submission develops

A production-ready submission should be explicit about the fact that several adjacent capabilities already exist. The opportunity is **not** to claim that LLM cost tracking, budget policies, or model fallbacks are new. The opportunity is to combine them into a strict, auditable agent-level authorization plane and prove that the financial invariant holds under concurrency.

| Capability | Already available today | What this submission develops / proves |
|---|---|---|
| Provider-level spend caps | OpenAI supports monthly organization/project hard spend limits and spend alerts; Anthropic provides organization-level spend controls | Fine-grained **team → agent → session → model-allocation** hierarchy, enforced before each governed request rather than only at provider account/project scope |
| LLM observability | **Langfuse** provides traces, sessions, token/cost tracking, dashboards, alerts/monitors, and a Metrics API | Langfuse is used as a **secondary observability and evidence layer**; the authoritative enforcement decision remains in the budget gateway and transactional budget store |
| Gateway budgets | **LiteLLM** supports user/team budgets, agent/session controls, and model budget fallbacks | We do not compete on a checklist alone; we prove a deterministic **reserve-before-inference** invariant across overlapping scopes with explicit race-condition and failure-injection tests |
| Policy-based usage limits | **Portkey** supports cost- and token-based usage policies, workspace limits, and rate limits | AWS-native reference implementation with a durable hierarchical ledger, exact request state machine, session closure semantics, and human-review circuit breaker |
| Cost/rate monitoring | **Helicone** provides observability, rate limits, and cost alerts | Strict hierarchical preauthorization plus model-allocation fallback and runaway-agent pause tied to one durable governance identity |

The positioning should therefore be:

> **We are not inventing LLM observability or the idea of a budget. We are building and validating an infrastructure-level agent budget control plane whose core guarantee is that a request cannot reach the provider unless its worst-case authorized exposure fits atomically inside every applicable budget and token policy.**

This distinction is important for judges. A feature comparison shows market awareness; the concurrency invariant, durable state, provider-aware reservation, session lifecycle, and circuit breaker show what is being engineered in this submission.

## The budget-control problem is harder than it first appears

### Post-call metering cannot enforce a hard limit

Consider an agent with **$0.10 left**. Three requests arrive simultaneously. Suppose each request can cost as much as $0.06.

A naïve implementation might do this independently in three workers:

```text
Worker A reads remaining = $0.10  → allowed
Worker B reads remaining = $0.10  → allowed
Worker C reads remaining = $0.10  → allowed

All three invoke LLM.

Actual cost = $0.18
```

The controller has now overspent even though every worker “checked the budget.”

That failure occurs because checking and charging were separate operations. The controller needs a concurrency invariant for every applicable scope \(s\):

\[
\text{committed}_s + \text{reserved}_s \le \text{limit}_s
\]

where:

- **committed** = cost already known from completed provider requests;
- **reserved** = worst-case authorized cost of requests currently in flight;
- **limit** = configured team, agent, session, or model allocation.

A request may reach the provider only after enough capacity has been **atomically reserved from all applicable scopes**.

For the example above, the first $0.06 reservation succeeds, leaving effectively $0.04 available. The other two reservations fail before either request reaches the LLM.

That is the central production-readiness property I would make explicit in the submission.

### Input cost can be known before generation

Current providers make preflight token measurement considerably easier than older gateway implementations.

OpenAI's Responses API exposes `POST /responses/input_tokens`. OpenAI says the endpoint accepts the same types of input used for Responses—including messages, files, images, tools, and conversations—and returns an accurate input-token count before generation. citeturn13search0turn15search6

Anthropic exposes a Messages token-counting endpoint supporting system prompts, tools, images, and documents. Anthropic describes the count as an estimate and notes that actual message input usage can differ slightly, which means a strict cost controller should retain a safety margin rather than assuming perfect equality. citeturn13search1turn15search1

Amazon Bedrock exposes model-aware token-counting functionality for Converse-style inputs, including messages, system content, and tool configuration. Bedrock's Converse response then reports actual `inputTokens`, `outputTokens`, and `totalTokens`. citeturn12search2turn12search0

This suggests clean provider adapter interfaces such as:

```python
class ProviderAdapter(Protocol):
    async def count_input_tokens(
        self,
        request: LLMRequest
    ) -> TokenEstimate:
        ...

    async def invoke(
        self,
        request: LLMRequest
    ) -> ProviderResponse:
        ...

    def extract_usage(
        self,
        response: ProviderResponse
    ) -> Usage:
        ...
```

The gateway should **not** base strict multimodal budget decisions on rough local heuristics such as characters divided by four. OpenAI specifically documents why local tokenizers become unreliable for structured messages, images, files, tools, and model-specific behavior. citeturn15search6

### Output cost is unknown, so output must be bounded

Counting the prompt only solves half of the problem. Before an LLM runs, the controller does not know how long its response will be.

Therefore, **strict pre-request budget enforcement is mathematically impossible unless the gateway can establish an upper bound on billable generation**.

The clean solution is to enforce a gateway-controlled output cap:

```text
reservation =
    maximum_input_charge
  + maximum_output_charge
  + predictable_tool_charges
  + applicable_pricing_surcharges
```

For OpenAI reasoning models, this is particularly important because generated reasoning tokens can consume output budget even though they are not all visible to the end user. OpenAI documents that `max_output_tokens` constrains the total generated tokens, including reasoning tokens, visible output, and other generated-token components. citeturn15search4 Anthropic similarly bills thinking as output tokens and exposes usage information for those generated tokens. citeturn15search9turn15search13

Consequently:

```python
effective_max_output = min(
    client_requested_max_output,
    policy_max_output,
    model_max_output
)
```

If the client sends no output limit, the gateway must inject one.

Otherwise a request with $0.10 remaining could theoretically generate substantially more than $0.10 in output charges after it has already been authorized.

### Make input and output token budgets first-class policy dimensions

The challenge wording refers to **token spend limits**, while its examples express budgets in dollars. The safest interpretation is to support **both monetary budgets and token quotas**. This strengthens the submission and prevents ambiguity during evaluation.

A budget policy can therefore contain one or more independent limits:

```yaml
budget:
  limit_usd: "50.00"
  max_total_tokens: 5000000
  max_input_tokens: 4000000
  max_output_tokens: 1000000
  window: monthly
```

The same structure can be applied at team, agent, and session scope. A request is authorized only if **every configured monetary and token constraint** can safely accommodate it.

For a strict controller, input and output accounting are intentionally asymmetric:

```text
BEFORE inference
  input tokens        = preflight counted / conservatively estimated
  output tokens       = unknown, therefore reserve effective_max_output
  dollar exposure     = worst-case price of those token classes + known surcharges

AFTER inference
  input tokens        = provider-reported actual input usage
  output tokens       = provider-reported actual output usage
  dollar charge       = provider-aware actual price calculation
  reservation         = reconciled to actual usage
```

For OpenAI Responses, the provider exposes a pre-generation input-token counting endpoint and returns usage containing `input_tokens`, `output_tokens`, and token-detail breakdowns after generation. Therefore the dashboard and immutable ledger should surface these fields explicitly rather than showing only a single `total_tokens` number.

Recommended ledger fields include:

```text
preflight_input_tokens
reserved_output_tokens
actual_input_tokens
actual_cached_input_tokens
actual_output_tokens
actual_reasoning_tokens (when provider exposes it)
total_billable_tokens / provider usage classes
input_cost_usd
output_cost_usd
tool_or_other_cost_usd
total_actual_cost_usd
```

A subtle accounting rule must be documented: provider token-detail fields are not always additive. For example, cached input tokens can be a **subset of total input tokens**. The cost engine must normalize provider usage into mutually exclusive billing buckets before multiplying by rates; otherwise the controller can double-count tokens and cost.

The budget state should therefore be able to track both money and tokens, for example:

```text
committed_nano_usd
reserved_nano_usd
committed_input_tokens
committed_output_tokens
reserved_output_tokens
```

The atomic authorization transaction can then enforce conditions such as:

```text
agent.committed_usd + agent.reserved_usd + request.reserve_usd <= agent.limit_usd
agent.committed_input_tokens + request.input_tokens <= agent.max_input_tokens
agent.committed_output_tokens + agent.reserved_output_tokens + request.max_output_tokens <= agent.max_output_tokens
```

This is also useful operationally: a runaway agent may be expensive because it makes too many calls, because its prompts grow recursively, because it generates unusually long outputs, or because it selects an expensive model. Separate input/output counters make those failure modes visible.

### The price catalog is a first-class subsystem

A production cost engine cannot merely contain:

```python
PRICE_PER_TOKEN = 0.00001
```

Contemporary provider billing can distinguish between input, cached input, output, cache creation/write behavior, reasoning, processing/service tier, long-context requests, and separately billed tools. For example, OpenAI currently exposes separate cached-input pricing, and GPT-5.6 documentation says cache writes are billed at a multiplier while prompts beyond a specified long-context threshold receive different token pricing. citeturn22search0turn15search8 Anthropic's prompt-caching accounting distinguishes normal input, cache reads, and cache creation, and its documentation explains how those token classes combine into the effective input total. citeturn15search3

I would therefore make pricing a versioned database entity:

```json
{
  "provider": "openai",
  "model": "gpt-5.6-terra",
  "effective_from": "2026-08-01T00:00:00Z",
  "currency": "USD",
  "input_per_million": "...",
  "cached_input_per_million": "...",
  "output_per_million": "...",
  "cache_write_multiplier": "...",
  "long_context_threshold": "...",
  "long_context_input_multiplier": "...",
  "long_context_output_multiplier": "...",
  "source_version": "...",
  "status": "ACTIVE"
}
```

Each ledger entry should record the **price-catalog version actually used**, because recalculating historical requests from today's price table could change historical spend figures when providers update prices.

For money, I would avoid binary floating-point entirely. Store a high-precision fixed unit such as **nano-USD** or use decimal arithmetic. For example:

```text
$1 = 1,000,000,000 nano-USD
```

This gives deterministic comparisons such as:

```text
remaining_nano_usd >= reservation_nano_usd
```

instead of risky floating-point comparisons around an enforcement boundary.

### Reserve, invoke, reconcile

The critical request lifecycle should be:

```text
REQUEST ARRIVES
      │
      ▼
Resolve authenticated tenant/team/agent/session
      │
      ▼
Check agent + session status
      │
      ├── PAUSED/CLOSED ───────────────► reject
      │
      ▼
Load routing policy + price catalog
      │
      ▼
Count input tokens for preferred model
      │
      ▼
Calculate conservative maximum request cost
      │
      ▼
Atomically reserve:
      team budget
      agent budget
      session budget
      preferred-model allocation
      │
      ├── fails ─► evaluate cheaper model
      │                  │
      │                  ├── reserve succeeds → invoke fallback
      │                  └── fails → reject
      │
      ▼
Invoke selected provider/model
      │
      ▼
Read ACTUAL usage from provider response
      │
      ▼
Calculate actual charge
      │
      ▼
Atomic reconciliation
 reserved -= reserved_request
 committed += actual_cost
 available += reservation - actual_cost
      │
      ▼
Evaluate 80% threshold / session closure
      │
      ▼
Write immutable usage event
      │
      ▼
Return model response + budget metadata
```

OpenAI exposes token-usage details including cached-token accounting; Anthropic Messages responses include `input_tokens` and `output_tokens`; and Bedrock Converse returns normalized token usage in its response, so post-call reconciliation can be based on provider-reported usage rather than the earlier estimate. citeturn15search0turn15search14turn12search0

### Failure states must also conserve money

A production system also has to answer: **what happens to a reservation if the gateway crashes after reserving money?**

The request record should therefore have a state machine:

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

Do not immediately release an ambiguous request after a socket timeout. The provider may have accepted and billed it even though the gateway failed to receive the completion. A conservative controller should temporarily retain the reservation until it can establish the outcome or until an operator-defined reconciliation procedure resolves it.

Similarly, provider retries should be tracked as **upstream attempts**, not blindly treated as free retries. Otherwise the gateway can undercount costs when the first request reached the provider but its response was lost.

## Recommended production architecture

For this challenge, I would implement the controller as a **long-running AWS gateway on ECS/Fargate rather than as one local script or purely a dashboarding service**.

A concrete deployable architecture is:

```text
                           INTERNET / VPC
                                │
                                ▼
                      AWS Application Load
                           Balancer
                                │
                     ┌──────────┴──────────┐
                     │                     │
                 /v1/* APIs           /healthz
                     │
                     ▼
            ┌─────────────────────┐
            │ ECS Fargate Service │
            │  Budget Gateway     │
            │                     │
            │ FastAPI / Go        │
            │ Provider adapters   │
            │ Budget engine       │
            │ Routing engine      │
            └─────┬─────────┬─────┘
                  │         │
          critical state    │ model calls
                  │         │
                  ▼         ▼
             DynamoDB    ┌────────────────────┐
                │        │ OpenAI             │
                │        │ Anthropic          │
                │        │ Amazon Bedrock     │
                │        └────────────────────┘
                │
        DynamoDB Streams
                │
                ▼
              Lambda
           ┌────┴─────┐
           │          │
           ▼          ▼
       Alerts      API Gateway
                   WebSocket
                       │
                       ▼
              Realtime Web UI
              S3 + CloudFront

Cross-cutting:
Secrets Manager
IAM task roles
CloudWatch / OpenTelemetry
KMS encryption
Terraform
```

### Where Langfuse fits in the architecture

**Langfuse should be included, but it should not become the enforcement source of truth.** It is most valuable here as the LLM-specific observability layer beside the budget controller. Langfuse supports traces and sessions, tracks usage/cost by usage type, exposes metrics for aggregated token/cost analysis, and can receive telemetry through its SDKs or OpenTelemetry. Its tracing pipeline is designed to export in the background, which is exactly why the hard financial authorization decision should remain synchronous in our gateway and DynamoDB transaction.

Recommended separation of responsibilities:

```text
                         CRITICAL ENFORCEMENT PATH
Agent request
    │
    ▼
Budget Gateway
    │
    ├── authenticate / resolve team-agent-session
    ├── count input tokens
    ├── reserve money + token exposure atomically ─────► DynamoDB
    ├── select preferred/fallback model
    ├── invoke provider
    └── reconcile provider-reported usage ─────────────► DynamoDB ledger
    │
    ▼
Response to agent

                         ASYNC OBSERVABILITY PATH
Budget Gateway / provider adapter
    │
    ├── OpenTelemetry / Langfuse SDK
    ▼
Langfuse
    ├── agent-run traces
    ├── session grouping
    ├── input/output token visibility
    ├── model and latency analysis
    ├── requested vs effective model
    ├── cost dashboards / monitors
    └── debugging and evaluation evidence

Infrastructure logs/metrics ───────────────────────────► CloudWatch
```

This creates three clearly separated layers:

1. **DynamoDB + gateway = financial authority.** If Langfuse is unavailable, budget enforcement must continue to work.
2. **Langfuse = LLM observability and forensic evidence.** It explains *why* an agent spent money and how a run behaved.
3. **CloudWatch/OpenTelemetry = infrastructure operations.** It covers service health, latency, errors, ECS behavior, and alarms.

For trace correlation, propagate the same stable identifiers across all systems:

```text
request_id       → gateway request + usage ledger + provider request metadata
session_id       → governance session + Langfuse session
agent_id         → Langfuse metadata/tag + budget scope
team_id          → Langfuse metadata/tag + budget scope
requested_model  → trace metadata
effective_model  → generation metadata
budget_decision  → allowed / warning / substituted / blocked / paused
```

For privacy, keep prompt/response capture **disabled or redacted by default** in the governance deployment. Langfuse can still receive token counts, cost, latency, model, agent/team/session identifiers, routing decisions, and error metadata. Content tracing can be enabled explicitly for approved environments.

A useful demo is to show the custom budget dashboard and then open the corresponding Langfuse session: the first proves hard spend governance; the second proves full LLM observability and explains the run.

### DynamoDB is a strong fit for the critical budget ledger

The key reason to use DynamoDB here is not merely scalability. It is **atomic multi-scope spending authorization**.

`TransactWriteItems` can group as many as 100 write actions into a synchronous all-or-nothing operation, permits conditions on writes, and rejects the whole transaction if a condition fails. DynamoDB also provides ACID guarantees for these transactions. citeturn20search0turn20search1

A single authorization transaction can therefore atomically modify:

```text
TEAM monthly budget
AGENT monthly budget
SESSION budget
MODEL allocation
REQUEST reservation record
```

with conditions conceptually equivalent to:

```text
team.available    >= reservation
agent.available   >= reservation
session.available >= reservation
model.available   >= reservation
agent.status      == ACTIVE
session.status    == OPEN
```

If another request modifies those counters concurrently, DynamoDB transaction conflicts cause a transaction cancellation rather than silently allowing inconsistent state. citeturn20search0 The gateway retries safe transaction conflicts with bounded exponential backoff, then rejects or returns a temporary-service response if it cannot obtain authorization.

An illustrative state item could look like:

```json
{
  "pk": "BUDGET#AGENT#agent-research-07",
  "sk": "WINDOW#MONTH#2026-08",
  "limit_nano_usd": 50000000000,
  "committed_nano_usd": 27453000000,
  "reserved_nano_usd": 130000000,
  "warning_80_sent": false,
  "window_start": "2026-08-01T00:00:00Z",
  "window_end": "2026-09-01T00:00:00Z",
  "status": "ACTIVE",
  "version": 438
}
```

For the $50 agent example, `limit_nano_usd` is $50 represented in fixed-point form.

### Do not use DynamoDB TTL as the budget-reset mechanism

This is a subtle but important production detail.

DynamoDB TTL is asynchronous: AWS states that expired items are generally deleted **within a few days** rather than exactly when their expiration timestamp occurs. citeturn19search0 Therefore this design would be wrong:

```text
At midnight:
"wait for previous monthly budget item to disappear via TTL"
```

Instead, make the **budget window part of the key**:

```text
BUDGET#TEAM#engineering / WINDOW#MONTH#2026-08
BUDGET#TEAM#engineering / WINDOW#MONTH#2026-09
```

At September's boundary, requests simply address a new deterministic September item.

TTL may later garbage-collect old state, but TTL should never determine whether a request is financially authorized.

### The budget database should have a single authoritative write region

DynamoDB global-table replication does not provide transaction atomicity across Regions; AWS states that transactional guarantees apply only inside the Region in which the original transaction occurs. citeturn19search10

For strict financial enforcement, therefore, I would initially deploy:

```text
Multi-AZ compute
       +
single authoritative AWS Region per tenant
```

rather than allowing active-active budget mutations in two Regions.

A later global enterprise version can assign each tenant a budget “home region” and fail traffic over deliberately, rather than permit two Regions to independently authorize the same remaining dollars.

### Proposed data model

A clean domain model is:

| Entity | Purpose |
|---|---|
| `BudgetPolicy` | Team/agent/session limits and windows |
| `BudgetState` | Current committed, reserved, available |
| `Session` | Session lifecycle and $2 example limit |
| `ModelAllocation` | Preferred-model sub-budget/fallback trigger |
| `RoutingPolicy` | Ordered same-provider fallback models |
| `RequestReservation` | Idempotency + outstanding authorized amount |
| `UsageLedger` | Immutable actual usage record |
| `PriceCatalog` | Versioned provider/model rates |
| `AlertEvent` | 80%, exhausted, runaway, substitution |
| `AgentState` | ACTIVE/PAUSED/review state |
| `AuditEvent` | Administrative budget/policy changes |

Example ledger event:

```json
{
  "request_id": "req_01K...",
  "team_id": "engineering",
  "agent_id": "agent-code-review",
  "session_id": "ses_9eb...",
  "provider": "openai",
  "requested_model": "gpt-5.6-terra",
  "effective_model": "gpt-5.6-luna",
  "decision": "MODEL_SUBSTITUTED",

  "preflight_input_tokens": 10342,
  "reserved_output_tokens": 4096,
  "input_tokens": 10342,
  "cached_input_tokens": 0,
  "output_tokens": 1874,
  "reasoning_tokens": 612,

  "reserved_nano_usd": 44000000,
  "actual_nano_usd": 4310800,

  "price_catalog_version": "openai-2026-08-01",
  "created_at": "2026-08-18T10:32:08.421Z",
  "completed_at": "2026-08-18T10:32:10.911Z"
}
```

The immutable ledger is useful both for audit and for rebuilding derived budget state if corruption or a pricing bug is discovered.

### Idempotency must exist above the database transaction

DynamoDB supports an idempotency token for `TransactWriteItems`, but AWS documents that its built-in client request token is valid for a limited time window. citeturn20search0

Therefore the public gateway should maintain its own durable request identifier:

```http
Idempotency-Key: 50da0fb0-47b6-4ac4-9dd4-587ec9d37b9e
```

and persist:

```text
tenant + idempotency_key → RequestReservation
```

A retry of the same logical model call must return or recover the same request state instead of reserving the budget twice.

## Enforcement, thresholds, sessions, and model substitution

### Budget configuration should support hierarchy without ambiguity

The challenge's examples translate naturally into configuration such as:

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

    session_budget:
      amount_usd: "2.00"

    routing:
      provider: openai
      preferred_model: gpt-5.6-terra

      model_allocations:
        gpt-5.6-terra:
          amount_usd: "40.00"
          window: monthly

      fallback_models:
        - gpt-5.6-luna

    runaway_detection:
      monthly_budget_percent: 20
      interval: 60m
      action: pause
```

An incoming request is governed by the intersection of its scopes:

```text
                      $500 team
                          │
                    $50 agent
                          │
                     $2 session
                          │
              preferred-model allowance
                          │
                       request
```

The request is legal only if it can safely fit into **every mandatory parent scope**.

### The 80% warning should be a state transition, not a polling alarm

A weak implementation has a cron job periodically ask:

```text
"Is any agent above 80%?"
```

That can warn late and can produce duplicate notifications.

Instead, after every reconciliation, calculate:

\[
\text{utilization}
=
\frac{\text{committed spend}}
     {\text{budget limit}}
\]

If it changes from below 80% to at least 80%, perform an atomic transition:

```text
warning_80_sent = false
        ↓
warning_80_sent = true
```

and emit:

```json
{
  "type": "budget.threshold_reached",
  "threshold": 80,
  "scope": "agent",
  "scope_id": "agent-code-review",
  "spent_usd": "40.16",
  "limit_usd": "50.00"
}
```

For operational visibility, the dashboard can also show **effective utilization**:

\[
\frac{\text{committed}+\text{reserved}}{\text{limit}}
\]

This warns operators that substantial money is currently in flight even though final provider usage has not yet reconciled.

The formal challenge test should use **committed actual spend** for the 80% warning, while effective utilization can be a secondary predictive metric.

### A hard limit should prevent the expensive call, not discover it afterwards

When insufficient available capacity remains:

```json
{
  "error": {
    "type": "budget_exhausted",
    "scope": "agent",
    "scope_id": "agent-code-review",
    "limit_usd": "50.00",
    "committed_usd": "49.91",
    "reserved_usd": "0.09",
    "available_usd": "0.00",
    "reset_at": "2026-09-01T00:00:00Z",
    "request_id": "req_01K..."
  }
}
```

Using HTTP `429` is defensible for a gateway designed to resemble provider APIs: OpenAI currently returns `429` for organization/project hard spend-limit exhaustion. citeturn18search1 The key is to include a machine-readable `budget_exhausted` reason so clients can distinguish financial exhaustion from ordinary request-per-minute throttling.

Most importantly:

```text
budget rejection
      ↓
NO provider API call occurs
```

That is what the success criterion should assert.

### Session budgets need explicit lifecycle state

The session object should have:

```text
OPEN
CLOSED_BUDGET
CLOSED_USER
CLOSED_ADMIN
EXPIRED
```

Suppose a session limit is $2.00 and it has $1.96 committed with no reservations.

A new request requires a worst-case $0.08.

The controller **must not invoke the model and discover that the session reached $2.04**.

Instead:

```text
remaining = $0.04
reservation = $0.08

reservation > remaining
        ↓
reject request
        ↓
atomically mark session CLOSED_BUDGET
```

This reveals a wording nuance in the challenge: a well-designed hard controller should never actually let the session “exceed” $2. It closes when the next request cannot safely fit, or immediately when reconciliation reaches the cap.

Any later request receives:

```json
{
  "error": {
    "type": "session_closed",
    "reason": "budget_exhausted",
    "session_id": "ses_9eb...",
    "limit_usd": "2.00"
  }
}
```

### Model substitution has an important logical trap

The problem statement says:

> when an agent's preferred model budget is exhausted, automatically reroute to a cheaper model

This cannot mean:

```text
Agent total budget = $50
Agent has already spent $50
Switch to cheaper model and spend another $1
```

That would violate the required $50 agent hard limit.

Therefore, the solution needs an additional distinction between:

```text
AGENT TOTAL BUDGET
and
PREFERRED MODEL ALLOCATION
```

For example:

```text
Agent total monthly cap         $50
Terra preferred-model cap       $40
Remaining fallback capacity     $10
```

At $40 of Terra spend:

```text
Terra allocation exhausted
        │
        ▼
Agent still has budget
        │
        ▼
Try Luna reservation
        │
        ├── fits agent/team/session → route Luna
        └── does not fit            → hard block
```

This both preserves budget correctness and exactly demonstrates the requested substitution behavior.

LiteLLM's current product documentation reflects a similar distinction by exposing per-model budgets and budget fallback chains, which reinforces that a model-level allocation is a sensible interpretation of the challenge. citeturn21search3 Portkey likewise supports user-plus-model grouped budget policies. citeturn21search1

### A current same-provider substitution example

A clean current OpenAI demonstration is a higher-cost GPT-5.6 tier falling back to **GPT-5.6 Luna**, which OpenAI describes as a cost-sensitive, high-volume model. Luna is currently listed at $0.20 per million text input tokens and $1.20 per million output tokens; OpenAI's current GPT-5.6 family guidance explicitly identifies Luna as the lowest-cost option for suitable reasoning workloads. citeturn22search0turn15search4

For an illustrative request with:

```text
10,000 uncached input tokens
2,000 maximum generated tokens
```

at Luna's listed token rates, the conservative text-only reservation is:

\[
10{,}000 \times \frac{\$0.20}{1{,}000{,}000}
+
2{,}000 \times \frac{\$1.20}{1{,}000{,}000}
\]

\[
= \$0.0020 + \$0.0024
= \boxed{\$0.0044}
\]

before any separately priced tools or applicable pricing modifiers. citeturn22search0

This is a useful demo because the dashboard can show, in real time:

```text
Requested model:   gpt-5.6-terra
Effective model:   gpt-5.6-luna
Decision:          SUBSTITUTED_BUDGET_PRESSURE
Reason:            preferred_model_allocation_exhausted
Reserved:          $0.0044
```

### Never substitute based only on price

A production router also needs a **capability check**.

Before moving from model A to cheaper model B, verify the fallback policy allows the request's:

```text
provider
modality
context length
tools
structured output
reasoning requirements
compliance/data-region policy
```

OpenAI's current model pages expose differing context, output, modality and tool-support characteristics, illustrating why cheaper cannot automatically mean interchangeable. citeturn22search0

The routing flow should therefore be:

```text
preferred model reservation fails
            │
            ▼
fallback belongs to same provider?
            │ no → block
            ▼ yes
supports required capabilities?
            │ no → next fallback / block
            ▼ yes
recount input for fallback model
            │
            ▼
recalculate fallback reservation
            │
            ▼
atomic budget reservation
            │
        ┌───┴────┐
        ▼        ▼
      route     block
```

Recounting matters because tokenization and request accounting can be model-specific; Anthropic, for example, documents token counting as model-aware and notes that actual usage may differ slightly from its preflight estimate. citeturn15search1

### Make substitutions visible

Silent substitution is dangerous for debugging. I would expose it in response metadata:

```http
X-Budget-Decision: substituted
X-Budget-Requested-Model: gpt-5.6-terra
X-Budget-Effective-Model: gpt-5.6-luna
X-Budget-Scope: preferred_model
```

as well as in the immutable ledger.

That lets application owners discover that quality or latency changed because governance rerouted traffic rather than because the original model behaved differently.

## Runaway-agent detection and the realtime control plane

### The bonus feature should be a circuit breaker, not just an alert

The problem asks to pause an agent when it consumes more than **20% of its monthly budget within one hour**.

For a $50/month agent:

\[
20\% \times \$50 = \boxed{\$10}
\]

So an agent spending more than $10 during the defined hour is suspicious.

I would interpret “within a single hour” in the production version as a **rolling 60-minute window**, because fixed calendar-hour buckets have a blind spot:

```text
11:50–11:59  spend $7
12:00–12:10  spend $7

Calendar-hour view:
$7 and $7

Rolling-60-minute view:
$14
```

The rolling version correctly notices the burst.

The event path can be:

```text
Usage reconciled
      │
      ▼
DynamoDB Stream
      │
      ▼
Runaway Detector Lambda
      │
      ▼
Update rolling agent spend
      │
      ▼
rolling_60m > 20% monthly limit?
      │
      ├── no → done
      │
      ▼
transaction:
agent.status = PAUSED
review_required = true
      │
      ├────────────► Slack / SNS / email
      │
      └────────────► Dashboard critical alert
```

The critical request path always checks `agent.status`, so as soon as `PAUSED` is durable:

```text
POST /v1/responses
        ↓
agent = PAUSED
        ↓
rejected before provider
```

### The detector should be idempotent

AWS documents that Lambda consumers of DynamoDB Streams use **at-least-once delivery**, meaning records can be processed more than once; AWS explicitly recommends idempotent consumers. citeturn19search1

Therefore events need stable IDs such as:

```text
usage_event_id = request_id + reconciliation_version
```

and the runaway detector must not add the same request twice to its rolling sum.

The dashboard updater needs the same protection.

### Human review needs a real administrative workflow

“Pause for human review” should not mean manually editing a DynamoDB row.

Expose:

```http
POST /admin/agents/{agent_id}/pause
POST /admin/agents/{agent_id}/resume
GET  /admin/agents/{agent_id}/runaway-events
```

A resume request should record:

```json
{
  "event": "agent.resumed",
  "agent_id": "agent-code-review",
  "actor": "alice@example.com",
  "reason": "Loop fixed in deployment 8f13c2",
  "previous_state": "PAUSED_RUNAWAY",
  "new_state": "ACTIVE",
  "timestamp": "..."
}
```

The audit history is especially valuable in the original scenario: an operator can answer not just **how much did this agent spend?**, but:

```text
When did acceleration begin?
Which request/session caused it?
Which model was involved?
When did the controller pause it?
Who resumed it?
Was the policy changed?
```

### Add secondary loop indicators as an extension

The mandated 20%-per-hour rule should remain deterministic, but the architecture leaves room for additional signals:

```text
requests per minute
tokens per minute
cost acceleration
same prompt hash repeating
same tool sequence repeating
same exception/retry repeating
session iteration count
```

The key is not to replace the explicit budget rule with an opaque anomaly model. For a governance system, the deterministic circuit breaker remains easy to explain and easy for judges to test.

Anthropic's recent **task budget** feature is interesting context here: its current beta lets an agent receive an advisory full-loop token budget covering thinking, tools, tool results, and output so that the model can self-regulate. Anthropic describes that mechanism as advisory rather than an infrastructure hard cap. citeturn15search16turn16search5 An external controller is still useful because a runaway model should not be trusted to be the sole enforcer of its own financial limit.

### The dashboard should distinguish spend from commitments

A strong realtime dashboard should not contain only one number called “Cost.”

For each team and agent, show:

```text
LIMIT                 $50.00
COMMITTED             $38.24
RESERVED / IN-FLIGHT   $0.71
AVAILABLE             $11.05
UTILIZATION             76.5%

STATUS                ACTIVE
WARNING               none
REQUESTS LAST HOUR      417
SPEND LAST HOUR        $8.31
INPUT TOKENS TODAY     1,842,110
OUTPUT TOKENS TODAY      214,802
RESERVED OUTPUT TOKENS    18,000
INPUT COST TODAY         $5.62
OUTPUT COST TODAY        $2.69
RUNAWAY THRESHOLD      $10.00

PRIMARY MODEL          gpt-5.6-terra
PRIMARY ALLOCATION     98.2%
FALLBACK               gpt-5.6-luna
SUBSTITUTIONS TODAY      31
```

This makes concurrency visible. An operator can see that, for example, $38 is actually billed while another $0.71 has already been committed to requests currently running.

API Gateway WebSocket APIs are bidirectional and allow the service to push messages to connected clients without waiting for another client request, making them suitable for this kind of live dashboard. citeturn19search2

A UI event could be:

```json
{
  "event_id": "evt_01K...",
  "type": "budget.state.updated",
  "scope": "agent",
  "agent_id": "agent-code-review",
  "version": 439,
  "committed_usd": "38.24",
  "reserved_usd": "0.71",
  "limit_usd": "50.00",
  "utilization_percent": 76.48,
  "status": "ACTIVE"
}
```

The browser can discard any event whose `version` is not newer than its current state, providing straightforward duplicate/out-of-order protection around the event stream.

## API surface, security, and operational readiness

### Expose both governance APIs and provider-compatible inference APIs

A production gateway benefits from two layers.

The control plane manages configuration:

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

GET    /healthz
GET    /readyz
```

The data plane proxies model calls:

```text
/v1/responses
/v1/chat/completions
```

and, as integration breadth increases:

```text
/anthropic/v1/messages
/bedrock/converse
```

This lets an existing agent change primarily its base URL rather than rewrite its complete model integration.

### Do not trust an agent to identify itself

This would be insecure:

```http
X-Agent-ID: cheap-agent
```

if the caller can simply replace it with another agent ID.

The gateway should map a trusted credential to governance identity server-side:

```text
API key / JWT subject / workload identity
                  ↓
            tenant identity
                  ↓
              team_id
                  ↓
             agent_id
```

The application may send a session identifier, but authorization must verify that the session belongs to the authenticated agent.

Provider credentials should likewise remain server-side. OpenAI explicitly recommends routing API traffic through a backend rather than putting provider keys in client environments where they could be extracted and abused. citeturn18search12

On AWS, the design would put third-party provider secrets in Secrets Manager and use an ECS task IAM role for AWS-native calls such as Bedrock, rather than storing keys in application configuration.

### Health and readiness should mean different things

I would use:

```text
GET /healthz
```

for “the process is alive” and:

```text
GET /readyz
```

for “this instance is ready to govern traffic.”

Readiness should verify at minimum:

```text
price catalog loaded
budget store reachable
required configuration loaded
provider adapters initialized
```

It should not make a billable LLM call on every health probe.

### Logging should be structured around a request ID

An example log:

```json
{
  "level": "INFO",
  "event": "budget.request.reconciled",
  "request_id": "req_01K...",
  "team_id": "engineering",
  "agent_id": "code-review",
  "session_id": "ses_...",
  "provider": "openai",
  "requested_model": "gpt-5.6-terra",
  "effective_model": "gpt-5.6-luna",
  "decision": "substituted",
  "input_tokens": 10342,
  "output_tokens": 1874,
  "reserved_usd": "0.0440",
  "actual_usd": "0.00431",
  "latency_ms": 2490
}
```

Recommended operational metrics include:

```text
budget_requests_total
budget_rejections_total{scope=...}
budget_substitutions_total
budget_warning_events_total
budget_reservation_conflicts_total
budget_reservations_outstanding
budget_reconciliation_failures_total

llm_cost_usd_total{provider,model,agent}
llm_input_tokens_total
llm_output_tokens_total
llm_request_latency_seconds

runaway_agent_pauses_total
active_sessions
closed_budget_sessions_total
```

### Prompt content does not need to be retained for budget governance

Budget governance usually needs:

```text
request ID
agent/session/team identity
model
token usage
monetary cost
routing decision
timestamps
status
```

rather than the user's full prompt and model answer.

I would therefore make prompt/response persistence **off by default**, with optional explicitly configured encrypted content logging for customers that genuinely need it. That reduces the amount of sensitive model content copied into the governance plane.

### Provider-side limits remain useful as defense in depth

The custom gateway should not replace provider-side spending controls.

For an OpenAI deployment, for example:

```text
Agent controller:       precise team/agent/session enforcement
Provider project cap:   emergency upper boundary
Organization hard cap:  enterprise-wide last resort
```

OpenAI's hard project/organization limits are useful as a secondary safety mechanism even though OpenAI warns that their enforcement can propagate with a small overshoot. citeturn18search1

Similarly, Anthropic's organization spend cap provides an outer financial boundary independently from the gateway. citeturn16search0

This produces multiple failure barriers:

```text
Runaway agent
     │
     ├─► session cap
     ├─► agent cap
     ├─► runaway pause
     ├─► team cap
     ├─► provider project/workspace cap
     └─► provider organization/account cap
```

## Validation strategy and success criteria

The project's strongest part can be its test suite. The tests should demonstrate **financial invariants under race conditions**, not merely successful API responses.

### Concurrent three-agent test

Create:

```text
Team Engineering:         $1.00 test budget

Agent A:                  $0.50
Agent B:                  $0.50
Agent C:                  $0.50
```

Then concurrently submit real model calls through all three agents.

The assertions should include:

```text
∀ scopes:
committed + reserved <= limit

team committed ==
sum(actual usage governed by that team)

each agent committed ==
sum(actual usage for that agent)

outstanding reservation count == 0
after all calls settle
```

Most importantly, drive the team sufficiently close to exhaustion that multiple requests race for the final available amount. Exactly those races validate the reason for using an atomic transaction. DynamoDB transactions execute the grouped updates atomically rather than allowing only some budget items to change. citeturn20search0turn20search1

### Input/output token accounting test

Add one explicit acceptance test for the token layer so the submission cannot be interpreted as dollar-only accounting.

Configure an agent with both monetary and token constraints, then submit requests whose prompts and output caps are known. Assert that:

```text
preflight input count is persisted
reserved output tokens == gateway-controlled max_output_tokens
provider-reported input/output usage is persisted after completion
cached/reasoning token details are normalized without double counting
input + output dollar components reconcile to total request cost
configured token quota can independently reject a request even when dollar budget remains
blocked token-quota requests do not invoke the provider
```

The dashboard should expose at minimum **Input Tokens**, **Output Tokens**, **Total/Effective Tokens**, **Input Cost**, **Output Cost**, and **Reserved Output Exposure** per agent/session.

### Warning test

Configure:

```text
Agent limit: $0.10
warning:     80%
```

Generate enough actual usage to cross:

```text
$0.079 → no warning
$0.081 → one warning
$0.090 → still one warning
```

Assert:

```text
warning event count == 1
warning_80_sent == true
```

A concurrency variant should have several responses finish near the threshold simultaneously and still produce exactly one durable alert.

### Hard-cap race test

This is arguably the most important acceptance test.

Suppose:

```text
remaining budget = $0.05
each concurrent request reserves $0.04
concurrent requests = 10
```

Expected outcome:

```text
1 authorized
9 rejected
```

not:

```text
10 authorized
then dashboard reports overspend
```

Instrument the provider adapter and assert that blocked requests **never invoke the upstream provider**.

### Session closure test

Configure:

```text
session limit = $0.02
```

Drive session spend until the next reservation does not fit.

Verify:

```text
session.status == CLOSED_BUDGET

subsequent request:
HTTP 429
type = session_closed
reason = budget_exhausted

upstream provider call count does not increase
```

### Model-substitution test

Configure:

```text
agent total budget       = $0.10
preferred-model budget   = $0.02
fallback                 = cheaper same-provider model
```

Consume the $0.02 preferred allocation while leaving sufficient agent/team/session capacity.

Next request should produce:

```text
requested_model != effective_model

decision =
SUBSTITUTED_PREFERRED_MODEL_BUDGET

actual request is observed
at the fallback provider model
```

Then exhaust the **agent's total budget** and ensure the controller does not continue substituting. That second assertion proves the implementation understands hierarchy rather than treating fallback as a way around the budget.

### Runaway test

For:

```text
monthly agent budget = $10
20% runaway threshold = $2
```

Generate more than $2 of governed spend inside the configured 60-minute window.

Expected:

```text
agent.status = PAUSED_RUNAWAY
review_required = true

runaway alert = emitted

all following model requests =
rejected before provider
```

Then:

```text
POST /admin/agents/{id}/resume
```

with authorized reviewer credentials and verify that a permanent audit event records the intervention.

### Crash and retry tests

Production readiness requires additional failure injection:

| Failure | Required behavior |
|---|---|
| Crash before reservation | No state change |
| Crash after reservation, before provider | Reservation recovered/released |
| Provider deterministic rejection | Reservation released |
| Provider success | Actual usage reconciled |
| Provider timeout with uncertain outcome | Reservation not prematurely released |
| Duplicate client request | No duplicate logical charge |
| Concurrent transaction conflict | Retry safely |
| Duplicate DynamoDB Stream event | No duplicate alert/spend |
| Dashboard reconnect | State reconstructed from database |
| Gateway process restart | Budget state preserved |
| September monthly boundary | New budget period used immediately |
| Expired TTL item still physically present | Does not affect current period |

The last test is especially worthwhile because DynamoDB explicitly does not guarantee immediate TTL deletion. citeturn19search0

### A concrete demo that would communicate production readiness

A compelling evaluation sequence would be:

```text
Dashboard:
Engineering Team
$398 / $500

Agent 01   $31 / $50
Agent 02   $39 / $50
Agent 03   $12 / $50
```

Start concurrent traffic.

Then visibly cross:

```text
Agent 02
$40.12 / $50
80.24%
WARNING
```

Continue preferred-model calls until:

```text
Preferred model allocation:
$40 / $40
EXHAUSTED
```

The next request visibly changes:

```text
requested: gpt-5.6-terra
effective: gpt-5.6-luna
decision:  budget substitution
```

Create a $2 session and consume it:

```text
SESSION CLOSED — BUDGET EXHAUSTED
```

Then launch a deliberately looping test agent until its rolling-hour expenditure crosses 20%:

```text
Agent 03
RUNAWAY SPEND DETECTED

STATUS: PAUSED
HUMAN REVIEW REQUIRED
```

Finally, show a direct request from that agent failing at the gateway **while the other agents continue operating**.

That proves isolation and enforcement rather than merely drawing a graph.

## Competitive positioning and recommended implementation

The market research changes how I would position this submission.

OpenAI already has organization/project monthly hard spend controls, although it explicitly says they may slightly overshoot while state propagates. citeturn18search1 Anthropic already has monthly spend controls, and in 2026 added an advisory task-budget mechanism for full agentic loops. citeturn16search0turn15search16 LiteLLM already advertises team and agent/session budgets and now documents model-budget fallback behavior. citeturn21search0turn21search3 Portkey already supports sophisticated pre-provider budget policies including combinations such as user × model and team × provider. citeturn21search1turn21search4 Helicone supports cost-oriented limits and alerts as well. citeturn21search2turn21search5 Langfuse already provides rich LLM traces, sessions, token/cost tracking, metrics, dashboards, and alerts; in this design it is deliberately integrated as the **observability layer**, while the custom gateway remains the synchronous financial authorization layer.

So the project should **not** claim:

> “Nobody can enforce LLM budgets today.”

That would be inaccurate in 2026.

A much stronger claim is:

> **Agent Budget Controller is an AWS-native reference architecture for deterministic hierarchical AI spend authorization. Unlike post-hoc metering, it reserves the worst-case cost of every request atomically across team, agent, session, and model allocations before inference, then reconciles provider-reported usage after completion.**

That is technically substantive.

### Recommended technology stack

For the challenge as written, my preferred build is:

| Layer | Recommendation | Reason |
|---|---|---|
| API/gateway | Python FastAPI | Fast implementation, async provider SDK ecosystem |
| Runtime | ECS Fargate | Long-lived HTTP/streaming gateway |
| Load balancing | ALB | Production HTTP entry point |
| Critical budget state | DynamoDB | Conditional atomic multi-item transactions |
| Event propagation | DynamoDB Streams | Budget state change feed |
| Event processor | Lambda | Alerts/dashboard projections |
| Dashboard realtime | API Gateway WebSocket | Server-pushed updates; AWS supports bidirectional WebSocket APIs. citeturn19search2 |
| Dashboard | React/Next.js | Admin UI |
| Static hosting | S3 + CloudFront | Simple managed frontend hosting |
| Providers | OpenAI + Bedrock initially | Demonstrates direct provider plus AWS-native provider |
| Additional integration | Anthropic | Strong breadth extension |
| Secrets | AWS Secrets Manager | Separate third-party credentials from app image |
| AWS model credentials | ECS task IAM role | No static Bedrock key |
| Metrics/logging | CloudWatch + OpenTelemetry | Production infrastructure observability |
| LLM observability | Langfuse via OpenTelemetry / SDK | Agent/session traces, explicit input/output token and cost evidence, model/routing analysis; **not** the enforcement source of truth |
| Infrastructure | Terraform | Reproducible cloud deployment |
| CI/CD | GitHub Actions → ECR → ECS | Repeatable deployments |

For the first production-quality submission, **OpenAI + Bedrock** is enough to prove provider abstraction. Anthropic is a valuable third adapter because its current preflight token-counting and usage semantics differ enough to demonstrate that the price/metering layer is genuinely provider-aware. OpenAI provides pre-generation input counting through Responses; Anthropic provides token counting for rich Messages inputs; Bedrock Converse provides normalized usage after generation. citeturn15search6turn13search1turn12search0

### Suggested repository structure

```text
agent-budget-controller/
│
├── apps/
│   ├── gateway/
│   │   ├── api/
│   │   ├── auth/
│   │   ├── budgets/
│   │   ├── metering/
│   │   ├── routing/
│   │   ├── providers/
│   │   │   ├── openai.py
│   │   │   ├── bedrock.py
│   │   │   └── anthropic.py
│   │   ├── pricing/
│   │   ├── runaway/
│   │   ├── ledger/
│   │   └── observability/
│   │
│   ├── stream_processor/
│   └── dashboard/
│
├── infrastructure/
│   └── terraform/
│       ├── ecs/
│       ├── dynamodb/
│       ├── alb/
│       ├── lambda/
│       ├── websocket/
│       ├── cloudfront/
│       ├── iam/
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
├── docker/
├── docker-compose.yml
├── Dockerfile
└── README.md
```

### The minimum production bar

A submission I would consider genuinely production-oriented rather than prototype-oriented should have all of these functioning together:

**Cloud-hosted inference path.** A real externally or VPC-accessible ECS gateway governs actual OpenAI and/or Bedrock requests.

**Atomic preauthorization.** Every billable request first reserves budget from team, agent, and session using one transactional decision.

**Durable state.** Killing every ECS task does not reset an agent's budget.

**Actual reconciliation.** Provider usage replaces estimated/reserved spending after completion.

**Explicit token governance.** Input and output token usage are tracked separately, token quotas can be configured alongside USD budgets, and cached/reasoning/provider-specific usage classes are normalized without double counting.

**LLM observability integration.** Langfuse receives correlated agent/session traces and token/cost metadata asynchronously, while enforcement continues correctly if the observability backend is unavailable.

**Hard output bounds.** Clients cannot bypass spend protection by omitting an output-token ceiling.

**Real concurrency tests.** At least three agents simultaneously compete for a shared team budget without allowing aggregate reservations over the cap.

**Price-aware fallback.** An intentionally exhausted preferred-model allocation causes a verified same-provider fallback while still respecting parent budgets.

**Circuit breaking.** Runaway spend actually changes the agent to `PAUSED` and blocks future provider traffic.

**Realtime UI.** Operators can see committed versus reserved spending, warnings, substitutions, session closure, and pauses.

**Operational controls.** `/healthz`, `/readyz`, structured request IDs, CloudWatch logs/metrics, audit records, secret management, error handling, and infrastructure as code.

### What would make the submission stand out

The strongest technical story is not the dashboard. It is this invariant:

\[
\boxed{
\forall\text{ scopes }s,\qquad
\text{committed}_s+\text{reserved}_s\le\text{limit}_s
}
\]

and the fact that this remains true while three or three hundred workers concurrently authorize calls.

That gives the entire project a coherent architecture:

```text
                    BEFORE inference
                           │
                exact/model-aware input count
                           │
                 bounded output exposure
                           │
                   worst-case price
                           │
                atomic hierarchical reserve
                           │
                     ┌─────┴─────┐
                     │           │
                 authorized    denied
                     │           │
                     ▼           X
                  provider    no spend
                     │
                     ▼
                 actual usage
                     │
                     ▼
                    AFTER
                  reconcile
```

That is also where this solution meaningfully improves on the original failure scenario. A recursive loop can attempt fifty thousand requests, but the financial damage is constrained by a sequence of **pre-spend barriers**:

```text
             runaway loop begins
                    │
                    ▼
          session budget reached
                    │
          session CLOSED_BUDGET
                    │
        ┌───────────┴───────────┐
        │ other session?        │
        ▼                       │
   agent keeps spending         │
        │                       │
        ▼                       │
20% monthly budget / 60m        │
        │                       │
        ▼                       │
 AGENT PAUSED_RUNAWAY           │
        │                       │
        X                       │
 no further model calls         │
                                │
If detector somehow fails:      │
        │                       │
        ▼                       │
 agent hard budget              │
        │                       │
        X                       │
                                │
If hierarchy fails:             │
        │                       │
        ▼                       │
 team hard budget               │
        │                       │
        X                       │
                                │
Provider-side limits remain
as additional defense in depth
```

The engineering team therefore does **not** learn about a runaway agent when a cloud invoice appears a month later. The infrastructure makes continued spending impossible—or pauses the workload far earlier—while maintaining an auditable account of exactly what was authorized, what was actually spent, which model served each request, and why a request was warned, substituted, closed, paused, or rejected. That is the core distinction between **LLM cost observability** and a genuine **agent budget control plane**.
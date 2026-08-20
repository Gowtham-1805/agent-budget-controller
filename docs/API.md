# API Reference

Every route the gateway exposes, with exact request/response shapes taken
directly from `apps/gateway/src/abc_gateway/api/schemas.py` and `routes.py`.

**Money always crosses the wire as a decimal string** (`"50.00"`), never a JSON
number — JSON numbers are IEEE-754 doubles in most clients, and a budget limit
that drifts in transit during parsing is worse than useless. See
[DECISIONS.md #D2](DECISIONS.md#d2-money-is-an-integer-never-a-float).

Interactive docs are also available at `/docs` on any running instance
(FastAPI's generated Swagger UI) — useful for exploring live, this document is
useful for reading offline or diffing against a deployment.

---

## Authentication

Every route except `/healthz` and `/readyz` requires a credential — either a
bearer API key (an agent, or the bootstrap admin key):

```
Authorization: Bearer <key>
```

or a human session, carried as a cookie (`abc_dash_session`) or, for the
dashboard's own server-side proxy, an `X-ABC-Session` header. Identity
(`tenant → team → agent` for a key, `tenant → user → role` for a session) is
resolved **server-side** from this credential — no request field is ever
trusted to say who the caller is. See
[DECISIONS.md #D10](DECISIONS.md#d10-a-caller-never-asserts-its-own-identity).

A session presented on a mutating (non-GET/HEAD) request via the cookie also
needs a matching `X-ABC-CSRF` header (double-submit against the non-HttpOnly
`abc_dash_csrf` cookie set at login) — not required when the session arrives
via `X-ABC-Session`, since a custom header cannot be attached to a request by
a third-party page the way a cookie is attached automatically.

### Roles

Ordered `AGENT < VIEWER < OPERATOR < ADMIN`. Every check is "at least this
role", never "exactly this role". A human session's `subject_kind` is
`"user"` and it always carries `agent_id=""`, so it can never reach the data
plane (`/v1/chat/completions`, `/v1/responses`) regardless of role — those
require `subject_kind == "agent"`.

| Role | Can do |
|---|---|
| **AGENT** (an issued API key, or the bootstrap `ABC_ADMIN_API_KEY` with `is_admin=true`) | Inference and session management as that one agent (or, for the admin key, everything) |
| **VIEWER** | Read the control plane: teams, agents, sessions, events, ledger, providers, catalog |
| **OPERATOR** | VIEWER, plus create/update teams and agents, routing policy, and the Playground |
| **ADMIN** | Everything: pause/resume, mint agent keys, provider secrets, audit log, user management |

A route's handler calls `principal.require_role(Role.X)` (or
`principal.require_agent()` on the data plane); a caller below the floor gets
`403`.

---

## Human authentication (`/v1/auth/*`)

| Method | Path | Auth | Notes |
|---|---|---|---|
| `POST` | `/v1/auth/login` | none | `{email, password}` → sets session + CSRF cookies. Every failure mode (unknown email, wrong password, locked account) returns the identical generic `401` — see [DECISIONS.md](DECISIONS.md) and `auth/sessions.py`. |
| `POST` | `/v1/auth/logout` | session | Revokes the session; `204`. |
| `GET` | `/v1/auth/session` | session | Returns the caller's own identity. |
| `POST` | `/v1/auth/password` | session | Change password; revokes every other session for the account. |
| `POST` | `/v1/auth/admin/users` | ADMIN | Create a user. |
| `GET` | `/v1/auth/admin/users` | ADMIN | List users in the caller's tenant. |
| `PATCH` | `/v1/auth/admin/users/{user_id}` | ADMIN | Update role/status/password; revokes the user's sessions. |
| `POST` | `/v1/auth/admin/users/{user_id}/unlock` | ADMIN | Clear a durable login-failure lockout. |
| `DELETE` | `/v1/auth/admin/users/{user_id}/sessions` | ADMIN | Revoke every session for a user. |

Password reset by email and MFA are deliberately out of scope for now — no
SMTP client or second-factor enrolment flow exists; account recovery is
covered by an admin-set password via `PATCH .../admin/users/{user_id}`.

---

## Health and readiness

### `GET /healthz`

Liveness only — the process is running. **Checks nothing else, on purpose**: a
liveness probe that fails when a dependency is down gets the container killed and
restarted, which fixes nothing and turns a partial outage into a crash loop.

```json
{"status": "ok", "version": "1.0.0"}
```

### `GET /readyz`

Readiness — this instance is safe to govern traffic. Returns `503` if not ready.

```json
{
  "status": "ready",
  "checks": {
    "price_catalog_loaded": true,
    "budget_store_reachable": true,
    "providers_configured": true,
    "identity_configured": true
  },
  "detail": {
    "catalog_version": "2026-08-19.1",
    "providers": "test",
    "store": "memory"
  }
}
```

Never makes a billable provider call — a readiness probe that costs money on
every check is its own outage.

---

## Data plane — governed inference

### `POST /v1/chat/completions`

OpenAI-compatible shape, deliberately, so an existing agent adopts governance by
changing its base URL rather than rewriting its integration. `POST /v1/responses`
is an identical alias.

**Headers:**

```
Authorization: Bearer <agent-key>
Idempotency-Key: <uuid>          (optional but strongly recommended)
```

**Request body:**

```json
{
  "model": "gpt-4o",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "Review this diff"}
  ],
  "max_tokens": 512,
  "temperature": 0.7,
  "tools": [ ],
  "session_id": "ses_..."
}
```

`max_tokens` is the client's *preferred* ceiling. The gateway lowers it to
`min(client, policy, model)` and sends *that* to the provider — omitting it does
not mean "unbounded," it means "use the policy cap." See
[ARCHITECTURE.md](ARCHITECTURE.md#the-request-lifecycle).

**Success response — `200`:**

```json
{
  "id": "01M0C6XPBKY7CZ3SNZVMA7DXBD",
  "object": "chat.completion",
  "model": "premium",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "..."}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 1000, "completion_tokens": 1000, "total_tokens": 2000},
  "budget": {
    "decision": "ALLOWED",
    "requested_model": "premium",
    "effective_model": "premium",
    "substituted": false,
    "estimated_cost_usd": "0.040000",
    "actual_cost_usd": "0.040000",
    "estimated_savings_usd": null
  }
}
```

**Response headers** (present on every governed response, success or
substitution — never silent):

```
X-Budget-Decision: ALLOWED | SUBSTITUTED_PREFERRED_MODEL_BUDGET
X-Budget-Requested-Model: gpt-4o
X-Budget-Effective-Model: gpt-4o-mini
X-Request-Id: 01M0C6XPBKY7CZ3SNZVMA7DXBD
X-Budget-Actual-Cost-Usd: 0.040000
X-Budget-Scope: preferred_model_allocation          (only when substituted)
X-Budget-Estimated-Savings-Usd: 0.009000             (only when substituted)
```

---

## Errors

Every denial names the exact scope and shortfall. This is the difference between
an ordinary `429` (ambiguous — rate limit? budget?) and a machine-readable
`budget_exhausted` carrying enough information for a client to decide whether to
back off, escalate, or route elsewhere.

### Status code mapping

| `error.type` | HTTP | Meaning | Retryable? |
|---|---|---|---|
| `budget_exhausted` | `429` | The scope named has insufficient remaining balance | No — wait for `reset_at` |
| `token_quota_exceeded` | `429` | A token dimension (input/output/total) is exhausted, independent of money | No |
| `session_closed` | `429` | The session has transitioned out of `OPEN` | No — open a new session |
| `session_expired` | `429` | The session's TTL passed | No |
| `agent_paused` | `423` | Administratively unavailable; a human must resume it | No, ever — requires `POST .../resume` |
| `exceeds_window_limit` | `422` | The request alone is larger than the scope's *entire* limit | No — shrink the request |
| `no_eligible_model` | `422` | No candidate in the routing chain was eligible | No |
| `idempotency_key_reuse` | `422` | Same key, different request body | No — use a new key |
| `idempotent_request_in_flight` | `409` | A logically identical request is still being processed | Yes, after `Retry-After` |
| `idempotent_request_unresolved` | `409` | The original request's provider outcome is still ambiguous | Yes, after `Retry-After` |
| `transient_contention` | `503` | Nothing was decided; the budget store lost a race internally | Yes, after `Retry-After` |
| `provider_rejected` | `502` | Provider proven not to have billed us | Depends on cause |
| `provider_unresolved` | `504` | Provider outcome ambiguous; reservation held pending | No — money is encumbered until resolved |

`429` is used for budget exhaustion deliberately, to match what LLM providers
themselves return for spend limits — existing client retry/backoff logic behaves
sensibly against it. The `type` field is what carries the real meaning.

### Error body shape

```json
{
  "error": {
    "type": "budget_exhausted",
    "message": "AGENT#code-review has $0.020000 available but this request requires $0.040000; no provider call was made",
    "scope": "agent",
    "scope_id": "code-review",
    "limit_usd": "0.100000",
    "committed_usd": "0.080000",
    "available_usd": "0.020000",
    "requested_usd": "0.040000",
    "reset_at": "2026-09-01T00:00:00+00:00",
    "request_id": "01M0C6XPK59WS155C9JZ3TK42Y"
  }
}
```

`reset_at` tells the caller when capacity returns, so it can wait rather than
retry-storm a budget that cannot possibly admit it yet. Every field is
`str | None` — only the fields relevant to that particular denial are populated
(see `ErrorBody` in `schemas.py`).

### `token_quota_exceeded` carries which dimension bound the request

```json
{
  "error": {
    "type": "token_quota_exceeded",
    "token_dimensions": ["output"],
    ...
  }
}
```

---

## Idempotent replay

A repeated `Idempotency-Key` for a request that already settled returns the
**original** response, verbatim, without invoking the provider a second time:

```
Idempotent-Replay: true
```
```json
{"replayed": true, "reservation_id": "...", "effective_model": "...", "state": "RECONCILED"}
```

---

## Control plane — teams

### `POST /v1/teams` — admin only, `201`

```json
{"team_id": "engineering", "budget": {"amount_usd": "500.00", "window": "MONTHLY", "warning_percent": 80, "billing_tz": "UTC"}}
```

`window` is one of `DAILY | WEEKLY | MONTHLY`. `tokens` is optional:
`{"max_input_tokens": ..., "max_output_tokens": ..., "max_total_tokens": ...}`.

### `PUT /v1/teams/{team_id}/budget` — admin only

Same `BudgetSpec` body as above.

---

## Control plane — agents

### `POST /v1/agents` — admin only, `201`

```json
{
  "agent_id": "code-review",
  "team_id": "engineering",
  "budget": {"amount_usd": "50.00", "window": "MONTHLY"},
  "session_budget_usd": "2.00",
  "session_min_viable_usd": null,
  "default_max_output_tokens": 4096,
  "routing": {
    "provider": "openai",
    "preferred_model": "gpt-4o",
    "fallback_models": ["gpt-4o-mini"],
    "allocations": [
      {"provider": "openai", "model": "gpt-4o", "amount_usd": "40.00"}
    ],
    "require_same_provider": true,
    "allow_fallback": true
  },
  "runaway": {"monthly_budget_percent": 20, "interval_minutes": 60, "enabled": true}
}
```

The allocation is a *sub*-budget of the agent — see
[DECISIONS.md #D7](DECISIONS.md#d7-model-allocation-is-a-sub-budget-not-a-parallel-budget).

### `PUT /v1/agents/{agent_id}/budget` — admin only

Same `BudgetSpec` body as team budgets.

### `PUT /v1/agents/{agent_id}/routing-policy` — admin only

Same `routing` object shape as in `CreateAgentRequest`.

### `POST /v1/agents/{agent_id}/keys` — admin only, `201`

Mints an API credential bound to exactly one agent. **Returned once** — only its
hash is retained server-side; there is no endpoint that reads it back.

```json
{"agent_id": "code-review", "key_id": "28ab0923facf", "api_key": "abc_8d3ccf..."}
```

---

## Control plane — sessions

Session budgets are lifecycle-scoped, not calendar-scoped — they never reset,
they *close*. See [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md#the-budget-hierarchy).

### `POST /v1/sessions` — any authenticated agent, `201`

```json
{"session_id": null, "ttl_seconds": 86400}
```

`session_id` is optional — omit it to have the gateway generate one. The session
is bound to whichever agent's credential created it; another agent's credential
cannot read or use it (`403`).

**Response:**

```json
{"session_id": "ses_...", "agent_id": "code-review", "status": "OPEN", "limit_usd": "2.000000", "committed_usd": "0.000000", "available_usd": "2.000000", "close_reason": null}
```

### `GET /v1/sessions/{session_id}`

Same shape. `status` is one of `OPEN | CLOSED_BUDGET | CLOSED_USER |
CLOSED_ADMIN | EXPIRED`.

### `POST /v1/sessions/{session_id}/close`

Voluntary close (`close_reason: "user_requested"`), distinct from the automatic
`CLOSED_BUDGET` transition.

---

## Control plane — budgets and ledger

### `GET /v1/budgets/{scope}/{scope_id}?window=MONTHLY`

`scope` is one of `TEAM | AGENT | SESSION | ALLOC` (case-insensitive).

```json
{
  "scope_type": "AGENT",
  "scope_id": "code-review",
  "window": "WINDOW#MONTH#2026-08",
  "limit_usd": "50.000000",
  "committed_usd": "38.240000",
  "reserved_usd": "0.710000",
  "pending_usd": "0.000000",
  "available_usd": "11.050000",
  "overage_usd": "0.000000",
  "utilization_percent": 76,
  "effective_utilization_percent": 78,
  "warning_sent": false,
  "open_reservations": 2,
  "reset_at": "2026-09-01T00:00:00+00:00",
  "input_tokens": 1842110,
  "output_tokens": 214802
}
```

`committed` vs. `reserved` is the distinction that makes concurrency visible — a
scope can be 76% settled and 78% effective, meaning almost all remaining budget
is already promised to in-flight requests. See
[ARCHITECTURE.md](ARCHITECTURE.md#the-request-lifecycle).

### `GET /v1/ledger?agent_id=...&limit=50`

**`agent_id` is required for an admin credential.** An admin has no ledger of its
own — omitting it returns `422`, not a silently empty result. See
[FINDINGS.md #F3](FINDINGS.md#f3-an-admin-ledger-query-silently-answered-a-different-question).
A non-admin (agent) credential defaults sensibly to its own ledger when
`agent_id` is omitted.

```json
{
  "entries": [
    {
      "entry_id": "01M0C6XPBNY82TE2VBAJBJV2FN#0",
      "request_id": "01M0C6XPBNY82TE2VBAJBJV2FN",
      "agent_id": "proof-agent",
      "session_id": null,
      "provider": "test",
      "requested_model": "premium",
      "effective_model": "premium",
      "decision": "ALLOWED",
      "kind": "USAGE",
      "preflight_input_tokens": 1000,
      "reserved_output_tokens": 1000,
      "actual_input_tokens": 1000,
      "actual_output_tokens": 1000,
      "actual_cached_input_tokens": 0,
      "actual_reasoning_tokens": 0,
      "estimated_max_cost_usd": "0.040000",
      "actual_total_cost_usd": "0.040000",
      "price_catalog_version": "2026-08-19.1",
      "created_at": "2026-08-19T05:11:52.949442+00:00",
      "completed_at": "2026-08-19T05:11:52.949442+00:00"
    }
  ]
}
```

`kind` is one of `USAGE | RELEASE | PENDING_ASSUMED | CORRECTION | OVERAGE`.
Every entry pins `price_catalog_version` — reconciliation always prices at the
version pinned on the *reservation*, never the currently-active one.

---

## Admin — human review

Pause/resume is a real audited workflow, never a database edit. A reason is
**required** to resume — restoring a runaway agent to service without recording
why is how the same loop ships twice.

### `POST /v1/admin/agents/{agent_id}/pause` — admin only

```json
{"reason": "suspected prompt injection", "actor": null}
```

### `POST /v1/admin/agents/{agent_id}/resume` — admin only

```json
{"reason": "investigated; false positive"}
```

Empty reason → `422`.

**Both return an `AuditRecordResponse`:**

```json
{
  "actor": "bootstrap-admin",
  "action": "agent.paused",
  "target": "proof-agent",
  "previous_state": "ACTIVE",
  "new_state": "PAUSED_ADMIN",
  "reason": "suspected prompt injection",
  "timestamp": "2026-08-19T13:26:34.485857+00:00"
}
```

### `GET /v1/admin/agents/{agent_id}/runaway-events` — admin only

Every automatic circuit-breaker trip for this agent.

### `GET /v1/admin/audit` — admin only

The full permanent audit trail for the tenant — every pause and resume, with
actor, reason, and the before/after state transition.

---

## curl reference

```bash
GW=http://127.0.0.1:8080
ADMIN=local-admin-key

# Provision
curl -X POST "$GW/v1/teams" -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"team_id":"engineering","budget":{"amount_usd":"500.00"}}'

curl -X POST "$GW/v1/agents" -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"agent_id":"code-review","team_id":"engineering","budget":{"amount_usd":"50.00"},"routing":{"provider":"openai","preferred_model":"gpt-4o"}}'

# Issue the agent its own credential
curl -X POST "$GW/v1/agents/code-review/keys" -H "Authorization: Bearer $ADMIN"

# Governed inference, as that agent
AGENT_KEY=abc_...
curl -X POST "$GW/v1/chat/completions" -H "Authorization: Bearer $AGENT_KEY" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: 50da0fb0-47b6-4ac4-9dd4-587ec9d37b9e' \
  -d '{"messages":[{"role":"user","content":"hello"}],"max_tokens":512}'

# Inspect
curl "$GW/v1/budgets/AGENT/code-review" -H "Authorization: Bearer $ADMIN"
curl "$GW/v1/ledger?agent_id=code-review" -H "Authorization: Bearer $ADMIN"

# Human review
curl -X POST "$GW/v1/admin/agents/code-review/pause" -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' -d '{"reason":"investigating a cost spike"}'
curl -X POST "$GW/v1/admin/agents/code-review/resume" -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' -d '{"reason":"confirmed the loop was fixed"}'
```

---

## Further reading

- [ARCHITECTURE.md](ARCHITECTURE.md) — how a request flows through the gateway
- [OPERATIONS.md](OPERATIONS.md) — running this locally and against a deployment

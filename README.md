# Agent Budget Controller

**A financial authorization gateway for AI inference.**

Not a cost dashboard. A request cannot reach an LLM provider unless its
worst-case cost has first been reserved, atomically, from every applicable
budget.

```
For every governed scope S:   committed_S + reserved_S  ≤  limit_S
```

...and that holds while many agents spend concurrently from overlapping
budgets, under real concurrent traffic — proven, not assumed.

---

## Why this exists

An agent has $0.10 left. Three requests arrive at once, each costing up to
$0.06. Every one of them checks the budget, sees $0.10, and proceeds — because
*checking* and *charging* were separate operations, and three workers slipped
between them. Actual spend: $0.18. Every dashboard says it never happened until
the invoice arrives.

The fix is to make checking and charging one atomic operation:

```
count → bound → estimate → RESERVE → invoke → reconcile
```

Everything in this repository follows from that requirement. The full argument
is in [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) — start there.

---

## Quick start

```bash
make setup          # venv + pinned dependencies
cp .env.example .env
make test           # 214 tests, ~30s, no network, no spend
make run            # gateway on http://localhost:8080
```

Then run the nine-step demo against it — normal traffic, the 80% warning, model
substitution, the hard cap with a zero-provider-call proof, session closure, a
runaway pause, an audited resume, and the ledger showing estimate beside actual:

```bash
PYTHONIOENCODING=utf-8 \
GATEWAY_URL=http://127.0.0.1:8080 \
ABC_ADMIN_API_KEY=local-admin-key \
python scripts/demo.py
```

Full runbook, Docker, and AWS deployment: [docs/OPERATIONS.md](docs/OPERATIONS.md).

---

## What's proven, right now

**214 tests pass** across seven suites in ~30 seconds. Four quality gates are
clean: `ruff`, `ruff format`, `mypy --strict`, `terraform validate`.

| | |
|---|---|
| Hard-cap race, 10 concurrent requests | ✅ exactly 1 authorized, provider invoked exactly once |
| Three-agent concurrency | ✅ invariant holds at every scope |
| 80% warning | ✅ exactly once, including under concurrent reconciliation |
| 100% hard block | ✅ `429`, provider invocation count unchanged |
| Session closure | ✅ both closure paths |
| Model substitution | ✅ capability-checked, verified cheaper, cannot escape the parent cap |
| Runaway detector | ✅ rolling window catches bursts calendar buckets miss |
| Both storage backends agree | ✅ 34 contract tests, identical assertions |

**Not verified:** AWS deployment, live provider E2E, and the container build —
each gated on credentials or tooling unavailable during development, reported
honestly rather than inferred. See
[docs/PROJECT_CONTEXT.md #what-has-not-been-proven](docs/PROJECT_CONTEXT.md#what-has-not-been-proven).

---

## Documentation

| Read this | To understand |
|---|---|
| **[docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)** | Start here — the problem, the guarantee, what's proven, five minutes |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How a request actually flows through the system, with diagrams |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | The exact DynamoDB schema, keys, and transactions |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Every load-bearing design choice, with its justification |
| [docs/FINDINGS.md](docs/FINDINGS.md) | Real bugs testing caught, and the discipline each one produced |
| [docs/API.md](docs/API.md) | Every endpoint, exact request/response shapes |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Run it, test it, deploy it, troubleshoot it |
| [docs/TESTING.md](docs/TESTING.md) | What each test suite proves, and how |
| [docs/spec/](docs/spec/) | The original requirements this was built from |
| [CLAUDE.md](CLAUDE.md) | Context for AI sessions working in this codebase |

Component-level docs: [apps/dashboard/README.md](apps/dashboard/README.md),
[infrastructure/terraform/README.md](infrastructure/terraform/README.md).

---

## Repository layout

```
apps/gateway/          FastAPI gateway — the enforcement engine
apps/stream_processor/ Lambda: runaway detection + threshold backstop
apps/dashboard/         Next.js operator UI
infrastructure/terraform/  VPC, ECS, ALB, DynamoDB, Lambda, IAM, KMS
infra/table_*.json      DynamoDB schema — shared by tests AND Terraform
pricing/catalog.json    Versioned, per-million-token price catalog
tests/                  unit · contract · concurrency · property · failure · acceptance · e2e
docs/                   everything above
```

---

## What this is not

This does not claim LLM budgets are a new idea — OpenAI, Anthropic, LiteLLM,
Portkey, and Helicone all offer some form of spend policy already, and Langfuse
does LLM observability better than anything here.

What's built here is the enforcement plane underneath: a deterministic,
hierarchical, reserve-before-inference authorization gateway whose invariant is
**proven under concurrency**, with the failure modes that actually bite —
ambiguous provider outcomes, duplicate stream delivery, crashed gateways,
window boundaries, provider overshoot — handled explicitly rather than hoped
away. See [docs/PROJECT_CONTEXT.md #what-this-is-not](docs/PROJECT_CONTEXT.md#what-this-is-not)
for the full positioning.

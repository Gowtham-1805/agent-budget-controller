# CLAUDE.md

Context for AI sessions working in this repository. Read this before touching
`apps/gateway/src/abc_gateway/engine/`, `repo/`, or `domain/` — that's where the
invariants below actually get enforced, and where a well-intentioned refactor
can silently break them.

For everything else — architecture, API, how to run it — see
[docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) first.

---

## The one invariant that must never break

```
For every governed scope S:   committed_S + reserved_S  ≤  limit_S
```

This must hold **under concurrent traffic**. If you touch anything in `engine/`
or `repo/`, re-run `tests/concurrency/` and `tests/property/` before considering
the change done — a subtle regression here does not show up as a test failure in
`tests/unit/`, it shows up as an occasionally-overspent budget that only appears
under load.

---

## Rules that must never be relaxed

These are not style preferences. Each one exists because relaxing it either
reintroduces a race condition or silently corrupts the books.

1. **Never use `float` for money.** `Money` (`domain/money.py`) is integer
   nano-USD. `Decimal` appears only at parsing/serialization boundaries —
   `pricing/loader.py` (parsing the catalog file), `money.py`'s
   `from_usd_str`/`to_usd_str` (display and input parsing), and
   `repo/dynamo/serde.py` / `repo/items.py` (DynamoDB returns numbers as
   `Decimal`, converted straight to `int`). It is never used for in-flight
   money arithmetic or a persisted value, and no `float()` call exists
   anywhere in `apps/gateway/src`.

2. **Never call a provider before a reservation is granted.** The lifecycle is
   `count → bound → estimate → RESERVE → invoke → reconcile`, always in that
   order. "Call the provider, then check the budget" is the exact failure mode
   this entire system exists to prevent.

3. **Never release a reservation on an ambiguous provider outcome.** A read
   timeout does not prove the provider didn't bill us — see
   [docs/DECISIONS.md #D5](docs/DECISIONS.md#d5-ambiguity-holds-the-money).
   Only `providers/classify.py`'s explicit allow-list may produce
   `FailedNotBilled`; everything else defaults to `FailedAmbiguous`, which holds
   the money as `RECONCILE_PENDING`.

4. **Never let a client bypass the output-token cap.** If a request omits
   `max_tokens`, the gateway must inject `min(policy_cap, model_cap)` and use
   *that* value both for the reservation and for the actual provider call.
   Without this, a reservation reserves nothing.

5. **Never trust a client-supplied identity.** Governance identity
   (`tenant/team/agent`) always comes from the resolved credential
   (`auth/identity.py`), never from a request header or body field. See
   [docs/DECISIONS.md #D10](docs/DECISIONS.md#d10-a-caller-never-asserts-its-own-identity).

6. **Never let a fallback model bypass a parent budget.** A model allocation is
   just another scope in the *same* reservation transaction as its parent agent.
   If you're adding new routing logic, keep it that way — see
   [docs/DECISIONS.md #D7](docs/DECISIONS.md#d7-model-allocation-is-a-sub-budget-not-a-parallel-budget).

7. **Never use TTL to decide authorization.** `housekeeping_ttl` is for garbage
   collection only. Budget windows are addressed by key
   (`WINDOW#MONTH#2026-08`), not by whether an old row has been deleted yet.

8. **Never clamp `remaining` at zero.** It is allowed to go negative. Clamping
   erases the evidence of an overage and breaks the accounting identity — see
   [docs/DECISIONS.md #D6](docs/DECISIONS.md#d6-overage-is-recorded-honestly-not-hidden).

9. **Never let observability block enforcement.** Langfuse, CloudWatch metrics,
   and the dashboard's WebSocket feed are fire-and-forget. If you're adding a
   new telemetry call, it must not be `await`-ed on the response path.

10. **Never write to `abc_ledger` with `UpdateItem` or `DeleteItem`.** The
    ledger is append-only by IAM policy, not just convention
    (`infrastructure/terraform/iam.tf`). If application code ever needs
    `UpdateItem` on that table, something upstream is architecturally wrong —
    corrections are new entries with `corrects_entry_id`, never edits.

---

## The two-backend contract

`engine/` must never import from `repo/dynamo/`. It builds a
`TransactionPlan` (`repo/plans.py`) and hands it to whichever backend implements
`BudgetRepository`. If you add a new repository method, implement it on **both**
`repo/memory/budget_repo.py` and `repo/dynamo/budget_repo.py`, and add a test to
`tests/contract/test_repository_contract.py` so it's proven identical on both.

Slot order in a `TransactionPlan` is load-bearing — DynamoDB's
`CancellationReasons` are positionally aligned with the submitted actions.
Reordering slots silently misattributes denials to the wrong scope.

---

## Testing discipline

**A concurrency test that has never been observed to fail is not evidence.**
The original hard-cap race test passed with `asyncio.gather` while being
completely vacuous — the coroutines never actually overlapped. See
[docs/FINDINGS.md #F1](docs/FINDINGS.md#f1-the-hard-cap-race-test-was-vacuous--asynciogather-does-not-test-concurrency).

If you write or modify a concurrency-sensitive test:

1. Use `tests/support/concurrency.py`'s `run_concurrently` (real OS threads) or
   `PhaseGate` (forced pathological schedule). Never `asyncio.gather` over code
   whose critical section has no `await`.
2. **Validate it by mutation** — deliberately break the invariant the test
   claims to protect, confirm the test fails, then fix the break. Only then
   trust the test.

Before declaring a change to `engine/` or `repo/` done:

```bash
make test-concurrency
make test-property
make test-contract
```

---

## Known state of this repository

- **Is a git repository as of the production-readiness audit.** `.gitignore`
  and `.github/workflows/` exist and there is now a `.git/` — this line
  previously said otherwise; check `git status`/`git log` for current truth
  rather than trusting this note, since repository state changes.
- **No AWS credentials, no Docker, no `make` binary** on the reference
  development machine. Every documented `make` target has a raw-command
  equivalent in [docs/OPERATIONS.md](docs/OPERATIONS.md).
- **Three items are genuinely unverified**, not just untested: AWS deployment,
  live provider E2E calls, and the container build. See
  [docs/PROJECT_CONTEXT.md #what-has-not-been-proven](docs/PROJECT_CONTEXT.md#what-has-not-been-proven).
  Do not describe any of these as working unless you have actually run the
  command and seen it succeed in this session.

---

## Commands

```bash
make test              # 371 tests, ~35s, no network, no spend
make check              # lint + typecheck + test
make run                 # gateway on :8080, in-memory store
make verify-pricing       # checks real-provider catalog rates for staleness
```

Full command reference: [docs/OPERATIONS.md](docs/OPERATIONS.md).

---

## Where the real explanations live

Don't re-derive these from scratch — they're already written, in depth, in one
of these:

| Question | Answer |
|---|---|
| Why is the schema shaped this way? | [docs/DECISIONS.md](docs/DECISIONS.md) |
| How does a request actually flow through the system? | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| What are the exact DynamoDB keys and transactions? | [docs/DATA_MODEL.md](docs/DATA_MODEL.md) |
| What bugs did testing actually catch, and why? | [docs/FINDINGS.md](docs/FINDINGS.md) |
| What does each test suite prove? | [docs/TESTING.md](docs/TESTING.md) |
| What's the exact API surface? | [docs/API.md](docs/API.md) |

Every module in `apps/gateway/src/abc_gateway/` also carries a detailed prose
docstring explaining *why*, not just *what* — read the module before assuming
its behaviour from its name.

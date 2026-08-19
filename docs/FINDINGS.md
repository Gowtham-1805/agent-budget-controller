# Findings

What the build actually taught us. Each entry is a real bug or near-miss found
during development, not a hypothetical — the kind of thing that is easy to lose
once the code is fixed and the failing test goes green. Recorded here so the
discipline that caught it doesn't get quietly abandoned later.

---

## F1. The hard-cap race test was vacuous — `asyncio.gather` does not test concurrency

**What happened.** The mandatory hard-cap race test (`$0.05` remaining, 10
concurrent `$0.04` requests, exactly one should be authorised) was first written
using `asyncio.gather` over ten coroutines. It passed.

To validate the test actually had teeth, the engine was deliberately broken:
commit-time condition re-evaluation was replaced with a read-then-write
implementation — evaluate the transaction's conditions once, then commit without
re-checking. That is the exact bug the test exists to catch. **The test still
passed.**

**Root cause.** `asyncio.gather` runs coroutines cooperatively on a single
thread. The event loop only yields control at an `await` point. The budget
engine's critical section — evaluate conditions, then commit — contains no
`await` in the in-memory backend, so ten "concurrent" coroutines actually ran one
after another, fully serialised, and never overlapped. The mutation had nothing
to trigger it, because there was no race for it to exploit.

**The fix.** Rewrote the test using real OS threads
(`tests/support/concurrency.py::run_concurrently`), released together via a
`threading.Barrier`, plus a `PhaseGate` that forces a specific pathological
schedule: every participant finishes evaluating its transaction's conditions
against the *original* pre-image before any of them is allowed to commit. That
is the schedule a read-then-write implementation cannot survive. Against the
same mutation, the rewritten test now fails with:

```
AssertionError: 10 requests were authorised against a budget that fits one;
                the check-then-act window is open
```

**The discipline this produced.** *A race test that has never been observed to
fail is not evidence.* Every concurrency-sensitive test added after this point
was validated the same way: break the invariant on purpose, confirm the test
catches it, then fix the break. This is recorded in
`tests/support/concurrency.py`'s module docstring so it isn't forgotten by the
next person who touches a concurrency test.

**Where to see it today:** `tests/concurrency/test_hard_cap_race.py`,
`tests/support/concurrency.py`.

---

## F2. Session closure silently didn't close — status lived on the wrong item

**What happened.** `try_close_session()` updated the `Session` domain record
(committed amount, close reason) but never updated the `status` attribute on the
DynamoDB item that the *authorization transaction* actually condition-checks
(`ConditionExpression: status = OPEN`). A session that should have been closed
kept accepting requests indefinitely.

**Root cause.** Two representations of "session status" existed —
one in the returned domain object, one in the stored item — and only one of them
was updated. The bug was invisible in isolation: reading the session back via
`GET /v1/sessions/{id}` showed the correct closed status, because that endpoint
read the (correctly updated) domain projection. Only the *next authorization
attempt*, which reads the raw item's `status` attribute inside a transaction,
would reveal the drift.

**How it was caught.** An acceptance test asserting that a request against an
exhausted session returns `session_closed` (not `budget_exhausted`) on the
*second* rejected request — proving the session had actually transitioned, not
just that the current request happened to be too large.

**The fix.** `try_close_session()` now updates the same DynamoDB item the
authorization condition reads, in the same write.

**The discipline this produced.** Whenever a piece of state is read by both a
condition expression and a domain-level accessor, a test needs to exercise the
condition-expression path specifically — reading the value back through a
"friendly" getter is not sufficient proof that the enforcement path sees the same
truth.

**Where to see it today:** `apps/gateway/src/abc_gateway/repo/memory/budget_repo.py`
and `repo/dynamo/expressions.py::session_close_action`;
`tests/acceptance/test_session_closure.py`.

---

## F3. An admin ledger query silently answered a different question

**What happened.** `GET /v1/ledger` with no `agent_id` query parameter, called
with an *admin* credential, returned `200 {"entries": []}` — a confident, empty
result — for a tenant that had genuinely spent money. This surfaced during manual
testing: the dashboard's Ledger tab appeared broken (showed nothing), even though
`curl`-ing the same endpoint with an explicit `agent_id` returned real entries.

**Root cause.**

```python
agent_id=agent_id or principal.agent_id
```

An admin credential's `principal.agent_id` is its own bootstrap identity
(`"admin"`), which has no ledger entries of its own — admins don't make governed
requests. When the caller omitted `agent_id`, the endpoint silently substituted
this meaningless value instead of either scanning tenant-wide or refusing the
request. The result looked exactly like "nothing has been spent," which is
indistinguishable from the real "nothing has been spent" case — a **silently
wrong answer**, which is worse than an explicit error.

A second, compounding issue: the dashboard had been pointed at a team
(`TEAM:acme`) that was never created in that gateway session, so two independent
bugs stacked and made diagnosis harder than either alone.

**The fix.** The endpoint now distinguishes admin from non-admin callers
explicitly:

```python
target_agent_id = agent_id if agent_id else (None if principal.is_admin else principal.agent_id)
if target_agent_id is None:
    raise HTTPException(422, "agent_id query parameter is required for admin credentials")
```

A non-admin credential still defaults sensibly to its own ledger (an agent asking
"what have I spent" doesn't need to name itself). An admin credential — which has
no ledger of its own — must say which agent it means, or gets a clear `422`
instead of a confident empty list.

**The discipline this produced.** *Silently answering a different question than
the one asked is worse than an explicit error.* Any endpoint that "defaults"
based on the caller's identity needs to ask whether that default is meaningful
for *every* kind of caller, not just the common one it was designed around.

**Where to see it today:**
`apps/gateway/src/abc_gateway/api/routes.py::get_ledger`;
`tests/e2e/test_api.py::TestBudgetVisibility` (three regression tests: admin
without `agent_id` → `422`; admin with `agent_id` → real entries; non-admin
without `agent_id` → its own entries).

---

## F4. `netstat`'s PID is not `bash`'s job number

**What happened (operational, not a code bug).** While restarting the gateway
locally, `kill 857` was issued against the PID Bash reported for a backgrounded
job. The new server then failed to bind to port 8080 and exited — but silently,
with the failure only visible in its log file, not on the terminal. Every request
made afterward was actually served by the *old*, unfixed process, which was
never actually killed. This produced a confusing several-minute stretch where a
just-applied fix appeared not to have taken effect.

**Root cause.** Under Git Bash/MSYS on Windows, the PID reported by `$!` for a
backgrounded job is not reliably the same as the real Windows process ID holding
the socket. `kill` against the wrong number is a silent no-op if that number
doesn't correspond to a live process.

**The fix (procedural).** Verify a restart actually took effect by checking what
process is *really* listening, using the platform's own view of the world:

```powershell
netstat -ano | grep ':8080' | grep LISTENING     # get the real Windows PID
taskkill //F //PID <that number>                  # kill it, not the bash job number
```

Then confirm the new process's own log shows a clean startup (`INFO: Uvicorn
running on...`), not a bind error.

**Where this is recorded operationally:**
[OPERATIONS.md #restarting-the-local-gateway](OPERATIONS.md#restarting-the-local-gateway-windows).

---

## Summary: the discipline these findings produced

| Finding | Discipline it produced |
|---|---|
| F1 | Concurrency tests must be validated by deliberate mutation before being trusted |
| F2 | State read by both a condition expression and a domain accessor needs a test that exercises the condition-expression path specifically |
| F3 | A "helpful" default must be checked against every kind of caller, not just the common one — silent wrong answers are worse than explicit errors |
| F4 | Verify a process restart against the platform's own view (`netstat`), not the shell's job accounting |

None of these were found by writing more tests in the abstract. Each was found by
either deliberately trying to break something that was supposed to be true, or by
actually using the system end-to-end and noticing the result didn't match
expectation. Both are worth doing on top of, not instead of, the acceptance test
suite.

---

## Further reading

- [TESTING.md](TESTING.md) — the test strategy this discipline feeds into
- [DECISIONS.md](DECISIONS.md) — the design choices these findings validated

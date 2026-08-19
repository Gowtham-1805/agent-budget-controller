# Design Decisions

Every load-bearing choice in this system, with the reasoning and the alternative
it beat. Ordered roughly by how foundational each one is — later decisions build
on earlier ones.

---

## D1. Money is materialised, not derived

**Decision:** `remaining = limit - committed - reserved` is stored as its own
attribute and updated with `SET remaining = if_not_exists(remaining, :limit) -
:cost`, rather than computed from `committed` and `reserved` at read time.

**Why:** DynamoDB `ConditionExpression` has no arithmetic operators. You cannot
write:

```
committed + reserved + :cost <= limit          ← not expressible
```

You can write:

```
remaining_nano >= :cost                        ← expressible
```

This single fact dictates the entire counter schema. `committed` and `reserved`
are kept alongside purely for reporting and for the accounting identity —
`remaining + committed + reserved == limit` — that every property test asserts.

**Alternative rejected:** Read `committed` and `reserved`, add them in application
code, compare against `limit`, then write. This reintroduces exactly the
check-then-act race the whole system exists to close — two concurrent readers see
the same pre-update values and both proceed.

**Consequence accepted:** Every mutation path (reserve, reconcile, release,
pending) must update `remaining` in lockstep with `committed`/`reserved`, by
construction, in the same expression. Getting this wrong in one code path breaks
the invariant silently.

---

## D2. Money is an integer, never a float

**Decision:** All monetary values are `int` nano-USD (1 USD = 1,000,000,000
nano-USD). `Money` (`domain/money.py`) defines no `__float__`, no `__truediv__`,
and rejects `bool` (which is a stealth `int` in Python). `Decimal` is used in
exactly one place: `pricing/loader.py`, to parse the catalog file.

**Why:** The comparison that decides whether a request may proceed —
`remaining_nano >= reservation_nano` — has to be *exact*. A float short by one
representational epsilon either authorises spend that should have been blocked,
or blocks spend that should have gone through, and both are unacceptable at an
enforcement boundary. `$0.10` is not exactly representable in IEEE-754 double
precision; nano-USD as an integer always is.

**Alternative rejected:** `Decimal` throughout. Considered and rejected —
`Decimal` arithmetic is slower, DynamoDB's number type still round-trips through
`Decimal` awkwardly at the boundary, and mixing `Decimal` with a schema that
serialises to plain integers invites exactly the kind of silent precision loss
this decision exists to prevent. Integers avoid the question entirely.

**Consequence accepted:** Every price in `pricing/catalog.json` must be
expressible as a whole number of nano-USD per million tokens. The loader rejects
any rate with sub-nano precision rather than rounding it — `pricing/loader.py`
raises `CatalogFormatError` on a value like `"0.0000000001"`.

---

## D3. Budget windows are keys, not TTL-managed rows

**Decision:** `BudgetWindow` resolves an instant to a deterministic string —
`WINDOW#MONTH#2026-08` — that is part of the DynamoDB sort key. DynamoDB's `ttl`
attribute (`housekeeping_ttl`) exists purely for eventual garbage collection and
is never read by the authorization path.

**Why:** AWS documents that TTL deletion happens *eventually*, with lag measured
in days, not on the stroke of expiry. A design that waited for August's budget
row to disappear before starting a fresh September budget would keep charging
September's traffic against August's already-exhausted limit for however long
deletion actually takes.

**Alternative rejected:** One row per scope, reset via a scheduled job or TTL
expiry. Rejected outright — it makes the reset instant non-deterministic, which
is disqualifying for a financial control.

**Consequence accepted:** `BudgetWindow.for_instant()` must be a pure function of
`(window_type, instant, timezone)` with no I/O, so that resolving "which window
does this request belong to" is always instant and always agrees across every
caller.

---

## D4. Status and counters are deliberately separate items

**Decision:** `AgentState` (`SK=STATE`) and the agent's `BudgetState`
(`SK=WINDOW#...`) are different DynamoDB items. Same for `Session` (`SK=META`)
versus its budget counters.

**Why:** DynamoDB forbids two actions against the same item within one
transaction. Authorization needs to **check** agent/session status and
**decrement** budget counters inside the *same* atomic transaction — checking
status via a prior read, before building the transaction, would leave a window in
which a pause lands between the read and the write, and one more request gets
through anyway. That window is precisely where a runaway agent does its damage;
the whole point of the circuit breaker is closed by this separation.

**Alternative rejected:** One item per scope, carrying both status and counters.
Simpler schema, but it forces status checks outside the transaction, which
reopens the TOCTOU gap this system exists to close.

**Consequence accepted:** Every entity needs its access pattern thought through
at design time — "does this ever need to be checked *and* updated atomically with
something else?" — rather than being modelled for storage convenience alone.

---

## D5. Ambiguity holds the money

**Decision:** A reservation only releases when the provider outcome is **proven**
not billed. Everything else — including a read timeout — defaults to
`RECONCILE_PENDING`, which keeps the reservation's full amount encumbered.

**Why:** A read timeout tells you the response did not arrive. It says nothing
about whether the completion was generated and billed — and for a long
generation, a timeout is exactly what an expensive *success* looks like from the
gateway's side. If the classifier treated ambiguity as "not billed," releasing
the hold would let the same dollars be spent again while the provider had, in
fact, already billed them once.

The allow-list for proven-not-billed (`providers/classify.py`) is narrow on
purpose: DNS failure, refused connection, TLS handshake failure, local validation
error, or a structured 4xx with no usage object. Notably, `ConnectTimeout`
qualifies (the connection never opened) while `ReadTimeout` does not (the request
was sent and may have been served) — collapsing those two into one "timeout"
bucket is the single easiest way to introduce a double-spend, so they are kept
explicitly distinct in the type system.

**Alternative rejected:** Treat any provider-side failure as unbilled and release
immediately. Simpler, and wrong — it fails toward under-counting our own
liability, which is the wrong direction for a budget controller to fail in.

**Consequence accepted:** Pending reservations require an operator resolution
path (`RECONCILE_PENDING → RECONCILE_RESOLVED`), and dashboards must surface
`pending_nano` explicitly so held-but-unconfirmed exposure is never invisible.

---

## D6. Overage is recorded honestly, not hidden

**Decision:** If a provider generates past the hard output cap sent to it, the
reconcile transaction still succeeds unconditionally — it carries no `remaining
>= 0` guard. `remaining_nano` is allowed to go negative and is **never clamped**.
The excess is recorded in a dedicated `overage_nano` counter and an immutable
`OVERAGE` ledger entry.

**Why:** No gateway can prevent a provider from ignoring the cap it was given,
and refusing to record the real charge would be strictly worse than recording it
— the alternative is silently understating actual spend. Clamping `remaining` at
zero would erase the evidence of what happened and break the accounting identity
the property tests assert.

There is a second, quieter benefit: a negative `remaining` is **self-healing**.
The very next request's condition, `remaining_nano >= :cost`, is false for any
positive cost, so the scope hard-closes immediately rather than continuing to
authorise against a budget that is already broken. Exposure is bounded to the
requests already in flight, not to an open-ended runaway.

**Alternative rejected:** Clamp `remaining` at zero and drop the excess.
Considered and rejected — it would make the books lie about how much was really
spent, which defeats the purpose of a financial ledger.

**What this decision is honest about:** the **prospective** invariant —
"no reservation is ever granted unless it fit atomically at that instant" — is
absolute and always holds. The **retrospective** statement is necessarily weaker:
`committed + reserved` can exceed `limit` by exactly `overage`, and `overage`
appearing at all means a provider ignored its own hard cap or the gateway's token
counting drifted. It is alarmed, not dashboarded, because it should never happen.

---

## D7. Model allocation is a sub-budget, not a parallel budget

**Decision:** A model allocation (e.g. "$40 of the agent's $50, earmarked for the
premium model") is modelled as *just another scope* in the same reservation
transaction, with the agent scope also present. It is not a separate budget that
could be spent independently of the agent's total.

**Why:** The literal requirement — "when the preferred model's budget is
exhausted, reroute to a cheaper model" — is incoherent if read against the
agent's *total* budget: an agent that has spent its entire $50 cannot be allowed
to spend more just because the next model happens to be cheaper. The coherent
reading is a sub-allocation: exhausting the $40 premium allocation still leaves
$10 of real agent capacity for the fallback to draw on, and not a cent more.

Because the allocation scope is *literally in the same transaction* as the parent
agent scope, "fallback cannot bypass an exhausted agent budget" is true **by
construction** — there is no code path that reserves against the allocation
without also reserving against its parent. This is what
`engine/routing.py::_may_try_next` relies on: it only advances to the next model
when the *sole* blocking scope is the allocation; a team/agent/session denial
aborts the chain immediately.

**Alternative rejected:** A model allocation as an independent budget, checked
separately from the agent total. Rejected — it would let fallback silently
escape the agent's cap, which is exactly the "spend past the limit on a cheaper
model" bug the hierarchy exists to prevent.

---

## D8. The engine is backend-agnostic; two backends prove it

**Decision:** `engine/` never imports anything from `repo/dynamo/`. It builds a
`TransactionPlan` — an ordered list of typed slots — and hands it to whichever
repository implements the `BudgetRepository` protocol. Both
`InMemoryBudgetRepository` and `DynamoBudgetRepository` compile the *same* plan
shape, and one contract test suite (`tests/contract/`) runs identically against
both.

**Why:** A fast in-memory backend used only for its own bespoke tests is a
comfortable fiction — its green tests say nothing about what happens against real
DynamoDB semantics. Making both backends satisfy the same contract, from the same
plan objects, is what turns "the in-memory tests pass" into evidence about
production behaviour.

**Alternative rejected:** Test only against DynamoDB (via moto), skip the
in-memory backend. Rejected because moto has no optimistic concurrency control —
it never emits `TransactionConflict` — and serialises requests internally, which
makes it structurally unable to construct certain race conditions. The in-memory
backend's `_txn.py` implements an explicit two-phase `evaluate → commit` with an
injectable interleaving hook specifically so the hardest TOCTOU races can be
constructed deterministically. See
[TESTING.md #the-four-layers](TESTING.md#the-four-layers-of-the-race-proof).

**Consequence accepted:** Every new repository method must be added to the
protocol and implemented (and tested) on both backends, or the contract breaks.

---

## D9. Runaway detection uses a rolling window, not calendar buckets

**Decision:** The circuit breaker sums spend across the last 60 minutes,
computed from one-minute buckets, rather than resetting a counter on the clock
hour.

**Why:** Calendar-hour buckets have a blind spot exactly where a runaway loop's
signature lives:

```
11:50-11:59  spend $7   (hour 11 total: $7 — under a $10 threshold)
12:00-12:10  spend $7   (hour 12 total: $7 — under a $10 threshold)
```

$14 was spent in 20 minutes and neither calendar hour trips a $10 threshold. A
rolling window sees $14 immediately.

**Alternative rejected:** A single counter reset every clock hour. Simpler and
cheaper (one write per event instead of a bucket scan), but blind to bursts that
straddle the boundary — which is exactly the shape a recursive loop produces.

**Consequence accepted:** Summing the rolling window costs a `BatchGetItem` over
up to 60 one-minute bucket items per evaluation, rather than a single-item read.

---

## D10. A caller never asserts its own identity

**Decision:** Governance identity (`tenant → team → agent`) is resolved
server-side from a trusted credential (`auth/identity.py`). No request field —
header, body, or query parameter — is ever trusted to say which agent is making
the call.

**Why:** A header like `X-Agent-ID: cheap-agent` is worthless as identity if the
caller can simply send a different value to draw from a different budget. Any
agent could then spend any other agent's money, and the entire budget hierarchy
would be decorative. There is an e2e test
(`test_a_client_cannot_choose_its_own_agent_identity`) that sends a deliberate
`X-Agent-ID` impersonation header alongside a real credential and asserts spend
lands on the authenticated agent, not the claimed one.

**Alternative rejected:** Trust a signed or otherwise "self-asserted" agent
identifier in the request. Rejected — the moment identity can be set by the
caller rather than looked up from what authenticated them, the hierarchy is
advisory rather than enforced.

**Consequence accepted:** Every credential (including session-scoped ones) must
be issued through a control-plane endpoint that binds it server-side; there is no
path for a client to "become" a different agent by constructing its own request.

---

## D11. Observability cannot break enforcement

**Decision:** Langfuse, CloudWatch metrics, and the dashboard's WebSocket feed
are all fire-and-forget from the authorization path. `TelemetrySink.emit()`
returns immediately; failures are caught, counted in
`telemetry_failures_total`, and never re-raised.

**Why:** The system's job is financial authorization. An outage in the layer
that *explains* spending must never become an outage in the layer that
*controls* it. There is an explicit failure-injection test asserting enforcement
continues correctly when Langfuse, the WebSocket, and the dashboard are all
simultaneously unavailable.

**Alternative rejected:** Await telemetry export before responding, to
guarantee every request is traced. Rejected — it makes Langfuse's availability a
dependency of the enforcement path, exactly backwards from the priority order.

**Consequence accepted:** Telemetry gaps are possible and must be monitored for
independently (`telemetry_failures_total`), rather than assumed complete.

---

## D12. Single authoritative write region

**Decision:** Budget-authoritative writes happen in one AWS region. DynamoDB
global tables are not used for active-active budget mutation.

**Why:** DynamoDB transactions are atomic only within the region where they
originate. Global-table replication does not extend that guarantee across
regions — two regions could each authorise spend against the same remaining
balance independently, which is precisely the race this entire system exists to
prevent, just moved up a level.

**Alternative rejected:** Global tables with app-level conflict resolution
("last write wins" or similar). Rejected — there is no conflict-resolution
strategy that preserves a strict financial invariant across two independently-
authorizing regions without a home-region ownership protocol, which was out of
scope for this iteration.

**Consequence accepted:** A regional outage is a hard outage for governed
inference in that tenant, rather than a gracefully-degraded multi-region
fallback. Documented as a known limitation, not silently absorbed.

---

## Further reading

- [ARCHITECTURE.md](ARCHITECTURE.md) — how these decisions compose into the system
- [DATA_MODEL.md](DATA_MODEL.md) — the schema these decisions produced
- [FINDINGS.md](FINDINGS.md) — what testing these decisions actually surfaced

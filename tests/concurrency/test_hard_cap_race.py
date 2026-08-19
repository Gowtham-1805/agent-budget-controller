"""The mandatory hard-cap race.

$0.05 of budget remains. Ten requests arrive at once, each needing $0.04.
Exactly one may be authorised, and the provider must be invoked exactly once.

This guards the failure that motivates the entire system. A naive
implementation reads the balance, sees $0.05, decides "yes", then charges. Ten
workers doing that concurrently all see $0.05, all proceed, and $0.40 is spent
against a $0.05 budget -- invisibly, until the invoice arrives.

Two layers run here and they prove different things:

* **The threaded layer** runs real OS threads with no artificial scheduling.
* **The phase-gate layer** forces the pathological schedule deliberately: every
  transaction evaluates its conditions against the original state before any of
  them commits. This is the schedule that read-then-write cannot survive, and
  it cannot occur under a store that serialises requests -- which is why it is
  constructed by hand rather than hoped for.

Both layers were validated by mutation: with commit-time re-evaluation removed
from the store, the phase-gate test authorises all ten and fails. A race test
that cannot fail is not evidence.
"""

from __future__ import annotations

import pytest
from tests.conftest import FIXED_NOW, TEAM, TENANT, active_agent, agent_policy, team_policy
from tests.support.concurrency import PhaseGate, run_concurrently

from abc_gateway.domain.errors import AuthorizationDenied, DenialCode
from abc_gateway.domain.money import Money
from abc_gateway.domain.reservation import ReservationGrant
from abc_gateway.domain.scopes import ScopeRef, ScopeType
from abc_gateway.domain.window import BudgetWindow, WindowType
from abc_gateway.providers.base import ChatMessage, ChatRequest, Timeouts

pytestmark = pytest.mark.serial

N_CONCURRENT = 10
TEAM_LIMIT = "0.05"
PER_REQUEST = "0.04"


def month_window() -> BudgetWindow:
    return BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW)


def team_scope() -> ScopeRef:
    return ScopeRef(ScopeType.TEAM, TEAM)


def chat() -> ChatRequest:
    return ChatRequest(messages=(ChatMessage("user", "hello"),), model="premium")


class TestHardCapRace:
    async def test_exactly_one_of_ten_concurrent_requests_is_authorized(
        self, repo, engine, provider, catalog, make_request
    ) -> None:
        team = team_policy(TEAM_LIMIT)
        agent = agent_policy("racer", "50.00")
        await repo.put_agent_state(active_agent("racer"))

        async def attempt(index: int):
            request = make_request(agent=agent, team=team, idempotency_key=f"race-{index}")
            # Each request needs exactly $0.04 against $0.05 of team budget.
            assert request.reserved_cost == Money.from_usd_str(PER_REQUEST)
            try:
                grant = await engine.reserve(request)
            except AuthorizationDenied as exc:
                return ("denied", exc.denial)
            await engine.mark_dispatched(TENANT, grant.reservation_id)
            outcome = await provider.invoke(
                chat(),
                grant.effective_model,
                max_output_tokens=grant.bounded_max_output_tokens,
                timeouts=Timeouts(),
                correlation_id=grant.reservation_id,
            )
            await engine.reconcile(
                TENANT,
                grant.reservation_id,
                outcome.usage,
                catalog.get("test", grant.effective_model),
                FIXED_NOW,
            )
            return ("authorized", grant)

        results = run_concurrently(attempt, N_CONCURRENT)
        authorized = [r for kind, r in results if kind == "authorized"]
        denied = [r for kind, r in results if kind == "denied"]

        assert len(authorized) == 1, f"expected 1 authorization, got {len(authorized)}"
        assert len(denied) == N_CONCURRENT - 1

        # The property that actually protects the money: the nine rejected
        # requests cost nothing, because they never reached the provider.
        assert provider.invocation_count == 1
        assert provider.invoked_reservation_ids == {authorized[0].reservation_id}

    async def test_denials_name_the_blocking_scope(self, repo, engine, make_request) -> None:
        """A denial must be actionable, not merely a 429.

        The caller needs to know that the *team* budget, in *this* window, bound
        the request -- otherwise financial exhaustion is indistinguishable from
        ordinary rate limiting.
        """
        team = team_policy(TEAM_LIMIT)
        agent = agent_policy("racer", "50.00")
        await repo.put_agent_state(active_agent("racer"))

        async def attempt(index: int):
            try:
                return await engine.reserve(
                    make_request(agent=agent, team=team, idempotency_key=f"n-{index}")
                )
            except AuthorizationDenied as exc:
                return exc.denial

        results = run_concurrently(attempt, N_CONCURRENT)
        denials = [r for r in results if not isinstance(r, ReservationGrant)]
        assert denials

        for denial in denials:
            assert denial.code is DenialCode.BUDGET_EXHAUSTED
            assert denial.blocking_scope == team_scope()
            assert denial.window is not None
            assert denial.window.type is WindowType.MONTHLY
            assert denial.requested == Money.from_usd_str(PER_REQUEST)
            assert denial.limit == Money.from_usd_str(TEAM_LIMIT)
            # Deterministic: retrying would evaluate identically.
            assert denial.retryable is False

    async def test_invariant_holds_after_the_race(
        self, repo, engine, provider, catalog, make_request
    ) -> None:
        team = team_policy(TEAM_LIMIT)
        agent = agent_policy("racer", "50.00")
        await repo.put_agent_state(active_agent("racer"))

        async def attempt(index: int):
            try:
                grant = await engine.reserve(
                    make_request(agent=agent, team=team, idempotency_key=f"s-{index}")
                )
            except AuthorizationDenied:
                return None
            await engine.mark_dispatched(TENANT, grant.reservation_id)
            outcome = await provider.invoke(
                chat(),
                grant.effective_model,
                max_output_tokens=grant.bounded_max_output_tokens,
                timeouts=Timeouts(),
                correlation_id=grant.reservation_id,
            )
            await engine.reconcile(
                TENANT,
                grant.reservation_id,
                outcome.usage,
                catalog.get("test", grant.effective_model),
                FIXED_NOW,
            )
            return grant

        run_concurrently(attempt, N_CONCURRENT)

        state = await repo.get_budget_state(team_scope(), month_window(), tenant_id=TENANT)
        assert state is not None

        # THE invariant.
        assert state.committed + state.reserved <= state.limit
        assert state.invariant_holds()

        assert state.committed == Money.from_usd_str(PER_REQUEST)
        assert state.reserved == Money.zero()
        assert state.remaining == Money.from_usd_str("0.01")
        # Nothing overshot its cap, so no excess should have been recorded.
        assert state.overage == Money.zero()
        assert state.open_reservations == 0


class TestTimeOfCheckTimeOfUse:
    """Proof that the check-then-act window is genuinely closed."""

    async def test_all_transactions_evaluate_before_any_commits(
        self, repo, engine, provider, make_request
    ) -> None:
        """The pathological schedule, constructed on purpose.

        Every transaction completes its condition evaluation against the
        pristine $0.05 balance before any of them commits. A read-then-write
        engine authorises all ten here; one that re-evaluates at commit
        authorises one.

        Verified by mutation: removing commit-time re-evaluation makes this
        test fail with ten authorizations and a $0.40 overspend.
        """
        team = team_policy(TEAM_LIMIT)
        agent = agent_policy("racer", "50.00")
        await repo.put_agent_state(active_agent("racer"))

        gate = PhaseGate(N_CONCURRENT)

        async def attempt(index: int):
            try:
                return await engine.reserve(
                    make_request(agent=agent, team=team, idempotency_key=f"gate-{index}")
                )
            except AuthorizationDenied as exc:
                return exc.denial

        with repo.store.interleave(gate):
            results = run_concurrently(attempt, N_CONCURRENT)

        assert gate.arrivals == N_CONCURRENT, (
            "not every transaction reached the gate; the interleaving was not "
            "actually forced and this test would prove nothing"
        )

        granted = [r for r in results if isinstance(r, ReservationGrant)]
        assert len(granted) == 1, (
            f"{len(granted)} requests were authorised against a budget that "
            f"fits one; the check-then-act window is open"
        )

        state = await repo.get_budget_state(team_scope(), month_window(), tenant_id=TENANT)
        assert state is not None
        assert state.committed + state.reserved <= state.limit
        assert provider.invocation_count == 0  # none of them invoked the provider

    async def test_concurrent_first_requests_to_a_new_window_all_succeed(
        self, repo, engine, make_request
    ) -> None:
        """Lazy window creation must not reject the loser of a creation race.

        Budget-window items are created on first touch. Were the condition only
        `attribute_not_exists(PK)`, several simultaneous first requests would
        all plan against "absent", and every one that committed after the first
        would be rejected -- despite the budget being untouched. That is exactly
        the millisecond after a window boundary, when traffic spikes. The
        `OR remaining >= cost` branch is what prevents it.
        """
        team = team_policy("10.00")
        agent = agent_policy("fresh", "10.00")
        await repo.put_agent_state(active_agent("fresh"))

        gate = PhaseGate(5)

        async def attempt(index: int):
            try:
                return await engine.reserve(
                    make_request(agent=agent, team=team, idempotency_key=f"new-{index}")
                )
            except AuthorizationDenied as exc:
                return exc.denial

        with repo.store.interleave(gate):
            results = run_concurrently(attempt, 5)

        assert all(isinstance(r, ReservationGrant) for r in results), (
            "a concurrent first request to a fresh window was wrongly rejected"
        )

        state = await repo.get_budget_state(team_scope(), month_window(), tenant_id=TENANT)
        assert state is not None
        assert state.reserved == Money.from_usd_str("0.20")  # 5 x $0.04
        assert state.invariant_holds()

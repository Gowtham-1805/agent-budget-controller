"""The 80% warning and the exact 100% hard block.

Two requirements that sound simple and are not:

* **Exactly one warning, ever.** Not one per reconciliation that observes the
  crossing, not one per polling interval. The failure modes here are felt by a
  person: a pager firing repeatedly, or not firing at all.
* **The hard block fires at exactly 100% consumed**, and the blocked request
  must cost nothing. Asserting the 429 is the easy half; asserting the provider
  invocation count did not move is the half that matters.
"""

from __future__ import annotations

import pytest
from tests.conftest import FIXED_NOW, TEAM, TENANT, active_agent, agent_policy, team_policy
from tests.support.concurrency import PhaseGate, run_concurrently

from abc_gateway.domain.alerts import AlertKind
from abc_gateway.domain.errors import DenialCode
from abc_gateway.domain.money import Money
from abc_gateway.domain.scopes import ScopeRef, ScopeType
from abc_gateway.domain.window import BudgetWindow, WindowType


def month_window() -> BudgetWindow:
    return BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW)


async def agent_state(repo, agent_id: str, scope=None):
    return await repo.get_budget_state(
        scope or ScopeRef(ScopeType.AGENT, agent_id), month_window(), tenant_id=TENANT
    )


class TestEightyPercentWarning:
    """Limit $0.10, warning at 80%. Each call costs $0.04."""

    @pytest.fixture
    async def setup(self, repo):
        await repo.put_agent_state(active_agent("warner"))
        return team_policy("100.00"), agent_policy("warner", "0.10")

    async def test_no_warning_below_the_threshold(self, repo, driver, setup) -> None:
        team, agent = setup
        # One call: $0.04 of $0.10 == 40%.
        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert result.allowed
        assert result.warnings == ()
        state = await agent_state(repo, "warner")
        assert state.utilization_percent == 40
        assert state.warning_80_sent is False

    async def test_exactly_one_warning_at_the_crossing(self, repo, driver, setup) -> None:
        team, agent = setup
        # Two calls: $0.08 of $0.10 == 80%, which is the crossing.
        results = await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=2)

        warnings = [w for r in results for w in r.warnings]
        assert len(warnings) == 1
        assert warnings[0].kind is AlertKind.WARN_80
        assert warnings[0].scope == ScopeRef(ScopeType.AGENT, "warner")
        assert warnings[0].limit == Money.from_usd_str("0.10")
        assert warnings[0].spent == Money.from_usd_str("0.08")

        state = await agent_state(repo, "warner")
        assert state.warning_80_sent is True
        assert state.utilization_percent == 80

    async def test_still_exactly_one_warning_after_further_spend(self, repo, driver, setup) -> None:
        """Crossing again must not re-warn. The flag is durable, not per-call."""
        team, agent = setup
        results = await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=3)

        allowed = [r for r in results if r.allowed]
        assert len(allowed) == 2, "third call should not fit in $0.10"

        warnings = [w for r in results for w in r.warnings]
        assert len(warnings) == 1
        assert len(repo.alerts(AlertKind.WARN_80.value)) == 1

    async def test_exactly_one_warning_under_concurrent_reconciliation(
        self, repo, catalog, provider
    ) -> None:
        """Several reconciliations crossing 80% at once still yield one warning.

        The conditional flag flip is the serialisation point: every reconciler
        observes the crossing, exactly one wins the flip, and the rest treat
        losing as a no-op.
        """
        from tests.support.driver import Driver

        await repo.put_agent_state(active_agent("racer"))
        team = team_policy("100.00")
        # $1.00 limit; each call costs $0.04, so 20 calls lands exactly on 80%.
        agent = agent_policy("racer", "1.00")

        driver = Driver(
            repo=repo,
            catalog=catalog,
            provider=provider,
            tenant_id=TENANT,
            team_id=TEAM,
        )

        gate = PhaseGate(20)

        async def one_call(index: int):
            return await driver.call(
                agent=agent,
                team=team,
                now=FIXED_NOW,
                idempotency_key=f"conc-{index}",
            )

        # The gate only guards authorization; reconciliations then race freely.
        with repo.store.interleave(gate):
            results = run_concurrently(one_call, 20)

        allowed = [r for r in results if r.allowed]
        assert len(allowed) == 20

        warnings = [w for r in results for w in r.warnings]
        assert len(warnings) == 1, f"expected exactly one warning, got {len(warnings)}"
        assert len(repo.alerts(AlertKind.WARN_80.value)) == 1


class TestExactHundredPercentHardBlock:
    """Drive committed to exactly the limit, then prove the next call is refused."""

    async def test_hard_block_at_exactly_one_hundred_percent(self, repo, driver, provider) -> None:
        await repo.put_agent_state(active_agent("capped"))
        team = team_policy("100.00")
        # $0.20 limit, $0.04 per call: five calls land exactly on 100%.
        agent = agent_policy("capped", "0.20")

        results = await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=5)
        assert all(r.allowed for r in results)

        state = await agent_state(repo, "capped")
        assert state.committed == state.limit == Money.from_usd_str("0.20")
        assert state.reserved == Money.zero()
        assert state.remaining == Money.zero()
        assert state.utilization_percent == 100

        invocations_before = provider.invocation_count

        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.BUDGET_EXHAUSTED
        assert blocked.denial.blocking_scope == ScopeRef(ScopeType.AGENT, "capped")
        assert blocked.denial.available == Money.zero()
        assert blocked.denial.requested == Money.from_usd_str("0.04")
        assert blocked.denial.window is not None
        assert blocked.denial.window.reset_at.isoformat().startswith("2026-09-01")

        # The assertion that separates enforcement from observability.
        assert provider.invocation_count == invocations_before

    async def test_the_invariant_holds_at_the_cap(self, repo, driver) -> None:
        await repo.put_agent_state(active_agent("capped"))
        team = team_policy("100.00")
        agent = agent_policy("capped", "0.20")

        await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=10)

        state = await agent_state(repo, "capped")
        assert state.committed + state.reserved <= state.limit
        assert state.invariant_holds()
        assert state.open_reservations == 0
        assert state.overage == Money.zero()


class TestRequestLargerThanTheBudget:
    async def test_reports_that_the_request_cannot_ever_fit(self, repo, driver) -> None:
        """A distinct, more useful error than "budget exhausted".

        The budget is untouched; the request is simply too big. Telling the
        caller to wait for a reset would be wrong -- they need a smaller
        request or a bigger limit.
        """
        await repo.put_agent_state(active_agent("tiny"))
        team = team_policy("100.00")
        agent = agent_policy("tiny", "0.001")  # smaller than one $0.04 call

        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not result.allowed
        assert result.denial.code is DenialCode.EXCEEDS_WINDOW_LIMIT
        assert result.denial.blocking_scope == ScopeRef(ScopeType.AGENT, "tiny")

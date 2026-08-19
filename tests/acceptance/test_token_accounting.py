"""Token metering and token quotas.

Two distinct requirements live here.

**Token quotas are independent of money.** An agent can hold plenty of dollars
and still be out of output tokens, and that request must be refused. If the two
were coupled, an operator capping tokens would find the cap silently ineffective
whenever the budget was healthy.

**Preflight estimates and actuals are both preserved.** The ledger has to answer
"what did we authorise?" and "what did it really cost?" separately. Collapsing
them into one number destroys the ability to audit an over- or under-estimate,
which is exactly what you need when reconciling against a provider invoice.
"""

from __future__ import annotations

from tests.conftest import FIXED_NOW, active_agent, agent_policy, team_policy

from abc_gateway.domain.errors import DenialCode
from abc_gateway.domain.money import Money
from abc_gateway.domain.tokens import TokenVector
from abc_gateway.domain.usage import NormalizedUsage, ProviderUsage


class TestTokenQuotasAreIndependentOfMoney:
    async def test_output_token_quota_rejects_while_dollars_remain(
        self, repo, driver, provider
    ) -> None:
        await repo.put_agent_state(active_agent("quota"))
        team = team_policy("1000.00")
        agent = agent_policy(
            "quota",
            "1000.00",  # dollars are not the constraint
            max_output_tokens=2500,  # two 1000-token calls fit; a third does not
        )

        results = await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=2)
        assert all(r.allowed for r in results)

        invocations_before = provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.TOKEN_QUOTA_EXCEEDED
        assert blocked.denial.token_dimensions == ("output",)
        assert blocked.denial.remaining_tokens.output == 500
        assert blocked.denial.requested_tokens.output == 1000

        # There was plenty of money; the quota alone stopped it.
        state = await repo.get_budget_state(
            blocked.denial.blocking_scope, blocked.denial.window, tenant_id="acme"
        )
        assert state.remaining > Money.zero()

        # And it cost nothing.
        assert provider.invocation_count == invocations_before

    async def test_input_token_quota_is_enforced_separately(self, repo, driver) -> None:
        await repo.put_agent_state(active_agent("quota"))
        team = team_policy("1000.00")
        agent = agent_policy("quota", "1000.00", max_input_tokens=1500)

        first = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert first.allowed

        second = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert not second.allowed
        assert second.denial.code is DenialCode.TOKEN_QUOTA_EXCEEDED
        assert second.denial.token_dimensions == ("input",)

    async def test_total_token_quota_is_enforced_separately(self, repo, driver) -> None:
        await repo.put_agent_state(active_agent("quota"))
        team = team_policy("1000.00")
        # 2000 tokens per call; 3000 total allows one call, not two.
        agent = agent_policy("quota", "1000.00", max_total_tokens=3000)

        first = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert first.allowed

        second = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert not second.allowed
        assert "total" in second.denial.token_dimensions


class TestOutputBounding:
    async def test_a_client_that_omits_a_limit_gets_the_policy_cap(
        self, repo, driver, provider
    ) -> None:
        """Omitting max_tokens must not mean "unbounded".

        If it did, a client could bypass spend protection entirely by leaving
        the field out -- the reservation would have no worst case to bound.
        """
        await repo.put_agent_state(active_agent("unbounded"))
        team = team_policy("100.00")
        agent = agent_policy("unbounded", "100.00", default_max_output_tokens=750)

        result = await driver.call(agent=agent, team=team, now=FIXED_NOW, max_output_tokens=None)

        assert result.allowed
        assert result.grant.bounded_max_output_tokens == 750
        # The cap was actually sent to the provider, not merely recorded.
        assert provider.invoked_max_output_tokens[-1] == 750

    async def test_a_client_cannot_exceed_the_policy_cap(self, repo, driver, provider) -> None:
        await repo.put_agent_state(active_agent("greedy"))
        team = team_policy("100.00")
        agent = agent_policy("greedy", "100.00", default_max_output_tokens=500)

        result = await driver.call(agent=agent, team=team, now=FIXED_NOW, max_output_tokens=100_000)

        assert result.allowed
        assert result.grant.bounded_max_output_tokens == 500
        assert provider.invoked_max_output_tokens[-1] == 500

    async def test_the_reservation_covers_the_full_output_ceiling(self, repo, driver) -> None:
        """Reserve the worst case, not the expected case."""
        await repo.put_agent_state(active_agent("bounded"))
        team = team_policy("100.00")
        agent = agent_policy("bounded", "100.00", default_max_output_tokens=1000)

        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert result.grant.reserved_tokens.output == 1000


class TestLedgerRecordsEstimateAndActual:
    async def test_both_estimate_and_actual_are_persisted(self, repo, driver, provider) -> None:
        await repo.put_agent_state(active_agent("meter"))
        team = team_policy("100.00")
        agent = agent_policy("meter", "100.00", default_max_output_tokens=1000)
        # Generates fewer tokens than the ceiling it was given, so the estimate
        # and the actual genuinely differ.
        provider.behaviour.output_tokens = 250

        await driver.call(agent=agent, team=team, now=FIXED_NOW)

        entry = repo.ledger_entries("USAGE")[0]

        # Preflight side.
        assert entry.preflight_input_tokens == 1000
        assert entry.reserved_output_tokens == 1000
        assert entry.estimated_max_cost == Money.from_usd_str("0.04")
        assert entry.estimated_cost.input_cost == Money.from_usd_str("0.01")
        assert entry.estimated_cost.output_cost == Money.from_usd_str("0.03")

        # Actual side.
        assert entry.actual_tokens.output == 250
        assert entry.actual_cost.input_cost == Money.from_usd_str("0.01")
        assert entry.actual_cost.output_cost == Money.from_usd_str("0.0075")
        assert entry.actual_total_cost == Money.from_usd_str("0.0175")

        # The two are distinguishable, and the unused hold was returned.
        assert entry.variance == Money.from_usd_str("0.0225")

    async def test_every_entry_pins_the_price_catalog_version(self, repo, driver, catalog) -> None:
        """Without this, a price change silently rewrites historical spend."""
        await repo.put_agent_state(active_agent("meter"))
        team = team_policy("100.00")
        agent = agent_policy("meter", "100.00")

        await driver.call(agent=agent, team=team, now=FIXED_NOW)

        entry = repo.ledger_entries("USAGE")[0]
        assert entry.price_catalog_version == catalog.version

    async def test_unused_reservation_is_returned_to_the_budget(
        self, repo, driver, provider
    ) -> None:
        await repo.put_agent_state(active_agent("meter"))
        team = team_policy("100.00")
        agent = agent_policy("meter", "100.00", default_max_output_tokens=1000)
        provider.behaviour.output_tokens = 100

        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        from abc_gateway.domain.scopes import ScopeRef
        from abc_gateway.domain.window import BudgetWindow, WindowType

        state = await repo.get_budget_state(
            ScopeRef.agent("meter"),
            BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW),
            tenant_id="acme",
        )
        # Reserved $0.04, spent $0.013, so $0.027 came back.
        assert state.committed == result.actual_cost
        assert state.reserved == Money.zero()
        assert state.remaining == state.limit - result.actual_cost


class TestCachedAndReasoningNormalization:
    async def test_cached_input_is_not_double_counted_end_to_end(
        self, repo, driver, provider, catalog
    ) -> None:
        await repo.put_agent_state(active_agent("cache"))
        team = team_policy("100.00")
        agent = agent_policy("cache", "100.00")
        provider.behaviour.cached_input_tokens = 800  # of 1000 input

        await driver.call(agent=agent, team=team, now=FIXED_NOW)

        entry = repo.ledger_entries("USAGE")[0]
        # Input tokens are still 1000, not 1800.
        assert entry.actual_tokens.input == 1000
        assert entry.actual_cached_input_tokens == 800
        # 200 @ $10/M + 800 @ $5/M = $0.002 + $0.004
        assert entry.actual_cost.input_cost == Money.from_usd_str("0.002")
        assert entry.actual_cost.cached_input_cost == Money.from_usd_str("0.004")

    async def test_reasoning_tokens_are_visible_but_not_billed_twice(
        self, repo, driver, provider
    ) -> None:
        await repo.put_agent_state(active_agent("reason"))
        team = team_policy("100.00")
        agent = agent_policy("reason", "100.00")
        provider.behaviour.output_tokens = 1000
        provider.behaviour.reasoning_tokens = 600

        await driver.call(agent=agent, team=team, now=FIXED_NOW)

        entry = repo.ledger_entries("USAGE")[0]
        assert entry.actual_reasoning_tokens == 600
        assert entry.actual_tokens.output == 1000  # not 1600
        assert entry.actual_cost.output_cost == Money.from_usd_str("0.03")

    def test_normalized_buckets_are_disjoint(self) -> None:
        usage = ProviderUsage(
            input_tokens=1000,
            output_tokens=500,
            cached_input_tokens=700,
            reasoning_tokens=300,
        )
        norm = NormalizedUsage.from_provider(usage)
        assert norm.uncached_input_tokens + norm.cached_input_tokens == 1000
        assert norm.to_vector() == TokenVector(input=1000, output=500, total=1500)

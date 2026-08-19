"""Model substitution under budget pressure.

The requirement -- "when the preferred model's budget is exhausted, reroute to a
cheaper model" -- hides a logical trap. Read against the agent's *total* budget
it is incoherent: an agent that has spent its entire $50 cannot be allowed to
spend more just because the next model is cheaper. That would make the agent cap
advisory.

The coherent reading is a sub-allocation: part of the agent's budget earmarked
for the premium model. Exhausting it leaves real agent capacity for the fallback
to draw on, and the parent caps still bind. This file tests both halves --
that substitution happens, and that it cannot be used to escape a parent.
"""

from __future__ import annotations

import pytest
from tests.conftest import FIXED_NOW, active_agent, agent_policy, team_policy

from abc_gateway.domain.errors import DenialCode
from abc_gateway.domain.ledger import BudgetDecision
from abc_gateway.domain.money import Money
from abc_gateway.domain.scopes import ScopeType

# Sized so premium costs exactly $0.01 per call and cheap exactly $0.001:
#   premium: 100 in @ $10/M + 300 out @ $30/M = $0.001 + $0.009 = $0.01
#   cheap:   100 in @ $1/M  + 300 out @ $3/M  = $0.0001 + $0.0009 = $0.001
INPUT_TOKENS = 100
OUTPUT_TOKENS = 300


@pytest.fixture
def sized_provider(provider):
    provider.behaviour.input_tokens = INPUT_TOKENS
    provider.behaviour.output_tokens = OUTPUT_TOKENS
    return provider


@pytest.fixture
async def setup(repo, sized_provider):
    await repo.put_agent_state(active_agent("router"))
    team = team_policy("100.00")
    agent = agent_policy(
        "router",
        "0.10",  # agent total
        preferred_model="premium",
        fallback_models=("cheap",),
        allocation_usd="0.02",  # premium sub-allocation: 2 calls
        default_max_output_tokens=OUTPUT_TOKENS,
    )
    return team, agent


class TestSubstitution:
    async def test_preferred_model_is_used_while_its_allocation_lasts(self, driver, setup) -> None:
        team, agent = setup
        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert result.allowed
        assert result.effective_model == "premium"
        assert not result.substituted
        assert result.actual_cost == Money.from_usd_str("0.01")

    async def test_substitutes_when_the_allocation_is_exhausted(
        self, repo, driver, setup, sized_provider
    ) -> None:
        team, agent = setup
        # Two calls consume the $0.02 premium allocation exactly.
        first = await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=2)
        assert all(r.allowed for r in first)
        assert all(r.effective_model == "premium" for r in first)

        third = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert third.allowed
        assert third.substituted
        assert third.grant.requested_model == "premium"
        assert third.effective_model == "cheap"
        # The adapter really did receive the fallback model, not just a label.
        assert sized_provider.invoked_models[-1] == "cheap"

    async def test_the_fallback_is_verified_cheaper_for_this_request(self, driver, setup) -> None:
        """Not assumed cheaper -- computed, for the actual request.

        Configuration drifts and prices change. A "fallback" that is dearer for
        a particular prompt shape would quietly increase spend under the banner
        of a cost control.
        """
        team, agent = setup
        await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=2)
        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert result.grant.estimated_savings is not None
        assert result.grant.estimated_savings > Money.zero()
        # $0.01 premium vs $0.001 cheap.
        assert result.grant.estimated_savings == Money.from_usd_str("0.009")

        chosen = next(a for a in result.attempts if a.model == "cheap")
        assert chosen.eligible
        assert chosen.reason == "cheaper_and_compatible"

    async def test_fallback_is_same_provider_and_strictly_cheaper_by_the_catalog(
        self, driver, setup, catalog
    ) -> None:
        """The two claims the requirement makes explicit, checked independently
        of the engine's own ``estimated_savings`` field.

        ``estimated_savings`` is the routing engine's self-report; a test that
        only reads that field back would not catch a bug in the computation
        that produced it. This recomputes both models' worst-case cost
        directly from the price catalog -- the same catalog the engine reads
        -- and checks the ordering, and the provider identity, from first
        principles rather than trusting the grant to describe itself
        correctly.
        """
        team, agent = setup
        await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=2)
        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert result.allowed
        assert result.substituted

        preferred_model = agent.routing.preferred.model
        preferred_provider = agent.routing.preferred.provider
        fallback_model = result.effective_model

        preferred_cost = catalog.get(preferred_provider, preferred_model).estimate_worst_case(
            input_tokens=INPUT_TOKENS, max_output_tokens=OUTPUT_TOKENS
        ).total
        fallback_cost = catalog.get(preferred_provider, fallback_model).estimate_worst_case(
            input_tokens=INPUT_TOKENS, max_output_tokens=OUTPUT_TOKENS
        ).total

        assert fallback_cost < preferred_cost, (
            "the fallback must be strictly cheaper for this exact request, not "
            "merely configured as a fallback"
        )
        # The fallback's provider matches the preferred model's provider --
        # routing did not smuggle in a cross-provider substitution.
        assert result.grant.provider == preferred_provider
        chosen = next(a for a in result.attempts if a.model == fallback_model)
        assert chosen.provider == preferred_provider

    async def test_substitution_is_recorded_in_the_ledger(self, repo, driver, setup) -> None:
        """Silent substitution is a debugging trap.

        An application owner seeing degraded output needs to find out that
        governance rerouted the traffic, not conclude their prompt broke.
        """
        team, agent = setup
        await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=3)

        entries = repo.ledger_entries("USAGE")
        substituted = [
            e for e in entries if e.decision is BudgetDecision.SUBSTITUTED_PREFERRED_MODEL_BUDGET
        ]
        assert len(substituted) == 1
        assert substituted[0].requested_model == "premium"
        assert substituted[0].effective_model == "cheap"


class TestFallbackCannotEscapeParentBudgets:
    """The half that proves the hierarchy is real."""

    async def test_exhausting_the_agent_budget_hard_blocks_despite_a_fallback(
        self, repo, driver, sized_provider
    ) -> None:
        await repo.put_agent_state(active_agent("router"))
        team = team_policy("100.00")
        agent = agent_policy(
            "router",
            "0.02",  # agent total == the allocation
            preferred_model="premium",
            fallback_models=("cheap",),
            allocation_usd="0.02",
            default_max_output_tokens=OUTPUT_TOKENS,
        )

        results = await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=2)
        assert all(r.allowed for r in results)

        invocations_before = sized_provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not blocked.allowed, (
            "the agent budget is spent; falling back to a cheaper model must "
            "not create additional capacity"
        )
        assert blocked.denial.code is DenialCode.BUDGET_EXHAUSTED
        assert blocked.denial.blocking_scope.type is ScopeType.AGENT
        assert sized_provider.invocation_count == invocations_before

    async def test_an_exhausted_team_budget_aborts_the_chain_immediately(
        self, repo, driver, sized_provider
    ) -> None:
        """A team denial is a full stop, not a routing signal.

        Trying every fallback in turn would waste latency and report
        "no eligible model" when the truthful answer is "the team is out of
        money".
        """
        await repo.put_agent_state(active_agent("router"))
        team = team_policy("0.01")  # room for exactly one premium call
        agent = agent_policy(
            "router",
            "10.00",
            preferred_model="premium",
            fallback_models=("cheap",),
            default_max_output_tokens=OUTPUT_TOKENS,
        )

        first = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert first.allowed

        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert not blocked.allowed
        assert blocked.denial.blocking_scope.type is ScopeType.TEAM
        # It did not fall through to "cheap" and report a routing failure.
        assert blocked.denial.code is DenialCode.BUDGET_EXHAUSTED


class TestCapabilityAwareRouting:
    async def test_refuses_a_cheaper_model_that_cannot_serve_the_request(
        self, repo, driver, sized_provider
    ) -> None:
        """Cheaper is not the same as interchangeable.

        `test::incapable` is the cheapest model in the catalog and supports no
        tools. Routing a tool-using request to it would not save money -- it
        would fail, or silently return something worse.
        """
        await repo.put_agent_state(active_agent("router"))
        team = team_policy("100.00")
        agent = agent_policy(
            "router",
            "0.10",
            preferred_model="premium",
            fallback_models=("incapable", "cheap"),
            allocation_usd="0.01",  # one premium call, then substitution
            default_max_output_tokens=OUTPUT_TOKENS,
        )
        tools = ({"name": "search", "parameters": {}},)

        await driver.call(agent=agent, team=team, now=FIXED_NOW, tools=tools)
        result = await driver.call(agent=agent, team=team, now=FIXED_NOW, tools=tools)

        assert result.allowed
        # Skipped the cheapest option and took the next compatible one.
        assert result.effective_model == "cheap"

        rejected = next(a for a in result.attempts if a.model == "incapable")
        assert not rejected.eligible
        assert rejected.reason == "incompatible:tools"

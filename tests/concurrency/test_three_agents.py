"""Three agents competing for one team budget.

Team $1.00, three agents at $0.50 each. The agents' limits deliberately sum to
$1.50 -- more than the team has -- so the team budget is the real constraint and
the agents genuinely contend for it. Overcommitting child budgets like this is
normal in practice; the parent cap is what makes it safe.

The assertions are about money, not about HTTP status codes: the invariant holds
at every scope, every dollar the team recorded is traceable to an agent, and
nothing stays reserved once the dust settles.
"""

from __future__ import annotations

import pytest
from tests.conftest import FIXED_NOW, TEAM, TENANT, active_agent, agent_policy, team_policy
from tests.support.concurrency import run_concurrently

from abc_gateway.domain.money import Money
from abc_gateway.domain.scopes import ScopeRef
from abc_gateway.domain.window import BudgetWindow, WindowType

pytestmark = pytest.mark.serial

AGENTS = ("agent-a", "agent-b", "agent-c")
CALLS_PER_AGENT = 8


def month_window() -> BudgetWindow:
    return BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW)


@pytest.fixture
async def setup(repo):
    for agent_id in AGENTS:
        await repo.put_agent_state(active_agent(agent_id))
    team = team_policy("1.00")
    agents = {a: agent_policy(a, "0.50") for a in AGENTS}
    return team, agents


class TestThreeAgentConcurrency:
    async def test_invariant_holds_at_every_scope(self, repo, driver, setup) -> None:
        team, agents = setup

        async def one_call(index: int):
            agent = agents[AGENTS[index % len(AGENTS)]]
            return await driver.call(
                agent=agent, team=team, now=FIXED_NOW, idempotency_key=f"c-{index}"
            )

        total = CALLS_PER_AGENT * len(AGENTS)
        results = run_concurrently(one_call, total)

        team_state = await repo.get_budget_state(
            ScopeRef.team(TEAM), month_window(), tenant_id=TENANT
        )
        assert team_state.committed + team_state.reserved <= team_state.limit
        assert team_state.invariant_holds()

        for agent_id in AGENTS:
            state = await repo.get_budget_state(
                ScopeRef.agent(agent_id), month_window(), tenant_id=TENANT
            )
            assert state.committed + state.reserved <= state.limit, (
                f"{agent_id} exceeded its own cap"
            )
            assert state.invariant_holds()

        allowed = [r for r in results if r.allowed]
        assert allowed, "at least some requests should have succeeded"

    async def test_team_spend_equals_the_sum_of_agent_spend(self, repo, driver, setup) -> None:
        """Every dollar the team recorded must be traceable to an agent.

        A mismatch means a request was charged to one scope and not another --
        the accounting equivalent of money appearing from nowhere.
        """
        team, agents = setup

        async def one_call(index: int):
            agent = agents[AGENTS[index % len(AGENTS)]]
            return await driver.call(
                agent=agent, team=team, now=FIXED_NOW, idempotency_key=f"s-{index}"
            )

        run_concurrently(one_call, CALLS_PER_AGENT * len(AGENTS))

        team_state = await repo.get_budget_state(
            ScopeRef.team(TEAM), month_window(), tenant_id=TENANT
        )
        agent_total = Money.zero()
        for agent_id in AGENTS:
            state = await repo.get_budget_state(
                ScopeRef.agent(agent_id), month_window(), tenant_id=TENANT
            )
            agent_total = agent_total + state.committed

        assert team_state.committed == agent_total

    async def test_no_reservations_remain_outstanding(self, repo, driver, setup) -> None:
        """Every hold must be resolved once the calls settle.

        A leaked reservation is money that is neither spent nor spendable -- it
        silently shrinks the budget until someone notices the numbers no longer
        add up.
        """
        team, agents = setup

        async def one_call(index: int):
            agent = agents[AGENTS[index % len(AGENTS)]]
            return await driver.call(
                agent=agent, team=team, now=FIXED_NOW, idempotency_key=f"o-{index}"
            )

        run_concurrently(one_call, CALLS_PER_AGENT * len(AGENTS))

        for scope in (ScopeRef.team(TEAM), *(ScopeRef.agent(a) for a in AGENTS)):
            state = await repo.get_budget_state(scope, month_window(), tenant_id=TENANT)
            assert state.reserved == Money.zero(), f"{scope} leaked a reservation"
            assert state.open_reservations == 0
            assert state.pending == Money.zero()

    async def test_the_team_cap_binds_before_the_agent_caps(self, repo, driver, setup) -> None:
        """$1.50 of agent budget cannot spend more than the team's $1.00."""
        team, agents = setup

        async def one_call(index: int):
            agent = agents[AGENTS[index % len(AGENTS)]]
            return await driver.call(
                agent=agent, team=team, now=FIXED_NOW, idempotency_key=f"t-{index}"
            )

        # 45 calls at $0.04 would be $1.80 if nothing stopped them.
        run_concurrently(one_call, 45)

        team_state = await repo.get_budget_state(
            ScopeRef.team(TEAM), month_window(), tenant_id=TENANT
        )
        assert team_state.committed <= Money.from_usd_str("1.00")
        assert team_state.overage == Money.zero()

    async def test_one_agent_cannot_starve_the_others_of_their_own_cap(
        self, repo, driver, setup
    ) -> None:
        """Isolation: an agent's own cap still binds it even with team room."""
        team, agents = setup
        heavy = agents["agent-a"]

        # $0.50 agent cap at $0.04 per call is 12 calls; ask for 20.
        results = await driver.spend(agent=heavy, team=team, now=FIXED_NOW, times=20)
        allowed = [r for r in results if r.allowed]

        assert len(allowed) == 12
        state = await repo.get_budget_state(
            ScopeRef.agent("agent-a"), month_window(), tenant_id=TENANT
        )
        assert state.committed == Money.from_usd_str("0.48")
        assert state.committed <= state.limit

        # And the team still has capacity for the other two agents.
        team_state = await repo.get_budget_state(
            ScopeRef.team(TEAM), month_window(), tenant_id=TENANT
        )
        assert team_state.remaining > Money.zero()

"""Every rejection path costs nothing, proven the strong way.

"A blocked request must not reach the provider" is the entire reason this
system exists, and it is easy to assert weakly: check that `provider.invoke`
was called the expected number of times. That can pass by accident if a bug
happens to route around the provider for an unrelated reason.

Each test here does two things instead: it snapshots `provider.invocation_count`
before and after the denied call, *and* it relies on `FakeProvider` refusing to
run at all for a reservation that is not currently `RESERVED`
(`ProviderInvokedWithoutReservation` -- see `providers/fake.py`). If a bug ever
let a denied request slip through to the provider, this second mechanism raises
regardless of what any assertion in this file checks for.

Six independent ways a request can be denied are covered, because each is a
different code path that could regress independently:

* the hard cap (money exhausted)
* an agent paused (runaway or admin)
* a session already closed
* a request larger than the scope's entire limit
* a token quota, with dollars still available
* a model allocation exhausted with no eligible fallback
"""

from __future__ import annotations

from tests.conftest import (
    FIXED_NOW,
    TENANT,
    active_agent,
    agent_policy,
    make_session,
    team_policy,
)

from abc_gateway.domain.agent import AgentState, AgentStatus
from abc_gateway.domain.errors import DenialCode
from abc_gateway.domain.session import SessionCloseReason, SessionStatus


class TestZeroProviderInvocationOnDenial:
    async def test_hard_cap_costs_nothing(self, repo, driver, provider) -> None:
        await repo.put_agent_state(active_agent("capped"))
        team = team_policy("100.00")
        agent = agent_policy("capped", "0.04")  # exactly one call fits

        first = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert first.allowed

        before = provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.BUDGET_EXHAUSTED
        assert provider.invocation_count == before

    async def test_a_paused_agent_costs_nothing(self, repo, driver, provider) -> None:
        await repo.put_agent_state(
            AgentState(
                agent_id="paused",
                tenant_id=TENANT,
                team_id="engineering",
                status=AgentStatus.PAUSED_RUNAWAY,
            )
        )
        team = team_policy("100.00")
        agent = agent_policy("paused", "10.00")

        before = provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.AGENT_PAUSED
        assert provider.invocation_count == before

    async def test_admin_paused_agent_costs_nothing(self, repo, driver, provider) -> None:
        await repo.put_agent_state(
            AgentState(
                agent_id="paused-admin",
                tenant_id=TENANT,
                team_id="engineering",
                status=AgentStatus.PAUSED_ADMIN,
            )
        )
        team = team_policy("100.00")
        agent = agent_policy("paused-admin", "10.00")

        before = provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.AGENT_PAUSED
        assert provider.invocation_count == before

    async def test_a_closed_session_costs_nothing(self, repo, driver, provider) -> None:
        await repo.put_agent_state(active_agent("chatter"))
        team = team_policy("100.00")
        agent = agent_policy("chatter", "10.00", session_budget_usd="0.04")
        session = make_session("ses_zero", "chatter", "0.04")
        await repo.put_session(session)

        # $0.04 session, one call spends it exactly -- closes on settlement.
        first = await driver.call(agent=agent, team=team, session=session, now=FIXED_NOW)
        assert first.allowed
        stored = await repo.get_session(TENANT, "ses_zero")
        assert stored.status is SessionStatus.CLOSED_BUDGET

        before = provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, session=stored, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.SESSION_CLOSED
        assert provider.invocation_count == before

    async def test_the_next_request_that_would_exceed_a_session_costs_nothing(
        self, repo, driver, provider
    ) -> None:
        """Closure path B: the request itself is what triggers the closure.

        $0.05 session, $0.04 per call: the first call fits ($0.01 left), and
        the second -- needing another $0.04 against $0.01 remaining -- is the
        one that would exceed. That is the request that must cost nothing.
        """
        await repo.put_agent_state(active_agent("chatter2"))
        team = team_policy("100.00")
        agent = agent_policy("chatter2", "10.00", session_budget_usd="0.05")
        session = make_session("ses_b", "chatter2", "0.05")
        await repo.put_session(session)

        first = await driver.call(agent=agent, team=team, session=session, now=FIXED_NOW)
        assert first.allowed

        before = provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, session=session, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.BUDGET_EXHAUSTED
        assert blocked.effects is not None
        assert blocked.effects.session_closed
        # $0.01 remains -- above zero, so the reason is "too little left to be
        # useful", not "exhausted". Either way the session is done and the
        # request that discovered that cost nothing.
        assert blocked.effects.session_close_reason is SessionCloseReason.REMAINING_BELOW_VIABLE
        assert provider.invocation_count == before

    async def test_an_oversized_request_costs_nothing(self, repo, driver, provider) -> None:
        """The request alone is larger than the scope's entire limit."""
        await repo.put_agent_state(active_agent("tiny"))
        team = team_policy("100.00")
        agent = agent_policy("tiny", "0.01")  # smaller than any call this driver makes

        before = provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.EXCEEDS_WINDOW_LIMIT
        assert provider.invocation_count == before

    async def test_a_token_quota_denial_costs_nothing_while_dollars_remain(
        self, repo, driver, provider
    ) -> None:
        await repo.put_agent_state(active_agent("quota2"))
        team = team_policy("1000.00")
        agent = agent_policy(
            "quota2", "1000.00", max_output_tokens=1500  # one call fits, a second does not
        )

        first = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert first.allowed

        before = provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.TOKEN_QUOTA_EXCEEDED
        assert provider.invocation_count == before

    async def test_an_exhausted_allocation_with_no_fallback_costs_nothing(
        self, repo, driver, provider
    ) -> None:
        """No eligible fallback configured: exhausting the allocation is terminal."""
        await repo.put_agent_state(active_agent("solo"))
        team = team_policy("100.00")
        agent = agent_policy(
            "solo",
            "10.00",
            preferred_model="premium",
            fallback_models=(),  # nothing to route to
            allocation_usd="0.04",  # exactly one premium call
        )

        first = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert first.allowed

        before = provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.BUDGET_EXHAUSTED
        assert provider.invocation_count == before

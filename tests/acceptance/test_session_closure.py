"""Session lifecycle and both closure paths.

There is a wording trap in the usual statement of this requirement -- "close the
session when it exceeds $2". A correct hard controller never lets a session
exceed its cap at all. It closes either when settlement lands exactly on the
limit, or when the *next* request's worst-case cost would not fit, and in the
second case it closes before that request reaches a provider.

Both paths are tested, because implementing only the first leaves a session that
can be nibbled at forever by requests too small to trip it and too numerous to
ignore.
"""

from __future__ import annotations

import pytest
from tests.conftest import (
    FIXED_NOW,
    TENANT,
    active_agent,
    agent_policy,
    make_session,
    team_policy,
)

from abc_gateway.domain.errors import DenialCode
from abc_gateway.domain.money import Money
from abc_gateway.domain.session import SessionCloseReason, SessionStatus


@pytest.fixture
async def setup(repo):
    await repo.put_agent_state(active_agent("chatter"))
    team = team_policy("100.00")
    # Agent has plenty; the session is the binding constraint.
    agent = agent_policy("chatter", "10.00", session_budget_usd="0.20")
    session = make_session("ses_a", "chatter", "0.20")
    await repo.put_session(session)
    return team, agent, session


class TestExactCapClosure:
    """Path A: settlement lands exactly on the session limit."""

    async def test_session_closes_when_spend_reaches_the_cap(self, repo, driver, setup) -> None:
        team, agent, session = setup
        # $0.20 session, $0.04 per call: five calls land exactly on the cap.
        results = await driver.spend(
            agent=agent, team=team, session=session, now=FIXED_NOW, times=5
        )
        assert all(r.allowed for r in results)

        stored = await repo.get_session(TENANT, "ses_a")
        assert stored.status is SessionStatus.CLOSED_BUDGET
        assert stored.close_reason is SessionCloseReason.BUDGET_EXHAUSTED

    async def test_session_never_exceeds_its_cap(self, repo, driver, setup) -> None:
        team, agent, session = setup
        await driver.spend(agent=agent, team=team, session=session, now=FIXED_NOW, times=10)

        from abc_gateway.domain.scopes import ScopeRef
        from abc_gateway.domain.window import BudgetWindow, WindowType

        state = await repo.get_budget_state(
            ScopeRef.session("ses_a"),
            BudgetWindow.for_instant(WindowType.SESSION, FIXED_NOW, session_id="ses_a"),
            tenant_id=TENANT,
        )
        assert state.committed <= state.limit
        assert state.committed == Money.from_usd_str("0.20")


class TestWouldExceedClosure:
    """Path B: the next request would not fit, so it is refused and the session ends."""

    async def test_request_rejected_before_the_provider_and_session_closed(
        self, repo, driver, provider
    ) -> None:
        await repo.put_agent_state(active_agent("chatter"))
        team = team_policy("100.00")
        # $0.10 session: two $0.04 calls leave $0.02 -- not enough for a third.
        agent = agent_policy("chatter", "10.00", session_budget_usd="0.10")
        session = make_session("ses_b", "chatter", "0.10")
        await repo.put_session(session)

        results = await driver.spend(
            agent=agent, team=team, session=session, now=FIXED_NOW, times=2
        )
        assert all(r.allowed for r in results)

        invocations_before = provider.invocation_count

        blocked = await driver.call(agent=agent, team=team, session=session, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code is DenialCode.BUDGET_EXHAUSTED
        assert blocked.denial.blocking_scope.id == "ses_b"
        # $0.02 remained, but the request needed $0.04.
        assert blocked.denial.available == Money.from_usd_str("0.02")
        assert blocked.denial.requested == Money.from_usd_str("0.04")

        # Refused before the provider, and the session is now closed.
        assert provider.invocation_count == invocations_before
        stored = await repo.get_session(TENANT, "ses_b")
        assert stored.status is SessionStatus.CLOSED_BUDGET

    async def test_subsequent_requests_are_rejected_as_session_closed(
        self, repo, driver, provider
    ) -> None:
        await repo.put_agent_state(active_agent("chatter"))
        team = team_policy("100.00")
        agent = agent_policy("chatter", "10.00", session_budget_usd="0.10")
        session = make_session("ses_c", "chatter", "0.10")
        await repo.put_session(session)

        await driver.spend(agent=agent, team=team, session=session, now=FIXED_NOW, times=3)
        invocations_before = provider.invocation_count

        again = await driver.call(agent=agent, team=team, session=session, now=FIXED_NOW)

        assert not again.allowed
        # Now the session status itself is the reason, not the balance.
        assert again.denial.code is DenialCode.SESSION_CLOSED
        assert provider.invocation_count == invocations_before

    async def test_a_single_oversized_request_does_not_close_a_healthy_session(
        self, repo, driver
    ) -> None:
        """One request being too large is not the same as the session being over.

        Closing here would end a session that still has most of its budget,
        because someone sent one unusually long prompt.
        """
        await repo.put_agent_state(active_agent("chatter"))
        team = team_policy("100.00")
        agent = agent_policy(
            "chatter",
            "10.00",
            session_budget_usd="0.10",
            # Raised so the client's own large ceiling is what binds here,
            # rather than the policy cap silently shrinking the request.
            default_max_output_tokens=20_000,
        )
        session = make_session("ses_d", "chatter", "0.10")
        await repo.put_session(session)

        # Untouched $0.10 session, but this one call would need $0.31.
        result = await driver.call(
            agent=agent,
            team=team,
            session=session,
            now=FIXED_NOW,
            max_output_tokens=10_000,
        )

        assert not result.allowed
        assert result.denial.code is DenialCode.EXCEEDS_WINDOW_LIMIT
        stored = await repo.get_session(TENANT, "ses_d")
        assert stored.status is SessionStatus.OPEN, (
            "one oversized request must not close a session that still has budget"
        )

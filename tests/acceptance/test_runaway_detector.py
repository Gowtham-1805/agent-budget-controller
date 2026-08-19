"""The runaway circuit breaker.

An agent spending more than 20% of its monthly budget inside a rolling 60-minute
window is paused and requires human review.

The tests that matter most here are the two that a simpler implementation gets
wrong: the burst that straddles a clock hour (invisible to calendar buckets),
and the duplicate stream delivery (which must not manufacture a runaway that
never happened).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.conftest import FIXED_NOW, TENANT, active_agent, agent_policy, team_policy

from abc_gateway.domain.agent import AgentStatus, RunawayPolicy
from abc_gateway.domain.alerts import AlertKind
from abc_gateway.domain.money import Money
from abc_gateway.runaway import ReviewError, ReviewService, RunawayDetector

MONTHLY_LIMIT = Money.from_usd_str("10.00")
THRESHOLD = Money.from_usd_str("2.00")  # 20% of $10


@pytest.fixture
def detector(repo) -> RunawayDetector:
    return RunawayDetector(repo)


@pytest.fixture
def review(repo) -> ReviewService:
    return ReviewService(repo)


@pytest.fixture
async def spender(repo, driver):
    await repo.put_agent_state(active_agent("looper"))
    return team_policy("1000.00"), agent_policy("looper", "10.00")


async def observe_spend(detector, repo, driver, team, agent, *, times: int, now):
    """Make governed calls and feed their ledger entries to the detector."""
    results = []
    for index in range(times):
        await driver.call(
            agent=agent,
            team=team,
            now=now,
            idempotency_key=f"run-{now.isoformat()}-{index}",
        )
        entry = repo.ledger_entries("USAGE")[-1]
        results.append(await detector.observe(entry, monthly_limit=MONTHLY_LIMIT, now=now))
    return results


class TestThreshold:
    def test_threshold_is_twenty_percent_of_the_monthly_budget(self) -> None:
        assert RunawayPolicy().threshold(MONTHLY_LIMIT) == THRESHOLD

    async def test_spend_below_the_threshold_does_not_trip(
        self, repo, driver, detector, spender
    ) -> None:
        team, agent = spender
        # 10 calls at $0.04 = $0.40, well under $2.00.
        results = await observe_spend(detector, repo, driver, team, agent, times=10, now=FIXED_NOW)

        assert not any(r.tripped for r in results)
        state = await repo.get_agent_state(TENANT, "looper")
        assert state.status is AgentStatus.ACTIVE

    async def test_crossing_the_threshold_pauses_the_agent(
        self, repo, driver, detector, spender
    ) -> None:
        team, agent = spender
        # 51 calls at $0.04 = $2.04, just over $2.00.
        results = await observe_spend(detector, repo, driver, team, agent, times=51, now=FIXED_NOW)

        tripped = [r for r in results if r.tripped]
        assert tripped
        assert tripped[0].paused
        assert tripped[0].rolling_spend > THRESHOLD

        state = await repo.get_agent_state(TENANT, "looper")
        assert state.status is AgentStatus.PAUSED_RUNAWAY
        assert state.review_required is True

        alerts = repo.alerts(AlertKind.RUNAWAY_DETECTED.value)
        assert len(alerts) == 1

    async def test_a_paused_agent_is_rejected_before_the_provider(
        self, repo, driver, detector, provider, spender
    ) -> None:
        """The circuit breaker has to actually break the circuit."""
        team, agent = spender
        await observe_spend(detector, repo, driver, team, agent, times=51, now=FIXED_NOW)

        invocations_before = provider.invocation_count
        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert not blocked.allowed
        assert blocked.denial.code.value == "agent_paused"
        assert provider.invocation_count == invocations_before

    async def test_pausing_one_agent_does_not_affect_another(
        self, repo, driver, detector, spender
    ) -> None:
        """Isolation: a runaway agent must not take the team down with it."""
        team, agent = spender
        await repo.put_agent_state(active_agent("healthy"))
        healthy = agent_policy("healthy", "10.00")

        await observe_spend(detector, repo, driver, team, agent, times=51, now=FIXED_NOW)

        result = await driver.call(agent=healthy, team=team, now=FIXED_NOW)
        assert result.allowed


class TestRollingWindow:
    async def test_a_burst_straddling_the_hour_boundary_is_caught(
        self, repo, driver, detector, spender
    ) -> None:
        """The case fixed hourly buckets are blind to.

            11:50-11:59  spend ~$1.08
            12:00-12:09  spend ~$1.08

        Two calendar hours each see about $1.08 and neither trips a $2.00
        threshold. A rolling window sees $2.16 and does -- and a burst spanning
        the boundary is exactly the shape a recursive loop produces.
        """
        team, agent = spender
        before = FIXED_NOW.replace(hour=11, minute=50)
        after = FIXED_NOW.replace(hour=12, minute=5)

        first = await observe_spend(detector, repo, driver, team, agent, times=27, now=before)
        assert not any(r.tripped for r in first), "first half alone must not trip"

        second = await observe_spend(detector, repo, driver, team, agent, times=27, now=after)

        assert any(r.tripped for r in second), (
            "a burst spanning the hour boundary must trip a rolling window"
        )

    async def test_spend_outside_the_window_no_longer_counts(
        self, repo, driver, detector, spender
    ) -> None:
        """The window really does roll: old spend ages out."""
        team, agent = spender
        early = FIXED_NOW
        much_later = FIXED_NOW + timedelta(hours=3)

        await observe_spend(detector, repo, driver, team, agent, times=40, now=early)

        results = await observe_spend(detector, repo, driver, team, agent, times=5, now=much_later)

        assert not any(r.tripped for r in results)
        # Only the recent five calls are inside the window.
        assert results[-1].rolling_spend == Money.from_usd_str("0.20")


class TestIdempotency:
    async def test_a_redelivered_entry_is_not_counted_twice(
        self, repo, driver, detector, spender
    ) -> None:
        """Stream delivery is at-least-once.

        Counting a redelivery would inflate the rolling total and could
        manufacture a runaway that never happened -- pausing a healthy agent.
        """
        team, agent = spender
        await driver.call(agent=agent, team=team, now=FIXED_NOW)
        entry = repo.ledger_entries("USAGE")[-1]

        first = await detector.observe(entry, monthly_limit=MONTHLY_LIMIT, now=FIXED_NOW)
        second = await detector.observe(entry, monthly_limit=MONTHLY_LIMIT, now=FIXED_NOW)

        assert first.counted
        assert not second.counted
        assert second.duplicate
        assert first.rolling_spend == Money.from_usd_str("0.04")

    async def test_a_duplicate_trip_does_not_pause_twice(
        self, repo, driver, detector, spender
    ) -> None:
        team, agent = spender
        results = await observe_spend(detector, repo, driver, team, agent, times=55, now=FIXED_NOW)

        paused = [r for r in results if r.paused]
        assert len(paused) == 1, "the agent must be paused exactly once"
        assert len(repo.alerts(AlertKind.RUNAWAY_DETECTED.value)) == 1


class TestHumanReview:
    async def test_resume_returns_the_agent_to_service_with_an_audit_record(
        self, repo, driver, detector, review, spender
    ) -> None:
        team, agent = spender
        await observe_spend(detector, repo, driver, team, agent, times=51, now=FIXED_NOW)

        record = await review.resume(
            TENANT,
            "looper",
            actor="alice@example.com",
            reason="fixed the retry loop in deploy 8f13c2",
            now=FIXED_NOW,
        )

        assert record.previous_state == AgentStatus.PAUSED_RUNAWAY.value
        assert record.new_state == AgentStatus.ACTIVE.value
        assert record.actor == "alice@example.com"

        state = await repo.get_agent_state(TENANT, "looper")
        assert state.status is AgentStatus.ACTIVE

        # The record is permanent, not a transient log line.
        audit = await repo.get_audit_events(TENANT)
        assert any(e["action"] == "agent.resumed" for e in audit)

    async def test_a_resumed_agent_can_spend_again(
        self, repo, driver, detector, review, spender
    ) -> None:
        team, agent = spender
        await observe_spend(detector, repo, driver, team, agent, times=51, now=FIXED_NOW)
        await review.resume(TENANT, "looper", actor="alice", reason="loop fixed", now=FIXED_NOW)

        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert result.allowed

    async def test_resume_requires_a_reason(self, repo, driver, detector, review, spender) -> None:
        """Resuming a runaway without recording why is how the same loop ships twice."""
        team, agent = spender
        await observe_spend(detector, repo, driver, team, agent, times=51, now=FIXED_NOW)

        with pytest.raises(ReviewError, match="reason is required"):
            await review.resume(TENANT, "looper", actor="alice", reason="  ", now=FIXED_NOW)

    async def test_manual_pause_and_resume_are_both_audited(self, repo, review) -> None:
        await repo.put_agent_state(active_agent("manual"))

        await review.pause(
            TENANT,
            "manual",
            actor="bob",
            reason="suspected prompt injection",
            now=FIXED_NOW,
        )
        state = await repo.get_agent_state(TENANT, "manual")
        assert state.status is AgentStatus.PAUSED_ADMIN

        await review.resume(
            TENANT,
            "manual",
            actor="bob",
            reason="investigated, false positive",
            now=FIXED_NOW,
        )

        audit = await repo.get_audit_events(TENANT)
        actions = [e["action"] for e in audit]
        assert actions == ["agent.paused", "agent.resumed"]
        assert all(e["reason"] for e in audit)

    async def test_resuming_an_active_agent_is_refused(self, repo, review) -> None:
        await repo.put_agent_state(active_agent("fine"))
        with pytest.raises(ReviewError, match="already ACTIVE"):
            await review.resume(TENANT, "fine", actor="bob", reason="n/a", now=FIXED_NOW)

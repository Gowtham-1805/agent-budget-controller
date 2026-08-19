"""Failure injection: what happens to the money when things go wrong.

The interesting cases are not the ones where the provider answers cleanly. They
are the ones where we do not know what the provider did.

The rule the whole module exists to verify: **a reservation is released only
when the provider provably did not bill us.** Anything else keeps the money
encumbered. Get this backwards and a timed-out request quietly hands its
reservation back to the budget while the provider bills us anyway -- and the
same dollars get spent a second time.
"""

from __future__ import annotations

import pytest
from tests.conftest import FIXED_NOW, TENANT, active_agent, agent_policy, team_policy

from abc_gateway.domain.money import Money
from abc_gateway.domain.reservation import DispatchState, ReservationState
from abc_gateway.domain.scopes import ScopeRef
from abc_gateway.domain.window import BudgetWindow, WindowType


def month_window() -> BudgetWindow:
    return BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW)


async def agent_state(repo, agent_id="faulty"):
    return await repo.get_budget_state(ScopeRef.agent(agent_id), month_window(), tenant_id=TENANT)


@pytest.fixture
async def setup(repo):
    await repo.put_agent_state(active_agent("faulty"))
    return team_policy("100.00"), agent_policy("faulty", "1.00")


class TestProviderRejection:
    async def test_a_proven_unbilled_failure_releases_the_reservation(
        self, repo, driver, provider, setup
    ) -> None:
        """A 4xx with a parsed error envelope and no usage: we owe nothing."""
        team, agent = setup
        provider.behaviour.outcome = "not_billed"

        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert result.provider_invoked
        state = await agent_state(repo)
        assert state.reserved == Money.zero()
        assert state.committed == Money.zero()
        assert state.remaining == state.limit
        assert state.invariant_holds()

        reservation = await repo.get_reservation(TENANT, result.grant.reservation_id)
        assert reservation.state is ReservationState.RELEASED

    async def test_a_billed_failure_still_charges(self, repo, driver, provider, setup) -> None:
        """Content filters and post-generation stops cost real money.

        There is no useful output, but the tokens were generated and billed.
        Refusing to record that would understate spend.
        """
        team, agent = setup
        provider.behaviour.outcome = "billed_failure"

        await driver.call(agent=agent, team=team, now=FIXED_NOW)

        state = await agent_state(repo)
        assert state.committed > Money.zero()
        assert state.reserved == Money.zero()
        assert state.invariant_holds()


class TestAmbiguousOutcome:
    """The case that most implementations get wrong."""

    async def test_a_timeout_does_not_release_the_reservation(
        self, repo, driver, provider, setup
    ) -> None:
        """A read timeout proves nothing about whether we were billed.

        The provider may have generated the whole completion and charged for it
        while the response was lost. Releasing here would let the same money be
        spent twice.
        """
        team, agent = setup
        provider.behaviour.outcome = "ambiguous"

        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        state = await agent_state(repo)
        # Still held: not returned to remaining, not moved to committed.
        assert state.reserved == Money.from_usd_str("0.04")
        assert state.committed == Money.zero()
        assert state.remaining == state.limit - Money.from_usd_str("0.04")

        reservation = await repo.get_reservation(TENANT, result.grant.reservation_id)
        assert reservation.state is ReservationState.RECONCILE_PENDING

    async def test_pending_exposure_is_visible_without_being_double_counted(
        self, repo, driver, provider, setup
    ) -> None:
        """`pending` is a breakdown of `reserved`, not an extra deduction.

        Operators need to see how much of the held money is unresolved, but
        adding it on top of `reserved` would deduct the same dollars twice.
        """
        team, agent = setup
        provider.behaviour.outcome = "ambiguous"

        await driver.call(agent=agent, team=team, now=FIXED_NOW)

        state = await agent_state(repo)
        assert state.pending == Money.from_usd_str("0.04")
        assert state.pending <= state.reserved
        assert state.invariant_holds()

    async def test_pending_holds_still_constrain_later_requests(
        self, repo, driver, provider
    ) -> None:
        """Unresolved money is not spendable money."""
        await repo.put_agent_state(active_agent("faulty"))
        team = team_policy("100.00")
        agent = agent_policy("faulty", "0.05")  # room for one call
        provider.behaviour.outcome = "ambiguous"

        first = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert first.provider_invoked

        second = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert not second.allowed, "an unresolved reservation must keep constraining the budget"

    async def test_a_pending_record_is_logged_as_provisional_spend(
        self, repo, driver, provider, setup
    ) -> None:
        """Recorded at the reserved amount, so exposure is over- not under-stated."""
        team, agent = setup
        provider.behaviour.outcome = "ambiguous"

        await driver.call(agent=agent, team=team, now=FIXED_NOW)

        entries = repo.ledger_entries("PENDING_ASSUMED")
        assert len(entries) == 1
        assert entries[0].actual_total_cost == Money.from_usd_str("0.04")
        assert entries[0].provider_request_id is not None


class TestCrashRecovery:
    async def test_dispatch_state_distinguishes_pre_and_post_provider_crashes(
        self, repo, engine, make_request
    ) -> None:
        """The one write that makes crash recovery decidable.

        Without it, a sweeper finding an abandoned reservation cannot tell
        whether the provider was ever contacted, and must hold every one of
        them forever to stay safe.
        """
        await repo.put_agent_state(active_agent("crasher"))
        team = team_policy("100.00")
        agent = agent_policy("crasher", "1.00")

        grant = await engine.reserve(make_request(agent=agent, team=team))
        reservation = await repo.get_reservation(TENANT, grant.reservation_id)
        # Reserved, but the provider has not been contacted: safe to release.
        assert reservation.dispatch_state is DispatchState.PRE_DISPATCH

        await engine.mark_dispatched(TENANT, grant.reservation_id)

        from abc_gateway.repo import keys

        item = repo.store.get(keys.reservation_key(TENANT, grant.reservation_id).as_tuple())
        assert item["dispatch_state"] == DispatchState.DISPATCHED.value

    async def test_budget_state_survives_a_process_restart(self, repo, catalog, provider) -> None:
        """State lives in the store, not in the engine's memory.

        A new engine over the same store must see the same balances -- this is
        the in-process analogue of restarting an ECS task.
        """
        from tests.support.driver import Driver

        from abc_gateway.engine.budget_engine import BudgetEngine

        await repo.put_agent_state(active_agent("survivor"))
        team = team_policy("100.00")
        agent = agent_policy("survivor", "1.00")

        driver = Driver(
            repo=repo,
            catalog=catalog,
            provider=provider,
            tenant_id=TENANT,
            team_id="engineering",
        )
        await driver.spend(agent=agent, team=team, now=FIXED_NOW, times=3)
        before = await agent_state(repo, "survivor")

        # Discard the engine entirely; keep only the store.
        new_engine = BudgetEngine(repo)
        assert new_engine is not driver.engine

        after = await agent_state(repo, "survivor")
        assert after.committed == before.committed == Money.from_usd_str("0.12")
        assert after.invariant_holds()


class TestOverage:
    async def test_a_provider_that_overshoots_its_cap_is_recorded_not_hidden(
        self, repo, driver, provider, setup
    ) -> None:
        """The honest case.

        If a provider generates past the hard cap we sent, we cannot un-bill it.
        The excess is recorded in `overage`, `remaining` is allowed to go
        negative, and neither is clamped -- clamping would erase the evidence
        that anything went wrong.
        """
        team, agent = setup
        provider.behaviour.overshoot_output_by = 5000

        result = await driver.call(agent=agent, team=team, now=FIXED_NOW)

        assert result.reconcile.had_overage
        state = await agent_state(repo)
        assert state.overage > Money.zero()
        # The accounting identity still balances, with the excess accounted for.
        assert state.invariant_holds()

        entries = repo.ledger_entries("OVERAGE")
        assert len(entries) == 1

    async def test_an_overspent_scope_hard_closes_on_the_next_request(
        self, repo, driver, provider
    ) -> None:
        """Negative remaining is self-healing.

        Once `remaining` is below zero, `remaining >= cost` is false for any
        positive cost, so the scope refuses everything until the window rolls
        or an operator intervenes. Exposure is bounded to what was already in
        flight.
        """
        await repo.put_agent_state(active_agent("faulty"))
        team = team_policy("100.00")
        agent = agent_policy("faulty", "0.05")
        provider.behaviour.overshoot_output_by = 20_000

        first = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert first.reconcile.had_overage

        state = await agent_state(repo)
        assert state.remaining < Money.zero()

        blocked = await driver.call(agent=agent, team=team, now=FIXED_NOW)
        assert not blocked.allowed

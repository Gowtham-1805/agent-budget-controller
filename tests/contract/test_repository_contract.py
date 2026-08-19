"""One contract, both backends.

Every test here runs twice: once against the in-memory store and once against
real DynamoDB semantics via moto. That is the point of the plan abstraction --
the engine builds backend-agnostic plans, each backend compiles them, and this
suite is what proves the two compile to the same *behaviour* and not merely to
the same shape.

Without it, the fast in-memory backend would be a comfortable fiction: green
tests that say nothing about what happens in production.
"""

from __future__ import annotations

import pytest
from tests.conftest import (
    FIXED_NOW,
    TEAM,
    TENANT,
    active_agent,
    agent_policy,
    build_request,
    team_policy,
)

from abc_gateway.domain.errors import AuthorizationDenied, DenialCode, ReleaseReason
from abc_gateway.domain.money import Money
from abc_gateway.domain.reservation import ReservationState
from abc_gateway.domain.scopes import ScopeRef, ScopeType
from abc_gateway.domain.usage import ProviderUsage
from abc_gateway.domain.window import BudgetWindow, WindowType
from abc_gateway.engine.budget_engine import BudgetEngine


def month_window() -> BudgetWindow:
    return BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW)


@pytest.fixture
def engine(backend) -> BudgetEngine:
    return BudgetEngine(backend)


@pytest.fixture
async def seeded(backend):
    await backend.put_agent_state(active_agent("contract"))
    return team_policy("10.00"), agent_policy("contract", "1.00")


async def state_of(backend, scope: ScopeRef):
    return await backend.get_budget_state(scope, month_window(), tenant_id=TENANT)


class TestAuthorization:
    async def test_a_reservation_decrements_remaining_and_increments_reserved(
        self, backend, engine, catalog, seeded
    ) -> None:
        team, agent = seeded
        grant = await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))

        assert grant.reserved_cost == Money.from_usd_str("0.04")
        state = await state_of(backend, ScopeRef.agent("contract"))
        assert state.remaining == Money.from_usd_str("0.96")
        assert state.reserved == Money.from_usd_str("0.04")
        assert state.committed == Money.zero()
        assert state.open_reservations == 1
        assert state.invariant_holds()

    async def test_the_window_item_is_created_lazily_at_its_limit(
        self, backend, engine, catalog, seeded
    ) -> None:
        """No window item exists until the first request touches it."""
        team, agent = seeded
        assert await state_of(backend, ScopeRef.agent("contract")) is None

        await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))

        state = await state_of(backend, ScopeRef.agent("contract"))
        assert state.limit == Money.from_usd_str("1.00")

    async def test_every_scope_in_the_hierarchy_is_charged(
        self, backend, engine, catalog, seeded
    ) -> None:
        team, agent = seeded
        await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))

        for scope in (ScopeRef.team(TEAM), ScopeRef.agent("contract")):
            state = await state_of(backend, scope)
            assert state.reserved == Money.from_usd_str("0.04"), f"{scope} not charged"

    async def test_exhaustion_denies_and_names_the_scope(self, backend, engine, catalog) -> None:
        await backend.put_agent_state(active_agent("small"))
        team = team_policy("10.00")
        agent = agent_policy("small", "0.05")

        await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))

        with pytest.raises(AuthorizationDenied) as exc:
            await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))

        denial = exc.value.denial
        assert denial.code is DenialCode.BUDGET_EXHAUSTED
        assert denial.blocking_scope == ScopeRef(ScopeType.AGENT, "small")
        # Only possible because the backend returned the item's pre-image.
        assert denial.available == Money.from_usd_str("0.01")
        assert denial.requested == Money.from_usd_str("0.04")

    async def test_a_paused_agent_is_denied_inside_the_transaction(
        self, backend, engine, catalog
    ) -> None:
        from abc_gateway.domain.agent import AgentStatus

        await backend.put_agent_state(active_agent("paused"))
        await backend.set_agent_status(
            TENANT,
            "paused",
            expected_status=AgentStatus.ACTIVE.value,
            new_status=AgentStatus.PAUSED_RUNAWAY.value,
            reason="test",
            actor="test",
        )
        team = team_policy("10.00")
        agent = agent_policy("paused", "1.00")

        with pytest.raises(AuthorizationDenied) as exc:
            await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))

        assert exc.value.denial.code is DenialCode.AGENT_PAUSED
        # Nothing was charged: the whole transaction was cancelled.
        assert await state_of(backend, ScopeRef.agent("paused")) is None

    async def test_a_token_quota_denies_while_money_remains(self, backend, engine, catalog) -> None:
        await backend.put_agent_state(active_agent("tokens"))
        team = team_policy("100.00")
        agent = agent_policy("tokens", "100.00", max_output_tokens=1500)

        await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))

        with pytest.raises(AuthorizationDenied) as exc:
            await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))

        assert exc.value.denial.code is DenialCode.TOKEN_QUOTA_EXCEEDED
        assert exc.value.denial.token_dimensions == ("output",)
        state = await state_of(backend, ScopeRef.agent("tokens"))
        assert state.remaining > Money.zero()  # dollars were never the issue


class TestSettlement:
    async def test_reconciliation_moves_reserved_into_committed(
        self, backend, engine, catalog, seeded
    ) -> None:
        team, agent = seeded
        grant = await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))
        # Generated 250 of the 1000 tokens it was allowed.
        await engine.reconcile(
            TENANT,
            grant.reservation_id,
            ProviderUsage(input_tokens=1000, output_tokens=250),
            catalog.get("test", "premium"),
            FIXED_NOW,
        )

        state = await state_of(backend, ScopeRef.agent("contract"))
        assert state.committed == Money.from_usd_str("0.0175")
        assert state.reserved == Money.zero()
        # The unused portion of the hold came back.
        assert state.remaining == Money.from_usd_str("0.9825")
        assert state.open_reservations == 0
        assert state.invariant_holds()

    async def test_release_returns_the_hold_untouched(
        self, backend, engine, catalog, seeded
    ) -> None:
        team, agent = seeded
        grant = await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))
        await engine.release(
            TENANT, grant.reservation_id, ReleaseReason.PROVIDER_REJECTED, FIXED_NOW
        )

        state = await state_of(backend, ScopeRef.agent("contract"))
        assert state.remaining == state.limit
        assert state.committed == Money.zero()
        assert state.reserved == Money.zero()

    async def test_pending_keeps_the_money_encumbered(
        self, backend, engine, catalog, seeded
    ) -> None:
        from abc_gateway.domain.errors import PendingReason

        team, agent = seeded
        grant = await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))
        await engine.mark_pending(
            TENANT, grant.reservation_id, PendingReason.PROVIDER_TIMEOUT, FIXED_NOW
        )

        state = await state_of(backend, ScopeRef.agent("contract"))
        # Still held; only the reporting breakdown changed.
        assert state.reserved == Money.from_usd_str("0.04")
        assert state.pending == Money.from_usd_str("0.04")
        assert state.committed == Money.zero()

        reservation = await backend.get_reservation(TENANT, grant.reservation_id)
        assert reservation.state is ReservationState.RECONCILE_PENDING

    async def test_double_reconciliation_is_refused(self, backend, engine, catalog, seeded) -> None:
        """The condition on reservation state is what stops double-counting."""
        from abc_gateway.repo.plans import TransactionDenied

        team, agent = seeded
        grant = await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))
        usage = ProviderUsage(input_tokens=1000, output_tokens=250)
        price = catalog.get("test", "premium")

        await engine.reconcile(TENANT, grant.reservation_id, usage, price, FIXED_NOW)
        committed_once = (await state_of(backend, ScopeRef.agent("contract"))).committed

        with pytest.raises((TransactionDenied, ValueError)):
            await engine.reconcile(TENANT, grant.reservation_id, usage, price, FIXED_NOW)

        after = await state_of(backend, ScopeRef.agent("contract"))
        assert after.committed == committed_once


class TestPersistence:
    async def test_a_reservation_round_trips_with_its_scope_vector(
        self, backend, engine, catalog, seeded
    ) -> None:
        """The stored undo vector is what settlement reverses.

        Recomputing it from policy at settlement time would silently corrupt the
        counters whenever policy changed mid-flight.
        """
        team, agent = seeded
        grant = await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))

        reservation = await backend.get_reservation(TENANT, grant.reservation_id)
        assert reservation is not None
        assert reservation.state is ReservationState.RESERVED
        assert reservation.reserved_cost == Money.from_usd_str("0.04")
        assert {s.scope.type for s in reservation.scopes} == {
            ScopeType.TEAM,
            ScopeType.AGENT,
        }
        assert all(s.cost == Money.from_usd_str("0.04") for s in reservation.scopes)

    async def test_state_is_read_back_identically(self, backend, engine, catalog, seeded) -> None:
        team, agent = seeded
        for index in range(3):
            grant = await engine.reserve(
                build_request(
                    catalog=catalog,
                    agent=agent,
                    team=team,
                    idempotency_key=f"persist-{index}",
                )
            )
            await engine.reconcile(
                TENANT,
                grant.reservation_id,
                ProviderUsage(input_tokens=1000, output_tokens=1000),
                catalog.get("test", "premium"),
                FIXED_NOW,
            )

        state = await state_of(backend, ScopeRef.agent("contract"))
        assert state.committed == Money.from_usd_str("0.12")
        assert state.invariant_holds()


class TestBudgetWindows:
    async def test_a_new_month_starts_from_a_fresh_budget(
        self, backend, engine, catalog, seeded
    ) -> None:
        """The reason windows are keys rather than TTL'd rows.

        September addresses a different item, so it begins at the full limit the
        instant the boundary passes -- no waiting for August's row to be
        collected.
        """
        from datetime import UTC, datetime

        team, agent = seeded
        await engine.reserve(build_request(catalog=catalog, agent=agent, team=team))

        september = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        await engine.reserve(build_request(catalog=catalog, agent=agent, team=team, now=september))

        august = await backend.get_budget_state(
            ScopeRef.agent("contract"), month_window(), tenant_id=TENANT
        )
        sept = await backend.get_budget_state(
            ScopeRef.agent("contract"),
            BudgetWindow.for_instant(WindowType.MONTHLY, september),
            tenant_id=TENANT,
        )

        assert august.window.id == "2026-08"
        assert sept.window.id == "2026-09"
        assert sept.reserved == Money.from_usd_str("0.04")
        assert sept.remaining == Money.from_usd_str("0.96")
        # August's spend did not follow it into September.
        assert august.reserved == Money.from_usd_str("0.04")


class TestPolicyPersistence:
    """``get_agent_policy``/``put_agent_policy``/``get_budget_policy``/
    ``put_budget_policy`` on both backends.

    Every admin control-plane route (create team, create agent, update a
    budget) and the stream processor's runaway detector call these four
    methods directly. The DynamoDB backend previously did not implement them
    at all -- ``DynamoBudgetRepository`` had no ``get_agent_policy`` and the
    rest, despite the interface declaring them and the item keys already
    existing in ``repo/keys.py``. Against a real deployment, creating a team
    or an agent would have raised ``AttributeError`` on the first call. This
    suite is what proves the fix round-trips identically on both backends,
    including the nested routing chain, model allocations, and the two
    fields that are legitimately absent (``session_budget``,
    ``session_min_viable``).
    """

    async def test_a_team_budget_policy_round_trips(self, backend) -> None:
        policy = team_policy("500.00")
        await backend.put_budget_policy(TENANT, policy)

        got = await backend.get_budget_policy(TENANT, ScopeRef.team(TEAM))

        assert got == policy

    async def test_an_unknown_budget_policy_is_none(self, backend) -> None:
        assert await backend.get_budget_policy(TENANT, ScopeRef.team("no-such-team")) is None

    async def test_updating_a_budget_policy_overwrites_it(self, backend) -> None:
        await backend.put_budget_policy(TENANT, team_policy("100.00"))
        await backend.put_budget_policy(TENANT, team_policy("250.00"))

        got = await backend.get_budget_policy(TENANT, ScopeRef.team(TEAM))
        assert got.limit == Money.from_usd_str("250.00")

    async def test_an_agent_policy_with_fallbacks_and_an_allocation_round_trips(
        self, backend
    ) -> None:
        """The shape that actually exercises nested serialisation: a routing
        chain with a fallback, a model allocation sub-budget, and a session
        budget -- none of which are flat scalar fields.
        """
        policy = agent_policy(
            "router",
            "50.00",
            preferred_model="premium",
            fallback_models=("cheap",),
            allocation_usd="40.00",
            session_budget_usd="2.00",
            max_input_tokens=100_000,
            max_output_tokens=50_000,
        )
        await backend.put_agent_policy(policy)

        got = await backend.get_agent_policy(TENANT, "router")

        assert got == policy
        assert got.routing.fallbacks == policy.routing.fallbacks
        assert got.routing.allocations == policy.routing.allocations
        assert got.session_budget == Money.from_usd_str("2.00")

    async def test_an_agent_policy_without_a_session_budget_round_trips_as_none(
        self, backend
    ) -> None:
        """The nullable fields must come back as None, not as zero or missing."""
        policy = agent_policy("no-session", "10.00")
        assert policy.session_budget is None

        await backend.put_agent_policy(policy)
        got = await backend.get_agent_policy(TENANT, "no-session")

        assert got.session_budget is None
        assert got.session_min_viable is None

    async def test_an_unknown_agent_policy_is_none(self, backend) -> None:
        assert await backend.get_agent_policy(TENANT, "no-such-agent") is None

    async def test_updating_an_agent_policy_overwrites_it(self, backend) -> None:
        await backend.put_agent_policy(agent_policy("updateable", "10.00"))
        await backend.put_agent_policy(agent_policy("updateable", "25.00"))

        got = await backend.get_agent_policy(TENANT, "updateable")
        assert got.budget.limit == Money.from_usd_str("25.00")

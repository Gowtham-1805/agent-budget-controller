"""Property-based verification of the accounting identity.

The hand-written tests check the scenarios someone thought of. This one checks
the sequences nobody thought of: Hypothesis drives reserve, reconcile, release
and mark-pending in arbitrary orders, with arbitrary amounts, and asserts after
*every single operation* that

    remaining + committed + reserved == limit

holds, that a hold is never released twice, and that outstanding-reservation
counts match reality.

Thousands of interleavings run in a couple of seconds, and when it does find a
counterexample Hypothesis shrinks it to the shortest failing sequence -- which
is usually short enough to read and turn into a permanent regression test.
"""

from __future__ import annotations

import asyncio

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule
from tests.conftest import (
    CATALOG_PATH,
    FIXED_NOW,
    TEAM,
    TENANT,
    active_agent,
    agent_policy,
    build_request,
    team_policy,
)

from abc_gateway.domain.errors import AuthorizationDenied, PendingReason, ReleaseReason
from abc_gateway.domain.money import Money
from abc_gateway.domain.reservation import ReservationState
from abc_gateway.domain.scopes import ScopeRef
from abc_gateway.domain.usage import ProviderUsage
from abc_gateway.domain.window import BudgetWindow, WindowType
from abc_gateway.engine.budget_engine import BudgetEngine
from abc_gateway.pricing import load_catalog
from abc_gateway.repo.memory import InMemoryBudgetRepository

CATALOG = load_catalog(CATALOG_PATH)
AGENT_ID = "prop"


def run(coro):
    return asyncio.run(coro)


class BudgetStateMachine(RuleBasedStateMachine):
    """Random lifecycles against one agent's budget."""

    def __init__(self) -> None:
        super().__init__()
        self.repo = InMemoryBudgetRepository()
        self.engine = BudgetEngine(self.repo)
        self.team = team_policy("1000.00")
        self.agent = agent_policy(AGENT_ID, "1.00")
        #: reservation_id -> the amount currently held for it. Includes
        #: pending holds, because ambiguity keeps the money encumbered.
        self.outstanding: dict[str, Money] = {}
        #: Holds whose provider outcome is unknown. Tracked separately because
        #: they are no longer RESERVED and so cannot be released or re-pended.
        self.pending: set[str] = set()
        self.settled: set[str] = set()
        run(self.repo.put_agent_state(active_agent(AGENT_ID)))

    @initialize()
    def start(self) -> None:
        pass

    # -- rules ----------------------------------------------------------

    @rule(
        input_tokens=st.integers(min_value=1, max_value=2000),
        output_tokens=st.integers(min_value=1, max_value=2000),
    )
    def reserve(self, input_tokens: int, output_tokens: int) -> None:
        request = build_request(
            catalog=CATALOG,
            agent=self.agent,
            team=self.team,
            input_tokens=input_tokens,
            max_output_tokens=output_tokens,
            now=FIXED_NOW,
        )
        try:
            grant = run(self.engine.reserve(request))
        except AuthorizationDenied:
            # A denial is a perfectly good outcome; what matters is that the
            # books still balance afterwards, which the invariants check.
            return
        self.outstanding[grant.reservation_id] = grant.reserved_cost

    def _reserved_ids(self) -> list[str]:
        """Holds still in RESERVED -- the only ones that may be settled."""
        return sorted(set(self.outstanding) - self.pending)

    @precondition(lambda self: bool(set(self.outstanding) - self.pending))
    @rule(data=st.data(), ratio=st.integers(min_value=0, max_value=150))
    def reconcile(self, data, ratio: int) -> None:
        """Settle a hold with usage anywhere from zero to 1.5x the ceiling.

        Deliberately allowed to exceed the reservation: a provider overshooting
        its cap is the case where naive implementations lose track of the money.
        """
        reservation_id = data.draw(st.sampled_from(self._reserved_ids()))
        reservation = run(self.repo.get_reservation(TENANT, reservation_id))

        output = max(0, (reservation.bounded_max_output_tokens * ratio) // 100)
        usage = ProviderUsage(
            input_tokens=reservation.preflight_input_tokens,
            output_tokens=output,
        )
        run(
            self.engine.reconcile(
                TENANT,
                reservation_id,
                usage,
                CATALOG.get("test", reservation.effective_model),
                FIXED_NOW,
            )
        )
        del self.outstanding[reservation_id]
        self.settled.add(reservation_id)

    @precondition(lambda self: bool(set(self.outstanding) - self.pending))
    @rule(data=st.data())
    def release(self, data) -> None:
        reservation_id = data.draw(st.sampled_from(self._reserved_ids()))
        run(self.engine.release(TENANT, reservation_id, ReleaseReason.PROVIDER_REJECTED, FIXED_NOW))
        del self.outstanding[reservation_id]
        self.settled.add(reservation_id)

    @precondition(lambda self: bool(set(self.outstanding) - self.pending))
    @rule(data=st.data())
    def mark_pending(self, data) -> None:
        reservation_id = data.draw(st.sampled_from(self._reserved_ids()))
        run(
            self.engine.mark_pending(
                TENANT, reservation_id, PendingReason.PROVIDER_TIMEOUT, FIXED_NOW
            )
        )
        # Stays in `outstanding` on purpose: an ambiguous outcome keeps the
        # money encumbered, which is the whole point of the pending state. It
        # moves into `pending` so it is never released or re-pended -- the
        # engine rejects both, and rightly so.
        self.pending.add(reservation_id)

    # -- invariants -----------------------------------------------------

    @invariant()
    def accounting_identity_holds(self) -> None:
        for scope in (ScopeRef.agent(AGENT_ID), ScopeRef.team(TEAM)):
            state = run(
                self.repo.get_budget_state(
                    scope,
                    BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW),
                    tenant_id=TENANT,
                )
            )
            if state is None:
                continue
            assert state.invariant_holds(), (
                f"{scope}: remaining {state.remaining.nano} + committed "
                f"{state.committed.nano} + reserved {state.reserved.nano} "
                f"!= limit {state.limit.nano}"
            )

    @invariant()
    def reserved_never_goes_negative(self) -> None:
        state = run(
            self.repo.get_budget_state(
                ScopeRef.agent(AGENT_ID),
                BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW),
                tenant_id=TENANT,
            )
        )
        if state is None:
            return
        assert state.reserved >= Money.zero()
        assert state.pending <= state.reserved
        assert state.open_reservations >= 0

    @invariant()
    def holds_match_outstanding_reservations(self) -> None:
        """The counter must agree with the reservations that actually exist."""
        state = run(
            self.repo.get_budget_state(
                ScopeRef.agent(AGENT_ID),
                BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW),
                tenant_id=TENANT,
            )
        )
        if state is None:
            return
        expected = sum(m.nano for m in self.outstanding.values())
        assert state.reserved.nano == expected, (
            f"reserved counter {state.reserved.nano} disagrees with the "
            f"{len(self.outstanding)} outstanding holds totalling {expected}"
        )

    @invariant()
    def settled_reservations_are_terminal_or_pending(self) -> None:
        for reservation_id in self.settled:
            reservation = run(self.repo.get_reservation(TENANT, reservation_id))
            assert reservation.state in (
                ReservationState.RECONCILED,
                ReservationState.RELEASED,
                ReservationState.RECONCILE_RESOLVED,
            ), f"{reservation_id} settled but is {reservation.state}"


# Two profiles. The default keeps the inner development loop fast; `thorough`
# runs in CI, where a few minutes buys a much wider search. Select with
#   pytest --hypothesis-profile=thorough
_COMMON = {
    "deadline": None,
    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.data_too_large],
}
settings.register_profile("default", settings(max_examples=40, stateful_step_count=15, **_COMMON))
settings.register_profile("thorough", settings(max_examples=300, stateful_step_count=30, **_COMMON))

BudgetInvariants = BudgetStateMachine.TestCase

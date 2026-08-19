"""Continuous sampling of the core invariant during genuine concurrent load.

The other concurrency suites (``test_hard_cap_race.py``, ``test_three_agents.py``)
check ``committed + reserved <= limit`` once, after the race has settled. That
proves the *final* state is consistent but says nothing about whether the
invariant ever dipped mid-flight: a transient overcommit that self-corrects
before the last write completes would still be visible to any concurrent
reader -- a dashboard poll, another authorization check reading the same scope
-- during that window, which is exactly the exposure this system exists to
close.

This test runs a background sampler thread that reads the governed scope on a
tight loop while workers race for a budget that fits exactly one of them, and
asserts the invariant on *every single sample*, not just the last one. It
repeats the race across several rounds, each against a fresh team scope, to
widen the chance of the sampler's tight loop actually catching a transient
state a single race could get lucky and miss.

Validated by mutation: with the commit-time re-check in
``repo/memory/_txn.py::_check_reserve`` disabled (made unconditionally
permissive), this test fails -- the sampler catches a mid-race state with
``committed + reserved > limit`` well before the race even finishes, which is a
strictly earlier and more direct signal than the after-the-fact assertions in
``test_hard_cap_race.py`` provide.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from tests.conftest import FIXED_NOW, TENANT, active_agent, agent_policy
from tests.support.concurrency import run_concurrently

from abc_gateway.domain.errors import AuthorizationDenied
from abc_gateway.domain.money import Money
from abc_gateway.domain.policy import BudgetPolicy
from abc_gateway.domain.reservation import ReservationGrant
from abc_gateway.domain.scopes import ScopeRef, ScopeType
from abc_gateway.domain.window import BudgetWindow, WindowType

pytestmark = pytest.mark.serial

N_CONCURRENT = 12
N_ROUNDS = 6
TEAM_LIMIT = "0.05"  # ten cents short of the twelve requests, so exactly one wins


def month_window() -> BudgetWindow:
    return BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW)


def round_team_policy(round_index: int) -> BudgetPolicy:
    """A team scope unique to this round, so each round starts pristine."""
    return BudgetPolicy(
        scope_type=ScopeType.TEAM,
        scope_id=f"sampling-round-{round_index}",
        limit=Money.from_usd_str(TEAM_LIMIT),
        window_type=WindowType.MONTHLY,
    )


class TestContinuousInvariantSampling:
    async def test_invariant_holds_on_every_sample_taken_during_the_race(
        self, repo, engine, make_request
    ) -> None:
        samples = 0
        violations: list[str] = []
        stop = threading.Event()
        scopes = [ScopeRef(ScopeType.TEAM, f"sampling-round-{r}") for r in range(N_ROUNDS)]

        def sample_loop() -> None:
            nonlocal samples

            async def read_all():
                readings = []
                for scope in scopes:
                    state = await repo.get_budget_state(scope, month_window(), tenant_id=TENANT)
                    if state is not None:
                        readings.append(state)
                return readings

            while not stop.is_set():
                for state in asyncio.run(read_all()):
                    samples += 1
                    if state.committed + state.reserved > state.limit:
                        violations.append(
                            f"sample#{samples} scope={state.scope.key()}: "
                            f"committed={state.committed} reserved={state.reserved} "
                            f"limit={state.limit}"
                        )
                # No sleep: sample as fast as possible so the window between a
                # transaction's individual item writes is not missed.

        sampler = threading.Thread(target=sample_loop, daemon=True)
        sampler.start()

        try:
            for round_index in range(N_ROUNDS):
                team = round_team_policy(round_index)
                agent = agent_policy(f"sampler-{round_index}", "50.00")
                await repo.put_agent_state(active_agent(f"sampler-{round_index}"))

                async def attempt(index: int, _team=team, _agent=agent, _r=round_index):
                    try:
                        return await engine.reserve(
                            make_request(
                                agent=_agent, team=_team, idempotency_key=f"samp-{_r}-{index}"
                            )
                        )
                    except AuthorizationDenied as exc:
                        return exc.denial

                results = run_concurrently(attempt, N_CONCURRENT)
                granted = [r for r in results if isinstance(r, ReservationGrant)]
                assert len(granted) == 1, (
                    f"round {round_index}: expected exactly 1 grant, got {len(granted)}"
                )
        finally:
            stop.set()
            sampler.join(timeout=5)

        assert samples > 0, "the sampler never got a reading in; this proves nothing"
        assert not violations, (
            f"invariant violated mid-race in {len(violations)}/{samples} samples: "
            f"{violations[:5]}"
        )

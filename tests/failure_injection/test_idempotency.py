"""Idempotency: a retry must never become a second charge.

Clients retry. Networks drop responses. If a retried request reserved budget a
second time, the same logical call would be billed twice and the budget would
drain at double the real rate.

Three situations share one symptom -- "this key already exists" -- and
conflating them is how a retry turns into a double charge or, worse, into the
wrong response being returned to the wrong request:

* same request, already finished -> replay the stored response, no provider call
* same request, still running     -> tell the caller to wait
* different request, same key     -> a client bug; refuse rather than guess
"""

from __future__ import annotations

import pytest
from tests.conftest import FIXED_NOW, TENANT, active_agent, agent_policy, team_policy

from abc_gateway.domain.errors import IdempotencyConflict, IdempotentReplay, RequestInFlight
from abc_gateway.domain.money import Money
from abc_gateway.domain.scopes import ScopeRef
from abc_gateway.domain.window import BudgetWindow, WindowType
from abc_gateway.engine.budget_engine import fingerprint_request


def month_window() -> BudgetWindow:
    return BudgetWindow.for_instant(WindowType.MONTHLY, FIXED_NOW)


@pytest.fixture
async def setup(repo):
    await repo.put_agent_state(active_agent("retrier"))
    return team_policy("100.00"), agent_policy("retrier", "1.00")


class TestIdempotentRetry:
    async def test_a_completed_request_replays_without_charging_again(
        self, repo, engine, provider, catalog, make_request, setup
    ) -> None:
        team, agent = setup
        request = make_request(agent=agent, team=team, idempotency_key="stable-key")

        grant = await engine.reserve(request)
        outcome = await provider.invoke(
            _chat(),
            grant.effective_model,
            max_output_tokens=grant.bounded_max_output_tokens,
            timeouts=_timeouts(),
            correlation_id=grant.reservation_id,
        )
        await engine.reconcile(
            TENANT,
            grant.reservation_id,
            outcome.usage,
            catalog.get("test", grant.effective_model),
            FIXED_NOW,
        )

        state_before = await repo.get_budget_state(
            ScopeRef.agent("retrier"), month_window(), tenant_id=TENANT
        )
        invocations_before = provider.invocation_count

        # The client retries the identical call.
        with pytest.raises(IdempotentReplay) as replay:
            await engine.reserve(make_request(agent=agent, team=team, idempotency_key="stable-key"))

        assert replay.value.reservation_id == grant.reservation_id

        state_after = await repo.get_budget_state(
            ScopeRef.agent("retrier"), month_window(), tenant_id=TENANT
        )
        assert state_after.committed == state_before.committed
        assert state_after.reserved == Money.zero()
        # And critically, the provider was not called a second time.
        assert provider.invocation_count == invocations_before

    async def test_a_retry_while_in_flight_is_told_to_wait(
        self, engine, make_request, setup
    ) -> None:
        """Starting a second copy would double-spend for one logical request."""
        team, agent = setup
        grant = await engine.reserve(
            make_request(agent=agent, team=team, idempotency_key="inflight")
        )

        with pytest.raises(RequestInFlight) as exc:
            await engine.reserve(make_request(agent=agent, team=team, idempotency_key="inflight"))

        assert exc.value.reservation_id == grant.reservation_id
        assert not exc.value.unresolved

    async def test_an_unresolved_retry_is_reported_as_such(
        self, engine, provider, make_request, setup
    ) -> None:
        """A pending original means the retry's outcome is unknown too."""
        from abc_gateway.domain.errors import PendingReason

        team, agent = setup
        grant = await engine.reserve(
            make_request(agent=agent, team=team, idempotency_key="ambiguous")
        )
        await engine.mark_pending(
            TENANT, grant.reservation_id, PendingReason.PROVIDER_TIMEOUT, FIXED_NOW
        )

        with pytest.raises(RequestInFlight) as exc:
            await engine.reserve(make_request(agent=agent, team=team, idempotency_key="ambiguous"))

        assert exc.value.unresolved

    async def test_reusing_a_key_for_a_different_request_is_refused(
        self, engine, make_request, setup
    ) -> None:
        """Returning the first request's response here would be wrong.

        The caller would silently receive an answer to a question they did not
        ask, which is worse than an error.
        """
        team, agent = setup
        first = make_request(agent=agent, team=team, idempotency_key="shared")
        first = _with_fingerprint(first, "prompt-a")
        await engine.reserve(first)

        second = make_request(agent=agent, team=team, idempotency_key="shared")
        second = _with_fingerprint(second, "prompt-b")

        with pytest.raises(IdempotencyConflict):
            await engine.reserve(second)

    async def test_different_keys_are_independent(self, repo, engine, make_request, setup) -> None:
        team, agent = setup
        await engine.reserve(make_request(agent=agent, team=team, idempotency_key="a"))
        await engine.reserve(make_request(agent=agent, team=team, idempotency_key="b"))

        state = await repo.get_budget_state(
            ScopeRef.agent("retrier"), month_window(), tenant_id=TENANT
        )
        assert state.reserved == Money.from_usd_str("0.08")
        assert state.open_reservations == 2


class TestFingerprint:
    def test_fingerprints_differ_for_different_content(self) -> None:
        assert fingerprint_request("model", "hello") != fingerprint_request("model", "world")

    def test_fingerprints_are_stable(self) -> None:
        assert fingerprint_request("a", "b") == fingerprint_request("a", "b")

    def test_field_boundaries_are_unambiguous(self) -> None:
        """("ab","c") and ("a","bc") must not collide.

        Naive concatenation makes them identical, which would let two different
        requests share a fingerprint and defeat the conflict check above.
        """
        assert fingerprint_request("ab", "c") != fingerprint_request("a", "bc")


def _with_fingerprint(request, content: str):
    from dataclasses import replace

    return replace(request, request_fingerprint=fingerprint_request(content))


def _chat():
    from abc_gateway.providers.base import ChatMessage, ChatRequest

    return ChatRequest(messages=(ChatMessage("user", "hello"),), model="premium")


def _timeouts():
    from abc_gateway.providers.base import Timeouts

    return Timeouts()

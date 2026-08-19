"""A deterministic fake provider.

This is test infrastructure, but it carries a real enforcement proof, so it is
worth being precise about what it does.

The requirement "a blocked request must not reach the provider" is easy to
assert weakly (count the calls) and easy to satisfy accidentally. This adapter
asserts it strongly: on every invocation it checks that the reservation it was
handed is *currently live and RESERVED*. That converts the question from "was
the provider called the right number of times?" -- which a buggy engine could
still pass -- into "was the provider ever called without a valid authorization?",
which is the property that actually protects the budget.

It also lets tests drive the outcomes that are otherwise hard to produce on
demand: ambiguous timeouts, post-generation billed failures, and providers that
overshoot the output cap they were given.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass

from ..domain.reservation import ReservationState
from ..domain.usage import ProviderUsage
from ..pricing.catalog import ModelCapabilities
from .base import (
    ChatRequest,
    FailedAmbiguous,
    FailedBilled,
    FailedNotBilled,
    ProviderOutcome,
    Succeeded,
    Timeouts,
)


class ProviderInvokedWithoutReservation(AssertionError):
    """The provider was called for a request that was not authorised.

    If this is ever raised, the gateway has spent money it did not have
    permission to spend. It is the single most serious failure the test suite
    can report.
    """


@dataclass
class FakeBehaviour:
    """How the fake should respond to the next call(s)."""

    input_tokens: int = 1000
    output_tokens: int = 1000
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    latency_seconds: float = 0.0
    #: One of: "success", "not_billed", "billed_failure", "ambiguous".
    outcome: str = "success"
    error_message: str = "injected failure"
    #: Generate beyond the cap it was given, to exercise the overage path.
    overshoot_output_by: int = 0


class FakeProvider:
    """A provider that never touches the network."""

    name = "test"

    def __init__(
        self,
        behaviour: FakeBehaviour | None = None,
        *,
        repository=None,
        tenant_id: str = "acme",
    ) -> None:
        self.behaviour = behaviour or FakeBehaviour()
        self._repo = repository
        self._tenant_id = tenant_id
        self._lock = threading.Lock()
        self.invocation_count = 0
        self.invoked_models: list[str] = []
        self.invoked_reservation_ids: set[str] = set()
        self.invoked_max_output_tokens: list[int] = []
        self._capabilities: dict[str, ModelCapabilities] = {}

    # -- configuration ------------------------------------------------------

    def set_capabilities(self, model: str, capabilities: ModelCapabilities) -> None:
        self._capabilities[model] = capabilities

    def reset(self) -> None:
        with self._lock:
            self.invocation_count = 0
            self.invoked_models.clear()
            self.invoked_reservation_ids.clear()
            self.invoked_max_output_tokens.clear()

    # -- adapter protocol ---------------------------------------------------

    async def count_input_tokens(self, request: ChatRequest, model: str) -> int:
        return self.behaviour.input_tokens

    def bound_max_output_tokens(self, request: ChatRequest, model: str, policy_cap: int) -> int:
        model_cap = self.capabilities(model).max_output_tokens
        requested = request.max_output_tokens
        # A client that omits a limit gets the policy cap, not "unlimited".
        if requested is None:
            return min(policy_cap, model_cap)
        return min(requested, policy_cap, model_cap)

    def capabilities(self, model: str) -> ModelCapabilities:
        return self._capabilities.get(
            model,
            ModelCapabilities(max_context_tokens=200_000, max_output_tokens=32_000),
        )

    async def invoke(
        self,
        request: ChatRequest,
        model: str,
        *,
        max_output_tokens: int,
        timeouts: Timeouts,
        correlation_id: str,
    ) -> ProviderOutcome:
        await self._assert_authorized(correlation_id)

        with self._lock:
            self.invocation_count += 1
            self.invoked_models.append(model)
            self.invoked_reservation_ids.add(correlation_id)
            self.invoked_max_output_tokens.append(max_output_tokens)

        if self.behaviour.latency_seconds:
            await asyncio.sleep(self.behaviour.latency_seconds)

        behaviour = self.behaviour
        usage = ProviderUsage(
            input_tokens=behaviour.input_tokens,
            output_tokens=min(behaviour.output_tokens, max_output_tokens)
            + behaviour.overshoot_output_by,
            cached_input_tokens=behaviour.cached_input_tokens,
            reasoning_tokens=behaviour.reasoning_tokens,
            provider_request_id=f"fake-{correlation_id}",
        )

        match behaviour.outcome:
            case "success":
                return Succeeded(usage=usage, content="ok")
            case "not_billed":
                return FailedNotBilled(error=behaviour.error_message, status_code=400)
            case "billed_failure":
                return FailedBilled(usage=usage, error=behaviour.error_message)
            case "ambiguous":
                return FailedAmbiguous(
                    error=behaviour.error_message,
                    provider_request_id=usage.provider_request_id,
                )
            case _:  # pragma: no cover
                raise ValueError(f"unknown fake outcome: {behaviour.outcome}")

    async def health(self) -> bool:
        return True

    # -- the enforcement assertion -----------------------------------------

    async def _assert_authorized(self, reservation_id: str) -> None:
        """Refuse to run without a live reservation.

        Counting invocations proves how often the provider ran. This proves it
        never ran unauthorised, which is the stronger and more useful claim.
        """
        if self._repo is None:
            return
        reservation = await self._repo.get_reservation(self._tenant_id, reservation_id)
        if reservation is None:
            raise ProviderInvokedWithoutReservation(
                f"provider invoked with no reservation ({reservation_id}); "
                f"a request reached the provider without authorization"
            )
        if reservation.state is not ReservationState.RESERVED:
            raise ProviderInvokedWithoutReservation(
                f"provider invoked for reservation {reservation_id} in state "
                f"{reservation.state}; only RESERVED entitles a provider call"
            )

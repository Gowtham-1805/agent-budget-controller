"""Provider abstraction.

Provider-specific behaviour stops here. The budget engine never parses a
provider's usage object, never knows a model's tokenizer, and never inspects an
HTTP status code -- it deals in :class:`ProviderOutcome` and
:class:`~..domain.usage.ProviderUsage` only.

The most consequential thing in this module is :class:`ProviderOutcome` and the
rule that governs it: **classification fails closed.** An adapter may report
"not billed" only for conditions that *prove* the request never reached the
provider's meter. Everything else -- every timeout, every reset, every
unrecognised exception -- is ambiguous, and ambiguity keeps the money
encumbered. Getting this backwards means a timed-out request silently returns
its reservation to the budget while the provider bills us anyway, and the same
money gets spent twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..domain.usage import ProviderUsage
from ..pricing.catalog import ModelCapabilities


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """A provider-neutral inference request."""

    messages: tuple[ChatMessage, ...]
    model: str
    #: What the client asked for. The gateway lowers this to the effective cap
    #: and sends *that* to the provider.
    max_output_tokens: int | None = None
    temperature: float | None = None
    tools: tuple[dict[str, Any], ...] = ()
    system: str | None = None
    requires_structured_output: bool = False
    requires_vision: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def approximate_characters(self) -> int:
        total = sum(len(m.content) for m in self.messages)
        return total + (len(self.system) if self.system else 0)


@dataclass(frozen=True, slots=True)
class Timeouts:
    connect_seconds: float = 10.0
    read_seconds: float = 120.0


# -- outcomes ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Succeeded:
    """The provider returned a completion and reported usage."""

    usage: ProviderUsage
    content: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FailedNotBilled:
    """Proven not to have been billed.

    Reserved for conditions where the request demonstrably never reached the
    provider's meter: DNS failure, refused connection, TLS failure, local
    validation error, or a 4xx carrying a parsed provider error envelope and no
    usage object. Nothing else qualifies.
    """

    error: str
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class FailedBilled:
    """Failed *after* generation, and billed anyway.

    Content filters and post-generation policy stops land here: there is a
    usage object, so we owe money despite there being no useful output.
    """

    usage: ProviderUsage
    error: str


@dataclass(frozen=True, slots=True)
class FailedAmbiguous:
    """Outcome unknown. The default for anything unclassified.

    A read timeout is always ambiguous: the provider may have generated the
    entire completion and billed for it while the response was lost in transit.
    """

    error: str
    provider_request_id: str | None = None


ProviderOutcome = Succeeded | FailedNotBilled | FailedBilled | FailedAmbiguous


class ProviderError(Exception):
    """An adapter could not be used at all (misconfiguration, missing key)."""


@runtime_checkable
class ProviderAdapter(Protocol):
    """What the gateway needs from any LLM provider."""

    name: str

    async def count_input_tokens(self, request: ChatRequest, model: str) -> int:
        """Count prompt tokens before generation.

        Should use the provider's own counting endpoint where one exists.
        Local heuristics such as characters-divided-by-four are unreliable for
        structured messages, images, and tool definitions, and a reservation
        built on a bad count does not bound anything.
        """
        ...

    def bound_max_output_tokens(self, request: ChatRequest, model: str, policy_cap: int) -> int:
        """Lower the client's request to an enforceable ceiling.

        The returned value MUST be what is sent to the provider. If the client
        omitted a limit, the policy cap is injected -- otherwise omitting
        ``max_tokens`` would be a way to bypass spend protection entirely.
        """
        ...

    def capabilities(self, model: str) -> ModelCapabilities:
        """What this model supports. Consulted before any substitution."""
        ...

    async def invoke(
        self,
        request: ChatRequest,
        model: str,
        *,
        max_output_tokens: int,
        timeouts: Timeouts,
        correlation_id: str,
    ) -> ProviderOutcome:
        """Call the provider.

        Must never raise for a provider-side failure -- it returns the
        appropriate :class:`ProviderOutcome` instead, so the caller is forced to
        deal with the billed/not-billed/ambiguous distinction explicitly rather
        than treating every exception as "didn't cost anything".
        """
        ...

    async def health(self) -> bool:
        """Whether the adapter is configured and usable.

        Must not make a billable call. A readiness probe that costs money per
        check is its own outage.
        """
        ...

"""Provider-reported usage, raw and normalised.

There is a subtle accounting trap here that is easy to get wrong and expensive
to get wrong quietly: **provider token-detail fields are not additive**.

When a provider reports ``input_tokens: 10000`` and ``cached_input_tokens: 8000``,
the cached figure is almost always a *subset* of the input figure, not an
addition to it. Only 2,000 tokens were billed at the full input rate. Summing
the two and multiplying gives 18,000 billed tokens and roughly double the true
cost. Reasoning tokens have the mirror-image problem: they are usually already
included in the output count, so pricing them separately double-charges again.

So raw usage is never priced directly. It is first normalised into buckets that
are mutually exclusive by construction, and only then multiplied by rates.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tokens import TokenVector


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    """Usage exactly as the provider reported it.

    Field semantics follow the provider's own convention, overlaps included.
    Normalise before pricing.
    """

    input_tokens: int
    output_tokens: int
    #: Portion of `input_tokens` served from cache at a discounted rate.
    #: A subset of input_tokens, not an addition to it.
    cached_input_tokens: int = 0
    #: Tokens written into the cache, billed at a premium by some providers.
    #: Genuinely additional where reported.
    cache_write_tokens: int = 0
    #: Portion of `output_tokens` spent on hidden reasoning. Already counted in
    #: output_tokens; tracked separately for visibility, not for extra billing.
    reasoning_tokens: int = 0
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"ProviderUsage.{name} cannot be negative")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError(
                "cached_input_tokens cannot exceed input_tokens; the provider "
                "reports it as a subset of total input"
            )
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError(
                "reasoning_tokens cannot exceed output_tokens; the provider "
                "reports it as a component of total output"
            )


@dataclass(frozen=True, slots=True)
class NormalizedUsage:
    """Usage split into disjoint billing buckets.

    The invariant that makes this safe to price:

        uncached_input + cached_input == raw input_tokens
        output == raw output_tokens

    Every token is counted exactly once, at exactly one rate.
    """

    #: Input tokens billed at the full input rate.
    uncached_input_tokens: int
    #: Input tokens billed at the (cheaper) cache-read rate.
    cached_input_tokens: int
    #: All generated tokens, reasoning included, billed at the output rate.
    output_tokens: int
    #: Tokens written to cache, billed at the cache-write rate where applicable.
    cache_write_tokens: int
    #: Visibility only. Already inside `output_tokens`; never priced again.
    reasoning_tokens: int

    @classmethod
    def from_provider(cls, usage: ProviderUsage) -> NormalizedUsage:
        return cls(
            uncached_input_tokens=usage.input_tokens - usage.cached_input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens,
        )

    @property
    def total_input_tokens(self) -> int:
        return self.uncached_input_tokens + self.cached_input_tokens

    @property
    def billable_total(self) -> int:
        """Every token that costs money, counted once.

        Cache writes are included because they are genuinely additional work;
        reasoning tokens are not, because they are already inside the output
        count.
        """
        return self.total_input_tokens + self.output_tokens + self.cache_write_tokens

    def to_vector(self) -> TokenVector:
        """Quota-facing view, used to decrement token counters."""
        return TokenVector(
            input=self.total_input_tokens,
            output=self.output_tokens,
            total=self.billable_total,
        )

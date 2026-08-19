"""Versioned price catalog and cost arithmetic.

Pricing is a first-class, versioned subsystem rather than constants scattered
through business logic. Two reasons, both practical:

1. Provider billing is not one number per model. Contemporary pricing
   distinguishes input, cached input, cache writes, output, reasoning, long-
   context tiers and separately-billed tools. A single ``PRICE_PER_TOKEN``
   cannot express any of it.
2. Prices change. Every ledger entry pins the catalog version used to compute
   it, and reconciliation prices at *the version pinned on the reservation* --
   never at whatever is active now. Without that, a price update would silently
   rewrite historical spend.

Rates are stored per *million* tokens because per-token nano prices are
fractional: $0.15 per million tokens is 0.15 nano-USD per token, which is not an
integer. Per million it is exactly 150,000,000 nano-USD, and the cost of N
tokens is ``ceil(N * rate / 1_000_000)`` -- integer throughout, rounded against
the spender.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from ..domain.ledger import CostBreakdown
from ..domain.money import Money
from ..domain.usage import NormalizedUsage

TOKENS_PER_MILLION: Final[int] = 1_000_000
#: Multipliers are held in basis points so that "1.25x" is the integer 12_500
#: rather than a float.
BASIS_POINTS: Final[int] = 10_000


class PricingError(ValueError):
    """Raised when a model's pricing cannot be resolved."""


class UnknownModelError(PricingError):
    """No price entry exists for the requested provider/model pair."""


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """What a model can do.

    Consulted before any budget-pressure substitution. "Cheaper" is not the same
    as "interchangeable": a fallback that cannot accept the request's tools, or
    whose context window is too small for its prompt, will fail or silently
    degrade the result. Routing on price alone turns a cost control into a
    correctness bug.
    """

    max_context_tokens: int
    max_output_tokens: int
    supports_tools: bool = True
    supports_structured_output: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False
    #: Whether the provider honours a hard output ceiling. Models that do not
    #: require a safety margin on top of the reservation.
    supports_hard_output_cap: bool = True


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Rates for one model, in nano-USD per million tokens."""

    provider: str
    model: str
    input_nano_per_mtok: int
    output_nano_per_mtok: int
    cached_input_nano_per_mtok: int = 0
    cache_write_nano_per_mtok: int = 0
    #: Some providers apply a floor per request.
    min_charge_nano: int = 0
    #: Prompts longer than this are billed at the long-context multipliers.
    long_context_threshold: int | None = None
    long_context_input_multiplier_bp: int = BASIS_POINTS
    long_context_output_multiplier_bp: int = BASIS_POINTS
    capabilities: ModelCapabilities = field(
        default_factory=lambda: ModelCapabilities(
            max_context_tokens=128_000, max_output_tokens=16_384
        )
    )

    @property
    def key(self) -> str:
        return f"{self.provider}::{self.model}"

    def _is_long_context(self, input_tokens: int) -> bool:
        return (
            self.long_context_threshold is not None and input_tokens > self.long_context_threshold
        )

    def _rate(self, base: int, multiplier_bp: int, long_context: bool) -> int:
        if not long_context or multiplier_bp == BASIS_POINTS:
            return base
        # Ceiling so that an odd multiplier never rounds the rate down.
        return -((-base * multiplier_bp) // BASIS_POINTS)

    def cost_of(self, tokens: int, rate_nano_per_mtok: int) -> Money:
        """``ceil(tokens * rate / 1_000_000)`` as exact integers."""
        if tokens <= 0 or rate_nano_per_mtok <= 0:
            return Money.zero()
        return Money(-((-tokens * rate_nano_per_mtok) // TOKENS_PER_MILLION))

    # -- estimation ---------------------------------------------------------

    def estimate_worst_case(
        self,
        input_tokens: int,
        max_output_tokens: int,
        *,
        safety_bps: int = 0,
    ) -> CostBreakdown:
        """The most this request could possibly cost.

        Assumes no cache hits (the expensive case) and that generation runs all
        the way to the output ceiling. This is the number that gets reserved,
        because a reservation that assumed anything cheaper would not actually
        bound our exposure.

        Args:
            input_tokens: Preflight-counted prompt size.
            max_output_tokens: The hard ceiling that will be sent to the
                provider. Not a guess -- the gateway enforces it.
            safety_bps: Extra margin in basis points, applied for models that
                cannot enforce a hard output cap.
        """
        long_ctx = self._is_long_context(input_tokens)
        in_rate = self._rate(
            self.input_nano_per_mtok, self.long_context_input_multiplier_bp, long_ctx
        )
        out_rate = self._rate(
            self.output_nano_per_mtok, self.long_context_output_multiplier_bp, long_ctx
        )

        breakdown = CostBreakdown(
            input_cost=self.cost_of(input_tokens, in_rate),
            output_cost=self.cost_of(max_output_tokens, out_rate),
        )
        if safety_bps <= 0:
            return _apply_minimum(breakdown, self.min_charge_nano)

        padded = breakdown.total.mul_div_ceil(BASIS_POINTS + safety_bps, BASIS_POINTS)
        # Attribute the whole margin to output, which is the unbounded side.
        margin = padded - breakdown.total
        return _apply_minimum(
            CostBreakdown(
                input_cost=breakdown.input_cost,
                output_cost=breakdown.output_cost + margin,
            ),
            self.min_charge_nano,
        )

    # -- actual pricing -----------------------------------------------------

    def price_actual(self, usage: NormalizedUsage) -> CostBreakdown:
        """Price normalised usage.

        Takes :class:`NormalizedUsage` rather than raw provider usage on
        purpose: the type system is what prevents someone pricing overlapping
        buckets and double-charging cached input.
        """
        long_ctx = self._is_long_context(usage.total_input_tokens)
        in_rate = self._rate(
            self.input_nano_per_mtok, self.long_context_input_multiplier_bp, long_ctx
        )
        out_rate = self._rate(
            self.output_nano_per_mtok, self.long_context_output_multiplier_bp, long_ctx
        )

        breakdown = CostBreakdown(
            input_cost=self.cost_of(usage.uncached_input_tokens, in_rate),
            cached_input_cost=self.cost_of(
                usage.cached_input_tokens, self.cached_input_nano_per_mtok
            ),
            output_cost=self.cost_of(usage.output_tokens, out_rate),
            tool_cost=self.cost_of(usage.cache_write_tokens, self.cache_write_nano_per_mtok),
        )
        return _apply_minimum(breakdown, self.min_charge_nano)


def _apply_minimum(breakdown: CostBreakdown, min_charge_nano: int) -> CostBreakdown:
    if min_charge_nano <= 0 or breakdown.total.nano >= min_charge_nano:
        return breakdown
    shortfall = Money(min_charge_nano - breakdown.total.nano)
    return CostBreakdown(
        input_cost=breakdown.input_cost,
        cached_input_cost=breakdown.cached_input_cost,
        output_cost=breakdown.output_cost,
        reasoning_cost=breakdown.reasoning_cost,
        tool_cost=breakdown.tool_cost + shortfall,
    )


@dataclass(frozen=True, slots=True)
class PriceCatalog:
    """An immutable, versioned set of model prices."""

    version: str
    published_at: datetime
    entries: dict[str, ModelPrice]
    source: str = "bundled"

    def get(self, provider: str, model: str) -> ModelPrice:
        key = f"{provider}::{model}"
        price = self.entries.get(key)
        if price is None:
            raise UnknownModelError(f"no price entry for {key} in catalog version {self.version}")
        return price

    def has(self, provider: str, model: str) -> bool:
        return f"{provider}::{model}" in self.entries

    def models_for(self, provider: str) -> tuple[ModelPrice, ...]:
        return tuple(p for p in self.entries.values() if p.provider == provider)

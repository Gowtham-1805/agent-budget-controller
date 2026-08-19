"""Pricing must be exact, and must never double-count.

The double-counting tests are the important ones here. Providers report token
details that overlap -- cached input is a subset of total input, reasoning
tokens are a component of output -- and a cost engine that sums them naively
roughly doubles the bill it computes. That error is invisible in a unit of one
request and enormous at fleet scale.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abc_gateway.domain.money import Money
from abc_gateway.domain.usage import NormalizedUsage, ProviderUsage
from abc_gateway.pricing import CatalogFormatError, load_catalog, parse_catalog
from abc_gateway.pricing.catalog import ModelPrice, UnknownModelError

CATALOG_PATH = Path(__file__).resolve().parents[2] / "pricing" / "catalog.json"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CATALOG_PATH)


class TestCatalogGolden:
    """The bundled catalog must parse to exact integer rates."""

    def test_loads(self, catalog) -> None:
        assert catalog.version
        assert len(catalog.entries) >= 6

    def test_one_million_tokens_costs_the_published_price(self, catalog) -> None:
        """The sanity check that catches an off-by-1e6 in the rate conversion."""
        premium = catalog.get("test", "premium")
        assert premium.cost_of(1_000_000, premium.input_nano_per_mtok) == Money.from_usd_str(
            "10.00"
        )
        assert premium.cost_of(1_000_000, premium.output_nano_per_mtok) == Money.from_usd_str(
            "30.00"
        )

    def test_every_rate_is_a_whole_number_of_nano_usd(self, catalog) -> None:
        for price in catalog.entries.values():
            for rate in (
                price.input_nano_per_mtok,
                price.output_nano_per_mtok,
                price.cached_input_nano_per_mtok,
            ):
                assert isinstance(rate, int)
                assert rate >= 0

    def test_unknown_model_raises(self, catalog) -> None:
        with pytest.raises(UnknownModelError, match="no price entry"):
            catalog.get("openai", "does-not-exist")


class TestAcceptanceFixture:
    """The synthetic rates the mandatory acceptance tests are built on."""

    def test_the_hard_cap_race_amount_is_exactly_four_cents(self, catalog) -> None:
        """1000 in + 1000 out on test::premium == $0.04, exactly.

        The hard-cap race test reserves $0.04 ten times against $0.05. If this
        arithmetic were off by a nano the whole scenario would lose its meaning.
        """
        premium = catalog.get("test", "premium")
        estimate = premium.estimate_worst_case(input_tokens=1000, max_output_tokens=1000)
        assert estimate.total == Money.from_usd_str("0.04")

    def test_the_fallback_really_is_ten_times_cheaper(self, catalog) -> None:
        premium = catalog.get("test", "premium")
        cheap = catalog.get("test", "cheap")
        p = premium.estimate_worst_case(1000, 1000).total
        c = cheap.estimate_worst_case(1000, 1000).total
        assert c < p
        assert c == Money.from_usd_str("0.004")


class TestWorstCaseEstimation:
    def test_reserves_the_full_output_ceiling(self, catalog) -> None:
        """Estimation assumes generation runs all the way to the cap.

        Anything less would not actually bound our exposure, which is the only
        reason the reservation exists.
        """
        premium = catalog.get("test", "premium")
        small = premium.estimate_worst_case(1000, 100)
        large = premium.estimate_worst_case(1000, 10_000)
        assert large.total > small.total

    def test_assumes_no_cache_hits(self, catalog) -> None:
        # Cache hits are cheaper, so assuming them would under-reserve.
        premium = catalog.get("test", "premium")
        estimate = premium.estimate_worst_case(1000, 1000)
        assert estimate.cached_input_cost == Money.zero()

    def test_safety_margin_only_increases_the_estimate(self, catalog) -> None:
        premium = catalog.get("test", "premium")
        plain = premium.estimate_worst_case(1000, 1000)
        padded = premium.estimate_worst_case(1000, 1000, safety_bps=200)
        assert padded.total > plain.total
        # 2% of $0.04 is $0.0008.
        assert padded.total == Money.from_usd_str("0.0408")

    def test_rounds_up_never_down(self) -> None:
        # 1 token at $0.15/M is 0.15 nano-USD. Rounding down would reserve zero.
        price = ModelPrice(
            provider="x", model="y", input_nano_per_mtok=150_000, output_nano_per_mtok=0
        )
        assert price.cost_of(1, price.input_nano_per_mtok) == Money(1)


class TestNoDoubleCounting:
    def test_cached_input_is_a_subset_not_an_addition(self) -> None:
        """10k input of which 8k cached bills 2k full + 8k cached, not 18k."""
        usage = ProviderUsage(input_tokens=10_000, output_tokens=0, cached_input_tokens=8_000)
        norm = NormalizedUsage.from_provider(usage)
        assert norm.uncached_input_tokens == 2_000
        assert norm.cached_input_tokens == 8_000
        assert norm.total_input_tokens == 10_000  # not 18_000

    def test_reasoning_tokens_are_not_billed_twice(self) -> None:
        """Reasoning is already inside the output count."""
        usage = ProviderUsage(input_tokens=0, output_tokens=2_000, reasoning_tokens=1_500)
        norm = NormalizedUsage.from_provider(usage)
        assert norm.output_tokens == 2_000  # not 3_500
        assert norm.reasoning_tokens == 1_500  # visibility only
        assert norm.billable_total == 2_000

    def test_pricing_uses_the_discounted_rate_for_cached_input(self, catalog) -> None:
        premium = catalog.get("test", "premium")  # $10/M input, $5/M cached
        usage = ProviderUsage(
            input_tokens=1_000_000, output_tokens=0, cached_input_tokens=1_000_000
        )
        cost = premium.price_actual(NormalizedUsage.from_provider(usage))
        assert cost.input_cost == Money.zero()
        assert cost.cached_input_cost == Money.from_usd_str("5.00")
        assert cost.total == Money.from_usd_str("5.00")

    def test_impossible_overlaps_are_rejected_at_the_boundary(self) -> None:
        # Catching this at construction beats discovering a negative uncached
        # count downstream in the cost engine.
        with pytest.raises(ValueError, match="cached_input_tokens cannot exceed"):
            ProviderUsage(input_tokens=100, output_tokens=0, cached_input_tokens=200)
        with pytest.raises(ValueError, match="reasoning_tokens cannot exceed"):
            ProviderUsage(input_tokens=0, output_tokens=100, reasoning_tokens=200)


class TestLongContext:
    def test_multiplier_applies_only_past_the_threshold(self) -> None:
        price = ModelPrice(
            provider="x",
            model="y",
            input_nano_per_mtok=1_000_000_000,
            output_nano_per_mtok=1_000_000_000,
            long_context_threshold=10_000,
            long_context_input_multiplier_bp=20_000,  # 2x
        )
        short = price.estimate_worst_case(10_000, 0)
        long = price.estimate_worst_case(10_001, 0)
        assert long.total.nano > short.total.nano * 2 - 1_000_000


class TestLoaderRejectsImprecision:
    def _catalog(self, **overrides):
        model = {
            "provider": "p",
            "model": "m",
            "input_per_million": "1.00",
            "output_per_million": "2.00",
            "capabilities": {"max_context_tokens": 1000, "max_output_tokens": 100},
        }
        model.update(overrides)
        return {"version": "v1", "models": [model]}

    def test_rejects_float_literals(self) -> None:
        """A JSON float has already lost precision before the loader sees it."""
        with pytest.raises(CatalogFormatError, match="not a float"):
            parse_catalog(self._catalog(input_per_million=2.5))

    def test_rejects_sub_nano_rates(self) -> None:
        with pytest.raises(CatalogFormatError, match="finer than one nano-USD"):
            parse_catalog(self._catalog(input_per_million="0.0000000001"))

    def test_rejects_negative_rates(self) -> None:
        with pytest.raises(CatalogFormatError, match="cannot be negative"):
            parse_catalog(self._catalog(input_per_million="-1.00"))

    def test_rejects_duplicate_models(self) -> None:
        raw = self._catalog()
        raw["models"].append(dict(raw["models"][0]))
        with pytest.raises(CatalogFormatError, match="duplicate price entry"):
            parse_catalog(raw)

    def test_rejects_inactive_entries(self) -> None:
        with pytest.raises(CatalogFormatError, match="only ACTIVE"):
            parse_catalog(self._catalog(status="DEPRECATED"))

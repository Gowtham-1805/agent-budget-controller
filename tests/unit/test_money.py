"""Money must be exact.

These tests exist because every one of them, if it failed in production, would
either authorise spend that should have been blocked or block spend that should
have been authorised.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from abc_gateway.domain.money import NANO_PER_USD, Money, MoneyError


class TestConstruction:
    def test_from_usd_str_is_exact(self) -> None:
        assert Money.from_usd_str("50.00").nano == 50 * NANO_PER_USD
        assert Money.from_usd_str("0.05").nano == 50_000_000
        assert Money.from_usd_str("0.04").nano == 40_000_000

    def test_parses_full_nano_precision(self) -> None:
        assert Money.from_usd_str("0.000000001").nano == 1

    def test_rejects_sub_nano_precision(self) -> None:
        # Silently rounding here would make the catalog disagree with the
        # provider's invoice by an amount that grows with volume.
        with pytest.raises(MoneyError, match="finer than one nano-USD"):
            Money.from_usd_str("0.0000000001")

    def test_rejects_float_input(self) -> None:
        # 0.1 is not representable in binary floating point. Accepting a float
        # would reintroduce exactly the imprecision this type prevents.
        with pytest.raises(MoneyError):
            Money(0.1)  # type: ignore[arg-type]

    def test_rejects_bool_masquerading_as_int(self) -> None:
        with pytest.raises(MoneyError):
            Money(True)  # type: ignore[arg-type]

    def test_accepts_decimal(self) -> None:
        assert Money.from_usd_str(Decimal("1.25")) == Money.from_usd_str("1.25")


class TestNoFloatContamination:
    """The type must make float arithmetic impossible, not merely discouraged."""

    def test_has_no_float_conversion(self) -> None:
        assert not hasattr(Money(1), "__float__")

    def test_has_no_division(self) -> None:
        assert not hasattr(Money(1), "__truediv__")

    def test_cannot_add_a_bare_int(self) -> None:
        with pytest.raises(TypeError):
            Money(1) + 1  # type: ignore[operator]


class TestArithmetic:
    def test_addition_and_subtraction(self) -> None:
        assert Money(300) + Money(200) == Money(500)
        assert Money(300) - Money(200) == Money(100)

    def test_subtraction_may_go_negative(self) -> None:
        # Deliberate: an overspent budget must be able to record a negative
        # remaining balance rather than clamp and lose the evidence.
        assert (Money(100) - Money(300)).nano == -200

    def test_mul_div_ceil_rounds_against_the_spender(self) -> None:
        # 1 nano * 1 / 3 is a third of a nano; a reservation must round up.
        assert Money(1).mul_div_ceil(1, 3) == Money(1)
        assert Money(10).mul_div_ceil(1, 3) == Money(4)

    def test_mul_div_ceil_is_exact_when_divisible(self) -> None:
        assert Money(9).mul_div_ceil(1, 3) == Money(3)

    def test_pct_floor(self) -> None:
        assert Money.from_usd_str("50.00").pct_floor(80) == Money.from_usd_str("40.00")
        assert Money(7).pct_floor(50) == Money(3)

    def test_rejects_zero_denominator(self) -> None:
        with pytest.raises(MoneyError):
            Money(1).mul_div_ceil(1, 0)


class TestComparison:
    def test_ordering(self) -> None:
        assert Money(1) < Money(2)
        assert Money(2) >= Money(2)
        assert min(Money(5), Money(3)) == Money(3)

    def test_hashable(self) -> None:
        assert len({Money(1), Money(1), Money(2)}) == 2

    def test_the_authorization_comparison_is_exact(self) -> None:
        """The comparison the whole system turns on.

        Ten reservations of $0.04 against $0.05: the first fits, the second
        does not. With floats this boundary is where epsilon errors live.
        """
        remaining = Money.from_usd_str("0.05")
        reservation = Money.from_usd_str("0.04")
        assert remaining >= reservation
        assert not (remaining - reservation) >= reservation


class TestDisplay:
    def test_to_usd_decimal_is_exact(self) -> None:
        assert Money.from_usd_str("1.23").to_usd_decimal() == Decimal("1.23")

    def test_str_renders_two_places(self) -> None:
        assert str(Money.from_usd_str("49.9")) == "$49.90"


class TestUnlimited:
    def test_unlimited_is_a_real_number_not_a_sentinel_none(self) -> None:
        # An unlimited scope takes the same code path as any other; there is no
        # None to branch on inside the authorization transaction.
        assert Money.unlimited().is_unlimited()
        assert Money.unlimited() > Money.from_usd_str("1000000000.00")

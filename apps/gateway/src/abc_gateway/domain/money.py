"""Integer fixed-point money.

Every monetary value in the enforcement path is an integer count of nano-USD.
Binary floating point is never used, because the comparison that decides whether
a request is allowed to reach a provider --

    remaining_nano >= reservation_nano

-- must be exact. A float that is short by one representational epsilon either
authorises spend that should have been blocked or blocks spend that should have
been authorised, and both are unacceptable at an enforcement boundary.

`Money` therefore deliberately does *not* implement ``__float__``, ``__truediv__``
or multiplication by a float. If you find yourself wanting one of those, you want
:meth:`Money.mul_div_ceil` (for pricing) or :meth:`Money.pct_floor` (for
thresholds) instead.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Final

NANO_PER_USD: Final[int] = 1_000_000_000

#: Sentinel used for scopes configured without a monetary cap. A real integer
#: rather than ``None`` so that every scope takes exactly one code path through
#: the authorization transaction -- an unlimited scope is just a scope with an
#: absurdly large limit, not a special case to be branched around.
UNLIMITED_NANO: Final[int] = 10**24


class MoneyError(ValueError):
    """Raised when a monetary value cannot be represented exactly."""


class Money:
    """An exact quantity of USD, stored as an integer number of nano-USD."""

    __slots__ = ("_nano",)

    def __init__(self, nano: int) -> None:
        if not isinstance(nano, int) or isinstance(nano, bool):
            raise MoneyError(f"Money requires an int number of nano-USD, got {type(nano).__name__}")
        self._nano = nano

    # -- construction -------------------------------------------------------

    @property
    def nano(self) -> int:
        """The underlying integer nano-USD value."""
        return self._nano

    @classmethod
    def zero(cls) -> Money:
        return cls(0)

    @classmethod
    def from_nano(cls, nano: int) -> Money:
        return cls(nano)

    @classmethod
    def from_usd_str(cls, value: str) -> Money:
        """Parse a decimal USD string such as ``"50.00"`` exactly.

        Accepts a string (or Decimal) rather than a float on purpose: ``0.1`` is
        not representable in binary floating point, so accepting a float here
        would reintroduce exactly the imprecision this class exists to prevent.

        Raises:
            MoneyError: if the value carries precision finer than one nano-USD.
        """
        try:
            dec = Decimal(value)
        except (InvalidOperation, TypeError, ArithmeticError) as exc:
            raise MoneyError(f"not a valid decimal USD amount: {value!r}") from exc

        if not dec.is_finite():
            raise MoneyError(f"USD amount must be finite, got {value!r}")

        scaled = dec * NANO_PER_USD
        if scaled != scaled.to_integral_value():
            raise MoneyError(
                f"USD amount {value!r} has precision finer than one nano-USD "
                f"and cannot be represented exactly"
            )
        return cls(int(scaled))

    @classmethod
    def unlimited(cls) -> Money:
        return cls(UNLIMITED_NANO)

    def is_unlimited(self) -> bool:
        return self._nano >= UNLIMITED_NANO

    # -- arithmetic ---------------------------------------------------------
    # Only operations that keep the value an exact integer are provided.

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self._nano + other._nano)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self._nano - other._nano)

    def __neg__(self) -> Money:
        return Money(-self._nano)

    def mul_div_ceil(self, numerator: int, denominator: int) -> Money:
        """Return ``ceil(self * numerator / denominator)`` in exact integers.

        This is the primitive behind cost estimation. Ceiling rather than
        rounding is deliberate: when the gateway is deciding how much to hold
        against a budget it must never under-reserve, so every rounding decision
        resolves against the spender.
        """
        if denominator == 0:
            raise MoneyError("denominator must be non-zero")
        product = self._nano * numerator
        # Floor division in Python rounds toward negative infinity, which is the
        # correct direction for negative values too: ceil(a/b) == -((-a)//b).
        return Money(-((-product) // denominator))

    def pct_floor(self, percent: int) -> Money:
        """Return ``floor(self * percent / 100)``.

        Used to compute threshold floors (e.g. the 80% warning boundary) with no
        floating-point involvement.
        """
        return Money((self._nano * percent) // 100)

    # -- comparison ---------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._nano == other._nano

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._nano < other._nano

    def __le__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._nano <= other._nano

    def __gt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._nano > other._nano

    def __ge__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self._nano >= other._nano

    def __hash__(self) -> int:
        return hash((Money, self._nano))

    # -- display ------------------------------------------------------------
    # Decimal only, and only on the way out to a human or an API response.

    def to_usd_decimal(self) -> Decimal:
        """Exact Decimal USD value. For display and serialisation only."""
        return Decimal(self._nano) / Decimal(NANO_PER_USD)

    def to_usd_str(self, places: int = 6) -> str:
        """Render as a fixed-point USD string, truncating toward zero."""
        quantum = Decimal(1).scaleb(-places)
        return str(self.to_usd_decimal().quantize(quantum))

    def __repr__(self) -> str:
        return f"Money(nano={self._nano}, usd={self.to_usd_str()})"

    def __str__(self) -> str:
        return f"${self.to_usd_str(2)}"


ZERO: Final[Money] = Money(0)
UNLIMITED: Final[Money] = Money(UNLIMITED_NANO)

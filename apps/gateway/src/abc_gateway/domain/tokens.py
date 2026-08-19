"""Token quantities and quota vectors.

Token budgets are a first-class enforcement dimension alongside money, not a
derived view of it. A team may hold plenty of dollars and still be out of output
tokens, and that request must be rejected. Keeping the two independent is what
makes that expressible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Sentinel for "no token cap configured". Mirrors ``UNLIMITED_NANO``: an
#: unconstrained scope is a scope with an unreachable limit, so there is exactly
#: one code path through authorization.
UNLIMITED_TOKENS: Final[int] = 10**18


@dataclass(frozen=True, slots=True)
class TokenVector:
    """A triple of token counts: input, output, and total.

    ``total`` is tracked as its own quota rather than derived as
    ``input + output`` because providers bill several token classes (cached
    input, cache writes, reasoning) that do not sum naively, and because an
    operator may want to cap total throughput independently of the split.
    """

    input: int = 0
    output: int = 0
    total: int = 0

    def __post_init__(self) -> None:
        for name in ("input", "output", "total"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"TokenVector.{name} must be an int, got {type(value).__name__}")
            if value < 0:
                raise ValueError(f"TokenVector.{name} must be non-negative, got {value}")

    @classmethod
    def zero(cls) -> TokenVector:
        return cls(0, 0, 0)

    @classmethod
    def unlimited(cls) -> TokenVector:
        return cls(UNLIMITED_TOKENS, UNLIMITED_TOKENS, UNLIMITED_TOKENS)

    @classmethod
    def of(cls, *, input: int, output: int) -> TokenVector:  # noqa: A002
        """Build a vector whose total is the sum of its parts."""
        return cls(input=input, output=output, total=input + output)

    def __add__(self, other: TokenVector) -> TokenVector:
        if not isinstance(other, TokenVector):
            return NotImplemented
        return TokenVector(
            input=self.input + other.input,
            output=self.output + other.output,
            total=self.total + other.total,
        )

    def fits_within(self, limits: TokenVector) -> bool:
        """True when every component is within the corresponding limit."""
        return (
            self.input <= limits.input
            and self.output <= limits.output
            and self.total <= limits.total
        )

    def exceeded_dimensions(self, limits: TokenVector) -> tuple[str, ...]:
        """Names of the quota dimensions this vector overruns.

        Used to turn a failed condition into a precise, machine-readable error
        that names *which* quota bound the request, rather than a vague
        "token quota exceeded".
        """
        over: list[str] = []
        if self.input > limits.input:
            over.append("input")
        if self.output > limits.output:
            over.append("output")
        if self.total > limits.total:
            over.append("total")
        return tuple(over)

"""Budget counters.

The schema here is dictated by one hard constraint: DynamoDB's
``ConditionExpression`` has no arithmetic. You cannot write

    committed + reserved + :cost <= limit

as a condition, so the invariant cannot be checked in that form at the moment it
matters. What you *can* write is

    remaining_nano >= :cost

and ``UpdateExpression``'s ``SET`` does support subtraction. So ``remaining`` is
maintained as a materialised, decrementing counter, and that single choice is
what makes an atomic hard cap expressible at all.

``committed`` and ``reserved`` are carried alongside for reporting and for the
identity that the property tests assert after every operation:

    remaining + committed + reserved == limit + overage
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .money import Money
from .scopes import ScopeRef
from .tokens import TokenVector
from .window import BudgetWindow


@dataclass(frozen=True, slots=True)
class BudgetState:
    """Current counters for one scope in one window."""

    scope: ScopeRef
    window: BudgetWindow
    limit: Money
    token_limits: TokenVector

    #: limit - committed - reserved. The only value the authorization condition
    #: reads. Allowed to go negative; see `overage`.
    remaining: Money
    remaining_tokens: TokenVector

    committed: Money = field(default_factory=Money.zero)
    reserved: Money = field(default_factory=Money.zero)
    #: Subset of `reserved` whose provider outcome is unknown. A reporting
    #: breakdown, not an additional deduction -- adding it to reserved would
    #: double-count the same held money.
    pending: Money = field(default_factory=Money.zero)
    #: Actual spend that exceeded its reservation. Only ever grows, and its
    #: existence means something is wrong: either a provider ignored the hard
    #: output cap we sent, or our token counting drifted. Alarmed, not
    #: dashboarded.
    overage: Money = field(default_factory=Money.zero)

    committed_tokens: TokenVector = field(default_factory=TokenVector.zero)
    reserved_tokens: TokenVector = field(default_factory=TokenVector.zero)

    open_reservations: int = 0
    warning_80_sent: bool = False
    warning_100_sent: bool = False
    policy_version: str = "1"
    version: int = 0

    # -- derived views ------------------------------------------------------

    @property
    def available(self) -> Money:
        """Spendable capacity, floored at zero for display purposes.

        The stored `remaining` is deliberately *not* floored -- see
        :meth:`is_overspent` -- but an operator reading a dashboard wants to see
        $0.00 rather than a negative balance.
        """
        return self.remaining if self.remaining > Money.zero() else Money.zero()

    @property
    def utilization_percent(self) -> int:
        """Committed spend as a percentage of the limit.

        This is what the 80% warning fires on: settled spend only. In-flight
        reservations are excluded because a warning that fires on money which
        may still be released would cry wolf.
        """
        if self.limit.nano <= 0:
            return 0
        return (self.committed.nano * 100) // self.limit.nano

    @property
    def effective_utilization_percent(self) -> int:
        """Committed plus in-flight, as a percentage of the limit.

        The operator-facing number. It is what makes concurrency visible: a
        scope can be at 60% settled and 95% effective, meaning almost all of the
        remaining budget is already promised to requests currently running.
        """
        if self.limit.nano <= 0:
            return 0
        return ((self.committed + self.reserved).nano * 100) // self.limit.nano

    @property
    def is_overspent(self) -> bool:
        return self.remaining < Money.zero() or self.overage > Money.zero()

    def invariant_holds(self) -> bool:
        """The accounting identity that must hold after every operation:

            remaining + committed + reserved == limit

        Every nano-USD of the limit is in exactly one of three states -- unspent,
        settled, or held against a request in flight. Nothing appears, nothing
        vanishes.

        Note what this does *not* claim. It does not say actual spend never
        exceeds the limit: a provider that generates past the hard cap we sent
        can force that, and no gateway can un-bill it. When that happens
        `committed` simply grows beyond `limit` and `remaining` goes negative,
        and the identity still balances -- which is precisely why `remaining` is
        never clamped. `overage` records how much of the excess arrived that way,
        as a diagnostic for alerting, not as a term in the accounting.
        """
        return (self.remaining + self.committed + self.reserved) == self.limit

    def can_admit(self, cost: Money, tokens: TokenVector) -> bool:
        """Whether a request of this size would currently be authorised.

        Advisory only. The authoritative decision is made by the conditional
        write inside the transaction; anything computed from a value that was
        read beforehand is stale by the time it is acted on.
        """
        return cost <= self.remaining and tokens.fits_within(self.remaining_tokens)

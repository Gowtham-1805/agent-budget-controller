"""The immutable usage ledger.

Every settled request writes exactly one entry, and entries are never mutated.
Corrections supersede rather than overwrite, carrying ``corrects_entry_id`` so
the original record and the correction both survive. That is what makes the
ledger usable as evidence: an auditor asking "what did we think we spent, and
when did we learn otherwise?" can answer it.

Immutability is enforced by IAM as well as by convention -- the gateway's role
holds ``PutItem`` on the ledger table but not ``UpdateItem`` or ``DeleteItem``
-- which is the main reason the ledger lives in its own table.

Each entry pins the price-catalog version used to compute it. Without that,
recomputing historical spend after a provider price change would silently
rewrite the past.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .money import Money
from .tokens import TokenVector


class LedgerKind(StrEnum):
    """What kind of financial event an entry records."""

    #: A completed request with provider-reported usage.
    USAGE = "USAGE"
    #: A reservation returned unspent.
    RELEASE = "RELEASE"
    #: An ambiguous outcome, provisionally recorded at the reserved amount.
    #: Counted as spend so that the runaway detector and dashboards err toward
    #: over-reporting our own exposure rather than under-reporting it.
    PENDING_ASSUMED = "PENDING_ASSUMED"
    #: Supersedes an earlier entry once better evidence arrived.
    CORRECTION = "CORRECTION"
    #: Actual cost exceeded the reservation.
    OVERAGE = "OVERAGE"


class BudgetDecision(StrEnum):
    """How the gateway disposed of a request."""

    ALLOWED = "ALLOWED"
    SUBSTITUTED_PREFERRED_MODEL_BUDGET = "SUBSTITUTED_PREFERRED_MODEL_BUDGET"
    BLOCKED_BUDGET_EXHAUSTED = "BLOCKED_BUDGET_EXHAUSTED"
    BLOCKED_TOKEN_QUOTA = "BLOCKED_TOKEN_QUOTA"
    BLOCKED_AGENT_PAUSED = "BLOCKED_AGENT_PAUSED"
    BLOCKED_SESSION_CLOSED = "BLOCKED_SESSION_CLOSED"


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Cost split by billing bucket.

    The buckets are mutually exclusive by construction. Providers report token
    details that overlap -- cached input is typically a *subset* of total input,
    not an addition to it -- so usage is normalised into disjoint buckets before
    any multiplication. Skipping that step double-counts both tokens and money.
    """

    input_cost: Money = field(default_factory=Money.zero)
    cached_input_cost: Money = field(default_factory=Money.zero)
    output_cost: Money = field(default_factory=Money.zero)
    reasoning_cost: Money = field(default_factory=Money.zero)
    tool_cost: Money = field(default_factory=Money.zero)

    @property
    def total(self) -> Money:
        return (
            self.input_cost
            + self.cached_input_cost
            + self.output_cost
            + self.reasoning_cost
            + self.tool_cost
        )

    @classmethod
    def zero(cls) -> CostBreakdown:
        return cls()


@dataclass(frozen=True, slots=True)
class UsageLedgerEntry:
    """One immutable financial record."""

    entry_id: str
    kind: LedgerKind
    reservation_id: str
    tenant_id: str
    team_id: str
    agent_id: str
    session_id: str | None

    provider: str
    requested_model: str
    effective_model: str
    decision: BudgetDecision

    # -- preflight estimate, recorded before the provider was contacted -------
    preflight_input_tokens: int
    reserved_output_tokens: int
    estimated_cost: CostBreakdown
    estimated_max_cost: Money
    reserved_cost: Money

    # -- provider-reported actuals ------------------------------------------
    actual_tokens: TokenVector
    actual_cached_input_tokens: int
    actual_reasoning_tokens: int
    actual_cost: CostBreakdown
    actual_total_cost: Money

    price_catalog_version: str
    created_at: datetime
    completed_at: datetime | None = None
    provider_request_id: str | None = None
    #: Set on CORRECTION entries to point at the entry being superseded. The
    #: superseded entry itself is never touched.
    corrects_entry_id: str | None = None
    scope_keys: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def make_entry_id(reservation_id: str, sequence: int) -> str:
        """Stable id used to deduplicate at-least-once stream delivery.

        DynamoDB Streams can deliver the same record more than once, so every
        consumer -- the runaway detector especially -- keys off this rather than
        counting deliveries.
        """
        return f"{reservation_id}#{sequence}"

    @property
    def variance(self) -> Money:
        """Reserved minus actual. Negative means the reservation was overshot."""
        return self.reserved_cost - self.actual_total_cost

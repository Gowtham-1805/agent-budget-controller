"""Budget scopes and the per-request deltas applied to them.

The budget hierarchy is TEAM -> AGENT -> SESSION -> MODEL ALLOCATION. A request
is legal only if it fits inside *every* mandatory scope at once, which is why
authorization is a single atomic transaction over the whole vector rather than a
sequence of individual checks.

Model allocation is deliberately modelled as just another scope rather than as a
special case. That is what makes "a cheaper fallback model can never be used to
escape an exhausted agent budget" true by construction: the agent scope is in
every candidate model's transaction, so there is no path that reserves against
the fallback without also reserving against its parents.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .money import Money
from .tokens import TokenVector
from .window import BudgetWindow


class ScopeType(StrEnum):
    """A level of the budget hierarchy.

    Ordering matters: :data:`SCOPE_PRECEDENCE` uses it to decide which of several
    simultaneously-failing scopes to report as *the* blocking one.
    """

    TEAM = "TEAM"
    AGENT = "AGENT"
    SESSION = "SESSION"
    MODEL_ALLOCATION = "ALLOC"


#: Outermost-first. When several scopes reject a request at once, the outermost
#: is the one the caller must actually resolve, and it also decides whether
#: falling back to a cheaper model is legal at all: an exhausted ALLOC can be
#: routed around, an exhausted TEAM cannot.
SCOPE_PRECEDENCE: tuple[ScopeType, ...] = (
    ScopeType.TEAM,
    ScopeType.AGENT,
    ScopeType.SESSION,
    ScopeType.MODEL_ALLOCATION,
)


@dataclass(frozen=True, slots=True, order=True)
class ScopeRef:
    """Identifies one budget scope."""

    type: ScopeType
    id: str

    def key(self) -> str:
        """Canonical scope key used in storage keys and audit records."""
        return f"{self.type.value}#{self.id}"

    @classmethod
    def team(cls, team_id: str) -> ScopeRef:
        return cls(ScopeType.TEAM, team_id)

    @classmethod
    def agent(cls, agent_id: str) -> ScopeRef:
        return cls(ScopeType.AGENT, agent_id)

    @classmethod
    def session(cls, session_id: str) -> ScopeRef:
        return cls(ScopeType.SESSION, session_id)

    @classmethod
    def model_allocation(cls, agent_id: str, provider: str, model: str) -> ScopeRef:
        """A per-model sub-budget belonging to one agent."""
        return cls(ScopeType.MODEL_ALLOCATION, f"{agent_id}#{provider}::{model}")

    def __str__(self) -> str:
        return self.key()


@dataclass(frozen=True, slots=True)
class ScopeLimits:
    """The configured caps for a scope.

    Used only to seed a budget-window item the first time it is touched. Once a
    window item exists its limits are immutable for the life of the window; a
    policy change mid-window requires an explicit, audited migration rather than
    silently taking effect on the next request.
    """

    limit: Money
    tokens: TokenVector

    @classmethod
    def unlimited(cls) -> ScopeLimits:
        return cls(limit=Money.unlimited(), tokens=TokenVector.unlimited())


@dataclass(frozen=True, slots=True)
class ScopeDelta:
    """The amount one request proposes to consume from one scope."""

    scope: ScopeRef
    window: BudgetWindow
    cost: Money
    tokens: TokenVector
    limits: ScopeLimits
    policy_version: str
    mandatory: bool = True

    def storage_key(self) -> tuple[str, str]:
        """The ``(partition_suffix, sort_key)`` pair identifying this scope's state.

        The partition component is completed with the tenant prefix by the
        storage layer; the domain does not know about table key formats beyond
        the fact that a scope-plus-window pair is what identifies a counter.
        """
        return (f"BUDGET#{self.scope.key()}", self.window.sort_key())

    def merge(self, other: ScopeDelta) -> ScopeDelta:
        """Combine two deltas addressing the same scope and window.

        Walking the policy graph can legitimately reach one scope twice -- a
        model allocation's parent chain re-reaches the agent's monthly window,
        for instance. DynamoDB forbids two actions against the same item inside
        one transaction, so colliding deltas are summed here rather than emitted
        twice and rejected at the API.
        """
        if self.scope != other.scope or self.window.sort_key() != other.window.sort_key():
            raise ValueError("cannot merge deltas addressing different scope/window pairs")
        return ScopeDelta(
            scope=self.scope,
            window=self.window,
            cost=self.cost + other.cost,
            tokens=self.tokens + other.tokens,
            limits=self.limits,
            policy_version=self.policy_version,
            mandatory=self.mandatory or other.mandatory,
        )


def outermost(scopes: tuple[ScopeRef, ...]) -> ScopeRef | None:
    """The outermost scope in :data:`SCOPE_PRECEDENCE` order, or None if empty."""
    if not scopes:
        return None
    return min(scopes, key=lambda s: SCOPE_PRECEDENCE.index(s.type))

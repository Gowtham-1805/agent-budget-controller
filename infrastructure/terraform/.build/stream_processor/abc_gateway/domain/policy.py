"""Budget and routing policy.

Policy is configuration: it is read when a request's scope vector is assembled,
but it is never consulted inside the authorization transaction itself. The
transaction reads only counters. That separation is deliberate -- it keeps the
transaction small, and it means a policy edit can never race an in-flight
authorization into an inconsistent decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .agent import RunawayPolicy
from .money import Money
from .scopes import ScopeLimits, ScopeType
from .tokens import TokenVector
from .window import DEFAULT_BILLING_TZ, WindowType


class PolicyError(ValueError):
    """Raised when a policy is internally inconsistent."""


#: A request that fans out to more scopes than this is rejected at policy-write
#: time rather than at request time. DynamoDB caps a transaction at 100 actions;
#: refusing early keeps the failure a configuration error the operator can fix,
#: instead of a runtime outage under load.
MAX_MANDATORY_SCOPES: int = 25


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """Limits for one scope over one kind of window."""

    scope_type: ScopeType
    scope_id: str
    limit: Money
    window_type: WindowType
    version: str = "1"
    tokens: TokenVector = field(default_factory=TokenVector.unlimited)
    warning_percent: int = 80
    billing_tz: str = DEFAULT_BILLING_TZ
    mandatory: bool = True

    def __post_init__(self) -> None:
        if self.limit.nano < 0:
            raise PolicyError(f"{self.scope_type}:{self.scope_id} limit cannot be negative")
        if not 0 < self.warning_percent < 100:
            raise PolicyError(
                f"warning_percent must be between 1 and 99, got {self.warning_percent}"
            )

    def scope_limits(self) -> ScopeLimits:
        return ScopeLimits(limit=self.limit, tokens=self.tokens)

    def admits(self, cost: Money, tokens: TokenVector) -> bool:
        """Whether a request of this size could *ever* fit in a pristine window.

        Checked in Python before building a transaction, because a condition
        expression cannot compare two literals. Answering it early turns an
        impossible request into an immediate, clearly-explained rejection rather
        than a confusing "budget exhausted" against a budget that is untouched.
        """
        return cost <= self.limit and tokens.fits_within(self.tokens)


@dataclass(frozen=True, slots=True)
class ModelAllocationPolicy:
    """A per-model sub-budget belonging to an agent.

    This exists to resolve a genuine ambiguity in the requirement "when the
    preferred model's budget is exhausted, reroute to a cheaper model". Taken
    literally against the agent's *total* budget it would be incoherent: an
    agent that has spent its entire $50 cannot be allowed to spend more on a
    cheaper model. The coherent reading is a sub-allocation -- $40 of the
    agent's $50 earmarked for the premium model -- so exhausting it leaves
    genuine agent capacity for the fallback to draw on.
    """

    provider: str
    model: str
    limit: Money
    window_type: WindowType = WindowType.MONTHLY
    tokens: TokenVector = field(default_factory=TokenVector.unlimited)


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    """One entry in a routing chain."""

    provider: str
    model: str
    #: Per-request output ceiling for this model, before client and policy caps
    #: are applied.
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    """Which models an agent may use, and in what order."""

    preferred: ModelCandidate
    fallbacks: tuple[ModelCandidate, ...] = ()
    allocations: tuple[ModelAllocationPolicy, ...] = ()
    allow_fallback: bool = True
    #: Refuse to substitute across providers unless explicitly permitted. A
    #: cross-provider swap changes data residency, tool semantics and response
    #: shape all at once, which is rarely what "use something cheaper" meant.
    require_same_provider: bool = True
    max_attempts: int = 3

    def chain(self) -> tuple[ModelCandidate, ...]:
        if not self.allow_fallback:
            return (self.preferred,)
        return (self.preferred, *self.fallbacks)

    def allocation_for(self, provider: str, model: str) -> ModelAllocationPolicy | None:
        for alloc in self.allocations:
            if alloc.provider == provider and alloc.model == model:
                return alloc
        return None


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    """The complete governance configuration for one agent."""

    agent_id: str
    team_id: str
    tenant_id: str
    budget: BudgetPolicy
    routing: RoutingPolicy
    session_budget: Money | None = None
    session_min_viable: Money | None = None
    #: Ceiling injected when a client omits an output limit. Without it, a
    #: client could bypass spend protection entirely by simply not sending
    #: max_tokens -- the reservation would have no worst case to bound.
    default_max_output_tokens: int = 4096
    runaway: RunawayPolicy = field(default_factory=RunawayPolicy)
    session_ttl_seconds: int = 86_400

    def __post_init__(self) -> None:
        if self.default_max_output_tokens <= 0:
            raise PolicyError("default_max_output_tokens must be positive")
        total_allocations = len(self.routing.allocations)
        if 4 + total_allocations > MAX_MANDATORY_SCOPES:
            raise PolicyError(
                f"agent {self.agent_id} fans out to more than {MAX_MANDATORY_SCOPES} scopes"
            )

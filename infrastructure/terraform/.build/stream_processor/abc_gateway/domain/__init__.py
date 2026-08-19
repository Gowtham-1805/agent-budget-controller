"""Core domain types for the Agent Budget Controller.

Nothing in this package knows about DynamoDB, HTTP, or any LLM provider. The
enforcement rules live here as pure values and pure functions so they can be
tested exhaustively without a network, a container, or a cloud account.
"""

from .agent import AgentState, AgentStatus, RunawayEvent, RunawayPolicy
from .alerts import AlertEvent, AlertKind, threshold_floor, utilization_percent
from .clock import Clock, ManualClock, SystemClock
from .errors import (
    AuthorizationDenied,
    Denial,
    DenialCode,
    GatewayError,
    IdempotencyConflict,
    IdempotentReplay,
    PendingReason,
    PlanBugError,
    ReleaseReason,
    RequestInFlight,
    TransientContention,
)
from .ledger import (
    BudgetDecision,
    CostBreakdown,
    LedgerKind,
    UsageLedgerEntry,
)
from .money import NANO_PER_USD, UNLIMITED, ZERO, Money, MoneyError
from .policy import (
    AgentPolicy,
    BudgetPolicy,
    ModelAllocationPolicy,
    ModelCandidate,
    PolicyError,
    RoutingPolicy,
)
from .reservation import (
    DispatchState,
    RequestReservation,
    ReservationGrant,
    ReservationState,
    ReservedScope,
)
from .scopes import ScopeDelta, ScopeLimits, ScopeRef, ScopeType, outermost
from .session import Session, SessionCloseReason, SessionStatus
from .state import BudgetState
from .tokens import UNLIMITED_TOKENS, TokenVector
from .window import BudgetWindow, WindowError, WindowType

__all__ = [
    "NANO_PER_USD",
    "UNLIMITED",
    "UNLIMITED_TOKENS",
    "ZERO",
    "AgentPolicy",
    "AgentState",
    "AgentStatus",
    "AlertEvent",
    "AlertKind",
    "AuthorizationDenied",
    "BudgetDecision",
    "BudgetPolicy",
    "BudgetState",
    "BudgetWindow",
    "Clock",
    "CostBreakdown",
    "Denial",
    "DenialCode",
    "DispatchState",
    "GatewayError",
    "IdempotencyConflict",
    "IdempotentReplay",
    "LedgerKind",
    "ManualClock",
    "ModelAllocationPolicy",
    "ModelCandidate",
    "Money",
    "MoneyError",
    "PendingReason",
    "PlanBugError",
    "PolicyError",
    "ReleaseReason",
    "RequestInFlight",
    "RequestReservation",
    "ReservationGrant",
    "ReservationState",
    "ReservedScope",
    "RoutingPolicy",
    "RunawayEvent",
    "RunawayPolicy",
    "ScopeDelta",
    "ScopeLimits",
    "ScopeRef",
    "ScopeType",
    "Session",
    "SessionCloseReason",
    "SessionStatus",
    "SystemClock",
    "TokenVector",
    "TransientContention",
    "UsageLedgerEntry",
    "WindowError",
    "WindowType",
    "outermost",
    "threshold_floor",
    "utilization_percent",
]

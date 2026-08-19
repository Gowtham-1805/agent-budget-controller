"""The enforcement engine.

Everything here serves one invariant, for every governed scope S:

    committed_S + reserved_S <= limit_S

held under concurrent traffic.
"""

from .budget_engine import (
    BudgetEngine,
    ReconcileResult,
    ReservationRequest,
    RetryPolicy,
    fingerprint_request,
    hash_idempotency_key,
)
from .effects import EffectOutcome, SettlementEffects
from .routing import NoEligibleModel, RoutingAttempt, RoutingEngine, RoutingOutcome
from .scope_resolver import (
    ScopeResolutionError,
    check_fits_in_pristine_window,
    resolve_scopes,
)

__all__ = [
    "BudgetEngine",
    "EffectOutcome",
    "NoEligibleModel",
    "ReconcileResult",
    "ReservationRequest",
    "RetryPolicy",
    "RoutingAttempt",
    "RoutingEngine",
    "RoutingOutcome",
    "ScopeResolutionError",
    "SettlementEffects",
    "check_fits_in_pristine_window",
    "fingerprint_request",
    "hash_idempotency_key",
    "resolve_scopes",
]

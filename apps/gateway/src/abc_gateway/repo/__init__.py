"""Persistence.

The engine builds backend-agnostic :class:`~.plans.TransactionPlan` objects;
each backend compiles them into its own atomic primitive. That boundary is what
lets one contract suite validate every backend identically.
"""

from .interfaces import BudgetRepository
from .plans import (
    IdempotencyPutSlot,
    ItemKey,
    LedgerPutSlot,
    PlanValidationError,
    ReservationPutSlot,
    ReservationUpdateSlot,
    ScopePendingSlot,
    ScopeReserveSlot,
    ScopeSeed,
    ScopeSettleSlot,
    SlotFailure,
    SlotKind,
    StatusCheckSlot,
    TransactionConflict,
    TransactionDenied,
    TransactionPlan,
)

__all__ = [
    "BudgetRepository",
    "IdempotencyPutSlot",
    "ItemKey",
    "LedgerPutSlot",
    "PlanValidationError",
    "ReservationPutSlot",
    "ReservationUpdateSlot",
    "ScopePendingSlot",
    "ScopeReserveSlot",
    "ScopeSeed",
    "ScopeSettleSlot",
    "SlotFailure",
    "SlotKind",
    "StatusCheckSlot",
    "TransactionConflict",
    "TransactionDenied",
    "TransactionPlan",
]

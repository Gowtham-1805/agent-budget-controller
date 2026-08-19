"""Item <-> domain conversion, shared by every backend.

Decoding lives here so both backends produce identical domain objects from
identical stored shapes. It also holds the strict numeric decoder: DynamoDB
returns numbers as ``Decimal``, and a value that is not a whole number is a bug
worth failing loudly on rather than truncating quietly.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..domain.money import Money
from ..domain.scopes import ScopeRef
from ..domain.state import BudgetState
from ..domain.tokens import TokenVector
from ..domain.window import BudgetWindow
from . import attributes as A


class SerdeError(ValueError):
    """A stored value could not be decoded safely."""


def to_int(value: Any, field: str = "value") -> int:
    """Decode a stored number as an exact int.

    Rejects fractional values instead of truncating. Every number this system
    stores is a count -- of nano-USD or of tokens -- so a fraction means
    something upstream used floating point, and silently flooring it would hide
    the corruption rather than surface it.
    """
    if isinstance(value, bool):
        raise SerdeError(f"{field}: expected a number, got a bool")
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        if value != value.to_integral_value():
            raise SerdeError(f"{field}: {value} is not a whole number")
        return int(value)
    if isinstance(value, float):
        raise SerdeError(f"{field}: floats are never stored; got {value!r}")
    if isinstance(value, str):
        try:
            dec = Decimal(value)
        except Exception as exc:
            raise SerdeError(f"{field}: {value!r} is not numeric") from exc
        if dec != dec.to_integral_value():
            raise SerdeError(f"{field}: {value} is not a whole number")
        return int(dec)
    raise SerdeError(f"{field}: cannot decode {type(value).__name__}")


def _int_field(item: dict[str, Any], name: str, default: int = 0) -> int:
    if name not in item:
        return default
    return to_int(item[name], name)


def budget_state_from_item(
    item: dict[str, Any],
    scope: ScopeRef,
    window: BudgetWindow,
) -> BudgetState:
    """Rebuild counters from a stored budget-window item."""
    return BudgetState(
        scope=scope,
        window=window,
        limit=Money(_int_field(item, A.LIMIT_NANO)),
        token_limits=TokenVector(
            input=_int_field(item, A.MAX_INPUT),
            output=_int_field(item, A.MAX_OUTPUT),
            total=_int_field(item, A.MAX_TOTAL),
        ),
        remaining=Money(_int_field(item, A.REMAINING_NANO)),
        remaining_tokens=TokenVector(
            input=max(0, _int_field(item, A.REMAINING_INPUT)),
            output=max(0, _int_field(item, A.REMAINING_OUTPUT)),
            total=max(0, _int_field(item, A.REMAINING_TOTAL)),
        ),
        committed=Money(_int_field(item, A.COMMITTED_NANO)),
        reserved=Money(_int_field(item, A.RESERVED_NANO)),
        pending=Money(_int_field(item, A.PENDING_NANO)),
        overage=Money(_int_field(item, A.OVERAGE_NANO)),
        committed_tokens=TokenVector(
            input=_int_field(item, A.COMMITTED_INPUT),
            output=_int_field(item, A.COMMITTED_OUTPUT),
            total=_int_field(item, A.COMMITTED_TOTAL),
        ),
        reserved_tokens=TokenVector(
            input=max(0, _int_field(item, A.RESERVED_INPUT)),
            output=max(0, _int_field(item, A.RESERVED_OUTPUT)),
            total=max(0, _int_field(item, A.RESERVED_TOTAL)),
        ),
        open_reservations=_int_field(item, A.OPEN_RESERVATIONS),
        warning_80_sent=bool(item.get(A.WARNING_80_SENT, False)),
        warning_100_sent=bool(item.get(A.WARNING_100_SENT, False)),
        policy_version=str(item.get(A.POLICY_VERSION, "1")),
        version=_int_field(item, A.VERSION),
    )

"""Domain <-> DynamoDB item conversion.

Two rules govern everything here.

**Numbers are integers or they are errors.** DynamoDB returns numbers as
``Decimal``. Every number this system stores is a count -- of nano-USD or of
tokens -- so a fractional value means something upstream used floating point.
Truncating it would hide the corruption; this module raises instead.

**Floats are never written.** There is no code path that puts a float into an
item, because the moment one exists the exactness guarantee is gone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ...domain.ledger import CostBreakdown, LedgerKind, UsageLedgerEntry
from ...domain.money import Money
from ...domain.reservation import (
    DispatchState,
    RequestReservation,
    ReservationState,
    ReservedScope,
)
from ...domain.scopes import ScopeRef, ScopeType
from ...domain.tokens import TokenVector
from .. import attributes as A
from ..items import SerdeError
from ..plans import ItemKey


def num(value: int) -> dict[str, str]:
    if isinstance(value, float):
        raise SerdeError("refusing to serialise a float; money and tokens are integers")
    return {"N": str(int(value))}


def txt(value: str) -> dict[str, str]:
    return {"S": value}


def decode_number(attr: dict[str, Any], field: str = "value") -> int:
    """Decode an ``{"N": "..."}`` attribute as an exact int."""
    raw = attr.get("N")
    if raw is None:
        raise SerdeError(f"{field}: not a number attribute")
    dec = Decimal(raw)
    if dec != dec.to_integral_value():
        raise SerdeError(f"{field}: {raw} is not a whole number")
    return int(dec)


def plain(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten a DynamoDB item into plain Python values.

    Numbers come back as ``int`` -- never ``Decimal`` and never ``float`` -- so
    downstream code cannot accidentally do inexact arithmetic on a balance.
    """
    return {key: unwrap(attr, key) for key, attr in item.items()}


def unwrap(attr: Any, field: str = "value") -> Any:
    """Convert a single DynamoDB attribute value to a plain Python value.

    Separate from :func:`plain` because the two operate on different shapes: an
    *item* is a mapping of field names to attribute values, whereas a list
    element is a bare attribute value. Conflating them turns a list of maps into
    a list of ``{"M": ...}`` wrappers -- which is silent until something reaches
    in for a key that is now one level deeper than expected.
    """
    if not isinstance(attr, dict):
        return attr
    if "N" in attr:
        return decode_number(attr, field)
    if "S" in attr:
        return attr["S"]
    if "BOOL" in attr:
        return attr["BOOL"]
    if "NULL" in attr:
        return None
    if "L" in attr:
        return [unwrap(v, field) for v in attr["L"]]
    if "M" in attr:
        return plain(attr["M"])
    if "SS" in attr:
        return list(attr["SS"])
    return attr


def _wrap(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"BOOL": value}
    if isinstance(value, int):
        return num(value)
    if isinstance(value, float):
        raise SerdeError("refusing to serialise a float")
    if value is None:
        return {"NULL": True}
    if isinstance(value, dict):
        return {"M": {k: _wrap(v) for k, v in value.items()}}
    if isinstance(value, (list, tuple)):
        return {"L": [_wrap(v) for v in value]}
    return txt(str(value))


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------


def reservation_to_item(reservation: RequestReservation, key: ItemKey) -> dict[str, Any]:
    """Serialise a reservation, including its per-scope undo vector.

    The scope vector is stored rather than recomputed at settlement time.
    Policy can change between reserve and reconcile, and reversing a hold using
    today's policy instead of the amount actually taken would corrupt the
    counters in a way no later reconciliation could detect.
    """
    return {
        A.PK: txt(key.partition_suffix),
        A.SK: txt(key.sort_key),
        A.ENTITY_TYPE: txt(A.E_RESERVATION),
        A.STATE: txt(reservation.state.value),
        A.DISPATCH_STATE: txt(reservation.dispatch_state.value),
        A.TENANT_ID: txt(reservation.tenant_id),
        "reservation_id": txt(reservation.reservation_id),
        "team_id": txt(reservation.team_id),
        "agent_id": txt(reservation.agent_id),
        "session_id": txt(reservation.session_id or ""),
        "provider": txt(reservation.provider),
        "requested_model": txt(reservation.requested_model),
        "effective_model": txt(reservation.effective_model),
        "reserved_nano": num(reservation.reserved_cost.nano),
        "reserved_input_tokens": num(reservation.reserved_tokens.input),
        "reserved_output_tokens": num(reservation.reserved_tokens.output),
        "reserved_total_tokens": num(reservation.reserved_tokens.total),
        "preflight_input_tokens": num(reservation.preflight_input_tokens),
        "bounded_max_output_tokens": num(reservation.bounded_max_output_tokens),
        "estimated_input_cost_nano": num(reservation.estimated_cost.input_cost.nano),
        "estimated_output_cost_nano": num(reservation.estimated_cost.output_cost.nano),
        "estimated_max_cost_nano": num(reservation.estimated_cost.total.nano),
        "price_catalog_version": txt(reservation.price_catalog_version),
        "created_at_epoch": num(int(reservation.created_at.timestamp())),
        "expires_at_epoch": num(int(reservation.expires_at.timestamp())),
        "idempotency_key_hash": txt(reservation.idempotency_key_hash or ""),
        "request_fingerprint": txt(reservation.request_fingerprint or ""),
        "attempt": num(reservation.attempt),
        "scopes": {
            "L": [
                {
                    "M": {
                        "scope_type": txt(scope.scope.type.value),
                        "scope_id": txt(scope.scope.id),
                        "partition_suffix": txt(scope.partition_suffix),
                        "sort_key": txt(scope.sort_key),
                        "cost_nano": num(scope.cost.nano),
                        "input_tokens": num(scope.tokens.input),
                        "output_tokens": num(scope.tokens.output),
                        "total_tokens": num(scope.tokens.total),
                    }
                }
                for scope in reservation.scopes
            ]
        },
        # Sparse GSI for the stale-reservation sweeper: removed on settlement,
        # so the index only ever holds work that still needs doing.
        A.GSI2PK: txt(f"RSTATE#{reservation.state.value}"),
        A.GSI2SK: txt(f"EXP#{int(reservation.expires_at.timestamp()):020d}"),
    }


def reservation_from_item(item: dict[str, Any]) -> RequestReservation:
    flat = plain(item)
    scopes = tuple(
        ReservedScope(
            scope=ScopeRef(ScopeType(sc["scope_type"]), sc["scope_id"]),
            partition_suffix=sc["partition_suffix"],
            sort_key=sc["sort_key"],
            cost=Money(sc["cost_nano"]),
            tokens=TokenVector(
                input=sc["input_tokens"],
                output=sc["output_tokens"],
                total=sc["total_tokens"],
            ),
        )
        for sc in flat.get("scopes", [])
    )
    return RequestReservation(
        reservation_id=flat["reservation_id"],
        tenant_id=flat[A.TENANT_ID],
        team_id=flat["team_id"],
        agent_id=flat["agent_id"],
        session_id=flat.get("session_id") or None,
        state=ReservationState(flat[A.STATE]),
        dispatch_state=DispatchState(flat[A.DISPATCH_STATE]),
        provider=flat["provider"],
        requested_model=flat["requested_model"],
        effective_model=flat["effective_model"],
        reserved_cost=Money(flat["reserved_nano"]),
        reserved_tokens=TokenVector(
            input=flat["reserved_input_tokens"],
            output=flat["reserved_output_tokens"],
            total=flat["reserved_total_tokens"],
        ),
        preflight_input_tokens=flat["preflight_input_tokens"],
        bounded_max_output_tokens=flat["bounded_max_output_tokens"],
        estimated_cost=CostBreakdown(
            input_cost=Money(flat.get("estimated_input_cost_nano", 0)),
            output_cost=Money(flat.get("estimated_output_cost_nano", 0)),
        ),
        scopes=scopes,
        price_catalog_version=flat["price_catalog_version"],
        created_at=datetime.fromtimestamp(flat["created_at_epoch"], tz=UTC),
        expires_at=datetime.fromtimestamp(flat["expires_at_epoch"], tz=UTC),
        idempotency_key_hash=flat.get("idempotency_key_hash") or None,
        request_fingerprint=flat.get("request_fingerprint") or None,
        attempt=flat.get("attempt", 1),
    )


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


def ledger_entry_to_item(entry: UsageLedgerEntry, key: ItemKey) -> dict[str, Any]:
    """Serialise an immutable ledger entry.

    Every field the audit story needs is stored explicitly -- especially the
    price-catalog version, without which recomputing historical spend after a
    price change would silently rewrite the past.
    """
    return {
        A.PK: txt(key.partition_suffix),
        A.SK: txt(key.sort_key),
        A.ENTITY_TYPE: txt(A.E_LEDGER_ENTRY),
        "entry_id": txt(entry.entry_id),
        "kind": txt(entry.kind.value),
        "reservation_id": txt(entry.reservation_id),
        A.TENANT_ID: txt(entry.tenant_id),
        "team_id": txt(entry.team_id),
        "agent_id": txt(entry.agent_id),
        "session_id": txt(entry.session_id or ""),
        "provider": txt(entry.provider),
        "requested_model": txt(entry.requested_model),
        "effective_model": txt(entry.effective_model),
        "decision": txt(entry.decision.value),
        "preflight_input_tokens": num(entry.preflight_input_tokens),
        "reserved_output_tokens": num(entry.reserved_output_tokens),
        "estimated_input_cost_nano": num(entry.estimated_cost.input_cost.nano),
        "estimated_output_cost_nano": num(entry.estimated_cost.output_cost.nano),
        "estimated_tool_cost_nano": num(entry.estimated_cost.tool_cost.nano),
        "estimated_max_cost_nano": num(entry.estimated_max_cost.nano),
        "reserved_nano": num(entry.reserved_cost.nano),
        "actual_input_tokens": num(entry.actual_tokens.input),
        "actual_output_tokens": num(entry.actual_tokens.output),
        "actual_total_tokens": num(entry.actual_tokens.total),
        "actual_cached_input_tokens": num(entry.actual_cached_input_tokens),
        "actual_reasoning_tokens": num(entry.actual_reasoning_tokens),
        "actual_input_cost_nano": num(entry.actual_cost.input_cost.nano),
        "actual_cached_input_cost_nano": num(entry.actual_cost.cached_input_cost.nano),
        "actual_output_cost_nano": num(entry.actual_cost.output_cost.nano),
        "actual_tool_cost_nano": num(entry.actual_cost.tool_cost.nano),
        "actual_total_cost_nano": num(entry.actual_total_cost.nano),
        "price_catalog_version": txt(entry.price_catalog_version),
        "created_at_epoch": num(int(entry.created_at.timestamp())),
        "completed_at_epoch": num(int((entry.completed_at or entry.created_at).timestamp())),
        "provider_request_id": txt(entry.provider_request_id or ""),
        "corrects_entry_id": txt(entry.corrects_entry_id or ""),
        "scope_keys": {"SS": list(entry.scope_keys)} if entry.scope_keys else {"NULL": True},
        A.GSI1PK: txt(f"TNT#{entry.tenant_id}#AGENT#{entry.agent_id}"),
        A.GSI1SK: txt(f"TS#{int((entry.completed_at or entry.created_at).timestamp()):020d}"),
    }


def ledger_entry_from_item(item: dict[str, Any]) -> UsageLedgerEntry:
    from ...domain.ledger import BudgetDecision

    flat = plain(item)
    return UsageLedgerEntry(
        entry_id=flat["entry_id"],
        kind=LedgerKind(flat["kind"]),
        reservation_id=flat["reservation_id"],
        tenant_id=flat[A.TENANT_ID],
        team_id=flat["team_id"],
        agent_id=flat["agent_id"],
        session_id=flat.get("session_id") or None,
        provider=flat["provider"],
        requested_model=flat["requested_model"],
        effective_model=flat["effective_model"],
        decision=BudgetDecision(flat["decision"]),
        preflight_input_tokens=flat["preflight_input_tokens"],
        reserved_output_tokens=flat["reserved_output_tokens"],
        estimated_cost=CostBreakdown(
            input_cost=Money(flat["estimated_input_cost_nano"]),
            output_cost=Money(flat["estimated_output_cost_nano"]),
            tool_cost=Money(flat.get("estimated_tool_cost_nano", 0)),
        ),
        estimated_max_cost=Money(flat["estimated_max_cost_nano"]),
        reserved_cost=Money(flat["reserved_nano"]),
        actual_tokens=TokenVector(
            input=flat["actual_input_tokens"],
            output=flat["actual_output_tokens"],
            total=flat["actual_total_tokens"],
        ),
        actual_cached_input_tokens=flat["actual_cached_input_tokens"],
        actual_reasoning_tokens=flat["actual_reasoning_tokens"],
        actual_cost=CostBreakdown(
            input_cost=Money(flat["actual_input_cost_nano"]),
            cached_input_cost=Money(flat["actual_cached_input_cost_nano"]),
            output_cost=Money(flat["actual_output_cost_nano"]),
            tool_cost=Money(flat["actual_tool_cost_nano"]),
        ),
        actual_total_cost=Money(flat["actual_total_cost_nano"]),
        price_catalog_version=flat["price_catalog_version"],
        created_at=datetime.fromtimestamp(flat["created_at_epoch"], tz=UTC),
        completed_at=datetime.fromtimestamp(flat["completed_at_epoch"], tz=UTC),
        provider_request_id=flat.get("provider_request_id") or None,
        corrects_entry_id=flat.get("corrects_entry_id") or None,
        scope_keys=tuple(flat.get("scope_keys") or ()),
    )

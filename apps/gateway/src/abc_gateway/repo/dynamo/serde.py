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

from ...domain.agent import RunawayPolicy
from ...domain.ledger import CostBreakdown, LedgerKind, UsageLedgerEntry
from ...domain.money import Money
from ...domain.policy import (
    AgentPolicy,
    BudgetPolicy,
    ModelAllocationPolicy,
    ModelCandidate,
    RoutingPolicy,
)
from ...domain.reservation import (
    DispatchState,
    RequestReservation,
    ReservationState,
    ReservedScope,
)
from ...domain.scopes import ScopeRef, ScopeType
from ...domain.tokens import TokenVector
from ...domain.window import DEFAULT_BILLING_TZ, WindowType
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


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
#
# Policy data (budget limits, routing chains, model allocations) is
# configuration, not a counter -- nothing here needs an atomic condition
# expression, so nested structures are stored as native DynamoDB M/L values
# via `_wrap`/`plain` rather than flattened into scalar attributes. That keeps
# a routing chain of arbitrary length representable without a schema change.


def _tokens_to_plain(tokens: TokenVector) -> dict[str, int]:
    return {"input": tokens.input, "output": tokens.output, "total": tokens.total}


def _tokens_from_plain(data: dict[str, Any] | None) -> TokenVector:
    if not data:
        return TokenVector.unlimited()
    return TokenVector(
        input=int(data["input"]), output=int(data["output"]), total=int(data["total"])
    )


def _candidate_to_plain(candidate: ModelCandidate) -> dict[str, Any]:
    return {
        "provider": candidate.provider,
        "model": candidate.model,
        "max_output_tokens": candidate.max_output_tokens,
    }


def _candidate_from_plain(data: dict[str, Any]) -> ModelCandidate:
    return ModelCandidate(
        provider=data["provider"],
        model=data["model"],
        max_output_tokens=int(data["max_output_tokens"]),
    )


def _allocation_to_plain(allocation: ModelAllocationPolicy) -> dict[str, Any]:
    return {
        "provider": allocation.provider,
        "model": allocation.model,
        "limit_nano": allocation.limit.nano,
        "window_type": allocation.window_type.value,
        "tokens": _tokens_to_plain(allocation.tokens),
    }


def _allocation_from_plain(data: dict[str, Any]) -> ModelAllocationPolicy:
    return ModelAllocationPolicy(
        provider=data["provider"],
        model=data["model"],
        limit=Money(int(data["limit_nano"])),
        window_type=WindowType(data["window_type"]),
        tokens=_tokens_from_plain(data.get("tokens")),
    )


def _budget_policy_to_plain(policy: BudgetPolicy) -> dict[str, Any]:
    return {
        "scope_type": policy.scope_type.value,
        "scope_id": policy.scope_id,
        "limit_nano": policy.limit.nano,
        "window_type": policy.window_type.value,
        "version": policy.version,
        "warning_percent": policy.warning_percent,
        "billing_tz": policy.billing_tz,
        "mandatory": policy.mandatory,
        "tokens": _tokens_to_plain(policy.tokens),
    }


def _budget_policy_from_plain(data: dict[str, Any]) -> BudgetPolicy:
    return BudgetPolicy(
        scope_type=ScopeType(data["scope_type"]),
        scope_id=data["scope_id"],
        limit=Money(int(data["limit_nano"])),
        window_type=WindowType(data["window_type"]),
        version=data.get("version", "1"),
        tokens=_tokens_from_plain(data.get("tokens")),
        warning_percent=int(data.get("warning_percent", 80)),
        billing_tz=data.get("billing_tz", DEFAULT_BILLING_TZ),
        mandatory=bool(data.get("mandatory", True)),
    )


def budget_policy_to_item(policy: BudgetPolicy, tenant_id: str, key: ItemKey) -> dict[str, Any]:
    """Serialise one scope's budget policy (team, or any non-agent scope)."""
    return {
        A.PK: txt(key.partition_suffix),
        A.SK: txt(key.sort_key),
        A.ENTITY_TYPE: txt(A.E_BUDGET_POLICY),
        A.TENANT_ID: txt(tenant_id),
        **_wrap(_budget_policy_to_plain(policy))["M"],
    }


def budget_policy_from_item(item: dict[str, Any]) -> BudgetPolicy:
    return _budget_policy_from_plain(plain(item))


def agent_policy_to_item(policy: AgentPolicy, key: ItemKey) -> dict[str, Any]:
    """Serialise the complete governance configuration for one agent."""
    routing = policy.routing
    routing_plain = {
        "preferred": _candidate_to_plain(routing.preferred),
        "fallbacks": [_candidate_to_plain(c) for c in routing.fallbacks],
        "allocations": [_allocation_to_plain(a) for a in routing.allocations],
        "allow_fallback": routing.allow_fallback,
        "require_same_provider": routing.require_same_provider,
        "max_attempts": routing.max_attempts,
    }
    runaway_plain = {
        "monthly_budget_percent": policy.runaway.monthly_budget_percent,
        "interval_minutes": policy.runaway.interval_minutes,
        "enabled": policy.runaway.enabled,
    }
    return {
        A.PK: txt(key.partition_suffix),
        A.SK: txt(key.sort_key),
        A.ENTITY_TYPE: txt(A.E_AGENT_POLICY),
        A.TENANT_ID: txt(policy.tenant_id),
        "agent_id": txt(policy.agent_id),
        "team_id": txt(policy.team_id),
        "budget": _wrap(_budget_policy_to_plain(policy.budget)),
        "routing": _wrap(routing_plain),
        "session_budget_nano": _wrap(
            policy.session_budget.nano if policy.session_budget is not None else None
        ),
        "session_min_viable_nano": _wrap(
            policy.session_min_viable.nano if policy.session_min_viable is not None else None
        ),
        "default_max_output_tokens": num(policy.default_max_output_tokens),
        "runaway": _wrap(runaway_plain),
        "session_ttl_seconds": num(policy.session_ttl_seconds),
    }


def agent_policy_from_item(item: dict[str, Any]) -> AgentPolicy:
    flat = plain(item)
    routing_data = flat["routing"]
    runaway_data = flat.get("runaway") or {}

    routing = RoutingPolicy(
        preferred=_candidate_from_plain(routing_data["preferred"]),
        fallbacks=tuple(_candidate_from_plain(c) for c in routing_data.get("fallbacks") or ()),
        allocations=tuple(
            _allocation_from_plain(a) for a in routing_data.get("allocations") or ()
        ),
        allow_fallback=bool(routing_data.get("allow_fallback", True)),
        require_same_provider=bool(routing_data.get("require_same_provider", True)),
        max_attempts=int(routing_data.get("max_attempts", 3)),
    )
    runaway = RunawayPolicy(
        monthly_budget_percent=int(runaway_data.get("monthly_budget_percent", 20)),
        interval_minutes=int(runaway_data.get("interval_minutes", 60)),
        enabled=bool(runaway_data.get("enabled", True)),
    )

    session_budget_nano = flat.get("session_budget_nano")
    session_min_viable_nano = flat.get("session_min_viable_nano")

    return AgentPolicy(
        agent_id=flat["agent_id"],
        team_id=flat["team_id"],
        tenant_id=flat[A.TENANT_ID],
        budget=_budget_policy_from_plain(flat["budget"]),
        routing=routing,
        session_budget=(
            Money(int(session_budget_nano)) if session_budget_nano is not None else None
        ),
        session_min_viable=(
            Money(int(session_min_viable_nano)) if session_min_viable_nano is not None else None
        ),
        default_max_output_tokens=int(flat.get("default_max_output_tokens", 4096)),
        runaway=runaway,
        session_ttl_seconds=int(flat.get("session_ttl_seconds", 86_400)),
    )

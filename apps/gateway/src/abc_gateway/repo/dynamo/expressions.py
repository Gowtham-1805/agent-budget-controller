"""DynamoDB expression builders.

The entire financial invariant is enforced by the expressions in this file, so
it is worth stating plainly why they look the way they do.

**Condition expressions have no arithmetic.** You cannot write
``committed + reserved + :cost <= limit``. What you *can* write is
``remaining_nano >= :cost``, and ``SET`` in an update expression *does* support
subtraction. That is the whole reason ``remaining`` is maintained as a
materialised decrementing counter rather than derived on read.

**Lazy window creation needs an OR.** A budget window item is created by the
first request that touches it, so the condition is::

    attribute_not_exists(PK) OR (remaining_nano >= :cost AND ...tokens fit)

Without the ``OR``, two concurrent first-requests would both plan against
"absent"; whichever committed second would find the item present and be
rejected, despite the budget being untouched. That is precisely the millisecond
after a window boundary, when traffic spikes.

**Seeding uses SET-with-minus, not ADD.** ``ADD`` on an absent attribute yields
``-cost`` rather than ``limit - cost``, and you cannot ``SET`` and ``ADD`` the
same path in one expression ("two document paths overlap"). Only
``SET x = if_not_exists(x, :limit) - :cost`` creates and decrements atomically.

**Every action asks for the pre-image on failure.** Without
``ReturnValuesOnConditionCheckFailure: ALL_OLD`` the best a denial could say is
"a condition failed". With it, the API can name the scope, the dimension, the
balance and the shortfall.
"""

from __future__ import annotations

from typing import Any

from ...domain.money import Money
from ...domain.tokens import TokenVector
from .. import attributes as A
from ..plans import (
    IdempotencyPutSlot,
    LedgerPutSlot,
    ReservationPutSlot,
    ReservationUpdateSlot,
    ScopePendingSlot,
    ScopeReserveSlot,
    ScopeSettleSlot,
    Slot,
    StatusCheckSlot,
)

ALL_OLD = "ALL_OLD"


def n(value: int) -> dict[str, str]:
    """A DynamoDB number attribute. Always an integer -- never a float."""
    return {"N": str(int(value))}


def s(value: str) -> dict[str, str]:
    return {"S": value}


def b(value: bool) -> dict[str, bool]:
    return {"BOOL": value}


def build_action(slot: Slot, table_names: dict[str, str]) -> dict[str, Any]:
    """Compile one plan slot into a TransactWriteItems action."""
    match slot:
        case ScopeReserveSlot():
            return _reserve_action(slot, table_names)
        case ScopeSettleSlot():
            return _settle_action(slot, table_names)
        case ScopePendingSlot():
            return _pending_action(slot, table_names)
        case StatusCheckSlot():
            return _status_check_action(slot, table_names)
        case IdempotencyPutSlot():
            return _idempotency_action(slot, table_names)
        case ReservationPutSlot():
            return _reservation_put_action(slot, table_names)
        case ReservationUpdateSlot():
            return _reservation_update_action(slot, table_names)
        case LedgerPutSlot():
            return _ledger_put_action(slot, table_names)
        case _:  # pragma: no cover
            raise TypeError(f"cannot compile slot: {type(slot).__name__}")


def _key(slot: Slot) -> dict[str, Any]:
    return {A.PK: s(slot.key.partition_suffix), A.SK: s(slot.key.sort_key)}


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def _reserve_action(slot: ScopeReserveSlot, tables: dict[str, str]) -> dict[str, Any]:
    seed = slot.seed
    limits = seed.limits

    condition = (
        "attribute_not_exists(#pk) OR ("
        "#rem_nano >= :cost AND "
        "#rem_in >= :in_tok AND "
        "#rem_out >= :out_tok AND "
        "#rem_tot >= :tot_tok)"
    )

    # SET creates-from-limit and decrements in one step; ADD accumulates the
    # reporting counters, which always exist by the time ADD runs because the
    # SET clause seeds them in the same expression.
    update = (
        "SET "
        "#rem_nano = if_not_exists(#rem_nano, :limit) - :cost, "
        "#rem_in = if_not_exists(#rem_in, :max_in) - :in_tok, "
        "#rem_out = if_not_exists(#rem_out, :max_out) - :out_tok, "
        "#rem_tot = if_not_exists(#rem_tot, :max_tot) - :tot_tok, "
        "#limit = if_not_exists(#limit, :limit), "
        "#max_in = if_not_exists(#max_in, :max_in), "
        "#max_out = if_not_exists(#max_out, :max_out), "
        "#max_tot = if_not_exists(#max_tot, :max_tot), "
        "#committed_nano = if_not_exists(#committed_nano, :zero), "
        "#committed_in = if_not_exists(#committed_in, :zero), "
        "#committed_out = if_not_exists(#committed_out, :zero), "
        "#committed_tot = if_not_exists(#committed_tot, :zero), "
        "#pending_nano = if_not_exists(#pending_nano, :zero), "
        "#overage_nano = if_not_exists(#overage_nano, :zero), "
        "#warn80 = if_not_exists(#warn80, :false), "
        "#warn100 = if_not_exists(#warn100, :false), "
        "#entity = if_not_exists(#entity, :entity), "
        "#scope_type = if_not_exists(#scope_type, :scope_type), "
        "#scope_id = if_not_exists(#scope_id, :scope_id), "
        "#tenant = if_not_exists(#tenant, :tenant), "
        "#window_type = if_not_exists(#window_type, :window_type), "
        "#window_id = if_not_exists(#window_id, :window_id), "
        "#window_start = if_not_exists(#window_start, :window_start), "
        "#window_end = if_not_exists(#window_end, :window_end), "
        "#policy_version = if_not_exists(#policy_version, :policy_version), "
        "#ttl = if_not_exists(#ttl, :ttl), "
        "#gsi1pk = if_not_exists(#gsi1pk, :gsi1pk), "
        "#gsi1sk = if_not_exists(#gsi1sk, :gsi1sk) "
        "ADD "
        "#reserved_nano :cost, "
        "#reserved_in :in_tok, "
        "#reserved_out :out_tok, "
        "#reserved_tot :tot_tok, "
        "#open_res :one, "
        "#version :one"
    )

    return {
        "Update": {
            "TableName": tables[slot.key.table],
            "Key": _key(slot),
            "ConditionExpression": condition,
            "UpdateExpression": update,
            "ExpressionAttributeNames": {
                "#pk": A.PK,
                "#rem_nano": A.REMAINING_NANO,
                "#rem_in": A.REMAINING_INPUT,
                "#rem_out": A.REMAINING_OUTPUT,
                "#rem_tot": A.REMAINING_TOTAL,
                "#limit": A.LIMIT_NANO,
                "#max_in": A.MAX_INPUT,
                "#max_out": A.MAX_OUTPUT,
                "#max_tot": A.MAX_TOTAL,
                "#committed_nano": A.COMMITTED_NANO,
                "#committed_in": A.COMMITTED_INPUT,
                "#committed_out": A.COMMITTED_OUTPUT,
                "#committed_tot": A.COMMITTED_TOTAL,
                "#reserved_nano": A.RESERVED_NANO,
                "#reserved_in": A.RESERVED_INPUT,
                "#reserved_out": A.RESERVED_OUTPUT,
                "#reserved_tot": A.RESERVED_TOTAL,
                "#pending_nano": A.PENDING_NANO,
                "#overage_nano": A.OVERAGE_NANO,
                "#open_res": A.OPEN_RESERVATIONS,
                "#warn80": A.WARNING_80_SENT,
                "#warn100": A.WARNING_100_SENT,
                "#entity": A.ENTITY_TYPE,
                "#scope_type": A.SCOPE_TYPE,
                "#scope_id": A.SCOPE_ID,
                "#tenant": A.TENANT_ID,
                "#window_type": A.WINDOW_TYPE,
                "#window_id": A.WINDOW_ID,
                "#window_start": A.WINDOW_START_EPOCH,
                "#window_end": A.WINDOW_END_EPOCH,
                "#policy_version": A.POLICY_VERSION,
                "#ttl": A.HOUSEKEEPING_TTL,
                "#gsi1pk": A.GSI1PK,
                "#gsi1sk": A.GSI1SK,
                "#version": A.VERSION,
            },
            "ExpressionAttributeValues": {
                ":cost": n(slot.cost.nano),
                ":in_tok": n(slot.tokens.input),
                ":out_tok": n(slot.tokens.output),
                ":tot_tok": n(slot.tokens.total),
                ":limit": n(limits.limit.nano),
                ":max_in": n(limits.tokens.input),
                ":max_out": n(limits.tokens.output),
                ":max_tot": n(limits.tokens.total),
                ":zero": n(0),
                ":one": n(1),
                ":false": b(False),
                ":entity": s(A.E_BUDGET_STATE),
                ":scope_type": s(seed.scope_type),
                ":scope_id": s(seed.scope_id),
                ":tenant": s(seed.tenant_id),
                ":window_type": s(seed.window_type),
                ":window_id": s(seed.window_id),
                ":window_start": n(seed.window_start_epoch),
                ":window_end": n(seed.window_end_epoch),
                ":policy_version": s(seed.policy_version),
                ":ttl": n(seed.housekeeping_ttl_epoch),
                ":gsi1pk": s(f"TNT#{seed.tenant_id}#SCOPETYPE#{seed.scope_type}"),
                ":gsi1sk": s(f"WINDOW#{seed.window_type}#{seed.window_id}#{seed.scope_id}"),
            },
            "ReturnValuesOnConditionCheckFailure": ALL_OLD,
        }
    }


# ---------------------------------------------------------------------------
# Settlement
# ---------------------------------------------------------------------------


def _settle_action(slot: ScopeSettleSlot, tables: dict[str, str]) -> dict[str, Any]:
    """Convert a hold into settled spend (or return it).

    Deliberately carries no budget condition. Recording what a provider actually
    charged us must never fail -- refusing to write the truth in order to keep a
    counter tidy is how books stop being trustworthy. The only guard is an
    accounting sanity check that the hold being reversed actually exists.
    """
    delta = slot.reserved_cost.nano - slot.actual_cost.nano
    token_delta = _token_delta(slot.reserved_tokens, slot.actual_tokens)

    update = (
        "SET #updated = :now "
        "ADD "
        "#rem_nano :delta_nano, "
        "#committed_nano :actual_nano, "
        "#reserved_nano :neg_reserved, "
        "#rem_in :delta_in, #committed_in :actual_in, #reserved_in :neg_res_in, "
        "#rem_out :delta_out, #committed_out :actual_out, #reserved_out :neg_res_out, "
        "#rem_tot :delta_tot, #committed_tot :actual_tot, #reserved_tot :neg_res_tot, "
        "#overage_nano :overage, "
        "#open_res :neg_one, "
        "#version :one"
    )

    return {
        "Update": {
            "TableName": tables[slot.key.table],
            "Key": _key(slot),
            "ConditionExpression": "attribute_exists(#pk) AND #reserved_nano >= :reserved",
            "UpdateExpression": update,
            "ExpressionAttributeNames": {
                "#pk": A.PK,
                "#rem_nano": A.REMAINING_NANO,
                "#rem_in": A.REMAINING_INPUT,
                "#rem_out": A.REMAINING_OUTPUT,
                "#rem_tot": A.REMAINING_TOTAL,
                "#committed_nano": A.COMMITTED_NANO,
                "#committed_in": A.COMMITTED_INPUT,
                "#committed_out": A.COMMITTED_OUTPUT,
                "#committed_tot": A.COMMITTED_TOTAL,
                "#reserved_nano": A.RESERVED_NANO,
                "#reserved_in": A.RESERVED_INPUT,
                "#reserved_out": A.RESERVED_OUTPUT,
                "#reserved_tot": A.RESERVED_TOTAL,
                "#overage_nano": A.OVERAGE_NANO,
                "#open_res": A.OPEN_RESERVATIONS,
                "#updated": A.UPDATED_AT,
                "#version": A.VERSION,
            },
            "ExpressionAttributeValues": {
                ":delta_nano": n(delta),
                ":actual_nano": n(slot.actual_cost.nano),
                ":reserved": n(slot.reserved_cost.nano),
                ":neg_reserved": n(-slot.reserved_cost.nano),
                ":delta_in": n(token_delta["input"]),
                ":delta_out": n(token_delta["output"]),
                ":delta_tot": n(token_delta["total"]),
                ":actual_in": n(slot.actual_tokens.input),
                ":actual_out": n(slot.actual_tokens.output),
                ":actual_tot": n(slot.actual_tokens.total),
                ":neg_res_in": n(-slot.reserved_tokens.input),
                ":neg_res_out": n(-slot.reserved_tokens.output),
                ":neg_res_tot": n(-slot.reserved_tokens.total),
                ":overage": n(slot.overage.nano),
                ":neg_one": n(-1),
                ":one": n(1),
                ":now": n(0),
            },
            "ReturnValuesOnConditionCheckFailure": ALL_OLD,
        }
    }


def _pending_action(slot: ScopePendingSlot, tables: dict[str, str]) -> dict[str, Any]:
    """Mark a hold unresolved without releasing it.

    Only ``pending`` moves. ``remaining``, ``reserved`` and ``committed`` stay
    exactly as they were, so the money remains encumbered while we do not know
    whether the provider billed us.
    """
    return {
        "Update": {
            "TableName": tables[slot.key.table],
            "Key": _key(slot),
            "ConditionExpression": "attribute_exists(#pk) AND #reserved_nano >= :reserved",
            "UpdateExpression": "ADD #pending_nano :reserved",
            "ExpressionAttributeNames": {
                "#pk": A.PK,
                "#reserved_nano": A.RESERVED_NANO,
                "#pending_nano": A.PENDING_NANO,
            },
            "ExpressionAttributeValues": {":reserved": n(slot.reserved_cost.nano)},
            "ReturnValuesOnConditionCheckFailure": ALL_OLD,
        }
    }


def _token_delta(reserved: TokenVector, actual: TokenVector) -> dict[str, int]:
    return {
        "input": reserved.input - actual.input,
        "output": reserved.output - actual.output,
        "total": reserved.total - actual.total,
    }


# ---------------------------------------------------------------------------
# Checks and records
# ---------------------------------------------------------------------------


def _status_check_action(slot: StatusCheckSlot, tables: dict[str, str]) -> dict[str, Any]:
    """Assert a status inside the transaction rather than reading it before.

    A prior read leaves a window in which a pause can land and one more request
    still gets through -- and a runaway agent is precisely the workload that
    will find that window.
    """
    names = {"#pk": A.PK, "#status": A.STATUS}
    values: dict[str, Any] = {":status": s(slot.expected_status)}
    condition = "attribute_exists(#pk) AND #status = :status"

    if slot.not_expired_after_epoch is not None:
        names["#expires"] = A.EXPIRES_AT_EPOCH
        values[":now"] = n(slot.not_expired_after_epoch)
        condition += " AND #expires > :now"

    return {
        "ConditionCheck": {
            "TableName": tables[slot.key.table],
            "Key": _key(slot),
            "ConditionExpression": condition,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": values,
            "ReturnValuesOnConditionCheckFailure": ALL_OLD,
        }
    }


def _idempotency_action(slot: IdempotencyPutSlot, tables: dict[str, str]) -> dict[str, Any]:
    return {
        "Put": {
            "TableName": tables[slot.key.table],
            "Item": {
                A.PK: s(slot.key.partition_suffix),
                A.SK: s(slot.key.sort_key),
                A.ENTITY_TYPE: s(A.E_IDEMPOTENCY),
                "reservation_id": s(slot.reservation_id),
                "request_fingerprint": s(slot.request_fingerprint or ""),
                A.STATE: s("RESERVED"),
                # TTL is fine here: this record is a duplicate-suppression aid,
                # never an authorization input.
                "ttl_epoch": n(slot.ttl_epoch),
            },
            "ConditionExpression": "attribute_not_exists(#pk)",
            "ExpressionAttributeNames": {"#pk": A.PK},
            "ReturnValuesOnConditionCheckFailure": ALL_OLD,
        }
    }


def _reservation_put_action(slot: ReservationPutSlot, tables: dict[str, str]) -> dict[str, Any]:
    from .serde import reservation_to_item

    return {
        "Put": {
            "TableName": tables[slot.key.table],
            "Item": reservation_to_item(slot.reservation, slot.key),
            "ConditionExpression": "attribute_not_exists(#pk)",
            "ExpressionAttributeNames": {"#pk": A.PK},
            "ReturnValuesOnConditionCheckFailure": ALL_OLD,
        }
    }


def _reservation_update_action(
    slot: ReservationUpdateSlot, tables: dict[str, str]
) -> dict[str, Any]:
    """Transition a reservation, conditional on its current state.

    The condition is what makes settlement idempotent: a duplicate reconcile
    finds the reservation already RECONCILED, fails its condition, and cancels
    the whole transaction rather than counting the spend twice.
    """
    names = {"#state": A.STATE, "#updated": A.UPDATED_AT}
    values: dict[str, Any] = {
        ":expected": s(slot.expected_state),
        ":new": s(slot.new_state),
        ":at": n(int(slot.at.timestamp())),
    }
    sets = ["#state = :new", "#updated = :at"]

    if slot.actual_cost is not None:
        names["#actual_nano"] = "actual_nano"
        values[":actual_nano"] = n(slot.actual_cost.nano)
        sets.append("#actual_nano = :actual_nano")
    if slot.actual_tokens is not None:
        names["#actual_in"] = "actual_input_tokens"
        names["#actual_out"] = "actual_output_tokens"
        values[":actual_in"] = n(slot.actual_tokens.input)
        values[":actual_out"] = n(slot.actual_tokens.output)
        sets += ["#actual_in = :actual_in", "#actual_out = :actual_out"]
    if slot.provider_request_id:
        names["#prid"] = "provider_request_id"
        values[":prid"] = s(slot.provider_request_id)
        sets.append("#prid = :prid")
    if slot.release_reason is not None:
        names["#release_reason"] = "release_reason"
        values[":release_reason"] = s(slot.release_reason.value)
        sets.append("#release_reason = :release_reason")
    if slot.pending_reason is not None:
        names["#pending_reason"] = "pending_reason"
        values[":pending_reason"] = s(slot.pending_reason.value)
        sets.append("#pending_reason = :pending_reason")

    return {
        "Update": {
            "TableName": tables[slot.key.table],
            "Key": _key(slot),
            "ConditionExpression": "#state = :expected",
            "UpdateExpression": "SET " + ", ".join(sets),
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": values,
            "ReturnValuesOnConditionCheckFailure": ALL_OLD,
        }
    }


def _ledger_put_action(slot: LedgerPutSlot, tables: dict[str, str]) -> dict[str, Any]:
    from .serde import ledger_entry_to_item

    return {
        "Put": {
            "TableName": tables[slot.key.table],
            "Item": ledger_entry_to_item(slot.entry, slot.key),
            "ConditionExpression": "attribute_not_exists(#pk)",
            "ExpressionAttributeNames": {"#pk": A.PK},
        }
    }


# ---------------------------------------------------------------------------
# Follow-up transactions
# ---------------------------------------------------------------------------


def threshold_flip_action(
    table: str,
    pk: str,
    sk: str,
    *,
    attribute: str,
    remaining_floor: Money,
    at_epoch: int,
) -> dict[str, Any]:
    """Flip a threshold flag, exactly once.

    The condition does two things at once: it refuses if the flag is already set
    (so only one caller can ever win), and it re-checks the crossing against the
    stored ``remaining`` (so a stale caller cannot flip it early). Between them,
    N concurrent reconciliations that all observe the crossing produce exactly
    one warning.
    """
    return {
        "Update": {
            "TableName": table,
            "Key": {A.PK: s(pk), A.SK: s(sk)},
            "ConditionExpression": "#flag = :false AND #rem <= :floor",
            "UpdateExpression": "SET #flag = :true, #at = :at",
            "ExpressionAttributeNames": {
                "#flag": attribute,
                "#rem": A.REMAINING_NANO,
                "#at": f"{attribute}_at_epoch",
            },
            "ExpressionAttributeValues": {
                ":false": b(False),
                ":true": b(True),
                ":floor": n(remaining_floor.nano),
                ":at": n(at_epoch),
            },
            "ReturnValuesOnConditionCheckFailure": ALL_OLD,
        }
    }


def session_close_action(
    table: str,
    pk: str,
    sk: str,
    *,
    new_status: str,
    reason: str,
    closed_by: str,
    at_epoch: int,
) -> dict[str, Any]:
    """Close an OPEN session. Conditional, so concurrent closers agree."""
    return {
        "Update": {
            "TableName": table,
            "Key": {A.PK: s(pk), A.SK: s(sk)},
            "ConditionExpression": "#status = :open",
            "UpdateExpression": (
                "SET #status = :new, #reason = :reason, #closed_by = :closed_by, #closed_at = :at"
            ),
            "ExpressionAttributeNames": {
                "#status": A.STATUS,
                "#reason": "close_reason",
                "#closed_by": "closed_by",
                "#closed_at": "closed_at_epoch",
            },
            "ExpressionAttributeValues": {
                ":open": s("OPEN"),
                ":new": s(new_status),
                ":reason": s(reason),
                ":closed_by": s(closed_by),
                ":at": n(at_epoch),
            },
            "ReturnValuesOnConditionCheckFailure": ALL_OLD,
        }
    }


def alert_put_action(table: str, pk: str, sk: str, item: dict[str, Any]) -> dict[str, Any]:
    """Write an alert, conditional on its derived key being free.

    Because the key is derived from scope, window and kind rather than being
    random, this conditional put is itself an exactly-once guarantee --
    independent of, and redundant with, the threshold flag.
    """
    return {
        "Put": {
            "TableName": table,
            "Item": {A.PK: s(pk), A.SK: s(sk), **item},
            "ConditionExpression": "attribute_not_exists(#pk)",
            "ExpressionAttributeNames": {"#pk": A.PK},
        }
    }

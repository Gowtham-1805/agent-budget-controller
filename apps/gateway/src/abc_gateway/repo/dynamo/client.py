"""DynamoDB client construction and cancellation decoding.

Two decisions here are load-bearing.

**botocore's own retries are turned off.** boto3 will happily retry a
``TransactWriteItems`` for you, and that is exactly wrong for this workload:
a retry issued below our layer does not carry our reservation's idempotency
discipline, so a "failed" call that actually succeeded can be applied twice. The
engine owns retry semantics, and it can only do that if nothing beneath it
retries silently.

**Cancellation reasons are positional.** DynamoDB reports a cancelled
transaction as a list aligned index-for-index with the actions submitted, with
``Code: "None"`` for the slots that passed. Mapping reason N back to slot N is
the only way to answer "which scope rejected this request?" -- and it is why
slot order in a plan must never be rearranged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ..plans import SlotFailure, TransactionPlan

CANCELLED = "TransactionCanceledException"

#: Reasons that mean "nothing was decided; try again".
RETRYABLE_REASONS = frozenset(
    {
        "TransactionConflict",
        "ThrottlingError",
        "ProvisionedThroughputExceeded",
        "RequestLimitExceeded",
    }
)

#: Reasons that mean the plan itself was malformed. Never a user-facing denial
#: -- these page an operator, because the gateway generated something invalid.
PLAN_BUG_REASONS = frozenset({"ValidationError", "DuplicateItem"})


def make_client(
    *,
    region: str,
    endpoint_url: str | None = None,
    max_pool_connections: int = 50,
) -> Any:
    """Build a DynamoDB client with our retry discipline.

    ``total_max_attempts=1`` is deliberate -- see the module docstring.
    """
    config = Config(
        region_name=region,
        retries={"mode": "standard", "total_max_attempts": 1},
        max_pool_connections=max_pool_connections,
        connect_timeout=3,
        read_timeout=10,
    )
    return boto3.client("dynamodb", endpoint_url=endpoint_url, config=config)


@dataclass(frozen=True, slots=True)
class Denied:
    """At least one condition genuinely evaluated false. Terminal."""

    failures: list[SlotFailure]


@dataclass(frozen=True, slots=True)
class Retryable:
    """The transaction lost a race. Nothing was decided."""

    reasons: tuple[str, ...]


class PlanBug(Exception):
    """The submitted transaction was structurally invalid."""


def decode_cancellation(error: ClientError, plan: TransactionPlan) -> Denied | Retryable:
    """Interpret a TransactionCanceledException against the plan that caused it.

    Note the precedence: if *any* slot reports ``ConditionalCheckFailed``, the
    result is a denial even when other slots merely conflicted. A condition that
    evaluated false was genuinely false, and retrying past it would be a way of
    eventually authorising a request the budget already refused. Fail closed.
    """
    if error.response.get("Error", {}).get("Code") != CANCELLED:
        raise error

    reasons = error.response.get("CancellationReasons", [])
    failures: list[SlotFailure] = []
    transient: list[str] = []

    for index, reason in enumerate(reasons):
        code = reason.get("Code")
        if code in (None, "None"):
            continue
        if code in PLAN_BUG_REASONS:
            raise PlanBug(f"slot {index} rejected as {code}: {reason.get('Message')}")
        if code in RETRYABLE_REASONS:
            transient.append(code)
            continue
        if code == "ConditionalCheckFailed":
            from .serde import plain

            raw_item = reason.get("Item")
            failures.append(
                SlotFailure(
                    index=index,
                    slot=plan.slot_at(index),
                    code=code,
                    # The pre-image is what turns "a condition failed" into
                    # "you needed $0.04 and had $0.01".
                    item=plain(dict(raw_item)) if raw_item else None,
                )
            )
            continue
        raise PlanBug(f"slot {index} cancelled with unrecognised code {code!r}")

    if failures:
        return Denied(failures)
    if transient:
        return Retryable(tuple(transient))
    raise PlanBug("transaction cancelled with no interpretable reason")


def is_conditional_check_failure(error: ClientError) -> bool:
    """True for a single-item conditional write that was refused."""
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def table_names(core: str, ledger: str) -> dict[str, str]:
    """Map logical table names used in plans to physical table names."""
    from .. import keys

    return {keys.TABLE_CORE: core, keys.TABLE_LEDGER: ledger}


def item_or_none(response: dict[str, Any]) -> dict[str, Any] | None:
    return response.get("Item")

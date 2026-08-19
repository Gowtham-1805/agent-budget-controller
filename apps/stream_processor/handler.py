"""DynamoDB Streams consumer.

Two responsibilities, both of which exist because the gateway cannot be trusted
to complete follow-up work after it has already answered a request:

* **Runaway detection.** Rolling 60-minute spend per agent, tripping the circuit
  breaker at 20% of the monthly budget.
* **Threshold backstop.** The gateway attempts the 80% flip inline for fast
  alerting, but if it dies between reconciling and warning, nobody is told. The
  stream sees the counter change regardless, so the guarantee survives.

Everything here must be idempotent. Lambda consumers of DynamoDB Streams get
**at-least-once** delivery, so a record can arrive more than once -- and
double-counting spend could pause a perfectly healthy agent, which is a
production incident manufactured by the monitoring.

Batch item failures are reported individually via ``batchItemFailures`` so one
poison record does not force the whole batch to be retried forever.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from abc_gateway.domain.agent import RunawayPolicy
from abc_gateway.domain.alerts import AlertEvent, AlertKind, threshold_floor
from abc_gateway.domain.money import Money
from abc_gateway.domain.scopes import ScopeRef, ScopeType
from abc_gateway.domain.window import BudgetWindow, WindowType
from abc_gateway.observability.logging import configure_logging, get_logger
from abc_gateway.repo import attributes as A
from abc_gateway.repo.dynamo import DynamoBudgetRepository
from abc_gateway.repo.dynamo.serde import ledger_entry_from_item, plain
from abc_gateway.runaway.detector import RunawayDetector

configure_logging(os.getenv("ABC_LOG_LEVEL", "INFO"))
logger = get_logger("abc_gateway.stream")

_repository: DynamoBudgetRepository | None = None


def repository() -> DynamoBudgetRepository:
    """Reused across invocations: Lambda keeps warm containers alive."""
    global _repository
    if _repository is None:
        _repository = DynamoBudgetRepository(
            region=os.environ.get("ABC_AWS_REGION", "us-east-1"),
            core_table=os.environ.get("ABC_TABLE_CORE", "abc_core"),
            ledger_table=os.environ.get("ABC_TABLE_LEDGER", "abc_ledger"),
        )
    return _repository


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Lambda entry point."""
    return asyncio.run(process(event))


async def process(event: dict[str, Any]) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    repo = repository()
    detector = RunawayDetector(repo)

    for record in event.get("Records", []):
        sequence = record.get("dynamodb", {}).get("SequenceNumber", "")
        try:
            await _handle_record(record, repo, detector)
        except Exception as exc:
            logger.error("stream.record_failed", error=str(exc), sequence_number=sequence)
            # Reported individually so one bad record does not block the batch.
            failures.append({"itemIdentifier": sequence})

    return {"batchItemFailures": failures}


async def _handle_record(
    record: dict[str, Any], repo: DynamoBudgetRepository, detector: RunawayDetector
) -> None:
    if record.get("eventName") == "REMOVE":
        # A TTL deletion. Never financially meaningful -- TTL is housekeeping.
        return

    image = record.get("dynamodb", {}).get("NewImage")
    if not image:
        return

    entity_type = image.get(A.ENTITY_TYPE, {}).get("S")
    if entity_type == A.E_LEDGER_ENTRY:
        await _handle_ledger_entry(image, repo, detector)
    elif entity_type == A.E_BUDGET_STATE:
        await _handle_budget_state(image, repo)


async def _handle_ledger_entry(
    image: dict[str, Any], repo: DynamoBudgetRepository, detector: RunawayDetector
) -> None:
    """Feed a financial event to the runaway detector."""
    entry = ledger_entry_from_item(image)

    policy = await repo.get_agent_policy(entry.tenant_id, entry.agent_id)
    monthly_limit, runaway_policy = _limits_for(policy)
    if monthly_limit is None:
        # Without a monthly limit there is no percentage to exceed.
        return

    result = await detector.observe(
        entry,
        monthly_limit=monthly_limit,
        now=datetime.now(UTC),
        policy=runaway_policy,
    )

    if result.duplicate:
        # Expected under at-least-once delivery, and the reason the detector
        # claims each entry before counting it.
        logger.debug("stream.duplicate_entry", entry_id=entry.entry_id)
        return

    if result.paused:
        logger.warning(
            "runaway.agent_paused",
            agent_id=entry.agent_id,
            tenant_id=entry.tenant_id,
            rolling_spend_usd=result.rolling_spend.to_usd_str(),
            threshold_usd=result.threshold.to_usd_str(),
            triggering_entry_id=entry.entry_id,
            review_required=True,
        )


async def _handle_budget_state(image: dict[str, Any], repo: DynamoBudgetRepository) -> None:
    """Backstop the threshold warning.

    The gateway tries this inline right after reconciling, which is fast. This
    path is what makes it *durable*: if the gateway crashed in between, the
    counter change still reaches the stream and the warning still fires.

    Both paths run the same conditional flip, so whichever gets there first
    wins and the other is a harmless no-op.
    """
    flat = plain(image)
    if flat.get(A.WARNING_80_SENT):
        return

    limit = Money(flat.get(A.LIMIT_NANO, 0))
    remaining = Money(flat.get(A.REMAINING_NANO, 0))
    if limit.nano <= 0:
        return

    warning_percent = 80
    floor = threshold_floor(limit, warning_percent)
    if remaining > floor:
        return  # threshold not reached

    scope_type = flat.get(A.SCOPE_TYPE)
    scope_id = flat.get(A.SCOPE_ID)
    tenant_id = flat.get(A.TENANT_ID)
    if not (scope_type and scope_id and tenant_id):
        return

    scope = ScopeRef(ScopeType(scope_type), scope_id)
    window = BudgetWindow(
        type=WindowType(flat[A.WINDOW_TYPE]),
        id=flat[A.WINDOW_ID],
        start_epoch=flat.get(A.WINDOW_START_EPOCH, 0),
        end_epoch=flat.get(A.WINDOW_END_EPOCH, 0),
    )

    flipped = await repo.try_flip_threshold(
        scope,
        window,
        threshold_percent=warning_percent,
        remaining_floor=floor,
        alert=AlertEvent(
            kind=AlertKind.WARN_80,
            scope=scope,
            window=window,
            occurred_at=datetime.now(UTC),
            tenant_id=tenant_id,
            threshold_percent=warning_percent,
            limit=limit,
            spent=Money(flat.get(A.COMMITTED_NANO, 0)),
        ),
    )
    if flipped:
        logger.warning(
            "budget.threshold_reached",
            scope=scope.key(),
            window=window.sort_key(),
            threshold_percent=warning_percent,
            limit_usd=limit.to_usd_str(),
            source="stream_backstop",
        )


def _limits_for(policy) -> tuple[Money | None, RunawayPolicy]:
    if policy is None:
        return (None, RunawayPolicy())
    return (policy.budget.limit, policy.runaway)

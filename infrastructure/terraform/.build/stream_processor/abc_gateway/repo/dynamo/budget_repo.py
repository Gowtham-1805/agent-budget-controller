"""DynamoDB budget repository.

Compiles backend-agnostic plans into ``TransactWriteItems`` and executes them.
Implements the same protocol as the in-memory backend, and is validated by the
same contract suite -- which is how we know a behaviour proven in memory holds
against the real store.

Calls are wrapped in ``asyncio.to_thread`` because boto3 is synchronous. That is
a deliberate simplification: the authorization path is one round trip, and a
thread hop costs far less than the complexity of a second SDK.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from botocore.exceptions import ClientError

from ...domain.agent import AgentState, AgentStatus
from ...domain.alerts import AlertEvent, threshold_floor
from ...domain.ledger import UsageLedgerEntry
from ...domain.money import Money
from ...domain.scopes import ScopeRef
from ...domain.session import Session, SessionCloseReason, SessionStatus
from ...domain.state import BudgetState
from ...domain.window import BudgetWindow
from .. import attributes as A
from .. import keys
from ..items import budget_state_from_item
from ..plans import PlanValidationError, TransactionConflict, TransactionDenied, TransactionPlan
from . import expressions as ex
from .client import (
    Denied,
    PlanBug,
    Retryable,
    decode_cancellation,
    error_code,
    is_conditional_check_failure,
    make_client,
    table_names,
)
from .serde import (
    ledger_entry_from_item,
    num,
    plain,
    reservation_from_item,
    txt,
)


class DynamoBudgetRepository:
    """Atomic budget state backed by DynamoDB."""

    def __init__(
        self,
        *,
        region: str,
        core_table: str,
        ledger_table: str,
        endpoint_url: str | None = None,
        client: Any = None,
    ) -> None:
        self.client = client or make_client(region=region, endpoint_url=endpoint_url)
        self.core_table = core_table
        self.ledger_table = ledger_table
        self.tables = table_names(core_table, ledger_table)

    # -- the single write path ---------------------------------------------

    async def apply(self, plan: TransactionPlan) -> None:
        """Apply every slot atomically, or none of them."""
        try:
            plan.validate()
        except PlanValidationError as exc:
            raise PlanBug(str(exc)) from exc

        actions = [ex.build_action(slot, self.tables) for slot in plan.slots]

        try:
            await asyncio.to_thread(
                self.client.transact_write_items,
                TransactItems=actions,
                # Useful inside its short validity window, but the durable
                # guarantee is the idempotency item in the plan itself.
                ClientRequestToken=plan.client_request_token[:36],
            )
        except ClientError as error:
            code = error_code(error)
            if code == "TransactionCanceledException":
                outcome = decode_cancellation(error, plan)
                if isinstance(outcome, Denied):
                    raise TransactionDenied(outcome.failures) from error
                if isinstance(outcome, Retryable):
                    raise TransactionConflict(", ".join(outcome.reasons)) from error
            if code == "TransactionInProgressException":
                # Our own in-flight duplicate; safe to retry once it settles.
                raise TransactionConflict("transaction already in progress") from error
            raise

    # -- conditional one-shot transitions ----------------------------------

    async def try_flip_threshold(
        self,
        scope: ScopeRef,
        window: BudgetWindow,
        *,
        threshold_percent: int,
        remaining_floor: Money,
        alert: AlertEvent,
    ) -> bool:
        """Flip the threshold flag and write its alert, exactly once.

        Two independent guarantees, on purpose: the conditional flag flip means
        only one caller can win, and the alert's key is derived from scope,
        window and kind so a duplicate write collides. Either alone would
        probably do; both together mean a pager fires once even if one mechanism
        is subverted by a future change.
        """
        attribute = A.WARNING_80_SENT if threshold_percent < 100 else A.WARNING_100_SENT
        state_key = keys.budget_state_key(alert.tenant_id, scope, window)
        alert_key = keys.alert_key(alert.tenant_id, alert)

        actions = [
            ex.threshold_flip_action(
                self.core_table,
                state_key.partition_suffix,
                state_key.sort_key,
                attribute=attribute,
                remaining_floor=remaining_floor,
                at_epoch=int(alert.occurred_at.timestamp()),
            ),
            ex.alert_put_action(
                self.core_table,
                alert_key.partition_suffix,
                alert_key.sort_key,
                self._alert_item(alert),
            ),
        ]
        return await self._try_transaction(actions)

    async def try_close_session(
        self,
        tenant_id: str,
        session_id: str,
        *,
        status: SessionStatus,
        reason: SessionCloseReason,
        closed_by: str | None = None,
        alert: AlertEvent | None = None,
    ) -> bool:
        meta = keys.session_meta_key(tenant_id, session_id)
        actions = [
            ex.session_close_action(
                self.core_table,
                meta.partition_suffix,
                meta.sort_key,
                new_status=status.value,
                reason=reason.value,
                closed_by=closed_by or "system",
                at_epoch=int(datetime.now(UTC).timestamp()),
            )
        ]
        if alert is not None:
            alert_key = keys.alert_key(tenant_id, alert)
            actions.append(
                ex.alert_put_action(
                    self.core_table,
                    alert_key.partition_suffix,
                    alert_key.sort_key,
                    self._alert_item(alert),
                )
            )
        return await self._try_transaction(actions)

    async def _try_transaction(self, actions: list[dict[str, Any]]) -> bool:
        """Run a transaction whose failure is an expected outcome.

        Losing the race is normal for all but one of several concurrent
        callers, so a cancellation returns False rather than raising.
        """
        try:
            await asyncio.to_thread(self.client.transact_write_items, TransactItems=actions)
            return True
        except ClientError as error:
            if error_code(error) == "TransactionCanceledException":
                return False
            raise

    # -- reads --------------------------------------------------------------

    async def get_budget_state(
        self,
        scope: ScopeRef,
        window: BudgetWindow,
        *,
        consistent: bool = True,
        tenant_id: str = "default",
    ) -> BudgetState | None:
        key = keys.budget_state_key(tenant_id, scope, window)
        response = await asyncio.to_thread(
            self.client.get_item,
            TableName=self.core_table,
            Key={A.PK: txt(key.partition_suffix), A.SK: txt(key.sort_key)},
            # Strongly consistent by default: an eventually-consistent read of a
            # financial counter is misleading in exactly the moments that matter.
            ConsistentRead=consistent,
        )
        item = response.get("Item")
        if not item:
            return None
        return budget_state_from_item(plain(item), scope, window)

    async def get_reservation(self, tenant_id: str, reservation_id: str):
        key = keys.reservation_key(tenant_id, reservation_id)
        response = await asyncio.to_thread(
            self.client.get_item,
            TableName=self.core_table,
            Key={A.PK: txt(key.partition_suffix), A.SK: txt(key.sort_key)},
            ConsistentRead=True,
        )
        item = response.get("Item")
        return reservation_from_item(item) if item else None

    async def mark_dispatched(self, tenant_id: str, reservation_id: str) -> None:
        """Flag that the provider is about to be contacted.

        Unconditional and outside any transaction: it is on the request's hot
        path, and its only consumer -- the sweeper -- treats a missing flag
        conservatively anyway.
        """
        key = keys.reservation_key(tenant_id, reservation_id)
        await asyncio.to_thread(
            self.client.update_item,
            TableName=self.core_table,
            Key={A.PK: txt(key.partition_suffix), A.SK: txt(key.sort_key)},
            UpdateExpression="SET #d = :dispatched",
            ExpressionAttributeNames={"#d": A.DISPATCH_STATE},
            ExpressionAttributeValues={":dispatched": txt("DISPATCHED")},
        )

    async def get_idempotency_record(self, tenant_id: str, key_hash: str) -> dict[str, Any] | None:
        key = keys.idempotency_key(tenant_id, key_hash)
        response = await asyncio.to_thread(
            self.client.get_item,
            TableName=self.core_table,
            Key={A.PK: txt(key.partition_suffix), A.SK: txt(key.sort_key)},
            ConsistentRead=True,
        )
        item = response.get("Item")
        return plain(item) if item else None

    async def find_reservation_by_idempotency_key(self, tenant_id: str, key_hash: str):
        record = await self.get_idempotency_record(tenant_id, key_hash)
        if not record:
            return None
        return await self.get_reservation(tenant_id, record["reservation_id"])

    async def get_session(self, tenant_id: str, session_id: str) -> Session | None:
        key = keys.session_meta_key(tenant_id, session_id)
        response = await asyncio.to_thread(
            self.client.get_item,
            TableName=self.core_table,
            Key={A.PK: txt(key.partition_suffix), A.SK: txt(key.sort_key)},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        flat = plain(item)
        return Session(
            session_id=session_id,
            tenant_id=tenant_id,
            team_id=flat.get("team_id", ""),
            agent_id=flat.get("agent_id", ""),
            status=SessionStatus(flat[A.STATUS]),
            opened_at=datetime.fromtimestamp(flat.get("opened_at_epoch", 0), tz=UTC),
            expires_at=datetime.fromtimestamp(flat[A.EXPIRES_AT_EPOCH], tz=UTC),
            limit=Money(flat.get("limit_nano", 0)),
            min_viable=Money(flat.get("min_viable_nano", 0)),
            close_reason=(
                SessionCloseReason(flat["close_reason"]) if flat.get("close_reason") else None
            ),
            closed_by=flat.get("closed_by"),
        )

    async def put_session(self, session: Session) -> None:
        key = keys.session_meta_key(session.tenant_id, session.session_id)
        await asyncio.to_thread(
            self.client.put_item,
            TableName=self.core_table,
            Item={
                A.PK: txt(key.partition_suffix),
                A.SK: txt(key.sort_key),
                A.ENTITY_TYPE: txt(A.E_SESSION),
                A.STATUS: txt(session.status.value),
                A.EXPIRES_AT_EPOCH: num(int(session.expires_at.timestamp())),
                "opened_at_epoch": num(int(session.opened_at.timestamp())),
                "agent_id": txt(session.agent_id),
                "team_id": txt(session.team_id),
                "limit_nano": num(session.limit.nano),
                "min_viable_nano": num(session.min_viable.nano),
                A.GSI1PK: txt(f"TNT#{session.tenant_id}#AGENT#{session.agent_id}#SESSIONS"),
                A.GSI1SK: txt(
                    f"STATUS#{session.status.value}#{int(session.opened_at.timestamp()):020d}"
                ),
            },
        )

    async def get_agent_state(self, tenant_id: str, agent_id: str) -> AgentState | None:
        key = keys.agent_state_key(tenant_id, agent_id)
        response = await asyncio.to_thread(
            self.client.get_item,
            TableName=self.core_table,
            Key={A.PK: txt(key.partition_suffix), A.SK: txt(key.sort_key)},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        flat = plain(item)
        return AgentState(
            agent_id=agent_id,
            tenant_id=tenant_id,
            team_id=flat.get("team_id", ""),
            status=AgentStatus(flat[A.STATUS]),
            review_required=bool(flat.get("review_required", False)),
            pause_reason=flat.get("pause_reason") or None,
            pause_source_event_id=flat.get("pause_source_event_id") or None,
        )

    async def put_agent_state(self, state: AgentState) -> None:
        key = keys.agent_state_key(state.tenant_id, state.agent_id)
        await asyncio.to_thread(
            self.client.put_item,
            TableName=self.core_table,
            Item={
                A.PK: txt(key.partition_suffix),
                A.SK: txt(key.sort_key),
                A.ENTITY_TYPE: txt(A.E_AGENT_STATE),
                A.STATUS: txt(state.status.value),
                "team_id": txt(state.team_id),
                "review_required": {"BOOL": state.review_required},
                "pause_reason": txt(state.pause_reason or ""),
                "pause_source_event_id": txt(state.pause_source_event_id or ""),
            },
        )

    async def set_agent_status(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        expected_status: str | None,
        new_status: str,
        reason: str,
        actor: str,
        source_event_id: str | None = None,
    ) -> bool:
        """Transition an agent conditionally.

        ``expected_status`` is what makes an automatic pause idempotent under
        at-least-once stream delivery: a redelivery finds the agent already
        paused, the condition fails, and no duplicate pause is emitted.
        """
        key = keys.agent_state_key(tenant_id, agent_id)
        names = {"#s": A.STATUS, "#r": "pause_reason", "#a": "actor", "#rr": "review_required"}
        values: dict[str, Any] = {
            ":new": txt(new_status),
            ":reason": txt(reason),
            ":actor": txt(actor),
            ":rr": {"BOOL": new_status.startswith("PAUSED")},
        }
        sets = ["#s = :new", "#r = :reason", "#a = :actor", "#rr = :rr"]
        condition = "attribute_exists(#s) AND #s <> :new"

        if expected_status is not None:
            values[":expected"] = txt(expected_status)
            condition = "#s = :expected"

        if source_event_id:
            names["#src"] = "pause_source_event_id"
            values[":src"] = txt(source_event_id)
            sets.append("#src = :src")

        try:
            await asyncio.to_thread(
                self.client.update_item,
                TableName=self.core_table,
                Key={A.PK: txt(key.partition_suffix), A.SK: txt(key.sort_key)},
                ConditionExpression=condition,
                UpdateExpression="SET " + ", ".join(sets),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as error:
            if is_conditional_check_failure(error):
                return False
            raise

    # -- rolling spend ------------------------------------------------------

    async def claim_rolling_entry(self, tenant_id: str, entry_id: str, *, ttl_epoch: int) -> bool:
        """Claim a ledger entry for counting, exactly once."""
        key = keys.rolling_mark_key(tenant_id, entry_id)
        try:
            await asyncio.to_thread(
                self.client.put_item,
                TableName=self.core_table,
                Item={
                    A.PK: txt(key.partition_suffix),
                    A.SK: txt(key.sort_key),
                    A.ENTITY_TYPE: txt(A.E_ROLLING_MARK),
                    "entry_id": txt(entry_id),
                    "ttl_epoch": num(ttl_epoch),
                },
                ConditionExpression="attribute_not_exists(#pk)",
                ExpressionAttributeNames={"#pk": A.PK},
            )
            return True
        except ClientError as error:
            if is_conditional_check_failure(error):
                return False
            raise

    async def add_rolling_spend(
        self, tenant_id: str, agent_id: str, *, bucket: str, amount: Money, ttl_epoch: int
    ) -> None:
        key = keys.rolling_bucket_key(tenant_id, agent_id, bucket)
        await asyncio.to_thread(
            self.client.update_item,
            TableName=self.core_table,
            Key={A.PK: txt(key.partition_suffix), A.SK: txt(key.sort_key)},
            UpdateExpression="ADD #spend :amount, #count :one SET #ttl = :ttl, #e = :entity",
            ExpressionAttributeNames={
                "#spend": "spend_nano",
                "#count": "event_count",
                "#ttl": "ttl_epoch",
                "#e": A.ENTITY_TYPE,
            },
            ExpressionAttributeValues={
                ":amount": num(amount.nano),
                ":one": num(1),
                ":ttl": num(ttl_epoch),
                ":entity": txt(A.E_ROLLING_BUCKET),
            },
        )

    async def sum_rolling_spend(
        self, tenant_id: str, agent_id: str, *, buckets: list[str]
    ) -> Money:
        """Sum the rolling window.

        BatchGetItem in chunks of 100; a 60-minute window is a single batch.
        """
        total = 0
        for chunk_start in range(0, len(buckets), 100):
            chunk = buckets[chunk_start : chunk_start + 100]
            request_keys = [
                {
                    A.PK: txt(keys.rolling_bucket_key(tenant_id, agent_id, b).partition_suffix),
                    A.SK: txt(keys.rolling_bucket_key(tenant_id, agent_id, b).sort_key),
                }
                for b in chunk
            ]
            response = await asyncio.to_thread(
                self.client.batch_get_item,
                RequestItems={self.core_table: {"Keys": request_keys, "ConsistentRead": True}},
            )
            for item in response.get("Responses", {}).get(self.core_table, []):
                total += plain(item).get("spend_nano", 0)
        return Money(total)

    async def record_runaway_event(self, event, alert: AlertEvent | None = None) -> None:
        if event is not None:
            await asyncio.to_thread(
                self.client.put_item,
                TableName=self.ledger_table,
                Item={
                    A.PK: txt(f"TNT#{event.tenant_id}#RUNAWAY#{event.agent_id}"),
                    A.SK: txt(f"{int(event.detected_at.timestamp()):020d}#{event.event_id}"),
                    A.ENTITY_TYPE: txt("RUNAWAY_EVENT"),
                    "event_id": txt(event.event_id),
                    "agent_id": txt(event.agent_id),
                    A.TENANT_ID: txt(event.tenant_id),
                    "rolling_spend_nano": num(event.rolling_spend.nano),
                    "threshold_nano": num(event.threshold.nano),
                    "monthly_limit_nano": num(event.monthly_limit.nano),
                    "interval_minutes": num(event.interval_minutes),
                    "detected_at_epoch": num(int(event.detected_at.timestamp())),
                    "triggering_entry_id": txt(event.triggering_entry_id or ""),
                },
            )
        if alert is not None:
            await self.record_alert(alert)

    async def record_alert(self, alert: AlertEvent) -> None:
        key = keys.alert_key(alert.tenant_id, alert)
        try:
            await asyncio.to_thread(
                self.client.put_item,
                TableName=self.core_table,
                Item={
                    A.PK: txt(key.partition_suffix),
                    A.SK: txt(key.sort_key),
                    **self._alert_item(alert),
                },
                ConditionExpression="attribute_not_exists(#pk)",
                ExpressionAttributeNames={"#pk": A.PK},
            )
        except ClientError as error:
            if not is_conditional_check_failure(error):
                raise

    async def record_audit_event(self, event: dict) -> None:
        """Append an administrative audit record. Never updated or deleted."""
        timestamp = event.get("timestamp", datetime.now(UTC).isoformat())
        day = timestamp[:10]
        await asyncio.to_thread(
            self.client.put_item,
            TableName=self.ledger_table,
            Item={
                A.PK: txt(f"TNT#{event['tenant_id']}#AUDIT#{day}"),
                A.SK: txt(f"{timestamp}#{event['action']}#{event['target']}"),
                A.ENTITY_TYPE: txt(A.E_AUDIT_EVENT),
                **{k: txt(str(v)) for k, v in event.items() if v is not None},
            },
        )

    async def get_runaway_events(self, tenant_id: str, agent_id: str) -> tuple:
        response = await asyncio.to_thread(
            self.client.query,
            TableName=self.ledger_table,
            KeyConditionExpression="#pk = :pk",
            ExpressionAttributeNames={"#pk": A.PK},
            ExpressionAttributeValues={":pk": txt(f"TNT#{tenant_id}#RUNAWAY#{agent_id}")},
            ScanIndexForward=False,
        )
        return tuple(plain(i) for i in response.get("Items", []))

    async def get_audit_events(self, tenant_id: str) -> tuple[dict, ...]:
        response = await asyncio.to_thread(
            self.client.query,
            TableName=self.ledger_table,
            KeyConditionExpression="begins_with(#pk, :prefix)",
            ExpressionAttributeNames={"#pk": A.PK},
            ExpressionAttributeValues={":prefix": txt(f"TNT#{tenant_id}#AUDIT#")},
        )
        return tuple(plain(i) for i in response.get("Items", []))

    async def query_ledger(
        self,
        tenant_id: str,
        *,
        agent_id: str | None = None,
        since_epoch: int | None = None,
        until_epoch: int | None = None,
        limit: int = 100,
    ) -> tuple[UsageLedgerEntry, ...]:
        if agent_id is None:
            raise ValueError("agent_id is required to query the ledger")
        condition = "#pk = :pk"
        values: dict[str, Any] = {":pk": txt(f"TNT#{tenant_id}#AGENT#{agent_id}")}
        if since_epoch is not None:
            condition += " AND #sk >= :since"
            values[":since"] = txt(f"TS#{since_epoch:020d}")
        response = await asyncio.to_thread(
            self.client.query,
            TableName=self.ledger_table,
            IndexName="GSI1",
            KeyConditionExpression=condition,
            ExpressionAttributeNames={"#pk": A.GSI1PK, "#sk": A.GSI1SK},
            ExpressionAttributeValues=values,
            Limit=limit,
            ScanIndexForward=False,
        )
        return tuple(ledger_entry_from_item(i) for i in response.get("Items", []))

    async def health_check(self) -> bool:
        """Cheap reachability probe for /readyz. Never a billable call."""
        try:
            await asyncio.to_thread(self.client.describe_table, TableName=self.core_table)
            return True
        except ClientError:
            return False

    # -- helpers ------------------------------------------------------------

    def _alert_item(self, alert: AlertEvent) -> dict[str, Any]:
        item: dict[str, Any] = {
            A.ENTITY_TYPE: txt(A.E_ALERT_EVENT),
            "alert_event_id": txt(alert.event_id),
            "kind": txt(alert.kind.value),
            "scope_key": txt(alert.scope.key()),
            A.TENANT_ID: txt(alert.tenant_id),
            "occurred_at_epoch": num(int(alert.occurred_at.timestamp())),
            # Drained by the stream consumer, which flips it to true. An outbox
            # rather than a direct publish, so a notification cannot be lost
            # between the state change and the send.
            "published": {"BOOL": False},
        }
        if alert.threshold_percent is not None:
            item["threshold_percent"] = num(alert.threshold_percent)
        if alert.limit is not None:
            item["limit_nano"] = num(alert.limit.nano)
        if alert.spent is not None:
            item["spent_nano"] = num(alert.spent.nano)
        if alert.triggering_reservation_id:
            item["triggering_reservation_id"] = txt(alert.triggering_reservation_id)
        for key, value in alert.detail.items():
            item[f"detail_{key}"] = txt(str(value))
        return item


__all__ = ["DynamoBudgetRepository", "threshold_floor"]

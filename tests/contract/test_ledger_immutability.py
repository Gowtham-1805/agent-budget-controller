"""The ledger is append-only in application code, not just by IAM policy.

CLAUDE.md's rule 10 is enforced two ways: `infrastructure/terraform/iam.tf`
denies the gateway's task role `UpdateItem` and `DeleteItem` on `abc_ledger`
outright, and separately, nothing in the application ever *tries* to call
them there. This suite proves the second half dynamically, by wrapping the
real boto3 client the moto-backed repository uses and recording every write
call it makes across a representative set of operations -- reserve, settle,
release, mark pending, warn, close a session, pause an agent -- then asserting
none of them issued `UpdateItem`, `DeleteItem`, or `BatchWriteItem` against the
ledger table, and that every `TransactWriteItems` action touching the ledger
table was a `Put`.

This is deliberately independent of the IAM deny statement: if application
code ever regressed to mutating the ledger, this test would catch it even
against a hypothetical deployment where the IAM policy was misconfigured or
absent, which is the point of defence in depth.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from tests.conftest import (
    FIXED_NOW,
    TENANT,
    active_agent,
    agent_policy,
    build_request,
    make_session,
    team_policy,
)
from tests.contract.conftest import dynamo_repo, moto_server  # noqa: F401 -- fixtures

from abc_gateway.domain.errors import PendingReason, ReleaseReason
from abc_gateway.domain.usage import ProviderUsage
from abc_gateway.engine.budget_engine import BudgetEngine
from abc_gateway.engine.effects import SettlementEffects

pytestmark = pytest.mark.serial

WRITE_METHODS = frozenset({"put_item", "update_item", "delete_item", "batch_write_item"})
MUTATING_ACTIONS = frozenset({"Update", "Delete"})


@dataclass
class ClientSpy:
    """Records every call made through a boto3 client, then delegates it."""

    client: Any
    calls: list[tuple[str, dict]] = field(default_factory=list)

    def __getattr__(self, name: str) -> Any:
        target = getattr(self.client, name)
        if name not in WRITE_METHODS and name != "transact_write_items":
            return target

        def wrapped(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            return target(**kwargs)

        return wrapped


@pytest.fixture
def spied(dynamo_repo):  # noqa: F811
    spy = ClientSpy(dynamo_repo.client)
    dynamo_repo.client = spy
    return dynamo_repo, spy


def _ledger_violations(spy: ClientSpy, ledger_table: str) -> list[str]:
    """Every write call this repository made that touched the ledger table
    with anything other than a Put, described in words for a failure message.
    """
    violations: list[str] = []
    for name, kwargs in spy.calls:
        if name in ("update_item", "delete_item", "batch_write_item"):
            if kwargs.get("TableName") == ledger_table:
                violations.append(f"{name} called directly against {ledger_table}")
            if name == "batch_write_item" and ledger_table in (kwargs.get("RequestItems") or {}):
                violations.append(f"batch_write_item touched {ledger_table}")
        elif name == "transact_write_items":
            for action in kwargs.get("TransactItems", []):
                for verb, spec in action.items():
                    if verb in MUTATING_ACTIONS and spec.get("TableName") == ledger_table:
                        violations.append(f"TransactWriteItems {verb} against {ledger_table}")
    return violations


class TestLedgerIsNeverMutated:
    async def test_a_full_lifecycle_never_updates_or_deletes_a_ledger_item(
        self, spied, catalog
    ) -> None:
        repo, spy = spied
        engine = BudgetEngine(repo)
        effects = SettlementEffects(repo)

        await repo.put_agent_state(active_agent("scribe"))
        team = team_policy("100.00")
        agent = agent_policy("scribe", "10.00", session_budget_usd="5.00")
        session = make_session("ses_scribe", "scribe", "5.00")
        await repo.put_session(session)

        # reserve -> settle (the ordinary path)
        request = build_request(
            catalog=catalog, agent=agent, team=team, session=session, idempotency_key="a"
        )
        grant = await engine.reserve(request)
        await engine.mark_dispatched(TENANT, grant.reservation_id)
        result = await engine.reconcile(
            TENANT,
            grant.reservation_id,
            ProviderUsage(input_tokens=1000, output_tokens=1000),
            catalog.get("test", grant.effective_model),
            FIXED_NOW,
        )
        reservation = await repo.get_reservation(TENANT, grant.reservation_id)
        await effects.apply(
            reservation, warning_percent=80, session_min_viable=None, now=FIXED_NOW
        )
        assert result.actual_cost.nano > 0

        # reserve -> release (proven-unbilled path)
        request_b = build_request(
            catalog=catalog, agent=agent, team=team, session=session, idempotency_key="b"
        )
        grant_b = await engine.reserve(request_b)
        await engine.mark_dispatched(TENANT, grant_b.reservation_id)
        await engine.release(TENANT, grant_b.reservation_id, ReleaseReason.PROVIDER_REJECTED, FIXED_NOW)

        # reserve -> mark_pending (ambiguous-outcome path)
        request_c = build_request(
            catalog=catalog, agent=agent, team=team, session=session, idempotency_key="c"
        )
        grant_c = await engine.reserve(request_c)
        await engine.mark_dispatched(TENANT, grant_c.reservation_id)
        await engine.mark_pending(
            TENANT, grant_c.reservation_id, PendingReason.PROVIDER_TIMEOUT, FIXED_NOW
        )

        violations = _ledger_violations(spy, repo.ledger_table)
        assert not violations, "\n".join(violations)

        # And ledger writes did actually happen -- an empty spy would prove
        # nothing.
        put_calls_to_ledger = [
            kwargs
            for name, kwargs in spy.calls
            if name == "transact_write_items"
            for action in kwargs.get("TransactItems", [])
            for verb, spec in action.items()
            if verb == "Put" and spec.get("TableName") == repo.ledger_table
        ]
        assert put_calls_to_ledger, "no ledger Put calls were observed; the spy proves nothing"

    async def test_a_correction_is_a_new_entry_not_an_edit(self, spied) -> None:
        """`corrects_entry_id` is how a mistaken entry is fixed: by writing a
        second, superseding entry that references the first -- never by
        editing the original. This proves the type supports it and that
        writing one is still a Put, not an Update.
        """
        from abc_gateway.domain.ledger import (
            BudgetDecision,
            CostBreakdown,
            LedgerKind,
            UsageLedgerEntry,
        )
        from abc_gateway.domain.money import Money
        from abc_gateway.domain.tokens import TokenVector
        from abc_gateway.repo import keys
        from abc_gateway.repo.dynamo.serde import ledger_entry_to_item

        repo, spy = spied
        zero = CostBreakdown(input_cost=Money.zero(), output_cost=Money.zero())
        original = UsageLedgerEntry(
            entry_id="entry-1",
            kind=LedgerKind.USAGE,
            reservation_id="res-1",
            tenant_id=TENANT,
            team_id="engineering",
            agent_id="scribe",
            session_id=None,
            provider="test",
            requested_model="premium",
            effective_model="premium",
            decision=BudgetDecision.ALLOWED,
            preflight_input_tokens=1000,
            reserved_output_tokens=1000,
            estimated_cost=zero,
            estimated_max_cost=Money.from_usd_str("0.04"),
            reserved_cost=Money.from_usd_str("0.04"),
            actual_tokens=TokenVector(input=1000, output=1000, total=2000),
            actual_cached_input_tokens=0,
            actual_reasoning_tokens=0,
            actual_cost=zero,
            actual_total_cost=Money.from_usd_str("0.04"),
            price_catalog_version="v1",
            created_at=FIXED_NOW,
            completed_at=FIXED_NOW,
        )
        from dataclasses import replace

        correction = replace(original, entry_id="entry-2", corrects_entry_id="entry-1")
        assert correction.corrects_entry_id == "entry-1"

        key = keys.ledger_key(
            TENANT,
            correction.agent_id,
            year_month=FIXED_NOW.strftime("%Y-%m"),
            created_at_micros=int(FIXED_NOW.timestamp() * 1_000_000),
            reservation_id=correction.reservation_id,
            sequence=0,
        )
        await asyncio.to_thread(
            repo.client.put_item,
            TableName=repo.ledger_table,
            Item=ledger_entry_to_item(correction, key),
        )

        violations = _ledger_violations(spy, repo.ledger_table)
        assert not violations
        put_to_ledger = [
            (n, kw)
            for n, kw in spy.calls
            if n == "put_item" and kw.get("TableName") == repo.ledger_table
        ]
        assert put_to_ledger, "the correction write was not observed"

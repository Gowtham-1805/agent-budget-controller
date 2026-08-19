"""The DynamoDB Streams consumer, driven by real stream records.

Every other suite proves the runaway detector and the threshold backstop as
pure logic (``tests/acceptance/test_runaway_detector.py``), one layer below
what AWS actually runs. Nothing in the suite before this one ever imported
``apps/stream_processor/handler.py`` or fed it a record shaped the way a real
DynamoDB Streams delivery is shaped.

This suite closes that gap by driving the real pipeline: governed calls run
through the ordinary engine against a moto-backed ``DynamoBudgetRepository``
(with `StreamSpecification` enabled on both tables, from the same
`infra/table_*.json` specs Terraform deploys), the resulting stream records are
pulled back out through moto's `dynamodbstreams` API exactly as Lambda would
receive them, and those *real* records are fed into ``handler.process()``. No
record shape is hand-constructed.

``handler.py`` is imported the way Lambda actually loads it -- as a bare
top-level module, not a package member -- by putting
``apps/stream_processor/`` on ``sys.path``, the same layout
``scripts/build_stream_processor_lambda.sh`` stages for deployment.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3
import pytest
from tests.conftest import TEAM, TENANT, active_agent, agent_policy, build_request, team_policy
from tests.contract.conftest import dynamo_repo, moto_server  # noqa: F401 -- fixtures
from tests.support.driver import Driver

from abc_gateway.domain.agent import AgentStatus
from abc_gateway.domain.scopes import ScopeRef
from abc_gateway.domain.window import BudgetWindow, WindowType
from abc_gateway.pricing import load_catalog
from abc_gateway.providers.base import ChatMessage, ChatRequest, Timeouts
from abc_gateway.providers.fake import FakeBehaviour, FakeProvider

STREAM_PROCESSOR_DIR = Path(__file__).resolve().parents[2] / "apps" / "stream_processor"
if str(STREAM_PROCESSOR_DIR) not in sys.path:
    sys.path.insert(0, str(STREAM_PROCESSOR_DIR))

import handler  # noqa: E402  -- imported the way the Lambda runtime loads it

pytestmark = pytest.mark.serial

AGENT = "runner"

#: `handler.py`'s runaway detection windows against `datetime.now(UTC)` at the
#: moment the Lambda runs, not against any timestamp carried on the ledger
#: entry -- that is real production behaviour, not a test seam. So unlike the
#: rest of the suite, which pins a fixed instant for reproducibility, these
#: tests must generate ledger entries close to the real wall clock or the
#: rolling 60-minute window the handler computes would simply not overlap
#: them.
NOW = datetime.now(UTC)

CATALOG_PATH = Path(__file__).resolve().parents[2] / "pricing" / "catalog.json"


def _drain_stream(streams_client, stream_arn: str, *, max_iterations: int = 20) -> list[dict]:
    """Pull every record currently on a stream, the way a Lambda poller would.

    Loops the shard iterator forward until it stops returning new records
    rather than assuming one `get_records` call drains everything -- moto, like
    real DynamoDB Streams, can return an empty page before the iterator is
    exhausted.
    """
    description = streams_client.describe_stream(StreamArn=stream_arn)["StreamDescription"]
    records: list[dict] = []
    for shard in description["Shards"]:
        iterator = streams_client.get_shard_iterator(
            StreamArn=stream_arn, ShardId=shard["ShardId"], ShardIteratorType="TRIM_HORIZON"
        )["ShardIterator"]
        empty_reads = 0
        for _ in range(max_iterations):
            if iterator is None:
                break
            response = streams_client.get_records(ShardIterator=iterator)
            batch = response.get("Records", [])
            records.extend(batch)
            iterator = response.get("NextShardIterator")
            empty_reads = empty_reads + 1 if not batch else 0
            if empty_reads >= 2:
                break
    return records


def _to_lambda_event(records: list[dict]) -> dict:
    """Wrap raw dynamodbstreams records the way the Lambda event source does."""
    return {"Records": records}


@pytest.fixture
def stack(dynamo_repo):  # noqa: F811
    """A governed stack wired to the moto-backed Dynamo repository."""
    provider = FakeProvider(
        FakeBehaviour(input_tokens=1000, output_tokens=1000), repository=dynamo_repo, tenant_id=TENANT
    )
    catalog = load_catalog(CATALOG_PATH)
    driver = Driver(repo=dynamo_repo, catalog=catalog, provider=provider, tenant_id=TENANT, team_id=TEAM)
    return dynamo_repo, driver, provider


@pytest.fixture(autouse=True)
def _use_stack_repository(monkeypatch, stack):
    """Point the module-level ``repository()`` singleton at our moto backend.

    ``handler.repository()`` builds a fresh client from environment variables
    on first call -- correct for a real Lambda cold start, wrong for a test
    that needs it pointed at moto. Patching the module-level cache is the same
    seam a test harness for any Lambda-style "warm singleton" would use.
    """
    repo, _driver, _provider = stack
    monkeypatch.setattr(handler, "_repository", repo)
    yield
    monkeypatch.setattr(handler, "_repository", None)


class TestRunawayDetectionFromRealStreamRecords:
    async def test_a_burst_of_real_ledger_records_pauses_the_agent(self, stack) -> None:
        """Item 26/27: >20% of the monthly budget inside 60 rolling minutes."""
        repo, driver, _provider = stack
        await repo.put_agent_state(active_agent(AGENT))
        team = team_policy("100.00")
        agent = agent_policy(AGENT, "1.00")  # threshold = 20% of $1.00 = $0.20
        await repo.put_agent_policy(agent)
        await repo.put_budget_policy(TENANT, team)

        # 6 calls at $0.04 = $0.24, strictly over the $0.20 threshold.
        results = await driver.spend(agent=agent, team=team, now=NOW, times=6)
        assert all(r.allowed for r in results)

        streams = boto3.client(
            "dynamodbstreams", region_name="us-east-1", endpoint_url=repo.client.meta.endpoint_url
        )
        ledger_stream_arn = repo.client.describe_table(TableName=repo.ledger_table)["Table"][
            "LatestStreamArn"
        ]
        records = _drain_stream(streams, ledger_stream_arn)
        assert len(records) == 6, f"expected 6 real ledger stream records, got {len(records)}"

        result = await handler.process(_to_lambda_event(records))
        assert result["batchItemFailures"] == []

        state = await repo.get_agent_state(TENANT, AGENT)
        assert state is not None
        assert state.status is AgentStatus.PAUSED_RUNAWAY
        assert state.review_required is True

    async def test_a_burst_below_the_threshold_does_not_pause(self, stack) -> None:
        repo, driver, _provider = stack
        await repo.put_agent_state(active_agent(AGENT))
        team = team_policy("100.00")
        agent = agent_policy(AGENT, "1.00")
        await repo.put_agent_policy(agent)
        await repo.put_budget_policy(TENANT, team)

        # 5 calls at $0.04 = $0.20 exactly -- the threshold is "strictly more
        # than 20%", so this must not trip.
        results = await driver.spend(agent=agent, team=team, now=NOW, times=5)
        assert all(r.allowed for r in results)

        streams = boto3.client(
            "dynamodbstreams", region_name="us-east-1", endpoint_url=repo.client.meta.endpoint_url
        )
        ledger_stream_arn = repo.client.describe_table(TableName=repo.ledger_table)["Table"][
            "LatestStreamArn"
        ]
        records = _drain_stream(streams, ledger_stream_arn)

        result = await handler.process(_to_lambda_event(records))
        assert result["batchItemFailures"] == []

        state = await repo.get_agent_state(TENANT, AGENT)
        assert state.status is AgentStatus.ACTIVE

    async def test_a_redelivered_batch_does_not_pause_twice_or_error(self, stack) -> None:
        """Item 25, half A: a redelivered *trip* does not pause twice.

        6 calls at $0.04 = $0.24, over the $0.20 threshold -- the same burst
        as the trip test above, replayed to prove the second delivery does not
        emit a second pause or a second runaway event. This exercises the
        status-conditional guard in ``RunawayDetector.observe`` (an agent
        already ``PAUSED_RUNAWAY`` cannot be re-tripped).
        """
        repo, driver, _provider = stack
        await repo.put_agent_state(active_agent(AGENT))
        team = team_policy("100.00")
        agent = agent_policy(AGENT, "1.00")
        await repo.put_agent_policy(agent)
        await repo.put_budget_policy(TENANT, team)

        await driver.spend(agent=agent, team=team, now=NOW, times=6)

        streams = boto3.client(
            "dynamodbstreams", region_name="us-east-1", endpoint_url=repo.client.meta.endpoint_url
        )
        ledger_stream_arn = repo.client.describe_table(TableName=repo.ledger_table)["Table"][
            "LatestStreamArn"
        ]
        records = _drain_stream(streams, ledger_stream_arn)

        first = await handler.process(_to_lambda_event(records))
        second = await handler.process(_to_lambda_event(records))  # exact redelivery

        assert first["batchItemFailures"] == []
        assert second["batchItemFailures"] == []

        events = await repo.get_runaway_events(TENANT, AGENT)
        assert len(events) == 1, "a redelivered batch must not record a second runaway trip"

        state = await repo.get_agent_state(TENANT, AGENT)
        assert state.status is AgentStatus.PAUSED_RUNAWAY  # still paused, not double-paused

    async def test_a_redelivered_batch_does_not_double_count_rolling_spend(self, stack) -> None:
        """Item 25, half B: entry-level dedup, isolated from the pause guard.

        Half A stays green even if ``claim_rolling_entry`` is broken, because
        the *other* idempotency mechanism (the status-conditional pause) masks
        it once the agent is already paused. This test stays under the
        runaway threshold specifically so nothing pauses, which makes
        ``claim_rolling_entry`` the only thing standing between a redelivered
        batch and a doubled rolling-spend total.

        Validated by mutation: with ``claim_rolling_entry``'s dedup bypassed,
        this test fails (rolling spend reads $0.24 instead of $0.12) while
        half A above keeps passing -- proof the two tests check genuinely
        different code paths.
        """
        repo, driver, _provider = stack
        await repo.put_agent_state(active_agent(AGENT))
        team = team_policy("100.00")
        agent = agent_policy(AGENT, "1.00")  # threshold $0.20; 3 calls stay under it
        await repo.put_agent_policy(agent)
        await repo.put_budget_policy(TENANT, team)

        await driver.spend(agent=agent, team=team, now=NOW, times=3)

        streams = boto3.client(
            "dynamodbstreams", region_name="us-east-1", endpoint_url=repo.client.meta.endpoint_url
        )
        ledger_stream_arn = repo.client.describe_table(TableName=repo.ledger_table)["Table"][
            "LatestStreamArn"
        ]
        records = _drain_stream(streams, ledger_stream_arn)
        assert len(records) == 3

        await handler.process(_to_lambda_event(records))
        await handler.process(_to_lambda_event(records))  # exact redelivery

        from abc_gateway.domain.money import Money
        from abc_gateway.runaway.detector import window_buckets

        rolling = await repo.sum_rolling_spend(TENANT, AGENT, buckets=window_buckets(NOW, 60))
        assert rolling == Money.from_usd_str("0.12"), (
            f"expected $0.12 (3 calls counted once each), got {rolling.to_usd_str()} -- "
            "the redelivery was double-counted"
        )
        state = await repo.get_agent_state(TENANT, AGENT)
        assert state.status is AgentStatus.ACTIVE  # $0.12 never crossed the $0.20 threshold


class TestBatchItemFailureIsolation:
    async def test_one_poison_record_does_not_block_the_rest_of_the_batch(self, stack) -> None:
        """A single malformed record must be reported individually, not fail
        the whole batch -- the entire reason `batchItemFailures` exists.
        """
        repo, driver, _provider = stack
        await repo.put_agent_state(active_agent(AGENT))
        team = team_policy("100.00")
        agent = agent_policy(AGENT, "1.00")
        await repo.put_agent_policy(agent)
        await repo.put_budget_policy(TENANT, team)

        await driver.spend(agent=agent, team=team, now=NOW, times=2)

        streams = boto3.client(
            "dynamodbstreams", region_name="us-east-1", endpoint_url=repo.client.meta.endpoint_url
        )
        ledger_stream_arn = repo.client.describe_table(TableName=repo.ledger_table)["Table"][
            "LatestStreamArn"
        ]
        good_records = _drain_stream(streams, ledger_stream_arn)
        assert good_records

        poison = {
            "eventID": "poison-1",
            "eventName": "INSERT",
            "dynamodb": {
                "NewImage": {
                    "entity_type": {"S": "LEDGER_ENTRY"},
                    # Missing every other required field: ledger_entry_from_item
                    # must raise while decoding it.
                },
                "SequenceNumber": "poison-seq-1",
            },
        }
        mixed = [good_records[0], poison, *good_records[1:]]

        result = await handler.process(_to_lambda_event(mixed))

        assert result["batchItemFailures"] == [{"itemIdentifier": "poison-seq-1"}]

    async def test_a_ttl_removal_is_ignored_without_error(self, stack) -> None:
        """A TTL deletion is housekeeping, never financially meaningful."""
        remove_event = {
            "eventID": "remove-1",
            "eventName": "REMOVE",
            "dynamodb": {"SequenceNumber": "remove-seq-1"},
        }
        result = await handler.process(_to_lambda_event([remove_event]))
        assert result["batchItemFailures"] == []


class TestThresholdBackstop:
    async def test_the_stream_flips_the_80_percent_warning_when_the_inline_path_is_skipped(
        self, stack
    ) -> None:
        """The backstop this module exists for: if the gateway died between
        reconciling and warning, the counter change still reaches the stream
        and the warning still fires from there.
        """
        repo, driver, provider = stack
        await repo.put_agent_state(active_agent(AGENT))
        team = team_policy("100.00")
        agent = agent_policy(AGENT, "0.10", warning_percent=80)
        await repo.put_agent_policy(agent)
        await repo.put_budget_policy(TENANT, team)

        catalog = load_catalog(CATALOG_PATH)

        # Reserve, invoke and reconcile directly through the engine, bypassing
        # SettlementEffects.apply -- simulating a gateway that reconciled and
        # then died before it could flip the warning inline. Two calls at
        # $0.04 against a $0.10 limit land exactly on the 80% crossing.
        for idempotency_key in ("backstop-1", "backstop-2"):
            request = build_request(
                catalog=catalog, agent=agent, team=team, now=NOW, idempotency_key=idempotency_key
            )
            grant = await driver.engine.reserve(request)
            await driver.engine.mark_dispatched(TENANT, grant.reservation_id)
            outcome = await provider.invoke(
                ChatRequest(messages=(ChatMessage("user", "hi"),), model="premium"),
                grant.effective_model,
                max_output_tokens=grant.bounded_max_output_tokens,
                timeouts=Timeouts(),
                correlation_id=grant.reservation_id,
            )
            await driver.engine.reconcile(
                TENANT,
                grant.reservation_id,
                outcome.usage,
                catalog.get("test", grant.effective_model),
                NOW,
            )
        # Deliberately no call to driver.effects.apply(...) here.

        scope = ScopeRef.agent(AGENT)
        window = BudgetWindow.for_instant(WindowType.MONTHLY, NOW)
        state = await repo.get_budget_state(scope, window, tenant_id=TENANT)
        assert state.warning_80_sent is False, "the inline path must genuinely have been skipped"
        assert state.utilization_percent >= 80

        streams = boto3.client(
            "dynamodbstreams", region_name="us-east-1", endpoint_url=repo.client.meta.endpoint_url
        )
        core_stream_arn = repo.client.describe_table(TableName=repo.core_table)["Table"][
            "LatestStreamArn"
        ]
        records = _drain_stream(streams, core_stream_arn)
        budget_state_records = [
            r
            for r in records
            if r.get("dynamodb", {}).get("NewImage", {}).get("entity_type", {}).get("S")
            == "BUDGET_STATE"
        ]
        assert budget_state_records, "no BUDGET_STATE records reached the core stream"

        result = await handler.process(_to_lambda_event(budget_state_records))
        assert result["batchItemFailures"] == []

        after = await repo.get_budget_state(scope, window, tenant_id=TENANT)
        assert after.warning_80_sent is True

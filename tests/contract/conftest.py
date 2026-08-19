"""Backend fixtures for the contract suite.

One suite runs against both backends, so anything asserted here is true of the
in-memory store *and* of real DynamoDB semantics as implemented by moto.

The capability smoke test below is deliberately a collection-time failure. The
denial decoder depends on ``ReturnValuesOnConditionCheckFailure`` returning the
item's pre-image inside a cancellation reason -- that is what turns "a condition
failed" into "you needed $0.04 and had $0.01". It is also the single most
version-fragile behaviour we rely on. Discovering it had regressed halfway
through a race test would be a miserable debugging session; failing loudly at
startup is cheap.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from abc_gateway.repo.dynamo import DynamoBudgetRepository
from abc_gateway.repo.memory import InMemoryBudgetRepository

INFRA = Path(__file__).resolve().parents[2] / "infra"
CORE_SPEC = json.loads((INFRA / "table_core.json").read_text(encoding="utf-8"))
LEDGER_SPEC = json.loads((INFRA / "table_ledger.json").read_text(encoding="utf-8"))


def _clean(spec: dict) -> dict:
    """Strip documentation keys before handing the spec to CreateTable."""
    out = {k: v for k, v in spec.items() if not k.startswith("_")}
    if "GlobalSecondaryIndexes" in out:
        out["GlobalSecondaryIndexes"] = [
            {k: v for k, v in gsi.items() if not k.startswith("_")}
            for gsi in out["GlobalSecondaryIndexes"]
        ]
    # TTL is configured after creation, not as part of CreateTable.
    out.pop("TimeToLiveSpecification", None)
    return out


@pytest.fixture(scope="session")
def moto_server():
    """A real HTTP DynamoDB mock.

    ThreadedMotoServer rather than the in-process decorator: each worker thread
    gets its own boto3 client over a real socket, which exercises genuine
    botocore serialisation and real exception construction. Sharing one
    in-process client across threads is where mock-related flakiness comes from.
    """
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0)
    server.start()
    _, port = server.get_host_and_port()
    # Bind address is reported as 0.0.0.0, which is a listen address and not a
    # dialable one on Windows. Connect to loopback explicitly.
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture
def dynamo_repo(moto_server, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

    from abc_gateway.repo.dynamo.client import make_client

    client = make_client(region="us-east-1", endpoint_url=moto_server)

    # Tables are created from the very same specs Terraform deploys, so the
    # schema under test cannot drift from the schema in production.
    for spec in (CORE_SPEC, LEDGER_SPEC):
        cleaned = _clean(spec)
        with contextlib.suppress(client.exceptions.ResourceNotFoundException):
            client.delete_table(TableName=cleaned["TableName"])
        client.create_table(**cleaned)

    return DynamoBudgetRepository(
        region="us-east-1",
        core_table=CORE_SPEC["TableName"],
        ledger_table=LEDGER_SPEC["TableName"],
        client=client,
    )


@pytest.fixture
def memory_repo():
    return InMemoryBudgetRepository()


@pytest.fixture(params=["memory", "dynamo"])
def backend(request):
    """Every contract test runs against both backends."""
    if request.param == "memory":
        return request.getfixturevalue("memory_repo")
    return request.getfixturevalue("dynamo_repo")

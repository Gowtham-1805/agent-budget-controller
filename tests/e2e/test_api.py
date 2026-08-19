"""End-to-end tests through the HTTP API.

Exercises the real FastAPI app -- middleware, auth, routing, the engine, and a
fake provider -- so the wiring is proven, not assumed.

The assertions that matter here are the ones about *identity* and about what a
rejection costs. A gateway that trusts a client-supplied agent id is not a
budget controller, and a 429 that still called the provider has not saved
anybody any money.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from tests.conftest import CATALOG_PATH, FIXED_NOW

from abc_gateway.api.deps import build_container
from abc_gateway.config.settings import Settings
from abc_gateway.domain.clock import ManualClock
from abc_gateway.main import create_app
from abc_gateway.providers.fake import FakeBehaviour, FakeProvider

ADMIN_KEY = "admin-test-key"
AGENT_KEY = "agent-test-key"
OTHER_AGENT_KEY = "other-agent-key"
TENANT = "acme"


@pytest.fixture
async def app_client():
    """A fully wired app over the in-memory store and a fake provider."""
    settings = Settings(
        environment="test",
        use_memory_store=True,
        price_catalog_path=str(CATALOG_PATH),
        admin_api_key=ADMIN_KEY,
        default_max_output_tokens=1000,
    )
    clock = ManualClock(FIXED_NOW)
    container = build_container(settings, clock=clock)

    provider = FakeProvider(
        FakeBehaviour(input_tokens=1000, output_tokens=1000),
        repository=container.repository,
        tenant_id=TENANT,
    )
    container.adapters["test"] = provider
    container.service.adapters = container.adapters

    # Two agents in the same team, each with its own credential. The second
    # exists so cross-agent access can be tested rather than assumed impossible.
    container.identity.register_raw(
        AGENT_KEY, tenant_id=TENANT, team_id="engineering", agent_id="code-review"
    )
    container.identity.register_raw(
        OTHER_AGENT_KEY, tenant_id=TENANT, team_id="engineering", agent_id="other-agent"
    )
    # The admin credential is scoped to the same tenant so it can configure it.
    container.identity.register_raw(
        ADMIN_KEY,
        tenant_id=TENANT,
        team_id="engineering",
        agent_id="admin",
        key_id="admin",
        is_admin=True,
    )

    app = create_app(settings, container=container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.provider = provider  # type: ignore[attr-defined]
            client.container = container  # type: ignore[attr-defined]
            yield client


def admin(key: str = ADMIN_KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def agent(key: str = AGENT_KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def provision(client, *, team_usd="100.00", agent_usd="0.20", **agent_kwargs):
    """Create a team and an agent through the public API."""
    await client.post(
        "/v1/teams",
        json={"team_id": "engineering", "budget": {"amount_usd": team_usd}},
        headers=admin(),
    )
    body = {
        "agent_id": "code-review",
        "team_id": "engineering",
        "budget": {"amount_usd": agent_usd},
        "routing": {"provider": "test", "preferred_model": "premium"},
        "default_max_output_tokens": 1000,
        **agent_kwargs,
    }
    return await client.post("/v1/agents", json=body, headers=admin())


class TestHealth:
    async def test_healthz_is_liveness_only(self, app_client) -> None:
        response = await app_client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_readyz_reports_what_it_checked(self, app_client) -> None:
        response = await app_client.get("/readyz")
        body = response.json()
        assert body["checks"]["price_catalog_loaded"] is True
        assert body["checks"]["budget_store_reachable"] is True
        assert body["checks"]["providers_configured"] is True

    async def test_readiness_makes_no_provider_call(self, app_client) -> None:
        """A probe that costs money on every check is its own outage."""
        before = app_client.provider.invocation_count
        await app_client.get("/readyz")
        assert app_client.provider.invocation_count == before

    async def test_readyz_reports_not_ready_when_the_store_is_unreachable(
        self, app_client, monkeypatch
    ) -> None:
        """Items 38/39: liveness and readiness must answer independently.

        Simulates the one failure a readiness probe exists to catch -- the
        budget store cannot be reached -- and asserts `/readyz` reports it
        with a 503, while `/healthz` (liveness) does not degrade with it. A
        readiness probe wired to also fail liveness would get a healthy
        process killed and restarted for a dependency outage restarting
        cannot fix.
        """

        async def broken_health_check() -> bool:
            raise ConnectionError("budget store unreachable")

        monkeypatch.setattr(
            app_client.container.repository, "health_check", broken_health_check
        )

        response = await app_client.get("/readyz")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["budget_store_reachable"] is False
        # The catalog and provider checks are unaffected -- only the store
        # check flips, proving the checks are independent, not fate-shared.
        assert body["checks"]["price_catalog_loaded"] is True
        assert body["checks"]["providers_configured"] is True

        liveness = await app_client.get("/healthz")
        assert liveness.status_code == 200
        assert liveness.json()["status"] == "ok"

    async def test_readyz_recovers_once_the_store_answers_again(
        self, app_client, monkeypatch
    ) -> None:
        """Readiness is re-evaluated per request, not cached from a failure."""

        async def broken_health_check() -> bool:
            raise ConnectionError("budget store unreachable")

        monkeypatch.setattr(
            app_client.container.repository, "health_check", broken_health_check
        )
        during_outage = await app_client.get("/readyz")
        assert during_outage.status_code == 503

        monkeypatch.undo()

        recovered = await app_client.get("/readyz")
        assert recovered.status_code == 200
        assert recovered.json()["status"] == "ready"


class TestAuthentication:
    async def test_an_unauthenticated_request_is_refused(self, app_client) -> None:
        response = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 401

    async def test_an_unknown_credential_is_refused(self, app_client) -> None:
        response = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer not-a-real-key"},
        )
        assert response.status_code == 401

    async def test_a_client_cannot_choose_its_own_agent_identity(self, app_client) -> None:
        """The security property the hierarchy depends on.

        If a caller could name its own agent, every agent could spend every
        other agent's budget and the whole structure would be decorative.
        """
        await provision(app_client, agent_usd="1.00")

        response = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={
                **agent(),
                # A deliberate attempt to impersonate another agent.
                "X-Agent-ID": "some-other-agent",
            },
        )

        assert response.status_code == 200
        # Spend landed on the authenticated agent, not the claimed one.
        budget = await app_client.get("/v1/budgets/AGENT/code-review", headers=admin())
        assert budget.json()["committed_usd"] != "0.000000"

    async def test_the_control_plane_requires_an_admin_credential(self, app_client) -> None:
        response = await app_client.post(
            "/v1/teams",
            json={"team_id": "engineering", "budget": {"amount_usd": "1.00"}},
            headers=agent(),
        )
        assert response.status_code == 403


class TestCredentialIssuance:
    async def test_an_issued_key_is_bound_to_exactly_one_agent(self, app_client) -> None:
        """The mechanism by which governance identity is established.

        Whoever holds the key can spend that agent's budget and no other, and
        there is no request field that could change which budget is drawn from.
        """
        await provision(app_client, agent_usd="1.00")

        issued = await app_client.post("/v1/agents/code-review/keys", headers=admin())
        assert issued.status_code == 201
        key = issued.json()["api_key"]

        used = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {key}"},
        )
        assert used.status_code == 200

        budget = await app_client.get("/v1/budgets/AGENT/code-review", headers=admin())
        assert budget.json()["committed_usd"] == "0.040000"

    async def test_issuing_a_key_requires_an_admin_credential(self, app_client) -> None:
        await provision(app_client, agent_usd="1.00")
        response = await app_client.post("/v1/agents/code-review/keys", headers=agent())
        assert response.status_code == 403

    async def test_a_key_cannot_be_issued_for_an_unknown_agent(self, app_client) -> None:
        response = await app_client.post("/v1/agents/does-not-exist/keys", headers=admin())
        assert response.status_code == 404


class TestGovernedInference:
    async def test_a_request_within_budget_succeeds(self, app_client) -> None:
        await provision(app_client, agent_usd="1.00")

        response = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}]},
            headers=agent(),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["usage"]["prompt_tokens"] == 1000
        assert body["budget"]["decision"] == "ALLOWED"
        assert body["budget"]["actual_cost_usd"] == "0.040000"
        assert response.headers["X-Budget-Decision"] == "ALLOWED"
        assert response.headers["X-Request-Id"]

    async def test_the_output_cap_reaches_the_provider(self, app_client) -> None:
        """Omitting max_tokens must not mean unbounded."""
        await provision(app_client, agent_usd="1.00")

        await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hello"}]},
            headers=agent(),
        )
        assert app_client.provider.invoked_max_output_tokens[-1] == 1000

    async def test_exhaustion_returns_429_and_calls_no_provider(self, app_client) -> None:
        """The assertion that separates enforcement from observability."""
        await provision(app_client, agent_usd="0.08")  # exactly two calls

        for _ in range(2):
            ok = await app_client.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hi"}]},
                headers=agent(),
            )
            assert ok.status_code == 200

        before = app_client.provider.invocation_count
        blocked = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=agent(),
        )

        assert blocked.status_code == 429
        error = blocked.json()["error"]
        assert error["type"] == "budget_exhausted"
        assert error["scope"] == "agent"
        assert error["scope_id"] == "code-review"
        assert error["available_usd"] == "0.000000"
        assert error["requested_usd"] == "0.040000"
        # Tells the caller when capacity returns, instead of inviting a retry storm.
        assert error["reset_at"].startswith("2026-09-01")
        assert app_client.provider.invocation_count == before

    async def test_an_oversized_request_is_distinguished_from_exhaustion(self, app_client) -> None:
        """Different problem, different remedy, different status code."""
        await provision(app_client, agent_usd="0.001")

        response = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=agent(),
        )
        assert response.status_code == 422
        assert response.json()["error"]["type"] == "exceeds_window_limit"


class TestIdempotency:
    async def test_a_replayed_request_does_not_call_the_provider_again(self, app_client) -> None:
        await provision(app_client, agent_usd="1.00")
        headers = {**agent(), "Idempotency-Key": "abc-123"}

        first = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=headers,
        )
        assert first.status_code == 200
        before = app_client.provider.invocation_count

        second = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=headers,
        )

        assert second.status_code == 200
        assert second.json()["replayed"] is True
        assert app_client.provider.invocation_count == before


class TestSessions:
    async def test_a_session_is_bound_to_its_creating_agent(self, app_client) -> None:
        """A session id is a correlation handle, not an authorisation."""
        await provision(app_client, agent_usd="1.00", session_budget_usd="0.20")

        created = await app_client.post("/v1/sessions", json={}, headers=agent())
        assert created.status_code == 201
        session_id = created.json()["session_id"]

        stolen = await app_client.get(f"/v1/sessions/{session_id}", headers=agent(OTHER_AGENT_KEY))
        assert stolen.status_code == 403

    async def test_a_session_closes_when_its_budget_is_spent(self, app_client) -> None:
        await provision(app_client, agent_usd="10.00", session_budget_usd="0.08")
        session_id = (await app_client.post("/v1/sessions", json={}, headers=agent())).json()[
            "session_id"
        ]

        for _ in range(2):
            await app_client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "session_id": session_id,
                },
                headers=agent(),
            )

        blocked = await app_client.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": session_id,
            },
            headers=agent(),
        )
        assert blocked.status_code == 429

        session = await app_client.get(f"/v1/sessions/{session_id}", headers=agent())
        assert session.json()["status"] == "CLOSED_BUDGET"


class TestAdmin:
    async def test_pause_blocks_traffic_and_resume_restores_it(self, app_client) -> None:
        await provision(app_client, agent_usd="10.00")

        paused = await app_client.post(
            "/v1/admin/agents/code-review/pause",
            json={"reason": "suspected loop"},
            headers=admin(),
        )
        assert paused.status_code == 200
        assert paused.json()["new_state"] == "PAUSED_ADMIN"

        before = app_client.provider.invocation_count
        blocked = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=agent(),
        )
        assert blocked.status_code == 423
        assert blocked.json()["error"]["type"] == "agent_paused"
        assert app_client.provider.invocation_count == before

        resumed = await app_client.post(
            "/v1/admin/agents/code-review/resume",
            json={"reason": "investigated; false positive"},
            headers=admin(),
        )
        assert resumed.status_code == 200

        allowed = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=agent(),
        )
        assert allowed.status_code == 200

    async def test_administrative_actions_are_audited(self, app_client) -> None:
        await provision(app_client, agent_usd="10.00")
        await app_client.post(
            "/v1/admin/agents/code-review/pause",
            json={"reason": "cost spike"},
            headers=admin(),
        )

        audit = await app_client.get("/v1/admin/audit", headers=admin())
        events = audit.json()["events"]
        assert any(e["action"] == "agent.paused" and e["reason"] == "cost spike" for e in events)

    async def test_resume_without_a_reason_is_refused(self, app_client) -> None:
        await provision(app_client, agent_usd="10.00")
        await app_client.post(
            "/v1/admin/agents/code-review/pause",
            json={"reason": "x"},
            headers=admin(),
        )
        response = await app_client.post(
            "/v1/admin/agents/code-review/resume",
            json={"reason": ""},
            headers=admin(),
        )
        assert response.status_code == 422


class TestBudgetVisibility:
    async def test_committed_and_reserved_are_reported_separately(self, app_client) -> None:
        """The distinction that makes concurrency legible to an operator."""
        await provision(app_client, agent_usd="1.00")
        await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=agent(),
        )

        body = (await app_client.get("/v1/budgets/AGENT/code-review", headers=admin())).json()

        assert body["committed_usd"] == "0.040000"
        assert body["reserved_usd"] == "0.000000"
        assert body["available_usd"] == "0.960000"
        assert body["utilization_percent"] == 4
        assert body["overage_usd"] == "0.000000"

    async def test_the_ledger_records_estimate_and_actual(self, app_client) -> None:
        await provision(app_client, agent_usd="1.00")
        app_client.provider.behaviour.output_tokens = 200

        await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=agent(),
        )

        entries = (await app_client.get("/v1/ledger", headers=agent())).json()["entries"]
        assert len(entries) == 1
        entry = entries[0]
        # Reserved the full ceiling, spent less; both are preserved.
        assert entry["reserved_output_tokens"] == 1000
        assert entry["actual_output_tokens"] == 200
        assert entry["estimated_max_cost_usd"] == "0.040000"
        assert entry["actual_total_cost_usd"] == "0.016000"
        assert entry["price_catalog_version"]

    async def test_an_admin_querying_without_agent_id_is_told_to_specify_one(
        self, app_client
    ) -> None:
        """Regression test.

        An admin credential has no ledger of its own. Silently substituting the
        admin's bootstrap identity as the agent filter produced a confident,
        empty `{"entries": []}` for a tenant that had genuinely spent money --
        indistinguishable from "nothing happened". Failing loudly beats
        answering a different question than the one that was asked.
        """
        await provision(app_client, agent_usd="1.00")
        await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=agent(),
        )

        response = await app_client.get("/v1/ledger", headers=admin())

        assert response.status_code == 422
        assert "agent_id" in response.json()["detail"]

    async def test_an_admin_querying_a_named_agent_sees_its_ledger(
        self, app_client
    ) -> None:
        await provision(app_client, agent_usd="1.00")
        await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=agent(),
        )

        response = await app_client.get(
            "/v1/ledger?agent_id=code-review", headers=admin()
        )

        assert response.status_code == 200
        assert len(response.json()["entries"]) == 1

    async def test_a_non_admin_querying_without_agent_id_sees_its_own_ledger(
        self, app_client
    ) -> None:
        """A normal agent credential has an obvious default: itself."""
        await provision(app_client, agent_usd="1.00")
        await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=agent(),
        )

        response = await app_client.get("/v1/ledger", headers=agent())

        assert response.status_code == 200
        assert len(response.json()["entries"]) == 1

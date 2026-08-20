"""Cross-tenant data isolation through the real HTTP API.

Every read/list endpoint scopes its query by `principal.tenant_id`, which
comes from the resolved credential (rule 5) -- this proves that scoping
actually holds end-to-end: tenant A's credential must never see tenant B's
teams, agents, sessions, or ledger, even when it knows the exact ID to ask
for.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from tests.conftest import CATALOG_PATH, FIXED_NOW

from abc_gateway.api.deps import build_container
from abc_gateway.config.settings import Settings
from abc_gateway.domain.clock import ManualClock
from abc_gateway.main import create_app

ADMIN_A = "admin-tenant-a"
ADMIN_B = "admin-tenant-b"
AGENT_A = "agent-tenant-a"


@pytest.fixture
async def app_client():
    settings = Settings(
        environment="test",
        use_memory_store=True,
        price_catalog_path=str(CATALOG_PATH),
        default_max_output_tokens=1000,
    )
    clock = ManualClock(FIXED_NOW)
    container = build_container(settings, clock=clock)

    container.identity.register_raw(
        ADMIN_A, tenant_id="tenant-a", team_id="admin", agent_id="admin", is_admin=True
    )
    container.identity.register_raw(
        ADMIN_B, tenant_id="tenant-b", team_id="admin", agent_id="admin", is_admin=True
    )
    container.identity.register_raw(
        AGENT_A, tenant_id="tenant-a", team_id="engineering", agent_id="code-review"
    )

    app = create_app(settings, container=container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _provision_tenant_a(client) -> None:
    await client.post(
        "/v1/teams",
        json={"team_id": "engineering", "budget": {"amount_usd": "100.00"}},
        headers=_auth(ADMIN_A),
    )
    await client.post(
        "/v1/agents",
        json={
            "agent_id": "code-review",
            "team_id": "engineering",
            "budget": {"amount_usd": "10.00"},
            "routing": {"provider": "test", "preferred_model": "premium"},
            "session_budget_usd": "1.00",
            "default_max_output_tokens": 1000,
        },
        headers=_auth(ADMIN_A),
    )


class TestTeamIsolation:
    async def test_tenant_b_cannot_list_tenant_as_teams(self, app_client) -> None:
        await _provision_tenant_a(app_client)
        response = await app_client.get("/v1/teams", headers=_auth(ADMIN_B))
        assert response.status_code == 200
        assert response.json() == []

    async def test_tenant_b_cannot_fetch_tenant_as_team_by_guessed_id(self, app_client) -> None:
        await _provision_tenant_a(app_client)
        response = await app_client.get("/v1/teams/engineering", headers=_auth(ADMIN_B))
        assert response.status_code == 404


class TestAgentIsolation:
    async def test_tenant_b_cannot_list_tenant_as_agents(self, app_client) -> None:
        await _provision_tenant_a(app_client)
        response = await app_client.get("/v1/agents", headers=_auth(ADMIN_B))
        assert response.status_code == 200
        assert response.json() == []

    async def test_tenant_b_cannot_fetch_tenant_as_agent_by_guessed_id(self, app_client) -> None:
        await _provision_tenant_a(app_client)
        response = await app_client.get("/v1/agents/code-review", headers=_auth(ADMIN_B))
        assert response.status_code == 404

    async def test_tenant_bs_admin_key_cannot_mint_a_key_for_tenant_as_agent(
        self, app_client
    ) -> None:
        """Even naming the exact agent id, an admin from another tenant
        cannot mint a credential against it -- the lookup is always scoped
        to the caller's own tenant_id, never the path parameter alone."""
        await _provision_tenant_a(app_client)
        response = await app_client.post(
            "/v1/agents/code-review/keys", headers=_auth(ADMIN_B)
        )
        assert response.status_code == 404


class TestLedgerAndEventsIsolation:
    async def test_tenant_bs_ledger_never_shows_tenant_as_entries(self, app_client) -> None:
        await _provision_tenant_a(app_client)
        # Tenant A actually spends something.
        await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=_auth(AGENT_A),
        )
        # An admin credential has no agent_id of its own (see the ledger
        # endpoint's own comment on that), so it must name one explicitly --
        # naming tenant A's exact agent id anyway must still see nothing.
        b_ledger = await app_client.get(
            "/v1/ledger", params={"agent_id": "code-review"}, headers=_auth(ADMIN_B)
        )
        assert b_ledger.status_code == 200
        assert b_ledger.json()["entries"] == []

    async def test_tenant_bs_events_never_show_tenant_as_events(self, app_client) -> None:
        await _provision_tenant_a(app_client)
        b_events = await app_client.get("/v1/events", headers=_auth(ADMIN_B))
        assert b_events.status_code == 200
        assert b_events.json() == []


class TestSessionIsolation:
    async def test_tenant_b_cannot_list_tenant_as_sessions(self, app_client) -> None:
        await _provision_tenant_a(app_client)
        await app_client.post("/v1/sessions", json={}, headers=_auth(AGENT_A))
        b_sessions = await app_client.get("/v1/sessions", headers=_auth(ADMIN_B))
        assert b_sessions.status_code == 200
        assert b_sessions.json() == []

    async def test_tenant_b_cannot_fetch_tenant_as_session_by_guessed_id(self, app_client) -> None:
        await _provision_tenant_a(app_client)
        created = await app_client.post("/v1/sessions", json={}, headers=_auth(AGENT_A))
        session_id = created.json()["session_id"]

        response = await app_client.get(
            f"/v1/sessions/{session_id}", headers=_auth(ADMIN_B)
        )
        # 404 (not found in tenant B's scope) or 403 (ownership check) are
        # both acceptable outcomes -- what must never happen is 200 with
        # tenant A's data.
        assert response.status_code in (403, 404)

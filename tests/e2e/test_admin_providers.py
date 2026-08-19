"""E2E and integration tests for Admin Provider Configuration and Governance."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from tests.conftest import CATALOG_PATH, FIXED_NOW

from abc_gateway.api.deps import build_container
from abc_gateway.config.settings import Settings
from abc_gateway.domain.clock import ManualClock
from abc_gateway.domain.providers import mask_secret
from abc_gateway.main import create_app

ADMIN_KEY = "test-admin-secret-key-12345"


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        environment="test",
        use_memory_store=True,
        enable_fake_provider=True,
        price_catalog_path=str(CATALOG_PATH),
        admin_api_key=ADMIN_KEY,
    )


@pytest.fixture
async def client(test_settings):
    clock = ManualClock(FIXED_NOW)
    container = build_container(test_settings, clock=clock)
    app = create_app(settings=test_settings, container=container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_mask_secret_helper():
    assert mask_secret(None) == ""
    assert mask_secret("") == ""
    assert mask_secret("short") == "••••rt"
    assert mask_secret("sk-1234567890abcdef1234567890abcdef7F2A") == "sk-••••••••••••••••••••7F2A"
    assert mask_secret("anthropic-secret-key-9999") == "anthropic-••••••••••••••••••••9999"


@pytest.mark.asyncio
async def test_admin_providers_requires_auth(client: AsyncClient):
    # Unauthenticated request
    resp = await client.get("/v1/admin/providers")
    assert resp.status_code == 401

    # Invalid credential
    resp = await client.get(
        "/v1/admin/providers",
        headers={"Authorization": "Bearer bad-key"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_providers_and_masking(client: AsyncClient):
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    resp = await client.get("/v1/admin/providers", headers=headers)
    assert resp.status_code == 200
    providers = resp.json()
    assert len(providers) >= 4

    provider_map = {p["provider"]: p for p in providers}
    assert "openai" in provider_map
    assert "bedrock" in provider_map
    assert "anthropic" in provider_map
    assert "test" in provider_map

    # Confirm secret is never returned
    for p in providers:
        assert p.get("api_key") is None
        if p.get("masked_api_key"):
            assert "••••" in p["masked_api_key"]


@pytest.mark.asyncio
async def test_configure_and_update_openai(client: AsyncClient):
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}

    # Update OpenAI configuration
    raw_key = "sk-production-test-key-abcdef1234567890-7F2A"
    payload = {
        "api_key": raw_key,
        "default_model": "gpt-4o",
        "enabled": True,
        "organization_id": "org-test-123",
    }
    resp = await client.put("/v1/admin/providers/openai", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["enabled"] is True
    assert data["default_model"] == "gpt-4o"
    assert data["masked_api_key"] == "sk-••••••••••••••••••••7F2A"
    assert data["organization_id"] == "org-test-123"
    # Never returns raw API key in body
    assert raw_key not in resp.text

    # Get single provider
    get_resp = await client.get("/v1/admin/providers/openai", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["masked_api_key"] == "sk-••••••••••••••••••••7F2A"
    assert raw_key not in get_resp.text

    # Check audit log to verify action was audited and NO raw secret is recorded
    audit_resp = await client.get("/v1/admin/audit", headers=headers)
    assert audit_resp.status_code == 200
    events = audit_resp.json()["events"]
    matching = [e for e in events if e.get("target") == "openai"]
    assert len(matching) > 0
    assert raw_key not in audit_resp.text


@pytest.mark.asyncio
async def test_invalid_model_rejected(client: AsyncClient):
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    payload = {
        "default_model": "non-existent-fake-model-xyz",
    }
    resp = await client.put("/v1/admin/providers/openai", json=payload, headers=headers)
    assert resp.status_code == 422
    assert "not in the price catalog" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_bedrock_iam_configuration(client: AsyncClient):
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    payload = {
        "region": "us-west-2",
        "default_model": "amazon.nova-pro-v1:0",
        "enabled": True,
    }
    resp = await client.put("/v1/admin/providers/bedrock", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_type"] == "iam_role"
    assert data["region"] == "us-west-2"
    assert data["default_model"] == "amazon.nova-pro-v1:0"
    assert data["enabled"] is True


@pytest.mark.asyncio
async def test_enable_disable_provider(client: AsyncClient):
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}

    # Disable test provider
    resp = await client.post("/v1/admin/providers/test/disable", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # Enable test provider
    resp = await client.post("/v1/admin/providers/test/enable", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True

    # Try enabling unconfigured provider (Anthropic with no key)
    resp = await client.post("/v1/admin/providers/anthropic/enable", headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_test_connection_endpoint(client: AsyncClient):
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}

    # Test provider test connection
    resp = await client.post("/v1/admin/providers/test/test", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "test"
    assert data["status"] == "healthy"
    assert data["authentication"] == "none"

    # Bedrock test connection (in test environment with no AWS ambient role)
    resp = await client.post("/v1/admin/providers/bedrock/test", headers=headers)
    assert resp.status_code == 200
    b_data = resp.json()
    assert b_data["provider"] == "bedrock"
    assert b_data["authentication"] == "iam_role"


@pytest.mark.asyncio
async def test_catalog_models_endpoint(client: AsyncClient):
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}

    # All models
    resp = await client.get("/v1/admin/catalog/models", headers=headers)
    assert resp.status_code == 200
    models = resp.json()
    assert len(models) > 0

    first = models[0]
    assert "provider" in first
    assert "model" in first
    assert "input_per_million" in first
    assert "output_per_million" in first
    assert "max_context_tokens" in first
    assert "max_output_tokens" in first
    assert first["preflight_token_counting"] is True

    # Filter by provider
    resp = await client.get("/v1/admin/catalog/models?provider=openai", headers=headers)
    assert resp.status_code == 200
    openai_models = resp.json()
    assert all(m["provider"] == "openai" for m in openai_models)


@pytest.mark.asyncio
async def test_non_admin_forbidden(client: AsyncClient, test_settings):
    # Register regular agent key
    clock = ManualClock(FIXED_NOW)
    container = build_container(test_settings, clock=clock)
    container.identity.register_raw(
        "regular-agent-key",
        tenant_id="acme",
        team_id="eng",
        agent_id="agent-1",
        is_admin=False,
    )
    app = create_app(settings=test_settings, container=container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as agent_client:
            agent_headers = {"Authorization": "Bearer regular-agent-key"}

            # Provider list requires admin
            r1 = await agent_client.get("/v1/admin/providers", headers=agent_headers)
            assert r1.status_code == 403

            # Provider update requires admin
            r2 = await agent_client.put(
                "/v1/admin/providers/openai",
                json={"default_model": "gpt-4o"},
                headers=agent_headers,
            )
            assert r2.status_code == 403

            # Provider test requires admin
            r3 = await agent_client.post("/v1/admin/providers/test/test", headers=agent_headers)
            assert r3.status_code == 403


@pytest.mark.asyncio
async def test_secret_retention_on_partial_update(client: AsyncClient):
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}

    # Initial configuration with full secret
    raw_key = "sk-initial-secret-key-1234567890abcdef-A1B2"
    resp = await client.put(
        "/v1/admin/providers/openai",
        json={"api_key": raw_key, "default_model": "gpt-4o", "enabled": True},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["masked_api_key"] == "sk-••••••••••••••••••••A1B2"

    # Update only default_model without sending api_key
    resp2 = await client.put(
        "/v1/admin/providers/openai",
        json={"default_model": "gpt-4o-mini"},
        headers=headers,
    )
    assert resp2.status_code == 200
    # Secret remains retained and masked
    assert resp2.json()["default_model"] == "gpt-4o-mini"
    assert resp2.json()["masked_api_key"] == "sk-••••••••••••••••••••A1B2"
    assert resp2.json()["configured"] is True


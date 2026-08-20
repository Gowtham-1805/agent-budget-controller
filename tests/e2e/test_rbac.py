"""RBAC across the control and data planes.

Covers the group table from the auth design: VIEWER can read but not write,
OPERATOR can write but not administer, ADMIN can do everything, and -- the
case a naive "just accept a session everywhere a key works" implementation
would get wrong -- a human session can never reach the data plane, and an
agent key can never reach the control plane.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from tests.conftest import CATALOG_PATH, FIXED_NOW

from abc_gateway.api.deps import build_container
from abc_gateway.auth.identity import hash_key
from abc_gateway.config.settings import Settings
from abc_gateway.domain.clock import ManualClock
from abc_gateway.domain.user import Role, UserRecord, UserStatus, normalize_email
from abc_gateway.main import create_app

TENANT = "acme"
AGENT_KEY = "agent-test-key"


@pytest.fixture
async def app_client():
    settings = Settings(
        environment="local",
        use_memory_store=True,
        price_catalog_path=str(CATALOG_PATH),
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
        default_max_output_tokens=1000,
    )
    clock = ManualClock(FIXED_NOW)
    container = build_container(settings, clock=clock)

    container.identity.register_raw(
        AGENT_KEY, tenant_id=TENANT, team_id="engineering", agent_id="code-review"
    )

    users: dict[str, str] = {}
    for role in (Role.VIEWER, Role.OPERATOR, Role.ADMIN):
        email = normalize_email(f"{role.value.lower()}@example.com")
        password = f"{role.value.lower()}-passphrase"
        user = UserRecord(
            user_id=f"user-{role.value.lower()}",
            tenant_id=TENANT,
            email=email,
            email_hash=hash_key(email),
            password_hash=container.sessions.passwords.hash(password),
            role=role,
            status=UserStatus.ACTIVE,
            created_at=datetime.now(UTC),
            password_changed_at=datetime.now(UTC),
        )
        await container.repository.create_user(user)
        users[role.value] = password

    # A team exists up front so read-group tests have something to read.
    from abc_gateway.domain.money import Money
    from abc_gateway.domain.policy import BudgetPolicy
    from abc_gateway.domain.scopes import ScopeType
    from abc_gateway.domain.window import WindowType

    await container.repository.put_budget_policy(
        TENANT,
        BudgetPolicy(
            scope_type=ScopeType.TEAM,
            scope_id="engineering",
            limit=Money.from_usd_str("100.00"),
            window_type=WindowType.MONTHLY,
        ),
    )

    app = create_app(settings, container=container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.role_passwords = users  # type: ignore[attr-defined]
            yield client


async def _session_headers(client, role: str) -> dict[str, str]:
    """A header-only credential for one role, with no cookie left behind.

    Login sets cookies on the shared client too; clearing them here is what
    makes "authenticate via X-ABC-Session" actually true for the request that
    follows, rather than silently falling back to a leftover cookie from a
    previous role's login (which would also require the CSRF header this
    helper's callers do not send).
    """
    email = f"{role.lower()}@example.com"
    password = client.role_passwords[role]
    response = await client.post("/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["session_token"]
    client.cookies.clear()
    return {"X-ABC-Session": token}


def _agent_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {AGENT_KEY}"}


# (role, method, path, body) -> expected status. One representative route per
# group from the auth design's table, not every route -- the pattern is what
# matters, and it is identical across every route in a group.
CASES = [
    # -- control-plane read: VIEWER+ --
    ("VIEWER", "GET", "/v1/teams", None, 200),
    ("OPERATOR", "GET", "/v1/teams", None, 200),
    ("ADMIN", "GET", "/v1/teams", None, 200),
    # -- control-plane write: OPERATOR+ --
    ("VIEWER", "POST", "/v1/teams", {"team_id": "x", "budget": {"amount_usd": "1.00"}}, 403),
    ("OPERATOR", "POST", "/v1/teams", {"team_id": "x", "budget": {"amount_usd": "1.00"}}, 201),
    ("ADMIN", "POST", "/v1/teams", {"team_id": "y", "budget": {"amount_usd": "1.00"}}, 201),
    # -- admin only: agent-key minting --
    (
        "OPERATOR",
        "POST",
        "/v1/agents/nonexistent/keys",
        None,
        403,
    ),
]


class TestRoleFloorPerRouteGroup:
    @pytest.mark.parametrize("role,method,path,body,expected", CASES)
    async def test_role_floor(self, app_client, role, method, path, body, expected) -> None:
        headers = await _session_headers(app_client, role)
        response = await app_client.request(method, path, json=body, headers=headers)
        assert response.status_code == expected, response.text


class TestAgentCannotReachControlPlane:
    async def test_agent_key_is_forbidden_on_a_control_plane_read(self, app_client) -> None:
        response = await app_client.get("/v1/teams", headers=_agent_headers())
        assert response.status_code == 403

    async def test_agent_key_is_forbidden_on_a_control_plane_write(self, app_client) -> None:
        response = await app_client.post(
            "/v1/teams",
            json={"team_id": "z", "budget": {"amount_usd": "1.00"}},
            headers=_agent_headers(),
        )
        assert response.status_code == 403


class TestHumanSessionCannotReachDataPlane:
    async def test_admin_session_is_forbidden_on_chat_completions(self, app_client) -> None:
        """The highest-privilege human role still cannot spend an agent's
        budget: a session carries no agent_id, and require_agent() is what
        stops a naive 'accept a session everywhere a key works' design from
        opening this hole."""
        headers = await _session_headers(app_client, "ADMIN")
        response = await app_client.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=headers,
        )
        assert response.status_code == 403

    async def test_operator_session_is_forbidden_on_responses(self, app_client) -> None:
        headers = await _session_headers(app_client, "OPERATOR")
        response = await app_client.post(
            "/v1/responses",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=headers,
        )
        assert response.status_code == 403


class TestNoCredentialIsUnauthenticated:
    async def test_missing_credential_on_a_read_route_is_401(self, app_client) -> None:
        response = await app_client.get("/v1/teams")
        assert response.status_code == 401

    async def test_missing_credential_on_the_data_plane_is_401(self, app_client) -> None:
        response = await app_client.post(
            "/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]}
        )
        assert response.status_code == 401

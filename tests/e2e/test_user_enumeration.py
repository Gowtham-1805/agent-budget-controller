"""User-enumeration resistance through the real login endpoint.

Unknown email, wrong password, and a locked account must all be
indistinguishable to an unauthenticated caller. This is proven at the HTTP
layer -- not just in auth/sessions.py's unit tests -- because it is easy for
a frontend or a future endpoint change to reintroduce a distinguishing detail
(a different status code, an extra field, a faster response) that the service
layer itself never had.
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
EMAIL = "operator@example.com"
PASSWORD = "correct horse battery staple"


@pytest.fixture
async def app_client():
    settings = Settings(
        environment="local",
        use_memory_store=True,
        price_catalog_path=str(CATALOG_PATH),
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
        login_ip_limit=1000,  # tier 1 must not interfere with these tests
        login_account_limit=5,
        login_account_window_seconds=900,
        login_account_lockout_cap_seconds=900,
    )
    clock = ManualClock(FIXED_NOW)
    container = build_container(settings, clock=clock)

    normalized = normalize_email(EMAIL)
    user = UserRecord(
        user_id="operator-1",
        tenant_id=TENANT,
        email=normalized,
        email_hash=hash_key(normalized),
        password_hash=container.sessions.passwords.hash(PASSWORD),
        role=Role.OPERATOR,
        status=UserStatus.ACTIVE,
        created_at=datetime.now(UTC),
        password_changed_at=datetime.now(UTC),
    )
    await container.repository.create_user(user)

    app = create_app(settings, container=container)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.container = container  # type: ignore[attr-defined]
            yield client


def _body(response) -> dict:
    data = response.json()
    return {k: v for k, v in data["error"].items() if k != "request_id"}


async def _lock_the_account(client) -> None:
    settings: Settings = client.container.settings
    for _ in range(settings.login_account_limit):
        await client.post("/v1/auth/login", json={"email": EMAIL, "password": "wrong"})


class TestUnknownEmailVsWrongPassword:
    async def test_identical_status_code(self, app_client) -> None:
        wrong_pw = await app_client.post(
            "/v1/auth/login", json={"email": EMAIL, "password": "wrong"}
        )
        unknown = await app_client.post(
            "/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
        )
        assert wrong_pw.status_code == unknown.status_code == 401

    async def test_identical_body_modulo_request_id(self, app_client) -> None:
        wrong_pw = await app_client.post(
            "/v1/auth/login", json={"email": EMAIL, "password": "wrong"}
        )
        unknown = await app_client.post(
            "/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
        )
        assert _body(wrong_pw) == _body(unknown)

    async def test_the_dummy_hash_path_actually_ran(self, app_client) -> None:
        """Confirms the response wasn't merely coincidentally identical --
        the unknown-email path really did pay the same Argon2 cost, via the
        counter rather than a flaky wall-clock timing assertion."""
        passwords = app_client.container.sessions.passwords
        before = passwords.dummy_verify_calls
        await app_client.post(
            "/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
        )
        assert passwords.dummy_verify_calls == before + 1


class TestLockedAccountIsIndistinguishable:
    async def test_locked_account_returns_the_same_body_as_wrong_password(
        self, app_client
    ) -> None:
        await _lock_the_account(app_client)

        locked_with_correct_password = await app_client.post(
            "/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        wrong_password_elsewhere = await app_client.post(
            "/v1/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
        )
        assert locked_with_correct_password.status_code == wrong_password_elsewhere.status_code
        assert _body(locked_with_correct_password) == _body(wrong_password_elsewhere)

    async def test_a_locked_account_never_returns_a_429_that_names_the_account(
        self, app_client
    ) -> None:
        """The account-level lockout must not surface as a distinguishable
        status code (e.g. 429) -- that would itself be an oracle for "this
        account exists and is currently locked."""
        await _lock_the_account(app_client)
        response = await app_client.post(
            "/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        assert response.status_code == 401

    async def test_lockout_is_a_cooldown_not_a_latch(self, app_client) -> None:
        """No admin action may ever be *required* to recover -- only time."""
        clock: ManualClock = app_client.container.clock
        await _lock_the_account(app_client)

        from datetime import timedelta

        settings: Settings = app_client.container.settings
        clock.advance(timedelta(seconds=settings.login_account_lockout_cap_seconds + 1))

        response = await app_client.post(
            "/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        assert response.status_code == 200

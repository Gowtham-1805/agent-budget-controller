"""End-to-end tests of the human authentication API.

Exercises the real FastAPI app -- middleware, cookies, the session service,
and the credential repository -- the same way tests/e2e/test_api.py exercises
the data plane.
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


async def _build_client(*, environment: str = "local"):
    settings = Settings(
        environment=environment,
        use_memory_store=True,
        price_catalog_path=str(CATALOG_PATH),
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
        login_ip_limit=100,
        login_account_limit=5,
        session_ttl_minutes=60,
        session_idle_timeout_minutes=30,
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
    return app, container, user


@pytest.fixture
async def app_client():
    """Cookies flow through this client's jar on subsequent requests.

    ``environment="local"`` so the session cookie is not marked ``Secure`` --
    httpx's cookie jar (like a real browser) correctly refuses to resend a
    ``Secure`` cookie over the plain-``http://`` transport this fixture uses,
    which would otherwise make every cookie-based follow-up request in this
    file 401 for a reason that has nothing to do with the behaviour under
    test. The ``Secure`` attribute itself is asserted separately below,
    directly off the response header, by ``app_client_prod_like``.
    """
    app, container, user = await _build_client(environment="local")
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.container = container  # type: ignore[attr-defined]
            client.seeded_user = user  # type: ignore[attr-defined]
            yield client


@pytest.fixture
async def app_client_prod_like():
    """Same wiring, but with Secure cookies -- for header assertions only.

    Never used for a second request in the same test: see the docstring on
    ``app_client`` for why that would 401.
    """
    app, container, user = await _build_client(environment="test")
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            client.container = container  # type: ignore[attr-defined]
            client.seeded_user = user  # type: ignore[attr-defined]
            yield client


class TestLoginSetsCookies:
    async def test_login_sets_httponly_secure_strict_session_cookie(
        self, app_client_prod_like
    ) -> None:
        response = await app_client_prod_like.post(
            "/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        assert response.status_code == 200

        set_cookie_headers = response.headers.get_list("set-cookie")
        session_cookie = next(h for h in set_cookie_headers if h.startswith("abc_dash_session="))
        assert "HttpOnly" in session_cookie
        assert "samesite=strict" in session_cookie.lower()
        # environment="test" is not "local", so Secure must be set.
        assert "secure" in session_cookie.lower()

    async def test_csrf_cookie_is_not_httponly(self, app_client_prod_like) -> None:
        response = await app_client_prod_like.post(
            "/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        set_cookie_headers = response.headers.get_list("set-cookie")
        csrf_cookie = next(h for h in set_cookie_headers if h.startswith("abc_dash_csrf="))
        assert "HttpOnly" not in csrf_cookie

    async def test_response_body_carries_the_session_token_for_the_proxy(self, app_client) -> None:
        response = await app_client.post(
            "/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        body = response.json()
        assert body["role"] == "OPERATOR"
        assert body["tenant_id"] == TENANT
        assert len(body["session_token"]) >= 32


class TestSessionIdentity:
    async def test_get_session_reflects_the_logged_in_user(self, app_client) -> None:
        await app_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        response = await app_client.get("/v1/auth/session")
        assert response.status_code == 200
        assert response.json()["email"] == EMAIL
        assert response.json()["role"] == "OPERATOR"

    async def test_get_session_without_a_cookie_is_401(self, app_client) -> None:
        response = await app_client.get("/v1/auth/session")
        assert response.status_code == 401

    async def test_x_abc_session_header_works_without_a_cookie(self, app_client) -> None:
        """The dashboard's server-side proxy forwards this header, not a cookie."""
        login = await app_client.post(
            "/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        token = login.json()["session_token"]
        app_client.cookies.clear()

        response = await app_client.get(
            "/v1/auth/session", headers={"X-ABC-Session": token}
        )
        assert response.status_code == 200
        assert response.json()["email"] == EMAIL


class TestLogout:
    async def test_logout_invalidates_the_session(self, app_client) -> None:
        await app_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        assert (await app_client.get("/v1/auth/session")).status_code == 200

        logout = await app_client.post("/v1/auth/logout")
        assert logout.status_code == 204

        after = await app_client.get("/v1/auth/session")
        assert after.status_code == 401

    async def test_logout_clears_the_cookies(self, app_client) -> None:
        await app_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        response = await app_client.post("/v1/auth/logout")
        set_cookie_headers = response.headers.get_list("set-cookie")
        session_clear = next(h for h in set_cookie_headers if h.startswith("abc_dash_session="))
        assert "abc_dash_session=\"\";" in session_clear or "Max-Age=0" in session_clear


def _csrf_header(client) -> dict[str, str]:
    """The double-submit CSRF header a real browser page would echo back.

    A cookie-authenticated, state-changing call needs this (see
    ``get_session_principal``'s CSRF check) precisely because a cookie is
    something the browser attaches automatically -- the header value is not,
    so only same-origin JS that could already read the non-HttpOnly CSRF
    cookie can supply it.
    """
    return {"X-ABC-CSRF": client.cookies.get("abc_dash_csrf")}


class TestCsrf:
    async def test_a_cookie_authenticated_mutation_without_csrf_is_refused(
        self, app_client
    ) -> None:
        await app_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        response = await app_client.post(
            "/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": "a whole new passphrase"},
        )
        assert response.status_code == 403

    async def test_a_wrong_csrf_token_is_refused(self, app_client) -> None:
        await app_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        response = await app_client.post(
            "/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": "a whole new passphrase"},
            headers={"X-ABC-CSRF": "not-the-real-token"},
        )
        assert response.status_code == 403

    async def test_the_x_abc_session_header_path_needs_no_csrf(self, app_client) -> None:
        """A custom header cannot be attached by a third-party page without
        script access to it -- only an ambient cookie needs the double-submit
        check."""
        login = await app_client.post(
            "/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}
        )
        token = login.json()["session_token"]
        app_client.cookies.clear()

        response = await app_client.post(
            "/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": "a whole new passphrase"},
            headers={"X-ABC-Session": token},
        )
        assert response.status_code == 204


class TestPasswordChange:
    async def test_wrong_current_password_is_rejected(self, app_client) -> None:
        await app_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        response = await app_client.post(
            "/v1/auth/password",
            json={"current_password": "not it", "new_password": "a whole new passphrase"},
            headers=_csrf_header(app_client),
        )
        assert response.status_code == 401

    async def test_correct_password_change_revokes_sibling_sessions(self, app_client) -> None:
        await app_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})

        # A second, independent session for the same user.
        token2, _session2 = await app_client.container.sessions.login(
            email=EMAIL, password=PASSWORD, ip="9.9.9.9", user_agent="other-device"
        )

        # Change the password using the *first* session's cookie (already in
        # the client's jar from login above).
        response = await app_client.post(
            "/v1/auth/password",
            json={"current_password": PASSWORD, "new_password": "a whole new passphrase"},
            headers=_csrf_header(app_client),
        )
        assert response.status_code == 204

        # The session that made the change is still alive.
        assert (await app_client.get("/v1/auth/session")).status_code == 200

        # The sibling session from another device is not. The client's jar
        # still carries the first session's cookie, and a cookie takes
        # priority over X-ABC-Session (see _extract_session_token), so it
        # must be cleared here or this would just re-check session 1.
        app_client.cookies.clear()
        second_check = await app_client.get(
            "/v1/auth/session", headers={"X-ABC-Session": token2}
        )
        assert second_check.status_code == 401


class TestOversizedPassword:
    async def test_an_overlong_password_is_rejected_before_hashing(self, app_client) -> None:
        """max_length=1024 on the schema: without it, an unauthenticated
        caller could post a multi-megabyte password and force it through
        Argon2 -- a free CPU-exhaustion primitive."""
        response = await app_client.post(
            "/v1/auth/login",
            json={"email": EMAIL, "password": "x" * 2000},
        )
        assert response.status_code == 422


class TestAdminUserManagement:
    async def test_create_user_requires_admin(self, app_client) -> None:
        await app_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
        response = await app_client.post(
            "/v1/auth/admin/users",
            json={"email": "new@example.com", "password": "a fine passphrase", "role": "VIEWER"},
            headers=_csrf_header(app_client),
        )
        # The seeded user is only OPERATOR.
        assert response.status_code == 403

    async def test_admin_can_create_and_list_users(self, app_client) -> None:
        from dataclasses import replace

        admin_user = replace(app_client.seeded_user, role=Role.ADMIN)
        await app_client.container.repository.put_user(admin_user)
        await app_client.post("/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})

        create = await app_client.post(
            "/v1/auth/admin/users",
            json={"email": "new@example.com", "password": "a fine passphrase", "role": "VIEWER"},
            headers=_csrf_header(app_client),
        )
        assert create.status_code == 201

        listing = await app_client.get("/v1/auth/admin/users")
        emails = {u["email"] for u in listing.json()}
        assert "new@example.com" in emails

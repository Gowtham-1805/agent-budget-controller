"""Login orchestration and session lifecycle (auth/sessions.py)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from abc_gateway.auth.identity import AuthenticationError, hash_key
from abc_gateway.auth.passwords import PasswordService
from abc_gateway.auth.ratelimit import LoginThrottle
from abc_gateway.auth.sessions import SessionService
from abc_gateway.config.settings import Settings
from abc_gateway.domain.clock import ManualClock
from abc_gateway.domain.user import Role, UserRecord, UserStatus, normalize_email
from abc_gateway.repo.memory import InMemoryBudgetRepository

NOW = datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)
EMAIL = "operator@example.com"
PASSWORD = "correct horse battery staple"

_TEST_SETTINGS = Settings(
    argon2_time_cost=1,
    argon2_memory_cost_kib=8192,
    argon2_parallelism=1,
    session_ttl_minutes=60,
    session_idle_timeout_minutes=30,
    login_ip_limit=100,
    login_ip_window_seconds=60,
    login_account_limit=5,
    login_account_window_seconds=900,
    login_account_lockout_cap_seconds=900,
)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(NOW)


@pytest.fixture
def repo() -> InMemoryBudgetRepository:
    return InMemoryBudgetRepository()


@pytest.fixture
def service(repo: InMemoryBudgetRepository, clock: ManualClock) -> SessionService:
    return SessionService(
        repository=repo,
        passwords=PasswordService(_TEST_SETTINGS),
        throttle=LoginThrottle(clock, limit=_TEST_SETTINGS.login_ip_limit, window_seconds=60),
        clock=clock,
        settings=_TEST_SETTINGS,
    )


@pytest.fixture
async def seeded_user(repo: InMemoryBudgetRepository, service: SessionService) -> UserRecord:
    normalized = normalize_email(EMAIL)
    user = UserRecord(
        user_id="user-1",
        tenant_id="acme",
        email=normalized,
        email_hash=hash_key(normalized),
        password_hash=service.passwords.hash(PASSWORD),
        role=Role.OPERATOR,
        status=UserStatus.ACTIVE,
        created_at=NOW,
        password_changed_at=NOW,
    )
    created = await repo.create_user(user)
    assert created
    return user


class TestLogin:
    async def test_correct_credentials_mint_a_session(
        self, service: SessionService, seeded_user: UserRecord
    ) -> None:
        raw_token, session = await service.login(
            email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest"
        )
        assert len(raw_token) >= 32  # secrets.token_urlsafe(32): 256+ bits
        assert session.user_id == seeded_user.user_id
        assert session.role is Role.OPERATOR

    async def test_raw_token_is_never_persisted(
        self, repo: InMemoryBudgetRepository, service: SessionService, seeded_user: UserRecord
    ) -> None:
        raw_token, session = await service.login(
            email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest"
        )
        stored = await repo.get_auth_session(session.token_hash)
        assert stored is not None
        from dataclasses import astuple

        assert raw_token not in astuple(stored)
        assert stored.token_hash == hash_key(raw_token)

    async def test_wrong_password_and_unknown_email_are_indistinguishable(
        self, service: SessionService, seeded_user: UserRecord
    ) -> None:
        with pytest.raises(AuthenticationError) as wrong_pw:
            await service.login(
                email=EMAIL, password="not the password", ip="1.2.3.4", user_agent="pytest"
            )
        with pytest.raises(AuthenticationError) as unknown_email:
            await service.login(
                email="nobody@example.com", password="anything", ip="1.2.3.5", user_agent="pytest"
            )
        assert str(wrong_pw.value) == str(unknown_email.value)

    async def test_a_locked_account_returns_the_same_generic_error(
        self, service: SessionService, seeded_user: UserRecord
    ) -> None:
        for _ in range(_TEST_SETTINGS.login_account_limit):
            with pytest.raises(AuthenticationError):
                await service.login(
                    email=EMAIL, password="wrong", ip="1.2.3.4", user_agent="pytest"
                )
        # The account is now locked. Even the *correct* password must be
        # refused with the identical generic message -- a distinct "locked"
        # response would itself be an account-existence/state oracle.
        with pytest.raises(AuthenticationError) as locked:
            await service.login(email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest")
        with pytest.raises(AuthenticationError) as wrong_pw:
            await service.login(
                email=EMAIL, password="something else", ip="1.2.3.4", user_agent="pytest"
            )
        assert str(locked.value) == str(wrong_pw.value)

    async def test_a_disabled_user_cannot_log_in(
        self, repo: InMemoryBudgetRepository, service: SessionService, seeded_user: UserRecord
    ) -> None:
        await repo.put_user(replace(seeded_user, status=UserStatus.DISABLED))
        with pytest.raises(AuthenticationError):
            await service.login(email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest")

    async def test_successful_login_clears_prior_failures(
        self, repo: InMemoryBudgetRepository, service: SessionService, seeded_user: UserRecord
    ) -> None:
        with pytest.raises(AuthenticationError):
            await service.login(email=EMAIL, password="wrong", ip="1.2.3.4", user_agent="pytest")
        await service.login(email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest")
        failures, locked_until = await repo.get_login_lockout(seeded_user.email_hash)
        assert failures == 0
        assert locked_until == 0


class TestResolve:
    async def test_a_live_session_resolves_to_the_live_role(
        self, service: SessionService, seeded_user: UserRecord
    ) -> None:
        raw_token, _session = await service.login(
            email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest"
        )
        principal = await service.resolve(raw_token)
        assert principal.user_id == seeded_user.user_id
        assert principal.subject_kind == "user"
        assert principal.agent_id == ""  # a human principal spends no agent's budget
        assert principal.role is Role.OPERATOR

    async def test_role_is_read_live_not_from_the_session_snapshot(
        self,
        repo: InMemoryBudgetRepository,
        service: SessionService,
        seeded_user: UserRecord,
    ) -> None:
        """A demotion must take effect immediately, not at session expiry."""
        raw_token, session = await service.login(
            email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest"
        )
        assert session.role is Role.OPERATOR  # the snapshot, unaffected below

        await repo.put_user(replace(seeded_user, role=Role.VIEWER))

        principal = await service.resolve(raw_token)
        assert principal.role is Role.VIEWER

    async def test_revoked_session_is_rejected(
        self, service: SessionService, seeded_user: UserRecord
    ) -> None:
        raw_token, _session = await service.login(
            email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest"
        )
        await service.logout(raw_token)
        with pytest.raises(AuthenticationError):
            await service.resolve(raw_token)

    async def test_absolutely_expired_session_is_rejected(
        self, clock: ManualClock, service: SessionService, seeded_user: UserRecord
    ) -> None:
        raw_token, _session = await service.login(
            email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest"
        )
        clock.advance(timedelta(minutes=_TEST_SETTINGS.session_ttl_minutes + 1))
        with pytest.raises(AuthenticationError):
            await service.resolve(raw_token)

    async def test_idle_expired_session_is_rejected(
        self, clock: ManualClock, service: SessionService, seeded_user: UserRecord
    ) -> None:
        raw_token, _session = await service.login(
            email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest"
        )
        clock.advance(timedelta(minutes=_TEST_SETTINGS.session_idle_timeout_minutes + 1))
        with pytest.raises(AuthenticationError):
            await service.resolve(raw_token)

    async def test_a_disabled_users_existing_session_stops_resolving(
        self,
        repo: InMemoryBudgetRepository,
        service: SessionService,
        seeded_user: UserRecord,
    ) -> None:
        raw_token, _session = await service.login(
            email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="pytest"
        )
        await repo.put_user(replace(seeded_user, status=UserStatus.DISABLED))
        with pytest.raises(AuthenticationError):
            await service.resolve(raw_token)


class TestRevocation:
    async def test_revoke_all_for_user_keeps_the_excepted_token(
        self, service: SessionService, seeded_user: UserRecord
    ) -> None:
        keep_token, _ = await service.login(
            email=EMAIL, password=PASSWORD, ip="1.2.3.4", user_agent="a"
        )
        drop_token, _ = await service.login(
            email=EMAIL, password=PASSWORD, ip="1.2.3.5", user_agent="b"
        )

        revoked = await service.revoke_all_for_user(
            seeded_user.tenant_id, seeded_user.user_id, except_raw_token=keep_token
        )
        assert revoked == 1

        await service.resolve(keep_token)  # still live; raises if not
        with pytest.raises(AuthenticationError):
            await service.resolve(drop_token)

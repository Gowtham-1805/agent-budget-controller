"""Proves the credential store behaves identically on both backends.

Uses the same ``backend`` fixture (parametrized memory/dynamo) as
``test_repository_contract.py`` -- every test in this file runs against both,
so a behaviour asserted here is true of the in-memory store *and* of real
DynamoDB semantics as implemented by moto.
"""

from __future__ import annotations

from datetime import UTC, datetime

from abc_gateway.auth.identity import ApiKeyRecord, hash_key
from abc_gateway.domain.auth_session import AuthSession
from abc_gateway.domain.user import Role, UserRecord, UserStatus

NOW = datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)


def _user(user_id: str, *, tenant_id: str = "acme", email: str = "a@example.com") -> UserRecord:
    return UserRecord(
        user_id=user_id,
        tenant_id=tenant_id,
        email=email,
        email_hash=hash_key(email),
        password_hash="$argon2id$fake$for$contract$test",
        role=Role.VIEWER,
        status=UserStatus.ACTIVE,
        created_at=NOW,
        password_changed_at=NOW,
    )


def _session(token_hash: str, *, user_id: str, tenant_id: str = "acme") -> AuthSession:
    from datetime import timedelta

    return AuthSession(
        token_hash=token_hash,
        user_id=user_id,
        tenant_id=tenant_id,
        role=Role.VIEWER,
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        last_seen_at=NOW,
        csrf_token="csrf-token-value",
    )


class TestUserPersistence:
    async def test_create_then_get_by_id_and_by_email(self, backend) -> None:
        user = _user("u1", email="viewer@example.com")
        assert await backend.create_user(user) is True

        by_id = await backend.get_user(user.tenant_id, user.user_id)
        by_email = await backend.get_user_by_email_hash(user.email_hash)
        assert by_id == user
        assert by_email == user

    async def test_duplicate_email_is_refused_on_both_backends(self, backend) -> None:
        first = _user("u1", email="dup@example.com")
        second = _user("u2", email="dup@example.com")
        assert await backend.create_user(first) is True
        assert await backend.create_user(second) is False
        # The first account is untouched.
        assert (await backend.get_user_by_email_hash(first.email_hash)).user_id == "u1"

    async def test_put_user_updates_password_without_disturbing_the_index(self, backend) -> None:
        from dataclasses import replace

        user = _user("u1", email="update@example.com")
        await backend.create_user(user)

        updated = replace(user, password_hash="$argon2id$new$hash")
        await backend.put_user(updated)

        fetched = await backend.get_user(user.tenant_id, user.user_id)
        assert fetched.password_hash == "$argon2id$new$hash"
        # The email index still resolves to the same account.
        via_email = await backend.get_user_by_email_hash(user.email_hash)
        assert via_email.user_id == user.user_id

    async def test_list_users_is_tenant_scoped(self, backend) -> None:
        a = _user("a1", tenant_id="tenant-a", email="a@tenant-a.example.com")
        b = _user("b1", tenant_id="tenant-b", email="b@tenant-b.example.com")
        await backend.create_user(a)
        await backend.create_user(b)

        tenant_a_users = await backend.list_users("tenant-a")
        assert [u.user_id for u in tenant_a_users] == ["a1"]

    async def test_unknown_user_and_unknown_email_return_none(self, backend) -> None:
        assert await backend.get_user("acme", "nonexistent") is None
        assert await backend.get_user_by_email_hash("nonexistent-hash") is None


class TestLoginCounters:
    async def test_count_increases_and_resets_after_window(self, backend) -> None:
        h = "counter-hash-1"
        first, _ = await backend.record_login_failure(
            h,
            at_epoch=1000,
            window_seconds=900,
            lockout_threshold=1000,
            lockout_base_seconds=60,
            lockout_cap_seconds=900,
        )
        second, _ = await backend.record_login_failure(
            h,
            at_epoch=1001,
            window_seconds=900,
            lockout_threshold=1000,
            lockout_base_seconds=60,
            lockout_cap_seconds=900,
        )
        assert (first, second) == (1, 2)

        # Beyond the window: resets to 1.
        third, _ = await backend.record_login_failure(
            h,
            at_epoch=1001 + 901,
            window_seconds=900,
            lockout_threshold=1000,
            lockout_base_seconds=60,
            lockout_cap_seconds=900,
        )
        assert third == 1

    async def test_lockout_trips_at_threshold_and_is_capped(self, backend) -> None:
        h = "counter-hash-lockout"
        count = locked_until = 0
        for i in range(5):
            count, locked_until = await backend.record_login_failure(
                h,
                at_epoch=1000 + i,
                window_seconds=900,
                lockout_threshold=5,
                lockout_base_seconds=60,
                lockout_cap_seconds=200,
            )
        assert count == 5
        assert locked_until > 0
        assert locked_until - (1000 + 4) <= 200  # never exceeds the cap

    async def test_clear_resets_to_a_fresh_window(self, backend) -> None:
        h = "counter-hash-clear"
        await backend.record_login_failure(
            h,
            at_epoch=1000,
            window_seconds=900,
            lockout_threshold=1000,
            lockout_base_seconds=60,
            lockout_cap_seconds=900,
        )
        await backend.clear_login_failures(h)
        failures, locked_until = await backend.get_login_lockout(h)
        assert (failures, locked_until) == (0, 0)

        count, _ = await backend.record_login_failure(
            h,
            at_epoch=1001,
            window_seconds=900,
            lockout_threshold=1000,
            lockout_base_seconds=60,
            lockout_cap_seconds=900,
        )
        assert count == 1


class TestSessionPersistence:
    async def test_put_then_get_returns_an_equal_session(self, backend) -> None:
        session = _session("token-hash-1", user_id="u1")
        await backend.put_auth_session(session)
        fetched = await backend.get_auth_session("token-hash-1")
        assert fetched == session

    async def test_unknown_token_hash_returns_none(self, backend) -> None:
        assert await backend.get_auth_session("no-such-token-hash") is None

    async def test_revoke_is_idempotent_true_then_false(self, backend) -> None:
        session = _session("token-hash-2", user_id="u1")
        await backend.put_auth_session(session)

        assert await backend.revoke_auth_session("token-hash-2") is True
        assert await backend.revoke_auth_session("token-hash-2") is False

    async def test_revoking_an_unknown_session_returns_false(self, backend) -> None:
        assert await backend.revoke_auth_session("never-existed") is False

    async def test_touch_updates_last_seen(self, backend) -> None:
        session = _session("token-hash-3", user_id="u1")
        await backend.put_auth_session(session)
        await backend.touch_auth_session("token-hash-3", last_seen_epoch=999_999)
        fetched = await backend.get_auth_session("token-hash-3")
        assert int(fetched.last_seen_at.timestamp()) == 999_999

    async def test_touching_an_unknown_session_does_not_raise(self, backend) -> None:
        await backend.touch_auth_session("never-existed", last_seen_epoch=1)

    async def test_revoke_user_sessions_honours_the_exception(self, backend) -> None:
        keep = _session("token-keep", user_id="u1")
        drop = _session("token-drop", user_id="u1")
        other_user = _session("token-other-user", user_id="u2")
        await backend.put_auth_session(keep)
        await backend.put_auth_session(drop)
        await backend.put_auth_session(other_user)

        revoked = await backend.revoke_user_sessions(
            "acme", "u1", except_token_hash="token-keep"
        )
        assert revoked == 1

        assert (await backend.get_auth_session("token-keep")).revoked is False
        assert (await backend.get_auth_session("token-drop")).revoked is True
        assert (await backend.get_auth_session("token-other-user")).revoked is False


class TestApiKeyPersistence:
    def _key(self, key_hash: str, *, tenant_id: str = "acme") -> ApiKeyRecord:
        return ApiKeyRecord(
            key_id=key_hash[:12],
            key_hash=key_hash,
            tenant_id=tenant_id,
            team_id="engineering",
            agent_id="agent-1",
        )

    async def test_put_then_get_by_hash(self, backend) -> None:
        record = self._key("apikey-hash-1")
        await backend.put_api_key(record)
        fetched = await backend.get_api_key_by_hash("apikey-hash-1")
        assert fetched == record

    async def test_unknown_key_hash_returns_none(self, backend) -> None:
        assert await backend.get_api_key_by_hash("no-such-key") is None

    async def test_list_api_keys_is_tenant_scoped(self, backend) -> None:
        await backend.put_api_key(self._key("k1", tenant_id="tenant-a"))
        await backend.put_api_key(self._key("k2", tenant_id="tenant-b"))
        tenant_a_keys = await backend.list_api_keys("tenant-a")
        assert [k.key_hash for k in tenant_a_keys] == ["k1"]


class TestReadinessMarker:
    async def test_has_any_credential_false_until_something_exists(self, backend) -> None:
        assert await backend.has_any_credential() is False
        await backend.create_user(_user("u1", email="marker@example.com"))
        assert await backend.has_any_credential() is True

    async def test_has_any_credential_true_after_an_api_key(self, backend) -> None:
        assert await backend.has_any_credential() is False
        await backend.put_api_key(
            ApiKeyRecord(
                key_id="k", key_hash="marker-key-hash", tenant_id="acme",
                team_id="eng", agent_id="agent-1",
            )
        )
        assert await backend.has_any_credential() is True

"""Human login: password verification, session issuance, and resolution.

Orchestrates the pieces that stay deliberately separate elsewhere in this
package -- :mod:`.passwords` (hashing only), :mod:`.ratelimit` (the per-IP
tier only) and the repository's durable per-account counter -- into one login
flow whose every failure path returns the identical generic error. That
uniformity is not a style choice: unknown email, wrong password, and a locked
account must be indistinguishable to the caller, or the account-existence and
lockout-state oracles the rest of this module works to close reopen at the
call site instead.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import replace
from datetime import timedelta
from typing import Any

from ..config.settings import Settings
from ..domain.auth_session import AuthSession
from ..domain.clock import Clock
from ..domain.user import Role, normalize_email
from .identity import AuthenticationError, AuthorizationError, Principal, hash_key
from .passwords import PasswordService
from .ratelimit import LoginThrottle


class SessionService:
    """Mints, resolves, and revokes human login sessions."""

    def __init__(
        self,
        *,
        repository: Any,
        passwords: PasswordService,
        throttle: LoginThrottle,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self.repo = repository
        self.passwords = passwords
        self.throttle = throttle
        self.clock = clock
        self.settings = settings

    async def login(
        self, *, email: str, password: str, ip: str, user_agent: str
    ) -> tuple[str, AuthSession]:
        """Verify credentials and mint a session.

        Returns ``(raw_token, session)``. The raw token exists only for the
        instant it takes the caller to set it as a cookie -- only its hash is
        ever persisted.
        """
        now = self.clock.now()
        now_epoch = int(now.timestamp())

        # Tier 1: per-IP, before any lookup or hashing.
        if not self.throttle.allow(ip):
            raise AuthenticationError("invalid email or password")
        self.throttle.record(ip)

        normalized = normalize_email(email)
        email_hash = hash_key(normalized)

        # Tier 2: durable per-account lockout. Checked before verification so
        # a locked account never even reaches the (already-failing) password
        # check -- but the dummy hash is still verified below regardless, so
        # this branch's response time matches every other failure's.
        _failures, locked_until = await self.repo.get_login_lockout(email_hash)
        if locked_until and now_epoch < locked_until:
            self.passwords.verify(None, password)
            raise AuthenticationError("invalid email or password")

        user = await self.repo.get_user_by_email_hash(email_hash)
        stored_hash = user.password_hash if user is not None else None
        password_ok = self.passwords.verify(stored_hash, password)

        if not password_ok or user is None or not user.is_active:
            await self.repo.record_login_failure(
                email_hash,
                at_epoch=now_epoch,
                window_seconds=self.settings.login_account_window_seconds,
                lockout_threshold=self.settings.login_account_limit,
                lockout_base_seconds=60,
                lockout_cap_seconds=self.settings.login_account_lockout_cap_seconds,
            )
            raise AuthenticationError("invalid email or password")

        await self.repo.clear_login_failures(email_hash)

        # A correctness write (upgrading a weaker historical hash to today's
        # parameters), not telemetry -- but off the enforcement path, so it
        # never blocks the response if it were slow.
        if self.passwords.needs_rehash(user.password_hash):
            await self.repo.put_user(replace(user, password_hash=self.passwords.hash(password)))

        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(24)
        session = AuthSession(
            token_hash=hash_key(raw_token),
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            role=user.role,
            issued_at=now,
            expires_at=now + timedelta(minutes=self.settings.session_ttl_minutes),
            last_seen_at=now,
            csrf_token=csrf_token,
            created_ip_hash=hash_key(ip) if ip else "",
            user_agent_fingerprint=hash_key(user_agent)[:16] if user_agent else "",
        )
        await self.repo.put_auth_session(session)
        return raw_token, session

    async def resolve(self, raw_token: str | None) -> Principal:
        """Resolve a session token to a live, trusted human principal.

        Role is re-read from the *user record* on every call, never taken
        from the session's own snapshot -- otherwise demoting or disabling a
        user would not take effect until their existing sessions expired on
        their own.
        """
        if not raw_token:
            raise AuthenticationError("missing credential")

        token_hash = hash_key(raw_token)
        session = await self.repo.get_auth_session(token_hash)
        now = self.clock.now()
        if session is None or not session.is_live_at(now):
            raise AuthenticationError("session expired or revoked")

        idle_cutoff = session.last_seen_at + timedelta(
            minutes=self.settings.session_idle_timeout_minutes
        )
        if now > idle_cutoff:
            raise AuthenticationError("session expired or revoked")

        user = await self.repo.get_user(session.tenant_id, session.user_id)
        if user is None or not user.is_active:
            raise AuthenticationError("session expired or revoked")

        await self.repo.touch_auth_session(token_hash, last_seen_epoch=int(now.timestamp()))

        return Principal(
            tenant_id=user.tenant_id,
            team_id="",
            agent_id="",
            key_id=token_hash[:12],
            is_admin=user.role is Role.ADMIN,
            role=user.role,
            user_id=user.user_id,
            subject_kind="user",
        )

    async def logout(self, raw_token: str) -> None:
        await self.repo.revoke_auth_session(hash_key(raw_token))

    async def revoke_all_for_user(
        self, tenant_id: str, user_id: str, *, except_raw_token: str | None = None
    ) -> int:
        except_hash = hash_key(except_raw_token) if except_raw_token else None
        count: int = await self.repo.revoke_user_sessions(
            tenant_id, user_id, except_token_hash=except_hash
        )
        return count

    async def get_session_for_csrf(self, raw_token: str) -> AuthSession | None:
        """Fetch the session record itself, for the CSRF double-submit check.

        Kept separate from :meth:`resolve` (which returns a ``Principal``) so
        the CSRF middleware layer never has to re-derive the token hash.
        """
        session: AuthSession | None = await self.repo.get_auth_session(hash_key(raw_token))
        return session

    @staticmethod
    def verify_csrf(session: AuthSession, header_token: str | None) -> None:
        """Double-submit check for cookie-authenticated, state-changing calls.

        Only required when the credential arrived as an ambient ``Cookie:``
        header -- one a browser attaches automatically to a same-origin
        request without any script needing to read it. A credential presented
        via a custom header (``X-ABC-Session``, as the dashboard's own
        server-side proxy uses) cannot be attached this way by a third-party
        page, so it needs no separate CSRF token.
        """
        if not header_token or not hmac.compare_digest(header_token, session.csrf_token):
            raise AuthorizationError("missing or invalid CSRF token")

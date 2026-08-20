"""Human login sessions.

A session is addressed by the hash of its bearer token, exactly like an agent
API key (``auth/identity.py``): the raw token is never persisted, only its
SHA-256 digest, so a leaked database dump does not hand out live sessions.

``role`` is carried on the session purely as a point-in-time snapshot for
logging and audit. It must never be the authority for what a request is
allowed to do -- :func:`..auth.sessions.SessionService.resolve` re-reads the
live role from the :class:`~.user.UserRecord` on every request, specifically
so that demoting or disabling a user takes effect immediately rather than
waiting for their existing sessions to expire.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .user import Role


@dataclass(frozen=True, slots=True)
class AuthSession:
    token_hash: str
    user_id: str
    tenant_id: str
    role: Role
    issued_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    csrf_token: str
    revoked: bool = False
    created_ip_hash: str = ""
    user_agent_fingerprint: str = ""

    def is_live_at(self, now: datetime) -> bool:
        return not self.revoked and now < self.expires_at

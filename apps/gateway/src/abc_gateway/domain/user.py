"""Human operators.

A user is a distinct concept from a :class:`~.agent.AgentState`: an agent is a
machine principal that spends money against a budget, while a user is a human
who operates the control plane -- creating teams, setting budgets, pausing
agents. The two never share a credential or a session, and a user principal
carries no ``agent_id`` (see :mod:`..auth.identity`), so a human can never
spend an agent's budget by presenting a session cookie to the data plane.

Roles are ordered, not a flat set, because every authorization check in this
system is "does this principal meet a *minimum* bar", never "does this
principal have exactly this role".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    """Ordered from least to most privileged."""

    #: A machine principal (an agent's API key). No control-plane access at all.
    AGENT = "AGENT"
    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"


#: Numeric rank for "at least" comparisons. Deliberately not the enum's
#: declaration order (StrEnum has no ordering of its own) or its string value.
ROLE_RANK: dict[Role, int] = {
    Role.AGENT: 0,
    Role.VIEWER: 1,
    Role.OPERATOR: 2,
    Role.ADMIN: 3,
}


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


@dataclass(frozen=True, slots=True)
class UserRecord:
    """A human operator account.

    ``email_hash`` is the lookup address (see ``repo/keys.py``'s tenant-less
    email index) so that account uniqueness and the login-by-email flow do not
    require the caller to already know their tenant. ``password_hash`` is the
    full encoded Argon2id string (algorithm, version, params and salt are
    embedded in it) -- never a bare digest.
    """

    user_id: str
    tenant_id: str
    email: str
    email_hash: str
    password_hash: str
    role: Role
    status: UserStatus
    created_at: datetime
    password_changed_at: datetime
    display_name: str = ""
    #: Reserved for a future MFA implementation. Unused today, but present now
    #: so enabling MFA later is a new column, not a migration of this one.
    mfa_secret_ref: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE

    def has_at_least(self, minimum: Role) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]


def normalize_email(raw: str) -> str:
    """The canonical form of an email address for lookup and storage.

    Case-insensitive and trimmed, so ``Alice@Example.com`` and
    ``alice@example.com `` collide on the same account rather than silently
    creating two.
    """
    return raw.strip().casefold()

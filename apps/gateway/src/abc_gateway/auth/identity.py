"""Governance identity resolution.

The single rule: **a caller never asserts who it is.** A header like

    X-Agent-ID: cheap-agent

is worthless as identity, because a caller that wants a bigger budget just sends
a different value. Any agent could spend any other agent's money, and the entire
hierarchy would be decorative.

Instead a trusted credential is mapped server-side:

    API key / JWT subject / workload identity
        -> tenant -> team -> agent

A session id *may* come from the client, because it is a correlation handle
rather than an authorisation -- but it is verified to belong to the
authenticated agent before it is honoured, so one agent cannot spend from
another's session.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Literal

from ..domain.user import ROLE_RANK, Role


class AuthenticationError(Exception):
    """The credential was missing, malformed, or unknown."""


class AuthorizationError(Exception):
    """The credential is valid but not entitled to what was requested."""


@dataclass(frozen=True, slots=True)
class Principal:
    """A resolved, trusted governance identity.

    Two shapes share this type: a machine principal (an agent's API key,
    ``subject_kind="agent"``) and a human principal (a logged-in user's
    session, ``subject_kind="user"``). A human principal always carries
    ``agent_id=""`` -- :meth:`require_agent` is what stops a logged-in human
    from spending an agent's budget by presenting their session cookie to the
    data plane, which is the specific hole a naive "accept a session
    everywhere a key works" implementation would open.
    """

    tenant_id: str
    team_id: str
    agent_id: str
    key_id: str
    is_admin: bool = False
    role: Role = Role.AGENT
    user_id: str | None = None
    subject_kind: Literal["agent", "user"] = "agent"

    def require_admin(self) -> None:
        if self.role is not Role.ADMIN:
            raise AuthorizationError("this operation requires an administrative credential")

    def require_role(self, minimum: Role) -> None:
        if ROLE_RANK[self.role] < ROLE_RANK[minimum]:
            raise AuthorizationError(f"this operation requires at least the {minimum.value} role")

    def require_agent(self) -> None:
        """The data plane is agent-only: a human session may never spend it."""
        if self.subject_kind != "agent":
            raise AuthorizationError("this operation requires an agent credential")


@dataclass(frozen=True, slots=True)
class ApiKeyRecord:
    """A credential and the identity it maps to."""

    key_id: str
    key_hash: str
    tenant_id: str
    team_id: str
    agent_id: str
    is_admin: bool = False


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class IdentityResolver:
    """Maps agent API-key credentials to governance identities.

    A small in-process cache backs the hot path (every data-plane request
    resolves a credential here), with the repository as the durable source of
    truth: a key minted via ``POST /v1/agents/{id}/keys`` is persisted there
    via :meth:`persist`, so it survives a restart and is visible to every
    gateway instance -- not just the one that minted it, which is what the
    in-memory-only registry this replaced could not do. A cache miss falls
    through to one repository read and warms the cache for next time.
    """

    def __init__(
        self,
        records: list[ApiKeyRecord] | None = None,
        *,
        repository: Any = None,
    ) -> None:
        self._by_hash: dict[str, ApiKeyRecord] = {}
        self._repository = repository
        for record in records or []:
            self._by_hash[record.key_hash] = record

    def register(self, record: ApiKeyRecord) -> None:
        """Add a credential to the in-process cache only."""
        self._by_hash[record.key_hash] = record

    def register_raw(
        self,
        raw_key: str,
        *,
        tenant_id: str,
        team_id: str,
        agent_id: str,
        key_id: str = "",
        is_admin: bool = False,
    ) -> ApiKeyRecord:
        """Register a new credential in the in-process cache, synchronously.

        Used for process-local bootstrap (the admin key from settings, which
        is reconstructed from the environment on every startup regardless of
        persistence). For a credential that must survive a restart or be
        visible to other instances -- an agent key minted through the public
        API -- call :meth:`persist` as well.
        """
        record = ApiKeyRecord(
            key_id=key_id or hash_key(raw_key)[:12],
            key_hash=hash_key(raw_key),
            tenant_id=tenant_id,
            team_id=team_id,
            agent_id=agent_id,
            is_admin=is_admin,
        )
        self.register(record)
        return record

    async def persist(self, record: ApiKeyRecord) -> None:
        """Write a credential through to the repository, if one is wired."""
        if self._repository is not None:
            await self._repository.put_api_key(record)

    async def resolve(self, raw_key: str | None) -> Principal:
        """Resolve a bearer credential to a governance identity."""
        if not raw_key:
            raise AuthenticationError("missing credential")

        candidate = hash_key(raw_key)
        # Constant-time comparison across the registry: a timing side channel
        # here would leak which key prefixes are valid.
        matched: ApiKeyRecord | None = None
        for key_hash, record in self._by_hash.items():
            if hmac.compare_digest(candidate, key_hash):
                matched = record

        if matched is None and self._repository is not None:
            found = await self._repository.get_api_key_by_hash(candidate)
            if found is not None and hmac.compare_digest(candidate, found.key_hash):
                self._by_hash[found.key_hash] = found  # warm the cache
                matched = found

        if matched is None:
            raise AuthenticationError("unknown credential")

        return Principal(
            tenant_id=matched.tenant_id,
            team_id=matched.team_id,
            agent_id=matched.agent_id,
            key_id=matched.key_id,
            is_admin=matched.is_admin,
            role=Role.ADMIN if matched.is_admin else Role.AGENT,
            subject_kind="agent",
        )

    async def has_any_credential(self) -> bool:
        """Whether at least one agent key is known, cache or repository.

        Used by ``Container.readiness()`` alongside the human-auth bootstrap
        checks -- this alone does not need to be true for the instance to be
        considered configured.
        """
        if self._by_hash:
            return True
        if self._repository is not None:
            return bool(await self._repository.has_any_credential())
        return False

    def __len__(self) -> int:
        return len(self._by_hash)


def verify_session_ownership(principal: Principal, session: Any) -> None:
    """Confirm a client-supplied session belongs to the authenticated agent.

    A session id is a correlation handle, not an authorisation. Without this
    check an agent could pass someone else's session id and spend from their
    budget -- and because session budgets are usually the smallest and most
    permissive scope, that is a soft target.
    """
    if session is None:
        raise AuthorizationError("unknown session")
    if session.tenant_id != principal.tenant_id:
        raise AuthorizationError("session belongs to a different tenant")
    if session.agent_id != principal.agent_id:
        raise AuthorizationError("session belongs to a different agent")

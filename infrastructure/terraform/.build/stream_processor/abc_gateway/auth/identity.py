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
from typing import Any


class AuthenticationError(Exception):
    """The credential was missing, malformed, or unknown."""


class AuthorizationError(Exception):
    """The credential is valid but not entitled to what was requested."""


@dataclass(frozen=True, slots=True)
class Principal:
    """A resolved, trusted governance identity."""

    tenant_id: str
    team_id: str
    agent_id: str
    key_id: str
    is_admin: bool = False

    def require_admin(self) -> None:
        if not self.is_admin:
            raise AuthorizationError("this operation requires an administrative credential")


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
    """Maps credentials to governance identities.

    Backed by an in-memory registry here. A production deployment would load
    these from DynamoDB or an identity provider; the interface is the same and
    the security property -- identity comes from the credential, never from the
    request body -- does not change.
    """

    def __init__(self, records: list[ApiKeyRecord] | None = None) -> None:
        self._by_hash: dict[str, ApiKeyRecord] = {}
        for record in records or []:
            self._by_hash[record.key_hash] = record

    def register(self, record: ApiKeyRecord) -> None:
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

    def resolve(self, raw_key: str | None) -> Principal:
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

        if matched is None:
            raise AuthenticationError("unknown credential")

        return Principal(
            tenant_id=matched.tenant_id,
            team_id=matched.team_id,
            agent_id=matched.agent_id,
            key_id=matched.key_id,
            is_admin=matched.is_admin,
        )

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

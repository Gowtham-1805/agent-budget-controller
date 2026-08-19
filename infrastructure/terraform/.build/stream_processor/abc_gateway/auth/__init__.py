"""Identity and authorization.

Governance identity is derived from a trusted credential, never from a
client-supplied header.
"""

from .identity import (
    ApiKeyRecord,
    AuthenticationError,
    AuthorizationError,
    IdentityResolver,
    Principal,
    hash_key,
    verify_session_ownership,
)

__all__ = [
    "ApiKeyRecord",
    "AuthenticationError",
    "AuthorizationError",
    "IdentityResolver",
    "Principal",
    "hash_key",
    "verify_session_ownership",
]

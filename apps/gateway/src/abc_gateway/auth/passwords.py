"""Argon2id hashing for human passwords.

Argon2id, not the SHA-256 used for agent API keys in :mod:`.identity`, and
deliberately not shared with it: agent keys are 256-bit random secrets with no
guessable distribution, so a slow memory-hard KDF buys them nothing and would
put ~100 ms of CPU on the inference hot path -- directly at odds with the
reserve-before-inference latency budget. Argon2id exists for exactly the
opposite case: a low-entropy, human-chosen secret that must be expensive to
brute-force offline.

Parameters come from ``Settings`` rather than being hardcoded, so tests can use
a cheap profile (see ``tests/unit/test_password_hashing.py``) without the
*production* defaults ever moving -- a unit test pins those defaults to a
floor so the cheap test profile can never silently become what actually ships.
"""

from __future__ import annotations

import contextlib

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

from ..config.settings import Settings

#: A password no account will ever have. Hashed once per process and verified
#: on every login attempt against an unknown email, so the wall-clock cost of
#: "no such user" is indistinguishable from "wrong password" -- otherwise
#: response time alone would be an account-existence oracle.
_DUMMY_PASSWORD = "no-such-account-000000000000000000000000"


def build_hasher(settings: Settings) -> PasswordHasher:
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost_kib,
        parallelism=settings.argon2_parallelism,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


class PasswordService:
    """Hashes and verifies passwords with one process-wide Argon2id profile."""

    def __init__(self, settings: Settings) -> None:
        self._hasher = build_hasher(settings)
        self._dummy_hash = self._hasher.hash(_DUMMY_PASSWORD)
        #: Test hook only: counts how many times the dummy-verify path ran, so
        #: enumeration-resistance tests can assert it happened without relying
        #: on flaky wall-clock timing assertions.
        self.dummy_verify_calls = 0

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, stored_hash: str | None, password: str) -> bool:
        """True iff ``password`` matches ``stored_hash``.

        ``stored_hash`` is None when no account matched the login email. The
        dummy hash is verified anyway -- never short-circuit this -- so the
        two cases cost the same amount of CPU time.
        """
        if stored_hash is None:
            self.dummy_verify_calls += 1
            with contextlib.suppress(VerificationError, InvalidHashError):
                self._hasher.verify(self._dummy_hash, password)
            return False
        try:
            return bool(self._hasher.verify(stored_hash, password))
        except (VerificationError, InvalidHashError):
            # VerificationError covers both a wrong password (its subclass
            # VerifyMismatchError) and a malformed/foreign hash string that
            # fails to decode -- both mean "not a match", never a crash.
            return False

    def needs_rehash(self, stored_hash: str) -> bool:
        """True if ``stored_hash`` was produced with weaker-than-current params.

        Callers should re-hash and persist on the next successful login when
        this is true -- a correctness write, not telemetry, but one that never
        blocks the response (see ``auth/sessions.py``).
        """
        return bool(self._hasher.check_needs_rehash(stored_hash))

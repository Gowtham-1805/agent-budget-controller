"""Concurrency proofs for the credential repository's write paths.

Per CLAUDE.md and docs/FINDINGS.md #F1: ``asyncio.gather`` over these methods
would not test anything, because their critical sections are synchronous
(``with self.store._lock: ...``) and contain no ``await`` -- the event loop
would run each call to completion before starting the next one, and a race
test written that way passes against a deliberately broken implementation.
``run_concurrently`` uses real OS threads instead, released together by a
barrier, so the critical sections genuinely overlap.

Both tests below were validated by mutation: removing the guard each asserts
on made the corresponding test fail, and restoring it made the test pass
again.

* ``test_concurrent_create_user_one_email_one_winner`` -- disabling the
  email-uniqueness check in ``create_user`` produced 8 winners instead of 1.
* ``test_concurrent_login_failures_no_lost_update`` -- replacing
  ``store._lock`` with a fresh, non-shared ``Lock()`` per call did *not*
  reproduce a lost update on its own: the critical section is short enough
  that 8 real OS threads released by one barrier usually still serialise
  through it by chance, which would have made this a vacuous test in the
  same way ``docs/FINDINGS.md #F1`` describes for ``asyncio.gather``. Adding
  a 20ms ``sleep`` between the read and the write -- inside the disabled
  lock, widening the race window the same way ``PhaseGate`` forces a
  pathological schedule elsewhere in this suite -- reliably reproduced 8
  lost updates (every thread returned 1). Both mutations were reverted after
  confirming the failure.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.support.concurrency import run_concurrently

from abc_gateway.domain.user import Role, UserRecord, UserStatus
from abc_gateway.repo.memory import InMemoryBudgetRepository

pytestmark = pytest.mark.serial

NOW = datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)


def _user(user_id: str, *, email: str, email_hash: str) -> UserRecord:
    return UserRecord(
        user_id=user_id,
        tenant_id="acme",
        email=email,
        email_hash=email_hash,
        password_hash="irrelevant-for-this-test",
        role=Role.VIEWER,
        status=UserStatus.ACTIVE,
        created_at=NOW,
        password_changed_at=NOW,
    )


def test_concurrent_create_user_one_email_one_winner() -> None:
    """N threads race to register the same email. Exactly one must win.

    Without the email-index conditional put, two threads could both observe
    "no existing index item" and both succeed, silently creating two accounts
    that share one login identity -- whichever logged in last would own the
    email going forward, and the other account would become unreachable by
    any credential a human could type in.
    """
    repo = InMemoryBudgetRepository()
    count = 8
    email_hash = "shared-email-hash"

    async def attempt(index: int) -> bool:
        return await repo.create_user(
            _user(f"user-{index}", email="race@example.com", email_hash=email_hash)
        )

    results = run_concurrently(attempt, count)

    assert sum(results) == 1, f"expected exactly one winner, got {results}"


def test_concurrent_login_failures_no_lost_update() -> None:
    """N threads record a login failure for the same account simultaneously.

    The durable per-account counter is the tier that actually bounds guess
    rate (auth/ratelimit.py's per-IP tier is trivially evaded by rotating
    source IPs). If the increment loses updates under concurrency, an
    attacker distributing guesses across N simultaneous connections gets N
    free attempts per counted increment -- the exact race that would make the
    lockout threshold meaningless.
    """
    repo = InMemoryBudgetRepository()
    count = 8
    email_hash = "shared-failure-hash"
    at_epoch = int(NOW.timestamp())

    async def attempt(_index: int) -> int:
        new_count, _locked_until = await repo.record_login_failure(
            email_hash,
            at_epoch=at_epoch,
            window_seconds=900,
            lockout_threshold=1000,  # high enough that lockout never trips here
            lockout_base_seconds=60,
            lockout_cap_seconds=900,
        )
        return new_count

    results = run_concurrently(attempt, count)

    assert sorted(results) == list(range(1, count + 1)), (
        f"expected the exact set 1..{count} with no duplicates or gaps, got {sorted(results)}"
    )

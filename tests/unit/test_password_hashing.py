"""Argon2id password hashing (auth/passwords.py)."""

from __future__ import annotations

from argon2.low_level import Type

from abc_gateway.auth.passwords import PasswordService, build_hasher
from abc_gateway.config.settings import Settings

#: A cheap profile so this file doesn't spend real Argon2 time in CI. The
#: production floor is asserted separately below, against Settings()'s own
#: defaults -- never against this cheap instance -- so the cheap profile can
#: never silently become what actually ships.
_TEST_SETTINGS = Settings(argon2_time_cost=1, argon2_memory_cost_kib=8192, argon2_parallelism=1)


def test_hash_and_verify_round_trip() -> None:
    service = PasswordService(_TEST_SETTINGS)
    stored = service.hash("correct horse battery staple")
    assert service.verify(stored, "correct horse battery staple") is True


def test_wrong_password_is_rejected() -> None:
    service = PasswordService(_TEST_SETTINGS)
    stored = service.hash("correct horse battery staple")
    assert service.verify(stored, "wrong password") is False


def test_two_hashes_of_the_same_password_differ() -> None:
    """Salted: identical passwords must never produce identical hashes."""
    service = PasswordService(_TEST_SETTINGS)
    first = service.hash("correct horse battery staple")
    second = service.hash("correct horse battery staple")
    assert first != second
    assert service.verify(first, "correct horse battery staple")
    assert service.verify(second, "correct horse battery staple")


def test_verify_against_unknown_hash_returns_false_not_raises() -> None:
    service = PasswordService(_TEST_SETTINGS)
    assert service.verify("$argon2id$not-a-real-hash", "anything") is False


def test_verify_with_no_stored_hash_runs_the_dummy_path() -> None:
    """The unknown-account path still costs a real Argon2 verify.

    This is half of the enumeration-resistance property (auth/sessions.py
    owns the other half: an identical error response). If an unknown email
    short-circuited here, the response would arrive measurably faster than a
    wrong-password response, which is itself an account-existence oracle.
    """
    service = PasswordService(_TEST_SETTINGS)
    before = service.dummy_verify_calls
    assert service.verify(None, "anything") is False
    assert service.dummy_verify_calls == before + 1


def test_needs_rehash_true_when_parameters_strengthen() -> None:
    weak = Settings(argon2_time_cost=1, argon2_memory_cost_kib=8192, argon2_parallelism=1)
    strong = Settings(argon2_time_cost=3, argon2_memory_cost_kib=65536, argon2_parallelism=4)

    weak_service = PasswordService(weak)
    stored = weak_service.hash("correct horse battery staple")

    strong_service = PasswordService(strong)
    assert strong_service.needs_rehash(stored) is True
    # A hash produced under the strong service's own parameters needs no
    # upgrade against itself.
    assert strong_service.needs_rehash(strong_service.hash("x")) is False


def test_production_defaults_meet_the_security_floor() -> None:
    """The floor this repo actually ships, not the cheap profile tests use.

    If this regresses, every future test run still passes -- the point of a
    fixed floor is that it is checked against ``Settings()``'s own defaults,
    independent of whatever profile any individual test happens to construct.
    """
    defaults = Settings()
    hasher = build_hasher(defaults)
    assert hasher.type is Type.ID, "must be Argon2id specifically, not Argon2i or Argon2d"
    assert defaults.argon2_memory_cost_kib >= 19_456, "OWASP floor: >= 19 MiB"
    assert defaults.argon2_time_cost >= 2

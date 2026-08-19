"""Shared test fixtures.

The builders here exist so that a test can say what it is *about* -- "a team
with $1.00 and three agents" -- without restating the whole policy graph each
time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from abc_gateway.domain.agent import AgentState, AgentStatus, RunawayPolicy
from abc_gateway.domain.clock import ManualClock
from abc_gateway.domain.money import Money
from abc_gateway.domain.policy import (
    AgentPolicy,
    BudgetPolicy,
    ModelAllocationPolicy,
    ModelCandidate,
    RoutingPolicy,
)
from abc_gateway.domain.scopes import ScopeType
from abc_gateway.domain.session import Session, SessionStatus
from abc_gateway.domain.tokens import TokenVector
from abc_gateway.domain.window import WindowType
from abc_gateway.engine.budget_engine import BudgetEngine, ReservationRequest
from abc_gateway.engine.scope_resolver import resolve_scopes
from abc_gateway.pricing import load_catalog
from abc_gateway.providers.fake import FakeBehaviour, FakeProvider
from abc_gateway.repo.memory import InMemoryBudgetRepository

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "pricing" / "catalog.json"

TENANT = "acme"
TEAM = "engineering"

#: Fixed so window keys never shift under the tests.
FIXED_NOW = datetime(2026, 8, 19, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(FIXED_NOW)


@pytest.fixture(scope="session")
def catalog():
    return load_catalog(CATALOG_PATH)


@pytest.fixture
def repo() -> InMemoryBudgetRepository:
    return InMemoryBudgetRepository()


@pytest.fixture
def engine(repo: InMemoryBudgetRepository) -> BudgetEngine:
    return BudgetEngine(repo)


@pytest.fixture
def provider(repo: InMemoryBudgetRepository) -> FakeProvider:
    """A fake provider wired to the repository.

    The wiring matters: it is what lets the fake refuse to run for a request
    that holds no live reservation.
    """
    return FakeProvider(
        FakeBehaviour(input_tokens=1000, output_tokens=1000),
        repository=repo,
        tenant_id=TENANT,
    )


# -- policy builders --------------------------------------------------------


def team_policy(limit_usd: str, *, warning_percent: int = 80) -> BudgetPolicy:
    return BudgetPolicy(
        scope_type=ScopeType.TEAM,
        scope_id=TEAM,
        limit=Money.from_usd_str(limit_usd),
        window_type=WindowType.MONTHLY,
        warning_percent=warning_percent,
    )


def agent_policy(
    agent_id: str,
    limit_usd: str,
    *,
    session_budget_usd: str | None = None,
    preferred_model: str = "premium",
    fallback_models: tuple[str, ...] = (),
    allocation_usd: str | None = None,
    max_input_tokens: int | None = None,
    max_output_tokens: int | None = None,
    max_total_tokens: int | None = None,
    default_max_output_tokens: int = 1000,
    warning_percent: int = 80,
) -> AgentPolicy:
    tokens = TokenVector.unlimited()
    if any(v is not None for v in (max_input_tokens, max_output_tokens, max_total_tokens)):
        unlimited = TokenVector.unlimited()
        tokens = TokenVector(
            input=max_input_tokens if max_input_tokens is not None else unlimited.input,
            output=max_output_tokens if max_output_tokens is not None else unlimited.output,
            total=max_total_tokens if max_total_tokens is not None else unlimited.total,
        )

    allocations: tuple[ModelAllocationPolicy, ...] = ()
    if allocation_usd is not None:
        allocations = (
            ModelAllocationPolicy(
                provider="test",
                model=preferred_model,
                limit=Money.from_usd_str(allocation_usd),
            ),
        )

    return AgentPolicy(
        agent_id=agent_id,
        team_id=TEAM,
        tenant_id=TENANT,
        budget=BudgetPolicy(
            scope_type=ScopeType.AGENT,
            scope_id=agent_id,
            limit=Money.from_usd_str(limit_usd),
            window_type=WindowType.MONTHLY,
            tokens=tokens,
            warning_percent=warning_percent,
        ),
        routing=RoutingPolicy(
            preferred=ModelCandidate("test", preferred_model, 32_000),
            fallbacks=tuple(ModelCandidate("test", m, 32_000) for m in fallback_models),
            allocations=allocations,
        ),
        session_budget=(Money.from_usd_str(session_budget_usd) if session_budget_usd else None),
        default_max_output_tokens=default_max_output_tokens,
        runaway=RunawayPolicy(),
    )


def make_session(
    session_id: str,
    agent_id: str,
    limit_usd: str,
    *,
    now: datetime = FIXED_NOW,
    min_viable_usd: str = "0",
) -> Session:
    return Session(
        session_id=session_id,
        tenant_id=TENANT,
        team_id=TEAM,
        agent_id=agent_id,
        status=SessionStatus.OPEN,
        opened_at=now,
        expires_at=now + timedelta(hours=24),
        limit=Money.from_usd_str(limit_usd),
        min_viable=Money.from_usd_str(min_viable_usd),
    )


def active_agent(agent_id: str) -> AgentState:
    return AgentState(agent_id=agent_id, tenant_id=TENANT, team_id=TEAM, status=AgentStatus.ACTIVE)


def build_request(
    *,
    catalog,
    agent: AgentPolicy,
    team: BudgetPolicy,
    session: Session | None = None,
    model: str | None = None,
    input_tokens: int = 1000,
    max_output_tokens: int = 1000,
    now: datetime = FIXED_NOW,
    idempotency_key: str | None = None,
    safety_bps: int = 0,
) -> ReservationRequest:
    """Assemble a governed request the way the API layer would.

    Mirrors the real lifecycle -- count, bound, estimate, resolve scopes -- so
    that engine tests exercise the same arithmetic the gateway will.
    """
    model = model or agent.routing.preferred.model
    price = catalog.get("test", model)
    estimated = price.estimate_worst_case(
        input_tokens=input_tokens,
        max_output_tokens=max_output_tokens,
        safety_bps=safety_bps,
    )
    tokens = TokenVector(
        input=input_tokens,
        output=max_output_tokens,
        total=input_tokens + max_output_tokens,
    )
    scopes = resolve_scopes(
        agent_policy=agent,
        team_policy=team,
        session=session,
        provider="test",
        model=model,
        cost=estimated.total,
        tokens=tokens,
        now=now,
    )
    return ReservationRequest(
        tenant_id=TENANT,
        team_id=TEAM,
        agent_id=agent.agent_id,
        session_id=session.session_id if session else None,
        provider="test",
        requested_model=agent.routing.preferred.model,
        effective_model=model,
        price=price,
        price_catalog_version=catalog.version,
        preflight_input_tokens=input_tokens,
        bounded_max_output_tokens=max_output_tokens,
        scopes=scopes,
        estimated_cost=estimated,
        now=now,
        idempotency_key=idempotency_key,
        check_session_status=session is not None,
    )


@pytest.fixture
def make_request(catalog):
    """Curried :func:`build_request` with the catalog already bound."""

    def _make(**kwargs) -> ReservationRequest:
        return build_request(catalog=catalog, **kwargs)

    return _make


@pytest.fixture
def driver(repo, catalog, provider):
    """Runs governed calls end to end against the in-memory stack."""
    from tests.support.driver import Driver

    return Driver(
        repo=repo,
        catalog=catalog,
        provider=provider,
        tenant_id=TENANT,
        team_id=TEAM,
    )


@pytest.fixture
def policies():
    """Expose the builders to tests without a pile of imports."""
    return type(
        "PolicyBuilders",
        (),
        {
            "team": staticmethod(team_policy),
            "agent": staticmethod(agent_policy),
            "session": staticmethod(make_session),
            "active_agent": staticmethod(active_agent),
        },
    )

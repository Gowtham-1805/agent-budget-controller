"""Application wiring.

One container holds everything a request needs, constructed once at startup. It
also decides which budget store the process uses -- and refuses to start with
the in-memory store in production, because a budget that resets when a task
restarts is not a budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import Header, Request

from ..auth.identity import AuthenticationError, IdentityResolver, Principal
from ..auth.passwords import PasswordService
from ..auth.ratelimit import LoginThrottle
from ..auth.sessions import SessionService
from ..config.settings import Settings
from ..domain.clock import Clock, SystemClock
from ..engine.budget_engine import BudgetEngine
from ..engine.effects import SettlementEffects
from ..engine.routing import RoutingEngine
from ..observability.telemetry import TelemetrySink, build_sink
from ..pricing import PriceCatalog, load_catalog
from ..providers.base import Timeouts
from ..providers.registry import ProviderRegistry
from ..runaway.detector import RunawayDetector
from ..runaway.review import ReviewService
from .service import InferenceService


@dataclass
class Container:
    """Everything the API needs, built once."""

    settings: Settings
    catalog: PriceCatalog
    repository: Any
    engine: BudgetEngine
    router: RoutingEngine
    effects: SettlementEffects
    service: InferenceService
    identity: IdentityResolver
    sessions: SessionService
    review: ReviewService
    detector: RunawayDetector
    telemetry: TelemetrySink
    clock: Clock
    adapters: dict[str, Any] = field(default_factory=dict)
    registry: ProviderRegistry | None = None

    async def readiness(self) -> tuple[bool, dict[str, bool], dict[str, str]]:
        """Whether this instance is safe to govern traffic.

        Deliberately more than "the process is alive". An instance with no price
        catalog cannot compute a reservation, and one that cannot reach the
        budget store cannot enforce anything -- both would accept traffic and
        fail every request, or worse, appear to work.

        Never makes a billable model call: a readiness probe that costs money on
        every check is its own outage.
        """
        # `len(self.identity)` alone would miss every credential that lives
        # only in the repository (another instance's minted keys, or a
        # persisted human user) and would force an unbounded scan if it tried
        # to check the repository directly on every probe -- so this checks
        # the two env-configured bootstrap paths first, and only awaits the
        # identity resolver's own bounded check as a fallback.
        identity_configured = (
            bool(self.settings.admin_api_key)
            or bool(self.settings.bootstrap_admin_email)
            or await self.identity.has_any_credential()
        )
        checks = {
            "price_catalog_loaded": bool(self.catalog and self.catalog.entries),
            "budget_store_reachable": await self._store_ok(),
            "providers_configured": bool(self.adapters),
            "identity_configured": identity_configured,
        }
        prod_count = sum(
            1
            for p in (self.registry.list_providers() if self.registry else [])
            if p.is_production_ready and p.configured and p.enabled
        )
        detail: dict[str, str] = {
            "catalog_version": self.catalog.version if self.catalog else "none",
            "providers": ",".join(sorted(self.adapters)) or "none",
            "production_providers": str(prod_count),
            "store": "memory" if self.settings.use_memory_store else "dynamodb",
        }
        return all(checks.values()), checks, detail

    async def _store_ok(self) -> bool:
        try:
            reachable: bool = await self.repository.health_check()
            return reachable
        except Exception:
            return False


def build_container(settings: Settings, *, clock: Clock | None = None) -> Container:
    """Construct the application graph."""
    catalog = load_catalog(settings.price_catalog_path)
    clock = clock or SystemClock()

    if settings.use_memory_store:
        if settings.is_production:
            # A budget that resets on deploy is not a budget. Fail loudly at
            # startup rather than silently lose every balance on the next
            # rolling restart.
            raise RuntimeError(
                "the in-memory budget store cannot be used in production; "
                "budget state would be lost on every restart"
            )
        from ..repo.memory import InMemoryBudgetRepository

        repository: Any = InMemoryBudgetRepository()
    else:
        from ..repo.dynamo import DynamoBudgetRepository

        repository = DynamoBudgetRepository(
            region=settings.aws_region,
            core_table=settings.table_core,
            ledger_table=settings.table_ledger,
            endpoint_url=settings.dynamodb_endpoint_url or None,
        )

    registry = ProviderRegistry(catalog=catalog, settings=settings)
    adapters = registry._adapters

    engine = BudgetEngine(repository)
    router = RoutingEngine(engine, catalog, default_safety_bps=settings.overshoot_safety_bps)
    effects = SettlementEffects(repository)
    telemetry = build_sink(settings)

    identity = IdentityResolver(repository=repository)
    if settings.admin_api_key:
        identity.register_raw(
            settings.admin_api_key,
            tenant_id="admin",
            team_id="admin",
            agent_id="admin",
            key_id="bootstrap-admin",
            is_admin=True,
        )

    sessions = SessionService(
        repository=repository,
        passwords=PasswordService(settings),
        throttle=LoginThrottle(
            clock,
            limit=settings.login_ip_limit,
            window_seconds=settings.login_ip_window_seconds,
        ),
        clock=clock,
        settings=settings,
    )

    service = InferenceService(
        repository=repository,
        engine=engine,
        router=router,
        effects=effects,
        adapters=adapters,
        catalog=catalog,
        clock=clock,
        telemetry=telemetry,
        timeouts=Timeouts(
            connect_seconds=settings.provider_connect_timeout_s,
            read_seconds=settings.provider_read_timeout_s,
        ),
    )

    return Container(
        settings=settings,
        catalog=catalog,
        repository=repository,
        engine=engine,
        router=router,
        effects=effects,
        service=service,
        identity=identity,
        sessions=sessions,
        review=ReviewService(repository),
        detector=RunawayDetector(repository),
        telemetry=telemetry,
        clock=clock,
        adapters=adapters,
        registry=registry,
    )


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


async def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_abc_session: str | None = Header(default=None, alias="X-ABC-Session"),
    x_abc_csrf: str | None = Header(default=None, alias="X-ABC-CSRF"),
) -> Principal:
    """Resolve the caller's governance identity from its credential.

    Note what is *not* consulted: any agent, team or tenant identifier in the
    request. Identity comes from the credential alone, so a caller cannot spend
    another agent's budget by claiming to be it.

    Two credential shapes share this path: an agent's API key, and a human's
    session -- carried either as a cookie or, for the dashboard's own
    server-side proxy, the ``X-ABC-Session`` header. A machine credential
    always wins when present, and the two are never merged: a session can
    never adopt an agent's identity, and an API key can never inherit a
    session's role. A human principal that resolves this way still carries no
    ``agent_id`` (see ``Principal.require_agent``), so it cannot reach the
    data plane regardless of role.

    A cookie-sourced session on a mutating request also needs the CSRF
    double-submit header -- the same check ``auth_routes.get_session_principal``
    applies, kept here too because this dependency, not that one, is what
    every control-plane write actually depends on. A session presented via
    ``X-ABC-Session`` needs no such check: a custom header cannot be attached
    to a request by a third-party page the way a cookie is attached
    automatically, so there is no ambient credential for CSRF to exploit.
    """
    container: Container = request.app.state.container
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    elif x_api_key:
        raw = x_api_key.strip()
    if raw:
        return await container.identity.resolve(raw)

    cookie_token = request.cookies.get(container.settings.session_cookie_name)
    session_token = cookie_token or x_abc_session
    if session_token:
        principal = await container.sessions.resolve(session_token)
        if cookie_token and request.method not in ("GET", "HEAD"):
            session = await container.sessions.get_session_for_csrf(cookie_token)
            if session is None:
                raise AuthenticationError("session expired or revoked")
            container.sessions.verify_csrf(session, x_abc_csrf)
        return principal

    raise AuthenticationError("missing credential")

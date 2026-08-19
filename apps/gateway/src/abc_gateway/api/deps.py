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

from ..auth.identity import IdentityResolver, Principal
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
        checks = {
            "price_catalog_loaded": bool(self.catalog and self.catalog.entries),
            "budget_store_reachable": await self._store_ok(),
            "providers_configured": bool(self.adapters),
            "identity_configured": len(self.identity) > 0,
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

    identity = IdentityResolver()
    if settings.admin_api_key:
        identity.register_raw(
            settings.admin_api_key,
            tenant_id="admin",
            team_id="admin",
            agent_id="admin",
            key_id="bootstrap-admin",
            is_admin=True,
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
) -> Principal:
    """Resolve the caller's governance identity from its credential.

    Note what is *not* consulted: any agent, team or tenant identifier in the
    request. Identity comes from the credential alone, so a caller cannot spend
    another agent's budget by claiming to be it.
    """
    container: Container = request.app.state.container
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    elif x_api_key:
        raw = x_api_key.strip()
    return container.identity.resolve(raw)

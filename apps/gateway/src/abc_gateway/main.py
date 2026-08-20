"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from ulid import ULID

from .api.auth_routes import auth_router
from .api.deps import Container, build_container
from .api.errors import register_exception_handlers
from .api.routes import admin_router, control_router, data_router, router
from .auth.bootstrap import seed_bootstrap_admin
from .config.settings import Settings, get_settings
from .observability.logging import configure_logging, get_logger, request_id_var

logger = get_logger("abc_gateway.main")


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    """Build the application.

    ``container`` is injectable so tests can supply an in-memory stack without
    reaching for environment variables or monkeypatching module globals.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_output=settings.environment != "local")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container or build_container(settings)
        # Runs before the readiness check below: a fresh deployment with
        # neither ABC_ADMIN_API_KEY nor a persisted user would otherwise have
        # a login page and no way to ever log into it.
        await seed_bootstrap_admin(app.state.container)
        ready, checks, detail = await app.state.container.readiness()
        logger.info(
            "gateway.started",
            environment=settings.environment,
            ready=ready,
            checks=checks,
            **detail,
        )
        yield
        # Give in-flight telemetry a moment, but never let it hold up shutdown.
        await app.state.container.telemetry.drain()
        logger.info("gateway.stopped")

    app = FastAPI(
        title="Agent Budget Controller",
        version="1.0.0",
        description=(
            "A financial authorization gateway for AI inference. Every governed "
            "request has its worst-case cost reserved atomically from every "
            "applicable budget before it can reach a provider."
        ),
        lifespan=lifespan,
    )

    # No CORS middleware by default: the dashboard proxies every call
    # server-side, so no browser ever makes a cross-origin request to this
    # API, and adding permissive headers would create exposure the current
    # architecture does not need. Configuring exact origins here is only for
    # the case of a browser client talking to the gateway directly.
    if settings.cors_allowed_origins:
        if "*" in settings.cors_allowed_origins:
            # A total same-origin-policy bypass for a cookie-authenticated
            # API -- Starlette permits this combination without complaint,
            # so it is refused here at startup instead.
            raise RuntimeError(
                "cors_allowed_origins may not contain '*' when session cookies are in use"
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-Api-Key",
                "X-ABC-Session",
                "X-ABC-CSRF",
                "X-Request-Id",
            ],
            expose_headers=["X-Request-Id", "X-Budget-Decision"],
            max_age=600,
        )

    @app.middleware("http")
    async def attach_request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Give every request a stable id and echo it back.

        One id ties the access log, the ledger entry, the provider's own request
        metadata and the Langfuse trace together -- which is the difference
        between "spend went up" and "this request, from this agent, at this
        time".
        """
        request_id = request.headers.get("X-Request-Id") or str(ULID())
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-Id"] = request_id
        return response

    register_exception_handlers(app)
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(control_router)
    app.include_router(data_router)
    app.include_router(admin_router)
    return app


app = create_app

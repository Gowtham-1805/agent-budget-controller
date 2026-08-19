"""Mapping domain errors to HTTP responses.

The guiding principle: a rejection must be *machine-readable* and must say which
constraint bound the request. A bare 429 is indistinguishable from ordinary rate
limiting, so a client cannot tell "slow down" from "your team is out of money
until September" -- and those call for completely different responses.

429 is used for budget exhaustion because it matches what providers themselves
return for spend limits, so existing client retry logic behaves sensibly. The
machine-readable ``type`` is what carries the real meaning.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ..auth.identity import AuthenticationError, AuthorizationError
from ..domain.errors import (
    AuthorizationDenied,
    Denial,
    DenialCode,
    IdempotencyConflict,
    IdempotentReplay,
    PlanBugError,
    RequestInFlight,
    TransientContention,
)
from ..observability.logging import get_logger
from .service import ProviderRejected, ProviderUnresolved

logger = get_logger("abc_gateway.errors")

#: Budget exhaustion returns 429 to match provider behaviour for spend limits;
#: the `type` field distinguishes it from throughput throttling.
_STATUS_BY_CODE = {
    DenialCode.BUDGET_EXHAUSTED: 429,
    DenialCode.TOKEN_QUOTA_EXCEEDED: 429,
    DenialCode.SESSION_CLOSED: 429,
    DenialCode.SESSION_EXPIRED: 429,
    # 423 Locked: the resource exists but is administratively unavailable, and
    # retrying will not help until a human acts.
    DenialCode.AGENT_PAUSED: 423,
    # 422: the request itself is the problem, not the current balance.
    DenialCode.EXCEEDS_WINDOW_LIMIT: 422,
    DenialCode.NO_ELIGIBLE_MODEL: 422,
}


def denial_to_response(denial: Denial, request_id: str) -> JSONResponse:
    status = _STATUS_BY_CODE.get(denial.code, 429)
    body: dict[str, object] = {
        "type": denial.code.value,
        "message": _message_for(denial),
        "request_id": request_id,
    }

    if denial.blocking_scope is not None:
        body["scope"] = denial.blocking_scope.type.value.lower()
        body["scope_id"] = denial.blocking_scope.id
    for field, value in (
        ("limit_usd", denial.limit),
        ("committed_usd", denial.committed),
        ("reserved_usd", denial.reserved),
        ("available_usd", denial.available),
        ("requested_usd", denial.requested),
    ):
        if value is not None:
            body[field] = value.to_usd_str()
    if denial.token_dimensions:
        body["token_dimensions"] = list(denial.token_dimensions)
    if denial.window is not None:
        # Tells the caller when capacity returns, so they can wait rather than
        # retry-storm a budget that cannot possibly admit them yet.
        body["reset_at"] = denial.window.reset_at.isoformat()
    if denial.session_closed:
        body["session_closed"] = True
    if denial.detail:
        body["detail"] = denial.detail

    return JSONResponse(
        status_code=status,
        content={"error": body},
        headers={"X-Budget-Decision": "blocked", "X-Request-Id": request_id},
    )


def _message_for(denial: Denial) -> str:
    scope = denial.blocking_scope.key() if denial.blocking_scope else "request"
    match denial.code:
        case DenialCode.BUDGET_EXHAUSTED:
            available = denial.available.to_usd_str() if denial.available else "0"
            requested = denial.requested.to_usd_str() if denial.requested else "?"
            return (
                f"{scope} has ${available} available but this request requires "
                f"${requested}; no provider call was made"
            )
        case DenialCode.TOKEN_QUOTA_EXCEEDED:
            dims = ", ".join(denial.token_dimensions) or "token"
            return f"{scope} has exhausted its {dims} token quota"
        case DenialCode.EXCEEDS_WINDOW_LIMIT:
            return (
                f"this request is larger than {scope}'s entire limit and could "
                f"never succeed; reduce the request or raise the limit"
            )
        case DenialCode.AGENT_PAUSED:
            return denial.detail or f"{scope} is paused and requires human review"
        case DenialCode.SESSION_CLOSED:
            return f"{scope} is closed; open a new session to continue"
        case DenialCode.SESSION_EXPIRED:
            return f"{scope} has expired"
        case DenialCode.NO_ELIGIBLE_MODEL:
            return denial.detail or "no model in the routing chain was eligible"
        case _:
            # Unreachable today -- mypy confirms the match is exhaustive over
            # DenialCode, which is exactly the property we want. Kept as a
            # runtime net so that adding a denial code without a message here
            # degrades to something generic rather than raising inside an
            # error handler.
            return denial.detail or "request denied"  # type: ignore[unreachable]


def register_exception_handlers(app) -> None:
    """Install handlers so every failure has one consistent shape."""

    @app.exception_handler(AuthorizationDenied)
    async def _denied(request: Request, exc: AuthorizationDenied) -> JSONResponse:
        return denial_to_response(exc.denial, _request_id(request))

    @app.exception_handler(AuthenticationError)
    async def _unauthenticated(request: Request, exc: AuthenticationError) -> JSONResponse:
        return _error(401, "unauthenticated", str(exc), request)

    @app.exception_handler(AuthorizationError)
    async def _unauthorized(request: Request, exc: AuthorizationError) -> JSONResponse:
        return _error(403, "forbidden", str(exc), request)

    @app.exception_handler(IdempotentReplay)
    async def _replay(request: Request, exc: IdempotentReplay) -> JSONResponse:
        # The original request already settled. Returning its stored outcome is
        # the whole point -- the provider is not called again.
        return JSONResponse(
            status_code=200,
            content={"replayed": True, **exc.response},
            headers={
                "Idempotent-Replay": "true",
                "X-Request-Id": _request_id(request),
            },
        )

    @app.exception_handler(RequestInFlight)
    async def _in_flight(request: Request, exc: RequestInFlight) -> JSONResponse:
        code = "idempotent_request_unresolved" if exc.unresolved else "idempotent_request_in_flight"
        return _error(
            409,
            code,
            f"reservation {exc.reservation_id} for this idempotency key is still open",
            request,
            headers={"Retry-After": "2"},
        )

    @app.exception_handler(IdempotencyConflict)
    async def _idem_conflict(request: Request, exc: IdempotencyConflict) -> JSONResponse:
        return _error(
            422,
            "idempotency_key_reuse",
            "this idempotency key was already used for a different request",
            request,
        )

    @app.exception_handler(TransientContention)
    async def _contention(request: Request, exc: TransientContention) -> JSONResponse:
        # Nothing was decided; the caller may safely retry.
        return _error(
            503,
            "transient_contention",
            f"budget store contention after {exc.attempts} attempts",
            request,
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(ProviderRejected)
    async def _provider_rejected(request: Request, exc: ProviderRejected) -> JSONResponse:
        return _error(502, "provider_rejected", exc.error, request)

    @app.exception_handler(ProviderUnresolved)
    async def _provider_unresolved(request: Request, exc: ProviderUnresolved) -> JSONResponse:
        # Honest about the state of the money: the hold is retained precisely
        # because we cannot prove the provider did not bill us.
        return _error(
            504,
            "provider_unresolved",
            f"{exc.error}; the reservation is held pending reconciliation",
            request,
            extra={"reservation_id": exc.reservation_id, "budget_held": True},
        )

    @app.exception_handler(PlanBugError)
    async def _plan_bug(request: Request, exc: PlanBugError) -> JSONResponse:
        # Never reported as a budget decision: this is our bug, and telling an
        # operator their budget is exhausted would send them hunting a spend
        # problem that does not exist.
        logger.error("gateway.plan_bug", error=str(exc), request_id=_request_id(request))
        return _error(500, "internal_error", "internal gateway error", request)

    @app.exception_handler(LookupError)
    async def _not_found(request: Request, exc: LookupError) -> JSONResponse:
        return _error(404, "not_found", str(exc), request)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def _error(
    status: int,
    error_type: str,
    message: str,
    request: Request,
    *,
    headers: dict[str, str] | None = None,
    extra: dict[str, object] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": error_type,
        "message": message,
        "request_id": _request_id(request),
    }
    if extra:
        body.update(extra)
    return JSONResponse(
        status_code=status,
        content={"error": body},
        headers={"X-Request-Id": _request_id(request), **(headers or {})},
    )

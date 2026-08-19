"""Classifying provider failures, conservatively.

This is the highest-stakes small function in the codebase. Its answer decides
whether a reservation is returned to the budget or held, and getting it wrong in
the permissive direction means the same money can be spent twice: we release the
hold, the provider bills us anyway, and the budget never learns.

So the default is **ambiguous**. "Not billed" must be *proven*, and the only
proofs accepted are conditions where the request demonstrably never reached the
provider's meter:

* the connection was never established (DNS, refused, unreachable, TLS)
* the request never left our process (local validation)
* the provider returned a structured 4xx *and* no usage object

Notably absent: timeouts. A read timeout tells us the response did not arrive.
It says nothing at all about whether the completion was generated and billed --
and for a long generation, a timeout is exactly what a *successful, expensive*
request looks like from our side.
"""

from __future__ import annotations

import socket
import ssl
from typing import Any

import httpx

from ..domain.usage import ProviderUsage
from .base import FailedAmbiguous, FailedBilled, FailedNotBilled, ProviderOutcome

#: Status codes where a structured provider error and no usage object together
#: indicate the request was rejected before generation.
PRE_GENERATION_STATUSES: frozenset[int] = frozenset({400, 401, 403, 404, 405, 422})

#: Exception types that prove no bytes reached the provider.
_NEVER_CONNECTED = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    socket.gaierror,
    ssl.SSLError,
)


def classify_exception(exc: BaseException, *, correlation_id: str) -> ProviderOutcome:
    """Turn a transport-level exception into an outcome.

    Note that ``ConnectTimeout`` is safe to treat as unbilled (the connection
    was never established) while ``ReadTimeout`` is not (the request was sent
    and may have been served). Collapsing the two into "timeout" is the single
    easiest way to introduce a double-spend.
    """
    if isinstance(exc, _NEVER_CONNECTED):
        return FailedNotBilled(error=f"{type(exc).__name__}: {exc}")

    if isinstance(exc, httpx.ReadTimeout | httpx.WriteTimeout | httpx.PoolTimeout):
        return FailedAmbiguous(
            error=f"{type(exc).__name__}: the provider may have generated and billed "
            f"this request; holding the reservation",
            provider_request_id=correlation_id,
        )

    if isinstance(exc, httpx.RemoteProtocolError | httpx.ReadError):
        # The connection was up and the request was likely sent.
        return FailedAmbiguous(error=f"{type(exc).__name__}: {exc}")

    return FailedAmbiguous(error=f"unclassified {type(exc).__name__}: {exc}")


def classify_http_error(
    status_code: int,
    body: dict[str, Any] | None,
    *,
    usage: ProviderUsage | None = None,
) -> ProviderOutcome:
    """Turn a non-2xx response into an outcome."""
    if usage is not None and (usage.input_tokens or usage.output_tokens):
        # Usage was reported, so tokens were processed and billed regardless of
        # the status code. Content filters land here.
        return FailedBilled(usage=usage, error=_message(body) or f"HTTP {status_code}")

    if status_code in PRE_GENERATION_STATUSES and _looks_structured(body):
        return FailedNotBilled(
            error=_message(body) or f"HTTP {status_code}", status_code=status_code
        )

    if status_code == 429 and not body:
        # Rate limited before the request was admitted.
        return FailedNotBilled(error="rate limited", status_code=429)

    # 5xx and anything unrecognised: the provider may have done the work.
    return FailedAmbiguous(error=_message(body) or f"HTTP {status_code}")


def _looks_structured(body: dict[str, Any] | None) -> bool:
    """Whether the body is a parsed provider error envelope.

    Requiring a recognisable envelope matters: a bare 400 from a proxy or load
    balancer is not evidence about the provider's meter, whereas the provider's
    own error object is.
    """
    if not isinstance(body, dict):
        return False
    return "error" in body or "message" in body or "type" in body


def _message(body: dict[str, Any] | None) -> str | None:
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or "")
    if isinstance(error, str):
        return error
    return str(body.get("message")) if body.get("message") else None

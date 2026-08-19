"""Anthropic adapter.

Anthropic is the most interesting of the three adapters for this system,
because it exposes a real preflight token-counting endpoint --
``/v1/messages/count_tokens`` -- which handles system prompts, tools, images and
documents exactly as the model will see them. That is strictly better than any
local tokenizer, so it is used when reachable.

Anthropic documents the count as an *estimate* that can differ slightly from
billed usage, so a small margin is added on top rather than treating it as
exact. The margin is deliberately one-sided: under-counting the prompt means
under-reserving, which is the direction that breaks the guarantee.

Cache accounting also differs from other providers and matters for cost:
``cache_read_input_tokens`` and ``cache_creation_input_tokens`` are reported
*alongside* ``input_tokens`` rather than inside it, so they are added rather
than subtracted -- the opposite of the OpenAI convention. Getting this backwards
silently misprices every cached request.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..domain.usage import ProviderUsage
from ..pricing.catalog import ModelCapabilities, PriceCatalog
from .base import (
    ChatRequest,
    ProviderError,
    ProviderOutcome,
    Succeeded,
    Timeouts,
)
from .classify import classify_exception, classify_http_error

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"

#: Anthropic documents count_tokens as an estimate; this covers the drift.
_COUNT_SAFETY_PERCENT = 3


class AnthropicAdapter:
    """Governed access to Anthropic."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        catalog: PriceCatalog,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("Anthropic API key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._catalog = catalog
        self._client = client

    # -- token counting -----------------------------------------------------

    async def count_input_tokens(self, request: ChatRequest, model: str) -> int:
        """Count the prompt using the provider's own endpoint.

        Preferred over a local tokenizer because it accounts for tools, images
        and documents the way the model actually will. Falls back to a
        pessimistic heuristic if the endpoint is unreachable -- a preflight
        outage must not become a spend-control outage.
        """
        payload = self._count_payload(request, model)
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        owns_client = self._client is None
        try:
            response = await client.post(
                f"{self._base_url}/messages/count_tokens",
                json=payload,
                headers=self._headers(),
            )
            if response.status_code == 200:
                body = response.json()
                return _with_margin(int(body.get("input_tokens", 0)))
        except Exception:
            pass
        finally:
            if owns_client:
                await client.aclose()

        return self._heuristic_tokens(request)

    def _heuristic_tokens(self, request: ChatRequest) -> int:
        characters = request.approximate_characters()
        # ~3 characters per token instead of ~4: over-counting is the safe
        # direction when the authoritative count is unavailable.
        return _with_margin(characters // 3 + 8 * len(request.messages))

    # -- capabilities -------------------------------------------------------

    def bound_max_output_tokens(self, request: ChatRequest, model: str, policy_cap: int) -> int:
        model_cap = self.capabilities(model).max_output_tokens
        if request.max_output_tokens is None:
            return min(policy_cap, model_cap)
        return min(request.max_output_tokens, policy_cap, model_cap)

    def capabilities(self, model: str) -> ModelCapabilities:
        if self._catalog.has(self.name, model):
            return self._catalog.get(self.name, model).capabilities
        return ModelCapabilities(max_context_tokens=200_000, max_output_tokens=64_000)

    # -- invocation ---------------------------------------------------------

    async def invoke(
        self,
        request: ChatRequest,
        model: str,
        *,
        max_output_tokens: int,
        timeouts: Timeouts,
        correlation_id: str,
    ) -> ProviderOutcome:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            # Required by the Messages API, and the value the reservation was
            # computed from. Anthropic bills thinking tokens as output, so this
            # cap is what bounds a reasoning request's true cost.
            "max_tokens": max_output_tokens,
        }
        if request.system:
            payload["system"] = request.system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = list(request.tools)

        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeouts.read_seconds, connect=timeouts.connect_seconds)
        )
        owns_client = self._client is None

        try:
            response = await client.post(
                f"{self._base_url}/messages", json=payload, headers=self._headers()
            )
        except Exception as exc:
            return classify_exception(exc, correlation_id=correlation_id)
        finally:
            if owns_client:
                await client.aclose()

        body = _safe_json(response)
        usage = _extract_usage(body, response)

        if response.status_code >= 400:
            return classify_http_error(response.status_code, body, usage=usage)
        if usage is None:
            return classify_http_error(response.status_code, body, usage=None)

        return Succeeded(usage=usage, content=_first_text(body), raw=body or {})

    def _count_payload(self, request: ChatRequest, model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if request.system:
            payload["system"] = request.system
        if request.tools:
            payload["tools"] = list(request.tools)
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "Content-Type": "application/json",
        }

    async def health(self) -> bool:
        return bool(self._api_key)


def _with_margin(tokens: int) -> int:
    return -(-(tokens * (100 + _COUNT_SAFETY_PERCENT)) // 100)


def _safe_json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        parsed = response.json()
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_usage(body: dict[str, Any] | None, response: httpx.Response) -> ProviderUsage | None:
    """Normalise Anthropic usage into our subset-based convention.

    Anthropic reports cache reads and cache writes as separate totals *in
    addition to* ``input_tokens``. Our :class:`ProviderUsage` treats
    ``cached_input_tokens`` as a subset of ``input_tokens``, so the totals are
    combined here and the subset relationship restored. Passing Anthropic's
    numbers through unchanged would understate the prompt and under-reserve.
    """
    if not body:
        return None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None

    plain_input = int(usage.get("input_tokens", 0))
    cache_read = int(usage.get("cache_read_input_tokens", 0))
    cache_write = int(usage.get("cache_creation_input_tokens", 0))

    return ProviderUsage(
        input_tokens=plain_input + cache_read,
        output_tokens=int(usage.get("output_tokens", 0)),
        cached_input_tokens=cache_read,
        cache_write_tokens=cache_write,
        provider_request_id=response.headers.get("request-id") or body.get("id"),
    )


def _first_text(body: dict[str, Any] | None) -> str:
    if not body:
        return ""
    for block in body.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text", ""))
    return ""

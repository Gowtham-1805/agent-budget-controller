"""Google Gemini adapter.

Gemini exposes a real preflight counting endpoint -- ``:countTokens`` -- which
counts the prompt the way the model will actually see it, including the system
instruction and tool declarations. As with Anthropic, that is strictly better
than a local tokenizer, so it is used when reachable and a pessimistic
heuristic covers the case where it is not.

Two provider-specific accounting details matter here, and both are the kind
that misprice silently rather than loudly:

**Thinking tokens are output tokens, reported separately.** Gemini 2.5 models
return ``thoughtsTokenCount`` *alongside* ``candidatesTokenCount``, not inside
it, and bill both at the output rate. They must therefore be added, not
ignored -- the mirror image of the reasoning-token trap described in
``domain/usage.py``, where a provider that already includes reasoning in the
output count would be double-charged by adding them. Getting this backwards in
either direction misprices every thinking request.

**``maxOutputTokens`` does not bound thinking.** It caps
``candidatesTokenCount`` only, so a request can bill well past the cap the
reservation was computed from. That is why every Gemini 2.5 entry in the price
catalog carries ``supports_hard_output_cap: false``: it makes the routing
engine add its overshoot margin instead of trusting a ceiling the provider does
not actually enforce. See ``pricing/catalog.json`` and ``engine/routing.py``.

Cache accounting, by contrast, needs no adjustment: Gemini reports
``cachedContentTokenCount`` as a subset of ``promptTokenCount``, which is
already the convention :class:`~..domain.usage.ProviderUsage` expects -- unlike
Anthropic, where the cache totals sit outside ``input_tokens`` and have to be
folded in.
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

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

#: countTokens is documented as exact for text, but tool declarations and
#: multimodal parts can drift. Same one-sided margin as the Anthropic adapter:
#: under-counting the prompt under-reserves, which is the direction that breaks
#: the guarantee.
_COUNT_SAFETY_PERCENT = 3


class GeminiAdapter:
    """Governed access to the Gemini API."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        catalog: PriceCatalog,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("Gemini API key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._catalog = catalog
        self._client = client

    # -- token counting -----------------------------------------------------

    async def count_input_tokens(self, request: ChatRequest, model: str) -> int:
        """Count the prompt using the provider's own endpoint.

        Falls back to a pessimistic heuristic if the endpoint is unreachable --
        a preflight outage must not become a spend-control outage.
        """
        payload = self._count_payload(request)
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        owns_client = self._client is None
        try:
            response = await client.post(
                f"{self._base_url}/models/{model}:countTokens",
                json=payload,
                headers=self._headers(),
            )
            if response.status_code == 200:
                body = response.json()
                return _with_margin(int(body.get("totalTokens", 0)))
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
        # Conservative default: assume the cap is not hard, so an unknown model
        # is reserved with the overshoot margin rather than without it.
        return ModelCapabilities(
            max_context_tokens=1_048_576,
            max_output_tokens=65_536,
            supports_hard_output_cap=False,
        )

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
        generation_config: dict[str, Any] = {
            # The value the reservation was computed from. Note this bounds the
            # visible completion only -- see the module docstring on thinking.
            "maxOutputTokens": max_output_tokens,
        }
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature

        payload: dict[str, Any] = {
            "contents": _to_contents(request),
            "generationConfig": generation_config,
        }
        if request.system:
            payload["systemInstruction"] = {"parts": [{"text": request.system}]}
        if request.tools:
            payload["tools"] = [{"functionDeclarations": list(request.tools)}]

        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeouts.read_seconds, connect=timeouts.connect_seconds)
        )
        owns_client = self._client is None

        try:
            response = await client.post(
                f"{self._base_url}/models/{model}:generateContent",
                json=payload,
                headers=self._headers(),
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

    def _count_payload(self, request: ChatRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {"contents": _to_contents(request)}
        # countTokens takes the system instruction and tools nested under a
        # generateContentRequest; omitting them undercounts a prompt that uses
        # either, which is the direction that under-reserves.
        if request.system or request.tools:
            inner: dict[str, Any] = {"contents": payload["contents"]}
            if request.system:
                inner["systemInstruction"] = {"parts": [{"text": request.system}]}
            if request.tools:
                inner["tools"] = [{"functionDeclarations": list(request.tools)}]
            return {"generateContentRequest": inner}
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }

    async def health(self) -> bool:
        return bool(self._api_key)


def _to_contents(request: ChatRequest) -> list[dict[str, Any]]:
    """Map neutral messages onto Gemini's ``contents``.

    Gemini names the assistant turn ``model``; sending ``assistant`` is
    rejected as an invalid role.
    """
    return [
        {
            "role": "model" if m.role == "assistant" else "user",
            "parts": [{"text": m.content}],
        }
        for m in request.messages
    ]


def _with_margin(tokens: int) -> int:
    return -(-(tokens * (100 + _COUNT_SAFETY_PERCENT)) // 100)


def _safe_json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        parsed = response.json()
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_usage(body: dict[str, Any] | None, response: httpx.Response) -> ProviderUsage | None:
    """Normalise ``usageMetadata`` into our subset-based convention.

    ``thoughtsTokenCount`` is reported outside ``candidatesTokenCount`` and
    billed at the output rate, so it is added to the output total and then
    recorded as ``reasoning_tokens`` -- which :class:`ProviderUsage` treats as a
    subset of output, exactly matching that sum. ``cachedContentTokenCount`` is
    already a subset of ``promptTokenCount`` and passes through unchanged.
    """
    if not body:
        return None
    usage = body.get("usageMetadata")
    if not isinstance(usage, dict):
        return None

    prompt_tokens = int(usage.get("promptTokenCount", 0))
    cached = int(usage.get("cachedContentTokenCount", 0))
    visible_output = int(usage.get("candidatesTokenCount", 0))
    thoughts = int(usage.get("thoughtsTokenCount", 0))

    return ProviderUsage(
        input_tokens=prompt_tokens,
        output_tokens=visible_output + thoughts,
        cached_input_tokens=min(cached, prompt_tokens),
        reasoning_tokens=thoughts,
        provider_request_id=body.get("responseId") or response.headers.get("x-request-id"),
    )


def _first_text(body: dict[str, Any] | None) -> str:
    if not body:
        return ""
    for candidate in body.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict) and "text" in part:
                return str(part.get("text", ""))
    return ""

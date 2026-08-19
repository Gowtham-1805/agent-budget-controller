"""OpenAI adapter.

Uses the Chat Completions API, which is the broadest-compatibility surface, and
sends ``max_completion_tokens`` as a hard ceiling on every request.

That ceiling is not advisory. It is the value the gateway reserved against, so
if it were omitted -- or silently raised by the client -- the reservation would
bound nothing. For reasoning models this matters more than it first appears:
generated reasoning tokens are billed as output even though they never appear in
the response, so a request with no cap can cost far more than its visible answer
suggests.

Token counting uses ``tiktoken`` locally, with a documented safety margin. A
local tokenizer is approximate for structured messages, tool definitions and
images, so the count is deliberately rounded *up*: under-counting the prompt
means under-reserving, which is the direction that breaks the guarantee.
"""

from __future__ import annotations

import json
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

DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Per-message framing overhead in the chat format. Approximate by design, and
#: rounded against us.
_MESSAGE_OVERHEAD_TOKENS = 4
#: Extra headroom on locally-counted prompts, in percent. Covers tokenizer drift
#: for tools, images and structured content that tiktoken cannot see exactly.
_COUNT_SAFETY_PERCENT = 5


class OpenAIAdapter:
    """Governed access to OpenAI."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        catalog: PriceCatalog,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        organization: str | None = None,
        use_tiktoken: bool = True,
    ) -> None:
        if not api_key:
            raise ProviderError("OpenAI API key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._catalog = catalog
        self._client = client
        self._organization = organization
        self._use_tiktoken = use_tiktoken
        self._encoders: dict[str, Any] = {}

    # -- token counting -----------------------------------------------------

    async def count_input_tokens(self, request: ChatRequest, model: str) -> int:
        """Count prompt tokens, rounded up.

        Falls back to a character heuristic only if tiktoken is unavailable --
        and that fallback is deliberately pessimistic, because the alternative
        to over-reserving is authorising spend we cannot cover.
        """
        encoder = self._encoder(model)
        if encoder is None:
            return self._heuristic_tokens(request)

        total = 0
        if request.system:
            total += len(encoder.encode(request.system)) + _MESSAGE_OVERHEAD_TOKENS
        for message in request.messages:
            total += len(encoder.encode(message.content)) + _MESSAGE_OVERHEAD_TOKENS
        if request.tools:
            # Tool schemas are serialised into the prompt; counting the JSON is
            # closer than ignoring them.
            total += len(encoder.encode(json.dumps(list(request.tools))))

        return _with_margin(total)

    def _encoder(self, model: str) -> Any:
        """Resolve a tokenizer, caching per model.

        tiktoken downloads its BPE table on first use, which is a slow, network-
        dependent surprise in CI and impossible in an air-gapped deployment.
        Set ``use_tiktoken=False`` to skip it entirely and use the pessimistic
        character heuristic instead -- the request still gets a safe (larger)
        reservation, it is simply less precise.
        """
        if not self._use_tiktoken:
            return None
        if model in self._encoders:
            return self._encoders[model]
        try:
            import tiktoken

            try:
                encoder = tiktoken.encoding_for_model(model)
            except KeyError:
                # An unknown model name is normal -- new models appear faster
                # than tiktoken's table. o200k_base is the current default.
                encoder = tiktoken.get_encoding("o200k_base")
        except Exception:
            encoder = None
        self._encoders[model] = encoder
        return encoder

    def _heuristic_tokens(self, request: ChatRequest) -> int:
        characters = request.approximate_characters()
        if request.tools:
            characters += len(json.dumps(list(request.tools)))
        # ~3 characters per token rather than the usual ~4: erring toward
        # over-counting keeps the reservation safe.
        return _with_margin(characters // 3 + _MESSAGE_OVERHEAD_TOKENS * len(request.messages))

    # -- capabilities -------------------------------------------------------

    def bound_max_output_tokens(self, request: ChatRequest, model: str, policy_cap: int) -> int:
        model_cap = self.capabilities(model).max_output_tokens
        if request.max_output_tokens is None:
            # A client that omits a limit gets the policy cap, never "unbounded".
            return min(policy_cap, model_cap)
        return min(request.max_output_tokens, policy_cap, model_cap)

    def capabilities(self, model: str) -> ModelCapabilities:
        if self._catalog.has(self.name, model):
            return self._catalog.get(self.name, model).capabilities
        return ModelCapabilities(max_context_tokens=128_000, max_output_tokens=16_384)

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
            "messages": self._messages(request),
            # The gateway's ceiling, sent as a hard cap. This is the value the
            # reservation was computed from.
            "max_completion_tokens": max_output_tokens,
        }
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
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(correlation_id),
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
            # A 2xx with no usage object is not something we can bill against,
            # and pretending it was free would understate spend.
            return classify_http_error(response.status_code, body, usage=None)

        return Succeeded(usage=usage, content=_first_message(body), raw=body or {})

    def _headers(self, correlation_id: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # Propagated so a gateway request can be correlated with the
            # provider's own logs during an incident.
            "X-Request-Id": correlation_id,
        }
        if self._organization:
            headers["OpenAI-Organization"] = self._organization
        return headers

    def _messages(self, request: ChatRequest) -> list[dict[str, str]]:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)
        return messages

    async def health(self) -> bool:
        """Configured and usable. Deliberately makes no billable call."""
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
    """Normalise OpenAI's usage object.

    ``cached_tokens`` is reported inside ``prompt_tokens_details`` as a *subset*
    of ``prompt_tokens``, and ``reasoning_tokens`` likewise sits inside
    ``completion_tokens``. Both are passed through as subsets; the pricing layer
    is what keeps them from being counted twice.
    """
    if not body:
        return None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None

    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}

    return ProviderUsage(
        input_tokens=int(usage.get("prompt_tokens", 0)),
        output_tokens=int(usage.get("completion_tokens", 0)),
        cached_input_tokens=int(prompt_details.get("cached_tokens", 0)),
        reasoning_tokens=int(completion_details.get("reasoning_tokens", 0)),
        provider_request_id=response.headers.get("x-request-id") or body.get("id"),
    )


def _first_message(body: dict[str, Any] | None) -> str:
    if not body:
        return ""
    choices = body.get("choices") or []
    if not choices:
        return ""
    return str((choices[0].get("message") or {}).get("content") or "")

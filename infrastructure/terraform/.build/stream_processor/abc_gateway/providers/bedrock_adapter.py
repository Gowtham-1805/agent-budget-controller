"""Amazon Bedrock adapter.

Bedrock is the AWS-native path, and it earns its place in the design for a
reason unrelated to pricing: it authenticates with the ECS task's IAM role, so
there is no third-party API key to store, rotate, or leak. Where a deployment
can use it, that is one whole category of secret handling removed.

It uses the Converse API, which normalises requests and usage across model
families, so the same adapter serves Nova, Claude-on-Bedrock and others without
per-family branching. ``maxTokens`` in ``inferenceConfig`` is the hard ceiling
the reservation was computed from.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..domain.usage import ProviderUsage
from ..pricing.catalog import ModelCapabilities, PriceCatalog
from .base import (
    ChatRequest,
    FailedAmbiguous,
    FailedBilled,
    FailedNotBilled,
    ProviderOutcome,
    Succeeded,
    Timeouts,
)

#: Bedrock error codes that prove the request was rejected before inference.
_PRE_INFERENCE_ERRORS = frozenset(
    {
        "ValidationException",
        "AccessDeniedException",
        "ResourceNotFoundException",
        "UnrecognizedClientException",
        "IncompleteSignature",
    }
)

#: Throttling happens at admission, before any tokens are processed.
_NOT_BILLED_ERRORS = frozenset({"ThrottlingException", "ServiceQuotaExceededException"})


class BedrockAdapter:
    """Governed access to Amazon Bedrock."""

    name = "bedrock"

    def __init__(
        self,
        *,
        catalog: PriceCatalog,
        region: str = "us-east-1",
        client: Any = None,
    ) -> None:
        self._catalog = catalog
        self._region = region
        self._client = client

    def _bedrock(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._region,
                # The engine owns retries; a retry below this layer would not
                # carry our reservation discipline.
                config=Config(retries={"mode": "standard", "total_max_attempts": 1}),
            )
        return self._client

    # -- token counting -----------------------------------------------------

    async def count_input_tokens(self, request: ChatRequest, model: str) -> int:
        """Count prompt tokens using Bedrock's counting API where available.

        Falls back to a pessimistic heuristic; over-counting is the safe
        direction, since an under-count means an under-reservation.
        """
        payload = {
            "messages": self._messages(request),
            **({"system": [{"text": request.system}]} if request.system else {}),
        }
        try:
            response = await asyncio.to_thread(
                self._bedrock().count_tokens,
                modelId=model,
                input={"converse": payload},
            )
            return int(response["inputTokens"])
        except Exception:
            return self._heuristic_tokens(request)

    def _heuristic_tokens(self, request: ChatRequest) -> int:
        characters = request.approximate_characters()
        return -(-(characters // 3 + 8 * len(request.messages)) * 105 // 100)

    # -- capabilities -------------------------------------------------------

    def bound_max_output_tokens(self, request: ChatRequest, model: str, policy_cap: int) -> int:
        model_cap = self.capabilities(model).max_output_tokens
        if request.max_output_tokens is None:
            return min(policy_cap, model_cap)
        return min(request.max_output_tokens, policy_cap, model_cap)

    def capabilities(self, model: str) -> ModelCapabilities:
        if self._catalog.has(self.name, model):
            return self._catalog.get(self.name, model).capabilities
        return ModelCapabilities(max_context_tokens=128_000, max_output_tokens=8_192)

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
        kwargs: dict[str, Any] = {
            "modelId": model,
            "messages": self._messages(request),
            "inferenceConfig": {"maxTokens": max_output_tokens},
        }
        if request.system:
            kwargs["system"] = [{"text": request.system}]
        if request.temperature is not None:
            kwargs["inferenceConfig"]["temperature"] = request.temperature
        if request.tools:
            kwargs["toolConfig"] = {"tools": list(request.tools)}

        try:
            response = await asyncio.to_thread(self._bedrock().converse, **kwargs)
        except Exception as exc:
            return self._classify(exc, correlation_id)

        usage = _extract_usage(response)
        if usage is None:
            return FailedAmbiguous(
                error="Bedrock returned no usage block; billing status unknown",
                provider_request_id=correlation_id,
            )
        return Succeeded(usage=usage, content=_first_text(response), raw=response)

    def _classify(self, exc: Exception, correlation_id: str) -> ProviderOutcome:
        """Map a botocore error to an outcome, failing closed.

        ``ModelErrorException`` is deliberately treated as *billed*: the model
        was invoked and failed partway, which typically still consumes tokens.
        Anything unrecognised is ambiguous.
        """
        code = ""
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str(response.get("Error", {}).get("Code", ""))

        if code in _PRE_INFERENCE_ERRORS or code in _NOT_BILLED_ERRORS:
            return FailedNotBilled(error=f"{code}: {exc}")
        if code == "ModelErrorException":
            return FailedBilled(
                usage=ProviderUsage(input_tokens=0, output_tokens=0), error=str(exc)
            )
        return FailedAmbiguous(
            error=f"{code or type(exc).__name__}: {exc}", provider_request_id=correlation_id
        )

    def _messages(self, request: ChatRequest) -> list[dict[str, Any]]:
        return [{"role": m.role, "content": [{"text": m.content}]} for m in request.messages]

    async def health(self) -> bool:
        """Credentials resolve. Deliberately makes no billable call."""
        try:
            import boto3

            return boto3.Session().get_credentials() is not None
        except Exception:
            return False


def _extract_usage(response: dict[str, Any]) -> ProviderUsage | None:
    """Normalise Converse usage.

    Like Anthropic's native API, Bedrock reports cache reads and writes
    alongside ``inputTokens`` rather than inside it, so they are folded in to
    restore our subset convention.
    """
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None

    plain_input = int(usage.get("inputTokens", 0))
    cache_read = int(usage.get("cacheReadInputTokens", 0))
    cache_write = int(usage.get("cacheWriteInputTokens", 0))

    return ProviderUsage(
        input_tokens=plain_input + cache_read,
        output_tokens=int(usage.get("outputTokens", 0)),
        cached_input_tokens=cache_read,
        cache_write_tokens=cache_write,
        provider_request_id=(response.get("ResponseMetadata", {}).get("RequestId")),
    )


def _first_text(response: dict[str, Any]) -> str:
    message = (response.get("output") or {}).get("message") or {}
    for block in message.get("content") or []:
        if isinstance(block, dict) and "text" in block:
            return str(block["text"])
    return ""

"""Provider adapters.

Provider-specific parsing, tokenization and error classification live here and
nowhere else. The budget engine deals only in outcomes and normalised usage.
"""

from typing import Any

from .base import (
    ChatMessage,
    ChatRequest,
    FailedAmbiguous,
    FailedBilled,
    FailedNotBilled,
    ProviderAdapter,
    ProviderError,
    ProviderOutcome,
    Succeeded,
    Timeouts,
)
from .classify import classify_exception, classify_http_error
from .fake import FakeBehaviour, FakeProvider, ProviderInvokedWithoutReservation


def build_adapters(settings: Any, catalog: Any) -> dict[str, ProviderAdapter]:
    """Construct the adapters this deployment is configured for.

    Missing credentials are not an error: a deployment governing only Bedrock
    has no reason to hold an OpenAI key. Adapters simply are not registered, and
    routing to an unregistered provider fails with a clear message rather than
    a confusing authentication error at request time.
    """
    adapters: dict[str, ProviderAdapter] = {}

    if settings.openai_api_key:
        from .openai_adapter import OpenAIAdapter

        adapters["openai"] = OpenAIAdapter(api_key=settings.openai_api_key, catalog=catalog)

    if settings.anthropic_api_key:
        from .anthropic_adapter import AnthropicAdapter

        adapters["anthropic"] = AnthropicAdapter(
            api_key=settings.anthropic_api_key, catalog=catalog
        )

    if settings.bedrock_enabled:
        from .bedrock_adapter import BedrockAdapter

        adapters["bedrock"] = BedrockAdapter(catalog=catalog, region=settings.bedrock_region)

    if getattr(settings, "enable_fake_provider", False):
        if settings.is_production:
            # The fake returns canned usage, so any budget it appears to enforce
            # is fictional. Allowing it in production would make every figure on
            # the dashboard confidently wrong.
            raise RuntimeError(
                "the fake provider cannot be enabled in production; it returns "
                "synthetic usage and would make budget figures meaningless"
            )
        adapters["test"] = FakeProvider(FakeBehaviour(input_tokens=1000, output_tokens=1000))

    return adapters


__all__ = [
    "ChatMessage",
    "ChatRequest",
    "FailedAmbiguous",
    "FailedBilled",
    "FailedNotBilled",
    "FakeBehaviour",
    "FakeProvider",
    "ProviderAdapter",
    "ProviderError",
    "ProviderInvokedWithoutReservation",
    "ProviderOutcome",
    "Succeeded",
    "Timeouts",
    "build_adapters",
    "classify_exception",
    "classify_http_error",
]

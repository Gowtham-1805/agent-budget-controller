"""Provider adapters, tested without spending money.

Real HTTP responses are replayed through httpx's mock transport, so the parsing
and classification logic is exercised against realistic payloads while the
network stays untouched.

The classification tests are the important ones. Every "not billed" verdict
returns money to a budget, so a wrong one lets the same dollars be spent twice.
"""

from __future__ import annotations

import httpx
import pytest

from abc_gateway.domain.usage import NormalizedUsage
from abc_gateway.providers.anthropic_adapter import AnthropicAdapter
from abc_gateway.providers.base import (
    ChatMessage,
    ChatRequest,
    FailedAmbiguous,
    FailedBilled,
    FailedNotBilled,
    Succeeded,
    Timeouts,
)
from abc_gateway.providers.classify import classify_exception, classify_http_error
from abc_gateway.providers.gemini_adapter import GeminiAdapter
from abc_gateway.providers.openai_adapter import OpenAIAdapter


def chat(max_output: int | None = None) -> ChatRequest:
    return ChatRequest(
        messages=(ChatMessage("user", "hello world"),),
        model="gpt-4o",
        max_output_tokens=max_output,
    )


def transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestFailureClassification:
    """Fail closed. "Not billed" must be proven, never assumed."""

    def test_a_connect_failure_is_proven_unbilled(self) -> None:
        outcome = classify_exception(httpx.ConnectError("refused"), correlation_id="r1")
        assert isinstance(outcome, FailedNotBilled)

    def test_a_connect_timeout_is_proven_unbilled(self) -> None:
        """The connection was never established, so nothing was metered."""
        outcome = classify_exception(httpx.ConnectTimeout("timed out"), correlation_id="r1")
        assert isinstance(outcome, FailedNotBilled)

    def test_a_read_timeout_is_ambiguous(self) -> None:
        """The most important single assertion in this file.

        A read timeout means the response did not arrive. It says nothing about
        whether the completion was generated and billed -- and for a long
        generation, a timeout is exactly what an expensive *success* looks like
        from our side. Treating it as unbilled would release the hold and let
        the same money be spent again.
        """
        outcome = classify_exception(httpx.ReadTimeout("slow"), correlation_id="r1")
        assert isinstance(outcome, FailedAmbiguous)

    def test_an_unknown_exception_is_ambiguous(self) -> None:
        outcome = classify_exception(RuntimeError("???"), correlation_id="r1")
        assert isinstance(outcome, FailedAmbiguous)

    def test_a_structured_4xx_without_usage_is_unbilled(self) -> None:
        outcome = classify_http_error(
            400, {"error": {"message": "invalid model", "type": "invalid_request"}}
        )
        assert isinstance(outcome, FailedNotBilled)

    def test_a_bare_4xx_from_a_proxy_is_ambiguous(self) -> None:
        """An unstructured body is not evidence about the provider's meter.

        It may not have come from the provider at all.
        """
        outcome = classify_http_error(400, None)
        assert isinstance(outcome, FailedAmbiguous)

    def test_a_5xx_is_ambiguous(self) -> None:
        outcome = classify_http_error(500, {"error": {"message": "oops"}})
        assert isinstance(outcome, FailedAmbiguous)

    def test_a_response_carrying_usage_is_billed_whatever_the_status(self) -> None:
        """Content filters generate tokens and then refuse to return them."""
        from abc_gateway.domain.usage import ProviderUsage

        outcome = classify_http_error(
            400,
            {"error": {"message": "content filtered"}},
            usage=ProviderUsage(input_tokens=100, output_tokens=50),
        )
        assert isinstance(outcome, FailedBilled)


class TestOpenAIAdapter:
    @pytest.fixture
    def adapter(self, catalog):
        return OpenAIAdapter(api_key="test-key", catalog=catalog)

    async def test_sends_a_hard_output_cap(self, catalog) -> None:
        """The reservation is meaningless unless the cap reaches the provider."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured.update(json.loads(request.content))
            return httpx.Response(200, json=_openai_response())

        adapter = OpenAIAdapter(api_key="k", catalog=catalog, client=transport(handler))
        await adapter.invoke(
            chat(),
            "gpt-4o",
            max_output_tokens=512,
            timeouts=Timeouts(),
            correlation_id="req-1",
        )

        assert captured["max_completion_tokens"] == 512

    async def test_parses_usage_including_cached_and_reasoning(self, catalog) -> None:
        adapter = OpenAIAdapter(
            api_key="k",
            catalog=catalog,
            client=transport(lambda r: httpx.Response(200, json=_openai_response())),
        )
        outcome = await adapter.invoke(
            chat(),
            "gpt-4o",
            max_output_tokens=512,
            timeouts=Timeouts(),
            correlation_id="req-1",
        )

        assert isinstance(outcome, Succeeded)
        assert outcome.usage.input_tokens == 1000
        assert outcome.usage.output_tokens == 300
        assert outcome.usage.cached_input_tokens == 800
        assert outcome.usage.reasoning_tokens == 120

    async def test_cached_tokens_stay_a_subset_of_input(self, catalog) -> None:
        """OpenAI reports cached tokens inside prompt_tokens.

        Normalisation must leave the input total unchanged, not inflate it.
        """
        adapter = OpenAIAdapter(
            api_key="k",
            catalog=catalog,
            client=transport(lambda r: httpx.Response(200, json=_openai_response())),
        )
        outcome = await adapter.invoke(
            chat(),
            "gpt-4o",
            max_output_tokens=512,
            timeouts=Timeouts(),
            correlation_id="req-1",
        )
        norm = NormalizedUsage.from_provider(outcome.usage)
        assert norm.total_input_tokens == 1000
        assert norm.uncached_input_tokens == 200

    async def test_a_client_omitting_a_limit_gets_the_policy_cap(self, adapter) -> None:
        assert adapter.bound_max_output_tokens(chat(None), "gpt-4o", 750) == 750

    async def test_a_client_cannot_exceed_the_policy_cap(self, adapter) -> None:
        assert adapter.bound_max_output_tokens(chat(99_999), "gpt-4o", 750) == 750

    async def test_token_counting_rounds_up(self, adapter) -> None:
        """Under-counting the prompt means under-reserving."""
        count = await adapter.count_input_tokens(chat(), "gpt-4o")
        assert count > 0

    async def test_health_makes_no_billable_call(self, catalog) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url)
            return httpx.Response(200, json={})

        adapter = OpenAIAdapter(api_key="k", catalog=catalog, client=transport(handler))
        assert await adapter.health() is True
        assert calls == [], "a readiness probe must never cost money"


class TestAnthropicAdapter:
    async def test_sends_max_tokens_as_a_hard_cap(self, catalog) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured.update(json.loads(request.content))
            return httpx.Response(200, json=_anthropic_response())

        adapter = AnthropicAdapter(api_key="k", catalog=catalog, client=transport(handler))
        await adapter.invoke(
            chat(),
            "claude-sonnet-4-5",
            max_output_tokens=256,
            timeouts=Timeouts(),
            correlation_id="req-1",
        )
        assert captured["max_tokens"] == 256

    async def test_cache_reads_are_folded_into_the_input_total(self, catalog) -> None:
        """Anthropic reports cache reads *alongside* input_tokens, not inside it.

        Our convention is subset-based, so the two must be combined. Passing
        Anthropic's numbers through unchanged would understate the prompt and
        under-reserve every cached request.
        """
        adapter = AnthropicAdapter(
            api_key="k",
            catalog=catalog,
            client=transport(lambda r: httpx.Response(200, json=_anthropic_response())),
        )
        outcome = await adapter.invoke(
            chat(),
            "claude-sonnet-4-5",
            max_output_tokens=256,
            timeouts=Timeouts(),
            correlation_id="req-1",
        )

        assert isinstance(outcome, Succeeded)
        # 200 plain + 800 cache reads.
        assert outcome.usage.input_tokens == 1000
        assert outcome.usage.cached_input_tokens == 800
        assert outcome.usage.cache_write_tokens == 50

        norm = NormalizedUsage.from_provider(outcome.usage)
        assert norm.uncached_input_tokens == 200

    async def test_uses_the_providers_count_tokens_endpoint(self, catalog) -> None:
        """Better than a local tokenizer: it sees tools and images as the model does."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json={"input_tokens": 1234})

        adapter = AnthropicAdapter(api_key="k", catalog=catalog, client=transport(handler))
        count = await adapter.count_input_tokens(chat(), "claude-sonnet-4-5")

        assert "/messages/count_tokens" in seen[0]
        # A margin is added because Anthropic documents the count as an estimate.
        assert count >= 1234

    async def test_falls_back_when_counting_is_unavailable(self, catalog) -> None:
        """A preflight outage must not become a spend-control outage."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        adapter = AnthropicAdapter(api_key="k", catalog=catalog, client=transport(handler))
        count = await adapter.count_input_tokens(chat(), "claude-sonnet-4-5")
        assert count > 0

    async def test_a_read_timeout_holds_the_reservation(self, catalog) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        adapter = AnthropicAdapter(api_key="k", catalog=catalog, client=transport(handler))
        outcome = await adapter.invoke(
            chat(),
            "claude-sonnet-4-5",
            max_output_tokens=256,
            timeouts=Timeouts(),
            correlation_id="req-1",
        )
        assert isinstance(outcome, FailedAmbiguous)


class TestGeminiAdapter:
    async def test_sends_max_output_tokens_in_generation_config(self, catalog) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured.update(json.loads(request.content))
            return httpx.Response(200, json=_gemini_response())

        adapter = GeminiAdapter(api_key="k", catalog=catalog, client=transport(handler))
        await adapter.invoke(
            chat(),
            "gemini-2.5-flash",
            max_output_tokens=256,
            timeouts=Timeouts(),
            correlation_id="req-1",
        )
        assert captured["generationConfig"]["maxOutputTokens"] == 256

    async def test_thinking_tokens_are_added_to_the_output_total(self, catalog) -> None:
        """The most important assertion for this adapter.

        Gemini reports thoughtsTokenCount *outside* candidatesTokenCount and
        bills both at the output rate. Passing candidatesTokenCount through
        alone would understate output on every thinking request -- the exact
        mirror of the Anthropic cache trap, in the opposite direction.
        """
        adapter = GeminiAdapter(
            api_key="k",
            catalog=catalog,
            client=transport(lambda r: httpx.Response(200, json=_gemini_response())),
        )
        outcome = await adapter.invoke(
            chat(),
            "gemini-2.5-flash",
            max_output_tokens=256,
            timeouts=Timeouts(),
            correlation_id="req-1",
        )

        assert isinstance(outcome, Succeeded)
        # 300 visible + 120 thinking, both billed as output.
        assert outcome.usage.output_tokens == 420
        assert outcome.usage.reasoning_tokens == 120
        # Cached input is already a subset of promptTokenCount here.
        assert outcome.usage.input_tokens == 1000
        assert outcome.usage.cached_input_tokens == 800

        norm = NormalizedUsage.from_provider(outcome.usage)
        assert norm.uncached_input_tokens == 200

    async def test_the_assistant_role_is_mapped_to_model(self, catalog) -> None:
        """Gemini rejects "assistant" as an invalid role."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured.update(json.loads(request.content))
            return httpx.Response(200, json=_gemini_response())

        adapter = GeminiAdapter(api_key="k", catalog=catalog, client=transport(handler))
        request = ChatRequest(
            messages=(ChatMessage("user", "hi"), ChatMessage("assistant", "hello")),
            model="gemini-2.5-flash",
        )
        await adapter.invoke(
            request,
            "gemini-2.5-flash",
            max_output_tokens=64,
            timeouts=Timeouts(),
            correlation_id="req-1",
        )
        assert [c["role"] for c in captured["contents"]] == ["user", "model"]

    async def test_uses_the_providers_count_tokens_endpoint(self, catalog) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json={"totalTokens": 1234})

        adapter = GeminiAdapter(api_key="k", catalog=catalog, client=transport(handler))
        count = await adapter.count_input_tokens(chat(), "gemini-2.5-flash")

        assert ":countTokens" in seen[0]
        assert count >= 1234

    async def test_falls_back_when_counting_is_unavailable(self, catalog) -> None:
        """A preflight outage must not become a spend-control outage."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        adapter = GeminiAdapter(api_key="k", catalog=catalog, client=transport(handler))
        count = await adapter.count_input_tokens(chat(), "gemini-2.5-flash")
        assert count > 0

    async def test_a_read_timeout_holds_the_reservation(self, catalog) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        adapter = GeminiAdapter(api_key="k", catalog=catalog, client=transport(handler))
        outcome = await adapter.invoke(
            chat(),
            "gemini-2.5-flash",
            max_output_tokens=256,
            timeouts=Timeouts(),
            correlation_id="req-1",
        )
        assert isinstance(outcome, FailedAmbiguous)

    def test_the_output_cap_is_not_treated_as_hard(self, catalog) -> None:
        """maxOutputTokens bounds the visible completion, not thinking.

        If this flips to True the routing engine stops adding its overshoot
        margin, and every 2.5 reservation starts understating the true cost.
        """
        assert catalog.get("gemini", "gemini-2.5-pro").capabilities.supports_hard_output_cap is False

    def test_long_prompts_are_priced_at_the_tiered_rate(self, catalog) -> None:
        """Gemini 2.5 Pro bills 2x input / 1.5x output above 200k tokens."""
        price = catalog.get("gemini", "gemini-2.5-pro")
        short = price.estimate_worst_case(1_000, 1_000)
        long = price.estimate_worst_case(200_001, 1_000)
        # Same output shape, but the long prompt crosses the threshold.
        assert long.input_cost.nano > short.input_cost.nano * 200


def _gemini_response() -> dict:
    return {
        "responseId": "resp_1",
        "candidates": [{"content": {"parts": [{"text": "hi"}], "role": "model"}}],
        "usageMetadata": {
            "promptTokenCount": 1000,
            "cachedContentTokenCount": 800,
            "candidatesTokenCount": 300,
            "thoughtsTokenCount": 120,
        },
    }


def _openai_response() -> dict:
    return {
        "id": "chatcmpl-1",
        "choices": [{"message": {"role": "assistant", "content": "hi"}}],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 300,
            "prompt_tokens_details": {"cached_tokens": 800},
            "completion_tokens_details": {"reasoning_tokens": 120},
        },
    }


def _anthropic_response() -> dict:
    return {
        "id": "msg_1",
        "content": [{"type": "text", "text": "hi"}],
        "usage": {
            "input_tokens": 200,
            "output_tokens": 300,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 50,
        },
    }

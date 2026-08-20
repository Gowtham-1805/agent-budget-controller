"""Provider configuration registry and lifecycle management.

Manages configuration for LLM providers (OpenAI, Bedrock, Anthropic, Gemini, Test),
maintains secure in-memory secret references (never returned in responses or logs),
and dynamically instantiates and tests provider adapters.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ..domain.money import Money
from ..domain.providers import (
    ProviderAuthType,
    ProviderConfig,
    ProviderConnectionStatus,
    ProviderTestResult,
    mask_secret,
)
from ..pricing.catalog import PriceCatalog
from .base import ProviderAdapter
from .openai_adapter import DEFAULT_BASE_URL

logger = logging.getLogger("abc_gateway.providers.registry")

SUPPORTED_PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "display_name": "OpenAI",
        "default_model": "gpt-4o",
        "auth_type": ProviderAuthType.API_KEY,
        "is_production_ready": True,
    },
    "bedrock": {
        "display_name": "Amazon Bedrock",
        "default_model": "amazon.nova-pro-v1:0",
        "auth_type": ProviderAuthType.IAM_ROLE,
        "is_production_ready": True,
        "default_region": "us-east-1",
    },
    "anthropic": {
        "display_name": "Anthropic",
        "default_model": "claude-sonnet-4-5",
        "auth_type": ProviderAuthType.API_KEY,
        "is_production_ready": True,
    },
    "gemini": {
        "display_name": "Google Gemini",
        "default_model": "gemini-2.5-flash",
        "auth_type": ProviderAuthType.API_KEY,
        "is_production_ready": True,
    },
    "test": {
        "display_name": "Test Provider",
        "default_model": "premium",
        "auth_type": ProviderAuthType.NONE,
        "is_production_ready": False,
    },
}


class ProviderRegistry:
    """Manages provider configurations and adapter instances."""

    def __init__(self, catalog: PriceCatalog, settings: Any = None) -> None:
        self.catalog = catalog
        self.settings = settings
        self._configs: dict[str, ProviderConfig] = {}
        self._secrets: dict[str, str] = {}
        self._adapters: dict[str, ProviderAdapter] = {}
        self._initialize_defaults()

    def _initialize_defaults(self) -> None:
        """Initialize provider configurations from settings if available."""
        # 1. OpenAI
        openai_key = getattr(self.settings, "openai_api_key", None)
        openai_configured = bool(openai_key)
        if openai_key:
            self._secrets["openai"] = openai_key
        self._configs["openai"] = ProviderConfig(
            provider="openai",
            display_name="OpenAI",
            enabled=openai_configured,
            configured=openai_configured,
            default_model="gpt-4o",
            auth_type=ProviderAuthType.API_KEY,
            masked_api_key=mask_secret(openai_key) if openai_key else None,
            is_production_ready=True,
        )

        # 2. Bedrock
        bedrock_enabled = getattr(self.settings, "bedrock_enabled", False)
        bedrock_region = getattr(self.settings, "bedrock_region", "us-east-1")
        self._configs["bedrock"] = ProviderConfig(
            provider="bedrock",
            display_name="Amazon Bedrock",
            enabled=bool(bedrock_enabled),
            configured=bool(bedrock_enabled),
            default_model="amazon.nova-pro-v1:0",
            auth_type=ProviderAuthType.IAM_ROLE,
            region=bedrock_region,
            is_production_ready=True,
        )

        # 3. Anthropic
        anthropic_key = getattr(self.settings, "anthropic_api_key", None)
        anthropic_configured = bool(anthropic_key)
        if anthropic_key:
            self._secrets["anthropic"] = anthropic_key
        self._configs["anthropic"] = ProviderConfig(
            provider="anthropic",
            display_name="Anthropic",
            enabled=anthropic_configured,
            configured=anthropic_configured,
            default_model="claude-sonnet-4-5",
            auth_type=ProviderAuthType.API_KEY,
            masked_api_key=mask_secret(anthropic_key) if anthropic_key else None,
            is_production_ready=True,
        )

        # 4. Gemini
        gemini_key = getattr(self.settings, "gemini_api_key", None)
        gemini_configured = bool(gemini_key)
        if gemini_key:
            self._secrets["gemini"] = gemini_key
        self._configs["gemini"] = ProviderConfig(
            provider="gemini",
            display_name="Google Gemini",
            enabled=gemini_configured,
            configured=gemini_configured,
            default_model="gemini-2.5-flash",
            auth_type=ProviderAuthType.API_KEY,
            masked_api_key=mask_secret(gemini_key) if gemini_key else None,
            is_production_ready=True,
        )

        # 5. Test provider
        test_enabled = getattr(self.settings, "enable_fake_provider", False)
        self._configs["test"] = ProviderConfig(
            provider="test",
            display_name="Test Provider",
            enabled=bool(test_enabled),
            configured=True,
            default_model="premium",
            auth_type=ProviderAuthType.NONE,
            test_params={"input_tokens": 1000, "output_tokens": 1000, "latency_ms": 0},
            is_production_ready=False,
            connection_status=ProviderConnectionStatus.HEALTHY if test_enabled else ProviderConnectionStatus.UNTESTED,
        )

        self._rebuild_all_adapters()

    def list_providers(self) -> list[ProviderConfig]:
        """Return all provider configurations with masked secrets."""
        return [
            self._configs[p]
            for p in ("openai", "bedrock", "anthropic", "gemini", "test")
            if p in self._configs
        ]

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        return self._configs.get(provider_id.lower())

    def update_provider(self, provider_id: str, updates: dict[str, Any]) -> ProviderConfig:
        """Update provider configuration. Never logs raw secrets."""
        provider_id = provider_id.lower()
        if provider_id not in SUPPORTED_PROVIDERS:
            raise LookupError(f"unsupported provider: {provider_id}")

        current = self._configs[provider_id]
        meta = SUPPORTED_PROVIDERS[provider_id]

        default_model = updates.get("default_model", current.default_model)
        if default_model and not self.catalog.has(provider_id, default_model):
            raise ValueError(
                f"model {default_model!r} is not in the price catalog for provider {provider_id!r}"
            )

        new_secret = updates.get("api_key")
        masked_key = current.masked_api_key
        is_configured = current.configured

        if meta["auth_type"] == ProviderAuthType.API_KEY:
            if new_secret and "••" not in new_secret:
                clean_secret = new_secret.strip()
                if clean_secret:
                    self._secrets[provider_id] = clean_secret
                    masked_key = mask_secret(clean_secret)
                    is_configured = True
            elif "api_key" in updates and not new_secret:
                self._secrets.pop(provider_id, None)
                masked_key = None
                is_configured = False
        elif meta["auth_type"] == ProviderAuthType.IAM_ROLE:
            # Bedrock is configured if IAM role / region is set
            is_configured = True

        enabled = updates.get("enabled", current.enabled)
        if enabled and not is_configured and meta["auth_type"] == ProviderAuthType.API_KEY:
            raise ValueError(f"cannot enable {provider_id} without an API key")

        region = updates.get("region", current.region)
        base_url = updates.get("base_url", current.base_url)
        organization_id = updates.get("organization_id", current.organization_id)
        test_params = updates.get("test_params", current.test_params)

        updated = ProviderConfig(
            provider=provider_id,
            display_name=meta["display_name"],
            enabled=enabled,
            configured=is_configured,
            default_model=default_model,
            auth_type=meta["auth_type"],
            masked_api_key=masked_key,
            region=region,
            base_url=base_url,
            organization_id=organization_id,
            test_params=dict(test_params),
            connection_status=current.connection_status,
            last_tested_at=current.last_tested_at,
            last_error=current.last_error,
            is_production_ready=meta["is_production_ready"],
        )

        self._configs[provider_id] = updated
        self._rebuild_adapter(provider_id)
        return updated

    def enable_provider(self, provider_id: str) -> ProviderConfig:
        provider_id = provider_id.lower()
        if provider_id not in self._configs:
            raise LookupError(f"unknown provider: {provider_id}")

        cfg = self._configs[provider_id]
        if not cfg.configured:
            raise ValueError(f"cannot enable {provider_id}: provider is not configured")

        return self.update_provider(provider_id, {"enabled": True})

    def disable_provider(self, provider_id: str) -> ProviderConfig:
        provider_id = provider_id.lower()
        if provider_id not in self._configs:
            raise LookupError(f"unknown provider: {provider_id}")

        return self.update_provider(provider_id, {"enabled": False})

    async def test_connection(self, provider_id: str, model_id: str | None = None) -> ProviderTestResult:
        """Test connection to provider without exposing secrets or making billable calls."""
        provider_id = provider_id.lower()
        if provider_id not in self._configs:
            raise LookupError(f"unknown provider: {provider_id}")

        cfg = self._configs[provider_id]
        model = model_id or cfg.default_model
        now = datetime.now(UTC)

        # Validate model in catalog
        if not self.catalog.has(provider_id, model):
            res = ProviderTestResult(
                provider=provider_id,
                status=ProviderConnectionStatus.UNHEALTHY,
                model=model,
                authentication="invalid",
                checked_at=now,
                message=f"Model {model!r} is not registered in the pricing catalog",
                error_type="invalid_model",
            )
            self._record_test_result(provider_id, res)
            return res

        # Check authentication requirements
        if cfg.auth_type == ProviderAuthType.API_KEY:
            secret = self._secrets.get(provider_id)
            if not secret:
                res = ProviderTestResult(
                    provider=provider_id,
                    status=ProviderConnectionStatus.UNHEALTHY,
                    model=model,
                    authentication="missing",
                    checked_at=now,
                    message=f"API key is not configured for {cfg.display_name}",
                    error_type="missing_credentials",
                )
                self._record_test_result(provider_id, res)
                return res

            adapter = self._build_adapter_instance(provider_id)
            if adapter is None:
                res = ProviderTestResult(
                    provider=provider_id,
                    status=ProviderConnectionStatus.UNHEALTHY,
                    model=model,
                    authentication="invalid",
                    checked_at=now,
                    message=f"Could not initialize adapter for {cfg.display_name}",
                    error_type="initialization_error",
                )
                self._record_test_result(provider_id, res)
                return res

            try:
                healthy = await adapter.health()
                status = ProviderConnectionStatus.HEALTHY if healthy else ProviderConnectionStatus.UNHEALTHY
                msg = "Connection test successful" if healthy else "Provider health check returned false"
                res = ProviderTestResult(
                    provider=provider_id,
                    status=status,
                    model=model,
                    authentication="valid" if healthy else "unverified",
                    checked_at=now,
                    message=msg,
                    error_type=None if healthy else "health_check_failed",
                )
                self._record_test_result(provider_id, res)
                return res
            except Exception as exc:
                res = ProviderTestResult(
                    provider=provider_id,
                    status=ProviderConnectionStatus.UNHEALTHY,
                    model=model,
                    authentication="failed",
                    checked_at=now,
                    message=f"Connection test failed: {exc!s}",
                    error_type="connection_failed",
                )
                self._record_test_result(provider_id, res)
                return res

        elif cfg.auth_type == ProviderAuthType.IAM_ROLE:
            # Bedrock IAM check
            adapter = self._build_adapter_instance(provider_id)
            try:
                healthy = await adapter.health() if adapter else False
                status = ProviderConnectionStatus.HEALTHY if healthy else ProviderConnectionStatus.UNHEALTHY
                msg = (
                    "Bedrock IAM credentials verified"
                    if healthy
                    else "AWS credentials/role not found in ambient environment (production ECS uses Task Role)"
                )
                res = ProviderTestResult(
                    provider=provider_id,
                    status=status,
                    model=model,
                    authentication="iam_role",
                    checked_at=now,
                    message=msg,
                    error_type=None if healthy else "aws_credentials_missing",
                )
                self._record_test_result(provider_id, res)
                return res
            except Exception as exc:
                res = ProviderTestResult(
                    provider=provider_id,
                    status=ProviderConnectionStatus.UNHEALTHY,
                    model=model,
                    authentication="iam_role",
                    checked_at=now,
                    message=f"Bedrock verification error: {exc!s}",
                    error_type="connection_failed",
                )
                self._record_test_result(provider_id, res)
                return res

        else:
            # Test provider
            res = ProviderTestResult(
                provider=provider_id,
                status=ProviderConnectionStatus.HEALTHY,
                model=model,
                authentication="none",
                checked_at=now,
                message="Test provider is operational for development and testing",
            )
            self._record_test_result(provider_id, res)
            return res

    def _record_test_result(self, provider_id: str, result: ProviderTestResult) -> None:
        cfg = self._configs.get(provider_id)
        if cfg:
            self._configs[provider_id] = ProviderConfig(
                provider=cfg.provider,
                display_name=cfg.display_name,
                enabled=cfg.enabled,
                configured=cfg.configured,
                default_model=cfg.default_model,
                auth_type=cfg.auth_type,
                masked_api_key=cfg.masked_api_key,
                region=cfg.region,
                base_url=cfg.base_url,
                organization_id=cfg.organization_id,
                test_params=dict(cfg.test_params),
                connection_status=result.status,
                last_tested_at=result.checked_at,
                last_error=result.message if result.status == ProviderConnectionStatus.UNHEALTHY else None,
                is_production_ready=cfg.is_production_ready,
            )

    def _rebuild_all_adapters(self) -> None:
        for provider_id in ("openai", "bedrock", "anthropic", "gemini", "test"):
            self._rebuild_adapter(provider_id)

    def _rebuild_adapter(self, provider_id: str) -> None:
        cfg = self._configs.get(provider_id)
        if not cfg or not cfg.enabled:
            self._adapters.pop(provider_id, None)
            return

        adapter = self._build_adapter_instance(provider_id)
        if adapter:
            self._adapters[provider_id] = adapter
        else:
            self._adapters.pop(provider_id, None)

    def _build_adapter_instance(self, provider_id: str) -> ProviderAdapter | None:
        cfg = self._configs.get(provider_id)
        if not cfg:
            return None

        if provider_id == "openai":
            secret = self._secrets.get("openai")
            if not secret:
                return None
            from .openai_adapter import OpenAIAdapter

            return OpenAIAdapter(
                api_key=secret,
                catalog=self.catalog,
                base_url=cfg.base_url or DEFAULT_BASE_URL,
                organization=cfg.organization_id or None,
            )

        elif provider_id == "anthropic":
            secret = self._secrets.get("anthropic")
            if not secret:
                return None
            from .anthropic_adapter import AnthropicAdapter

            return AnthropicAdapter(api_key=secret, catalog=self.catalog)

        elif provider_id == "gemini":
            secret = self._secrets.get("gemini")
            if not secret:
                return None
            from .gemini_adapter import DEFAULT_BASE_URL as GEMINI_BASE_URL
            from .gemini_adapter import GeminiAdapter

            return GeminiAdapter(
                api_key=secret,
                catalog=self.catalog,
                base_url=cfg.base_url or GEMINI_BASE_URL,
            )

        elif provider_id == "bedrock":
            from .bedrock_adapter import BedrockAdapter

            return BedrockAdapter(catalog=self.catalog, region=cfg.region or "us-east-1")

        elif provider_id == "test":
            from .fake import FakeBehaviour, FakeProvider

            params = cfg.test_params or {}
            return FakeProvider(
                FakeBehaviour(
                    input_tokens=params.get("input_tokens", 1000),
                    output_tokens=params.get("output_tokens", 1000),
                )
            )

        return None

    def get_active_adapters(self) -> dict[str, ProviderAdapter]:
        """Active adapters ready for inference."""
        return dict(self._adapters)

    def get_catalog_models(self, provider_id: str | None = None) -> list[dict[str, Any]]:
        """Return catalog models with capability and pricing information."""
        result: list[dict[str, Any]] = []
        for entry in self.catalog.entries.values():
            if provider_id and entry.provider.lower() != provider_id.lower():
                continue
            caps = entry.capabilities
            result.append(
                {
                    "provider": entry.provider,
                    "model": entry.model,
                    "status": "ACTIVE",
                    "input_per_million": Money(entry.input_nano_per_mtok).to_usd_str(),
                    "output_per_million": Money(entry.output_nano_per_mtok).to_usd_str(),
                    "cached_input_per_million": Money(entry.cached_input_nano_per_mtok).to_usd_str(),
                    "max_context_tokens": caps.max_context_tokens,
                    "max_output_tokens": caps.max_output_tokens,
                    "supports_tools": caps.supports_tools,
                    "supports_structured_output": caps.supports_structured_output,
                    "supports_vision": caps.supports_vision,
                    "supports_reasoning": caps.supports_reasoning,
                    "preflight_token_counting": True,
                    "catalog_version": self.catalog.version,
                }
            )
        return sorted(result, key=lambda x: (x["provider"], x["model"]))

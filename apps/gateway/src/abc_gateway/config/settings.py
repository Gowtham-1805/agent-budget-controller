"""Configuration.

Everything is environment-driven so the same image runs locally and in ECS. No
secret has a default value -- an unset key means the corresponding adapter is
simply not registered, which is a clearer failure than silently starting with a
placeholder credential.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[5]


class Settings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="ABC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    environment: str = "local"
    log_level: str = "INFO"
    service_name: str = "agent-budget-controller"

    # -- budget store ------------------------------------------------------
    aws_region: str = "us-east-1"
    table_core: str = "abc_core"
    table_ledger: str = "abc_ledger"
    #: Point at DynamoDB Local for offline development; empty means real AWS.
    dynamodb_endpoint_url: str | None = None
    #: Use the in-process store. Development and tests only -- state does not
    #: survive a restart, which defeats the entire purpose in production.
    use_memory_store: bool = False

    # -- enforcement -------------------------------------------------------
    #: Injected when a client omits an output limit. Without it, omitting
    #: max_tokens would be a way to bypass spend protection entirely.
    default_max_output_tokens: int = 4096
    #: Extra margin for models that cannot enforce a hard output cap.
    overshoot_safety_bps: int = 200
    provider_connect_timeout_s: float = 10.0
    provider_read_timeout_s: float = 120.0
    reservation_ttl_minutes: int = 20

    price_catalog_path: str = str(REPO_ROOT / "pricing" / "catalog.json")

    # -- provider credentials ---------------------------------------------
    # Read without the ABC_ prefix, matching each provider's own convention.
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    bedrock_enabled: bool = False
    bedrock_region: str = "us-east-1"
    #: Register the deterministic fake provider under the name "test".
    #: Development and demos only -- it returns canned usage, so any budget it
    #: appears to enforce is fictional. Refused outright in production.
    enable_fake_provider: bool = False

    # -- observability -----------------------------------------------------
    # Strictly outside the authorization path. If any of this is unavailable,
    # enforcement must continue unaffected.
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = Field(default=None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = Field(default=None, alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", alias="LANGFUSE_HOST")
    otel_exporter_otlp_endpoint: str | None = None
    #: Prompt and response capture, off by default. Budget governance needs
    #: usage and identity metadata, not the content of the conversation.
    trace_content: bool = False

    # -- auth --------------------------------------------------------------
    #: Bootstrap admin key for the control plane. In a deployed environment this
    #: comes from Secrets Manager.
    admin_api_key: str | None = Field(default=None, alias="ABC_ADMIN_API_KEY")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Domain models for LLM provider configuration and status."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ProviderAuthType(StrEnum):
    API_KEY = "api_key"
    IAM_ROLE = "iam_role"
    NONE = "none"


class ProviderConnectionStatus(StrEnum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNTESTED = "untested"


def mask_secret(secret: str | None) -> str:
    """Mask an API key so secrets are never returned over the API or logged.

    Examples:
        sk-1234567890abcdef1234567890abcdef7F2A -> sk-••••••••••••••••••••7F2A
        short-key-1234 -> ••••••••1234
    """
    if not secret:
        return ""
    secret = secret.strip()
    if len(secret) <= 8:
        return "••••" + secret[-2:] if len(secret) >= 2 else "••••"

    prefix = ""
    if secret.startswith("sk-"):
        prefix = "sk-"
        remainder = secret[3:]
    elif secret.startswith("anthropic-"):
        prefix = "anthropic-"
        remainder = secret[10:]
    else:
        remainder = secret

    suffix = remainder[-4:] if len(remainder) >= 4 else remainder
    return f"{prefix}••••••••••••••••••••{suffix}"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """A configured LLM provider."""

    provider: str
    display_name: str
    enabled: bool
    configured: bool
    default_model: str
    auth_type: ProviderAuthType
    masked_api_key: str | None = None
    region: str | None = None
    base_url: str | None = None
    organization_id: str | None = None
    test_params: dict[str, Any] = field(default_factory=dict)
    connection_status: ProviderConnectionStatus = ProviderConnectionStatus.UNTESTED
    last_tested_at: datetime | None = None
    last_error: str | None = None
    is_production_ready: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "configured": self.configured,
            "default_model": self.default_model,
            "auth_type": self.auth_type.value,
            "masked_api_key": self.masked_api_key,
            "region": self.region,
            "base_url": self.base_url,
            "organization_id": self.organization_id,
            "test_params": dict(self.test_params),
            "connection_status": self.connection_status.value,
            "last_tested_at": self.last_tested_at.isoformat() if self.last_tested_at else None,
            "last_error": self.last_error,
            "is_production_ready": self.is_production_ready,
        }


@dataclass(frozen=True, slots=True)
class ProviderTestResult:
    """Outcome of a provider connection test."""

    provider: str
    status: ProviderConnectionStatus
    model: str
    authentication: str
    checked_at: datetime
    message: str
    error_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "provider": self.provider,
            "status": self.status.value,
            "model": self.model,
            "authentication": self.authentication,
            "checked_at": self.checked_at.isoformat(),
            "message": self.message,
        }
        if self.error_type:
            res["error_type"] = self.error_type
        return res

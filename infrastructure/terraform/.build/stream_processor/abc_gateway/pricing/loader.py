"""Parse a price catalog file into exact integer rates.

Decimal is used here and only here. Catalog files express prices the way humans
and provider pricing pages do -- ``"2.50"`` dollars per million tokens -- and
converting that to an exact integer count of nano-USD is the one place where
decimal parsing belongs. Past this boundary every value is an ``int``.

Conversion is required to be exact. A rate that does not land on a whole
nano-USD is rejected rather than rounded, because a silently rounded rate would
produce a catalog whose arithmetic disagrees with the provider's invoice by an
amount that grows with volume.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..domain.money import NANO_PER_USD
from .catalog import (
    BASIS_POINTS,
    ModelCapabilities,
    ModelPrice,
    PriceCatalog,
    PricingError,
)


class CatalogFormatError(PricingError):
    """The catalog file is malformed."""


def load_catalog(path: str | Path) -> PriceCatalog:
    """Load and validate a catalog file."""
    file_path = Path(path)
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogFormatError(f"price catalog not found: {file_path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogFormatError(f"price catalog is not valid JSON: {exc}") from exc
    return parse_catalog(raw, source=str(file_path))


def parse_catalog(raw: dict[str, Any], *, source: str = "inline") -> PriceCatalog:
    version = _require_str(raw, "version")
    published_at = _parse_timestamp(raw.get("published_at"))

    models = raw.get("models")
    if not isinstance(models, list) or not models:
        raise CatalogFormatError("catalog must contain a non-empty 'models' list")

    entries: dict[str, ModelPrice] = {}
    for index, item in enumerate(models):
        if not isinstance(item, dict):
            raise CatalogFormatError(f"models[{index}] is not an object")
        price = _parse_model(item, index)
        if price.key in entries:
            raise CatalogFormatError(f"duplicate price entry for {price.key}")
        entries[price.key] = price

    return PriceCatalog(
        version=version,
        published_at=published_at,
        entries=entries,
        source=source,
    )


def _parse_model(item: dict[str, Any], index: int) -> ModelPrice:
    provider = _require_str(item, "provider", context=f"models[{index}]")
    model = _require_str(item, "model", context=f"models[{index}]")
    where = f"{provider}::{model}"

    if item.get("status", "ACTIVE").upper() != "ACTIVE":
        raise CatalogFormatError(f"{where}: only ACTIVE entries may be loaded")

    caps_raw = item.get("capabilities") or {}
    if not isinstance(caps_raw, dict):
        raise CatalogFormatError(f"{where}: 'capabilities' must be an object")

    capabilities = ModelCapabilities(
        max_context_tokens=_require_int(caps_raw, "max_context_tokens", where),
        max_output_tokens=_require_int(caps_raw, "max_output_tokens", where),
        supports_tools=bool(caps_raw.get("supports_tools", True)),
        supports_structured_output=bool(caps_raw.get("supports_structured_output", True)),
        supports_vision=bool(caps_raw.get("supports_vision", False)),
        supports_reasoning=bool(caps_raw.get("supports_reasoning", False)),
        supports_hard_output_cap=bool(caps_raw.get("supports_hard_output_cap", True)),
    )

    long_threshold = item.get("long_context_threshold")
    if long_threshold is not None and not isinstance(long_threshold, int):
        raise CatalogFormatError(f"{where}: long_context_threshold must be an integer or null")

    return ModelPrice(
        provider=provider,
        model=model,
        input_nano_per_mtok=_usd_to_nano(item, "input_per_million", where),
        output_nano_per_mtok=_usd_to_nano(item, "output_per_million", where),
        cached_input_nano_per_mtok=_usd_to_nano(
            item, "cached_input_per_million", where, default="0"
        ),
        cache_write_nano_per_mtok=_usd_to_nano(item, "cache_write_per_million", where, default="0"),
        min_charge_nano=_usd_to_nano(item, "min_charge_usd", where, default="0"),
        long_context_threshold=long_threshold,
        long_context_input_multiplier_bp=_multiplier_bp(
            item.get("long_context_input_multiplier"), where
        ),
        long_context_output_multiplier_bp=_multiplier_bp(
            item.get("long_context_output_multiplier"), where
        ),
        capabilities=capabilities,
    )


def _usd_to_nano(
    item: dict[str, Any],
    field: str,
    where: str,
    *,
    default: str | None = None,
) -> int:
    value = item.get(field, default)
    if value is None:
        raise CatalogFormatError(f"{where}: missing required price field '{field}'")
    if isinstance(value, float):
        # Rejected rather than accepted-and-converted: a float literal in the
        # catalog has already lost precision before we ever see it.
        raise CatalogFormatError(
            f"{where}.{field} must be a string or integer, not a float, so the value is exact"
        )
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ArithmeticError) as exc:
        raise CatalogFormatError(f"{where}.{field} is not a valid decimal: {value!r}") from exc

    if dec < 0:
        raise CatalogFormatError(f"{where}.{field} cannot be negative")

    scaled = dec * NANO_PER_USD
    if scaled != scaled.to_integral_value():
        raise CatalogFormatError(
            f"{where}.{field} = {value!r} is finer than one nano-USD and "
            f"cannot be represented exactly"
        )
    return int(scaled)


def _multiplier_bp(value: Any, where: str) -> int:
    if value is None:
        return BASIS_POINTS
    if isinstance(value, float):
        raise CatalogFormatError(f"{where}: multipliers must be strings, not floats")
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ArithmeticError) as exc:
        raise CatalogFormatError(f"{where}: invalid multiplier {value!r}") from exc
    scaled = dec * BASIS_POINTS
    if scaled != scaled.to_integral_value():
        raise CatalogFormatError(f"{where}: multiplier {value!r} is finer than one basis point")
    if scaled <= 0:
        raise CatalogFormatError(f"{where}: multiplier must be positive")
    return int(scaled)


def _require_str(raw: dict[str, Any], key: str, context: str = "catalog") -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise CatalogFormatError(f"{context}: '{key}' must be a non-empty string")
    return value


def _require_int(raw: dict[str, Any], key: str, context: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise CatalogFormatError(f"{context}: capabilities.{key} must be a positive integer")
    return value


def _parse_timestamp(value: Any) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if not isinstance(value, str):
        raise CatalogFormatError("'published_at' must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogFormatError(f"'published_at' is not ISO-8601: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

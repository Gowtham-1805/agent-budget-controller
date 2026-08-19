"""Structured logging.

Every log line carries the request id, so a single governed call can be followed
from authorization through provider invocation to reconciliation in CloudWatch
Logs Insights.

Money is always rendered from its integer nano value. A log line that prints a
float would eventually disagree with the ledger, and a discrepancy between what
the logs say and what the books say is the worst possible thing to discover
during an incident.

Prompt and response content is never logged. Budget governance needs usage,
identity, pricing and routing metadata; copying conversation content into the
governance plane creates a data-protection problem that buys nothing.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from ..domain.money import Money

#: Bound once per request and attached to every subsequent line automatically.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def money(value: Money | None) -> str | None:
    """Render money for logs, exactly, from the integer."""
    return value.to_usd_str() if value is not None else None


def _add_request_id(_logger, _method, event_dict: dict[str, Any]) -> dict[str, Any]:
    request_id = request_id_var.get()
    if request_id:
        event_dict.setdefault("request_id", request_id)
    return event_dict


def _reject_floats(_logger, _method, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Convert stray floats to strings rather than let them look authoritative.

    A float in a log line is either a latency (fine) or a monetary value
    (not fine). Rather than guess, anything named like money is rendered
    through the exact path and flagged if it arrives as a float.
    """
    for key, value in list(event_dict.items()):
        if isinstance(value, float) and ("usd" in key or "cost" in key or "nano" in key):
            event_dict[key] = f"{value!r}"
            event_dict["_float_money_warning"] = key
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog for the process."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _reject_floats,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "abc_gateway"):
    return structlog.get_logger(name)


def log_request_decision(
    logger,
    *,
    event: str,
    request_id: str,
    tenant_id: str,
    team_id: str,
    agent_id: str,
    session_id: str | None,
    provider: str,
    requested_model: str,
    effective_model: str,
    decision: str,
    preflight_input_tokens: int | None = None,
    reserved_output_tokens: int | None = None,
    actual_input_tokens: int | None = None,
    actual_output_tokens: int | None = None,
    estimated_max_cost: Money | None = None,
    reserved: Money | None = None,
    actual_cost: Money | None = None,
    latency_ms: float | None = None,
    status: str = "ok",
    **extra: Any,
) -> None:
    """Emit the canonical per-request record.

    One shape for every governed request, so a dashboard query does not have to
    know which code path produced the line.
    """
    logger.info(
        event,
        request_id=request_id,
        tenant_id=tenant_id,
        team_id=team_id,
        agent_id=agent_id,
        session_id=session_id,
        provider=provider,
        requested_model=requested_model,
        effective_model=effective_model,
        budget_decision=decision,
        preflight_input_tokens=preflight_input_tokens,
        reserved_output_tokens=reserved_output_tokens,
        actual_input_tokens=actual_input_tokens,
        actual_output_tokens=actual_output_tokens,
        estimated_max_cost_usd=money(estimated_max_cost),
        reserved_usd=money(reserved),
        actual_total_cost_usd=money(actual_cost),
        latency_ms=round(latency_ms, 2) if latency_ms is not None else None,
        status=status,
        **extra,
    )

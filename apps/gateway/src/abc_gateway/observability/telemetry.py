"""Langfuse and metrics -- deliberately fire-and-forget.

The rule this module exists to enforce: **observability must never be able to
break enforcement.** If Langfuse is down, slow, misconfigured, or returning
errors, governed requests must continue to be authorised and settled exactly as
before. An outage in the system that *explains* spending must not become an
outage in the system that *controls* it.

That is why every emission is wrapped, every failure is swallowed after being
counted, and nothing here is ever awaited on the authorization path. There is a
`telemetry_failures_total` counter precisely so that "we are flying blind" is
itself visible rather than silent.

Content is not sent by default. Token counts, costs, latency, model choice and
routing decisions explain a run perfectly well without copying the conversation
into a third-party system.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..domain.money import Money
from .logging import get_logger

logger = get_logger("abc_gateway.telemetry")

#: CloudWatch EMF caps a single document at 100 metrics; the counters here are
#: a handful of fixed names plus a small dimension fan-out, nowhere near that,
#: but the guard exists so a future dimension explosion fails loudly in a log
#: line instead of silently dropping metrics CloudWatch would otherwise reject.
_EMF_MAX_METRICS = 100


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Identifiers propagated across every observability system.

    The same values appear in CloudWatch logs, the ledger and Langfuse, so an
    incident can be followed across all three without guessing at correlations.
    """

    request_id: str
    tenant_id: str
    team_id: str
    agent_id: str
    session_id: str | None
    requested_model: str
    effective_model: str
    provider: str
    budget_decision: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Metrics:
    """In-process counters, exported as CloudWatch EMF.

    Deliberately trivial. Metrics are useful but never load-bearing, so this
    does not deserve a dependency that could fail.

    Each call to :meth:`increment` both updates the in-process running total
    (``snapshot()``, used by tests and by anything introspecting current
    counts) *and* emits one CloudWatch Embedded Metric Format log line for
    that single increment. EMF needs no SDK call and no extra network hop: it
    is a JSON line carrying an ``_aws`` directive that tells the CloudWatch
    Logs agent which top-level fields are metrics, so the ECS task's existing
    stdout -> `awslogs` pipe doubles as the metrics pipe.

    The EMF value on each line is deliberately the increment (usually 1), not
    the running total: CloudWatch treats every EMF line as a new datapoint and
    sums same-period values, so logging the cumulative counter on every call
    would make graphs climb by the *sum of all prior totals* rather than by
    the actual event rate.
    """

    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()

    def increment(self, name: str, value: int = 1, **dimensions: str) -> None:
        key = (
            name
            if not dimensions
            else f"{name}|" + ",".join(f"{k}={v}" for k, v in sorted(dimensions.items()))
        )
        self._counters[key] += value
        self._emit_emf(name, value, dimensions)

    def snapshot(self) -> dict[str, int]:
        return dict(self._counters)

    def reset(self) -> None:
        self._counters.clear()

    def _emit_emf(
        self, name: str, value: int, dimensions: dict[str, str], *, namespace: str = "AgentBudgetController"
    ) -> None:
        """Write one CloudWatch EMF record for a single metric event.

        Writing to stdout cannot block the request and must never raise in a
        way that reaches the caller, so the whole thing is wrapped -- a metric
        that fails to serialise is a lost datapoint, not an incident.
        (`infrastructure/terraform/iam.tf` also grants `cloudwatch:PutMetricData`
        directly, as an unused defence-in-depth path; this EMF line is what
        actually carries the metric today.)
        """
        try:
            dim_names = sorted(dimensions)[:_EMF_MAX_METRICS]
            document = {
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": namespace,
                            "Dimensions": [dim_names] if dim_names else [[]],
                            "Metrics": [{"Name": name, "Unit": "Count"}],
                        }
                    ],
                },
                name: value,
                **dimensions,
            }
            print(json.dumps(document), flush=True)
        except Exception:
            logger.warning("telemetry.emf_emit_failed", exc_info=True)


metrics = Metrics()


class TelemetrySink:
    """Async, best-effort export to Langfuse.

    Every public method returns immediately. Work happens on a background task
    whose failures are logged and counted but never raised.
    """

    def __init__(self, *, enabled: bool = False, client: Any = None) -> None:
        self.enabled = enabled
        self._client = client
        self._tasks: set[asyncio.Task] = set()

    def emit(
        self,
        context: TraceContext,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        estimated_cost: Money | None = None,
        actual_cost: Money | None = None,
        latency_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        """Record one governed request. Never blocks, never raises."""
        metrics.increment("budget_requests_total", decision=context.budget_decision)
        if not self.enabled or self._client is None:
            return

        payload = {
            "name": "governed-llm-request",
            "id": context.request_id,
            "session_id": context.session_id,
            "user_id": context.agent_id,
            "metadata": {
                "tenant_id": context.tenant_id,
                "team_id": context.team_id,
                "agent_id": context.agent_id,
                "requested_model": context.requested_model,
                "effective_model": context.effective_model,
                "provider": context.provider,
                "budget_decision": context.budget_decision,
                **context.metadata,
            },
            "usage": {"input": input_tokens, "output": output_tokens},
            # Rendered from integers; the trace must agree with the ledger.
            "estimated_cost_usd": estimated_cost.to_usd_str() if estimated_cost else None,
            "actual_cost_usd": actual_cost.to_usd_str() if actual_cost else None,
            "latency_ms": latency_ms,
            "error": error,
        }
        self._spawn(payload)

    def _spawn(self, payload: dict[str, Any]) -> None:
        try:
            task = asyncio.create_task(self._send(payload))
        except RuntimeError:
            # No running loop (e.g. a synchronous worker). Telemetry is
            # optional; enforcement is not.
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, payload: dict[str, Any]) -> None:
        try:
            await self._client.emit(payload)
        except Exception as exc:
            # Counted so that "telemetry is broken" is itself observable rather
            # than a silent gap in the traces.
            metrics.increment("telemetry_failures_total")
            logger.warning(
                "telemetry.export_failed",
                error=str(exc),
                request_id=payload.get("id"),
            )

    async def drain(self, timeout: float = 2.0) -> None:
        """Wait briefly for in-flight exports, on shutdown only."""
        if not self._tasks:
            return
        await asyncio.wait(self._tasks, timeout=timeout)


def build_sink(settings) -> TelemetrySink:
    """Construct the telemetry sink for this deployment.

    A misconfigured or unreachable Langfuse yields a disabled sink rather than a
    failed startup -- refusing to boot the spend controller because the tracing
    backend is down would invert the priorities entirely.
    """
    if not settings.langfuse_enabled:
        return TelemetrySink(enabled=False)
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.warning("telemetry.disabled", reason="langfuse credentials not configured")
        return TelemetrySink(enabled=False)

    try:
        from langfuse import Langfuse

        client = _LangfuseClient(
            Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
        )
        return TelemetrySink(enabled=True, client=client)
    except Exception as exc:
        logger.warning("telemetry.init_failed", error=str(exc))
        return TelemetrySink(enabled=False)


class _LangfuseClient:
    """Thin adapter so the sink does not depend on Langfuse's API shape."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def emit(self, payload: dict[str, Any]) -> None:
        # Langfuse's SDK buffers and flushes in the background; the thread hop
        # keeps its synchronous call off the event loop.
        await asyncio.to_thread(
            self._client.create_event,
            name=payload["name"],
            metadata=payload["metadata"],
        )

"""Runaway-agent circuit breaker.

The rule: an agent that spends more than 20% of its monthly budget inside a
rolling 60-minute window is paused for human review.

Two design choices carry most of the weight.

**Rolling, not calendar-aligned.** Hourly buckets that reset on the hour have a
blind spot precisely where it matters:

    11:50-11:59   spend $7
    12:00-12:10   spend $7

Two calendar hours each see $7 and neither trips a $10 threshold. A rolling
window sees $14 and does. Bursts that straddle the boundary are exactly the
shape a recursive loop produces, so the cheaper implementation is blind to the
case it exists to catch. Spend is therefore accumulated into one-minute buckets
and the last sixty are summed.

**Idempotent, because delivery is at-least-once.** DynamoDB Streams can deliver
the same record more than once. Every ledger entry carries a stable
``entry_id``, and a marker item records that it has been counted -- so a
redelivery adds nothing, and a duplicate cannot manufacture a runaway that never
happened.

The rule stays deterministic on purpose. An anomaly model might catch more, but
a control that pauses production workloads has to be explainable to the person
whose agent was paused and reproducible by whoever reviews it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain.agent import AgentStatus, RunawayEvent, RunawayPolicy
from ..domain.alerts import AlertEvent, AlertKind
from ..domain.ledger import LedgerKind, UsageLedgerEntry
from ..domain.money import Money
from ..domain.scopes import ScopeRef

#: Kinds that count toward rolling spend. PENDING_ASSUMED is included
#: deliberately: an unresolved request may well have been billed, and a runaway
#: detector should err toward noticing.
COUNTED_KINDS: frozenset[LedgerKind] = frozenset(
    {LedgerKind.USAGE, LedgerKind.PENDING_ASSUMED, LedgerKind.OVERAGE}
)


def bucket_key(at: datetime) -> str:
    """The one-minute bucket an instant falls into."""
    return at.strftime("%Y-%m-%dT%H:%M")


def window_buckets(now: datetime, minutes: int) -> list[str]:
    """Every bucket in the rolling window ending at ``now``."""
    return [bucket_key(now - timedelta(minutes=offset)) for offset in range(minutes)]


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """What the detector concluded for one ledger entry."""

    counted: bool
    duplicate: bool = False
    rolling_spend: Money = field(default_factory=Money.zero)
    threshold: Money = field(default_factory=Money.zero)
    tripped: bool = False
    paused: bool = False
    event: RunawayEvent | None = None


class RunawayDetector:
    """Accumulates rolling spend and trips the breaker."""

    def __init__(self, repository, *, policy: RunawayPolicy | None = None) -> None:
        self.repo = repository
        self.default_policy = policy or RunawayPolicy()

    async def observe(
        self,
        entry: UsageLedgerEntry,
        *,
        monthly_limit: Money,
        now: datetime,
        policy: RunawayPolicy | None = None,
    ) -> DetectionResult:
        """Record one ledger entry and decide whether the agent has run away."""
        policy = policy or self.default_policy
        if not policy.enabled or entry.kind not in COUNTED_KINDS:
            return DetectionResult(counted=False)

        # At-least-once delivery: a redelivered entry must add nothing.
        first_time = await self.repo.claim_rolling_entry(
            entry.tenant_id, entry.entry_id, ttl_epoch=int(now.timestamp()) + 7200
        )
        if not first_time:
            return DetectionResult(counted=False, duplicate=True)

        await self.repo.add_rolling_spend(
            entry.tenant_id,
            entry.agent_id,
            bucket=bucket_key(entry.completed_at or entry.created_at),
            amount=entry.actual_total_cost,
            ttl_epoch=int(now.timestamp()) + 7200,
        )

        rolling = await self.repo.sum_rolling_spend(
            entry.tenant_id,
            entry.agent_id,
            buckets=window_buckets(now, policy.interval_minutes),
        )
        threshold = policy.threshold(monthly_limit)

        if not policy.is_runaway(rolling, monthly_limit):
            return DetectionResult(counted=True, rolling_spend=rolling, threshold=threshold)

        event = RunawayEvent(
            event_id=f"runaway#{entry.tenant_id}#{entry.agent_id}#{bucket_key(now)}",
            agent_id=entry.agent_id,
            tenant_id=entry.tenant_id,
            detected_at=now,
            rolling_spend=rolling,
            threshold=threshold,
            monthly_limit=monthly_limit,
            interval_minutes=policy.interval_minutes,
            triggering_entry_id=entry.entry_id,
        )

        # Conditional on the agent still being ACTIVE, so a second delivery --
        # or a second entry moments later -- finds it already paused and does
        # not emit a duplicate pause.
        paused = await self.repo.set_agent_status(
            entry.tenant_id,
            entry.agent_id,
            expected_status=AgentStatus.ACTIVE.value,
            new_status=AgentStatus.PAUSED_RUNAWAY.value,
            reason=(
                f"rolling {policy.interval_minutes}m spend {rolling} exceeded "
                f"{policy.monthly_budget_percent}% of the monthly budget "
                f"({threshold})"
            ),
            actor="runaway-detector",
            source_event_id=event.event_id,
        )

        if paused:
            await self.repo.record_runaway_event(
                event,
                alert=AlertEvent(
                    kind=AlertKind.RUNAWAY_DETECTED,
                    scope=ScopeRef.agent(entry.agent_id),
                    window=None,
                    occurred_at=now,
                    tenant_id=entry.tenant_id,
                    limit=monthly_limit,
                    spent=rolling,
                    triggering_reservation_id=entry.reservation_id,
                    detail={
                        "threshold": threshold.to_usd_str(),
                        "interval_minutes": str(policy.interval_minutes),
                    },
                ),
            )

        return DetectionResult(
            counted=True,
            rolling_spend=rolling,
            threshold=threshold,
            tripped=True,
            paused=paused,
            event=event,
        )

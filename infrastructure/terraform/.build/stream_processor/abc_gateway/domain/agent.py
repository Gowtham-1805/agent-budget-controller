"""Agent state and the circuit breaker.

An agent's status is checked *inside* the authorization transaction, not by a
separate read beforehand. That distinction is the whole point of the circuit
breaker: if the status check were a prior read, a pause landing between the read
and the reservation would still let one more request through, and a runaway
agent is precisely the situation where requests arrive faster than any read-then-
act sequence can keep up with.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .money import Money


class AgentStatus(StrEnum):
    """Whether an agent may currently spend."""

    ACTIVE = "ACTIVE"
    #: Tripped automatically by the runaway detector. Requires human review.
    PAUSED_RUNAWAY = "PAUSED_RUNAWAY"
    #: Paused deliberately by an operator.
    PAUSED_ADMIN = "PAUSED_ADMIN"
    DISABLED = "DISABLED"


PAUSED_STATUSES: frozenset[AgentStatus] = frozenset(
    {
        AgentStatus.PAUSED_RUNAWAY,
        AgentStatus.PAUSED_ADMIN,
        AgentStatus.DISABLED,
    }
)


@dataclass(frozen=True, slots=True)
class AgentState:
    """Runtime state of one agent."""

    agent_id: str
    tenant_id: str
    team_id: str
    status: AgentStatus
    review_required: bool = False
    paused_at: datetime | None = None
    pause_reason: str | None = None
    #: The runaway event that caused an automatic pause, for audit correlation.
    pause_source_event_id: str | None = None
    resumed_at: datetime | None = None
    resumed_by: str | None = None

    @property
    def can_spend(self) -> bool:
        return self.status is AgentStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class RunawayPolicy:
    """Deterministic runaway-detection rule.

    Kept deterministic on purpose. An anomaly model might catch more, but a
    governance control that pauses production workloads has to be explainable to
    the person whose agent was paused, and reproducible by whoever reviews it.
    """

    #: Percentage of the monthly budget that, if spent inside the window,
    #: constitutes a runaway. The challenge specifies 20.
    monthly_budget_percent: int = 20
    #: Rolling, not calendar-aligned. A burst spanning 11:50-12:10 is invisible
    #: to hourly buckets that reset on the hour, and that is exactly the shape a
    #: recursive loop produces.
    interval_minutes: int = 60
    enabled: bool = True

    def threshold(self, monthly_limit: Money) -> Money:
        """Spend inside the rolling window that trips the breaker."""
        return monthly_limit.pct_floor(self.monthly_budget_percent)

    def is_runaway(self, rolling_spend: Money, monthly_limit: Money) -> bool:
        return self.enabled and rolling_spend > self.threshold(monthly_limit)


@dataclass(frozen=True, slots=True)
class RunawayEvent:
    """A recorded circuit-breaker trip."""

    event_id: str
    agent_id: str
    tenant_id: str
    detected_at: datetime
    rolling_spend: Money
    threshold: Money
    monthly_limit: Money
    interval_minutes: int
    triggering_entry_id: str | None = None

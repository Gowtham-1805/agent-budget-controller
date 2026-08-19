"""Human review workflow for paused agents.

"Pause for human review" must not mean an operator editing a database row at
3am. It means an authenticated API call that leaves a permanent record of who
did what and why.

The audit trail is the point. After an incident, the questions that actually get
asked are: when did spend accelerate, which agent and session caused it, when
did the controller pause it, who resumed it, and on what basis. A status column
answers none of those; an append-only sequence of audit events answers all of
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..domain.agent import AgentStatus
from ..domain.alerts import AlertEvent, AlertKind
from ..domain.scopes import ScopeRef


class ReviewError(Exception):
    """The requested administrative transition was not possible."""


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """A permanent record of an administrative action."""

    tenant_id: str
    actor: str
    action: str
    target: str
    previous_state: str
    new_state: str
    reason: str
    timestamp: datetime
    request_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "actor": self.actor,
            "action": self.action,
            "target": self.target,
            "previous_state": self.previous_state,
            "new_state": self.new_state,
            "reason": self.reason,
            "timestamp": self.timestamp.isoformat(),
            "request_id": self.request_id,
        }


class ReviewService:
    """Administrative pause and resume."""

    def __init__(self, repository) -> None:
        self.repo = repository

    async def pause(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        actor: str,
        reason: str,
        now: datetime,
        request_id: str | None = None,
    ) -> AuditRecord:
        """Pause an agent deliberately."""
        state = await self.repo.get_agent_state(tenant_id, agent_id)
        if state is None:
            raise ReviewError(f"unknown agent: {agent_id}")
        if not state.can_spend:
            raise ReviewError(f"agent {agent_id} is already {state.status}")

        changed = await self.repo.set_agent_status(
            tenant_id,
            agent_id,
            expected_status=AgentStatus.ACTIVE.value,
            new_status=AgentStatus.PAUSED_ADMIN.value,
            reason=reason,
            actor=actor,
        )
        if not changed:
            raise ReviewError(f"agent {agent_id} could not be paused; it may have changed")

        return await self._audit(
            tenant_id,
            actor=actor,
            action="agent.paused",
            target=agent_id,
            previous_state=state.status.value,
            new_state=AgentStatus.PAUSED_ADMIN.value,
            reason=reason,
            now=now,
            request_id=request_id,
            alert_kind=AlertKind.AGENT_PAUSED,
        )

    async def resume(
        self,
        tenant_id: str,
        agent_id: str,
        *,
        actor: str,
        reason: str,
        now: datetime,
        request_id: str | None = None,
    ) -> AuditRecord:
        """Return a paused agent to service.

        A reason is required rather than optional. Resuming a runaway agent
        without recording why is how the same loop ships twice.
        """
        state = await self.repo.get_agent_state(tenant_id, agent_id)
        if state is None:
            raise ReviewError(f"unknown agent: {agent_id}")
        if state.can_spend:
            raise ReviewError(f"agent {agent_id} is already ACTIVE")
        if not reason.strip():
            raise ReviewError("a reason is required to resume a paused agent")

        changed = await self.repo.set_agent_status(
            tenant_id,
            agent_id,
            expected_status=state.status.value,
            new_status=AgentStatus.ACTIVE.value,
            reason=reason,
            actor=actor,
        )
        if not changed:
            raise ReviewError(f"agent {agent_id} could not be resumed; it may have changed")

        return await self._audit(
            tenant_id,
            actor=actor,
            action="agent.resumed",
            target=agent_id,
            previous_state=state.status.value,
            new_state=AgentStatus.ACTIVE.value,
            reason=reason,
            now=now,
            request_id=request_id,
            alert_kind=AlertKind.AGENT_RESUMED,
        )

    async def runaway_events(self, tenant_id: str, agent_id: str) -> tuple[Any, ...]:
        events: tuple[Any, ...] = await self.repo.get_runaway_events(tenant_id, agent_id)
        return events

    async def _audit(
        self,
        tenant_id: str,
        *,
        actor: str,
        action: str,
        target: str,
        previous_state: str,
        new_state: str,
        reason: str,
        now: datetime,
        request_id: str | None,
        alert_kind: AlertKind,
    ) -> AuditRecord:
        record = AuditRecord(
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            target=target,
            previous_state=previous_state,
            new_state=new_state,
            reason=reason,
            timestamp=now,
            request_id=request_id,
        )
        await self.repo.record_audit_event(record.as_dict())
        await self.repo.record_alert(
            AlertEvent(
                kind=alert_kind,
                scope=ScopeRef.agent(target),
                window=None,
                occurred_at=now,
                tenant_id=tenant_id,
                detail={"actor": actor, "reason": reason},
            )
        )
        return record

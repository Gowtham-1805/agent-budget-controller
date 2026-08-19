"""Post-settlement effects: threshold warnings and session closure.

These deliberately run as a *second* transaction after reconciliation, for two
reasons that are easy to miss and impossible to work around:

1. **Conditions evaluate the pre-update image.** There is no way to express "if
   this update pushes utilization past 80%" as a condition on the update that
   causes it.
2. **The flag lives on the item being updated.** ``warning_80_sent`` sits on the
   same budget item reconciliation is already writing, and a transaction may
   touch an item only once.

Running them separately introduces a gap in which the gateway could die, so the
same logic is also driven from the budget-state stream as a backstop. Both paths
are idempotent -- the flag flip is conditional and the alert's key is derived --
so running both is harmless, and that redundancy is what makes the guarantee
durable rather than best-effort.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..domain.alerts import AlertEvent, AlertKind, threshold_floor
from ..domain.errors import DenialCode
from ..domain.money import Money
from ..domain.reservation import RequestReservation
from ..domain.scopes import ScopeRef, ScopeType
from ..domain.session import SessionCloseReason, SessionStatus
from ..domain.state import BudgetState
from ..domain.window import BudgetWindow


@dataclass(frozen=True, slots=True)
class EffectOutcome:
    """What the post-settlement pass actually did."""

    warnings_emitted: tuple[AlertEvent, ...] = ()
    session_closed: bool = False
    session_close_reason: SessionCloseReason | None = None
    exhausted_scopes: tuple[ScopeRef, ...] = ()


class SettlementEffects:
    """Evaluates thresholds and session closure after a reservation settles."""

    def __init__(self, repository) -> None:
        self.repo = repository

    async def apply(
        self,
        reservation: RequestReservation,
        *,
        warning_percent: int,
        session_min_viable: Money | None,
        now: datetime,
    ) -> EffectOutcome:
        warnings: list[AlertEvent] = []
        exhausted: list[ScopeRef] = []
        session_closed = False
        close_reason: SessionCloseReason | None = None

        for reserved in reservation.scopes:
            window = _window_from_sort_key(reserved.sort_key, reservation)
            state = await self.repo.get_budget_state(
                reserved.scope, window, tenant_id=reservation.tenant_id
            )
            if state is None or state.limit.nano <= 0:
                continue

            warning = await self._maybe_warn(
                state, reservation, warning_percent=warning_percent, now=now
            )
            if warning is not None:
                warnings.append(warning)

            if state.remaining <= Money.zero():
                exhausted.append(reserved.scope)

            if reserved.scope.type is ScopeType.SESSION and reservation.session_id:
                closed, reason = await self._maybe_close_session(
                    state, reservation, session_min_viable, now
                )
                session_closed = session_closed or closed
                close_reason = close_reason or reason

        return EffectOutcome(
            warnings_emitted=tuple(warnings),
            session_closed=session_closed,
            session_close_reason=close_reason,
            exhausted_scopes=tuple(exhausted),
        )

    async def close_session_on_denial(
        self,
        denial,
        *,
        tenant_id: str,
        session_id: str,
        min_viable: Money | None,
        now: datetime,
    ) -> SessionCloseReason | None:
        """Closure path B: the next request could not fit, so the session ends.

        Deliberately synchronous with the rejection, so the 429 the caller
        receives already says the session is closed rather than inviting them to
        retry into a session that is finished.

        The remaining balance comes from the denial's captured pre-image, so
        this costs no extra read.

        Note the distinction preserved here: a session with $0.01 left facing a
        $0.04 request is *done*, but a session with plenty left facing a huge
        request is not -- that is one request being too large, and closing the
        session over it would be wrong.
        """
        if denial.blocking_scope is None or denial.blocking_scope.type is not ScopeType.SESSION:
            return None

        # EXCEEDS_WINDOW_LIMIT means the request is larger than the session's
        # entire budget -- it would have failed against a brand-new session too.
        # That is one oversized request, not a spent session, and closing over
        # it would end a conversation that still has most of its money.
        if denial.code is not DenialCode.BUDGET_EXHAUSTED:
            return None

        # Reaching here means a request that *would* have fit in a fresh
        # session no longer fits. The session is effectively finished: leaving
        # it open only produces a stream of individually-rejected requests.
        remaining = denial.available or Money.zero()
        floor = min_viable or Money.zero()
        reason = (
            SessionCloseReason.BUDGET_EXHAUSTED
            if remaining <= Money.zero()
            else SessionCloseReason.REMAINING_BELOW_VIABLE
        )
        if floor > Money.zero() and remaining >= floor:
            # An operator set a viability floor and the balance is still above
            # it, so honour their intent and keep the session open.
            return None
        alert = AlertEvent(
            kind=AlertKind.SESSION_CLOSED,
            scope=denial.blocking_scope,
            window=denial.window,
            occurred_at=now,
            tenant_id=tenant_id,
            limit=denial.limit,
            spent=denial.committed,
            detail={"reason": reason.value, "path": "would_exceed"},
        )
        closed = await self.repo.try_close_session(
            tenant_id,
            session_id,
            status=SessionStatus.CLOSED_BUDGET,
            reason=reason,
            closed_by="system",
            alert=alert,
        )
        return reason if closed else None

    async def _maybe_warn(
        self,
        state: BudgetState,
        reservation: RequestReservation,
        *,
        warning_percent: int,
        now: datetime,
    ) -> AlertEvent | None:
        """Emit the threshold warning exactly once, ever.

        The warning fires on *committed* spend, not on committed-plus-reserved.
        In-flight reservations can still be released, and a warning that fires
        on money which may never be spent trains operators to ignore it.
        """
        if state.warning_80_sent:
            return None
        if state.utilization_percent < warning_percent:
            return None

        alert = AlertEvent(
            kind=AlertKind.WARN_80,
            scope=state.scope,
            window=state.window,
            occurred_at=now,
            tenant_id=reservation.tenant_id,
            threshold_percent=warning_percent,
            limit=state.limit,
            spent=state.committed,
            reserved=state.reserved,
            triggering_reservation_id=reservation.reservation_id,
        )

        # The conditional flip is the serialisation point: of N concurrent
        # reconcilers that all observe the crossing, exactly one wins it.
        won = await self.repo.try_flip_threshold(
            state.scope,
            state.window,
            threshold_percent=warning_percent,
            remaining_floor=threshold_floor(state.limit, warning_percent),
            alert=alert,
        )
        return alert if won else None

    async def _maybe_close_session(
        self,
        state: BudgetState,
        reservation: RequestReservation,
        min_viable: Money | None,
        now: datetime,
    ) -> tuple[bool, SessionCloseReason | None]:
        """Closure path A: settled spend reached the session's cap.

        Path B -- the next request would not fit -- is handled at authorization
        time, because the point there is to close *before* a provider is
        contacted.
        """
        floor = min_viable or Money.zero()
        if state.remaining > Money.zero() and state.remaining >= floor:
            return (False, None)

        reason = (
            SessionCloseReason.BUDGET_EXHAUSTED
            if state.remaining <= Money.zero()
            else SessionCloseReason.REMAINING_BELOW_VIABLE
        )
        alert = AlertEvent(
            kind=AlertKind.SESSION_CLOSED,
            scope=state.scope,
            window=state.window,
            occurred_at=now,
            tenant_id=reservation.tenant_id,
            limit=state.limit,
            spent=state.committed,
            triggering_reservation_id=reservation.reservation_id,
            detail={"reason": reason.value},
        )
        closed = await self.repo.try_close_session(
            reservation.tenant_id,
            reservation.session_id or "",
            status=SessionStatus.CLOSED_BUDGET,
            reason=reason,
            closed_by="system",
            alert=alert,
        )
        return (closed, reason if closed else None)


def _window_from_sort_key(sort_key: str, reservation: RequestReservation) -> BudgetWindow:
    """Rebuild the window a reservation held against, from its stored key.

    Reconstructed from the reservation rather than recomputed from the clock:
    a request that settles just after a window boundary must settle against the
    window it was authorised in, not the one that has since begun.
    """
    from ..domain.window import WindowType

    _, window_type, window_id = sort_key.split("#", 2)
    return BudgetWindow(
        type=WindowType(window_type),
        id=window_id,
        # Boundaries are not needed for a key-addressed read; the identity of
        # the window is entirely carried by its type and id.
        start_epoch=0,
        end_epoch=0,
    )

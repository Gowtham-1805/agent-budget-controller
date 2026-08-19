"""An in-process transaction engine that mirrors DynamoDB's semantics.

This is not a convenience mock. It is the adversarial oracle that carries the
strongest correctness proof in the test suite, and it earns that role by
modelling one specific thing faithfully: **DynamoDB evaluates a transaction's
conditions at commit time, atomically, not when the caller planned the write.**

The engine therefore runs in two explicit phases with a hook between them:

    evaluate_all()   -- a dry run, observable
    <interleave hook -- other transactions may commit here>
    commit_all()     -- re-evaluate under the lock, then apply all or nothing

A test can use the hook to construct the exact interleaving that breaks a naive
implementation: let ten transactions all evaluate against the same pre-image,
then commit them. A correct engine re-checks at commit and admits one. An engine
that trusted its phase-1 read would admit all ten and overspend the budget by
8x.

moto cannot express that scenario -- it serialises requests, so the window never
opens -- which is precisely why this backend exists alongside it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from .. import attributes as A
from ..plans import (
    IdempotencyPutSlot,
    LedgerPutSlot,
    ReservationPutSlot,
    ReservationUpdateSlot,
    ScopePendingSlot,
    ScopeReserveSlot,
    ScopeSettleSlot,
    SlotFailure,
    StatusCheckSlot,
    TransactionDenied,
    TransactionPlan,
)

ItemKeyTuple = tuple[str, str, str]
Item = dict[str, Any]

#: Called between evaluate and commit. Tests replace it to force interleavings.
InterleaveHook = Callable[[], None]


class MemoryStore:
    """A thread-safe item store with DynamoDB-like transaction semantics."""

    def __init__(self) -> None:
        self._items: dict[ItemKeyTuple, Item] = {}
        self._lock = threading.RLock()
        self._interleave_hook: InterleaveHook | None = None
        #: Counts committed transactions; useful for asserting how much work a
        #: retry policy actually did.
        self.commit_count = 0
        self.denial_count = 0

    # -- test controls ------------------------------------------------------

    @contextmanager
    def interleave(self, hook: InterleaveHook) -> Iterator[None]:
        """Install a hook that runs between evaluation and commit."""
        previous = self._interleave_hook
        self._interleave_hook = hook
        try:
            yield
        finally:
            self._interleave_hook = previous

    # -- raw access ---------------------------------------------------------

    def get(self, key: ItemKeyTuple) -> Item | None:
        with self._lock:
            item = self._items.get(key)
            return dict(item) if item is not None else None

    def put(self, key: ItemKeyTuple, item: Item) -> None:
        with self._lock:
            self._items[key] = dict(item)

    def delete(self, key: ItemKeyTuple) -> None:
        with self._lock:
            self._items.pop(key, None)

    def scan(self, table: str) -> list[Item]:
        with self._lock:
            return [dict(v) for (t, _, _), v in self._items.items() if t == table]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.commit_count = 0
            self.denial_count = 0

    # -- transactions -------------------------------------------------------

    def execute(self, plan: TransactionPlan) -> None:
        """Run a plan atomically.

        Raises:
            TransactionDenied: at least one condition evaluated false at commit.
        """
        plan.validate()

        # Phase 1: dry run. Its result is deliberately discarded -- it exists to
        # give a test something to observe, and to make the point that nothing
        # decided here is trusted.
        self._evaluate(plan, snapshot=self._snapshot())

        if self._interleave_hook is not None:
            self._interleave_hook()

        # Phase 2: the authoritative evaluation. Everything that matters happens
        # under this lock, against state read inside it.
        with self._lock:
            failures = self._evaluate(plan, snapshot=self._items)
            if failures:
                self.denial_count += 1
                raise TransactionDenied(failures)
            for slot in plan.slots:
                self._apply(slot)
            self.commit_count += 1

    def _snapshot(self) -> dict[ItemKeyTuple, Item]:
        with self._lock:
            return {k: dict(v) for k, v in self._items.items()}

    # -- condition evaluation ----------------------------------------------

    def _evaluate(
        self, plan: TransactionPlan, snapshot: dict[ItemKeyTuple, Item]
    ) -> list[SlotFailure]:
        failures: list[SlotFailure] = []
        for index, slot in enumerate(plan.slots):
            key = slot.key.as_tuple()
            item = snapshot.get(key)
            failure = self._check(index, slot, item)
            if failure is not None:
                failures.append(failure)
        return failures

    def _check(self, index: int, slot: Any, item: Item | None) -> SlotFailure | None:
        match slot:
            case ScopeReserveSlot():
                return self._check_reserve(index, slot, item)
            case StatusCheckSlot():
                return self._check_status(index, slot, item)
            case IdempotencyPutSlot() | ReservationPutSlot() | LedgerPutSlot():
                # Conditional create: attribute_not_exists(PK).
                if item is not None:
                    return SlotFailure(index, slot, "ConditionalCheckFailed", dict(item))
                return None
            case ReservationUpdateSlot():
                if item is None:
                    return SlotFailure(index, slot, "ConditionalCheckFailed", None)
                if item.get(A.STATE) != slot.expected_state:
                    return SlotFailure(index, slot, "ConditionalCheckFailed", dict(item))
                return None
            case ScopeSettleSlot() | ScopePendingSlot():
                # Settlement must never be blocked by a budget condition:
                # recording what a provider actually charged us is not
                # optional. The only guard is an accounting sanity check.
                if item is None:
                    return SlotFailure(index, slot, "ConditionalCheckFailed", None)
                held = slot.reserved_cost.nano
                if int(item.get(A.RESERVED_NANO, 0)) < held:
                    return SlotFailure(index, slot, "ConditionalCheckFailed", dict(item))
                return None
            case _:
                return None

    def _check_reserve(
        self, index: int, slot: ScopeReserveSlot, item: Item | None
    ) -> SlotFailure | None:
        """`attribute_not_exists(PK) OR (remaining >= cost AND tokens fit)`.

        The `OR` is not optional. Without it, two concurrent first-requests for
        a brand-new window would both plan against "item absent"; the loser
        would find the item present at commit and be rejected even though the
        budget was untouched. That is exactly the millisecond after a window
        boundary, when traffic spikes.
        """
        if item is None:
            return None
        if int(item.get(A.REMAINING_NANO, 0)) < slot.cost.nano:
            return SlotFailure(index, slot, "ConditionalCheckFailed", dict(item))
        for remaining_attr, _, _, _, dimension in A.TOKEN_FIELDS:
            requested = getattr(slot.tokens, dimension)
            if int(item.get(remaining_attr, 0)) < requested:
                return SlotFailure(index, slot, "ConditionalCheckFailed", dict(item))
        return None

    def _check_status(
        self, index: int, slot: StatusCheckSlot, item: Item | None
    ) -> SlotFailure | None:
        if item is None:
            return SlotFailure(index, slot, "ConditionalCheckFailed", None)
        if item.get(A.STATUS) != slot.expected_status:
            return SlotFailure(index, slot, "ConditionalCheckFailed", dict(item))
        if slot.not_expired_after_epoch is not None:
            expires = int(item.get(A.EXPIRES_AT_EPOCH, 0))
            if expires <= slot.not_expired_after_epoch:
                return SlotFailure(index, slot, "ConditionalCheckFailed", dict(item))
        return None

    # -- mutation -----------------------------------------------------------

    def _apply(self, slot: Any) -> None:
        key = slot.key.as_tuple()
        match slot:
            case ScopeReserveSlot():
                self._apply_reserve(key, slot)
            case ScopeSettleSlot():
                self._apply_settle(key, slot)
            case ScopePendingSlot():
                item = self._items[key]
                item[A.PENDING_NANO] = int(item.get(A.PENDING_NANO, 0)) + slot.reserved_cost.nano
            case StatusCheckSlot():
                pass  # a check mutates nothing
            case IdempotencyPutSlot():
                self._items[key] = {
                    A.PK: key[1],
                    A.SK: key[2],
                    A.ENTITY_TYPE: A.E_IDEMPOTENCY,
                    "reservation_id": slot.reservation_id,
                    "request_fingerprint": slot.request_fingerprint,
                    A.STATE: "RESERVED",
                    "ttl_epoch": slot.ttl_epoch,
                }
            case ReservationPutSlot():
                self._items[key] = {
                    A.PK: key[1],
                    A.SK: key[2],
                    A.ENTITY_TYPE: A.E_RESERVATION,
                    A.STATE: slot.reservation.state.value,
                    A.DISPATCH_STATE: slot.reservation.dispatch_state.value,
                    "reservation": slot.reservation,
                }
            case ReservationUpdateSlot():
                item = self._items[key]
                item[A.STATE] = slot.new_state
                if slot.provider_request_id:
                    item["provider_request_id"] = slot.provider_request_id
                if slot.release_reason:
                    item["release_reason"] = slot.release_reason.value
                if slot.pending_reason:
                    item["pending_reason"] = slot.pending_reason.value
            case LedgerPutSlot():
                self._items[key] = {
                    A.PK: key[1],
                    A.SK: key[2],
                    A.ENTITY_TYPE: A.E_LEDGER_ENTRY,
                    "entry": slot.entry,
                }

    def _apply_reserve(self, key: ItemKeyTuple, slot: ScopeReserveSlot) -> None:
        item = self._items.get(key)
        if item is None:
            item = _seed_item(key, slot)
            self._items[key] = item

        # `remaining = if_not_exists(remaining, limit) - cost`, then
        # `ADD reserved cost`. Seeding and decrementing in one step is what
        # makes lazy window creation race-free.
        item[A.REMAINING_NANO] = int(item[A.REMAINING_NANO]) - slot.cost.nano
        item[A.RESERVED_NANO] = int(item.get(A.RESERVED_NANO, 0)) + slot.cost.nano
        for remaining_attr, _, reserved_attr, _, dimension in A.TOKEN_FIELDS:
            requested = getattr(slot.tokens, dimension)
            item[remaining_attr] = int(item[remaining_attr]) - requested
            item[reserved_attr] = int(item.get(reserved_attr, 0)) + requested
        item[A.OPEN_RESERVATIONS] = int(item.get(A.OPEN_RESERVATIONS, 0)) + 1
        item[A.VERSION] = int(item.get(A.VERSION, 0)) + 1

    def _apply_settle(self, key: ItemKeyTuple, slot: ScopeSettleSlot) -> None:
        """Turn a hold into settled spend (or give it back).

        `remaining` moves by the *difference* between what was held and what was
        actually spent, so an over-reservation returns the unused portion and an
        under-reservation -- a provider overshoot -- drives remaining negative.

        Negative is deliberate and is never clamped. Clamping would erase the
        evidence and break the accounting identity the property tests assert.
        It also makes the breach self-healing: with remaining below zero, the
        next `remaining >= cost` check fails for any positive cost, so the scope
        hard-closes until the window rolls or an operator intervenes.
        """
        item = self._items[key]
        delta = slot.reserved_cost.nano - slot.actual_cost.nano

        item[A.REMAINING_NANO] = int(item[A.REMAINING_NANO]) + delta
        item[A.COMMITTED_NANO] = int(item.get(A.COMMITTED_NANO, 0)) + slot.actual_cost.nano
        item[A.RESERVED_NANO] = int(item[A.RESERVED_NANO]) - slot.reserved_cost.nano
        if slot.overage.nano:
            item[A.OVERAGE_NANO] = int(item.get(A.OVERAGE_NANO, 0)) + slot.overage.nano

        for remaining_attr, committed_attr, reserved_attr, _, dimension in A.TOKEN_FIELDS:
            held = getattr(slot.reserved_tokens, dimension)
            actual = getattr(slot.actual_tokens, dimension)
            item[remaining_attr] = int(item[remaining_attr]) + (held - actual)
            item[committed_attr] = int(item.get(committed_attr, 0)) + actual
            item[reserved_attr] = int(item[reserved_attr]) - held

        # A pending hold that later settles must stop being counted as pending.
        pending = int(item.get(A.PENDING_NANO, 0))
        if pending:
            item[A.PENDING_NANO] = max(0, pending - slot.reserved_cost.nano)

        item[A.OPEN_RESERVATIONS] = max(0, int(item.get(A.OPEN_RESERVATIONS, 0)) - 1)
        item[A.VERSION] = int(item.get(A.VERSION, 0)) + 1


def _seed_item(key: ItemKeyTuple, slot: ScopeReserveSlot) -> Item:
    """Create a budget-window item at its configured limits."""
    seed = slot.seed
    return {
        A.PK: key[1],
        A.SK: key[2],
        A.ENTITY_TYPE: A.E_BUDGET_STATE,
        A.REMAINING_NANO: seed.limits.limit.nano,
        A.REMAINING_INPUT: seed.limits.tokens.input,
        A.REMAINING_OUTPUT: seed.limits.tokens.output,
        A.REMAINING_TOTAL: seed.limits.tokens.total,
        A.LIMIT_NANO: seed.limits.limit.nano,
        A.MAX_INPUT: seed.limits.tokens.input,
        A.MAX_OUTPUT: seed.limits.tokens.output,
        A.MAX_TOTAL: seed.limits.tokens.total,
        A.COMMITTED_NANO: 0,
        A.COMMITTED_INPUT: 0,
        A.COMMITTED_OUTPUT: 0,
        A.COMMITTED_TOTAL: 0,
        A.RESERVED_NANO: 0,
        A.RESERVED_INPUT: 0,
        A.RESERVED_OUTPUT: 0,
        A.RESERVED_TOTAL: 0,
        A.PENDING_NANO: 0,
        A.OVERAGE_NANO: 0,
        A.OPEN_RESERVATIONS: 0,
        A.WARNING_80_SENT: False,
        A.WARNING_100_SENT: False,
        A.POLICY_VERSION: seed.policy_version,
        A.WINDOW_START_EPOCH: seed.window_start_epoch,
        A.WINDOW_END_EPOCH: seed.window_end_epoch,
        A.WINDOW_TYPE: seed.window_type,
        A.WINDOW_ID: seed.window_id,
        A.SCOPE_TYPE: seed.scope_type,
        A.SCOPE_ID: seed.scope_id,
        A.TENANT_ID: seed.tenant_id,
        A.HOUSEKEEPING_TTL: seed.housekeeping_ttl_epoch,
        A.VERSION: 0,
    }

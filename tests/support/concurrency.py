"""Helpers for genuinely concurrent tests.

A warning worth recording, because it cost real debugging time here: running N
coroutines through ``asyncio.gather`` is **not** a concurrency test when the
code under test is synchronous inside. The event loop runs each coroutine to
its next await, and a synchronous critical section has no awaits, so the tasks
execute one after another and never overlap. A race test written that way
passes against a deliberately broken engine -- which is to say it proves
nothing.

Real OS threads are used instead. Each worker gets its own event loop, and a
barrier releases them together so their critical sections genuinely overlap.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def run_concurrently[T](
    make_coro: Callable[[int], Coroutine[Any, Any, T]],
    count: int,
    *,
    timeout: float = 30.0,
) -> list[T]:
    """Run ``count`` coroutines on ``count`` real threads, released together.

    Each worker blocks on a barrier immediately before the call under test, so
    all of them enter it at the same moment rather than trickling in.
    """
    barrier = threading.Barrier(count, timeout=timeout)

    def worker(index: int) -> T:
        async def run() -> T:
            barrier.wait()
            return await make_coro(index)

        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(worker, range(count)))


class PhaseGate:
    """Holds every participant between transaction phases.

    Installed as the store's interleave hook, this forces the pathological
    schedule: *every* transaction finishes evaluating its conditions against the
    original state before *any* of them commits.

    That is the schedule a read-then-write implementation cannot survive -- all
    N see enough budget and all N proceed. An implementation that re-evaluates
    its conditions at commit time, which is what DynamoDB does, still admits
    only one.
    """

    def __init__(self, participants: int, timeout: float = 15.0) -> None:
        self._barrier = threading.Barrier(participants, timeout=timeout)
        self.arrivals = 0
        self._lock = threading.Lock()

    def __call__(self) -> None:
        with self._lock:
            self.arrivals += 1
        # A participant may be denied before reaching the gate, which breaks the
        # barrier. Releasing the rest is correct -- the point of the gate is to
        # hold everyone until the last *arriving* participant has evaluated, and
        # the assertions hold either way.
        with contextlib.suppress(threading.BrokenBarrierError):
            self._barrier.wait()

    def abort(self) -> None:
        self._barrier.abort()

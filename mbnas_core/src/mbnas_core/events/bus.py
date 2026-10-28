"""Event bus over the ``events`` table (at-least-once, replayable, no Redis).

Contract
* :func:`emit` appends one event **inside the caller's open transaction** (``repo.append_event`` allocates the
  run-local ``seq`` from ``runs.last_seq``), so an event is visible to readers only once that transaction commits.
* :meth:`EventBus.notify` is called by the writer *after* commit and wakes in-process subscribers immediately;
  events committed by another process (the worker) are picked up by DB polling every ``poll_s`` seconds.
* :meth:`EventBus.subscribe` yields stored events with ``seq > after_seq`` in order, then waits for more; each
  subscription has its own read-only-use connection. It never yields an event twice within one subscription.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections.abc import AsyncIterator
from typing import Any

from mbnas_core.config import Settings
from mbnas_core.db import repo
from mbnas_core.db.core import connect

REPLAY_PAGE = 500


def emit(conn: sqlite3.Connection, run_id: str, type: str, payload: dict[str, Any]) -> int:
    """Append an event in the caller's transaction and return its seq (validated against the contract Event)."""
    if not conn.in_transaction:
        raise RuntimeError("events.emit must run inside db.core.tx(conn) so the event commits with its state change")
    return repo.append_event(conn, run_id, type, payload)


class EventBus:
    """In-process wakeups plus DB polling for one API process."""

    def __init__(self, settings: Settings, *, poll_s: float = 0.5) -> None:
        self.settings = settings
        self.poll_s = poll_s
        self._lock = threading.Lock()
        self._waiters: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}

    def notify(self, run_id: str) -> None:
        """Wake subscribers of ``run_id`` (thread-safe; call after the writing transaction committed)."""
        with self._lock:
            waiters = list(self._waiters.get(run_id, ()))
        for loop, event in waiters:
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # loop closed: subscriber is gone
                pass

    def _register(self, run_id: str, item: tuple[asyncio.AbstractEventLoop, asyncio.Event]) -> None:
        with self._lock:
            self._waiters.setdefault(run_id, set()).add(item)

    def _unregister(self, run_id: str, item: tuple[asyncio.AbstractEventLoop, asyncio.Event]) -> None:
        with self._lock:
            group = self._waiters.get(run_id)
            if group is not None:
                group.discard(item)
                if not group:
                    self._waiters.pop(run_id, None)

    async def wait(self, run_id: str, timeout: float) -> None:
        """Sleep until notified for ``run_id`` or ``timeout`` seconds elapse (whichever first)."""
        item = (asyncio.get_running_loop(), asyncio.Event())
        self._register(run_id, item)
        try:
            await asyncio.wait_for(item[1].wait(), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._unregister(run_id, item)

    async def subscribe(self, run_id: str, after_seq: int, *,
                        heartbeat_s: float | None = None) -> AsyncIterator[dict[str, Any] | None]:
        """Yield events with ``seq > after_seq`` forever (DB replay, then live via notify/poll).

        With ``heartbeat_s`` a ``None`` item is yielded whenever that long passed without one (SSE keep-alive).
        """
        conn = connect(self.settings, check_same_thread=False, init=False)
        cursor, last_beat = after_seq, time.monotonic()
        try:
            while True:
                batch = repo.get_events_after(conn, run_id, cursor, REPLAY_PAGE)
                for event in batch:
                    cursor = event["seq"]
                    yield event
                if len(batch) == REPLAY_PAGE:
                    continue
                now = time.monotonic()
                if heartbeat_s is not None and now - last_beat >= heartbeat_s:
                    last_beat = now
                    yield None
                    continue
                timeout = self.poll_s if heartbeat_s is None else min(self.poll_s, heartbeat_s - (now - last_beat))
                await self.wait(run_id, max(0.01, timeout))
        finally:
            conn.close()

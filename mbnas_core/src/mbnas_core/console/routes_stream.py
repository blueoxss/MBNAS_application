"""``GET /runs/{run_id}/events`` — authenticated fetch SSE (contract ``streamRunEvents``), served at
``/core/v1/console/runs/{run_id}/events`` (scope server|admin) and, with ``expose_legacy_api``, at ``/api/v1``.

Wire format per event: ``id: <seq>`` / ``event: <type>`` / ``data: <Event JSON>``. The cursor is
``Last-Event-ID`` (reconnect) or ``after_seq`` (default 0); events with a larger seq are replayed from the DB,
then streamed live (in-process wakeups + 0.5 s DB polling for events committed by the worker). A ``: heartbeat``
comment is sent every ``sse_heartbeat_s`` (15 s). If the cursor cannot be replayed — it is older than
``last_seq - max_replay_events``, older than the oldest stored event, or ahead of ``last_seq`` — the stream first
sends one ``snapshot.required`` event (no ``id`` line, so the client cursor is not advanced) telling the client to
load ``/snapshot``, and then continues from ``last_seq``.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from mbnas_core.db import repo
from mbnas_core.db.core import connect
from mbnas_core.console.projection import current_scope, project_public
from mbnas_contracts.errors import ApiError

router = APIRouter()
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def format_event(event: dict[str, Any], *, with_id: bool = True, scope: str | None = None) -> str:
    """One SSE frame; the event body goes through the console projection hook (``project_public("Event", ...)``)."""
    data = json.dumps(project_public("Event", event, scope), separators=(",", ":"), allow_nan=False)
    head = f"id: {event['seq']}\n" if with_id else ""
    return f"{head}event: {event['type']}\ndata: {data}\n\n"


MAX_CURSOR = 10**18


def _cursor(after_seq: int | None, last_event_id: str | None) -> int:
    """``Last-Event-ID`` (ASCII digits only; anything else is ignored) wins over ``after_seq``.

    ``str.isdigit`` alone is not enough: it is true for characters such as '²' that ``int`` rejects, and very long
    digit strings exceed Python's int-conversion limit. Values past :data:`MAX_CURSOR` are clamped (and then reported
    as a cursor ahead of ``last_seq``, i.e. ``snapshot.required``).
    """
    text = (last_event_id or "").strip()
    if text.isascii() and text.isdigit():
        return int(text) if len(text) <= 18 else MAX_CURSOR
    return min(after_seq or 0, MAX_CURSOR)


def replay_gap(cursor: int, last_seq: int, oldest: int | None, max_replay: int) -> str | None:
    """Why ``cursor`` cannot be replayed exactly, or None."""
    if cursor > last_seq:
        return "CURSOR_AHEAD_OF_LAST_SEQ"
    if cursor < last_seq - max_replay:
        return "CURSOR_OLDER_THAN_REPLAY_RETENTION"
    if oldest is not None and cursor < oldest - 1:
        return "EVENTS_NO_LONGER_STORED"
    return None


@router.get("/runs/{run_id}/events")
async def stream_events(request: Request, run_id: str, after_seq: int | None = Query(None, ge=0),
                        last_event_id: str | None = Header(None, alias="Last-Event-ID")) -> StreamingResponse:
    svc = request.app.state.services
    conn = connect(svc.settings, init=False)
    try:
        run = repo.get_run(conn, run_id)
        oldest, _ = repo.event_bounds(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", f"run {run_id} not found")
    cursor, last_seq = _cursor(after_seq, last_event_id), int(run["last_seq"])
    gap = replay_gap(cursor, last_seq, oldest, svc.settings.max_replay_events)
    bus = request.app.state.bus
    scope = current_scope()  # bound by the console router dependency; frozen for the life of the stream

    async def body() -> AsyncIterator[str]:
        start = cursor
        yield "retry: 3000\n\n"
        if gap is not None:
            yield format_event({"seq": max(1, last_seq), "type": "snapshot.required", "run_id": run_id,
                                "payload": {"reason": gap, "after_seq": cursor, "last_seq": last_seq,
                                            "oldest_seq": oldest, "snapshot_url": f"/api/v1/runs/{run_id}/snapshot"}},
                               with_id=False, scope=scope)
            start = last_seq
        # client disconnects cancel this generator (StreamingResponse), which closes the subscription
        async for event in bus.subscribe(run_id, start, heartbeat_s=svc.sse_heartbeat_s):
            yield ": heartbeat\n\n" if event is None else format_event(event, scope=scope)

    return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)

"""Shared route helpers: per-request DB connection, app services, projected contract JSON responses, and the console
router dependencies that authenticate the caller and bind the request scope (projection) and the structure sandbox.

* :func:`console_principal` guards ``/core/v1/console/*``: a scoped principal of kind ``server`` or ``admin``
  (:func:`mbnas_core.runner_api.auth.require_principal`). Every console body is the public projection
  (:func:`mbnas_core.console.projection.project_public`) whatever the principal kind; raw owner payloads exist only on
  ``/core/v1/owner/*`` (:mod:`mbnas_core.console.owner_routes`).
* :func:`legacy_principal` guards the optional ``/api/v1`` owner debug mount (``CoreSettings.expose_legacy_api``):
  the static bearer token; responses are the same public projection.
"""
from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from mbnas_core.db import repo
from mbnas_core.db.core import connect
from mbnas_core.events.bus import EventBus
from mbnas_core.api.auth import require_bearer
from mbnas_core.console.projection import project_public, set_scope
from mbnas_core.runner_api.auth import require_principal
from mbnas_contracts.errors import ApiError, pydantic_errors
from mbnas_core.console.services import AppServices

log = logging.getLogger("mbnas_core.console")


def services(request: Request) -> AppServices:
    return request.app.state.services


def bus(request: Request) -> EventBus:
    return request.app.state.bus


def db(request: Request) -> Iterator[sqlite3.Connection]:
    """One connection per request (schema initialised once at startup).

    FastAPI may run the dependency and the sync endpoint on different threadpool threads, so the connection is
    opened with ``check_same_thread=False``; it is still used by exactly one request at a time.
    """
    conn = connect(request.app.state.services.settings, init=False, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def contract_body(component: str, model: type[BaseModel], payload: dict[str, Any], *,
                  run_id: str | None = None) -> dict[str, Any]:
    """``payload`` projected onto the public contract component and validated with its pydantic mirror.

    The mirror validates the PROJECTED body (closed components forbid unknown members, so the projection must have
    reduced the payload to the public DTO). A body that still does not match is the SERVER's fault (a stored row or a
    view out of step with the contract): it is logged and answered 500 ``RESPONSE_CONTRACT_VIOLATION`` with the field
    errors, never 422 "request does not match" (which would blame the client's request).
    """
    body = project_public(component, payload, run_id=run_id)
    try:
        model.model_validate(body)
    except ValidationError as exc:
        errors = pydantic_errors(exc)
        log.error("response for %s violates the contract: %s", component, errors[:5])
        raise ApiError(500, "RESPONSE_CONTRACT_VIOLATION",
                       f"server response for {component} does not match the contract (see server log by request_id)",
                       errors=errors) from exc
    return body


def contract_json(component: str, model: type[BaseModel], payload: dict[str, Any], status: int = 200,
                  headers: dict[str, str] | None = None, *, run_id: str | None = None) -> JSONResponse:
    """:func:`contract_body` as a JSON response."""
    return JSONResponse(contract_body(component, model, payload, run_id=run_id), status_code=status, headers=headers)


def _bind_request(request: Request, scope: str) -> None:
    from mbnas_core.structure import sandbox

    set_scope(scope)
    svc = request.app.state.services
    sandbox.bind(sandbox.for_settings(svc.settings))


async def console_principal(request: Request,
                            principal: dict[str, Any] = Depends(require_principal("server"))) -> dict[str, Any]:
    """``/core/v1/console`` guard (async so the context variables reach the endpoint's thread)."""
    _bind_request(request, "admin" if principal.get("kind") == "admin" else "server")
    return principal


async def legacy_principal(request: Request, _: None = Depends(require_bearer)) -> None:
    """``/api/v1`` owner debug guard (static bearer token)."""
    _bind_request(request, "server")


async def bind_legacy_scope(request: Request) -> None:
    """Scope/sandbox binding for legacy routes that authenticate per route (artifact bearer-or-signed-URL)."""
    _bind_request(request, "server")


def get_run_or_404(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    run = repo.get_run(conn, run_id)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", f"run {run_id} not found")
    return run

"""Error bodies of the core console app: every 4xx/5xx response is the contract ``Error`` ``{code, message, errors,
request_id}``.

``ApiError``, ``pydantic_errors`` and the HTTP code table are host-independent and live in :mod:`mbnas_contracts.errors`;
the FastAPI parts (:func:`install`, :func:`error_response`) need the web framework and therefore exist once per host
package (this module and ``mbnas_server.api.errors`` carry the same code; WP-1 contract section 4).

WP-4 (contract 1.5.0): an Error answered on the console mounts (``/core/v1/console/*`` and the optional ``/api/v1`` debug
mount) carries ``location: core`` and goes through the public projection (free text scrubbed: no absolute paths, PIDs
or host names; :func:`mbnas_core.console.projection.project_public`). Runner and owner routes answer it unchanged.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from mbnas_contracts.errors import ApiError, _HTTP_CODES, pydantic_errors

__all__ = ["ApiError", "error_response", "install", "pydantic_errors"]

log = logging.getLogger("mbnas_core.api")


PUBLIC_PREFIXES = ("/core/v1/console", "/api/v1")


def error_response(request: Request, status: int, code: str, message: str, errors: list[dict[str, Any]] | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message or code,
                            "request_id": getattr(request.state, "request_id", None)}
    if errors is not None:
        body["errors"] = errors
    path = str(request.url.path)
    if any(path == p or path.startswith(p + "/") for p in PUBLIC_PREFIXES):
        from mbnas_core.console.projection import project_public

        body = project_public("Error", {**body, "location": "core"})
        body.setdefault("message", code)  # a scrub never empties the message (Error.message minLength 1)
    return JSONResponse(body, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    """Register the exception handlers on ``app``."""

    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status, exc.code, exc.message, exc.errors, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _req(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, "VALIDATION_FAILED", "request does not match the contract schema",
                              pydantic_errors(exc))

    @app.exception_handler(ValidationError)
    async def _model(request: Request, exc: ValidationError) -> JSONResponse:
        return error_response(request, 422, "VALIDATION_FAILED", "request does not match the contract schema",
                              pydantic_errors(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return error_response(request, exc.status_code, code, str(exc.detail or code), headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error (request_id=%s)", getattr(request.state, "request_id", None))
        return error_response(request, 500, "INTERNAL_ERROR", "internal server error (see server log by request_id)")

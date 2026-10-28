"""Error bodies of the gateway: every 4xx/5xx response is the public ``Error``
``{code, location, message, request_id}`` (+ ``errors`` when there are field details).

``ApiError``, ``pydantic_errors`` and the HTTP code table are host-independent and live in :mod:`mbnas_contracts.errors`; the
FastAPI parts (:func:`install`, :func:`error_response`) need the web framework and therefore exist once per host
package (this module and ``mbnas_core.api.errors`` carry the same code; WP-1 contract section 4).

WP-3B (contract section 2.8, DD-A20): ``location`` is a :class:`mbnas_contracts.errors.ErrorLocation` value. Errors raised
by the gateway itself are ``gateway``; :class:`GatewayError` carries an explicit location (``core`` for errors relayed
from core, which keep core's ``code``/``message``/``errors`` and the request id chain).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from mbnas_contracts.errors import ApiError, ErrorLocation, _HTTP_CODES, pydantic_errors

__all__ = ["ApiError", "ErrorLocation", "GatewayError", "error_body", "error_response", "install", "pydantic_errors"]

log = logging.getLogger("mbnas_server.api")


class GatewayError(ApiError):
    """:class:`ApiError` with a public ``location`` and optional extra body fields (e.g. ``core_request_id``)."""

    def __init__(self, status: int, code: str, message: str, *, location: ErrorLocation | str = ErrorLocation.GATEWAY,
                 errors: list[dict[str, Any]] | None = None, headers: dict[str, str] | None = None,
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(status, code, message, errors=errors, headers=headers)
        self.location = ErrorLocation(location).value
        self.extra = extra or {}


def error_body(request_id: str | None, code: str, message: str, location: str = ErrorLocation.GATEWAY.value,
               errors: list[dict[str, Any]] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "location": location, "message": message or code, "request_id": request_id}
    if errors is not None:
        body["errors"] = errors
    for k, v in (extra or {}).items():
        body.setdefault(k, v)
    return body


def error_response(request: Request, status: int, code: str, message: str, errors: list[dict[str, Any]] | None = None,
                   headers: dict[str, str] | None = None, location: str = ErrorLocation.GATEWAY.value,
                   extra: dict[str, Any] | None = None) -> JSONResponse:
    body = error_body(getattr(request.state, "request_id", None), code, message, location, errors, extra)
    return JSONResponse(body, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    """Register the exception handlers on ``app``."""

    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError) -> JSONResponse:
        location = getattr(exc, "location", ErrorLocation.GATEWAY.value)
        return error_response(request, exc.status, exc.code, exc.message, exc.errors, exc.headers, location,
                              getattr(exc, "extra", None))

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

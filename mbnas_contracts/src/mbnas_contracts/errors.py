"""Error bodies: every 4xx/5xx response is the contract ``Error`` ``{code, message, errors, request_id}``.

Framework-free part shared by the FastAPI hosts: :class:`ApiError` (raise from a route to answer with an Error body),
:func:`pydantic_errors` (contract ``errors`` items of a validation error) and ``_HTTP_CODES`` (HTTP status -> Error
``code`` for framework HTTP exceptions). The exception handlers (``install(app)``) and ``error_response`` need FastAPI,
which this package does not import (contracts import only pydantic/PyYAML), so each host package keeps its own copy and
raises/handles the one :class:`ApiError` class defined here.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

_HTTP_CODES = {400: "BAD_REQUEST", 401: "AUTH_REQUIRED", 403: "ACCESS_DENIED", 404: "NOT_FOUND",
               405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 410: "EXPIRED", 422: "VALIDATION_FAILED"}


class ErrorLocation(str, Enum):
    """Public ``Error.location`` (DD-A20 / WP-3 contract section 2.8): which component produced the error.

    ``gateway`` = the public gateway itself (auth, limits, uploads, path checks, static web); ``core`` = the core
    control plane (relayed unchanged, or ``CORE_UNAVAILABLE`` when it cannot be reached); ``runner`` = a GPU runner
    (reported through core); ``store`` = the gateway artifact/upload store. Added by WP-3B (single contracts edit).
    """

    GATEWAY = "gateway"
    CORE = "core"
    RUNNER = "runner"
    STORE = "store"


class ApiError(Exception):
    """Raise from a route to answer with an Error body."""

    def __init__(self, status: int, code: str, message: str, *, errors: list[dict[str, Any]] | None = None,
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.errors, self.headers = status, code, message, errors, headers


def _pydantic_errors(exc: Any) -> list[dict[str, Any]]:
    """``errors`` items of a pydantic ``ValidationError`` or FastAPI ``RequestValidationError`` (both have
    ``.errors()``)."""
    out = []
    for e in exc.errors():
        loc = [str(p) for p in e.get("loc", ()) if p != "body"]
        out.append({"field": ".".join(loc) or "$", "message": str(e.get("msg", "invalid")),
                    "code": str(e.get("type", "invalid")).upper()})
    return out


pydantic_errors = _pydantic_errors  # public name for route helpers (api.deps.contract_json)

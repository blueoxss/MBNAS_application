"""Bearer authentication of the gateway.

* :func:`require_bearer` guards every ``/api/v1`` route: no/blank ``Authorization`` -> 401 ``AUTH_REQUIRED``
  (with ``WWW-Authenticate: Bearer``), a wrong token -> 403 ``ACCESS_DENIED``. Comparison is constant-time. The expected token
  is ``app.state.api_token`` (``GatewaySettings.api_token``, set by ``create_app``).

The request-id middleware that used to live here is :class:`mbnas_contracts.asgi.RequestIdMiddleware` (shared by both hosts).
``mbnas_core.api.auth`` carries its own copy of these two helpers (the core's copy checks scoped principals).
"""
from __future__ import annotations

import hmac

from fastapi import Request

from mbnas_contracts.errors import ApiError


def bearer_ok(request: Request) -> bool:
    """True when the request carries the configured bearer token; raises 401/403 ApiError otherwise."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if not header.strip() or scheme.lower() != "bearer" or not value.strip():
        raise ApiError(401, "AUTH_REQUIRED", "bearer token required", headers={"WWW-Authenticate": "Bearer"})
    expected: str = request.app.state.api_token
    if not hmac.compare_digest(value.strip().encode(), expected.encode()):
        raise ApiError(403, "ACCESS_DENIED", "invalid bearer token")
    return True


def require_bearer(request: Request) -> None:
    """FastAPI dependency form of :func:`bearer_ok`."""
    bearer_ok(request)

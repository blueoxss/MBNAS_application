"""Path hygiene of the gateway.

Two layers, both answering the public ``Error`` ``404 NOT_FOUND`` with ``location: gateway``:

* :class:`PathGuardMiddleware` (pure ASGI, before routing): a request whose decoded path has a ``.`` or ``..`` segment
  (raw, or percent-encoded such as ``%2E%2E`` / ``..%2F``) is refused before any route, the static SPA fallback
  included, can see it. Without this a dot segment in a path parameter (``/api/v1/runs/r1/artifacts/%2E%2E``) would be
  relayed as a dot segment and normalised by the HTTP client into a DIFFERENT core route (``/runs/r1``).
* :func:`segment` (inside the relay routes, i.e. after authentication): every path parameter that goes into a core URL
  is checked and percent-encoded with ``quote(value, safe="")``, so core sees exactly the decoded value as ONE segment
  (``/``, ``%``, ``?``, ``#`` and non-ASCII are encoded; nothing is normalised). Control characters (C0, DEL, C1) are
  refused with 404 instead of reaching the HTTP client (``httpx.InvalidURL``). It runs after the bearer check on
  purpose: without credentials such an id answers 401 like any other id (no existence signal).
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from mbnas_contracts.errors import ErrorLocation
from mbnas_server.api.errors import GatewayError, error_body

DOT_SEGMENTS = frozenset({".", ".."})
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
NOT_FOUND_MESSAGE = "no such resource"


def has_dot_segment(path: str) -> bool:
    """True when the (decoded) URL path has a ``.`` or ``..`` segment."""
    return any(part in DOT_SEGMENTS for part in path.split("/"))


def segment(value: str) -> str:
    """One path parameter for a core URL: 404 ``NOT_FOUND`` for a dot segment or a control character, else the value
    percent-encoded as a single segment."""
    if value in DOT_SEGMENTS or CONTROL.search(value):
        raise GatewayError(404, "NOT_FOUND", NOT_FOUND_MESSAGE, location=ErrorLocation.GATEWAY)
    return quote(value, safe="")


class PathGuardMiddleware:
    """Pure ASGI: 404 ``NOT_FOUND`` (JSON ``Error``, ``location: gateway``) for a dot path segment, before routing."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not has_dot_segment(str(scope.get("path") or "")):
            await self.app(scope, receive, send)
            return
        request_id = (scope.get("state") or {}).get("request_id")
        body = json.dumps(error_body(request_id, "NOT_FOUND", NOT_FOUND_MESSAGE, ErrorLocation.GATEWAY.value)).encode()
        await send({"type": "http.response.start", "status": 404,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

"""Pure-ASGI middleware shared by the FastAPI hosts (stdlib + :mod:`mbnas_contracts.errors`): request-body size limit
and request id.

:class:`BodyLimitMiddleware` is pure ASGI, so it also protects routes that read the body before authentication.
A request whose ``Content-Length`` exceeds ``max_bytes`` is answered 413 ``PAYLOAD_TOO_LARGE`` (contract ``Error``
body) without reading it. A body without a trustworthy length (chunked) is counted while the application reads
it; once the limit is crossed the remaining input is cut off, the application's own answer is discarded and the 413
is sent instead. Legitimate bodies (a protocol JSON of a few KB) are far below the 1 MiB default.

The 413 body names the component that refused the request in ``location`` (contract ``Error.location``,
:class:`mbnas_contracts.errors.ErrorLocation`): the host passes ``location`` (``gateway`` / ``core``) or, when it does
not, :func:`body_limit_location` derives it from the request path. Runner and owner protocol paths
(:data:`PROTOCOL_PREFIXES`) never get one: their ``ErrorBody`` has no ``location`` field and core answers its errors
there unchanged (``mbnas_core.api.errors``).

:class:`RequestIdMiddleware` (pure ASGI, safe for streaming responses) accepts a well-formed ``X-Request-ID``
or creates one, stores it on ``request.state.request_id`` and echoes it on the response.

Bearer-token checks are not part of this module: each host package keeps its own.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from mbnas_contracts.errors import ErrorLocation

DEFAULT_MAX_BODY_BYTES = 1 << 20
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# runner / owner protocol mounts of core: their ErrorBody (mbnas_contracts.runner_protocol) has no ``location``
PROTOCOL_PREFIXES = ("/core/v1/runner", "/core/v1/owner")
CORE_PATH_PREFIX = "/core/"


def body_limit_location(path: str, location: str | None = None) -> str | None:
    """``Error.location`` of a 413 for a request to ``path``: ``None`` (no field) below :data:`PROTOCOL_PREFIXES`,
    else the host's ``location`` when given, else ``core`` for the other ``/core/`` paths (the console mount) and
    ``gateway`` for everything else (the public ``/api/v1`` routes and the web console are served by the gateway)."""
    if any(path == p or path.startswith(p + "/") for p in PROTOCOL_PREFIXES):
        return None
    if location is not None:
        return location
    return ErrorLocation.CORE.value if path.startswith(CORE_PATH_PREFIX) else ErrorLocation.GATEWAY.value


class BodyLimitMiddleware:
    def __init__(self, app: Any, max_bytes: int = DEFAULT_MAX_BODY_BYTES, *, location: str | None = None) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.location = None if location is None else ErrorLocation(location).value  # invalid value -> ValueError

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, send)
            return
        state = {"seen": 0, "exceeded": False, "answered": False}

        async def counting_receive() -> dict[str, Any]:
            if state["exceeded"]:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                state["seen"] += len(message.get("body", b""))
                if state["seen"] > self.max_bytes:
                    state["exceeded"] = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message: dict[str, Any]) -> None:
            if state["exceeded"]:
                if not state["answered"]:
                    state["answered"] = True
                    await self._reject(scope, send)
                return  # drop whatever the application tried to answer
            await send(message)

        await self.app(scope, counting_receive, guarded_send)
        if state["exceeded"] and not state["answered"]:
            await self._reject(scope, send)

    async def _reject(self, scope: dict[str, Any], send: Any) -> None:
        request_id = (scope.get("state") or {}).get("request_id")
        error: dict[str, Any] = {"code": "PAYLOAD_TOO_LARGE", "request_id": request_id,
                                 "message": f"request body larger than {self.max_bytes} bytes"}
        location = body_limit_location(str(scope.get("path") or ""), self.location)
        if location is not None:
            error["location"] = location
        body = json.dumps(error).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})


class RequestIdMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = rid

        async def send_with_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        await self.app(scope, receive, send_with_id)

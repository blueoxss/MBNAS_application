"""HTTP client of the gateway towards the core console API (WP-3 contract section 2.1).

* One :class:`httpx.AsyncClient` per app (``base_url = core_url + core_prefix``), authenticated with the service token
  ``GatewaySettings.core_token`` (``Authorization: Bearer``; never sent to browsers, never logged). TLS verification
  follows ``core_tls_verify`` / ``core_ca_file``; timeouts ``core_connect_timeout_s`` / ``core_read_timeout_s``.
* Every call forwards the gateway request id as ``X-Request-ID`` (so core's Error ``request_id`` is the same id) and,
  when given, ``Idempotency-Key`` / ``Last-Event-ID``.
* A core error answer ``{code, message, errors?, location?, request_id}`` becomes a :class:`GatewayError` with the same
  HTTP status, ``code``, ``message`` and ``errors`` and ``location`` = core's value or ``core``; if core's request id
  differs from ours it is kept as ``core_request_id``. A core 401 means the *service token* was refused and is reported
  as 502 ``CORE_AUTH_FAILED`` (the browser's credential is not the problem). A non-JSON error is 502
  ``CORE_BAD_RESPONSE``.
* Connection failures / timeouts are 503 ``CORE_UNAVAILABLE`` (``location=core``, ``Retry-After``). There is no fallback:
  the gateway never answers a relayed route from local state.
* DD-A41 (d): without an injected transport the client refuses a plain ``http://`` ``core_url`` to a non-loopback host
  (:class:`mbnas_server.config.CoreUrlInsecure`, ``CORE_URL_INSECURE``) unless ``core_allow_insecure_http`` is set, so
  the app factory fails at start instead of sending the service token in clear text.

``transport`` (an ``httpx.AsyncBaseTransport``) can be injected, e.g. ``httpx.ASGITransport(core_app)`` for the in-process
combined harness (WP-3C).
"""
from __future__ import annotations

import json
import logging
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from mbnas_contracts.errors import ErrorLocation
from mbnas_server.api.errors import GatewayError
from mbnas_server.config import GatewaySettings

log = logging.getLogger("mbnas_server.api.core_client")
RETRY_AFTER_S = "5"
FORWARD_REQUEST_HEADERS = ("idempotency-key", "last-event-id", "content-type")


def core_unavailable(exc: BaseException | None = None) -> GatewayError:
    detail = type(exc).__name__ if exc is not None else "unreachable"
    return GatewayError(503, "CORE_UNAVAILABLE", f"the core service is not reachable ({detail}); try again later",
                        location=ErrorLocation.CORE, headers={"Retry-After": RETRY_AFTER_S})


def _verify(settings: GatewaySettings) -> bool | ssl.SSLContext:
    if not settings.core_tls_verify:
        return False
    if settings.core_ca_file:
        return ssl.create_default_context(cafile=settings.core_ca_file)
    return True


def relay_error(resp_status: int, content: bytes, request_id: str | None) -> GatewayError:
    """GatewayError for a non-2xx core answer (see module docstring)."""
    try:
        body = json.loads(content.decode("utf-8")) if content else None
    except (UnicodeDecodeError, ValueError):
        body = None
    if not isinstance(body, dict) or not isinstance(body.get("code"), str) or not body["code"]:
        return GatewayError(502, "CORE_BAD_RESPONSE", f"core answered HTTP {resp_status} without an Error body",
                            location=ErrorLocation.CORE)
    if resp_status == 401:
        log.error("core refused the gateway service token (request_id=%s)", request_id)
        return GatewayError(502, "CORE_AUTH_FAILED", "the gateway is not authorised at core (service token)",
                            location=ErrorLocation.CORE)
    try:
        location = ErrorLocation(body.get("location") or ErrorLocation.CORE.value).value
    except ValueError:
        location = ErrorLocation.CORE.value
    errors = body.get("errors") if isinstance(body.get("errors"), list) else None
    extra: dict[str, Any] = {}
    core_rid = body.get("request_id")
    if core_rid and core_rid != request_id:
        extra["core_request_id"] = core_rid
    headers = {}
    return GatewayError(resp_status, body["code"], str(body.get("message") or body["code"]), location=location,
                        errors=errors, headers=headers or None, extra=extra)


class CoreClient:
    """Async relay client (see module docstring)."""

    def __init__(self, settings: GatewaySettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if transport is None:  # a real network client: DD-A41 (d) CORE_URL_INSECURE (an injected transport has no wire)
            settings.require_secure_core_url()
        self.settings = settings
        self.base_url = settings.core_url + settings.core_prefix
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"User-Agent": "mbnas-gateway"}
            if self.settings.core_token is not None:
                headers["Authorization"] = f"Bearer {self.settings.core_token.get_secret_value()}"
            timeout = httpx.Timeout(self.settings.core_read_timeout_s, connect=self.settings.core_connect_timeout_s)
            kw: dict[str, Any] = {"base_url": self.base_url, "headers": headers, "timeout": timeout,
                                  "follow_redirects": False}
            if self._transport is not None:
                kw["transport"] = self._transport
            else:
                kw["verify"] = _verify(self.settings)
            self._client = httpx.AsyncClient(**kw)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _headers(request_id: str | None, extra: dict[str, str] | None) -> dict[str, str | bytes]:
        """Forwarded headers; a non-ASCII value (Starlette decodes header bytes as latin-1) is sent as its original
        bytes instead of failing in the HTTP client's ASCII encoding; one that has no latin-1 form is dropped."""
        headers: dict[str, str | bytes] = {}
        for name, value in (extra or {}).items():
            if value is None:
                continue
            if not value.isascii():
                try:
                    headers[name] = value.encode("latin-1")
                except UnicodeEncodeError:
                    log.warning("header %s not forwarded to core: not representable as HTTP header bytes", name)
                continue
            headers[name] = value
        if request_id:
            headers["X-Request-ID"] = request_id
        return headers

    async def request(self, method: str, path: str, *, request_id: str | None, params: Any = None,
                      json_body: Any = None, content: bytes | None = None, headers: dict[str, str] | None = None,
                      raise_for_error: bool = True) -> httpx.Response:
        """One buffered call; non-2xx answers raise :class:`GatewayError` unless ``raise_for_error`` is False."""
        try:
            resp = await self.client.request(method, path, params=params, json=json_body, content=content,
                                             headers=self._headers(request_id, headers))
        except httpx.HTTPError as exc:  # connect/read errors, timeouts, protocol errors
            log.warning("core call %s %s failed: %s (request_id=%s)", method, path, type(exc).__name__, request_id)
            raise core_unavailable(exc) from exc
        if raise_for_error and resp.status_code >= 400:
            raise relay_error(resp.status_code, resp.content, request_id)
        return resp

    @asynccontextmanager
    async def stream(self, method: str, path: str, *, request_id: str | None, params: Any = None,
                     headers: dict[str, str] | None = None) -> AsyncIterator[httpx.Response]:
        """Streaming call (SSE). Errors before the first byte raise like :meth:`request`."""
        timeout = httpx.Timeout(self.settings.core_sse_read_timeout_s, connect=self.settings.core_connect_timeout_s)
        req = self.client.build_request(method, path, params=params, headers=self._headers(request_id, headers),
                                        timeout=timeout)
        try:
            resp = await self.client.send(req, stream=True)
        except httpx.HTTPError as exc:
            raise core_unavailable(exc) from exc
        try:
            if resp.status_code >= 400:
                content = await resp.aread()
                raise relay_error(resp.status_code, content, request_id)
            yield resp
        finally:
            await resp.aclose()

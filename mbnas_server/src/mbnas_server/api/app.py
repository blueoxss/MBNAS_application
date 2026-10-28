"""Gateway FastAPI application factory and the ``mbnas-gateway`` entry point (WP-1 skeleton, contract section 3.1(b);
WP-3B relay, uploads, rate limits, audit log and static web).

``create_app(settings)`` requires ``MBNAS_GW_API_TOKEN`` (startup fails without it) and mounts every ``/api/v1`` route behind
the bearer dependency: the minimal ``GET /api/v1/health`` and the read-only archive router. CORS allows exactly
``MBNAS_GW_CORS_ORIGINS`` (no wildcard, no credentials); ``X-Request-ID`` is echoed on every response and carried in every
Error body. FastAPI's own ``/docs`` and ``/openapi.json`` are disabled: the canonical contract is the packaged
``mbnas_contracts/openapi/openapi.json``. Request bodies larger than ``MBNAS_GW_MAX_REQUEST_BODY_BYTES`` (default 1 MiB) are
answered 413 before they are read. uvicorn access/error log records are passed through ``util.log_redact`` so URL/header
secrets are never logged.

The gateway has no database and imports no core or runner code (``mbnas_model_runtime`` only lazily, for validate
manifests). WP-3B: the console API is relayed to core over HTTP (:mod:`mbnas_server.api.relay`, ``core_url`` +
``core_prefix`` with ``core_token``); ``GET /api/v1/health`` merges gateway and core status; ``POST
/api/v1/uploads/model-config`` stores model YAML uploads (body limit ``upload_max_bytes`` + 64 KiB multipart slack on
that path); :class:`~mbnas_server.api.limits.RateLimitMiddleware` limits ``/api`` traffic per token and writes the
sensitive-read audit log; :class:`~mbnas_server.api.path_guard.PathGuardMiddleware` answers 404 ``NOT_FOUND`` for a
``.``/``..`` path segment before routing (DD-A44); the built web console is served from ``web_dist`` for every other
GET (DD-A30).
``create_app(settings, core_transport=...)`` accepts an ``httpx`` transport for the in-process combined harness.

WP-5B: ``app.state.artifact_store`` is the content-addressed artifact store (``ArtifactStore.from_settings``: root
``artifact_store``, cap ``artifact_max_bytes``); stale temp files of crashed writers are swept when the app starts.
Artifact bytes, refresh, export and the configuration revisions are served by :mod:`mbnas_server.api.artifact_routes`.

Access log (DD-A60 (5), C11 "T5 log request id linkage"): :class:`AccessLogMiddleware` writes exactly one line per
HTTP request to the ``mbnas_server.access`` logger - ``method``, ``path`` (query string through
:func:`~mbnas_contracts.util.log_redact.redact`: ``token``/``access_token``/``sig``/``exp`` values become ``***``;
control characters and whitespace escaped, so one request is always one line), ``status``, ``request_id`` (the id
this gateway also forwards to the core as ``X-Request-ID``, so the core's access line of the relayed call carries the
same id), ``duration_ms`` and ``client`` - on stderr, the gateway's existing process log (``runtime/logs/gateway.log``
under start.sh, the container log in docker), with the redaction filter installed on its handler. ``mbnas-gateway``
runs uvicorn with ``access_log=False``: this line replaces uvicorn's own access line there (a ``uvicorn --factory``
launch keeps both).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time

from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mbnas_contracts.asgi import BodyLimitMiddleware, RequestIdMiddleware
from mbnas_contracts.openapi import contract
from mbnas_contracts.util.log_redact import install_log_redaction, redact
from mbnas_server.api import artifact_routes, errors, relay, static_web, uploads
from mbnas_server.api.auth import require_bearer
from mbnas_server.api.gateway_inputs import InputsCache
from mbnas_server.api.core_client import CoreClient
from mbnas_server.api.limits import AuditLog, Limiter, RateLimitMiddleware
from mbnas_server.api.path_guard import PathGuardMiddleware
from mbnas_server.archive import ArchiveStore
from mbnas_server.archive.routes import router as archive_router
from mbnas_server.artifacts.store import ArtifactStore
from mbnas_server.config import GatewaySettings

API_PREFIX = "/api/v1"
ALLOWED_HEADERS = ["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID", "X-Request-ID", "X-Filename"]
EXPOSED_HEADERS = ["X-Request-ID", "X-Artifact-Sha256", "X-Artifact-Verification", "X-Bundle-Sha256",
                   "Content-Disposition", "Retry-After"]
GRACEFUL_SHUTDOWN_S = 3
log = logging.getLogger("mbnas_server.api")
# composed at import (a dotted string literal would read as a module reference to check_import_closure)
ACCESS_LOGGER = __name__.split(".")[0] + ".access"
ACCESS_FORMAT = "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s %(message)s"
_UNPRINTABLE = re.compile(r"[\x00-\x20\x7f-\xa0]")


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """StreamHandler on the *current* ``sys.stderr`` (looked up per record, like logging's last-resort handler)."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property  # type: ignore[override]
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, _value: Any) -> None:
        pass


def access_logger() -> logging.Logger:
    """The gateway's access logger: INFO, one stderr handler with UTC timestamps (installed once), not propagated (so
    a root handler can never print a request twice), redaction filter on logger and handler. Idempotent."""
    lg = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(h, _StderrHandler) for h in lg.handlers):
        handler = _StderrHandler()
        formatter = logging.Formatter(ACCESS_FORMAT, "%Y-%m-%dT%H:%M:%S")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        lg.addHandler(handler)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    install_log_redaction((ACCESS_LOGGER,))
    return lg


def _printable(text: str) -> str:
    """Control characters, whitespace and C1 characters percent-escaped (one line per request, no injected line)."""
    return _UNPRINTABLE.sub(lambda m: f"%{ord(m.group(0)):02X}", text)


class AccessLogMiddleware:
    """Pure ASGI: one access-log line per HTTP request, written when the request ends (see the module docstring).

    Sits inside :class:`RequestIdMiddleware` (the request id is already in ``scope["state"]``) and outside CORS, the
    rate limiter, the path guard and the body limit, so preflights, 429, dot-segment 404 and 413 answers are logged
    too; a request whose application raised before answering is logged with status 500 (Starlette's error middleware
    sends that 500)."""

    def __init__(self, app: Any, logger: logging.Logger | None = None) -> None:
        self.app = app
        self.log = logger or logging.getLogger(ACCESS_LOGGER)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status: list[int] = []

        async def send_status(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start" and not status:
                status.append(int(message["status"]))
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        finally:
            self._write(scope, status[0] if status else 500, (time.perf_counter() - started) * 1000.0)

    def _write(self, scope: dict[str, Any], status: int, duration_ms: float) -> None:
        try:
            path = str(scope.get("path") or "/")
            query = scope.get("query_string") or b""
            if query:
                path = f"{path}?{query.decode('latin-1')}"
            rid = (scope.get("state") or {}).get("request_id") or "-"
            client = scope.get("client")
            peer = f"{client[0]}:{client[1]}" if client else "-"
            self.log.info("method=%s path=%s status=%d request_id=%s duration_ms=%.1f client=%s",
                          _printable(str(scope.get("method") or "-")), _printable(redact(path)), status,
                          _printable(str(rid)), duration_ms, _printable(peer))
        except Exception:  # noqa: BLE001 - logging must never break a response
            pass

UPLOAD_PATH = API_PREFIX + "/uploads/"
MULTIPART_SLACK = 64 << 10


class RoutedBodyLimit:
    """Pure ASGI: :class:`BodyLimitMiddleware` with ``upload_bytes`` below ``/api/v1/uploads/`` and ``default_bytes``
    elsewhere (a 1 MiB YAML in a multipart envelope is slightly larger than 1 MiB); its 413 bodies name ``location``
    ``gateway`` (C05 D3)."""

    def __init__(self, app: Any, default_bytes: int, upload_bytes: int, location: str = "gateway") -> None:
        self.default = BodyLimitMiddleware(app, max_bytes=default_bytes, location=location)
        self.upload = BodyLimitMiddleware(app, max_bytes=upload_bytes, location=location)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        routed = scope.get("type") == "http" and str(scope.get("path") or "").startswith(UPLOAD_PATH)
        await (self.upload if routed else self.default)(scope, receive, send)


def create_app(settings: GatewaySettings | None = None,
               core_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """Build the gateway app (see module docstring)."""
    settings = settings or GatewaySettings.load()
    token = settings.require_api_token()
    # uvicorn has configured its loggers before it calls this factory: strip token/access_token/sig/exp query
    # values and Authorization values from access/error log records (S11-F3)
    install_log_redaction()
    version = str(contract()["info"]["version"])  # the API version served is the packaged contract's

    core = CoreClient(settings, transport=core_transport)
    store = ArtifactStore.from_settings(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        try:
            removed = store.sweep_tmp()  # temp files of crashed writers (DD-A47 (a): hard-link temps by ctime)
            if removed:
                log.info("artifact store: %d stale temp file(s) removed at start", len(removed))
        except Exception as exc:  # noqa: BLE001 - an unreadable store must not stop the gateway (health reports it)
            log.warning("artifact store sweep skipped: %s", getattr(exc, "code", None) or type(exc).__name__)
        try:
            yield
        finally:
            await core.aclose()

    app = FastAPI(title="MBNAS Server API", version=version, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.core = core
    app.state.api_token = token
    app.state.settings = settings
    app.state.artifact_store = store  # WP-5B (DD-A39): runner local transfer target, served with digest re-check
    app.state.inputs_cache = InputsCache.from_settings(settings)  # DD-A46: validate-time host inputs
    app.state.contract_version = version
    app.state.archive = ArchiveStore(settings.archive_roots, settings.archive_driver_dirs, settings.archive_cache_ttl_s,
                                     campaign_roots=settings.archive_campaign_roots)  # DD-A23: 12-search campaign arms
    errors.install(app)

    protected = APIRouter(prefix=API_PREFIX, dependencies=[Depends(require_bearer)])
    protected.include_router(relay.router)  # WP-3B: console relay to core (+ merged /health)
    protected.include_router(artifact_routes.router)  # WP-5B: refresh, export zip, config revisions
    protected.include_router(uploads.router)  # F5a model-config uploads
    protected.include_router(archive_router)  # contract 1.3.0: read-only /archive/* (bearer, like every route above)
    app.include_router(protected)
    app.include_router(artifact_routes.open_router, prefix=API_PREFIX)  # artifact bytes: bearer or signed URL
    app.include_router(static_web.router)  # DD-A30: last, catch-all GET outside /api

    app.add_middleware(RoutedBodyLimit, default_bytes=settings.max_request_body_bytes,  # innermost
                       upload_bytes=max(settings.max_request_body_bytes, settings.upload_max_bytes + MULTIPART_SLACK))
    app.add_middleware(PathGuardMiddleware)  # DD-A44 (e): dot path segments -> 404 before routing
    app.add_middleware(RateLimitMiddleware, settings=settings, limiter=Limiter(settings),
                       audit=AuditLog(settings.audit_log))  # inside CORS: a 429 still carries CORS headers
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "OPTIONS"], allow_headers=ALLOWED_HEADERS,
                       expose_headers=EXPOSED_HEADERS, max_age=600)
    app.add_middleware(AccessLogMiddleware, logger=access_logger())  # DD-A60 (5): one line per request with its id
    app.add_middleware(RequestIdMiddleware)
    return app


def app_from_env() -> FastAPI:
    """uvicorn factory: ``uvicorn --factory mbnas_server.api.app:app_from_env``."""
    return create_app(GatewaySettings.load())


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description="MBNAS gateway (user-facing /api/v1 + read-only archive)")
    parser.add_argument("--env-file", default=None, help="dotenv file (else environment / MBNAS_GW_ENV_FILE)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    if args.env_file:
        os.environ["MBNAS_GW_ENV_FILE"] = args.env_file
    settings = GatewaySettings.load()
    problem = settings.core_url_problem()  # DD-A41 (d): refuse before uvicorn starts (exit 2, like mbnas-runner)
    if problem is not None:
        print(f"mbnas-gateway: {problem}", file=sys.stderr)
        return 2
    # open SSE streams never end on their own: bound the graceful shutdown so a restart takes seconds; clients
    # reconnect with Last-Event-ID and the server replays what they missed
    # access_log=False: AccessLogMiddleware writes the one access line per request (with its request id, DD-A60 (5))
    uvicorn.run("mbnas_server.api.app:app_from_env", factory=True, host=args.host or settings.host,
                port=args.port or settings.port, proxy_headers=True, log_level="info", access_log=False,
                timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S)
    return 0

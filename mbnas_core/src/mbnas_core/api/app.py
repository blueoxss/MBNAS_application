"""FastAPI application factory of the core console app and the ``mbnas-core-api`` entry point (WP-1 contract §3.1(b)).

``create_app(settings, services=None)`` requires ``MBNAS_CORE_API_TOKEN`` (startup fails without it), initialises the
DB schema once, and mounts every console route at ``/core/v1/console`` behind a scoped principal of kind ``server`` or
``admin`` (WP-3 contract section 1.1; the gateway relays the browser's ``/api/v1`` to it with its server token) except
the artifact download, which accepts that principal *or* a valid signed URL. Responses and SSE events go through the
projection hook :func:`mbnas_core.console.projection.project_public`. The same routes are also mounted at ``/api/v1``
behind the static bearer token only when ``CoreSettings.expose_legacy_api`` is true (owner-mode local debugging;
default false). CORS allows exactly ``MBNAS_CORE_CORS_ORIGINS`` (no
wildcard, no credentials); ``X-Request-ID`` is echoed on every response and carried in every Error body. FastAPI's own
``/docs`` and ``/openapi.json`` are disabled: the canonical contract is ``mbnas_contracts/openapi/openapi.json``.
Request bodies larger than ``MBNAS_CORE_MAX_REQUEST_BODY_BYTES`` (default 1 MiB) are answered 413 before they are read.
uvicorn access/error log records are passed through ``util.log_redact`` so URL/header secrets are never logged.
The read-only ``/archive/*`` routes are not part of this app: they are served by the gateway (``mbnas_server``).

WP-2 (contract section 1.4): ``/core/v1/runner/*`` (E1-E10, scope ``runner``; :mod:`mbnas_core.runner_api.routes`) and
the minimal ``/core/v1/owner/{runners,principals}`` reads (scope ``admin``) are mounted beside ``/api/v1`` (kept until
WP-3). WP-4 (contract 1.5.0): every console body, SSE event and the public export is the public projection
(:mod:`mbnas_core.console.projection`, opaque ids salted per database), the configuration-revision routes
(:mod:`mbnas_core.console.routes_revisions`) join the console, and the raw owner audit / owner export live only under
``/core/v1/owner/*`` (:mod:`mbnas_core.console.owner_routes`, scope ``admin``). Runner routes have their own body limit ``MBNAS_CORE_RUNNER_BODY_LIMIT_BYTES`` (32 MiB); every other route keeps
``MBNAS_CORE_MAX_REQUEST_BODY_BYTES``. Job tickets are signed with ``MBNAS_CORE_TICKET_KEY`` (a random per-process key
when unset, logged as a warning). The core sweeper (:mod:`mbnas_core.runner_api.sweeper`) runs once when the app
starts and then every ``heartbeat_s`` until shutdown.

Access log (DD-A60 (5), C11 "T5 log request id linkage"): :class:`AccessLogMiddleware` writes exactly one line per
HTTP request to the ``mbnas_core.access`` logger - ``method``, ``path`` (query string through
:func:`~mbnas_contracts.util.log_redact.redact`: ``token``/``access_token``/``sig``/``exp`` values become ``***``;
control characters and whitespace escaped, so one request is always one line), ``status``, ``request_id`` (the
``X-Request-ID`` the gateway forwards, so the gateway's and the core's line of one user request carry the same id),
``duration_ms`` and ``client`` - on stderr, the core's existing process log (``runtime/logs/core.log`` under start.sh,
the journal / container log otherwise), with the redaction filter installed on its handler. ``mbnas-core-api`` runs
uvicorn with ``access_log=False``: this line replaces uvicorn's own access line there (a ``uvicorn --factory`` launch
keeps both).
"""
from __future__ import annotations

import argparse
import logging
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mbnas_core.config import CoreSettings
from mbnas_core.db.core import connect, public_id_salt
from mbnas_core.events.bus import EventBus
from mbnas_contracts.util.log_redact import install_log_redaction, redact
from mbnas_core.api import errors
from mbnas_core.console import (
    owner_routes, projection, routes_files, routes_meta, routes_registry_structure, routes_revisions, routes_runs,
    routes_stream, routes_training,
)
from mbnas_contracts.asgi import BodyLimitMiddleware, RequestIdMiddleware
from mbnas_core.console.deps import console_principal, legacy_principal
from mbnas_core.console.services import AppServices
from mbnas_core.runner_api import routes as runner_routes
from mbnas_core.runner_api.sweeper import Sweeper

API_PREFIX = "/api/v1"
CONSOLE_PREFIX = "/core/v1/console"
API_VERSION = "1.5.0"  # contract version served (mbnas_contracts openapi.json; WP-4 promotion, DD-A42)
ALLOWED_HEADERS = ["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID", "X-Request-ID"]
EXPOSED_HEADERS = ["X-Request-ID", "X-Artifact-Sha256", "X-Bundle-Sha256", "Content-Disposition"]
GRACEFUL_SHUTDOWN_S = 3
log = logging.getLogger("mbnas_core.api")
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
    """The core's access logger: INFO, one stderr handler with UTC timestamps (installed once), not propagated (so a
    root handler can never print a request twice), redaction filter on logger and handler. Idempotent."""
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

    Sits inside :class:`RequestIdMiddleware` (the request id is already in ``scope["state"]``) and outside CORS and the
    body limit, so preflights and 413 answers are logged too; a request whose application raised before answering is
    logged with status 500 (Starlette's error middleware sends that 500)."""

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


class RoutedBodyLimit:
    """Pure ASGI: :class:`BodyLimitMiddleware` with ``runner_bytes`` below ``prefix`` and ``default_bytes`` elsewhere.

    Its 413 bodies name ``location`` ``core`` (C05 D3) - also on the legacy ``/api/v1`` owner debug mount, whose path
    alone would read as a gateway route; the runner / owner protocol paths get no ``location`` field
    (:func:`mbnas_contracts.asgi.body_limit_location`)."""

    def __init__(self, app: Any, default_bytes: int, runner_bytes: int, prefix: str = runner_routes.RUNNER_PREFIX,
                 location: str = "core") -> None:
        self.default = BodyLimitMiddleware(app, max_bytes=default_bytes, location=location)
        self.runner = BodyLimitMiddleware(app, max_bytes=runner_bytes, location=location)
        self.prefix = prefix.rstrip("/")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path") or "")
        routed = scope.get("type") == "http" and (path == self.prefix or path.startswith(self.prefix + "/"))
        await (self.runner if routed else self.default)(scope, receive, send)


def _ticket_key(settings: CoreSettings) -> bytes:
    if settings.ticket_key is not None:
        return settings.ticket_key.get_secret_value().encode("utf-8")
    log.warning("MBNAS_CORE_TICKET_KEY is not set: using a random per-process ticket key (tickets of leased jobs "
                "become invalid when the core restarts)")
    return secrets.token_bytes(32)


def create_app(settings: CoreSettings | None = None, services: AppServices | None = None) -> FastAPI:
    """Build the core console app (see module docstring)."""
    settings = settings or CoreSettings.load()
    token = settings.require_api_token()
    # uvicorn has configured its loggers before it calls this factory: strip token/access_token/sig/exp query
    # values and Authorization values from access/error log records (S11-F3)
    install_log_redaction()
    svc = services or AppServices(settings)
    conn = connect(settings)  # creates/migrates the schema once
    try:
        salt = public_id_salt(conn)
    finally:
        conn.close()
    if salt:
        projection.configure(salt)  # opaque public worker / device ids are stable per database (DD-A19 D7)
    bus = EventBus(settings, poll_s=svc.sse_poll_s)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        sweeper = Sweeper(settings).start() if settings.sweeper_enabled else None
        _app.state.sweeper = sweeper
        try:
            yield
        finally:
            if sweeper is not None:
                sweeper.stop()

    app = FastAPI(title="MBNAS Core API", version=API_VERSION, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.api_token = token
    app.state.ticket_key = _ticket_key(settings)
    app.state.core_contract = API_VERSION
    app.state.services = svc
    app.state.bus = bus
    errors.install(app)

    def console_routers(prefix: str, guard: Any, mode: str) -> None:
        protected = APIRouter(prefix=prefix, dependencies=[Depends(guard)])
        protected.include_router(routes_meta.router)
        protected.include_router(routes_runs.router)
        protected.include_router(routes_training.router)
        protected.include_router(routes_registry_structure.router)  # contract 1.4.0: registry / user model structure
        protected.include_router(routes_revisions.router)  # contract 1.5.0: configuration revisions (F5d)
        protected.include_router(routes_stream.router)
        app.include_router(protected)
        app.include_router(routes_files.build_router(mode), prefix=prefix)  # credential or signed URL, per route

    console_routers(CONSOLE_PREFIX, console_principal, "console")  # scope server|admin (WP-3 section 1.1)
    if settings.expose_legacy_api:
        console_routers(API_PREFIX, legacy_principal, "legacy")  # owner local debug only
    app.include_router(runner_routes.router, prefix=runner_routes.RUNNER_PREFIX)  # scope runner (WP-2 E1-E10)
    app.include_router(runner_routes.owner_router, prefix=runner_routes.OWNER_PREFIX)  # scope admin
    app.include_router(owner_routes.router, prefix=runner_routes.OWNER_PREFIX)  # scope admin: raw audit / owner export

    app.add_middleware(RoutedBodyLimit, default_bytes=settings.max_request_body_bytes,  # innermost: CORS/request id apply
                       runner_bytes=settings.runner_body_limit_bytes)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                       allow_methods=["GET", "POST", "OPTIONS"], allow_headers=ALLOWED_HEADERS,
                       expose_headers=EXPOSED_HEADERS, max_age=600)
    app.add_middleware(AccessLogMiddleware, logger=access_logger())  # DD-A60 (5): one line per request with its id
    app.add_middleware(RequestIdMiddleware)
    return app


def app_from_env() -> FastAPI:
    """uvicorn factory: ``uvicorn --factory mbnas_core.api.app:app_from_env``."""
    return create_app(CoreSettings.load())


def main(argv: list[str] | None = None) -> int:
    import os

    import uvicorn

    parser = argparse.ArgumentParser(description="MBNAS core console API (WP-1: /api/v1 console routes)")
    parser.add_argument("--env-file", default=None, help="dotenv file (else environment / MBNAS_CORE_ENV_FILE)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    if args.env_file:
        os.environ["MBNAS_CORE_ENV_FILE"] = args.env_file
    settings = CoreSettings.load()
    # open SSE streams never end on their own: bound the graceful shutdown so a restart takes seconds; clients
    # reconnect with Last-Event-ID and the server replays what they missed
    # access_log=False: AccessLogMiddleware writes the one access line per request (with its request id, DD-A60 (5))
    uvicorn.run("mbnas_core.api.app:app_from_env", factory=True, host=args.host or settings.host,
                port=args.port or settings.port, proxy_headers=True, log_level="info", access_log=False,
                timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S)
    return 0

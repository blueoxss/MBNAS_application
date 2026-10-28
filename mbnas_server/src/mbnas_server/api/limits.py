"""Per-token rate limiting and the sensitive-read audit log of the gateway (R13 / DD-A20, WP-3 contract section 2.6).

:class:`RateLimitMiddleware` (pure ASGI, applies to ``/api/`` paths only; static web is not limited):

* token bucket per credential: ``rate_limit_rps`` refill, ``rate_limit_burst`` capacity (defaults 10 / 50);
* expensive routes (``POST`` validate, every ``structure-check``, ``training-runs``; ``GET`` export): at most
  ``expensive_concurrency`` in flight (default 1) and ``expensive_per_minute`` starts per rolling minute per credential;
* SSE (``GET /runs/{id}/events``): at most ``sse_max_per_token`` open streams per credential (default 4).

Exceeding any limit answers 429 ``RATE_LIMITED`` (``location=gateway``, ``Retry-After``). The credential key is
``sha256(bearer)[:16]`` (the token itself is never stored), or ``anon:<client ip>`` for requests without a bearer (signed
artifact URLs, requests that will fail authentication). The limiter runs before routing, so unauthenticated floods are
limited as well.

:class:`AuditLog` appends one JSON line per sensitive read (candidate detail, lineage, generations; live and archive) to
``GatewaySettings.audit_log`` (default ``$MBNAS_HOME/gateway/logs/audit.jsonl``):
``{time, token_id, route, method, target, status, request_id}``. ``target`` is the path parameters as a dict.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from typing import Any

from mbnas_contracts.errors import ErrorLocation
from mbnas_contracts.util.time import utcnow_iso
from mbnas_server.api.errors import error_body
from mbnas_server.config import GatewaySettings

log = logging.getLogger("mbnas_server.api.limits")

EXPENSIVE = [
    ("POST", re.compile(r"^/api/v1/protocols/validate$")),
    ("POST", re.compile(r"^/api/v1/structure-check$")),
    ("POST", re.compile(r"^/api/v1/registry/[^/]+/structure-check$")),
    ("POST", re.compile(r"^/api/v1/runs/[^/]+/candidates/[^/]+/structure-check$")),
    ("POST", re.compile(r"^/api/v1/training-runs$")),
    ("GET", re.compile(r"^/api/v1/runs/[^/]+/export$")),
]
SSE = re.compile(r"^/api/v1/runs/[^/]+/events$")
AUDITED = [
    ("candidate_detail", re.compile(r"^/api/v1/runs/(?P<run_id>[^/]+)/candidates/(?P<candidate_id>[^/]+)$")),
    ("archive_candidate_detail",
     re.compile(r"^/api/v1/archive/runs/(?P<run_id>[^/]+)/candidates/(?P<candidate_id>[^/]+)$")),
    ("archive_generations", re.compile(r"^/api/v1/archive/runs/(?P<run_id>[^/]+)/generations$")),
    ("archive_generation", re.compile(r"^/api/v1/archive/runs/(?P<run_id>[^/]+)/generations/(?P<generation>[^/]+)$")),
    ("lineage", re.compile(r"^/api/v1/.*/lineage(/.*)?$")),
]


def credential_key(scope: dict[str, Any]) -> str:
    headers = dict(scope.get("headers") or [])
    auth = headers.get(b"authorization", b"").decode("latin-1")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return hashlib.sha256(value.strip().encode()).hexdigest()[:16]
    client = scope.get("client") or ("?", 0)
    return f"anon:{client[0]}"


class _Bucket:
    __slots__ = ("tokens", "stamp")

    def __init__(self, burst: float) -> None:
        self.tokens, self.stamp = burst, time.monotonic()


class Limiter:
    """In-process limiter state (one per app)."""

    def __init__(self, settings: GatewaySettings) -> None:
        self.s = settings
        self.lock = threading.Lock()
        self.buckets: dict[str, _Bucket] = {}
        self.inflight: dict[str, int] = defaultdict(int)
        self.starts: dict[str, deque[float]] = defaultdict(deque)
        self.streams: dict[str, int] = defaultdict(int)

    def take(self, key: str) -> float | None:
        """Consume one token; returns None or the seconds to wait."""
        with self.lock:
            b = self.buckets.get(key) or self.buckets.setdefault(key, _Bucket(self.s.rate_limit_burst))
            now = time.monotonic()
            b.tokens = min(float(self.s.rate_limit_burst), b.tokens + (now - b.stamp) * self.s.rate_limit_rps)
            b.stamp = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return None
            return (1.0 - b.tokens) / self.s.rate_limit_rps

    def enter_expensive(self, key: str) -> str | None:
        with self.lock:
            now = time.monotonic()
            q = self.starts[key]
            while q and now - q[0] >= 60.0:
                q.popleft()
            if self.inflight[key] >= self.s.expensive_concurrency:
                return f"at most {self.s.expensive_concurrency} expensive request(s) at a time"
            if len(q) >= self.s.expensive_per_minute:
                return f"at most {self.s.expensive_per_minute} expensive requests per minute"
            self.inflight[key] += 1
            q.append(now)
            return None

    def leave_expensive(self, key: str) -> None:
        with self.lock:
            self.inflight[key] = max(0, self.inflight[key] - 1)

    def enter_stream(self, key: str) -> bool:
        with self.lock:
            if self.streams[key] >= self.s.sse_max_per_token:
                return False
            self.streams[key] += 1
            return True

    def leave_stream(self, key: str) -> None:
        with self.lock:
            self.streams[key] = max(0, self.streams[key] - 1)


class AuditLog:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"
        try:
            with self.lock:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except OSError:
            log.exception("audit log %s is not writable", self.path)


async def _reject(scope: dict[str, Any], send: Any, message: str, retry_after: float) -> None:
    request_id = (scope.get("state") or {}).get("request_id")
    body = json.dumps(error_body(request_id, "RATE_LIMITED", message, ErrorLocation.GATEWAY.value)).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
               (b"retry-after", str(max(1, int(retry_after + 0.999))).encode())]
    await send({"type": "http.response.start", "status": 429, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware:
    def __init__(self, app: Any, settings: GatewaySettings, limiter: Limiter, audit: AuditLog) -> None:
        self.app, self.s, self.limiter, self.audit = app, settings, limiter, audit

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path") or "")
        if scope["type"] != "http" or not path.startswith("/api/") or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method"))
        key = credential_key(scope)
        status = {"code": 0}

        async def tracking_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status["code"] = int(message["status"])
            await send(message)

        expensive = stream = False
        if self.s.rate_limit_enabled:
            wait = self.limiter.take(key)
            if wait is not None:
                await _reject(scope, send, f"too many requests (limit {self.s.rate_limit_rps:g}/s, burst "
                                           f"{self.s.rate_limit_burst})", wait)
                return
            if any(m == method and rx.match(path) for m, rx in EXPENSIVE):
                why = self.limiter.enter_expensive(key)
                if why is not None:
                    await _reject(scope, send, why, 1.0)
                    return
                expensive = True
            elif method == "GET" and SSE.match(path):
                if not self.limiter.enter_stream(key):
                    await _reject(scope, send, f"at most {self.s.sse_max_per_token} event streams per token", 3.0)
                    return
                stream = True
        try:
            await self.app(scope, receive, tracking_send)
        finally:
            if expensive:
                self.limiter.leave_expensive(key)
            if stream:
                self.limiter.leave_stream(key)
            if method == "GET":
                for name, rx in AUDITED:
                    m = rx.match(path)
                    if m:
                        self.audit.write({"time": utcnow_iso(), "token_id": key, "route": name, "method": method,
                                          "target": m.groupdict(), "status": status["code"],
                                          "request_id": (scope.get("state") or {}).get("request_id")})
                        break

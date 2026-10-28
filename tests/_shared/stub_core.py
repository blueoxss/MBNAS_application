"""Stub core for the gateway UNIT tests of tests/public/server (WP-3C; contract WP-3 section 3 "gateway 전용 검사").

``StubCore`` is a FastAPI app mounted under ``/core/v1/console`` that records every request (method, path, query,
headers, JSON body) and answers from a programmable table. It is reached through ``harness.StreamingASGITransport`` so no
socket is needed. Its answers are NOT the core's (the real relay is exercised by tests/owner with ``combined_app``); they
only pin what the gateway does with a given upstream answer. No ``mbnas_core`` import (public suite rule, DD-A22).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

PREFIX = "/core/v1/console"
CORE_TOKEN = "mbnas_server_stubpref_stub-core-secret-0123456789"


class StubCore:
    def __init__(self, token: str = CORE_TOKEN) -> None:
        self.token = token
        self.calls: list[dict[str, Any]] = []
        self.routes: dict[tuple[str, str], Callable[[dict[str, Any]], Response]] = {}
        self.sse_frames: list[str] = ["retry: 3000\n\n", "id: 1\nevent: run.created\ndata: {\"seq\":1}\n\n",
                                      ": heartbeat\n\n", "id: 2\nevent: run.state\ndata: {\"seq\":2}\n\n"]
        self.sse_hold_open = False
        self.app = self._build()

    # -- programming ---------------------------------------------------------------------------------------------
    def on(self, method: str, path: str, status: int = 200, body: Any = None, headers: dict[str, str] | None = None,
           content: bytes | None = None, media_type: str | None = None) -> None:
        def answer(_call: dict[str, Any]) -> Response:
            if content is not None:
                return Response(content, status_code=status, headers=headers, media_type=media_type)
            return JSONResponse(body if body is not None else {}, status_code=status, headers=headers)

        self.routes[(method.upper(), path)] = answer

    def last(self, path_suffix: str | None = None) -> dict[str, Any]:
        calls = [c for c in self.calls if path_suffix is None or c["path"].endswith(path_suffix)]
        assert calls, f"stub core saw no call ending with {path_suffix!r}: {[c['path'] for c in self.calls]}"
        return calls[-1]

    # -- app -------------------------------------------------------------------------------------------------------
    def _build(self) -> FastAPI:
        app = FastAPI()
        stub = self

        @app.api_route(PREFIX + "/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
        async def any_route(rest: str, request: Request) -> Response:
            raw = await request.body()
            try:
                body = json.loads(raw) if raw else None
            except ValueError:
                body = raw
            call = {"method": request.method, "path": request.url.path, "query": dict(request.query_params),
                    "headers": {k.lower(): v for k, v in request.headers.items()}, "body": body}
            stub.calls.append(call)
            if request.headers.get("authorization") != f"Bearer {stub.token}":
                return JSONResponse({"code": "ACCESS_DENIED", "message": "stub core: wrong service token",
                                     "request_id": request.headers.get("x-request-id")}, status_code=403)
            if rest.endswith("/events") and request.method == "GET":
                async def frames():
                    for f in stub.sse_frames:
                        yield f
                        await asyncio.sleep(0)
                    while stub.sse_hold_open:
                        await asyncio.sleep(0.02)
                return StreamingResponse(frames(), media_type="text/event-stream",
                                         headers={"Cache-Control": "no-cache"})
            handler = stub.routes.get((request.method, request.url.path))
            if handler is None:
                return JSONResponse({"stub": True, "path": request.url.path}, status_code=200)
            return handler(call)

        @app.get("/core/v1/health")
        async def health() -> dict[str, Any]:
            return {"status": "ok", "version": "stub"}

        return app

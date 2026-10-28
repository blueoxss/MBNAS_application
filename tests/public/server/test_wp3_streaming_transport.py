"""WP-3C harness self-test: ``harness.StreamingASGITransport`` streams an endless ASGI body chunk by chunk (what the
in-process SSE relay needs) and delivers ``http.disconnect`` to the app when the client closes the response."""
from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from conftest_common import owner_harness  # DD-A43(b): owner-only harness; module skipped without it

harness = owner_harness()


def _app(state: dict) -> FastAPI:
    app = FastAPI()

    @app.get("/endless")
    async def endless(request: Request) -> StreamingResponse:
        async def body():
            i = 0
            try:
                while True:
                    yield f"data: {i}\n\n"
                    i += 1
                    await asyncio.sleep(0.01)
            finally:
                state["closed"] = True

        return StreamingResponse(body(), media_type="text/event-stream")

    @app.post("/echo")
    async def echo(request: Request) -> dict:
        return {"body": (await request.body()).decode(), "q": request.url.query,
                "h": request.headers.get("x-request-id")}

    return app


def test_endless_stream_is_delivered_incrementally_and_closed():
    state: dict = {}

    async def go() -> list[str]:
        t = harness.StreamingASGITransport(_app(state))
        async with httpx.AsyncClient(transport=t, base_url="http://core.invalid") as c:
            got: list[str] = []
            async with c.stream("GET", "/endless") as r:
                assert r.status_code == 200
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        got.append(line)
                    if len(got) == 3:
                        break
            await asyncio.sleep(0.05)
            return got

    got = asyncio.run(go())
    assert got == ["data: 0", "data: 1", "data: 2"]
    assert state.get("closed") is True


def test_body_query_and_headers_reach_the_app():
    async def go() -> dict:
        t = harness.StreamingASGITransport(_app({}))
        async with httpx.AsyncClient(transport=t, base_url="http://core.invalid") as c:
            r = await c.post("/echo?a=1&b=x%20y", content=b"payload", headers={"X-Request-ID": "rid-1"})
            assert r.status_code == 200
            assert t.requests == [("POST", "/echo")]
            return r.json()

    assert asyncio.run(go()) == {"body": "payload", "q": "a=1&b=x%20y", "h": "rid-1"}

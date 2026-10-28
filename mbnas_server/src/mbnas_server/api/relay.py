"""``/api/v1`` relay routers of the gateway (WP-3 contract section 2.2-2.5; WEB_PRIVATE_INVENTORY section 7).

Every console route is forwarded to core ``<core_url><core_prefix><same sub-path>`` with the service token
(:mod:`mbnas_server.api.core_client`); query strings, ``Idempotency-Key`` and ``Last-Event-ID`` are forwarded, the gateway
request id is propagated as ``X-Request-ID``. Core answers are returned with their status, body and the relevant headers
(``Content-Type``, ``Content-Disposition``, ``X-Artifact-Sha256``, ``X-Bundle-Sha256``, ``Cache-Control``,
``X-Content-Type-Options``, ``ETag``, ``Last-Modified``). In JSON and SSE bodies, absolute or relative URLs that point at
core (``<core_url><core_prefix>``, ``<core_url>/api/v1``, ``"<core_prefix>/``) are rewritten to this gateway's
``/api/v1`` so artifact ``url``/``refresh_url`` and ``snapshot_url`` stay usable from the browser.

Route-specific work:

* ``GET /health`` — gateway status merged with the core health; core unreachable -> HTTP 200, ``status: degraded``,
  ``core: "unreachable"`` and empty core sections (never invented values).
* ``POST /protocols/validate`` — :mod:`validate_prep` adds ``path_checks`` / ``dataset_yaml`` / ``dataset_manifests`` and the
  answer's ``resolution.paths.*.realpath`` is scrubbed to ``requested``. Host inputs a browser put in the body are
  dropped first (only this gateway measures its host; the core trusts them). A valid answer's inputs are cached by its
  ``protocol_hash`` (DD-A46, :mod:`mbnas_server.api.gateway_inputs`).
* ``POST /runs``, ``POST /training-runs``, ``POST /runs/{id}/final-retrain`` (DD-A46) and ``POST /runs/{id}/resume``
  (re-validated before a PAUSED run resumes) — the same host inputs are attached (measured now from the request's
  protocol, for a final retrain / resume from the run's protocol in core's run view; the cache entry of the protocol
  hash when there is nothing to measure), so the core re-validates with the customer host's view instead of its own
  filesystem; browser-supplied host inputs are dropped. Nothing is attached without ``dataset_roots``.
* ``POST /structure-check`` and ``POST /training-runs`` (``source.kind == "user"``) — ``upload_id`` (or a legacy absolute
  ``model_path`` read on this host) is replaced by ``model_yaml_text`` + ``source_sha256`` (+ ``model_name``), so core
  never receives a path for a translated request; body problems go to core unchanged (DD-A44 (d),
  :func:`mbnas_server.api.uploads.carry_model_source`). Without ``model_yaml_roots`` and ``output_roots`` an absolute
  ``model_path`` goes to core unchanged (DD-A47 (d), owner all-in-one: core checks its own roots).
* ``GET /runs/{id}/events`` — SSE relay, frame-faithful (``after_seq`` forwarded, ``Last-Event-ID`` only when it is a
  decimal sequence number; gateway heartbeat comment when core is silent for ``sse_heartbeat_s``). When core ends the
  stream the browser stream ends too (never a synthesized ``snapshot.required``); when it fails mid-way a ``retry:``
  field is sent first, so EventSource reconnects with its Last-Event-ID.

Every path parameter goes into the core URL through :func:`mbnas_server.api.path_guard.segment` (one percent-encoded
segment; 404 ``NOT_FOUND`` for a dot segment or a control character), and JSON bodies the gateway re-encodes are parsed
strictly (``NaN``/``Infinity``/overflowing numbers: 422 ``VALIDATION_FAILED``, never echoed).
* Artifact bytes, refresh, export and the configuration revisions: :mod:`mbnas_server.api.artifact_routes` (WP-5B:
  bytes from the gateway's artifact store, records from core).
"""
from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from mbnas_contracts.errors import ErrorLocation
from mbnas_contracts.protocol_hash import protocol_hash
from mbnas_server.api import gateway_inputs, validate_prep
from mbnas_server.api.core_client import CoreClient
from mbnas_server.api.errors import GatewayError
from mbnas_server.api.path_guard import segment
from mbnas_server.api.uploads import carry_model_source
from mbnas_server.config import GatewaySettings

API_PREFIX = "/api/v1"
RELAY_HEADERS = ("content-type", "content-disposition", "x-artifact-sha256", "x-bundle-sha256", "cache-control",
                 "x-content-type-options", "etag", "last-modified", "retry-after")
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
SSE_RETRY_MS = 3000
RETRY = f"retry: {SSE_RETRY_MS}\n\n".encode()  # sent when the core stream fails mid-way (core already sent one at start)
LAST_EVENT_ID = re.compile(r"[0-9]+")
# DD-A44 (d): the keys core's POST /structure-check and TrainingSource accept; a legacy model_path request is translated
# to model_yaml_text only when its keys are a subset of these (else core answers the body problem itself)
STRUCTURE_CHECK_KEYS = frozenset({"model_path", "model_yaml_text", "source_sha256", "model_name", "input_size",
                                  "class_count"})
TRAINING_SOURCE_KEYS = frozenset({"kind", "run_id", "candidate_id", "baseline_id", "model_path", "model_yaml_text",
                                  "source_sha256", "model_name"})

router = APIRouter()  # mounted under the bearer-protected /api/v1 router by the app factory


# ------------------------------------------------------------------------------------------------ helpers
def _core(request: Request) -> CoreClient:
    return request.app.state.core


def _settings(request: Request) -> GatewaySettings:
    return request.app.state.settings


def _inputs_cache(request: Request) -> gateway_inputs.InputsCache:
    cache = getattr(request.app.state, "inputs_cache", None)
    if cache is None:
        cache = request.app.state.inputs_cache = gateway_inputs.InputsCache.from_settings(_settings(request))
    return cache


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _fwd_headers(request: Request) -> dict[str, str]:
    """``Idempotency-Key`` as received; ``Last-Event-ID`` only when it is a decimal sequence number (a malformed one is
    dropped, as core would ignore it; ``after_seq`` then decides)."""
    out = {}
    key = request.headers.get("idempotency-key")
    if key is not None:
        out["idempotency-key"] = key
    last = (request.headers.get("last-event-id") or "").strip()
    if LAST_EVENT_ID.fullmatch(last):
        out["last-event-id"] = last
    return out


def _url_pairs(request: Request) -> list[tuple[bytes, bytes]]:
    s = _settings(request)
    public = (str(request.base_url).rstrip("/") + API_PREFIX).encode()
    pairs = [((s.core_url + s.core_prefix).encode(), public), ((s.core_url + API_PREFIX).encode(), public)]
    if s.core_prefix != API_PREFIX:
        pairs.append((f'"{s.core_prefix}/'.encode(), f'"{API_PREFIX}/'.encode()))
    return pairs


def _rewrite(data: bytes, pairs: list[tuple[bytes, bytes]]) -> bytes:
    for old, new in pairs:
        if old in data:
            data = data.replace(old, new)
    return data


def _response(request: Request, resp: httpx.Response, content: bytes | None = None) -> Response:
    data = resp.content if content is None else content
    ctype = resp.headers.get("content-type", "")
    if content is None and ctype.startswith("application/json"):
        data = _rewrite(data, _url_pairs(request))
    headers = {k: resp.headers[k] for k in RELAY_HEADERS if k in resp.headers and k != "content-type"}
    return Response(data, status_code=resp.status_code, headers=headers, media_type=ctype or None)


class _NonFinite(ValueError):
    pass


def _no_constant(_name: str) -> Any:
    raise _NonFinite("non-finite numbers are not valid JSON")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):  # 1e999
        raise _NonFinite("number out of the finite range")
    return value


def strict_loads(raw: bytes) -> Any:
    """``json.loads`` that refuses the non-standard constants and overflowing numbers Python would accept (they could
    not be re-encoded for core)."""
    return json.loads(raw.decode("utf-8"), parse_constant=_no_constant, parse_float=_finite_float)


def _invalid_json(message: str) -> GatewayError:
    return GatewayError(422, "VALIDATION_FAILED", "request body is not valid JSON",
                        errors=[{"field": "$", "code": "JSON_INVALID", "message": message}])


async def _json(request: Request) -> Any:
    """The request body as JSON (``None`` when empty); 422 ``VALIDATION_FAILED`` for anything that is not strict JSON.
    Messages never echo the offending input."""
    raw = await request.body()
    try:
        return strict_loads(raw) if raw else None
    except UnicodeDecodeError as exc:
        raise _invalid_json("body is not UTF-8") from exc
    except ValueError as exc:  # JSONDecodeError (position only), _NonFinite, int digit limit
        raise _invalid_json(str(exc)[:200]) from exc


async def relay(request: Request, method: str, path: str, *, json_body: Any = None, raw: bool = False) -> Response:
    """Forward ``request`` (its query string, body unless ``json_body`` is given) to core ``path``."""
    content = None
    if json_body is None and method == "POST" and raw is False:
        content = await request.body()
    headers = _fwd_headers(request)
    if content is not None:
        headers["content-type"] = request.headers.get("content-type", "application/json")
    resp = await _core(request).request(method, path, request_id=_rid(request), params=request.query_params.multi_items()
                                        or None, json_body=json_body, content=content, headers=headers)
    return _response(request, resp)


# ------------------------------------------------------------------------------------------------ health
def _gateway_status(request: Request) -> dict[str, Any]:
    from mbnas_server.api.static_web import dist_present
    from mbnas_server.doctor import store_writable

    s = _settings(request)
    return {"status": "ok", "version": request.app.state.contract_version, "core_url_configured": bool(s.core_url),
            "web_dist": dist_present(s.web_dist), "store_writable": store_writable(s.artifact_store)}


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    s = _settings(request)
    gateway = _gateway_status(request)
    try:
        resp = await _core(request).request("GET", "/health", request_id=_rid(request))
        body = json.loads(_rewrite(resp.content, _url_pairs(request)))
        if not isinstance(body, dict):
            raise GatewayError(502, "CORE_BAD_RESPONSE", "core health is not an object", location=ErrorLocation.CORE)
        body["core"] = {"status": body.get("status"), "reachable": True}
    except GatewayError as exc:
        body = {"status": "degraded", "warnings": [f"core: {exc.code}"], "capabilities": {},
                "versions": {}, "devices": [], "registry": [], "commit_sha": "unknown", "presets": [],
                "worker_count": 0,
                "core": "unreachable" if exc.code == "CORE_UNAVAILABLE" else {"reachable": True, "error": exc.code}}
    if not gateway["store_writable"] and body.get("status") == "ok":
        body["status"] = "degraded"
        body.setdefault("warnings", []).append("gateway: artifact store not writable")
    # DD-A19 / DD-A59 (a): the allowlists are the customer host's roots, i.e. this gateway's own settings (empty lists
    # when unset) plus its read-only archive roots. The core's allowlists name core-host paths: never relayed, also not
    # as a fallback when the gateway has no roots of its own.
    body["allowlists"] = {"dataset_roots": list(s.dataset_roots), "output_roots": list(s.output_roots),
                          "archive_roots": list(s.archive_roots)}
    body["gateway"] = gateway
    body["request_id"] = _rid(request)
    return JSONResponse(body)


# ------------------------------------------------------------------------------------------------ meta
@router.get("/protocols/presets")
async def presets(request: Request) -> Response:
    return await relay(request, "GET", "/protocols/presets")


@router.get("/registry")
async def registry(request: Request) -> Response:
    return await relay(request, "GET", "/registry")


@router.post("/protocols/validate")
async def validate(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, dict):
        body = gateway_inputs.attach(body, None)  # host inputs come from this gateway only
        body = await run_in_threadpool(validate_prep.prepare, _settings(request), body)
    resp = await _core(request).request("POST", "/protocols/validate", request_id=_rid(request), json_body=body,
                                        headers=_fwd_headers(request))
    try:
        answer = strict_loads(resp.content)
        data = json.dumps(validate_prep.scrub_realpaths(answer), separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode()
    except ValueError:  # not a JSON object answer: relayed as received (URL rewrite only)
        return _response(request, resp)
    inputs = gateway_inputs.inputs_of(body)
    if inputs is not None and isinstance(answer, dict) and answer.get("valid") and answer.get("protocol_hash"):
        await run_in_threadpool(_inputs_cache(request).put, answer["protocol_hash"], inputs)  # DD-A46
    return _response(request, resp, _rewrite(data, _url_pairs(request)))


def _hash_of(protocol: Any) -> str | None:
    try:
        return protocol_hash(protocol) if isinstance(protocol, dict) else None
    except (TypeError, ValueError):  # not canonical JSON: core answers that itself
        return None


async def _with_inputs(request: Request, body: Any, *, protocol_hash_: Any = None, protocol: Any = None) -> Any:
    """``body`` (a JSON object) with browser-supplied host inputs dropped and this gateway's attached (DD-A46)."""
    if not isinstance(body, dict):
        return body
    settings = _settings(request)
    inputs = None
    if gateway_inputs.enabled(settings):
        inputs = await run_in_threadpool(gateway_inputs.for_request, settings, _inputs_cache(request),
                                         protocol_hash=protocol_hash_, protocol=protocol)
    return gateway_inputs.attach(body, inputs)


async def _relay_json(request: Request, path: str, body: Any) -> Response:
    """POST ``body`` (re-encoded JSON) to core ``path``; the answer as received (URL rewrite)."""
    if body is None:  # empty request body: forwarded empty, core answers it
        return await relay(request, "POST", path)
    resp = await _core(request).request("POST", path, request_id=_rid(request), json_body=body,
                                        headers=_fwd_headers(request))
    return _response(request, resp)


# ------------------------------------------------------------------------------------------------ runs
@router.post("/runs")
async def create_run(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, dict):
        body = await _with_inputs(request, body, protocol_hash_=body.get("protocol_hash"), protocol=body.get("protocol"))
    return await _relay_json(request, "/runs", body)


@router.get("/runs")
async def list_runs(request: Request) -> Response:
    return await relay(request, "GET", "/runs")


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> Response:
    return await relay(request, "GET", f"/runs/{segment(run_id)}")


@router.get("/runs/{run_id}/snapshot")
async def snapshot(request: Request, run_id: str) -> Response:
    return await relay(request, "GET", f"/runs/{segment(run_id)}/snapshot")


@router.post("/runs/{run_id}/pause")
async def pause(request: Request, run_id: str) -> Response:
    return await relay(request, "POST", f"/runs/{segment(run_id)}/pause")


@router.post("/runs/{run_id}/resume")
async def resume(request: Request, run_id: str) -> Response:
    """A PAUSED run is re-validated before it resumes: the run's host inputs are attached like on run creation
    (DD-A46; the body is otherwise empty)."""
    path = f"/runs/{segment(run_id)}/resume"
    enabled = gateway_inputs.enabled(_settings(request))
    body = await _json(request)
    if body is None and not enabled:
        return await relay(request, "POST", path)
    if body is None:
        body = {}
    if isinstance(body, dict):  # browser-supplied host inputs are dropped in every profile
        run_hash = run_protocol = None
        if enabled:
            run_hash, run_protocol = await _run_protocol(request, run_id)
        body = await _with_inputs(request, body, protocol_hash_=run_hash, protocol=run_protocol)
    return await _relay_json(request, path, body)


@router.post("/runs/{run_id}/cancel")
async def cancel(request: Request, run_id: str) -> Response:
    return await relay(request, "POST", f"/runs/{segment(run_id)}/cancel")


@router.post("/runs/{run_id}/final-retrain")
async def final_retrain(request: Request, run_id: str) -> Response:
    path = f"/runs/{segment(run_id)}/final-retrain"
    body = await _json(request)
    if isinstance(body, dict):
        parent_hash = parent_protocol = None
        if gateway_inputs.enabled(_settings(request)):  # the child re-uses the parent's resolved protocol
            parent_hash, parent_protocol = await _run_protocol(request, run_id)
        body = await _with_inputs(request, body, protocol_hash_=parent_hash, protocol=parent_protocol)
    return await _relay_json(request, path, body)


async def _run_protocol(request: Request, run_id: str) -> tuple[Any, Any]:
    """``(protocol_hash, resolved_protocol)`` of a run from core's run view; ``(None, None)`` when core cannot answer
    (it then answers the relayed request itself: 404, 503, ...)."""
    try:
        run = json.loads((await _core(request).request("GET", f"/runs/{segment(run_id)}",
                                                       request_id=_rid(request))).content)
    except (GatewayError, ValueError):
        return None, None
    if not isinstance(run, dict):
        return None, None
    return run.get("protocol_hash"), run.get("resolved_protocol")


@router.get("/runs/{run_id}/candidates/{candidate_id}")
async def candidate(request: Request, run_id: str, candidate_id: str) -> Response:
    return await relay(request, "GET", f"/runs/{segment(run_id)}/candidates/{segment(candidate_id)}")


@router.post("/runs/{run_id}/candidates/{candidate_id}/structure-check")
async def candidate_structure_check(request: Request, run_id: str, candidate_id: str) -> Response:
    return await relay(request, "POST", f"/runs/{segment(run_id)}/candidates/{segment(candidate_id)}/structure-check")


# ------------------------------------------------------------------------------------------------ training / structure
@router.post("/training-runs")
async def training_runs(request: Request) -> Response:
    body = await _json(request)
    source = body.get("source") if isinstance(body, dict) else None
    if isinstance(source, dict) and source.get("kind") == "user":
        carried = await run_in_threadpool(carry_model_source, _settings(request), source, TRAINING_SOURCE_KEYS,
                                          "source.")
        if carried is not None:
            body = dict(body, source=carried)
    if isinstance(body, dict):
        protocol = body.get("protocol")
        body = await _with_inputs(request, body, protocol_hash_=_hash_of(protocol), protocol=protocol)
    resp = await _core(request).request("POST", "/training-runs", request_id=_rid(request), json_body=body,
                                        headers=_fwd_headers(request))
    return _response(request, resp)


@router.post("/structure-check")
async def user_structure_check(request: Request) -> Response:
    body = await _json(request)
    if isinstance(body, dict):
        carried = await run_in_threadpool(carry_model_source, _settings(request), body, STRUCTURE_CHECK_KEYS, "")
        if carried is not None:
            body = carried
    resp = await _core(request).request("POST", "/structure-check", request_id=_rid(request), json_body=body,
                                        headers=_fwd_headers(request))
    return _response(request, resp)


@router.get("/registry/{model_id}/structure")
async def registry_structure(request: Request, model_id: str) -> Response:
    return await relay(request, "GET", f"/registry/{segment(model_id)}/structure")


@router.post("/registry/{model_id}/structure-check")
async def registry_structure_check(request: Request, model_id: str) -> Response:
    return await relay(request, "POST", f"/registry/{segment(model_id)}/structure-check")


@router.get("/structure/block-catalog")
async def block_catalog(request: Request) -> Response:
    return await relay(request, "GET", "/structure/block-catalog")



# ------------------------------------------------------------------------------------------------ SSE
def _sse_comment(text: str) -> bytes:
    return f": {text}\n\n".encode()


@router.get("/runs/{run_id}/events")
async def events(request: Request, run_id: str) -> StreamingResponse:
    core = _core(request)
    settings = _settings(request)
    pairs = _url_pairs(request)
    cm = core.stream("GET", f"/runs/{segment(run_id)}/events", request_id=_rid(request),
                     params=request.query_params.multi_items() or None, headers=_fwd_headers(request))
    resp = await cm.__aenter__()  # errors before the first byte become ordinary Error answers (404, 503, ...)

    async def body() -> AsyncIterator[bytes]:
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)

        async def pump() -> None:
            buf, failed = b"", False
            try:
                async for chunk in resp.aiter_bytes():  # decoded; also works on pre-read bodies
                    buf += chunk
                    while b"\n\n" in buf:
                        frame, buf = buf.split(b"\n\n", 1)
                        await queue.put(_rewrite(frame, pairs) + b"\n\n")
            except Exception:  # noqa: BLE001 - core went away mid-stream
                failed = True
            finally:
                if buf:
                    await queue.put(_rewrite(buf, pairs))
                if failed:
                    await queue.put(RETRY)
                await queue.put(None)

        task = asyncio.create_task(pump())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=settings.sse_heartbeat_s)
                except asyncio.TimeoutError:
                    yield _sse_comment("heartbeat gateway")
                    continue
                if item is None:
                    break
                yield item
        finally:
            task.cancel()
            try:
                await cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(body(), media_type="text/event-stream", headers=SSE_HEADERS)

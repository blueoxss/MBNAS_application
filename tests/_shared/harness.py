"""Shared helpers for tests/owner/{api,worker,orchestration,qa} and tests/public/server (tests/_shared on sys.path).

Everything here is SYNTHETIC test plumbing: temp settings/DB/output root, a static all-AVAILABLE registry,
a static A100-shaped device list, the stub handlers of ``stub_handlers.py`` and an in-process worker variant.
"""
from __future__ import annotations

import copy
import io
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TOKEN = "test-token-0123456789"
SIGNING_KEY = "test-signing-key-abcdefghijklmnop"
ORIGIN = "http://localhost:5173"
# Host paths of this test run (WP-1 contract §9): tests/_shared/conftest_common.py sets the MBNAS_TEST_* defaults under
# $TARGET_ROOT/runtime/test_root before this module is imported. Read at import time (clean_env removes MBNAS_*).
TEST_ROOT = os.environ.get("MBNAS_TEST_ROOT") or str(HERE.parents[1] / "runtime" / "test_root")
TEST_WEIGHTS_DIR = os.environ.get("MBNAS_TEST_WEIGHTS_DIR", f"{TEST_ROOT}/weights")


def make_settings(tmp_roots: dict[str, Path], **overrides: Any):
    from mbnas_core.config import CoreSettings as Settings

    kw: dict[str, Any] = dict(api_token=TOKEN, artifact_signing_key=SIGNING_KEY, cors_origins=[ORIGIN],
                              db_path=str(tmp_roots["runs"] / "db" / "mbnas.sqlite3"), output_root=str(tmp_roots["runs"]),
                              output_roots=[str(tmp_roots["runs"])], dataset_roots=[str(tmp_roots["data"])],
                              lease_ttl_s=30, heartbeat_s=1, max_replay_events=50, min_free_disk_gb=0.0,
                              weights_dir=TEST_WEIGHTS_DIR, log_dir=str(tmp_roots["runs"] / "logs"),
                              # WP-3A: a core app built directly from these settings (no gateway) keeps the owner debug
                              # mount /api/v1; the combined harness passes expose_legacy_api=False explicitly
                              expose_legacy_api=True)
    kw.update(overrides)
    return Settings(**kw)


def runner_settings_for(core_settings: Any, **overrides: Any):
    """RunnerSettings the owner worker hands to a job child for ``core_settings`` (contract §5 ``child_settings_env``:
    work root = core output root, same allow roots / weights / logs, pickle resume allowed on the owner host)."""
    from mbnas_runner.config import RunnerSettings

    kw: dict[str, Any] = dict(work_root=str(core_settings.output_root), output_roots=list(core_settings.output_roots),
                              dataset_roots=list(core_settings.dataset_roots), weights_dir=str(core_settings.weights_dir),
                              log_dir=str(core_settings.log_dir), allow_pickle_resume=True)
    kw.update(overrides)
    return RunnerSettings(**kw)


def make_gateway_settings(tmp_roots: dict[str, Path], **overrides: Any):
    """GatewaySettings for the gateway app (archive tests, contract §9): same bearer token / signing key / CORS origin
    as ``make_settings``; archive roots empty unless given; artifact store, logs, the audit log and the manifest /
    gateway-input cache (``manifest_cache_root``: ``manifests/``, ``gateway_inputs/``) inside the tmp run root -- every
    gateway path that defaults to ``$MBNAS_HOME`` is pinned, so no test writes into ``<repo>/runtime/gateway``."""
    from mbnas_server.config import GatewaySettings

    gw = tmp_roots["runs"] / "gateway"
    kw: dict[str, Any] = dict(api_token=TOKEN, artifact_signing_key=SIGNING_KEY, cors_origins=[ORIGIN],
                              artifact_store=str(gw / "artifacts"), log_dir=str(gw / "logs"),
                              audit_log=str(gw / "logs" / "audit.jsonl"), manifest_cache_root=str(gw),
                              archive_roots=[], archive_driver_dirs=[], archive_campaign_roots=[],
                              # WP-3C: no web bundle unless a test passes one (the default <repo>/mbnas_web/dist exists
                              # only on hosts where the bundle was built; tests must not depend on that)
                              web_dist=str(tmp_roots["runs"] / "gateway" / "no-web-dist"))
    kw.update(overrides)
    return GatewaySettings(**kw)


def stub_env(monkeypatch: Any, epoch_s: float = 0.01) -> None:
    """Child processes import stub_handlers from this directory."""
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(HERE), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep))
    monkeypatch.setenv("MBNAS_JOB_HANDLERS_MODULE", "stub_handlers")
    monkeypatch.setenv("MBNAS_STUB_EPOCH_S", str(epoch_s))
    if str(HERE) not in sys.path:  # appended: tests/_shared/datasets.py must not shadow a site-packages `datasets`
        sys.path.append(str(HERE))


def make_services(settings: Any, registry: dict[str, dict[str, Any]], devices: list[dict[str, Any]], **kw: Any):
    from mbnas_core.console.services import AppServices

    entries = list(registry.values())
    return AppServices(settings, registry_fn=lambda: (entries, "synthetic-test-registry", None),
                       devices_fn=lambda: copy.deepcopy(devices),
                       versions_fn=lambda: {"python": "test", "packages": {}},
                       source_fn=lambda: {"algorithm": "test", "sha256": "sha256:" + "0" * 64, "files": 0},
                       **{"sse_heartbeat_s": 0.3, "sse_poll_s": 0.05, **kw})


def small_protocol(ds: dict[str, str], output_root: str, *, arms: tuple[str, ...] = ("GA", "IndependentRandom"),
                   population: int = 2, generations: int = 2, baselines: tuple[str, ...] = ("yolo11n",),
                   targets: tuple[int, ...] = (1, 2), budget_h: float = 0.75, score_ge: float = 0.0) -> dict[str, Any]:
    """PILOT_SMOKE-shaped protocol over a synthetic dataset with tiny rungs."""
    from mbnas_core.protocol.presets import pilot_smoke_protocol

    proto = pilot_smoke_protocol({"dataset_yaml": ds["dataset_yaml"], "train_root": ds["train_root"],
                                  "search_val_root": ds["search_val_root"], "final_eval_root": ds["final_eval_root"],
                                  "output_root": output_root})
    weights = [r["fitness_weights"] for r in proto["rungs"]]
    ids = [f"L{i}" for i in range(len(targets))]
    proto["rungs"] = [{"id": rid, "epoch_target_total": t, "fitness_weights": weights[min(i, len(weights) - 1)],
                       "promotion": ({"to": ids[i + 1], "score_ge": score_ge, "ap_ge": 0.0} if i + 1 < len(ids) else None)}
                      for i, (rid, t) in enumerate(zip(ids, targets))]
    proto["training"]["scheduler_horizon_epochs"] = targets[-1]
    proto["console"]["baselines"] = [b for b in proto["console"]["baselines"] if b["id"] in baselines]
    proto["console"]["baseline_epoch_target"] = targets[-1]
    proto["console"]["search"].update(arms=list(arms), population=population, generations=generations,
                                      budget_gpu_hours=budget_h)
    proto["comparison"]["search_budget_GPU_hours_per_arm_per_seed"] = budget_h
    return proto


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def validate_and_run(client: Any, protocol: dict[str, Any], key: str = "idem-1") -> tuple[str, dict[str, Any]]:
    """POST validate then run; returns (run_id, validate body)."""
    v = client.post("/api/v1/protocols/validate", json={"protocol": protocol}, headers=auth())
    assert v.status_code == 200, v.text
    body = v.json()
    assert body["valid"], body["errors"]
    r = client.post("/api/v1/runs", json={"protocol": body["resolved"], "protocol_hash": body["protocol_hash"],
                                         "idempotency_key": key, "validation_id": body["validation_id"]},
                    headers=auth())
    assert r.status_code == 200, r.text
    return r.json()["run_id"], body


def make_inline_worker(settings: Any, worker_id: str = "inline-worker:1"):
    """Worker that runs handlers in-process (fast orchestration tests; the subprocess path has its own tests)."""
    from mbnas_core.db import repo
    from mbnas_core.db.core import tx
    from mbnas_core.orchestration import engine
    from mbnas_core.owner_worker import child
    from mbnas_contracts.jobs_protocol import Emitter
    from mbnas_core.owner_worker.contract import handler_override
    from mbnas_core.owner_worker.main import Worker, WorkerConfig, _Live

    class InlineWorker(Worker):
        def execute(self, job: dict[str, Any]) -> None:
            base, stop, _log = self._paths(job)
            with tx(self.conn):
                assert jobs_start(self, job)
                job = repo.get_job(self.conn, job["id"])
                engine.job_started(self.conn, job, self.id)
            live = _Live(started_at=job.get("started_at"))
            buf = io.StringIO()
            spec = child.load_context(self.settings.db_path, job["id"], str(stop),
                                      native=handler_override() is None)
            result = child.run_job(spec, Emitter(job["id"], buf, str(stop)))
            for line in buf.getvalue().splitlines():
                self._handle_line(job, line, live, base)
            if live.progress:
                self._progress_event(job, live)
            self._finalize(job, result, 0, False, live)

    def jobs_start(w: Any, job: dict[str, Any]) -> bool:
        from mbnas_core.scheduling import jobs

        return jobs.start_attempt(w.conn, job["id"], w.id)

    return InlineWorker(settings, WorkerConfig(poll_s=0.01), worker_id=worker_id)


def drain(worker: Any, limit: int = 500) -> int:
    """Tick ``worker`` until no job can be leased; returns the number of jobs executed."""
    n = 0
    while n < limit and worker.tick():
        n += 1
    return n


def wait_for(predicate: Any, timeout: float = 30.0, interval: float = 0.05) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


# -- WP-3C: combined core + gateway in one process (contract WP-3 section 2.8) -----------------------------------------
#
# ``combined_app()`` builds the real core app (``/core/v1/console`` + ``/core/v1/runner`` + ``/core/v1/owner``,
# ``expose_legacy_api=False``) and the real gateway app (``/api/v1``) whose core client reaches the core app through an
# in-process ASGI transport: no sockets, no ports. Principals are issued with ``mbnas_core.runner_api.auth`` (one
# ``server`` token the gateway presents to core, one ``admin`` token for owner routes). The user's bearer is ``TOKEN``
# (the gateway's ``api_token``), so ``client.post("/api/v1/...", headers=auth())`` keeps working unchanged.

import httpx as _httpx  # noqa: E402  (installed in every profile: the gateway and core both depend on it)


class StreamingASGITransport(_httpx.AsyncBaseTransport):
    """``httpx.AsyncBaseTransport`` over an ASGI app that STREAMS the response body.

    ``httpx.ASGITransport`` (0.28) buffers the whole body until the app finishes, which never happens for an SSE stream;
    the gateway's SSE relay needs chunks as they are produced. The app runs in its own asyncio task; closing the
    response sends ``http.disconnect`` and cancels the task (what a real client disconnect does).
    """

    def __init__(self, app: Any, client: tuple[str, int] = ("127.0.0.1", 50000)) -> None:
        self.app = app
        self.client = client
        self.requests: list[tuple[str, str]] = []  # (method, path) seen, for relay assertions

    async def __aenter__(self) -> "StreamingASGITransport":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        return None

    async def handle_async_request(self, request: Any) -> Any:
        import asyncio

        import httpx

        body = b"".join([chunk async for chunk in request.stream])
        self.requests.append((request.method, request.url.path))
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": request.method,
                 "headers": [(k.lower(), v) for k, v in request.headers.raw], "scheme": request.url.scheme,
                 "path": request.url.path, "raw_path": request.url.raw_path.split(b"?")[0],
                 "query_string": request.url.query, "server": (request.url.host, request.url.port or 80),
                 "client": self.client, "root_path": ""}
        queue: asyncio.Queue = asyncio.Queue()
        disconnected = asyncio.Event()
        sent = False

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            await queue.put(message)

        async def run() -> None:
            try:
                await self.app(scope, receive, send)
            except BaseException as exc:  # surfaced to the reader below
                await queue.put({"type": "_error", "exc": exc})
            finally:
                await queue.put({"type": "_done"})

        task = asyncio.ensure_future(run())
        start = await queue.get()
        if start["type"] == "_error":
            raise start["exc"]
        if start["type"] != "http.response.start":
            raise RuntimeError(f"ASGI app sent {start['type']} before http.response.start")

        class _Body(httpx.AsyncByteStream):
            async def __aiter__(self):  # type: ignore[override]
                while True:
                    msg = await queue.get()
                    if msg["type"] == "http.response.body":
                        if msg.get("body"):
                            yield msg["body"]
                        if not msg.get("more_body", False):
                            return
                    elif msg["type"] == "_error":
                        raise msg["exc"]
                    elif msg["type"] == "_done":
                        return

            async def aclose(self) -> None:
                disconnected.set()
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except BaseException:
                        pass

        return httpx.Response(start["status"], headers=[(k, v) for k, v in start.get("headers", [])], stream=_Body())


def issue_principals(core_settings: Any, kinds: tuple[str, ...] = ("server", "admin")) -> dict[str, str]:
    """Issue one principal per kind on the core DB; returns ``{kind: token, f"{kind}_id": principal id}``."""
    from mbnas_core.db.core import connect
    from mbnas_core.runner_api import auth as core_auth

    conn = connect(core_settings)
    try:
        out: dict[str, str] = {}
        for kind in kinds:
            row, tok = core_auth.create_principal(conn, kind)
            out[kind], out[f"{kind}_id"] = tok, row["id"]
        return out
    finally:
        conn.close()


def core_settings_for_gateway(tmp_roots: dict[str, Path], **overrides: Any):
    """``make_settings`` for the core behind a gateway: legacy ``/api/v1`` NOT exposed, sweeper off (deterministic)."""
    kw: dict[str, Any] = dict(expose_legacy_api=False, sweeper_enabled=False)
    kw.update(overrides)
    return make_settings(tmp_roots, **kw)


def gateway_settings_for_core(tmp_roots: dict[str, Path], core_token: str, **overrides: Any):
    """``make_gateway_settings`` wired to an in-process core (URL host is only a label for the ASGI transport).

    The gateway's dataset/output allow roots mirror the core's (``make_settings``): owner all-in-one profile, where the
    gateway computes manifests on the same host (WP-3 section 2.4)."""
    kw: dict[str, Any] = dict(core_url="http://core.invalid", core_token=core_token,
                              dataset_roots=[str(tmp_roots["data"])], output_roots=[str(tmp_roots["runs"])])
    kw.update(overrides)
    return make_gateway_settings(tmp_roots, **kw)


def gateway_app_over(gw_settings: Any, core_app: Any, transport: Any | None = None):
    """Gateway app whose core client talks to ``core_app`` in-process (``create_app(settings, core_transport=...)``,
    the WP-3B seam; see docs/internal/evidence/WP3/TEST_ADAPTATIONS.md)."""
    import inspect

    from mbnas_server.api.app import create_app as create_gateway

    if "core_transport" not in inspect.signature(create_gateway).parameters:
        raise RuntimeError("mbnas_server.api.app.create_app(settings, *, core_transport=None) seam missing (WP-3B)")
    return create_gateway(gw_settings, core_transport=transport or StreamingASGITransport(core_app))


class Combined:
    """Result of :func:`combined_app`: the two apps, their settings, the issued tokens and the core transport."""

    def __init__(self, core_app: Any, gateway_app: Any, core_settings: Any, gateway_settings: Any,
                 tokens: dict[str, str], transport: StreamingASGITransport) -> None:
        self.core_app, self.gateway_app = core_app, gateway_app
        self.core_settings, self.gateway_settings = core_settings, gateway_settings
        self.tokens, self.transport = tokens, transport


def combined_app(tmp_roots: dict[str, Path], registry: dict[str, dict[str, Any]], devices: list[dict[str, Any]], *,
                 core_settings: Any | None = None, services: Any | None = None,
                 gateway_overrides: dict[str, Any] | None = None) -> Combined:
    """Core app + gateway app in one process (WP-3 section 2.8); see the block comment above."""
    from mbnas_core.api.app import create_app as create_core

    cs = core_settings or core_settings_for_gateway(tmp_roots)
    svc = services or make_services(cs, registry, devices)
    core_app = create_core(cs, svc)  # initialises the schema before principals are issued
    tokens = issue_principals(cs)
    gs = gateway_settings_for_core(tmp_roots, tokens["server"], **(gateway_overrides or {}))
    transport = StreamingASGITransport(core_app)
    return Combined(core_app, gateway_app_over(gs, core_app, transport), cs, gs, tokens, transport)

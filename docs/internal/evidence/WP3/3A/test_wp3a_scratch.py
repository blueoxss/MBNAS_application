"""WP-3A scratch self-checks (core side of WP-3 contract section 1). SYNTHETIC; CPU only; no sockets except the
sandbox's own subprocess pipes and one ephemeral 127.0.0.1 uvicorn
thread for SSE. Kept OUT of tests/ (a copy there was picked up by concurrent suite runs). Run it with a conftest that
exec()s tests/owner/conftest.py and tests/owner/api/conftest.py (see 3A/README.md), e.g. from a scratch dir:

    python -B -m pytest -q --timeout=150 --rootdir=<dir> <dir>/test_wp3a_scratch.py
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time

import pytest

import harness
from mbnas_core.db.core import connect
from mbnas_core.runner_api import auth as pauth

TINY = "nc: 1\nbackbone:\n  - [-1, 1, Conv, [16, 3, 2]]\n  - [-1, 1, Conv, [32, 3, 2]]\nhead:\n  - [[1], 1, Detect, [nc]]\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def core(tmp_roots, registry, host_devices):
    """Core app only, console at /core/v1/console, legacy /api/v1 off, sandbox ON (production defaults)."""
    from fastapi.testclient import TestClient
    from mbnas_core.api.app import create_app

    settings = harness.make_settings(tmp_roots, expose_legacy_api=False, sweeper_enabled=False)
    svc = harness.make_services(settings, registry, host_devices)
    app = create_app(settings, svc)
    conn = connect(settings)
    tokens = {k: pauth.create_principal(conn, k, runner_id="R1" if k == "runner" else None)[1]
              for k in ("server", "admin", "runner")}
    conn.close()
    with TestClient(app) as c:
        c.settings, c.tokens, c.svc = settings, tokens, svc  # type: ignore[attr-defined]
        yield c


def H(c, kind="server"):
    return {"Authorization": f"Bearer {c.tokens[kind]}"}


# -- (1) mount, scopes, legacy switch -------------------------------------------------------------------------------
def test_console_mount_scopes_and_legacy_off(core):
    assert core.get("/core/v1/console/health").status_code == 401
    assert core.get("/core/v1/console/health", headers=H(core, "runner")).status_code == 403
    assert core.get("/core/v1/console/health", headers={"Authorization": f"Bearer {harness.TOKEN}"}).status_code == 401
    assert core.get("/core/v1/console/health", headers=H(core)).status_code == 200
    assert core.get("/core/v1/console/health", headers=H(core, "admin")).status_code == 200
    assert core.get("/api/v1/health", headers=harness.auth()).status_code == 404  # expose_legacy_api=false (default)
    assert core.get("/core/v1/console/structure/block-catalog", headers=H(core)).status_code == 200


def test_legacy_mount_only_when_enabled(tmp_roots, registry, host_devices):
    from fastapi.testclient import TestClient
    from mbnas_core.api.app import create_app
    from mbnas_core.config import CoreSettings

    assert CoreSettings(output_root="/tmp/x").expose_legacy_api is False
    settings = harness.make_settings(tmp_roots, expose_legacy_api=True)
    with TestClient(create_app(settings, harness.make_services(settings, registry, host_devices))) as c:
        assert c.get("/api/v1/health", headers=harness.auth()).status_code == 200
        assert c.get("/api/v1/health").status_code == 401


# -- (2) projection --------------------------------------------------------------------------------------------------
def test_projection_admin_raw_server_pruned():
    from mbnas_contracts.openapi import prune
    from mbnas_core.console.projection import project_public

    event = {"seq": 1, "type": "run.updated", "run_id": "r", "payload": {}, "candidate_id": None}
    assert project_public("Event", event, "admin") == event
    assert project_public("Event", event, "server") == prune("Event", event)
    assert "candidate_id" not in project_public("Event", event, "server") or prune("Event", event).get("candidate_id") is None
    assert project_public("NotAComponent", {"a": None}, "server") == {"a": None}


def test_sse_on_console_mount(core, protocol):
    v = core.post("/core/v1/console/protocols/validate", json={"protocol": protocol}, headers=H(core)).json()
    assert v["valid"], v["errors"]
    r = core.post("/core/v1/console/runs", headers=H(core), json={"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                  "validation_id": v["validation_id"], "idempotency_key": "sse-1"})
    rid = r.json()["run_id"]
    # a real uvicorn thread (TestClient blocks closing an endless stream; same pattern as tests/owner/api/test_sse.py)
    import socket
    import httpx
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(core.app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    harness.wait_for(lambda: server.started, timeout=10)
    lines = []
    try:
        url = f"http://127.0.0.1:{port}/core/v1/console/runs/{rid}/events"
        with httpx.Client(timeout=10) as c, c.stream("GET", url, headers=H(core)) as resp:
            assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
            for line in resp.iter_lines():
                lines.append(line)
                if line.startswith("data: "):
                    break
        with httpx.Client(timeout=10) as c:
            assert c.get(url).status_code == 401  # SSE behind the principal too
    finally:
        server.should_exit = True
        th.join(timeout=10)
    data = json.loads(lines[-1][len("data: "):])
    assert data["run_id"] == rid and data["seq"] >= 1


# -- (3) manifest provider -------------------------------------------------------------------------------------------
def _gateway_body(protocol, settings):
    """What mbnas_server.api.validate_prep.prepare sends (computed here with the same runtime modules)."""
    from mbnas_server.api import validate_prep
    from mbnas_server.config import GatewaySettings

    gs = GatewaySettings(api_token=harness.TOKEN, dataset_roots=list(settings.dataset_roots),
                         output_roots=list(settings.output_roots),
                         artifact_store=settings.output_root + "/gw/art", log_dir=settings.output_root + "/gw/logs",
                         manifest_cache_root=settings.output_root + "/gw")
    return validate_prep.prepare(gs, {"protocol": protocol})


def test_validate_with_gateway_inputs_touches_no_core_file(core, protocol, monkeypatch):
    local = core.post("/core/v1/console/protocols/validate", json={"protocol": protocol}, headers=H(core)).json()
    body = _gateway_body(protocol, core.settings)
    assert {"path_checks", "dataset_yaml", "dataset_manifests"} <= set(body)
    from mbnas_core.protocol import validator

    def boom(*a, **k):
        raise AssertionError("core touched the filesystem in gateway mode")

    monkeypatch.setattr(validator, "check_server_path", boom)
    monkeypatch.setattr(validator, "load_dataset_yaml", boom)
    monkeypatch.setattr(validator, "free_disk_gb", boom)
    monkeypatch.setattr("mbnas_model_runtime.datasets.manifest.DatasetService.manifest_for", boom)
    r = core.post("/core/v1/console/protocols/validate", json=body, headers=H(core))
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["valid"], g["errors"]
    assert g["capabilities"]["validation_host"] == "gateway" and "validation_host" not in local["capabilities"]
    assert g["protocol_hash"] == local["protocol_hash"]  # same data, same protocol, either host
    assert g["resolved"]["dataset"] == local["resolved"]["dataset"]


def test_gateway_inputs_errors_are_validation_errors(core, protocol):
    body = _gateway_body(protocol, core.settings)
    bad = copy.deepcopy(body)
    bad["path_checks"]["train_root"] = {"requested": protocol["console"]["paths"]["train_root"], "realpath": None,
                                        "ok": False, "code": "PATH_OUTSIDE_ALLOWLIST", "message": "outside"}
    j = core.post("/core/v1/console/protocols/validate", json=bad, headers=H(core)).json()
    assert not j["valid"] and {"console.paths.train_root": "PATH_OUTSIDE_ALLOWLIST"}.items() <= {e["field"]: e["code"] for e in j["errors"]}.items()
    miss = copy.deepcopy(body)
    del miss["dataset_manifests"]["splits"]["final_eval"]
    j = core.post("/core/v1/console/protocols/validate", json=miss, headers=H(core)).json()
    assert not j["valid"] and any(e["code"] == "MANIFEST_MISSING" for e in j["errors"])
    junk = copy.deepcopy(body)
    junk["path_checks"]["train_root"]["unexpected"] = 1
    r = core.post("/core/v1/console/protocols/validate", json=junk, headers=H(core))
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"


# -- (4)/(5)/(9) sandbox, allowlist, carried YAML text ---------------------------------------------------------------
def _check(core, text, **extra):
    return core.post("/core/v1/console/structure-check", headers=H(core),
                     json={"model_yaml_text": text, "source_sha256": _sha(text), **extra})


def test_carried_text_runs_in_the_sandbox(core):
    r = _check(core, TINY, input_size=64, class_count=1)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "PASS" and doc["sandbox"]["isolation"] in ("unshare-rn+setsid+rlimits", "setsid+rlimits")
    assert doc["sandbox"]["limits"]["as_bytes"] == core.settings.structure_check_as_bytes
    assert doc["source_sha256"] == _sha(TINY)
    r = core.post("/core/v1/console/structure-check", headers=H(core), json={"model_yaml_text": TINY, "source_sha256": "0" * 64})
    assert r.status_code == 422 and r.json()["code"] == "SOURCE_SHA256_MISMATCH"
    r = core.post("/core/v1/console/structure-check", headers=H(core), json={"model_yaml_text": TINY, "model_path": "/x.yaml",
                                                                           "source_sha256": _sha(TINY)})
    assert r.status_code == 422


@pytest.mark.parametrize("module", ["check_requirements", "torch_load", "nn.Linear", "torchvision.ops.nms", "TorchVision",
                                    "__import__('os').system"])
def test_allowlist_rejects_before_any_build(core, monkeypatch, module):
    from mbnas_core.structure import sandbox

    monkeypatch.setattr(sandbox.StructureSandbox, "run", lambda *a, **k: pytest.fail("sandbox reached"))
    text = TINY.replace("[-1, 1, Conv, [32, 3, 2]]", f"[-1, 1, {json.dumps(module)}, [100000000, 100000000]]")
    r = _check(core, text, input_size=64)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["status"] == "FAIL" and "BLOCK_UNSUPPORTED" in {d["code"] for d in doc["diagnostics"]}
    assert doc.get("observation") is None


def test_argument_schema_limits(core, monkeypatch):
    from mbnas_core.structure import sandbox

    monkeypatch.setattr(sandbox.StructureSandbox, "run", lambda *a, **k: pytest.fail("sandbox reached"))
    cases = {
        "channels_4097": TINY.replace("[16, 3, 2]", "[4097, 3, 2]"),
        "int_65537": TINY.replace("[16, 3, 2]", "[16, 65537, 2]"),
        "repeats_65": TINY.replace("[-1, 1, Conv, [16", "[-1, 65, Conv, [16"),
        "rows_257": "nc: 1\nbackbone:\n" + "  - [-1, 1, Conv, [16, 3, 1]]\n" * 256 + "head:\n  - [[-1], 1, Detect, [nc]]\n",
        "nc_10001": TINY.replace("nc: 1", "nc: 10001"),
        "depth_3": TINY.replace("[16, 3, 2]", "[16, [[[1]]], 2]"),
        "string_expr": TINY.replace("[16, 3, 2]", "[16, '9**999999', 2]"),
        "alias_65": "a: &x [1]\nb: [" + ", ".join(["*x"] * 65) + "]\n" + TINY,
        "activation": "activation: __import__('os').system('true')\n" + TINY,
    }
    for name, text in cases.items():
        r = _check(core, text, input_size=64)
        assert r.status_code == 200, (name, r.text)
        codes = {d["code"] for d in r.json()["diagnostics"]}
        assert r.json()["status"] == "FAIL" and codes & {"ARG_SCHEMA_INVALID", "ACTIVATION_REFUSED"}, (name, codes)
    ok_rows = "nc: 1\nbackbone:\n" + "  - [-1, 1, Conv, [16, 3, 1]]\n" * 255 + "head:\n  - [[-1], 1, Detect, [nc]]\n"
    from mbnas_model_runtime.genotype import load_declared_text
    assert load_declared_text(ok_rows)[1] == []  # 256 rows is the bound itself
    assert load_declared_text("a: &x [1]\nb: [" + ", ".join(["*x"] * 64) + "]\n" + TINY)[1] == []  # 64 aliases allowed


def test_input_size_bound(core):
    r = _check(core, TINY, input_size=1312)
    assert r.status_code == 422 and r.json()["code"] == "INPUT_SIZE_INVALID"
    r = core.post("/core/v1/console/registry/yolo11n/structure-check", json={"input_size": 1312}, headers=H(core))
    assert r.status_code == 422 and r.json()["code"] == "INPUT_SIZE_INVALID"


def test_registry_models_pass_the_allowlist():
    from ultralytics.nn.tasks import yaml_model_load

    from mbnas_model_runtime.genotype import validate_declared
    from mbnas_model_runtime.registry import SPEC_BY_ID

    for spec in SPEC_BY_ID.values():
        if spec.backend == "ultralytics":
            assert validate_declared(yaml_model_load(spec.config)) == [], spec.id


def _rss_kb() -> int:
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1])
    return 0


def test_sandbox_failure_mapping(tmp_roots):
    from mbnas_core.console import model_services as ms
    from mbnas_core.structure import sandbox

    base = harness.make_settings(tmp_roots)
    def run(**over):
        s = base.model_copy(update=over)
        sandbox.bind(sandbox.for_settings(s))
        try:
            return ms.check_user_model(s, model_yaml_text=TINY, source_sha256=_sha(TINY), input_size=64, class_count=1)
        finally:
            sandbox.bind(None)

    rss = _rss_kb()
    doc = run(structure_check_as_bytes=2 << 30)
    assert doc["status"] == "UNVERIFIED" and doc["diagnostics"][0]["code"] == "STRUCTURE_CHECK_RESOURCE_LIMIT", doc["diagnostics"]
    assert _rss_kb() - rss < 50_000  # the API process did not grow by the worker's allocation (KiB)
    doc = run(structure_check_timeout_s=0.5)
    assert doc["status"] == "UNVERIFIED" and doc["diagnostics"][0]["code"] == "STRUCTURE_CHECK_TIMEOUT"
    doc = run(structure_check_max_output_bytes=1000)
    assert doc["diagnostics"][0]["code"] == "STRUCTURE_CHECK_RESOURCE_LIMIT"
    doc = run(structure_check_cpu_s=1)
    assert doc["status"] == "UNVERIFIED" and doc["diagnostics"][0]["code"] == "STRUCTURE_CHECK_RESOURCE_LIMIT", doc["diagnostics"]
    s = base.model_copy(update={"structure_check_unshare": False})
    sandbox.bind(sandbox.for_settings(s))
    try:
        doc = ms.check_user_model(s, model_yaml_text=TINY, source_sha256=_sha(TINY), input_size=64, class_count=1)
    finally:
        sandbox.bind(None)
    assert doc["status"] == "PASS" and doc["sandbox"]["isolation"] == "setsid+rlimits"


def test_worker_env_has_no_mbnas_secret(monkeypatch):
    from mbnas_core.structure import sandbox

    monkeypatch.setenv("MBNAS_CORE_API_TOKEN", "secret-x")
    env = sandbox.worker_env("/tmp/x")
    assert not [k for k in env if k.startswith("MBNAS")] and "secret-x" not in json.dumps(env)
    assert env["ULTRALYTICS_SAFE_LOAD"] == "1" and env["YOLO_AUTOINSTALL"] == "false" and env["YOLO_OFFLINE"] == "1"
    assert env["CUDA_VISIBLE_DEVICES"] == ""
    argv, iso = sandbox.StructureSandbox(sandbox.Limits(1, 1, 64, 0, 1, 0, 1, True)).argv()
    assert argv[-4:] == ["-I", "-B", "-m", "mbnas_core.structure.check_worker"] and "python" in argv[-5]
    assert (argv[:2] == [argv[0], "-rn"] and argv[0].endswith("unshare")) == (iso == "unshare-rn+setsid+rlimits")


def test_semaphore_one_check_at_a_time(tmp_roots):
    from mbnas_core.console import model_services as ms
    from mbnas_core.structure import sandbox

    s = harness.make_settings(tmp_roots, structure_check_queue_timeout_s=0.0)
    box = sandbox.for_settings(s)
    results: list = []

    def one():
        sandbox.bind(box)
        try:
            results.append(ms.check_user_model(s, model_yaml_text=TINY, source_sha256=_sha(TINY), input_size=64,
                                               class_count=1)["status"])
        except ms.StructureRequestError as exc:
            results.append((exc.code, exc.status))

    threads = [threading.Thread(target=one) for _ in range(2)]
    for t in threads:
        t.start()
        time.sleep(0.2)
    for t in threads:
        t.join()
    assert sorted(map(str, results)) == sorted(map(str, ["PASS", ("STRUCTURE_CHECK_BUSY", 429)])), results


# -- (9) training run from carried text, (8) device_used, (7) CLI ------------------------------------------------------
def test_training_source_carried_text_binding():
    from mbnas_core.training_runs.selection import TrainingSource

    src = TrainingSource(kind="user", model_yaml_text=TINY, source_sha256=_sha(TINY), model_name="m.yaml")
    assert "model_yaml_text" not in src.binding_dump() and src.binding_dump()["source_sha256"] == _sha(TINY)
    with pytest.raises(ValueError):
        TrainingSource(kind="user", model_yaml_text=TINY)
    with pytest.raises(ValueError):
        TrainingSource(kind="baseline", baseline_id="yolo11n", model_yaml_text=TINY, source_sha256=_sha(TINY))


def test_training_run_from_carried_text(core, make_dataset, tmp_roots, monkeypatch):
    ds = make_dataset("tds", counts={"train": 4, "val": 2, "test": 2})
    protocol = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"))
    protocol["console"]["training"] = {"epoch_target_total": 3, "seed": 13}
    protocol["console"]["search"]["evaluation_enabled"] = False
    text = TINY
    req = {"source": {"kind": "user", "model_yaml_text": text, "source_sha256": _sha(text), "model_name": "tiny.yaml"},
           "protocol": protocol, "mode": "scratch", "validation_enabled": True, "idempotency_key": "carried-1"}
    r = core.post("/core/v1/console/training-runs", json=req, headers=H(core))
    if r.status_code == 422 and r.json()["code"] == "CLASS_COUNT_MISMATCH":
        pytest.skip("synthetic dataset class count differs from TINY nc (fixture shape)")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "model_yaml_text" not in body["source"] and body["source"]["source_sha256"] == _sha(text)
    conn = connect(core.settings, init=False)
    from mbnas_core.db import repo

    cand = repo.get_candidate(conn, body["candidate_id"])
    run = repo.get_run(conn, body["run_id"])
    conn.close()
    assert cand["architecture_path"].startswith(core.settings.output_root) and os.path.isfile(cand["architecture_path"])
    assert "model_yaml_text" not in json.dumps(run["resolved_protocol"])
    assert cand["audit"]["training_source"]["source_sha256"] == _sha(text)


def test_device_used_is_recorded():
    from mbnas_core.orchestration.engine import with_device_used

    ev = {"ap": 0.1, "raw_metrics": {"a": 1}}
    assert with_device_used(ev, {"device_used": "cpu"})["raw_metrics"] == {"a": 1, "device_used": "cpu"}
    assert with_device_used(ev, {}) is ev


def test_principal_cli_server_kind(tmp_roots, capsys, monkeypatch):
    from mbnas_core.ops import principals

    env = tmp_roots["runs"] / "core.env"
    env.write_text(f"MBNAS_CORE_DB_PATH={tmp_roots['runs']}/db/cli.sqlite3\nMBNAS_CORE_OUTPUT_ROOT={tmp_roots['runs']}\n")
    assert principals.main(["--env-file", str(env), "create", "--kind", "server"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["token"].startswith("mbnas_server_") and out["principal"]["kind"] == "server"
    assert "create --kind server" in principals.__doc__

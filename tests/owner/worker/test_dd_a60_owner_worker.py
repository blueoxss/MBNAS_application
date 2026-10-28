"""DD-A60 (2)/(3) and C05 D10 at the owner worker (``mbnas_core.owner_worker``).

(2) Split deployment: the protocol's ``console.paths.output_root`` is a customer-host path the gateway checked on its
host (DD-A46; here ``validate_prep.prepare`` with gateway roots) and the core host cannot create it (here: its parent is
made read-only after the gateway measured it - the T5 ``PermissionError``). The REAL evaluation-free CPU generation
(real job child subprocess, ultralytics + the vendored search space) completes: the stop file, the attempt log and the
generation journal/YAMLs live under the core-local ``MBNAS_CORE_OUTPUT_ROOT/<run>`` and nothing is created under the
customer root. A job whose handling raises (its core-local run directory is a file) is FAILED with WORKER_IO_ERROR and
the same worker goes on with the next job (stub handlers for that row).
(3) An evaluation-ON PROPOSE_GENERATION takes its class count from the gateway dataset summary recorded with the run's
validation: the REAL proposal handler succeeds with the customer's dataset YAML absent on this host; a core-locally
validated run keeps the core-local read as the fallback.
C05 D10: the worker entry point writes its log lines through the redaction filter.
SYNTHETIC datasets; CPU only; no GPU, no network.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import harness
import pytest
from harness import auth

from mbnas_core.db import repo
from mbnas_core.db.core import connect
from mbnas_core.owner_worker.main import Worker, owner_config

WORKER_IO_ERROR = "WORKER_IO_ERROR"  # the job reason code of an OSError while the worker handles a job (DD-A60 (2))

OFF_SEARCH = {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"], "seeds": [7],
              "candidate_count": 2, "max_attempts": 12, "allow_duplicates": False, "arms": ["GA"]}


def _off_policy(output_root: str) -> dict[str, Any]:
    return {"schema_version": "nas-policy/1",
            "console": {"paths": {"output_root": output_root}, "search": copy.deepcopy(OFF_SEARCH)}}


@pytest.fixture
def split(tmp_path, tmp_roots, make_dataset, registry, host_devices, monkeypatch):
    """Core with its own roots (core-host/runs, core-host/data) + the gateway's measurement of customer paths
    (customer dataset in ``tmp_roots["data"]``, customer output root below ``tmp_path/customer``)."""
    from fastapi.testclient import TestClient

    from mbnas_core.api.app import create_app
    from mbnas_server.api import validate_prep

    monkeypatch.delenv("MBNAS_JOB_HANDLERS_MODULE", raising=False)  # real handlers unless a test asks for stubs
    core_runs, core_data = tmp_path / "core-host" / "runs", tmp_path / "core-host" / "data"
    core_runs.mkdir(parents=True)
    core_data.mkdir(parents=True)
    customer = tmp_path / "customer"
    customer.mkdir()
    settings = harness.make_settings(tmp_roots, output_root=str(core_runs), output_roots=[str(core_runs)],
                                     dataset_roots=[str(core_data)], db_path=str(core_runs / "db" / "mbnas.sqlite3"),
                                     log_dir=str(core_runs / "logs"), expose_legacy_api=False, sweeper_enabled=False,
                                     worker_python=sys.executable)
    app = create_app(settings, harness.make_services(settings, registry, host_devices))
    tokens = harness.issue_principals(settings)
    gw = harness.make_gateway_settings(tmp_roots, dataset_roots=[str(tmp_roots["data"])], output_roots=[str(customer)],
                                       manifest_cache_root=str(tmp_path / "gateway-cache"))

    def measure(protocol: dict[str, Any]) -> dict[str, Any]:
        body = validate_prep.prepare(gw, {"protocol": copy.deepcopy(protocol)})
        return {k: body[k] for k in ("path_checks", "dataset_yaml", "dataset_manifests") if k in body}

    with TestClient(app) as core:
        ns = SimpleNamespace(core=core, settings=settings, customer=customer, measure=measure,
                             headers=auth(tokens["server"]), make_dataset=make_dataset, tmp_roots=tmp_roots)
        try:
            yield ns
        finally:
            customer.chmod(0o755)  # tmp cleanup


def _validate(s, protocol: dict[str, Any], inputs: dict[str, Any] | None) -> dict[str, Any]:
    r = s.core.post("/core/v1/console/protocols/validate", json={"protocol": protocol, **(inputs or {})},
                    headers=s.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"], body["errors"]
    return body


def _create(s, v: dict[str, Any], key: str, inputs: dict[str, Any] | None) -> str:
    r = s.core.post("/core/v1/console/runs", json={"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                                                    "validation_id": v["validation_id"], "idempotency_key": key,
                                                    **(inputs or {})}, headers=s.headers)
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def _q(settings, fn):
    conn = connect(settings, init=False)
    try:
        return fn(conn)
    finally:
        conn.close()


@pytest.mark.slow
@pytest.mark.owner_worker
def test_customer_only_output_root_off_generation_completes_with_core_local_files(split):
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    out = split.customer / "out"
    policy = _off_policy(str(out))
    inputs = split.measure(policy)  # the gateway host can create the customer output root ...
    assert inputs["path_checks"]["output_root"]["ok"] is True
    v = _validate(split, policy, inputs)
    assert v["capabilities"]["validation_host"] == "gateway"
    split.customer.chmod(0o555)  # ... the core host cannot (T5: PermissionError on mkdir under the customer root)
    run_id = _create(split, v, "dd-a60-off-customer", inputs)

    worker = Worker(split.settings, owner_config(poll_s=0.05, kill_grace_s=2), worker_id="dd-a60-owner:1")
    try:
        assert worker.tick() is True  # before DD-A60: PermissionError escaped tick() and the worker process died
        assert worker.tick() is False
    finally:
        worker.conn.close()

    run = _q(split.settings, lambda c: repo.get_run(c, run_id))
    jobs = _q(split.settings, lambda c: repo.list_jobs(c, run_id))
    assert [(j["kind"], j["status"]) for j in jobs] == [("PROPOSE_GENERATION", "SUCCEEDED")], [j.get("error") for j in jobs]
    assert run["status"] == "COMPLETED" and run["reason_code"] is None, run
    cands = _q(split.settings, lambda c: repo.list_candidates(c, run_id))
    assert len(cands) == 2 and all(c["audit"]["structure"]["status"] == "PASS" for c in cands)
    assert not out.exists(), "nothing may be created under the customer-only output root on the core host"
    local = Path(split.settings.output_root) / run_id
    assert (local / "control").is_dir()  # stop files: always core-local (MBNAS_CORE_OUTPUT_ROOT/<run>/control)
    journal = json.loads((local / "architectures" / "generation-7.json").read_text())
    assert journal["run_id"] == run_id and len(journal["candidates"]) == 2
    assert all(str(c["architecture_path"]).startswith(str(local / "architectures")) for c in cands)
    logs = list((local / "logs").glob("*.stderr.log"))
    assert logs and "core-local run directory" in logs[0].read_text()
    rows = _q(split.settings, lambda c: [dict(r) for r in c.execute(
        "SELECT payload_json FROM events WHERE run_id = ? AND type = 'log'", (run_id,))])
    assert rows and not any(json.loads(r["payload_json"]).get("reason_code") == WORKER_IO_ERROR for r in rows)


@pytest.mark.owner_worker
def test_a_failing_job_is_failed_with_a_reason_and_the_worker_keeps_running(split, monkeypatch):
    harness.stub_env(monkeypatch)  # stub handlers: this row is about the worker loop, not the generator
    root = str(split.settings.output_root)
    first = _create(split, _validate(split, _off_policy(root), None), "dd-a60-broken", None)
    second = _create(split, _validate(split, _off_policy(root), None), "dd-a60-healthy", None)
    control = Path(root) / first / "control"
    control.parent.mkdir(parents=True, exist_ok=True)
    control.write_text("a file where the control directory should be\n")  # mkdir of <run>/control fails: EEXIST

    worker = Worker(split.settings, owner_config(poll_s=0.05, kill_grace_s=2), worker_id="dd-a60-owner:2")
    try:
        ran = 0
        while worker.tick():
            ran += 1
        assert ran == 2 and worker.jobs_done == 2  # the second job still ran in the same worker
    finally:
        worker.conn.close()

    (bad,) = _q(split.settings, lambda c: repo.list_jobs(c, first))
    assert bad["status"] == "FAILED" and bad["attempt"] == 1
    assert bad["error"]["reason_code"] == WORKER_IO_ERROR, bad["error"]
    assert bad["error"]["outputs"].get("retryable") is False
    assert "FileExistsError (EEXIST)" in bad["error"]["message"] and root not in bad["error"]["message"]  # no path
    assert _q(split.settings, lambda c: repo.get_run(c, first))["status"] == "FAILED"
    (good,) = _q(split.settings, lambda c: repo.list_jobs(c, second))
    assert good["status"] == "SUCCEEDED", good.get("error")
    assert _q(split.settings, lambda c: repo.get_run(c, second))["status"] == "COMPLETED"
    rows = _q(split.settings, lambda c: [dict(r) for r in c.execute(
        "SELECT payload_json FROM events WHERE run_id = ? AND type = 'log'", (first,))])
    assert any(json.loads(r["payload_json"]).get("reason_code") == WORKER_IO_ERROR for r in rows), rows


def _eval_on_protocol(ds: dict[str, str], output_root: str) -> dict[str, Any]:
    proto = harness.small_protocol(ds, output_root, arms=("IndependentRandom",), baselines=(), targets=(1,))
    proto["training"]["requested_input_size"] = [64, 64]  # small search space build on this CPU
    proto["console"]["baseline_override_reason"] = "DD-A60 (3) test: search arm only"
    proto["console"]["search"].update(population=2, generations=1, seeds=[11])
    return proto


def _proposal_spec(s, run_id: str):
    from mbnas_core.owner_worker import child

    (job,) = [j for j in _q(s.settings, lambda c: repo.list_jobs(c, run_id)) if j["kind"] == "PROPOSE_GENERATION"]
    stop = Path(s.settings.output_root) / run_id / "control" / f"{job['id']}.stop"
    spec = child.load_context(s.settings.db_path, job["id"], str(stop), native=True)
    notes: list[str] = []
    return child.owner_params(spec, s.settings.db_path, s.settings, note=notes.append), notes


@pytest.mark.slow
@pytest.mark.owner_worker
def test_evaluation_on_proposal_uses_the_gateway_dataset_summary(split):
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    from mbnas_core.search.handlers import propose_generation

    ds = split.make_dataset("customer", counts={"train": 4, "val": 2, "test": 2}, names=["car", "bus"])
    proto = _eval_on_protocol(ds, str(split.customer / "out"))
    inputs = split.measure(proto)
    v = _validate(split, proto, inputs)
    assert v["capabilities"]["validation_host"] == "gateway"
    run_id = _create(split, v, "dd-a60-eval-on", inputs)
    spec, notes = _proposal_spec(split, run_id)
    assert spec.params["nc"] == 2 and spec.params["dataset_summary"]["source"] == "gateway"
    assert spec.params["dataset_summary"]["validation_id"] == v["validation_id"]
    assert any("gateway dataset summary" in n for n in notes), notes
    local = Path(split.settings.output_root) / run_id / "architectures"
    assert spec.params["architecture_dir"] == str(local)  # the customer run dir is outside the core allowlist
    Path(ds["dataset_yaml"]).unlink()  # the dataset YAML exists on the gateway host only: the core must not read it
    result = propose_generation(spec, settings=split.settings)
    assert result.status.value == "SUCCEEDED", (result.reason_code, result.message)
    assert result.outputs["space"]["nc"] == 2 and len(result.outputs["candidates"]) == 2


@pytest.mark.slow
@pytest.mark.owner_worker
def test_without_a_gateway_summary_the_core_local_read_is_the_fallback(split):
    pytest.importorskip("torch")
    from mbnas_core.search.handlers import propose_generation

    data = Path(split.settings.dataset_roots[0])
    sys.path.append(str(Path(harness.__file__).resolve().parent))
    import datasets as _datasets  # tests/_shared/datasets.py

    ds = _datasets.build_dataset(data, "corelocal", {"train": 4, "val": 2, "test": 2}, "0 0.5 0.5 0.25 0.25\n",
                                 None, 1)
    proto = _eval_on_protocol(ds, str(split.settings.output_root))
    v = _validate(split, proto, None)  # core-local validation (owner all-in-one): no gateway summary is recorded
    assert v["capabilities"].get("validation_host") is None
    run_id = _create(split, v, "dd-a60-eval-on-local", None)
    spec, notes = _proposal_spec(split, run_id)
    assert "nc" not in spec.params and "dataset_summary" not in spec.params
    assert any("fallback" in n for n in notes), notes
    Path(ds["dataset_yaml"]).unlink()  # the fallback really reads the YAML on this host: gone -> DATASET_YAML_INVALID
    result = propose_generation(spec, settings=split.settings)
    assert result.status.value == "FAILED" and result.reason_code == "DATASET_YAML_INVALID"


def test_owner_params_leaves_other_kinds_and_explicit_nc_alone(split, tmp_path):
    from mbnas_contracts.schemas.jobs import JobKind, JobSpec
    from mbnas_core.owner_worker import child

    train = JobSpec(job_id="j1", run_id="run-x", kind=JobKind.CANDIDATE_TRAIN, protocol_hash="sha256:p",
                    candidate_id="c1", rung="L0", cumulative_target=1, fixed_horizon=1,
                    params={"architecture_dir": "/nowhere"})
    assert child.owner_params(train, split.settings.db_path, split.settings) is train
    gen = JobSpec(job_id="j2", run_id="run-x", kind=JobKind.PROPOSE_GENERATION, protocol_hash="sha256:p",
                  params={"nc": 3, "architecture_dir": str(Path(split.settings.output_root) / "run-x" / "architectures"),
                          "protocol": {"console": {"search": {"evaluation_enabled": True}}}})
    same = child.owner_params(gen, split.settings.db_path, split.settings)
    assert same.params == gen.params  # explicit nc kept, a usable architecture_dir kept, no DB read needed


def _worker_env(settings, home: Path, **extra: str) -> dict[str, str]:
    """The subprocess worker's environment: this test's settings, and MBNAS_HOME in the test's tmp dir so no default
    path of the worker ever points into the repository's runtime/ (clean_env removed the operator's MBNAS_*)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_")}
    env.update({"MBNAS_HOME": str(home), "MBNAS_CORE_DB_PATH": settings.db_path,
                "MBNAS_CORE_OUTPUT_ROOT": settings.output_root,
                "MBNAS_CORE_OUTPUT_ROOTS": json.dumps(settings.output_roots),
                "MBNAS_CORE_DATASET_ROOTS": json.dumps(settings.dataset_roots), "CUDA_VISIBLE_DEVICES": "",
                **extra})
    return env


def test_worker_entry_point_log_lines_are_redacted(split, tmp_path):
    """C05 D10: the worker's own stderr lines pass the log_redact filter (a token-shaped value in the worker id)."""
    secret = "SECRET-dd-a60-0123456789"
    home = tmp_path / "mbnas-home"
    p = subprocess.run([sys.executable, "-B", "-m", "mbnas_core.owner_worker", "--once", "--poll-s", "0.05",
                        "--worker-id", f"redaction-probe token={secret}"], env=_worker_env(split.settings, home),
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-2000:]
    assert "owner worker redaction-probe token=*** started" in p.stderr, p.stderr[-2000:]
    assert "stopped after 0 job(s)" in p.stderr
    assert secret not in p.stderr and secret not in p.stdout


def test_configure_logging_installs_the_redaction_filter_on_its_handler():
    import logging

    from mbnas_contracts.util.log_redact import RedactSecretsFilter
    from mbnas_core.owner_worker import main as worker_main

    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    try:
        worker_main.configure_logging()
        worker_main.configure_logging()  # idempotent: one handler
        added = [h for h in root.handlers if h not in before]
        assert len(added) == 1 and isinstance(added[0], worker_main._StderrHandler)
        assert any(isinstance(f, RedactSecretsFilter) for f in added[0].filters)
        record = logging.LogRecord("mbnas_core.owner_worker", logging.INFO, __file__, 1, "request %s",
                                   ("GET /x?access_token=abc Authorization: Bearer def",), None)
        assert all(f.filter(record) for f in added[0].filters)
        text = record.getMessage()
        assert "abc" not in text and "def" not in text and "access_token=***" in text, text
    finally:
        for h in [h for h in root.handlers if h not in before]:
            root.removeHandler(h)
        root.setLevel(level)

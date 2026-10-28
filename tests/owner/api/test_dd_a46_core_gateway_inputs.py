"""DD-A46 (WP-4B, core side): the run-creating console routes accept the gateway's host inputs.

In a split deployment the customer's dataset and ``console.paths.output_root`` exist on the gateway host only. The
gateway measures them (``path_checks`` / ``dataset_yaml`` / ``dataset_manifests``, produced here by the gateway's own
:func:`mbnas_server.api.validate_prep.prepare`) and sends them with ``POST /protocols/validate``; DD-A46 makes
``POST /core/v1/console/runs``, ``/training-runs`` and ``/runs/{id}/final-retrain`` accept the same optional inputs and
re-validate with them instead of core-local path checks, so a gateway-validated protocol is no longer refused with
409 STALE_VALIDATION (evidence/WP7/7B/split_output_root_probe.txt).

"Absent on the core host" is modelled in-process by core allow roots (``dataset_roots`` / ``output_roots``) that do not
contain the protocol's paths, so every core-local check of them fails (PATH_OUTSIDE_ALLOWLIST). The core is called
directly with its server token (no gateway app): this tests the core contract the gateway relies on. SYNTHETIC data;
CPU only; no job is executed.
"""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

import harness
from harness import auth

from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx


@pytest.fixture
def split(tmp_path, tmp_roots, make_dataset, registry, host_devices):
    from fastapi.testclient import TestClient

    from mbnas_core.api.app import create_app
    from mbnas_server.api import validate_prep

    core_runs, core_data = tmp_path / "core-host" / "runs", tmp_path / "core-host" / "data"
    core_runs.mkdir(parents=True)
    core_data.mkdir(parents=True)
    settings = harness.make_settings(tmp_roots, output_root=str(core_runs), output_roots=[str(core_runs)],
                                     dataset_roots=[str(core_data)], db_path=str(core_runs / "db" / "mbnas.sqlite3"),
                                     log_dir=str(core_runs / "logs"), expose_legacy_api=False, sweeper_enabled=False)
    app = create_app(settings, harness.make_services(settings, registry, host_devices))
    tokens = harness.issue_principals(settings)
    gw = harness.make_gateway_settings(tmp_roots, dataset_roots=[str(tmp_roots["data"])],
                                       output_roots=[str(tmp_roots["runs"])],
                                       manifest_cache_root=str(tmp_path / "gateway-cache"))
    ds = make_dataset("customer", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "customer-out"), arms=(), baselines=("yolo11n",),
                                   targets=(1,))

    def measure(protocol):
        body = validate_prep.prepare(gw, {"protocol": copy.deepcopy(protocol)})
        inputs = {k: body[k] for k in ("path_checks", "dataset_yaml", "dataset_manifests") if k in body}
        assert set(inputs) == {"path_checks", "dataset_yaml", "dataset_manifests"}, sorted(inputs)
        return inputs

    with TestClient(app) as core:
        yield SimpleNamespace(core=core, settings=settings, ds=ds, proto=proto, measure=measure,
                              headers=auth(tokens["server"]))


def _validated(s):
    v = s.core.post("/core/v1/console/protocols/validate", json={"protocol": s.proto, **s.measure(s.proto)},
                    headers=s.headers)
    assert v.status_code == 200, v.text
    body = v.json()
    assert body["valid"], body["errors"]
    assert body["capabilities"].get("validation_host") == "gateway"
    return body


def _run_body(body, key):
    return {"protocol": body["resolved"], "protocol_hash": body["protocol_hash"], "idempotency_key": key,
            "validation_id": body["validation_id"]}


def _add_train_image(ds):
    """Change the customer's train split (one more image + label): every manifest hash of it changes."""
    from PIL import Image

    root = Path(ds["root"])
    Image.new("RGB", (32, 24), (1, 2, 3)).save(root / "images" / "train" / "train_new.png")
    (root / "labels" / "train" / "train_new.txt").write_text("0 0.5 0.5 0.25 0.25\n")


def test_core_alone_refuses_the_customer_paths(split):
    """Control: without the gateway's inputs the core checks its own allow roots and the protocol is invalid."""
    r = split.core.post("/core/v1/console/protocols/validate", json={"protocol": split.proto}, headers=split.headers)
    assert r.status_code == 200 and r.json()["valid"] is False
    assert {e["code"] for e in r.json()["errors"]} & {"PATH_OUTSIDE_ALLOWLIST", "PATH_NOT_FOUND"}, r.json()["errors"]


def test_create_run_revalidates_with_the_gateway_inputs(split):
    body = _validated(split)
    resolution = body["resolved"]["resolution"]
    assert all(set(p) == {"requested"} for p in resolution["paths"].values())  # 1.5.0: no realpath recorded
    assert "resolution.paths" in resolution["hash_excluded"]
    without = split.core.post("/core/v1/console/runs", json=_run_body(body, "split-1"), headers=split.headers)
    assert without.status_code == 409 and without.json()["code"] == "STALE_VALIDATION", without.text  # pre-DD-A46
    created = split.core.post("/core/v1/console/runs", json={**_run_body(body, "split-1"), **split.measure(split.proto)},
                              headers=split.headers)
    assert created.status_code == 200, created.text
    out = created.json()
    assert out["created"] is True and out["protocol_hash"] == body["protocol_hash"]
    replay = split.core.post("/core/v1/console/runs", json={**_run_body(body, "split-1"), **split.measure(split.proto)},
                             headers=split.headers)
    assert replay.status_code == 200 and replay.json() == {**out, "created": False}
    # the inputs are measured for THIS request: data changed since validation is still caught
    _add_train_image(split.ds)
    stale = split.core.post("/core/v1/console/runs", json={**_run_body(body, "split-2"), **split.measure(split.proto)},
                            headers=split.headers)
    assert stale.status_code == 409 and stale.json()["code"] == "STALE_VALIDATION", stale.text
    assert any(e["field"] == "dataset.train_manifest_hash" for e in stale.json()["errors"]), stale.json()["errors"]
    bad = split.core.post("/core/v1/console/runs", json={**_run_body(body, "split-3"), "path_checks": "not-an-object"},
                          headers=split.headers)
    assert bad.status_code == 422 and bad.json()["code"] == "VALIDATION_FAILED"


def test_training_run_validates_with_the_gateway_inputs(split):
    proto = copy.deepcopy(split.proto)
    proto["console"]["training"] = {"epoch_target_total": 2, "seed": 13}
    proto["console"]["search"]["evaluation_enabled"] = False
    req = {"source": {"kind": "baseline", "baseline_id": "yolo11n"}, "protocol": proto, "mode": "scratch",
           "validation_enabled": True, "idempotency_key": "split-train-1"}
    without = split.core.post("/core/v1/console/training-runs", json=req, headers=split.headers)
    assert without.status_code == 422, without.text
    assert without.json()["code"] in ("PATH_OUTSIDE_ALLOWLIST", "PATH_NOT_FOUND"), without.text
    r = split.core.post("/core/v1/console/training-runs", json={**req, **split.measure(proto)}, headers=split.headers)
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["created"] is True and created["source"] == {"kind": "baseline", "baseline_id": "yolo11n"}
    conn = connect(split.settings, init=False)
    try:
        run = repo.get_run(conn, created["run_id"])
        assert all(set(p) == {"requested"} for p in run["resolved_protocol"]["resolution"]["paths"].values())
        assert run["resolved_protocol"]["dataset"]["train_manifest_hash"].startswith("sha256:")
    finally:
        conn.close()
    # the selected configuration is a recorded revision (F5d)
    revs = split.core.get(f"/core/v1/console/runs/{created['run_id']}/candidates/{created['candidate_id']}/"
                          "config-revisions", headers=split.headers)
    assert revs.status_code == 200, revs.text
    assert [r["kind"] for r in revs.json()["revisions"]] == ["selected"]


def test_final_retrain_revalidates_the_parent_only_with_gateway_inputs(split):
    body = _validated(split)
    created = split.core.post("/core/v1/console/runs", json={**_run_body(body, "split-parent"),
                                                             **split.measure(split.proto)}, headers=split.headers)
    assert created.status_code == 200, created.text
    parent = created.json()["run_id"]
    conn = connect(split.settings, init=False)
    try:
        with tx(conn):
            repo.update_run_status(conn, parent, "COMPLETED")
    finally:
        conn.close()
    url = f"/core/v1/console/runs/{parent}/final-retrain"
    fr = {"idempotency_key": "fr-1", "budget_gpu_hours": 0.25}
    # fresh inputs: the freshness check passes, the request reaches the finalist selection (none evaluated here)
    r = split.core.post(url, json={**fr, **split.measure(split.proto)}, headers=split.headers)
    assert r.status_code == 409 and r.json()["code"] == "NO_FINALIST", r.text
    # no inputs: unchanged behaviour (the child reuses the parent's frozen protocol; nothing is re-validated)
    r = split.core.post(url, json=fr, headers=split.headers)
    assert r.status_code == 409 and r.json()["code"] == "NO_FINALIST", r.text
    _add_train_image(split.ds)
    r = split.core.post(url, json={**fr, **split.measure(split.proto)}, headers=split.headers)
    assert r.status_code == 409 and r.json()["code"] == "STALE_VALIDATION", r.text
    r = split.core.post(url, json={**fr, "dataset_manifests": {"format": "other"}}, headers=split.headers)
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED", r.text


def test_resume_revalidates_with_the_gateway_inputs(split):
    """Same rule on ``POST /runs/{id}/resume`` (a paused run is re-validated before it continues)."""
    body = _validated(split)
    created = split.core.post("/core/v1/console/runs", json={**_run_body(body, "split-resume"),
                                                             **split.measure(split.proto)}, headers=split.headers)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    conn = connect(split.settings, init=False)
    try:
        with tx(conn):
            repo.update_run_status(conn, run_id, "PAUSED")
    finally:
        conn.close()
    url = f"/core/v1/console/runs/{run_id}/resume"
    blocked = split.core.post(url, json={}, headers=split.headers)  # core-local re-validation fails on the core host
    assert blocked.status_code == 409 and blocked.json()["code"] == "RESUME_BLOCKED", blocked.text
    assert any(e.get("name") == "revalidation" for e in blocked.json()["errors"]), blocked.json()["errors"]
    resumed = split.core.post(url, json=split.measure(split.proto), headers=split.headers)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] in ("RESUMING", "RUNNING", "QUEUED"), resumed.json()["status"]

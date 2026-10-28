"""DD-A62 (c): the evaluation-free generation of a split deployment keeps its journal and YAMLs in the core-local run
directory (DD-A60 (2): ``MBNAS_CORE_OUTPUT_ROOT/<run>/architectures`` because the customer ``console.paths.output_root``
cannot be created on the core host). Asserted here:

* ``engine._recover_generation`` reads that core-local journal (before: only ``run_dir(run)/architectures``, a customer
  path, so a cancelled / crashed / lease-lost generation lost its saved-but-unnotified candidates) - located through the
  core settings of the environment, or through a stored candidate's verified core-local YAML when the settings are not
  exported (owner worker started with ``--env-file``);
* ``engine.generation_progress`` registers the core-local YAMLs as ``architecture`` artifacts (``meta.location =
  core_local``, relpath ``architectures/<file>``) instead of logging ARCHITECTURE_ARTIFACT_UNAVAILABLE; a file that is not
  exactly the candidate's recorded YAML at ``<root>/<run>/architectures/<candidate>.yaml`` is never registered;
* the core download route serves such an artifact (``artifacts.registry.resolve_file``: a ``core_local`` row resolves
  against ``<settings.output_root>/<run>`` only) with the registration guards - a changed, oversized or symlinked file
  or a path that is not ``architectures/<name>.yaml`` is refused;
* the REAL CPU generation through the owner worker (slow) ends with one READY core-local artifact per candidate.
SYNTHETIC datasets; CPU only.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import harness
import pytest
from harness import auth

from mbnas_contracts.schemas.jobs import JobResult
from mbnas_core.artifacts.registry import run_dir
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.orchestration import engine
from mbnas_core.owner_worker.main import Worker, owner_config

UNAVAILABLE = "ARCHITECTURE_ARTIFACT_UNAVAILABLE"
OFF_SEARCH = {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"], "seeds": [7],
              "candidate_count": 2, "max_attempts": 12, "allow_duplicates": False, "arms": ["GA"]}


@pytest.fixture
def split(tmp_path, tmp_roots, make_dataset, registry, host_devices, monkeypatch):
    """Core with its own roots (core-host/runs) + the gateway's measurement of a customer output root (the fixture of
    test_dd_a60_owner_worker.py); ``split.off_run()`` creates an evaluation-free run validated through the gateway and
    then makes the customer root read-only, so the core host cannot create the customer output root (T5)."""
    from fastapi.testclient import TestClient

    from mbnas_core.api.app import create_app
    from mbnas_server.api import validate_prep

    monkeypatch.delenv("MBNAS_JOB_HANDLERS_MODULE", raising=False)
    for key in ("MBNAS_CORE_OUTPUT_ROOT", "MBNAS_CORE_OUTPUT_ROOTS", "MBNAS_CORE_ENV_FILE"):
        monkeypatch.delenv(key, raising=False)
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
    headers = auth(tokens["server"])

    with TestClient(app) as core:
        def off_run(key: str) -> str:
            customer.chmod(0o755)  # the gateway host can create the customer output root ...
            policy = {"schema_version": "nas-policy/1", "console": {"paths": {"output_root": str(customer / "out")},
                                                                     "search": copy.deepcopy(OFF_SEARCH)}}
            body = validate_prep.prepare(gw, {"protocol": copy.deepcopy(policy)})
            inputs = {k: body[k] for k in ("path_checks", "dataset_yaml", "dataset_manifests") if k in body}
            r = core.post("/core/v1/console/protocols/validate", json={"protocol": policy, **inputs}, headers=headers)
            assert r.status_code == 200 and r.json()["valid"], r.text
            v = r.json()
            assert v["capabilities"]["validation_host"] == "gateway"
            customer.chmod(0o555)  # ... the core host cannot (T5: PermissionError on mkdir under the customer root)
            r = core.post("/core/v1/console/runs", json={"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                                                          "validation_id": v["validation_id"], "idempotency_key": key,
                                                          **inputs}, headers=headers)
            assert r.status_code == 200, r.text
            return r.json()["run_id"]

        def q(fn):
            conn = connect(settings, init=False)
            try:
                return fn(conn)
            finally:
                conn.close()

        try:
            yield SimpleNamespace(core=core, settings=settings, customer=customer, core_runs=core_runs, q=q,
                                  off_run=off_run, headers=headers)
        finally:
            customer.chmod(0o755)


def _crafted_generation(run: dict[str, Any], local_arch: Path, n: int = 2) -> list[dict[str, Any]]:
    """A journal ``generation-7.json`` + ``n`` YAML files in ``local_arch``, shaped like the generator's
    (``mbnas_core.search.generation``; SYNTHETIC YAML text, structure PASS)."""
    local_arch.mkdir(parents=True, exist_ok=True)
    cands = []
    for i in range(n):
        cid = f"{run['id']}:RAND:s7:g00:c{i:03d}"
        text = f"# SYNTHETIC generated candidate {i}\nnc: 1\nbackbone: []\nhead: []\n"
        path = local_arch / (cid.replace(":", "_") + ".yaml")
        path.write_text(text)
        digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
        cands.append({"row": {"id": cid, "name": f"Random candidate {i + 1}", "arm": "IndependentRandom",
                              "family": "NAS:yolo11", "generation": 0, "search_seed": 7, "training_seed": None,
                              "architecture_hash": digest, "architecture_yaml": text, "architecture_path": str(path),
                              "parents": [], "parent_evaluation_ids": [], "lineage": {"operation": "random"},
                              "blocks": [], "candidate_status": "BUILD_VALID",
                              "audit": {"origin": "random_generation", "structure": {"status": "PASS"},
                                        "generation": {"index": i, "attempt": i + 1, "seed": 7}}},
                      "genotype": {"synthetic": i}, "yaml_hash": digest})
    state = {"version": 1, "run_id": run["id"], "protocol_hash": run["protocol_hash"], "seed": 7, "attempts": n,
             "duplicates": 0, "failure_counts": {}, "diagnostics": [], "candidates": cands, "rng_state": None}
    (local_arch / "generation-7.json").write_text(json.dumps(state))
    return cands


def _generation_job(conn, run_id: str) -> dict[str, Any]:
    (job,) = [j for j in repo.list_jobs(conn, run_id) if j["kind"] == "PROPOSE_GENERATION"]
    return job


def _artifacts(conn, run_id: str) -> dict[str, dict[str, Any]]:
    return {a["candidate_id"]: a for a in repo.list_artifacts(conn, run_id) if a["kind"] == "architecture"}


def _unavailable_logs(conn, run_id: str) -> list[dict[str, Any]]:
    return [e["payload"] for e in repo.get_events_after(conn, run_id, 0, 10_000)
            if e["type"] == "log" and e["payload"].get("reason_code") == UNAVAILABLE]


def test_recover_generation_reads_the_core_local_journal_and_registers_its_yamls(split, monkeypatch):
    run_id = split.off_run("dd-a62-c-recover")
    local_arch = split.core_runs / run_id / "architectures"
    run = split.q(lambda c: repo.get_run(c, run_id))
    assert not run_dir(run).exists()  # the customer output root was never created on this host
    cands = _crafted_generation(run, local_arch)
    monkeypatch.setenv("MBNAS_CORE_OUTPUT_ROOT", str(split.core_runs))  # the core settings of this host's environment
    conn = connect(split.settings, init=False)
    try:
        job = _generation_job(conn, run_id)
        recovered = engine._recover_generation(engine.context(conn, run_id), job)  # settings source, no DB needed
        assert [c["row"]["id"] for c in recovered.get("candidates", [])] == [c["row"]["id"] for c in cands]
        assert recovered["generation_summary"]["generated_count"] == 2
        # the job ends without having notified any candidate (e.g. cancelled right after the journal save)
        with tx(conn):
            engine.job_finished(conn, job, JobResult(job_id=job["id"], status="CANCELLED", attempt=1,
                                                     reason_code="RUN_CANCELLED"))
        stored = {c["id"]: c for c in repo.list_candidates(conn, run_id)}
        assert set(stored) == {c["row"]["id"] for c in cands}
        arts = _artifacts(conn, run_id)
        assert set(arts) == set(stored) and not _unavailable_logs(conn, run_id)
        for cid, art in arts.items():
            path = Path(stored[cid]["architecture_path"])
            assert art["relpath"] == f"architectures/{path.name}" and art["state"] == "READY"
            assert art["meta"]["location"] == engine.CORE_LOCAL
            assert art["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest() and art["bytes"] == path.stat().st_size
        results = repo.get_run(conn, run_id)["budget"]["generation_results"]
        assert results["7"]["generated_count"] == 2
    finally:
        conn.close()


def test_without_exported_settings_a_stored_candidate_locates_the_journal(split, monkeypatch):
    """An owner worker started with ``--env-file`` does not export MBNAS_CORE_OUTPUT_ROOT: the settings of the environment
    point elsewhere, and the journal is found through the verified core-local YAML of an already stored candidate."""
    run_id = split.off_run("dd-a62-c-envfile")
    run = split.q(lambda c: repo.get_run(c, run_id))
    cands = _crafted_generation(run, split.core_runs / run_id / "architectures", n=3)
    monkeypatch.setenv("MBNAS_CORE_OUTPUT_ROOT", str(split.customer / "elsewhere"))  # wrong for this host on purpose
    conn = connect(split.settings, init=False)
    try:
        job = _generation_job(conn, run_id)
        with tx(conn):  # nothing stored yet and no usable settings: the requeue recovers nothing
            engine.job_requeued(conn, job, paused=True, reason_code="PAUSED")
        assert repo.list_candidates(conn, run_id) == []
        with tx(conn):  # the first candidate was notified (owner worker progress line)
            engine.generation_progress(conn, job, {"generated_candidate": cands[0]})
        with tx(conn):  # now the stored candidate's verified core-local YAML locates the journal
            engine.job_requeued(conn, job, paused=True, reason_code="PAUSED")
        assert {c["id"] for c in repo.list_candidates(conn, run_id)} == {c["row"]["id"] for c in cands}
        assert len(_artifacts(conn, run_id)) == 3 and not _unavailable_logs(conn, run_id)
    finally:
        conn.close()


def test_a_core_local_file_is_registered_only_when_it_is_the_candidate_yaml(split, tmp_path):
    run_id = split.off_run("dd-a62-c-refuse")
    run = split.q(lambda c: repo.get_run(c, run_id))
    local_arch = split.core_runs / run_id / "architectures"
    cands = _crafted_generation(run, local_arch, n=4)
    # 0: bytes differ from the recorded YAML; 1: a symlink; 2: not under <run>/architectures; 3: valid
    Path(cands[0]["row"]["architecture_path"]).write_text("# tampered\n")
    target = tmp_path / "outside.yaml"
    target.write_text(cands[1]["row"]["architecture_yaml"])
    link = Path(cands[1]["row"]["architecture_path"])
    link.unlink()
    link.symlink_to(target)
    other = split.core_runs / "another-dir" / "architectures"
    other.mkdir(parents=True)
    moved = other / Path(cands[2]["row"]["architecture_path"]).name
    moved.write_text(cands[2]["row"]["architecture_yaml"])
    cands[2]["row"]["architecture_path"] = str(moved)
    conn = connect(split.settings, init=False)
    try:
        job = _generation_job(conn, run_id)
        with tx(conn):
            for cand in cands:
                engine.generation_progress(conn, job, {"generated_candidate": cand})
        assert set(_artifacts(conn, run_id)) == {cands[3]["row"]["id"]}
        assert {p["candidate_id"] for p in _unavailable_logs(conn, run_id)} == {c["row"]["id"] for c in cands[:3]}
        assert len(repo.list_candidates(conn, run_id)) == 4  # the candidates themselves stay durable
    finally:
        conn.close()
    for i, cand in enumerate(cands):
        assert (engine.core_local_run_dir(run, cand["row"]) is not None) == (i == 3), i


def test_core_download_route_serves_a_core_local_architecture(split):
    run_id = split.off_run("dd-a62-c-download")
    run = split.q(lambda c: repo.get_run(c, run_id))
    cands = _crafted_generation(run, split.core_runs / run_id / "architectures", n=1)
    conn = connect(split.settings, init=False)
    try:
        with tx(conn):
            engine.generation_progress(conn, _generation_job(conn, run_id), {"generated_candidate": cands[0]})
        (art,) = _artifacts(conn, run_id).values()
    finally:
        conn.close()
    r = split.core.get(f"/core/v1/console/runs/{run_id}/artifacts/{art['id']}", headers=split.headers)
    assert r.status_code == 200, r.text
    assert r.content == cands[0]["row"]["architecture_yaml"].encode() and r.headers["X-Artifact-Sha256"] == art["sha256"]


def test_core_download_route_applies_the_registration_guards(split, tmp_path):
    """A core-local file that changed after registration (bytes, size) is LOAD_FAILED (409, the row flips to
    LOAD_FAILED), a symlink - even one to identical bytes inside the core-local run directory - and a core_local row whose
    path is not ``architectures/<name>.yaml`` are ACCESS_DENIED (403); a vanished file is MISSING (404): it is never looked
    up in ``run_dir(run)`` instead."""
    run_id = split.off_run("dd-a62-c-guards")
    run = split.q(lambda c: repo.get_run(c, run_id))
    local = split.core_runs / run_id
    cands = _crafted_generation(run, local / "architectures", n=5)
    conn = connect(split.settings, init=False)
    try:
        job = _generation_job(conn, run_id)
        with tx(conn):
            for cand in cands:
                engine.generation_progress(conn, job, {"generated_candidate": cand})
            (local / "logs").mkdir()
            (local / "logs" / "attempt.log").write_text("not an architecture\n")
            repo.insert_artifact(conn, id="art-dd-a62-foreign", run_id=run_id, kind="other", relpath="logs/attempt.log",
                                 evaluation_id=None, candidate_id=None,
                                 sha256=hashlib.sha256(b"not an architecture\n").hexdigest(), mime_type="text/plain",
                                 bytes=20, sample_id=None, split_hash=None, state="READY", meta={"location": "core_local"})
        arts = _artifacts(conn, run_id)
    finally:
        conn.close()
    path = [Path(c["row"]["architecture_path"]) for c in cands]
    art = [arts[c["row"]["id"]] for c in cands]

    def get(artifact_id: str):
        return split.core.get(f"/core/v1/console/runs/{run_id}/artifacts/{artifact_id}", headers=split.headers)

    assert get(art[0]["id"]).status_code == 200
    path[1].write_text(cands[1]["row"]["architecture_yaml"] + "# changed after registration\n")
    path[2].write_bytes(b"#" * (1024 * 1024 + 1))
    path[3].unlink()
    path[3].symlink_to(path[0].name)  # relative link inside the same directory, identical bytes to a registered file
    path[4].unlink()
    expected = {art[1]["id"]: (409, "LOAD_FAILED"), art[2]["id"]: (409, "LOAD_FAILED"),
                art[3]["id"]: (403, "ACCESS_DENIED"), "art-dd-a62-foreign": (403, "ACCESS_DENIED"),
                art[4]["id"]: (404, "MISSING")}
    for artifact_id, (status, code) in expected.items():
        r = get(artifact_id)
        assert (r.status_code, r.json()["code"]) == (status, code), (artifact_id, r.text)
    states = split.q(lambda c: {a["id"]: a["state"] for a in repo.list_artifacts(c, run_id)})
    assert states[art[1]["id"]] == states[art[2]["id"]] == "LOAD_FAILED" and states[art[4]["id"]] == "MISSING"
    assert states[art[0]["id"]] == states[art[3]["id"]] == "READY"


@pytest.mark.slow
@pytest.mark.owner_worker
def test_real_split_generation_registers_one_core_local_architecture_per_candidate(split):
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    run_id = split.off_run("dd-a62-c-real")
    worker = Worker(split.settings, owner_config(poll_s=0.05, kill_grace_s=2), worker_id="dd-a62-owner:1")
    try:
        assert worker.tick() is True and worker.tick() is False
    finally:
        worker.conn.close()
    run = split.q(lambda c: repo.get_run(c, run_id))
    assert run["status"] == "COMPLETED", (run["status"], run["reason_code"])
    assert not (split.customer / "out").exists()
    cands = split.q(lambda c: repo.list_candidates(c, run_id))
    arts = split.q(lambda c: _artifacts(c, run_id))
    assert len(cands) == 2 and set(arts) == {c["id"] for c in cands}
    for cand in cands:
        art, path = arts[cand["id"]], Path(cand["architecture_path"])
        assert path.parent == split.core_runs / run_id / "architectures" and os.path.isfile(path)
        assert art["meta"]["location"] == engine.CORE_LOCAL and art["relpath"] == f"architectures/{path.name}"
        assert art["sha256"] == hashlib.sha256(cand["architecture_yaml"].encode()).hexdigest()
    assert not split.q(lambda c: _unavailable_logs(c, run_id))
    revisions = split.q(lambda c: [dict(r) for r in c.execute(
        "SELECT candidate_id, kind, artifact_id FROM config_revisions WHERE run_id = ?", (run_id,))])
    assert {(r["candidate_id"], r["kind"], r["artifact_id"]) for r in revisions} == {
        (cid, "generated", art["id"]) for cid, art in arts.items()}
    snap = split.core.get(f"/core/v1/console/runs/{run_id}/snapshot", headers=split.headers)
    assert snap.status_code == 200, snap.text
    views = {c["id"]: c["artifacts"] for c in snap.json()["generated_candidates"]}
    assert {cid: [(a["kind"], a["state"]) for a in v] for cid, v in views.items()} == {
        cid: [("architecture", "READY")] for cid in arts}

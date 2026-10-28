"""V24: a real worker process is SIGKILLed in the middle of a real evaluation-free generation job (REAL CPU backend:
ultralytics + the ETRI-aware search space; no stub handler, no GPU).

What is asserted
* the candidates already saved in the durable journal survive the crash unchanged (same ids, hashes, audit including
  the id of the worker that generated them) and are committed exactly once;
* the retry (attempt 2, a new worker after lease expiry) continues the saved RNG stream: nothing is sampled again, so
  the final candidate list and attempt count equal an uninterrupted run of the same job;
* the run completes with one PROPOSE_GENERATION job and no evaluation rows.

Timing hook: the generation child sleeps ``MBNAS_TEST_GENERATION_SLEEP_S`` seconds after each durable save (see
``mbnas_server.search.generation``); it only widens the window in which the test kills the worker.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import harness
import pytest
from harness import auth

from mbnas_core.artifacts.registry import run_dir
from mbnas_core.db import repo
from mbnas_core.db.core import connect
from mbnas_contracts.schemas.jobs import JobSpec

pytestmark = pytest.mark.slow
SEED, COUNT = 42, 3


def _env(settings, tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("MBNAS_")}
    env.update({"MBNAS_CORE_DB_PATH": settings.db_path, "MBNAS_CORE_OUTPUT_ROOT": settings.output_root,  # DD-A13
                "MBNAS_CORE_OUTPUT_ROOTS": json.dumps(settings.output_roots),
                "MBNAS_CORE_DATASET_ROOTS": json.dumps(settings.dataset_roots), "MBNAS_CORE_LEASE_TTL_S": "2",
                "MBNAS_CORE_HEARTBEAT_S": "1",
                "MBNAS_CORE_WEIGHTS_DIR": settings.weights_dir, "MBNAS_CORE_WORKER_PYTHON": sys.executable,
                "YOLO_CONFIG_DIR": str(tmp_path / "ultralytics_cfg"), "YOLO_OFFLINE": "1", "YOLO_AUTOINSTALL": "false",
                "CUDA_VISIBLE_DEVICES": ""})
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _spawn(settings, tmp_path, name, worker_id, **extra):
    return subprocess.Popen([sys.executable, "-m", "mbnas_core.owner_worker", "--cpu", "--poll-s", "0.1",
                             "--kill-grace-s", "2", "--worker-id", worker_id],
                            env=_env(settings, tmp_path, **extra), stdout=open(tmp_path / f"{name}.log", "a"),
                            stderr=subprocess.STDOUT)


def _q(settings, fn):
    conn = connect(settings, init=False)
    try:
        return fn(conn)
    finally:
        conn.close()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f"/proc/{pid}/stat") as fh:
        return fh.read().split(")")[-1].split()[0] != "Z"


def _journal(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


@pytest.mark.owner_worker  # A15: PROPOSE_GENERATION runs on the owner worker (DD-A4)
def test_sigkill_mid_generation_recovers_saved_candidates_once_without_resampling(client, api_settings, tmp_roots, tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("ultralytics")
    api_settings.worker_python = sys.executable
    p = {"schema_version": "nas-policy/1", "console": {"paths": {"output_root": str(tmp_roots["runs"] / "out")},
         "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64, "families": ["yolo11n"], "seeds": [SEED],
                    "candidate_count": COUNT, "max_attempts": 12, "allow_duplicates": False, "arms": ["GA"]}}}
    v = client.post("/api/v1/protocols/validate", json={"protocol": p}, headers=auth()).json()
    assert v["valid"], v
    created = client.post("/api/v1/runs", json={"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                          "validation_id": v["validation_id"], "idempotency_key": "v24-gen-crash"}, headers=auth())
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    run = _q(api_settings, lambda c: repo.get_run(c, run_id))
    journal = run_dir(run) / "architectures" / f"generation-{SEED}.json"

    # -- attempt 1: worker w1 is killed while its generation child is sleeping after a durable save ----------------
    first = _spawn(api_settings, tmp_path, "w1", "gen-worker-1", MBNAS_TEST_GENERATION_SLEEP_S=6)
    try:
        harness.wait_for(lambda: (_journal(journal) or {}).get("candidates"), timeout=240, interval=0.05)
        child_pid = harness.wait_for(lambda: _q(api_settings, lambda c: next(
            (w["row"].get("child_pid") for w in repo.list_workers(c) if w["row"].get("child_pid")), None)), timeout=30)
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=10)
        harness.wait_for(lambda: not _alive(child_pid), timeout=15)  # PDEATHSIG ends the orphaned generation child
    finally:
        if first.poll() is None:
            first.kill()
    saved = _journal(journal)
    assert saved is not None and 1 <= len(saved["candidates"]) < COUNT, "kill must land mid-job"
    saved_candidates = json.loads(json.dumps(saved["candidates"]))
    assert {c["row"]["audit"]["generation"]["worker_id"] for c in saved_candidates} == {"gen-worker-1"}
    assert {c["row"]["audit"]["generation"]["execution"] for c in saved_candidates} == {"CPU"}
    assert _q(api_settings, lambda c: repo.get_run(c, run_id))["status"] not in ("COMPLETED", "FAILED")

    # -- attempt 2: a new worker takes over after the lease expires -------------------------------------------------
    second = _spawn(api_settings, tmp_path, "w2", "gen-worker-2")
    try:
        harness.wait_for(lambda: _q(api_settings, lambda c: repo.get_run(c, run_id))["status"] == "COMPLETED", timeout=300)
    finally:
        second.send_signal(signal.SIGTERM)
        second.wait(timeout=30)

    jobs = _q(api_settings, lambda c: repo.list_jobs(c, run_id))
    assert [(j["kind"], j["status"], j["attempt"]) for j in jobs] == [("PROPOSE_GENERATION", "SUCCEEDED", 2)]
    final = _journal(journal)
    assert final["candidates"][:len(saved_candidates)] == saved_candidates, "a saved candidate was regenerated or altered"
    assert len(final["candidates"]) == COUNT
    workers = [c["row"]["audit"]["generation"]["worker_id"] for c in final["candidates"]]
    assert workers[:len(saved_candidates)] == ["gen-worker-1"] * len(saved_candidates)
    assert set(workers[len(saved_candidates):]) == {"gen-worker-2"}

    print(f"V24 evidence: saved_before_kill={len(saved_candidates)} attempts_at_kill={saved['attempts']} "
          f"final_candidates={len(final['candidates'])} final_attempts={final['attempts']} worker_ids={workers}")
    # committed exactly once, in generation order, from the journal
    cands = _q(api_settings, lambda c: repo.list_candidates(c, run_id))
    ids = [c["row"]["id"] for c in final["candidates"]]
    assert sorted(c["id"] for c in cands) == sorted(ids) and len(set(ids)) == COUNT
    assert _q(api_settings, lambda c: c.execute("SELECT COUNT(*) FROM evaluations WHERE run_id=?", (run_id,)).fetchone()[0]) == 0
    assert len([a for a in _q(api_settings, lambda c: repo.list_artifacts(c, run_id)) if a["kind"] == "architecture"]) == COUNT
    summary = _q(api_settings, lambda c: repo.get_run(c, run_id))["budget"]["generation_results"][str(SEED)]
    assert summary["generated_count"] == COUNT and summary["attempts"] == final["attempts"]

    # no re-sampling: an uninterrupted run of the same job yields the same candidates with the same number of draws
    job = jobs[0]
    ref_dir = tmp_roots["runs"] / "out" / "reference_architectures"
    spec = JobSpec.model_validate({**job["spec"], "attempt": 1, "params": {
        **job["spec"]["params"], "protocol": run["resolved_protocol"], "architecture_dir": str(ref_dir)}})
    from mbnas_core.search.generation import generate
    ref = generate(spec, lambda m: None, settings=api_settings)
    assert [c["row"]["architecture_hash"] for c in ref["candidates"]] == [c["row"]["architecture_hash"] for c in final["candidates"]]
    assert [c["row"]["id"] for c in ref["candidates"]] == ids
    assert ref["generation_summary"]["attempts"] == final["attempts"], "the retry drew samples that the journal had recorded"
    assert ref["generation_summary"]["duplicates"] == final["duplicates"]

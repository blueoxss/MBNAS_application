"""DD-A62 (a) explicit training commit and (d) claim filtering for CPU-only runners, over the real core runner routes.

(a) An explicit training run with validation ON on a CPU-only fleet: the CPU runner's result carries the runner's real
NOT_EVALUATED payload (``mbnas_runner.evaluation.handlers.not_evaluated_payload``, DD-A60 (4)); the core commits it as
``SEARCH_COMPLETE`` + ``performance_status`` NOT_EVALUATED + reason EVALUATION_REQUIRES_CUDA with the checkpoint kept and
no metric, and the run is COMPLETED - before DD-A62 it was EVAL_FAILED (METRIC_MISSING:ap) and FAILED MODEL_RUN_FAILED.

(d) Mixed fleet: a runner registered as a CPU device does not lease a job whose result needs a CUDA measurement
(``runner_api.routes.requires_cuda``: evaluation-ON search jobs, explicit training that evaluates, BENCHMARK) while an
ONLINE non-CPU runner offers that kind (``needs_cuda``): the job stays QUEUED for the GPU runner (claim 204) while an
eligible job behind it is leased. On a CPU-only fleet - also once the GPU runner left - the CPU runner leases exactly as
before (and (a) records what it could not evaluate). GPU runners lease as before. Runners: ``runner_sim.RunnerSim``
(SYNTHETIC device identities), device inventory from the runners table (``MBNAS_CORE_DEVICE_SOURCE=runners``).
No GPU, no NVML.
"""
from __future__ import annotations

import copy
from typing import Any

import harness
import pytest
import runner_sim as rs

from mbnas_contracts.schemas.jobs import JobResult
from mbnas_contracts.util.time import utcnow_iso
from mbnas_core.db import repo
from mbnas_core.db.core import connect
from mbnas_core.protocol.validator import devices_from_runners

pytestmark = pytest.mark.runner_protocol

CPU = {"device": "cpu", "device_uuid": None, "device_name": "CPU (CUDA_VISIBLE_DEVICES='')"}
REASON = "EVALUATION_REQUIRES_CUDA"


@pytest.fixture
def fleet(rp_settings, registry, tmp_path, stub):
    """Core app whose validation devices are the registered runners (as in test_dd_a60_cpu_fleet.py);
    ``fleet.sim(runner_id, **device)`` issues a runner principal, registers that runner and returns its RunnerSim."""
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from mbnas_core.api.app import create_app
    from mbnas_core.console.services import AppServices
    from mbnas_core.runner_api import auth

    settings = rp_settings.model_copy(update={"device_source": "runners"})

    def live_devices() -> list[dict[str, Any]]:
        conn = connect(settings, init=False)
        try:
            return devices_from_runners(conn)
        finally:
            conn.close()

    entries = list(registry.values())
    svc = AppServices(settings, registry_fn=lambda: (entries, "synthetic-test-registry", None), devices_fn=live_devices,
                      versions_fn=lambda: {"python": "test", "packages": {}},
                      source_fn=lambda: {"algorithm": "test", "sha256": "sha256:" + "0" * 64, "files": 0})
    rs.write_synthetic_probe(settings.output_root)
    app = create_app(settings, svc)
    rs.runtime_manifest()  # warm (see the runner_protocol conftest)

    with TestClient(app) as core:
        def sim(runner_id: str, **device: Any) -> rs.RunnerSim:
            conn = connect(settings)
            try:
                _row, token = auth.create_principal(conn, "runner", runner_id=runner_id)
            finally:
                conn.close()
            s = rs.RunnerSim(core, token, runner_id, tmp_path / f"runner-{runner_id}")
            s.register(**device)
            return s

        def q(fn):
            conn = connect(settings, init=False)
            try:
                return fn(conn)
            finally:
                conn.close()

        yield SimpleNamespace(core=core, settings=settings, sim=sim, q=q)


def _proto(base_proto, device: str) -> dict[str, Any]:
    proto = copy.deepcopy(base_proto())
    proto["console"]["benchmark"]["device"] = device
    return proto


def _search_run(fleet, base_proto, device: str, key: str) -> str:
    """An evaluation-ON run (one BASELINE_TRAIN job; DD-A62 (d) calls it CUDA-only)."""
    run_id, _ = harness.validate_and_run(fleet.core, _proto(base_proto, device), key=key)
    return run_id


def _training_run(fleet, base_proto, device: str, key: str, *, validation: bool) -> str:
    proto = _proto(base_proto, device)
    proto["console"]["training"] = {"epoch_target_total": 2, "seed": 13}
    r = fleet.core.post("/api/v1/training-runs", headers=harness.auth(), json={
        "source": {"kind": "baseline", "baseline_id": "yolo11n"}, "protocol": proto, "mode": "scratch",
        "validation_enabled": validation, "idempotency_key": key})
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def _job(fleet, run_id: str) -> dict[str, Any]:
    (job,) = fleet.q(lambda c: repo.list_jobs(c, run_id))
    return job


def _queued_untouched(job: dict[str, Any]) -> bool:
    return job["status"] == "QUEUED" and job["attempt"] == 0 and job["lease_owner"] is None


# -- (a) ----------------------------------------------------------------------------------------------------------------
def test_explicit_training_with_validation_on_a_cpu_fleet_completes_not_evaluated(fleet, base_proto):
    from mbnas_runner.evaluation.handlers import latency_device_problem, not_evaluated_payload

    cpu = fleet.sim("cpu-a", **CPU)
    run_id = _training_run(fleet, base_proto, "cpu", "dd-a62-a-train", validation=True)
    lease = cpu.claim()
    assert lease is not None and lease.run_id == run_id  # CPU-only fleet: the CPU runner takes it (DD-A32 mapping)
    cpu.start(lease)
    run = fleet.q(lambda c: repo.get_run(c, run_id))
    full = str(cpu.run_dir(lease) / "candidates" / "model" / "full_e002.pt")  # runner-host paths (never read here)
    ema = str(cpu.run_dir(lease) / "candidates" / "model" / "eval_ema_e002.pt")
    payload = not_evaluated_payload(
        reason=latency_device_problem("cpu"), device="cpu", device_entry="cpu", precision="FP16", eval_batch=8,
        input_shape=[64, 64], benchmark=run["resolved_protocol"]["console"]["benchmark"], split="search_val", epochs=2,
        eval_ckpt=ema, eval_sha256="b" * 64, checkpoint_path=full, checkpoint_sha256="a" * 64)
    result = JobResult(job_id=lease.job_id, status="SUCCEEDED", attempt=lease.attempt, exit_code=0,
                       outputs={"epochs_done": 2, "evaluation": payload, "device_used": "cpu", "device_uuid": None,
                                "rung_result": {"epochs_done": 2, "rung_end_training_ckpt": full,
                                                "rung_end_ema_eval_ckpt": ema}},
                       started_at=lease.started["started_at"], finished_at=utcnow_iso())
    r = cpu.result(lease, cpu.commit_body(lease, result))
    assert r.status_code == 200, r.text
    assert cpu.ack(lease).status_code == 204

    run = fleet.q(lambda c: repo.get_run(c, run_id))
    assert run["status"] == "COMPLETED" and run["reason_code"] is None, (run["status"], run["reason_code"])
    job = _job(fleet, run_id)
    assert job["status"] == "SUCCEEDED"
    (ev,) = fleet.q(lambda c: repo.list_evaluations(c, run_id))
    row = ev["row"]
    assert ev["status"] == "SEARCH_COMPLETE" and not ev["immutable"]
    assert row["performance_status"] == "NOT_EVALUATED" and row["reason_code"] == REASON
    assert [row[k] for k in ("ap", "recall", "latency_ms", "size_mb", "fitness")] == [None] * 5
    assert row["latency_source"] == "pending" and row["split_hash"] is None and row["gate"] is None
    assert row["checkpoint_path"] == full and row["checkpoint_sha256"] == "a" * 64 and row["epochs"] == 2
    assert row["training_mode"] == "scratch" and row["source_kind"] == "baseline"
    raw = row["raw_metrics"]
    assert raw["device_used"] == "cpu" and raw["validation_enabled"] is True and raw["evaluation_status"] == "NOT_EVALUATED"
    assert raw["rung_result"]["rung_end_training_ckpt"] == full
    (cand,) = fleet.q(lambda c: repo.list_candidates(c, run_id))
    assert cand["candidate_status"] == "SEARCH_COMPLETE"
    assert cand["audit"]["orchestration"]["rungs"]["TRAIN"]["state"] == "evaluated"  # DD-5 shape (no failure)
    events = fleet.q(lambda c: repo.get_events_after(c, run_id, 0, 10_000))
    assert not [e for e in events if e["type"] == "candidate.evaluated"]
    # the public view of the row is contract-valid and says what happened
    snap = fleet.core.get(f"/api/v1/runs/{run_id}/snapshot", headers=harness.auth())
    assert snap.status_code == 200, snap.text
    from mbnas_server.api.contract_check import contract_errors

    assert contract_errors("Snapshot", snap.json()) == []
    (public,) = [c for c in snap.json()["candidates"] if c["candidate_id"] == cand["id"]]
    assert public["status"] == "SEARCH_COMPLETE" and public["performance_status"] == "NOT_EVALUATED"
    assert public["reason_code"] == REASON and public["fitness"] is None and public["latency_source"] == "pending"


# -- (d) ----------------------------------------------------------------------------------------------------------------
def test_requires_cuda_and_needs_cuda_rules():
    from mbnas_core.runner_api.routes import needs_cuda, requires_cuda

    search = {"console": {"search": {"evaluation_enabled": True}}}
    off = {"console": {"search": {"evaluation_enabled": False}}}
    explicit = {"console": {"search": {"evaluation_enabled": True},  # what training selection writes for every run
                            "extra": {"training": {"enabled": True, "validation_enabled": True, "mode": "scratch"}}}}
    no_val = copy.deepcopy(explicit)
    no_val["console"]["extra"]["training"]["validation_enabled"] = False
    official = copy.deepcopy(no_val)
    official["console"]["extra"]["training"]["mode"] = "evaluate_only"
    for kind in ("BASELINE_TRAIN", "CANDIDATE_TRAIN", "FINAL_RETRAIN", "EVALUATE"):
        assert requires_cuda(search, kind) is True and requires_cuda({}, kind) is True  # evaluation defaults to ON
        assert requires_cuda(explicit, kind) is True and requires_cuda(official, kind) is True
        assert requires_cuda(off, kind) is False and requires_cuda(no_val, kind) is False  # DD-A32: cuda:0 -> cpu
    assert requires_cuda(off, "BENCHMARK") is True  # a latency measurement
    gpu_kinds = frozenset({"BASELINE_TRAIN", "EVALUATE"})
    assert needs_cuda(search, "BASELINE_TRAIN", gpu_kinds) is True  # mixed fleet: left to the GPU runner
    assert needs_cuda(explicit, "BASELINE_TRAIN", gpu_kinds) is True
    assert needs_cuda(search, "CANDIDATE_TRAIN", gpu_kinds) is False  # no GPU runner offers that kind
    assert needs_cuda(search, "BASELINE_TRAIN", frozenset()) is False  # CPU-only fleet: the CPU runner takes it
    assert needs_cuda(no_val, "BASELINE_TRAIN", gpu_kinds) is False  # nothing to measure


def test_cpu_runner_leaves_evaluation_on_jobs_to_the_gpu_runner_and_leases_what_it_can_run(fleet, base_proto):
    cpu = fleet.sim("cpu-a", **CPU)
    gpu = fleet.sim("gpu-a")  # runner_sim default: SYNTHETIC GPU identity (uuid GPU-sim-gpu-a)
    search_run = _search_run(fleet, base_proto, "GPU-sim-gpu-a", "dd-a62-d-search")
    r = cpu.claim_raw()
    assert r.status_code == 204, r.text  # nothing else queued: no job for the CPU runner
    assert _queued_untouched(_job(fleet, search_run))  # skipped, never leased (no attempt consumed)
    # an explicit training job without validation queued AFTER the search job: the CPU runner skips the older
    # evaluation-ON job and leases this one (same priority, so plain acquire_lease would have handed out the older)
    train_run = _training_run(fleet, base_proto, "cpu", "dd-a62-d-train", validation=False)
    lease = cpu.claim()
    assert lease is not None and lease.run_id == train_run, lease and lease.run_id
    assert _queued_untouched(_job(fleet, search_run))
    # the GPU runner leases the evaluation-ON job as before
    gl = gpu.claim()
    assert gl is not None and gl.run_id == search_run and gl.attempt == 1
    assert _job(fleet, search_run)["lease_owner"] == "gpu-a"


def test_explicit_training_that_evaluates_goes_to_the_gpu_runner_while_one_is_online(fleet, base_proto):
    cpu = fleet.sim("cpu-a", **CPU)
    gpu = fleet.sim("gpu-a")
    run_id = _training_run(fleet, base_proto, "GPU-sim-gpu-a", "dd-a62-d-val", validation=True)
    assert cpu.claim_raw().status_code == 204  # mixed fleet: the evaluation goes to CUDA
    assert _queued_untouched(_job(fleet, run_id))
    assert gpu.deregister().status_code == 204  # the GPU runner leaves: a CPU-only fleet again
    lease = cpu.claim()
    assert lease is not None and lease.run_id == run_id  # DD-A62 (a) then commits its result NOT_EVALUATED


def test_cpu_only_fleet_still_runs_explicit_training_with_validation(fleet, base_proto):
    cpu = fleet.sim("cpu-a", **CPU)
    run_id = _training_run(fleet, base_proto, "cpu", "dd-a62-d-cpu-only", validation=True)
    lease = cpu.claim()
    assert lease is not None and lease.run_id == run_id and lease.kind == "BASELINE_TRAIN"


def test_gpu_runner_claims_are_unchanged(fleet, base_proto):
    """The GPU path is ``repo.acquire_lease`` as before: oldest first, whatever the run needs."""
    gpu = fleet.sim("gpu-a")
    fleet.sim("cpu-a", **CPU)
    first = _training_run(fleet, base_proto, "GPU-sim-gpu-a", "dd-a62-g-1", validation=False)
    second = _search_run(fleet, base_proto, "GPU-sim-gpu-a", "dd-a62-g-2")
    lease = gpu.claim()
    assert lease is not None and lease.run_id == first
    other = fleet.sim("gpu-b", device="sim-dev1", device_uuid="GPU-sim-gpu-b")
    lease2 = other.claim()
    assert lease2 is not None and lease2.run_id == second


def test_cpu_only_fleet_cpu_runner_still_takes_an_evaluation_on_job(fleet, base_proto):
    """No ONLINE non-CPU runner: unchanged lease (``repo.acquire_lease``); the evaluation the CPU runner cannot measure
    is committed NOT_EVALUATED with its reason (DD-A62 (a), see tests/owner/orchestration/test_dd_a62_not_evaluated_search.py).
    A GPU runner that left (OFFLINE) or was never seen online does not make the fleet mixed."""
    cpu = fleet.sim("cpu-a", **CPU)
    gpu = fleet.sim("gpu-a")
    run_id = _search_run(fleet, base_proto, "GPU-sim-gpu-a", "dd-a62-d-cpu-search")
    assert cpu.claim_raw().status_code == 204  # mixed fleet
    assert gpu.deregister().status_code == 204
    from mbnas_core.runner_api import auth

    def stale_principal(conn):  # a principal without a /register call: a REGISTERED runner row, never ONLINE
        from mbnas_core.db.core import tx
        with tx(conn):
            auth.create_principal(conn, "runner", runner_id="gpu-never-seen")
    fleet.q(stale_principal)
    lease = cpu.claim()
    assert lease is not None and lease.run_id == run_id and lease.attempt == 1

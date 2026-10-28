"""T3-01..05, T3-36 (design A3 section 14): claim, kind allowlist, lost claim response, envelope equivalence, unbilled
lease, owner-worker boundary. SYNTHETIC (stub handlers, in-process core app over TestClient)."""
from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import harness
import pytest
import runner_sim as rs

from mbnas_contracts.jobs_protocol import JobFailure
from mbnas_contracts.util.time import utcnow_iso
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.scheduling import jobs

pytestmark = pytest.mark.runner_protocol


def test_two_runners_never_claim_the_same_job(core, rp_settings, base_proto, tokens, tmp_path):
    """T3-01 (test_lease.py:21 over HTTP): 12 runners claim concurrently from 8 queued jobs; one claim response =
    one lease, no job handed to two runners, every lease attempt 1 LEASED; a second claim re-delivers the same job."""
    from mbnas_core.runner_api import auth

    run_id, _ = harness.validate_and_run(core, base_proto())
    conn = connect(rp_settings, init=False)
    try:
        with tx(conn):
            for i in range(7):  # + the protocol's BASELINE_TRAIN = 8 queued runner jobs
                jobs.ensure_job(conn, run_id=run_id, kind="BENCHMARK", protocol_hash="sha256:x", key_extra=f"bench-{i}")
        total = conn.execute("SELECT COUNT(*) FROM jobs WHERE run_id = ? AND status = 'QUEUED'", (run_id,)).fetchone()[0]
        sims = []
        for i in range(12):
            _, tok = auth.create_principal(conn, "runner", runner_id=f"RX{i:02d}")
            sims.append(rs.RunnerSim(core, tok, f"RX{i:02d}", tmp_path / f"rx{i}"))
    finally:
        conn.close()
    assert total == 8
    for s in sims:
        s.register()
    got: dict[str, list[str]] = {}
    barrier = threading.Barrier(len(sims))

    def claim(s: rs.RunnerSim) -> None:
        barrier.wait()
        lease = s.claim()
        got[s.runner_id] = [] if lease is None else [lease.job_id]

    threads = [threading.Thread(target=claim, args=(s,)) for s in sims]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    leased = [j for ids in got.values() for j in ids]
    assert len(leased) == len(set(leased)) == total  # no job twice, every job leased once
    conn = connect(rp_settings, init=False)
    try:
        rows = conn.execute("SELECT id, lease_owner, attempt, status FROM jobs WHERE run_id = ?", (run_id,)).fetchall()
    finally:
        conn.close()
    assert {r["attempt"] for r in rows} == {1} and {r["status"] for r in rows} == {"LEASED"}
    assert {r["id"]: r["lease_owner"] for r in rows} == {j: rid for rid, ids in got.items() for j in ids}
    for s in sims:  # E2(a): the same runner gets its own LEASED job again, nobody gets another runner's job
        again = s.claim()
        assert (again.job_id if again else None) == (got[s.runner_id][0] if got[s.runner_id] else None)


def test_claim_rejects_kind_outside_allowlist(core, runner_sim, search_proto, owner_worker, db):
    """T3-02: a claim naming a kind outside DD-A4 is refused 400 KIND_NOT_ALLOWED (never silently filtered); the
    PROPOSE_GENERATION job is left for the owner worker, whose tick() leases it."""
    run_id, _ = harness.validate_and_run(core, search_proto)
    runner_sim.register()
    before = {j["id"]: (j["status"], j["attempt"]) for j in repo.list_jobs(db, run_id)}
    for kinds in (["PROPOSE_GENERATION"], ["BASELINE_TRAIN", "PROPOSE_GENERATION"]):
        r = runner_sim.claim_raw(kinds)
        assert r.status_code == 400 and r.json()["code"] == "KIND_NOT_ALLOWED", r.text
    assert {j["id"]: (j["status"], j["attempt"]) for j in repo.list_jobs(db, run_id)} == before  # nothing leased
    prop = [j for j in repo.list_jobs(db, run_id) if j["kind"] == "PROPOSE_GENERATION"]
    assert prop and all(j["status"] == "QUEUED" for j in prop)
    assert owner_worker.tick()
    leased = [j for j in repo.list_jobs(db, run_id) if j["kind"] == "PROPOSE_GENERATION" and j["attempt"] >= 1]
    assert leased and all(j["lease_owner"] in (None, owner_worker.id) for j in leased)
    assert any(j["status"] == "SUCCEEDED" for j in leased)


def test_claim_retry_returns_same_leased_job_without_new_attempt(core, runner_sim, base_proto, db):
    """T3-03 (F7): a claim whose response was lost is answered again with the same job and attempt; once the
    attempt runs, a further claim is 429 RUNNER_BUSY (one job per runner)."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    first = runner_sim.claim()
    assert first is not None and first.attempt == 1
    row = repo.get_job(db, first.job_id)
    second = runner_sim.claim()
    assert second is not None and (second.job_id, second.attempt) == (first.job_id, first.attempt)
    again = repo.get_job(db, first.job_id)
    assert again["attempt"] == 1 and again["status"] == "LEASED" and again["lease_owner"] == "R1"
    assert again["lease_expires_at"] == row["lease_expires_at"]  # re-delivery is not a new lease
    runner_sim.start(second)  # the re-delivered ticket is valid for the attempt
    busy = runner_sim.claim_raw()
    assert busy.status_code == 429 and busy.json()["code"] == "RUNNER_BUSY"


def _swap(value, old: str, new: str):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, dict):
        return {k: _swap(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [_swap(v, old, new) for v in value]
    return value


def _give_real_genotypes(db, run_id: str, config: dict) -> None:
    """SYNTHETIC DB state: the stub proposer's candidates carry '# stub' YAML that neither side can build into an
    ArchSpec (both raise ARCHITECTURE_MISSING); every second candidate gets a real yolo11 genotype so the native
    search-candidate path (core search_arch vs runner search_arch) is compared on content too."""
    with tx(db):
        for i, row in enumerate(db.execute("SELECT id, audit_json FROM candidates WHERE run_id = ? AND arm != 'Baseline' "
                                           "ORDER BY id", (run_id,)).fetchall()):
            audit = json.loads(row["audit_json"] or "{}")
            if i % 2 or audit.get("proposal", {}).get("genotype", {}).get("yaml"):
                continue
            audit.setdefault("proposal", {})["genotype"] = {"yaml": config, "family": "yolo11"}
            db.execute("UPDATE candidates SET audit_json = ? WHERE id = ?", (json.dumps(audit), row["id"]))


def _core_params(settings, job_id: str):
    from mbnas_core.owner_worker import child

    try:
        return "ok", child.load_context(settings.db_path, job_id, "/STOP", native=True, settings=settings).params
    except JobFailure as fail:
        return "fail", fail.reason_code


def _runner_params(sim: rs.RunnerSim, lease: rs.Lease):
    try:
        return "ok", sim.spec(lease, native=True).params
    except JobFailure as fail:
        return "fail", fail.reason_code


def test_envelope_native_params_equal_child_load_context(core, rp_settings, runner_sim, search_proto, owner_worker,
                                                          db):
    """T3-04: for every runner job of a search run (baseline + GA/Random candidates over two rungs, the second rung
    resuming from runner-registered checkpoints) the runner's native params from the envelope equal the owner child's
    ``load_context(native=True)`` params from the same DB state, after substituting run_dir and the stop path."""
    import ultralytics
    import yaml

    run_id, _ = harness.validate_and_run(core, search_proto)
    runner_sim.register()
    compared: list[tuple[str, str, str]] = []
    resumed = 0
    real_yaml = yaml.safe_load((Path(ultralytics.__file__).parent / "cfg/models/11/yolo11.yaml").read_text())
    real_yaml["nc"] = 1
    for _ in range(200):
        harness.drain(owner_worker)
        _give_real_genotypes(db, run_id, real_yaml)
        lease = runner_sim.claim()
        if lease is None:
            break
        core_side = _core_params(rp_settings, lease.job_id)
        run_side = _runner_params(runner_sim, lease)
        core_base = str(Path(search_proto["console"]["paths"]["output_root"]) / run_id)
        assert core_side[0] == run_side[0], (lease.kind, core_side, run_side)
        if core_side[0] == "ok":
            expected = _swap(_swap(core_side[1], "/STOP", str(runner_sim.stop_path(lease))),
                             core_base, str(runner_sim.run_dir(lease)))
            assert json.loads(json.dumps(run_side[1])) == json.loads(json.dumps(expected)), lease.kind
            resumed += bool(run_side[1].get("resume_from"))
        else:
            assert core_side[1] == run_side[1]
        compared.append((lease.kind, lease.job_id, core_side[0]))
        out = runner_sim.run_attempt(lease)
        assert out["response"].status_code == 200, out["response"].text
    kinds_ok = {k for k, _, status in compared if status == "ok"}
    assert {"BASELINE_TRAIN", "CANDIDATE_TRAIN"} <= {k for k, _, _ in compared}
    assert {"BASELINE_TRAIN", "CANDIDATE_TRAIN"} <= kinds_ok, compared  # both paths compared field by field
    assert repo.get_run(db, run_id)["status"] in ("COMPLETED", "RUNNING", "FAILED")
    print(f"T3-04 compared={len(compared)} ok_kinds={sorted(kinds_ok)} resumed={resumed} jobs={compared}")


def test_lease_without_start_is_not_billed_after_sweep(core, runner_sim, base_proto, sweeper, db):
    """T3-05 (test_lease.py:86 over HTTP): a claimed but never started attempt is requeued by the sweeper without any
    ledger row; its stale ticket can no longer start it."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    lease = runner_sim.claim()
    assert sweeper(after_s=3600)["reclaimed"] == [lease.job_id]
    assert repo.get_job(db, lease.job_id)["status"] == "QUEUED"
    assert repo.list_costs(db, run_id) == [] and "failed_gpu_seconds" not in repo.get_run(db, run_id)["costs"]
    late = runner_sim.start_raw(lease)
    assert late.status_code == 409 and late.json()["code"] == "LEASE_LOST"
    assert repo.list_costs(db, run_id) == []


def test_owner_worker_leases_propose_generation_and_runner_never_sees_it(core, rp_settings, runner_sim, make_dataset,
                                                                       tmp_roots, stub, db):
    """T3-36: with the CLI's default owner configuration the owner worker leases PROPOSE_GENERATION and nothing else;
    a runner's claim never returns a PROPOSE_GENERATION job (204 while only proposals are queued)."""
    from mbnas_core.owner_worker.main import OWNER_KINDS, owner_config

    ds = make_dataset("gen", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("GA",), baselines=())
    run_id, _ = harness.validate_and_run(core, proto)
    cfg = owner_config(poll_s=0.01)
    assert cfg.kinds == OWNER_KINDS == ["PROPOSE_GENERATION"] and cfg.lease_generation_only() is None
    owner = harness.make_inline_worker(rp_settings, "owner-default:1")
    owner.cfg = cfg
    try:
        runner_sim.register()
        queued = [j["kind"] for j in repo.list_jobs(db, run_id) if j["status"] == "QUEUED"]
        assert queued and set(queued) == {"PROPOSE_GENERATION"}
        assert runner_sim.claim_raw().status_code == 204
        assert owner.tick()  # leases + runs the proposal
        assert {j["kind"] for j in repo.list_jobs(db, run_id) if j["lease_owner"] == owner.id or
                j["kind"] == "PROPOSE_GENERATION"} == {"PROPOSE_GENERATION"}
        train = [j for j in repo.list_jobs(db, run_id) if j["kind"] == "CANDIDATE_TRAIN" and j["status"] == "QUEUED"]
        assert train, "the proposal queued no training job"
        assert not owner.tick(), "the owner worker leased a runner kind"
        assert all(j["status"] == "QUEUED" for j in repo.list_jobs(db, run_id) if j["kind"] == "CANDIDATE_TRAIN")
        lease = runner_sim.claim()
        assert lease is not None and lease.kind == "CANDIDATE_TRAIN"
    finally:
        owner.conn.close()


def test_owner_mode_wait_gpu_clear_never_calls_nvml(rp_settings):
    """Owner side of the moved S7-F1 wait (WP-2A request): the owner worker no longer imports mbnas_runner.gpu and its
    wait_gpu_clear() never queries NVML (the runner daemon owns the wait, tests/public/runner)."""
    import mbnas_core.owner_worker.main as owner_main
    from mbnas_core.owner_worker.main import Worker, owner_config

    tree = ast.parse(Path(owner_main.__file__).read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
               {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m.startswith("mbnas_runner") for m in imported), imported
    w = Worker(rp_settings, owner_config(), worker_id="owner-nvml:1")
    try:
        def boom(*_a, **_k):
            raise AssertionError("NVML queried by the owner worker")

        w.gpu_pids_fn = boom
        out = w.wait_gpu_clear({"id": "job-x", "run_id": "run-x", "spec": {"params": {}}})
        assert out == {"cleared": True, "waited_s": 0.0, "foreign": [], "nvml": False}
    finally:
        w.conn.close()

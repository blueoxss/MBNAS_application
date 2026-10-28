"""Interface check against the REAL handler modules (CPU only, no training, no GPU): the real search handler runs a
PROPOSE_GENERATION job in-process; the child's native parameter translation must validate under the real
training/evaluation parameter models, and a search candidate's ArchSpec must build on CPU."""
from __future__ import annotations

import harness
import pytest

from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx
from mbnas_core.owner_worker import child

pytestmark = [pytest.mark.slow, pytest.mark.owner_worker]  # A15: real PROPOSE_GENERATION on the owner worker
pytest.importorskip("torch", reason="the real search handler builds ultralytics models (torch is not installed here)")  # 2026-10-15
training_handlers = pytest.importorskip("mbnas_runner.training.handlers")
evaluation_handlers = pytest.importorskip("mbnas_runner.evaluation.handlers")


@pytest.fixture
def real_worker(api_settings, monkeypatch):
    from mbnas_core.owner_worker.main import child_settings_env

    monkeypatch.delenv("MBNAS_JOB_HANDLERS_MODULE", raising=False)
    for key, value in child_settings_env(api_settings).items():  # what a spawned child would see
        monkeypatch.setenv(key, value)
    w = harness.make_inline_worker(api_settings, worker_id="inline-real:1")
    w.cfg.kinds = ["PROPOSE_GENERATION"]
    yield w
    w.conn.close()


def test_real_proposals_translate_to_native_training_params(client, make_dataset, tmp_roots, real_worker, api_settings,
                                                            registry, monkeypatch):
    import mbnas_core.registry as core_registry

    # DD-A16: the owner child resolves the baseline ArchSpec from an AVAILABLE registry entry (synthetic registry)
    monkeypatch.setattr(core_registry, "load_registry", lambda output_root=None: registry)
    ds = make_dataset("n", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("GA",), generations=1)
    run_id, _ = harness.validate_and_run(client, proto)
    assert harness.drain(real_worker) == 1  # the real PROPOSE_GENERATION handler, in-process
    conn = connect(api_settings, init=False)
    prop = next(j for j in repo.list_jobs(conn, run_id) if j["kind"] == "PROPOSE_GENERATION")
    err = prop.get("error") or {}
    assert prop["status"] == "SUCCEEDED", (err.get("reason_code"), str(err.get("message"))[:600])
    cands = [c for c in repo.list_candidates(conn, run_id) if c["arm"] == "GA"]
    assert len(cands) == 2 and all(c["audit"]["proposal"]["genotype"] for c in cands)
    jobs = {j["candidate_id"]: j for j in repo.list_jobs(conn, run_id) if j["kind"] in ("CANDIDATE_TRAIN", "BASELINE_TRAIN")}
    assert set(jobs) == {c["id"] for c in cands} | {f"{run_id}.base.yolo11n"}
    for cid, job in jobs.items():
        spec = child.load_context(api_settings.db_path, job["id"], "/tmp/stop", native=True, settings=api_settings)
        params = training_handlers.TrainJobParams.for_job(spec)
        assert params.run_dir.endswith(run_id) and params.stop_request_path == "/tmp/stop"
        if job["kind"] == "BASELINE_TRAIN":
            from mbnas_core.registry import arch_spec  # DD-A16: was `params.arch is None`

            assert params.adapter_id == "yolo11n"
            assert params.arch == arch_spec("yolo11n", api_settings.weights_dir, registry["yolo11n"]).as_dict()
        else:
            from mbnas_model_runtime.adapters.base import ArchSpec
            from mbnas_model_runtime.adapters.ultralytics_adapter import UltralyticsAdapter

            arch = ArchSpec(**params.arch)
            report = UltralyticsAdapter(arch.family, arch.variant).build(arch, 1, "scratch", 0)  # CPU build
            assert report is not None
    # EVALUATE translation needs an eval_ema checkpoint row
    cid = cands[0]["id"]
    with tx(conn):
        repo.insert_checkpoint(conn, id="ck-test", run_id=run_id, candidate_id=cid, epoch=1, kind="eval_ema",
                               path="/tmp/eval.pt", sha256="0" * 64, bytes=1, rung="L0")
        from mbnas_core.scheduling import jobs as jobmod

        ev_job, _ = jobmod.ensure_job(conn, run_id=run_id, kind="EVALUATE", protocol_hash=jobs[cid]["spec"]["protocol_hash"],
                                      candidate_id=cid, rung="L0", params={"candidate": {"id": cid}})
    spec = child.load_context(api_settings.db_path, ev_job["id"], None, native=True)
    eparams = evaluation_handlers.EvalJobParams.model_validate(spec.params)
    assert eparams.eval_ckpt == "/tmp/eval.pt" and eparams.arch["model_id"] == cid
    conn.close()


def test_native_training_params_carry_the_expected_resume_source(client, make_dataset, tmp_roots, real_worker,
                                                                 api_settings):
    """S5-F1: the real handler gets the latest non-PRUNED training_full row and the stage's epochs_added."""
    ds = make_dataset("r", counts={"train": 4, "val": 2, "test": 2})
    proto = harness.small_protocol(ds, str(tmp_roots["runs"] / "out"), arms=("GA",), generations=1)
    run_id, _ = harness.validate_and_run(client, proto)
    assert harness.drain(real_worker) == 1
    conn = connect(api_settings, init=False)
    job = next(j for j in repo.list_jobs(conn, run_id) if j["kind"] == "CANDIDATE_TRAIN")
    cid = job["candidate_id"]
    params = training_handlers.TrainJobParams.for_job(
        child.load_context(api_settings.db_path, job["id"], None, native=True))
    assert params.resume_from is None  # first stage, nothing recorded: scratch is the trial
    assert params.stage_epochs_added == job["spec"]["params"]["stage"]["epochs_added"] == job["spec"]["cumulative_target"]
    with tx(conn):
        for epoch in (1, 2):
            repo.insert_checkpoint(conn, id=f"ck-full-{epoch}", run_id=run_id, candidate_id=cid, epoch=epoch,
                                   kind="training_full", path=f"/tmp/full_e000{epoch}.pt", sha256="0" * 64, bytes=1,
                                   rung="L0")
        repo.mark_checkpoint_pruned(conn, "ck-full-2", prune={"policy": "test"})
    params = training_handlers.TrainJobParams.for_job(
        child.load_context(api_settings.db_path, job["id"], None, native=True))
    assert params.resume_from is not None and params.resume_from["id"] == "ck-full-1"  # PRUNED row skipped
    assert params.resume_from["epoch"] == 1 and params.resume_from["path"] == "/tmp/full_e0001.pt"
    conn.close()

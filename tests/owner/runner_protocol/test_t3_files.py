"""T3-23..27 (design A3 sections 6.3, 9.3, 14): checkpoint digests recomputed by the runner daemon, core-side checkpoint
format checks, pre-registered artifacts and execution configuration consumed without core file access, progress
batches. SYNTHETIC (stub handlers; the real runner daemon code where the runner decides)."""
from __future__ import annotations

import hashlib

import harness
import pytest
import runner_sim as rs

from mbnas_contracts.util.time import utcnow_iso
from mbnas_core.db import repo

pytestmark = pytest.mark.runner_protocol


def _errors(db, run_id):
    return [e["payload"] for e in repo.get_events_after(db, run_id, 0, 5000)
            if e["type"] == "log" and e["payload"].get("level") == "error"]


def _report(path, **over):
    data = path.read_bytes()
    body = {"kind": "training_full", "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "epoch": 1, "rung": "L0", "hashes": {"protocol": "p"}}
    body.update(over)
    return body


def test_runner_recomputes_checkpoint_sha_and_rejects_tampered_file(core, daemon_factory, base_proto, db):
    """T3-23 / F17 (test_worker_integrity.py:46 on the runner daemon): the daemon recomputes the file sha256; a
    tampered file, or a report without digest, is not registered and reaches the run as a CHECKPOINT_REPORT_REJECTED
    error log through the next progress batch; an upper-case ``sha256:``-prefixed digest is normalised."""
    from mbnas_runner.client import JobFence
    from mbnas_runner.daemon import _Live
    from mbnas_runner.envelope import run_dir_for
    from mbnas_runner.journal import AttemptJournal

    run_id, _ = harness.validate_and_run(core, base_proto())
    d = daemon_factory()
    d.register()
    claim = d.client.claim(d.kinds)
    fence = JobFence.from_claim(claim)
    j = AttemptJournal.create(d.settings.work_root, fence.job_id, fence.attempt)
    j.record_claim(claim)
    assert d._start(j, fence) is not None
    env = claim["envelope"]
    base = run_dir_for(d.settings.work_root, run_id)
    live = _Live()
    good = base / "candidates" / "ck" / "full_e0001.pt"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"A" * 4096)
    d._checkpoint(j, fence, env, _report(good), live, base)
    rows = repo.list_checkpoints(db, run_id)
    assert len(rows) == 1 and rows[0]["sha256"] == hashlib.sha256(b"A" * 4096).hexdigest()
    assert _errors(db, run_id) == []

    # Same size, different bytes: the child reported the digest of the original content.
    tampered = base / "candidates" / "ck" / "full_e0002.pt"
    tampered.write_bytes(b"B" * 4096)
    report = _report(tampered, epoch=2)
    tampered.write_bytes(b"C" * 4096)
    d._checkpoint(j, fence, env, report, live, base)
    d._flush_progress(j, fence, live)
    assert [r["epoch"] for r in repo.list_checkpoints(db, run_id)] == [1], "tampered file was registered"
    errs = _errors(db, run_id)
    assert len(errs) == 1 and errs[0]["reason_code"] == "CHECKPOINT_REPORT_REJECTED"
    assert "sha256 mismatch" in errs[0]["message"]
    assert errs[0]["reported_sha256"] == report["sha256"]
    assert errs[0]["computed_sha256"] == hashlib.sha256(b"C" * 4096).hexdigest()

    # A report without any digest cannot be verified either, and an uppercase / prefixed digest is normalised.
    d._checkpoint(j, fence, env, _report(good, epoch=3, sha256=None), live, base)
    d._checkpoint(j, fence, env, _report(good, epoch=4, sha256="sha256:" + hashlib.sha256(b"A" * 4096).hexdigest().upper()),
                  live, base)
    d._flush_progress(j, fence, live)
    assert [r["epoch"] for r in repo.list_checkpoints(db, run_id)] == [1, 4]
    assert len(_errors(db, run_id)) == 2


def test_core_rejects_checkpoint_without_digest_wrong_kind_or_stale_attempt(core, runner_sim, base_proto, sweeper, db):
    """T3-24: the core answers 422 CHECKPOINT_REPORT_REJECTED for a report without a digest, a non-64-hex digest, a
    kind outside the four checkpoint kinds, bytes < 0 or a path-like relpath; 409 for a stale attempt; a resent report
    (same id, same digest) is answered again and the same id with another digest is 409. No row is added by any refusal."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    lease = runner_sim.claim()
    runner_sim.start(lease)
    cid = repo.get_job(db, lease.job_id)["candidate_id"]
    ok = {"checkpoint_id": "ck-t324-ok", "candidate_id": cid, "kind": "training_full", "epoch": 1,
          "relpath": f"candidates/{cid}/full_e001.json", "uri": "/runner/x/full_e001.json", "sha256": "a" * 64,
          "bytes": 10, "hashes": {}}
    bad = [{k: v for k, v in ok.items() if k != "sha256"}, {**ok, "sha256": "zz"}, {**ok, "sha256": "a" * 63},
           {**ok, "kind": "bogus"}, {**ok, "bytes": -1}, {**ok, "relpath": "/abs/full.json"},
           {**ok, "relpath": "../escape.json"}]
    for body in bad:
        r = runner_sim.checkpoint(lease, {**body, "checkpoint_id": "ck-t324-bad"})
        assert r.status_code == 422 and r.json()["code"] == "CHECKPOINT_REPORT_REJECTED", (body, r.text)
    assert repo.list_checkpoints(db, run_id) == []
    r = runner_sim.checkpoint(lease, ok)
    assert r.status_code == 200 and r.json()["state"] == "PRESENT", r.text
    again = runner_sim.checkpoint(lease, ok)
    assert again.status_code == 200 and again.json() == r.json()
    other = runner_sim.checkpoint(lease, {**ok, "sha256": "b" * 64})
    assert other.status_code == 409 and other.json()["code"] == "CHECKPOINT_CONFLICT"
    assert [c["id"] for c in repo.list_checkpoints(db, run_id)] == ["ck-t324-ok"]
    loc = db.execute("SELECT * FROM artifact_locations WHERE object_id = 'ck-t324-ok'").fetchall()
    assert len(loc) == 1 and loc[0]["store"] == "runner_local" and loc[0]["verified_by"] == "runner:R1"
    assert loc[0]["uri"] == "/runner/x/full_e001.json" and loc[0]["attempt"] == 1
    sweeper(after_s=3600)
    stale = runner_sim.checkpoint(lease, {**ok, "checkpoint_id": "ck-t324-stale"})
    assert stale.status_code == 409 and stale.json()["code"] == "LEASE_LOST"
    assert [c["id"] for c in repo.list_checkpoints(db, run_id)] == ["ck-t324-ok"]


def test_result_artifacts_resolve_to_preregistered_rows_without_hashing(core, runner_sim, base_proto, db, monkeypatch):
    """T3-25 / R3: evaluation artifacts pre-registered by the runner (E7) are linked to the committed evaluation by
    their artifact_id; the core never hashes a file while committing (sha256_file call count 0)."""
    from mbnas_core.artifacts import registry
    from mbnas_core.orchestration import state

    calls: list = []
    for mod in (registry, state):
        monkeypatch.setattr(mod, "sha256_file", lambda *a, **k: calls.append(a) or "0" * 64)
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    lease = runner_sim.claim()
    out = runner_sim.run_attempt(lease)
    assert out["response"].status_code == 200, out["response"].text
    assert calls == [], calls
    (ev,) = [e for e in repo.list_evaluations(db, run_id) if e["status"] == "EVALUATED"]
    linked = sorted(a["id"] for a in repo.list_artifacts(db, run_id) if a.get("evaluation_id") == ev["id"])
    assert out["artifact_ids"] and linked == sorted(out["artifact_ids"])
    rows = {a["id"]: a for a in repo.list_artifacts(db, run_id)}
    for entry in out["result"].outputs["evaluation"]["artifacts"]:
        row = rows[entry["artifact_id"]]
        assert row["kind"] == entry["kind"] and row["state"] == "READY" and len(row["sha256"].removeprefix("sha256:")) == 64
    assert not [p for p in ev["row"].get("problems") or [] if "artifact" in str(p)]


def test_execution_configuration_report_updates_candidate_without_core_file_access(core, daemon_factory, base_proto,
                                                                                  db, monkeypatch):
    """T3-26 / R4: the runner daemon reports the executed architecture.yaml (pre-registered as an ``architecture``
    artifact); the core updates candidates.architecture_yaml and audit.execution_configuration from the report without
    reading or hashing any file (the file-based recorders and sha256_file are never called)."""
    from mbnas_core.artifacts import registry
    from mbnas_core.orchestration import model_artifacts

    monkeypatch.setenv("MBNAS_JOB_HANDLERS_MODULE", "remote_handlers")
    calls: list = []

    def forbidden(name):
        def fn(*a, **k):
            calls.append(name)
            raise AssertionError(f"core file access: {name}")
        return fn

    monkeypatch.setattr(model_artifacts, "record_model_configuration", forbidden("record_model_configuration"))
    monkeypatch.setattr(model_artifacts, "record_native_images", forbidden("record_native_images"))
    monkeypatch.setattr(registry, "sha256_file", forbidden("sha256_file"))
    run_id, _ = harness.validate_and_run(core, base_proto())
    d = daemon_factory()
    assert d.run(once=True) == 0
    job = repo.list_jobs(db, run_id)[0]
    assert job["status"] == "SUCCEEDED", job.get("error")
    assert calls == []
    cand = repo.get_candidate(db, job["candidate_id"])
    yaml_path = __import__("pathlib").Path(d.settings.work_root) / "runs" / run_id / "candidates" / cand["id"] / \
        "architecture.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    assert cand["architecture_yaml"] == text
    ec = cand["audit"]["execution_configuration"]
    assert ec["sha256"] == hashlib.sha256(text.encode()).hexdigest() and ec["class_count"] == 1
    art = repo.get_artifact(db, run_id, ec["artifact_id"])
    assert art["kind"] == "architecture" and art["relpath"] == ec["path"] == f"candidates/{cand['id']}/architecture.yaml"
    assert job["result"]["outputs"]["execution_configuration"]["artifact_id"] == ec["artifact_id"]
    assert "runner_artifact_problems" not in job["result"]["outputs"]


def test_progress_batch_touches_job_and_emits_one_candidate_updated_per_batch(core, runner_sim, base_proto, db):
    """T3-27: one progress batch (several messages) refreshes jobs.heartbeat_at and emits exactly one
    candidate.updated and one worker.updated event; progress before start is refused (409)."""
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    lease = runner_sim.claim()
    early = runner_sim.progress(lease, [{"type": "progress", "job_id": lease.job_id, "ts": utcnow_iso(), "epoch": 1,
                                         "payload": {}}])
    assert early.status_code == 409 and early.json()["code"] == "LEASE_LOST"
    runner_sim.start(lease)
    before = repo.get_job(db, lease.job_id)["heartbeat_at"]
    seq0 = repo.get_run(db, run_id)["last_seq"]
    msgs = [{"type": "progress", "job_id": lease.job_id, "ts": utcnow_iso(), "epoch": e, "epoch_target_total": 3,
             "global_step": 4 * e, "payload": {"loss": 1.0 / e}} for e in (1, 2, 3)]
    r = runner_sim.progress(lease, msgs)
    assert r.status_code == 200 and r.json() == {"accepted_seq": 3}
    after = repo.get_job(db, lease.job_id)["heartbeat_at"]
    assert after and (before is None or after > before)
    new = [e for e in repo.get_events_after(db, run_id, seq0, 1000)]
    assert [e["type"] for e in new].count("candidate.updated") == 1
    assert [e["type"] for e in new].count("worker.updated") == 1
    cand_ev = next(e for e in new if e["type"] == "candidate.updated")
    assert "3" in str(cand_ev["payload"])  # the newest epoch of the batch reached the candidate

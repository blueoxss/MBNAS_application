"""WP-S3: worker-side integrity and OFF-path execution invariants (synthetic runs, no GPU work).

* DD-8 / G11: ``Worker._checkpoint`` recomputes the file sha256 and refuses a report whose digest does not match.
* DD-6 / G16: a ``generation_only`` job child always runs with ``CUDA_VISIBLE_DEVICES=""`` whatever the worker flavour.
"""
from __future__ import annotations

import hashlib
import os

import harness
import pytest

from mbnas_core.artifacts.registry import run_dir
from mbnas_core.db import repo
from mbnas_core.db.core import connect
from mbnas_core.owner_worker import main as worker_main
from mbnas_core.owner_worker.main import Worker, WorkerConfig


@pytest.fixture
def run_and_job(client, protocol, api_settings):
    run_id, _ = harness.validate_and_run(client, protocol)
    conn = connect(api_settings, init=False)
    try:
        job = next(j for j in repo.list_jobs(conn, run_id) if j.get("candidate_id"))
        run = repo.get_run(conn, run_id)
    finally:
        conn.close()
    return run, job


def _report(path, **over):
    data = path.read_bytes()
    body = {"kind": "training_full", "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "epoch": 1, "rung": "L0", "hashes": {"protocol": "p"}}
    body.update(over)
    return body


def _errors(conn, run_id):
    return [e["payload"] for e in repo.get_events_after(conn, run_id, 0, 500)
            if e["type"] == "log" and e["payload"].get("level") == "error"]


def test_checkpoint_sha_is_recomputed_and_tampered_file_is_rejected(api_settings, run_and_job):
    run, job = run_and_job
    base = run_dir(run)
    good = base / "candidates" / "ck" / "full_e0001.pt"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"A" * 4096)
    w = Worker(api_settings, WorkerConfig(cpu=True), worker_id="w-sha:1")
    try:
        w._checkpoint(job, _report(good), base)
        rows = repo.list_checkpoints(w.conn, run["id"])
        assert len(rows) == 1 and rows[0]["sha256"] == hashlib.sha256(b"A" * 4096).hexdigest()
        assert _errors(w.conn, run["id"]) == []

        # Same size, different bytes: the child reported the digest of the original content.
        tampered = base / "candidates" / "ck" / "full_e0002.pt"
        tampered.write_bytes(b"B" * 4096)
        report = _report(tampered, epoch=2)
        tampered.write_bytes(b"C" * 4096)
        w._checkpoint(job, report, base)
        assert [r["epoch"] for r in repo.list_checkpoints(w.conn, run["id"])] == [1], "tampered file was registered"
        errs = _errors(w.conn, run["id"])
        assert len(errs) == 1 and errs[0]["reason_code"] == "CHECKPOINT_REPORT_REJECTED"
        assert "sha256 mismatch" in errs[0]["message"]
        assert errs[0]["reported_sha256"] == report["sha256"]
        assert errs[0]["computed_sha256"] == hashlib.sha256(b"C" * 4096).hexdigest()

        # A report without any digest cannot be verified either, and an uppercase / prefixed digest is normalised.
        w._checkpoint(job, _report(good, epoch=3, sha256=None), base)
        w._checkpoint(job, _report(good, epoch=4, sha256="sha256:" + hashlib.sha256(b"A" * 4096).hexdigest().upper()), base)
        assert [r["epoch"] for r in repo.list_checkpoints(w.conn, run["id"])] == [1, 4]
        assert len(_errors(w.conn, run["id"])) == 2
    finally:
        w.conn.close()


class _P:
    pid = 1


@pytest.mark.parametrize("cpu", [True, False])
def test_generation_only_child_never_sees_a_gpu(api_settings, tmp_path, monkeypatch, cpu):
    captured: dict = {}

    def popen(argv, **kw):
        captured.update(kw)
        return _P()

    monkeypatch.setattr(worker_main.subprocess, "Popen", popen)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")  # an inherited GPU must not leak into the OFF child
    w = Worker(api_settings, WorkerConfig(cpu=cpu, gpu_index=0), worker_id="w-gen:cpu" if cpu else "w-gen:gpu")
    try:
        w._spawn({"id": "job-off", "spec": {"params": {"generation_only": True}}}, tmp_path / "stop", tmp_path / "log")
        assert captured["env"]["CUDA_VISIBLE_DEVICES"] == ""
        assert captured["env"]["MBNAS_WORKER_ID"] == w.id
        captured.clear()
        w._spawn({"id": "job-train", "spec": {"params": {}}}, tmp_path / "stop", tmp_path / "log")
        expected = "" if cpu else "0"  # a --cpu worker never exposes a GPU; a GPU worker pins its own index
        assert captured["env"]["CUDA_VISIBLE_DEVICES"] == expected
        assert "MBNAS_API_TOKEN" not in captured["env"] and "MBNAS_CORE_API_TOKEN" not in captured["env"]  # DD-A13
    finally:
        w.conn.close()
    assert os.environ["CUDA_VISIBLE_DEVICES"] == "0"

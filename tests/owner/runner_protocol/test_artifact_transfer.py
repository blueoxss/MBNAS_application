"""WP-5C (DD-A38/DD-A39) against the real core app: the real ``RunnerDaemon`` with ``artifact_transfer=local`` runs a
training job through the T3 harness (core TestClient transport, a real ``python -m mbnas_runner.child`` with the
SYNTHETIC ``remote_handlers``) and every file the core registered - E6 checkpoints and E7 artifacts (evaluation panels,
the executed architecture.yaml) - ends up in the content-addressed store ``<store>/<sha256[:2]>/<sha256>`` with its
DD-A39 meta; a transfer failure never fails the job, is reported in ``result.outputs.artifact_transfer`` and is
completed by a later retry pass. The gateway side is checked with the WP-5A store and download helper
(``mbnas_server.artifacts``; the runner itself imports nothing from the gateway): every core artifact record downloads
with a matching digest once its bytes are in the store and is ARTIFACT_NOT_READY before. SYNTHETIC."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import harness
import pytest

from mbnas_core.db import repo
from mbnas_runner.journal import AttemptJournal, Stage
from mbnas_runner.transfer import META_KEYS, OK
from mbnas_server.artifacts.download import serve_artifact
from mbnas_server.artifacts.store import ArtifactStore, StoreError

pytestmark = pytest.mark.runner_protocol


def _hex(value: str) -> str:
    return str(value).removeprefix("sha256:")


def _registered(db, run_id: str) -> dict[tuple[str, str], str]:
    """{(object, id): sha256} of everything the core registered for the run (E6 checkpoints + E7 artifacts)."""
    out = {("checkpoint", c["id"]): _hex(c["sha256"]) for c in repo.list_checkpoints(db, run_id)}
    out.update({("artifact", a["id"]): _hex(a["sha256"]) for a in repo.list_artifacts(db, run_id)})
    return out


def _download(store: Path, art: dict) -> tuple[dict, bytes]:
    """What the gateway serves for a core artifact record (DD-A39 download path, in-process)."""
    headers, body = serve_artifact(ArtifactStore(str(store)), {**art, "sha256": _hex(art["sha256"])})
    with body:
        return headers, b"".join(body)


def _run_job(core, daemon_factory, base_proto, db, monkeypatch, store: Path):
    monkeypatch.setenv("MBNAS_JOB_HANDLERS_MODULE", "remote_handlers")  # + the executed architecture.yaml
    run_id, _ = harness.validate_and_run(core, base_proto())
    # max tries high enough that heartbeat-kicked retries during the job never exhaust them (the failure test)
    d = daemon_factory(artifact_transfer="local", artifact_store_dir=str(store), artifact_transfer_max_tries=50)
    assert d.run(once=True) == 0
    if d._retry_thread is not None:  # a heartbeat-kicked retry pass may still be running
        d._retry_thread.join(30)
    (job,) = repo.list_jobs(db, run_id)
    return d, run_id, job


def test_local_transfer_stores_every_registered_file_with_meta(core, daemon_factory, base_proto, db, monkeypatch,
                                                                tmp_path):
    """Checkpoints, evaluation panels and the executed architecture.yaml registered with the core are in the store under
    their digest, byte-identical (sha256 re-computed here), with the DD-A39 meta; atomically written checkpoints are
    hard-linked, the in-place-written architecture.yaml is copied; identical panel bytes are stored once (no-op for the
    later registrations); the committed result reports ``{mode: local, ok: true, failed: []}``."""
    store = tmp_path / "gateway" / "artifacts"
    d, run_id, job = _run_job(core, daemon_factory, base_proto, db, monkeypatch, store)
    assert job["status"] == "SUCCEEDED", job.get("error")
    regs = _registered(db, run_id)
    kinds = {a["kind"] for a in repo.list_artifacts(db, run_id)}
    assert {"gt", "tp", "fn", "fp", "architecture"} <= kinds
    assert sum(1 for obj, _ in regs if obj == "checkpoint") == 2  # epoch_target_total 2 -> two full-state checkpoints
    by_sha: dict[str, set[str]] = {}
    for (_, oid), sha in regs.items():
        by_sha.setdefault(sha, set()).add(oid)
    run_dir = Path(d.settings.work_root) / "runs" / run_id
    for (obj, oid), sha in regs.items():
        path = store / sha[:2] / sha
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, (obj, oid)
        meta = json.loads((store / sha[:2] / f"{sha}.json").read_text(encoding="utf-8"))
        assert sorted(meta) == sorted(META_KEYS)
        assert meta["artifact_id"] in by_sha[sha]  # the first registration of these bytes
        assert (meta["run_id"], meta["job_id"], meta["attempt"]) == (run_id, job["id"], 1)
        assert meta["registered_by"] == "R1" and meta["source"] == "local" and meta["bytes"] == path.stat().st_size
        assert (run_dir / meta["relpath"]).is_file()
    for c in repo.list_checkpoints(db, run_id):
        meta = json.loads((store / c["sha256"][:2] / f"{c['sha256']}.json").read_text(encoding="utf-8"))
        assert meta["kind"] == "checkpoint" and meta["artifact_id"] == c["id"]
        assert meta["mime"] == "application/octet-stream"
        assert os.stat(store / c["sha256"][:2] / c["sha256"]).st_ino == os.stat(run_dir / meta["relpath"]).st_ino
    (arch,) = [a for a in repo.list_artifacts(db, run_id) if a["kind"] == "architecture"]
    sha = _hex(arch["sha256"])
    assert os.stat(store / sha[:2] / sha).st_ino != os.stat(run_dir / arch["relpath"]).st_ino  # copied, not linked
    panels = [a for a in repo.list_artifacts(db, run_id) if a["kind"] in ("gt", "tp", "fn", "fp")]
    assert len({_hex(a["sha256"]) for a in panels}) == 1  # the SYNTHETIC panels share their bytes: stored once
    objects = sorted(p.name for p in store.rglob("*") if p.is_file() and not p.name.endswith(".json"))
    assert objects == sorted(set(regs.values()))  # nothing else in the store, no temp names left
    assert not [p for p in store.rglob(".*")]
    gw = ArtifactStore(str(store))
    assert sorted(o.sha256 for o in gw.iter_all()) == sorted(set(regs.values()))
    for art in repo.list_artifacts(db, run_id):  # gateway download of every core artifact record: digest match
        headers, data = _download(store, art)
        assert hashlib.sha256(data).hexdigest() == headers["X-Artifact-Sha256"] == _hex(art["sha256"])
        assert int(headers["Content-Length"]) == art["bytes"] == len(data)
    outputs = job["result"]["outputs"]
    assert outputs["artifact_transfer"] == {"mode": "local", "ok": True, "transferred": len(regs), "failed": []}
    j = AttemptJournal.create(d.settings.work_root, job["id"], 1)
    assert j.stage == Stage.CLOSED and j.result["result"]["outputs"]["artifact_transfer"] == outputs["artifact_transfer"]
    items = [it for _, entry in j.transfers() for it in entry["items"]]
    assert {it["state"] for it in items} == {OK} and len(items) == len(regs)
    methods = sorted(it["method"] for it in items if it["kind"] in ("gt", "tp", "fn", "fp"))
    assert methods == ["exists", "exists", "exists", "link"]


def test_transfer_failure_never_fails_the_job_and_a_later_retry_completes(core, daemon_factory, base_proto, db,
                                                                          monkeypatch, tmp_path):
    """The store is unusable while the job runs (a file where its directory should be): the job still SUCCEEDS, its
    committed result lists every registered file as failed and retryable (codes only, no host paths) and the journal
    keeps them; once the store exists a retry pass stores all of them byte-identical, without touching the committed
    result (result.json frozen, the core's job row unchanged)."""
    store = tmp_path / "gateway" / "artifacts"
    store.parent.mkdir(parents=True)
    store.write_text("not a directory yet")
    d, run_id, job = _run_job(core, daemon_factory, base_proto, db, monkeypatch, store)
    assert job["status"] == "SUCCEEDED", job.get("error")
    regs = _registered(db, run_id)
    at = job["result"]["outputs"]["artifact_transfer"]
    assert at["mode"] == "local" and at["ok"] is False and at["transferred"] == 0
    assert sorted((f["object"], f["artifact_id"]) for f in at["failed"]) == sorted(regs)
    assert {(f["code"], f["retry"]) for f in at["failed"]} == {("STORE_UNAVAILABLE", True)}
    assert str(tmp_path) not in json.dumps(at)
    j = AttemptJournal.create(d.settings.work_root, job["id"], 1)
    frozen = j.result
    assert j.stage == Stage.CLOSED and j.transfers_open
    store.unlink()
    for art in repo.list_artifacts(db, run_id):  # registered but not transferred: not downloadable yet
        with pytest.raises(StoreError) as exc:
            _download(store, art)
        assert exc.value.code == "ARTIFACT_NOT_READY" and exc.value.status == 404
    counts = d.retry_transfers(now=time.time() + 10 ** 6)
    assert counts["ok"] == len(regs) and counts["left"] == 0 and not j.transfers_open
    for sha in regs.values():
        assert hashlib.sha256((store / sha[:2] / sha).read_bytes()).hexdigest() == sha
        assert (store / sha[:2] / f"{sha}.json").is_file()
    for art in repo.list_artifacts(db, run_id):
        headers, data = _download(store, art)
        assert hashlib.sha256(data).hexdigest() == headers["X-Artifact-Sha256"] == _hex(art["sha256"])
    assert j.result == frozen
    assert repo.get_job(db, job["id"])["result"] == job["result"]

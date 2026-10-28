"""Artifact access matrix (bearer / signed / expired / tampered / traversal / missing / modified) and the export
bundle contents. Synthetic stub artifacts; no GPU."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import harness
import pytest
from harness import SIGNING_KEY, TOKEN, auth

from mbnas_core.artifacts.registry import ArtifactError, register_file, sign
from mbnas_core.db import repo
from mbnas_core.db.core import connect, tx


@pytest.fixture
def done_run(client, protocol, inline_worker):
    run_id, _ = harness.validate_and_run(client, protocol)
    harness.drain(inline_worker)
    snap = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    assert snap["run"]["status"] == "COMPLETED"
    return run_id, snap


def _path_query(url):
    parts = urlsplit(url)
    return parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}


def test_signed_and_bearer_access(client, done_run, api_settings):
    run_id, snap = done_run
    art = next(a for c in snap["candidates"] for a in c["artifacts"] if a["kind"] == "tp")
    assert art["auth_mode"] == "signed_url" and art["state"] == "READY" and art["expires_at"]
    path, query = _path_query(art["url"])
    r = client.get(path, params=query)  # no Authorization header at all
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["x-artifact-sha256"] == hashlib.sha256(r.content).hexdigest() == art["sha256"]
    assert client.get(path, headers=auth()).status_code == 200
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth("nope")).status_code == 403
    bad = dict(query, sig="0" * 64)
    assert client.get(path, params=bad).json()["code"] == "ACCESS_DENIED"
    exp = int(time.time()) - 5
    r = client.get(path, params={"exp": exp, "sig": sign(SIGNING_KEY.encode(), art["id"], exp)})
    assert r.status_code == 410 and r.json()["code"] == "EXPIRED"
    other = next(a for c in snap["candidates"] for a in c["artifacts"] if a["id"] != art["id"])
    r = client.get(f"/api/v1/runs/{run_id}/artifacts/{other['id']}", params=query)  # signature of another artifact
    assert r.status_code == 403
    refreshed = client.post(f"/api/v1/runs/{run_id}/artifacts/{art['id']}/refresh", headers=auth()).json()
    assert refreshed["url"] != "" and refreshed["state"] == "READY"


@pytest.mark.parametrize("artifact_id", ["..%2F..%2Fetc%2Fpasswd", "%2Fetc%2Fpasswd", "art-unknown", "%2E%2E", "%2E"])
def test_no_filesystem_path_from_request(client, done_run, artifact_id):
    run_id, _ = done_run
    r = client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}", headers=auth())
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_artifact_is_run_scoped(client, done_run):
    run_id, snap = done_run
    art = snap["candidates"][-1]["artifacts"][0]
    r = client.get(f"/api/v1/runs/run-other/artifacts/{art['id']}", headers=auth())
    assert r.status_code == 404


def _art(snap, kind="gt"):
    return next(a for c in snap["candidates"] for a in c["artifacts"] if a["kind"] == kind)


def _file_of(api_settings, run_id, aid):
    conn = connect(api_settings, init=False)
    try:
        row = repo.get_artifact(conn, run_id, aid)
        run = repo.get_run(conn, run_id)
    finally:
        conn.close()
    return Path(run["resolved_protocol"]["console"]["paths"]["output_root"]) / run_id / row["relpath"]


def test_missing_modified_symlink_and_not_ready(client, done_run, api_settings, tmp_path):
    run_id, snap = done_run
    gt, tp, fn = _art(snap, "gt"), _art(snap, "tp"), _art(snap, "fn")
    _file_of(api_settings, run_id, gt["id"]).unlink()
    r = client.get(f"/api/v1/runs/{run_id}/artifacts/{gt['id']}", headers=auth())
    assert r.status_code == 404 and r.json()["code"] == "MISSING"
    f = _file_of(api_settings, run_id, tp["id"])
    f.write_bytes(f.read_bytes()[:-1] + b"X")  # same size, different bytes
    r = client.get(f"/api/v1/runs/{run_id}/artifacts/{tp['id']}", headers=auth())
    assert r.status_code == 409 and r.json()["code"] == "LOAD_FAILED"
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    target = _file_of(api_settings, run_id, fn["id"])
    target.unlink()
    os.symlink(outside, target)
    r = client.get(f"/api/v1/runs/{run_id}/artifacts/{fn['id']}", headers=auth())
    assert r.status_code == 403 and b"secret" not in r.content
    conn = connect(api_settings, init=False)
    with tx(conn):
        run = repo.get_run(conn, run_id)
        with pytest.raises(ArtifactError):
            register_file(conn, run, kind="other", path=str(target))  # symlink escaping the run dir
        with pytest.raises(ArtifactError):
            register_file(conn, run, kind="other", path="../../etc/passwd")
        with pytest.raises(ValueError):
            repo.insert_artifact(conn, id="a-abs", run_id=run_id, kind="other", relpath="/etc/passwd")
        repo.insert_artifact(conn, id="a-pending", run_id=run_id, kind="gt", relpath="pending.png", state="NOT_READY")
    conn.close()
    r = client.get(f"/api/v1/runs/{run_id}/artifacts/a-pending", headers=auth())
    assert r.status_code == 409 and r.json()["code"] == "NOT_READY"
    snap2 = client.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    states = {a["id"]: a["state"] for c in snap2["candidates"] for a in c["artifacts"]}
    assert states[gt["id"]] == "MISSING" and states[tp["id"]] == "LOAD_FAILED"


def test_export_bundle(client, done_run, api_settings):
    run_id, snap = done_run
    assert client.get(f"/api/v1/runs/{run_id}/export").status_code == 401
    r = client.get(f"/api/v1/runs/{run_id}/export", headers=auth())
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["x-bundle-sha256"] == hashlib.sha256(r.content).hexdigest()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    required = {"README.txt", "protocol/requested.json", "protocol/resolved.json", "protocol/protocol_hash.txt",
                "manifests/hashes.json", "evaluations.csv", "evaluations.jsonl", "lineage.json", "generations.json",
                "cost_ledger.csv", "checkpoints.json", "artifacts.json", "jobs.json", "events.jsonl",
                "provenance.json", "environment.lock.json", "SHA256SUMS"}
    assert required <= names
    assert any(n.startswith("manifests/") and n != "manifests/hashes.json" for n in names)  # cached split manifests
    resolved = json.loads(zf.read("protocol/resolved.json"))
    assert resolved == snap["run"]["resolved_protocol"]
    assert zf.read("protocol/protocol_hash.txt").decode().strip() == snap["run"]["protocol_hash"]
    rows = list(csv.DictReader(io.StringIO(zf.read("evaluations.csv").decode())))
    assert len(rows) == len(snap["candidates"])
    events = zf.read("events.jsonl").decode().splitlines()
    assert len(events) == snap["last_seq"]
    ledger = list(csv.DictReader(io.StringIO(zf.read("cost_ledger.csv").decode())))
    assert ledger and {r["arm"] for r in ledger} >= {"Baseline", "GA", "IndependentRandom"}
    for line in zf.read("SHA256SUMS").decode().splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256(zf.read(name)).hexdigest() == digest
    blob = b"".join(zf.read(n) for n in names)
    assert TOKEN.encode() not in blob and SIGNING_KEY.encode() not in blob
    env = json.loads(zf.read("environment.lock.json"))
    assert set(env) == {"api_process", "recorded_environment_lock"}
    requested = json.loads(zf.read("protocol/requested.json"))
    assert requested["status"] == "PRESET_PILOT_SMOKE"  # the user's pre-resolution protocol, kept verbatim
    r2 = client.get(f"/api/v1/runs/{run_id}/export", headers=auth())
    assert set(zipfile.ZipFile(io.BytesIO(r2.content)).namelist()) == names

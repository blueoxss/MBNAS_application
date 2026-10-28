"""WP-5B end to end (DD-A38/DD-A39; W4 wiring notes; SECURITY_BASELINE R8): a runner-produced file layout (real
``RunnerDaemon`` + real job child with the SYNTHETIC ``remote_handlers``: two full-state checkpoints, four evaluation
panels, the executed architecture.yaml) registered with the real core (E6/E7), transferred by the runner's ``local``
mode into the gateway store, and downloaded THROUGH THE GATEWAY: digest match, location transition
NOT_READY -> PRESENT@server_store (READY), tampered bytes 409 LOAD_FAILED, untransferred 404 ARTIFACT_NOT_READY,
signed URL expiry 410, checkpoint download, URLs signed in core views (core key != gateway key) delegated to the
core, export zip with the executed YAML bytes. SYNTHETIC numbers and bytes; CPU only."""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from urllib.parse import parse_qs, urlsplit

import pytest

from harness import auth

from mbnas_core.db import repo
from mbnas_server.artifacts.signing import sign

pytestmark = pytest.mark.runner_protocol


def _hex(value: str) -> str:
    return str(value).removeprefix("sha256:")


def _record(core, headers, run_id: str, object_id: str) -> dict:
    r = core.get(f"/core/v1/console/runs/{run_id}/artifacts/{object_id}/record", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _objects(db, run_id: str) -> list[tuple[str, str, str]]:
    """(object, id, sha256 hex) of everything the core registered for the run (E7 artifacts + E6 checkpoints)."""
    out = [("artifact", a["id"], _hex(a["sha256"])) for a in repo.list_artifacts(db, run_id)]
    out += [("checkpoint", c["id"], _hex(c["sha256"])) for c in repo.list_checkpoints(db, run_id)]
    return out


def _server_store_rows(db, kind: str, object_id: str) -> list[dict]:
    return [loc for loc in repo.list_artifact_locations(db, kind, object_id) if loc["store"] == "server_store"]


def test_register_transfer_download_digest_match_and_location_transition(gateway, core, server_headers, db,
                                                                        finished_run, store_dir):
    run_id, job = finished_run()
    assert job["result"]["outputs"]["artifact_transfer"]["ok"] is True
    objects = _objects(db, run_id)
    kinds = {a["kind"] for a in repo.list_artifacts(db, run_id)}
    assert {"gt", "tp", "fn", "fp", "architecture"} <= kinds and sum(o == "checkpoint" for o, _, _ in objects) == 2
    for obj, oid, sha in objects:
        before = _record(core, server_headers, run_id, oid)
        assert (before["object"], before["origin"], before["sha256"]) == (obj, "runner", sha)
        assert before["state"] == "NOT_READY" and before["server_store"] is None  # registered, never verified (R8)
        r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{oid}", headers=auth())
        assert r.status_code == 200, (oid, r.text)
        assert hashlib.sha256(r.content).hexdigest() == r.headers["x-artifact-sha256"] == sha
        assert r.headers["x-artifact-verification"] == "pre-send"
        assert r.content == (store_dir / sha[:2] / sha).read_bytes()
        after = _record(core, server_headers, run_id, oid)
        assert after["state"] == "READY" and after["server_store"] == "PRESENT"
        (loc,) = _server_store_rows(db, obj, oid)
        assert (loc["state"], loc["verified_by"], _hex(loc["sha256"]), loc["relpath"]) == (
            "PRESENT", "store", sha, f"{sha[:2]}/{sha}")
    for c in repo.list_checkpoints(db, run_id):  # checkpoints: application/octet-stream, kind checkpoint
        r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{c['id']}", headers=auth())
        assert r.headers["content-type"] == "application/octet-stream" and len(r.content) == c["bytes"]
        view = gateway.post(f"/api/v1/runs/{run_id}/artifacts/{c['id']}/refresh", headers=auth()).json()
        assert view["kind"] == "checkpoint" and view["state"] == "READY" and view["auth_mode"] == "signed_url"
        assert view["metadata"]["checkpoint_id"] == c["id"]
        parts = urlsplit(view["url"])
        r = gateway.get(parts.path, params={k: v[0] for k, v in parse_qs(parts.query).items()})  # no bearer
        assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == _hex(c["sha256"])
    # downloading again does not add location rows (one server_store row per object, reported once)
    oid = objects[0][1]
    assert gateway.get(f"/api/v1/runs/{run_id}/artifacts/{oid}", headers=auth()).status_code == 200
    assert len(_server_store_rows(db, objects[0][0], oid)) == 1


def test_urls_signed_in_core_views_work_through_the_gateway(gateway, combo, db, finished_run, core_signing_key):
    """Core views sign with the CORE key; the gateway (another key) delegates their verification to the core."""
    assert combo.gateway_settings.artifact_signing_key.get_secret_value() != core_signing_key
    run_id, _ = finished_run()
    # DD-A53 / WP-5B locations: a runner artifact is NOT_READY (bearer_fetch, no signed URL) until the gateway verified
    # its bytes in the store; the intended order is download + verify first, then the core view signs it
    snap = gateway.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    first = [a for c in snap["candidates"] for a in c.get("artifacts") or []]
    assert first and all(a["state"] == "NOT_READY" and a["auth_mode"] == "bearer_fetch" for a in first)
    for art in first:
        r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{art['id']}", headers=auth())
        assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == _hex(art["sha256"])
    snap = gateway.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    arts = [a for c in snap["candidates"] for a in c.get("artifacts") or []]
    assert {a["id"] for a in arts} == {a["id"] for a in first}
    assert all(a["state"] == "READY" and a["auth_mode"] == "signed_url" for a in arts)
    for art in arts:
        parts = urlsplit(art["url"])
        assert parts.path == f"/api/v1/runs/{run_id}/artifacts/{art['id']}"  # gateway URL (rewritten from core)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert query["sig"] == sign(core_signing_key.encode(), art["id"], int(query["exp"]))
        r = gateway.get(parts.path, params=query)  # no Authorization header at all
        assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == _hex(art["sha256"])
        forged = gateway.get(parts.path, params={**query, "sig": "0" * 64})
        assert forged.status_code == 403 and forged.json()["code"] == "ACCESS_DENIED"
        other = gateway.get(f"/api/v1/runs/{run_id}/artifacts/art-guess", params=query)  # another id, same sig
        assert other.status_code == 403 and other.json()["code"] == "ACCESS_DENIED"  # no existence signal


def test_signed_url_expiry_is_410_for_both_keys(gateway, combo, db, finished_run, core_signing_key):
    run_id, _ = finished_run()
    art = repo.list_artifacts(db, run_id)[0]
    exp = int(time.time()) - 5
    gw_key = combo.gateway_settings.artifact_signing_key.get_secret_value().encode()
    for key in (gw_key, core_signing_key.encode()):
        r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{art['id']}", params={"exp": exp,
                                                                                 "sig": sign(key, art["id"], exp)})
        assert r.status_code == 410 and r.json()["code"] == "EXPIRED"
    view = gateway.post(f"/api/v1/runs/{run_id}/artifacts/{art['id']}/refresh", headers=auth()).json()
    q = {k: v[0] for k, v in parse_qs(urlsplit(view["url"]).query).items()}
    assert q["sig"] == sign(gw_key, art["id"], int(q["exp"])) and int(q["exp"]) > time.time()
    assert gateway.get(urlsplit(view["url"]).path, params=q).status_code == 200


def test_tampered_store_bytes_are_409_and_recorded_load_failed(gateway, core, server_headers, db, finished_run,
                                                               store_dir):
    run_id, _ = finished_run()
    art = next(a for a in repo.list_artifacts(db, run_id) if a["kind"] == "architecture")
    sha = _hex(art["sha256"])
    path = store_dir / sha[:2] / sha
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01  # same size, different bytes
    path.write_bytes(bytes(data))
    r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{art['id']}", headers=auth())
    assert r.status_code == 409 and r.json()["code"] == "LOAD_FAILED" and str(store_dir) not in r.text
    rec = _record(core, server_headers, run_id, art["id"])
    assert rec["state"] == "LOAD_FAILED" and rec["server_store"] == "LOAD_FAILED"
    (loc,) = _server_store_rows(db, "artifact", art["id"])
    assert loc["state"] == "LOAD_FAILED" and loc["verified_by"] == "store"
    path.write_bytes(bytes(data[:-1]))  # and a truncated object: size check, also 409
    assert gateway.get(f"/api/v1/runs/{run_id}/artifacts/{art['id']}", headers=auth()).status_code == 409


def test_untransferred_objects_are_404_artifact_not_ready(gateway, core, server_headers, db, finished_run, store_dir):
    run_id, job = finished_run(artifact_transfer="none")
    assert "artifact_transfer" not in (job["result"].get("outputs") or {})
    assert not store_dir.exists() or not any(store_dir.rglob("*"))
    for obj, oid, _ in _objects(db, run_id):
        r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{oid}", headers=auth())
        assert r.status_code == 404 and r.json()["code"] == "ARTIFACT_NOT_READY", (obj, oid, r.text)
        rec = _record(core, server_headers, run_id, oid)
        assert rec["state"] == "NOT_READY" and rec["origin"] == "runner"
        view = gateway.post(f"/api/v1/runs/{run_id}/artifacts/{oid}/refresh", headers=auth()).json()
        assert view["state"] == "NOT_READY" and view["auth_mode"] == "bearer_fetch"
    r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/art-unknown", headers=auth())
    assert r.status_code == 404 and r.json()["code"] == "MISSING"


def test_view_overlays_follow_the_locations(gateway, db, finished_run):
    """Helpers for the core views (WP-4B): runner artifacts are NOT_READY until the gateway verified them; runner
    checkpoints are PRESENT (runner_local) although the core host has no such file."""
    from mbnas_core.artifacts.locations import checkpoint_states, effective_states

    run_id, _ = finished_run()
    arts = repo.list_artifacts(db, run_id)
    assert effective_states(db, run_id) == {a["id"]: "NOT_READY" for a in arts}
    assert gateway.get(f"/api/v1/runs/{run_id}/artifacts/{arts[0]['id']}", headers=auth()).status_code == 200
    assert effective_states(db, run_id) == {a["id"]: "NOT_READY" for a in arts[1:]}
    cks = repo.list_checkpoints(db, run_id)
    assert checkpoint_states(db, run_id) == {c["id"]: "PRESENT" for c in cks}


@pytest.mark.parametrize("transfer", ["none", "local"])
def test_runner_artifacts_are_never_flipped_missing_by_a_core_disk_lookup(gateway, core, server_headers, combo, db,
                                                                           finished_run, transfer):
    """T7 finding f2 (DD-A49 b): the WP-3 gateway relayed artifact downloads to the core, which looked for a runner's
    bytes on its own disk, answered 404 MISSING and flipped the rows to MISSING. Now the gateway serves runner objects
    from its store only (never asking the core for their bytes) and the core's own byte route refuses them without a
    disk lookup and without touching the row."""
    run_id, _ = finished_run(artifact_transfer=transfer)
    arts = repo.list_artifacts(db, run_id)
    assert arts and {a["state"] for a in arts} == {"READY"}
    before = {a["id"]: (a["state"], a["sha256"], a["bytes"]) for a in arts}
    seen = len(combo.transport.requests)
    for a in arts:
        r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{a['id']}", headers=auth())
        if transfer == "local":
            assert r.status_code == 200 and r.headers["x-artifact-sha256"] == _hex(a["sha256"])
        else:
            assert r.status_code == 404 and r.json()["code"] == "ARTIFACT_NOT_READY", r.text
            assert r.json()["location"] == "store"
        direct = core.get(f"/core/v1/console/runs/{run_id}/artifacts/{a['id']}", headers=server_headers)
        assert direct.status_code == 404 and direct.json()["code"] == "ARTIFACT_NOT_READY"
    core_calls = [path for method, path in combo.transport.requests[seen:] if method == "GET"]
    assert all(path.endswith("/record") for path in core_calls), core_calls  # no byte relay for runner objects
    after = {a["id"]: (a["state"], a["sha256"], a["bytes"]) for a in repo.list_artifacts(db, run_id)}
    assert after == before  # never flipped to MISSING / LOAD_FAILED
    snap = gateway.get(f"/api/v1/runs/{run_id}/snapshot", headers=auth()).json()
    assert {a["state"] for c in snap["candidates"] for a in c.get("artifacts") or []} <= {"READY", "NOT_READY"}


def test_location_report_is_server_scoped_and_checked(core, server_headers, combo, db, finished_run):
    run_id, _ = finished_run()
    art = repo.list_artifacts(db, run_id)[0]
    url = f"/core/v1/console/artifacts/{art['id']}/locations"
    good = {"run_id": run_id, "sha256": art["sha256"], "bytes": art["bytes"], "state": "PRESENT"}
    assert core.post(url, json=good).status_code == 401
    runner_like = {"Authorization": "Bearer not-a-principal"}
    assert core.post(url, json=good, headers=runner_like).status_code in (401, 403)
    r = core.post(url, json={**good, "sha256": "0" * 64}, headers=server_headers)
    assert r.status_code == 409 and r.json()["code"] == "LOCATION_CONFLICT"
    r = core.post(url, json={**good, "bytes": art["bytes"] + 1}, headers=server_headers)
    assert r.status_code == 409 and r.json()["code"] == "LOCATION_CONFLICT"
    r = core.post(url, json={**good, "run_id": "run-other"}, headers=server_headers)
    assert r.status_code == 404 and r.json()["code"] == "MISSING"
    r = core.post(url, json={**good, "store": "runner_local"}, headers=server_headers)
    assert r.status_code == 422
    r = core.post(url, json={**good, "relpath": "/etc/passwd"}, headers=server_headers)
    assert r.status_code == 422
    r = core.post(url, json=good, headers=server_headers)
    assert r.status_code == 200 and r.json()["artifact_state"] == "READY" and r.json()["created"] is True
    r = core.post(url, json=good, headers=server_headers)
    assert r.status_code == 200 and r.json()["created"] is False
    assert len(_server_store_rows(db, "artifact", art["id"])) == 1


def test_export_zip_carries_the_executed_yaml_bytes_from_the_store(gateway, db, finished_run, store_dir):
    run_id, _ = finished_run()
    arch = next(a for a in repo.list_artifacts(db, run_id) if a["kind"] == "architecture")
    sha = _hex(arch["sha256"])
    r = gateway.get(f"/api/v1/runs/{run_id}/export", headers=auth())
    assert r.status_code == 200 and r.headers["x-bundle-sha256"] == hashlib.sha256(r.content).hexdigest()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    member = f"architectures/{arch['id']}.yaml"
    assert {"protocol/resolved.json", "artifacts.json", "checkpoints.json", "SHA256SUMS", member,
            "export_objects.json"} <= names
    assert zf.read(member) == (store_dir / sha[:2] / sha).read_bytes()
    assert hashlib.sha256(zf.read(member)).hexdigest() == sha
    for line in zf.read("SHA256SUMS").decode().splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256(zf.read(name)).hexdigest() == digest
    assert {line.split("  ", 1)[1] for line in zf.read("SHA256SUMS").decode().splitlines()} == names - {"SHA256SUMS"}
    index = {e["id"]: e for e in json.loads(zf.read("export_objects.json"))}
    assert index[arch["id"]]["included"] is True and _hex(index[arch["id"]]["sha256"]) == sha
    assert not any(n.startswith("images/") for n in names)
    with_images = zipfile.ZipFile(io.BytesIO(gateway.get(f"/api/v1/runs/{run_id}/export?include=images",
                                                         headers=auth()).content))
    panels = [a for a in repo.list_artifacts(db, run_id) if a["kind"] in ("gt", "tp", "fn", "fp")]
    image_members = {n.split("/", 1)[1].rsplit(".", 1)[0]: n for n in with_images.namelist() if n.startswith("images/")}
    for a in panels:
        assert hashlib.sha256(with_images.read(image_members[a["id"]])).hexdigest() == _hex(a["sha256"])
    again = zipfile.ZipFile(io.BytesIO(gateway.get(f"/api/v1/runs/{run_id}/export", headers=auth()).content))
    assert again.namelist() == zf.namelist()  # same members, same order; only provenance.exported_at moves
    assert all(again.read(n) == zf.read(n) for n in names - {"provenance.json", "SHA256SUMS"})

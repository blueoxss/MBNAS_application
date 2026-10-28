"""V4-B fix (DD-A55 a, DD-A51 c): ``CandidateDetail.checkpoints[].artifact_id`` (contract 1.5.0, unchanged) is the
checkpoint id - the id the gateway serves at ``GET /api/v1/runs/{run}/artifacts/{ck-id}`` - exactly while a
``server_store`` location about the checkpoint's recorded digest is PRESENT, and null otherwise (only on the runner,
never transferred, store copy reported damaged, report about other bytes).

Real core + real gateway in one process (``harness.combined_app``) with the real ``RunnerDaemon`` and its ``local``
transfer into the gateway store (fixtures: ``conftest.py``, the same as ``test_artifact_wiring.py``). The store
verification is the gateway's own download (digest re-check + location report) or, simulated, the location report the
gateway would send (``POST /core/v1/console/artifacts/{id}/locations`` with its service token). SYNTHETIC bytes; CPU
only.
"""
from __future__ import annotations

import hashlib

import pytest

from harness import auth

from mbnas_contracts.schemas.records import CandidateDetail
from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.orchestration.retention import checkpoint_views

pytestmark = pytest.mark.runner_protocol


def _hex(value: str) -> str:
    return str(value).removeprefix("sha256:")


def _detail(gateway, core, server_headers, run_id: str, candidate_id: str) -> dict[str, dict]:
    """``{checkpoint id: view}`` of the candidate detail through the gateway; the core console answer is the same."""
    r = gateway.get(f"/api/v1/runs/{run_id}/candidates/{candidate_id}", headers=auth())
    assert r.status_code == 200, r.text
    body = r.json()
    CandidateDetail.model_validate(body)  # the 1.5.0 contract model (closed CheckpointView)
    direct = core.get(f"/core/v1/console/runs/{run_id}/candidates/{candidate_id}", headers=server_headers)
    assert direct.status_code == 200 and direct.json()["checkpoints"] == body["checkpoints"]
    views = {c["id"]: c for c in body["checkpoints"]}
    assert all("artifact_id" in v and "path" not in v for v in views.values()), views  # null is sent, never a path
    return views


def _report(core, server_headers, run_id: str, ck: dict, state: str) -> dict:
    """The location report the gateway sends after it (re)verified its store copy of ``ck``."""
    r = core.post(f"/core/v1/console/artifacts/{ck['id']}/locations", headers=server_headers,
                  json={"run_id": run_id, "sha256": ck["sha256"], "bytes": ck["bytes"], "state": state})
    assert r.status_code == 200, r.text
    return r.json()


def test_artifact_id_is_the_checkpoint_id_while_the_store_holds_verified_bytes(gateway, core, server_headers, db,
                                                                              finished_run):
    run_id, job = finished_run()
    assert job["result"]["outputs"]["artifact_transfer"]["ok"] is True
    first, second = cks = repo.list_checkpoints(db, run_id)  # two full-state checkpoints of the one candidate
    cid = first["candidate_id"]
    assert second["candidate_id"] == cid

    # 1) transferred by the runner, never verified by the gateway: PRESENT (runner_local) but no store id yet
    views = _detail(gateway, core, server_headers, run_id, cid)
    assert set(views) == {c["id"] for c in cks}
    assert {k: (v["state"], v["artifact_id"]) for k, v in views.items()} == {c["id"]: ("PRESENT", None) for c in cks}

    # 2) the gateway verifies the first one (its download: store bytes + digest re-check + location report)
    r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{first['id']}", headers=auth())
    assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == _hex(first["sha256"])
    views = _detail(gateway, core, server_headers, run_id, cid)
    assert views[first["id"]]["artifact_id"] == first["id"] and views[second["id"]]["artifact_id"] is None
    # the advertised id is exactly what the gateway serves, byte-identical to the recorded digest
    r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{views[first['id']]['artifact_id']}", headers=auth())
    assert r.status_code == 200 and r.headers["x-artifact-sha256"] == _hex(first["sha256"])
    assert hashlib.sha256(r.content).hexdigest() == _hex(first["sha256"]) and len(r.content) == first["bytes"]

    # 3) simulated store verification of the second one (the report the gateway sends)
    assert _report(core, server_headers, run_id, second, "PRESENT")["artifact_state"] == "READY"
    views = _detail(gateway, core, server_headers, run_id, cid)
    assert {k: (v["state"], v["artifact_id"]) for k, v in views.items()} == {c["id"]: ("PRESENT", c["id"]) for c in cks}

    # 4) a damaged store copy withdraws the id (the runner copy keeps the row PRESENT); a re-verification restores it
    assert _report(core, server_headers, run_id, second, "LOAD_FAILED")["artifact_state"] == "LOAD_FAILED"
    views = _detail(gateway, core, server_headers, run_id, cid)
    assert (views[second["id"]]["state"], views[second["id"]]["artifact_id"]) == ("PRESENT", None)
    assert views[first["id"]]["artifact_id"] == first["id"]
    assert _report(core, server_headers, run_id, second, "PRESENT")["artifact_state"] == "READY"
    assert _detail(gateway, core, server_headers, run_id, cid)[second["id"]]["artifact_id"] == second["id"]

    # 5) retention deleted both on the runner: the one with a verified store copy stays PRESENT + downloadable by its
    #    id, the one whose store copy is reported missing reads PRUNED without an id
    assert _report(core, server_headers, run_id, second, "MISSING")["artifact_state"] == "MISSING"
    with tx(db):
        for ck in cks:
            repo.mark_checkpoint_pruned(db, ck["id"], prune={"policy": "last_rung_end_only", "reason": "SYNTHETIC"})
    views = _detail(gateway, core, server_headers, run_id, cid)
    assert (views[first["id"]]["state"], views[first["id"]]["artifact_id"]) == ("PRESENT", first["id"])
    assert (views[second["id"]]["state"], views[second["id"]]["artifact_id"]) == ("PRUNED", None)
    r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{first['id']}", headers=auth())
    assert r.status_code == 200 and hashlib.sha256(r.content).hexdigest() == _hex(first["sha256"])


def test_untransferred_checkpoints_keep_a_null_artifact_id(gateway, core, server_headers, db, finished_run):
    """Never in the store: the detail offers no store id, the gateway answers 404 ARTIFACT_NOT_READY by checkpoint id
    (the web shows "아직 저장소에 없음"), and a store row about OTHER bytes never makes one appear."""
    run_id, _ = finished_run(artifact_transfer="none")
    cks = repo.list_checkpoints(db, run_id)
    assert len(cks) == 2
    cid = cks[0]["candidate_id"]
    views = _detail(gateway, core, server_headers, run_id, cid)
    assert {k: (v["state"], v["artifact_id"]) for k, v in views.items()} == {c["id"]: ("PRESENT", None) for c in cks}
    for ck in cks:
        r = gateway.get(f"/api/v1/runs/{run_id}/artifacts/{ck['id']}", headers=auth())
        assert r.status_code == 404 and r.json()["code"] == "ARTIFACT_NOT_READY", r.text
    other = "f" * 64
    assert other != _hex(cks[0]["sha256"])
    with tx(db):
        repo.insert_artifact_location(db, id="loc-synthetic-other-bytes", object_kind="checkpoint",
                                      object_id=cks[0]["id"], run_id=run_id, store="server_store",
                                      relpath=f"{other[:2]}/{other}", sha256=other, bytes=cks[0]["bytes"],
                                      verified_by="store", state="PRESENT")
    views = _detail(gateway, core, server_headers, run_id, cid)
    assert all(v["artifact_id"] is None for v in views.values()), views


def test_checkpoint_views_artifact_id_member_only_when_the_store_was_asked():
    """``stored`` given: id or None per row; not given (export bundle callers): no ``artifact_id`` member at all."""
    rows = [{"id": "ck-a", "path": "/nonexistent/full_e1.pt", "state": "PRESENT"},
            {"id": "ck-b", "path": "/nonexistent/full_e2.pt", "state": "PRUNED"}]
    located = {"ck-a": "PRESENT", "ck-b": "PRUNED"}
    with_store = checkpoint_views(rows, located, stored={"ck-a"})
    assert [(v["id"], v["state"], v["artifact_id"]) for v in with_store] == [("ck-a", "PRESENT", "ck-a"),
                                                                           ("ck-b", "PRUNED", None)]
    assert all("artifact_id" not in v for v in checkpoint_views(rows, located))
    assert all("artifact_id" not in r for r in rows)  # the stored rows are never rewritten

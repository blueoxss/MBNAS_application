"""Where the bytes of an artifact or checkpoint live, as the core records it.

The core never holds runner bytes; ``artifact_locations`` rows (schema v4) say where they are:

* ``runner_local`` - written by the runner routes E6 (checkpoints) / E7 (artifacts) when a runner registers a file of
  its run directory (``verified_by = runner:<id>``). "Present on the runner's disk" never makes anything downloadable;
* ``server_store`` - written by :func:`report_server_store` when the GATEWAY reports that it found the bytes in its
  content-addressed store (``<store>/<sha256[:2]>/<sha256>``) and re-computed their SHA-256 (``verified_by =
  store``, route ``POST /core/v1/console/artifacts/{id}/locations``). One row per object, updated in place
  (``relpath`` = the store key ``<sha256[:2]>/<sha256>``);
* no location row at all - the core registered a file of its own run directory itself
  (:func:`mbnas_core.artifacts.registry.register_file`: owner worker / all-in-one profile, core-written architecture
  YAMLs). Such an object has ``origin = "core"`` and its bytes are served by the core's own download route.

:func:`gateway_record` is the run-scoped record the gateway downloads by (``GET
/core/v1/console/runs/{run_id}/artifacts/{id}/record``): an artifact id of the run first, else a checkpoint id of the
same run (checkpoints have no ``artifacts`` row; the store meta records the checkpoint id as ``artifact_id``).
``state`` follows the locations: a runner object is READY only when a ``server_store`` row is PRESENT for the record's
digest (LOAD_FAILED / MISSING when the gateway reported that, else NOT_READY); a core object keeps its registry state.
The record carries no path, no runner id and only the public metadata keys of the ``Artifact`` view.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from mbnas_contracts.util.time import utcnow_iso
from mbnas_core.artifacts.registry import PUBLIC_META_KEYS
from mbnas_core.db import repo

SERVER_STORE = "server_store"
RUNNER_LOCAL = "runner_local"
REPORTABLE_STATES = ("PRESENT", "LOAD_FAILED", "MISSING")
"""States the gateway may report for its store copy (``artifact_locations.state``)."""
CHECKPOINT_MIME = "application/octet-stream"
_HEX = re.compile(r"[0-9a-f]{64}")
_CHECKPOINT_COLUMNS = "id, run_id, candidate_id, rung, epoch, kind, sha256, bytes, state, created_at"


class LocationError(Exception):
    """A location report that must be refused: ``status`` / ``code`` / ``message`` of the Error answer."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def hex_sha(value: Any) -> str | None:
    """Lower-case 64-hex digest of ``value`` (an optional ``sha256:`` prefix is removed), or None when it is not one."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    text = text[7:] if text.startswith("sha256:") else text
    return text if _HEX.fullmatch(text) else None


def _public_meta(meta: Any) -> dict[str, Any]:
    if not isinstance(meta, Mapping):
        return {}
    return {k: meta[k] for k in PUBLIC_META_KEYS
            if k in meta and (meta[k] is None or isinstance(meta[k], (str, int, float, bool)))}


def _server_store_row(locations: Sequence[Mapping[str, Any]], sha: str | None) -> dict[str, Any] | None:
    """The ``server_store`` row of these locations that is about digest ``sha`` (latest first), or None."""
    rows = [dict(loc) for loc in locations if loc.get("store") == SERVER_STORE and sha is not None
            and hex_sha(loc.get("sha256")) == sha]
    rows.sort(key=lambda r: (str(r.get("updated_at") or ""), str(r.get("id") or "")))
    return rows[-1] if rows else None


def _effective(origin: str, registry_state: str | None, server: Mapping[str, Any] | None) -> str:
    """Public ``Artifact.state`` of an object (see the module docstring)."""
    if server is not None:
        state = server.get("state")
        if state == "PRESENT":
            return "READY"
        if state in ("LOAD_FAILED", "MISSING"):
            return str(state)
    if origin == "core":
        return registry_state if registry_state in ("READY", "NOT_READY", "MISSING", "LOAD_FAILED", "EXPIRED",
                                                    "ACCESS_DENIED") else "NOT_READY"
    return "NOT_READY"


def _checkpoint_row(conn: sqlite3.Connection, run_id: str, checkpoint_id: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_CHECKPOINT_COLUMNS} FROM checkpoints WHERE id = ? AND run_id = ?",
                       (checkpoint_id, run_id)).fetchone()
    return None if row is None else {k: row[k] for k in row.keys()}


def is_runner_object(locations: Sequence[Mapping[str, Any]]) -> bool:
    """True when a runner registered the object (a ``runner_local`` location exists): its bytes are not the core's own,
    so the core must neither serve nor judge them from its own disk."""
    return any(loc.get("store") == RUNNER_LOCAL for loc in locations)


def _origin(locations: Sequence[Mapping[str, Any]]) -> str:
    return "runner" if is_runner_object(locations) else "core"


def artifact_record(row: Mapping[str, Any], locations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Gateway record of an ``artifacts`` row (see :func:`gateway_record`)."""
    sha = hex_sha(row.get("sha256"))
    origin = _origin(locations)
    server = _server_store_row(locations, sha)
    meta = row.get("meta") if isinstance(row.get("meta"), Mapping) else {}
    return {"object": "artifact", "id": row["id"], "run_id": row["run_id"], "kind": row["kind"],
            "candidate_id": row.get("candidate_id"), "evaluation_id": row.get("evaluation_id"), "sha256": sha,
            "bytes": row.get("bytes"), "mime_type": row.get("mime_type"), "sample_id": row.get("sample_id"),
            "split_hash": row.get("split_hash"), "metadata": _public_meta(meta),
            "matching": meta.get("matching") or None, "origin": origin, "registry_state": row.get("state"),
            "server_store": None if server is None else server.get("state"),
            "state": _effective(origin, row.get("state"), server)}


def checkpoint_record(row: Mapping[str, Any], locations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Gateway record of a ``checkpoints`` row: an ``Artifact`` of kind ``checkpoint`` (metadata: checkpoint id, epoch,
    stage = rung, category = checkpoint kind). A checkpoint written on the core host (no runner location) has no
    download route of its own: it is READY only once the gateway store holds it."""
    sha = hex_sha(row.get("sha256"))
    origin = _origin(locations)
    server = _server_store_row(locations, sha)
    state = _effective("runner", None, server)  # never served from the core host (see docstring)
    if state == "NOT_READY" and row.get("state") == "PRUNED":
        state = "MISSING"  # deleted by retention on the runner and never verified in the store
    return {"object": "checkpoint", "id": row["id"], "run_id": row["run_id"], "kind": "checkpoint",
            "candidate_id": row.get("candidate_id"), "evaluation_id": None, "sha256": sha, "bytes": row.get("bytes"),
            "mime_type": CHECKPOINT_MIME, "sample_id": None, "split_hash": None,
            "metadata": _public_meta({"checkpoint_id": row["id"], "epoch": row.get("epoch"), "stage": row.get("rung"),
                                      "category": row.get("kind"), "candidate_id": row.get("candidate_id")}),
            "matching": None, "origin": origin, "registry_state": row.get("state"),
            "server_store": None if server is None else server.get("state"), "state": state}


def gateway_record(conn: sqlite3.Connection, run_id: str, object_id: str) -> dict[str, Any] | None:
    """The run-scoped record of artifact (or else checkpoint) ``object_id``; None when the run has neither."""
    row = repo.get_artifact(conn, run_id, object_id)
    if row is not None:
        return artifact_record(row, repo.list_artifact_locations(conn, "artifact", object_id))
    ck = _checkpoint_row(conn, run_id, object_id)
    if ck is not None:
        return checkpoint_record(ck, repo.list_artifact_locations(conn, "checkpoint", object_id))
    return None


def effective_states(conn: sqlite3.Connection, run_id: str) -> dict[str, str]:
    """``{artifact id: state}`` for the runner-registered artifacts of a run whose public state differs from the
    registry state (READY in the registry, but no verified store copy yet, or a store copy reported damaged). A view
    builder can overlay it (``state = overlay.get(id, row["state"])``); core-registered artifacts are not listed."""
    out: dict[str, str] = {}
    locs: dict[str, list[dict[str, Any]]] = {}
    for loc in conn.execute("SELECT * FROM artifact_locations WHERE run_id = ? AND object_kind = 'artifact'",
                            (run_id,)).fetchall():
        locs.setdefault(loc["object_id"], []).append({k: loc[k] for k in loc.keys()})
    for row in repo.list_artifacts(conn, run_id):
        rows = locs.get(row["id"], [])
        if _origin(rows) != "runner":
            continue
        state = _effective("runner", row.get("state"), _server_store_row(rows, hex_sha(row.get("sha256"))))
        if state != row.get("state"):
            out[row["id"]] = state
    return out


def checkpoint_states(conn: sqlite3.Connection, run_id: str) -> dict[str, str]:
    """``{checkpoint id: PRESENT | PRUNED | MISSING}`` from the recorded locations, for the checkpoints of a run that
    have location rows (registered by a runner, E6). A core-local ``os.path.isfile`` (the view of
    ``orchestration.retention.checkpoint_views``) cannot see a runner's disk or the gateway store. PRUNED = the row
    records the retention deletion and no verified store copy exists; PRESENT = a location is PRESENT (``runner_local``:
    the runner holds it, resumable there; ``server_store``: verified in the gateway store, downloadable); MISSING =
    every location reports it gone or damaged. Checkpoints without location rows (written on the core host) are not
    listed: the caller keeps its file check for them."""
    rows: dict[str, list[dict[str, Any]]] = {}
    for loc in conn.execute("SELECT * FROM artifact_locations WHERE run_id = ? AND object_kind = 'checkpoint'",
                            (run_id,)).fetchall():
        rows.setdefault(loc["object_id"], []).append({k: loc[k] for k in loc.keys()})
    out: dict[str, str] = {}
    for ck in conn.execute("SELECT id, sha256, state FROM checkpoints WHERE run_id = ?", (run_id,)).fetchall():
        locs = rows.get(ck["id"])
        if not locs:
            continue
        sha = hex_sha(ck["sha256"])
        live = [loc for loc in locs if loc.get("state") == "PRESENT" and hex_sha(loc.get("sha256")) == sha]
        if ck["state"] == "PRUNED":
            out[ck["id"]] = "PRESENT" if any(loc["store"] == SERVER_STORE for loc in live) else "PRUNED"
        else:
            out[ck["id"]] = "PRESENT" if live else "MISSING"
    return out


def report_server_store(conn: sqlite3.Connection, run_id: str, object_id: str, *, sha256: Any, size: Any,
                        state: str, verified_by: str = "store", now: str | None = None) -> dict[str, Any]:
    """Record the gateway's verdict about its store copy of ``object_id`` (inside the caller's transaction) and return
    ``{location_id, object, id, run_id, state (location), artifact_state (public), created}``.

    Refused (:class:`LocationError`): an unknown object of the run (404 MISSING), a state outside
    :data:`REPORTABLE_STATES` or a malformed digest / size (422 VALIDATION_FAILED), a PRESENT report whose digest or
    size differs from the record (409 LOCATION_CONFLICT: the gateway verified other bytes than the registered ones).
    Idempotent: the one ``server_store`` row of the object is updated in place."""
    if state not in REPORTABLE_STATES:
        raise LocationError(422, "VALIDATION_FAILED", f"state must be one of {', '.join(REPORTABLE_STATES)}")
    sha = hex_sha(sha256)
    if sha is None:
        raise LocationError(422, "VALIDATION_FAILED", "sha256 must be a hex SHA-256 digest")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        raise LocationError(422, "VALIDATION_FAILED", "bytes must be a non-negative integer")
    if not isinstance(verified_by, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", verified_by):
        raise LocationError(422, "VALIDATION_FAILED", "verified_by must be a short label")
    record = gateway_record(conn, run_id, object_id)
    if record is None:
        raise LocationError(404, "MISSING", "artifact not found in this run's registry")
    if record["sha256"] != sha:
        raise LocationError(409, "LOCATION_CONFLICT", "the reported digest is not the registered digest of this object")
    if state == "PRESENT" and size is not None and record.get("bytes") is not None and record["bytes"] != size:
        raise LocationError(409, "LOCATION_CONFLICT", "the reported size is not the registered size of this object")
    kind = record["object"]
    size = size if size is not None else (record.get("bytes") or 0)
    now = now or utcnow_iso()
    current = _server_store_row(repo.list_artifact_locations(conn, kind, object_id), sha)
    created = current is None
    if current is None:
        loc = repo.insert_artifact_location(
            conn, id=f"loc-{secrets.token_hex(8)}", object_kind=kind, object_id=object_id, run_id=run_id,
            store=SERVER_STORE, relpath=f"{sha[:2]}/{sha}", sha256=sha, bytes=int(size), verified_by=verified_by,
            state=state, now=now)
    else:
        conn.execute("UPDATE artifact_locations SET state = ?, bytes = ?, verified_by = ?, verified_at = ?, "
                     "updated_at = ? WHERE id = ?", (state, int(size), verified_by, now, now, current["id"]))
        loc = {**current, "state": state}
    after = gateway_record(conn, run_id, object_id) or record
    return {"location_id": loc["id"], "object": kind, "id": object_id, "run_id": run_id, "state": state,
            "artifact_state": after["state"], "created": created}

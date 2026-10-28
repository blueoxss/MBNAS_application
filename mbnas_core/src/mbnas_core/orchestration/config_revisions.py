"""Configuration revisions of candidate model YAMLs (F5d / DD-A23; contract 1.5.0 ``ModelConfigRevision``).

One ``config_revisions`` row (schema v5) per recorded revision of a candidate's model configuration:

* ``original``  - the uploaded / registered file of an explicit user-model training (the gateway-carried
  ``model_yaml_text`` + ``source_sha256``: core keeps the exact bytes; the upload store keeps them too under
  ``artifact_id = upl-<sha256>``; or the legacy owner-local ``model_path`` file);
* ``selected``  - the configuration an explicit training selection (or a final-retrain finalist) bound to the new trial
  (the ``architectures/<candidate>.yaml`` the selection wrote; ``artifact_id`` = its registered architecture artifact);
* ``executed``  - the configuration the trainer ran (``record_model_configuration[_from_report]``);
* ``generated`` - the configuration the proposer wrote (search proposals and generation-only candidates).

``source_sha256`` is the sha256 of the stored bytes (``text``), ``canonical_hash`` the ``yaml_hash`` of the canonical YAML
(``StructureReport.yaml_hash``) and ``canonical_sha256`` the sha256 of the canonical text, so a row records both digests.
``parent_revision_id`` is the revision this one was derived from (by default the candidate's latest revision; the source
candidate's latest one for a selection from another run; null for a first revision). Recording the same bytes again for
the same kind is a no-op (executed configurations are re-reported at every rung), so ``revision_id`` is content
derived (``rev-`` + 16 hex of sha256(run | candidate | kind | source_sha256)): stable, opaque, and no random id is drawn
(id sequences of the runs and candidates are unaffected). All writes run inside the caller's transaction; a failure to
record never blocks the operation that triggered it (callers log and continue).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from mbnas_contracts.util.time import utcnow_iso

KINDS = ("original", "selected", "executed", "generated")
_AUTO = object()
_HEX = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def normal_sha256(value: Any) -> str | None:
    """``sha256:<hex>`` form of a (possibly unprefixed) sha256 hex digest, else None."""
    m = _HEX.match(str(value or "").strip().lower())
    return f"sha256:{m.group(1)}" if m else None


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    out = dict(r)
    out["meta"] = json.loads(out.pop("meta_json") or "{}")
    return out


def list_revisions(conn: sqlite3.Connection, run_id: str, candidate_id: str) -> list[dict[str, Any]]:
    """Revisions of one candidate, oldest first."""
    rows = conn.execute("SELECT * FROM config_revisions WHERE run_id = ? AND candidate_id = ? ORDER BY created_at, rowid",
                        (run_id, candidate_id)).fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def get_revision(conn: sqlite3.Connection, run_id: str, candidate_id: str, revision_id: str) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM config_revisions WHERE id = ? AND run_id = ? AND candidate_id = ?",
                             (revision_id, run_id, candidate_id)).fetchone())


def latest_revision(conn: sqlite3.Connection, candidate_id: str, run_id: str | None = None) -> dict[str, Any] | None:
    sql = "SELECT * FROM config_revisions WHERE candidate_id = ?" + (" AND run_id = ?" if run_id else "")
    args: tuple[Any, ...] = (candidate_id, run_id) if run_id else (candidate_id,)
    return _row(conn.execute(sql + " ORDER BY created_at DESC, rowid DESC LIMIT 1", args).fetchone())


def record(conn: sqlite3.Connection, *, run_id: str, candidate_id: str, kind: str, text: str | None,
           source_sha256: str | None = None, canonical_hash: str | None = None, canonical_text: str | None = None,
           architecture_hash: str | None = None, artifact_id: str | None = None, parent_revision_id: Any = _AUTO,
           meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Record one revision (see module docstring); returns the stored (or the identical existing) row, or None when
    neither bytes nor their digest are known."""
    if kind not in KINDS:
        raise ValueError(f"unknown configuration revision kind {kind!r}")
    if not conn.in_transaction:
        raise RuntimeError("config revisions are recorded inside db.core.tx(conn)")
    sha = normal_sha256(source_sha256) if source_sha256 else (sha256_text(text) if isinstance(text, str) else None)
    if sha is None:
        return None
    if isinstance(text, str) and sha256_text(text) != sha:
        raise ValueError("revision text does not match source_sha256")
    latest = latest_revision(conn, candidate_id, run_id)
    same = conn.execute("SELECT * FROM config_revisions WHERE run_id = ? AND candidate_id = ? AND kind = ? "
                        "AND source_sha256 = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                        (run_id, candidate_id, kind, sha)).fetchone()
    if same is not None:
        return _row(same)
    parent = (latest or {}).get("id") if parent_revision_id is _AUTO else parent_revision_id
    rev_id = "rev-" + hashlib.sha256(f"{run_id}|{candidate_id}|{kind}|{sha}".encode("utf-8")).hexdigest()[:16]
    row = {"id": rev_id, "run_id": run_id, "candidate_id": candidate_id, "kind": kind,
           "source_sha256": sha, "canonical_hash": canonical_hash,
           "canonical_sha256": sha256_text(canonical_text) if isinstance(canonical_text, str) else None,
           "architecture_hash": architecture_hash, "parent_revision_id": parent, "artifact_id": artifact_id,
           "text": text if isinstance(text, str) else None,
           "meta_json": json.dumps(meta or {}, sort_keys=True, separators=(",", ":")), "created_at": utcnow_iso()}
    conn.execute(f"INSERT INTO config_revisions ({', '.join(row)}) VALUES ({', '.join(':' + k for k in row)})", row)
    return _row(conn.execute("SELECT * FROM config_revisions WHERE id = ?", (row["id"],)).fetchone())


def public_view(row: dict[str, Any]) -> dict[str, Any]:
    """Contract ``ModelConfigRevision`` of a revision row (the bytes are served by ``.../{revision_id}/yaml``)."""
    return {"revision_id": row["id"], "source_sha256": row["source_sha256"], "canonical_hash": row.get("canonical_hash"),
            "architecture_hash": row.get("architecture_hash"), "kind": row["kind"], "created_at": row["created_at"],
            "parent_revision_id": row.get("parent_revision_id"), "artifact_id": row.get("artifact_id")}


def try_record(conn: sqlite3.Connection, log: Any = None, **kwargs: Any) -> dict[str, Any] | None:
    """:func:`record` inside a SAVEPOINT; a failure is logged on the run (``log(conn, run_id, message, **fields)``) and
    swallowed, so recording a revision never blocks the operation that triggered it."""
    from mbnas_core.db.core import tx

    try:
        with tx(conn):
            return record(conn, **kwargs)
    except (ValueError, sqlite3.Error) as exc:
        if log is not None:
            log(conn, kwargs.get("run_id"), "configuration revision could not be recorded", level="warning",
                candidate_id=kwargs.get("candidate_id"), reason_code="CONFIG_REVISION_UNRECORDED",
                reason=type(exc).__name__)
        return None

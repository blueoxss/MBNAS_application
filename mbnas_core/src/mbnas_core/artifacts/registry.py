"""Artifact registry (reimplementation 2026-10-15; the original module was never committed).

재구현(2026-10-15), 원본 누락, 근거: api/routes_files.py (resolve_file / verify_signature / artifact_view 사용),
orchestration/engine.py (register_file / artifact_path), worker·api (run_dir), tests/api/test_artifacts_export.py
(접근 매트릭스), contracts/openapi.json (Artifact 스키마, "Never accepts filesystem paths").

Rules implemented here:

* Every artifact is a row of the ``artifacts`` table that stores a **run-relative** path (``relpath``). A request
  names an artifact by id only; the file is located via ``resolve_file`` and must resolve (symlinks followed) to a
  regular file below the run directory, which itself must sit below one of the server's output roots.
* ``register_file`` hashes the file, guesses its MIME type and stores the row as READY. Paths with ``..`` segments,
  paths outside the run directory and symlinks that leave it are refused with :class:`ArtifactError`.
* DD-A62 (c): a row with ``meta.location = core_local`` (:data:`CORE_LOCAL`; a generated architecture YAML that the
  owner worker of a split deployment wrote to the core-local run directory ``<core output_root>/<run id>``) is resolved
  against that directory only, with the registration guards (:func:`resolve_file`).
* Signed URLs carry ``exp`` (unix seconds) and ``sig`` = hex HMAC-SHA256(key, ``"<artifact_id>.<exp>"``). The
  signature binds the artifact id and the expiry only (the id is unique across runs; the route additionally looks
  the id up inside the run named in the path).
"""
from __future__ import annotations

import hashlib
import hmac
import mimetypes
import os
import sqlite3
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mbnas_core.db import repo
from mbnas_contracts.util.atomic import sha256_file
from mbnas_contracts.util.paths import is_within
from mbnas_contracts.util.time import format_iso

API_PREFIX = "/api/v1"
CORE_LOCAL = "core_local"
"""``meta.location`` of an artifact registered relative to the core-local run directory ``<core output_root>/<run id>``
(``owner_worker.paths.core_run_dir``) instead of ``run_dir(run)`` (DD-A62 (c),
``orchestration.engine.register_generated_architecture``); not a public metadata key."""
CORE_LOCAL_MAX_BYTES = 1024 * 1024
"""Size bound of a core-local generated YAML, at registration and when it is served."""
ARTIFACT_KINDS = ("gt", "tp", "fn", "fp", "pr_curve", "confusion_matrix", "architecture", "checkpoint", "onnx",
                  "tensorrt_engine", "other")
PUBLIC_META_KEYS = ("origin", "category", "filename", "stage", "candidate_id", "batch_index", "stage_epoch_start",
                    "stage_epoch_target", "epoch", "checkpoint_id", "identifier_note", "configuration_role",
                    "configuration_kind", "yaml_hash", "architecture_hash")
"""Scalar metadata keys an ``Artifact`` view may carry (also the gateway record of
:mod:`mbnas_core.artifacts.locations`; same list as ``mbnas_server.artifacts.signing.PUBLIC_META_KEYS``)."""


class ArtifactError(Exception):
    """An artifact cannot be registered or served. ``code`` is one of NOT_READY, LOAD_FAILED, MISSING,
    ACCESS_DENIED (routes_files.STATUS maps these to HTTP statuses); ``message`` is safe to show to the client.

    재구현(2026-10-15), 원본 누락, 근거: routes_files.py(``exc.code``/``exc.message``), engine._register_artifacts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def run_dir(run: Mapping[str, Any]) -> Path:
    """``<console.paths.output_root>/<run id>`` of a run row (or a ``{"id", "resolved_protocol"}`` shell).

    재구현(2026-10-15), 원본 누락, 근거: tests/api/test_artifacts_export.py::_file_of 가 같은 식으로 경로를
    계산하고, routes_runs.py 가 run 생성 전에 shell 매핑으로 호출한다. 디렉터리는 만들지 않는다.
    """
    try:
        out = run["resolved_protocol"]["console"]["paths"]["output_root"]
    except (KeyError, TypeError) as exc:
        raise ArtifactError("MISSING", "run has no resolved console.paths.output_root") from exc
    rid = str(run["id"])
    if not rid or rid in (".", "..") or "/" in rid or "\x00" in rid:
        raise ArtifactError("ACCESS_DENIED", f"unsafe run id {rid!r}")
    return Path(str(out)) / rid


def artifact_path(run_id: str, artifact_id: str) -> str:
    """API path of an artifact (no host, no query).

    재구현(2026-10-15), 원본 누락, 근거: engine._register_artifacts 가 view 의 ``url`` 로 저장하고, 테스트가
    ``/api/v1/runs/{run_id}/artifacts/{artifact_id}`` 로 요청한다.
    """
    return f"{API_PREFIX}/runs/{run_id}/artifacts/{artifact_id}"


def sign(key: bytes, artifact_id: str, exp: int) -> str:
    """Hex HMAC-SHA256 of ``"<artifact_id>.<exp>"``.

    재구현(2026-10-15), 원본 누락, 근거: tests/api/test_artifacts_export.py 가 ``sign(SIGNING_KEY.encode(), id, exp)``
    로 만료된 정상 서명을 만들어 410 EXPIRED 를 기대한다(64자 hex 위조 서명은 403).
    """
    return hmac.new(key, f"{artifact_id}.{int(exp)}".encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(key: bytes, artifact_id: str, exp: Any, sig: Any, *, now: float | None = None) -> str:
    """``"ok"``, ``"expired"`` (authentic but stale) or ``"invalid"`` (bad/missing parts, wrong signature).

    The signature is checked first so a forged signature is never reported as merely expired.

    재구현(2026-10-15), 원본 누락, 근거: routes_files._authorize (invalid→403 ACCESS_DENIED, expired→410 EXPIRED).
    """
    if exp is None or sig is None:
        return "invalid"
    try:
        exp_i = int(str(exp))
    except ValueError:
        return "invalid"
    # bytes, not str: hmac.compare_digest(str, str) raises TypeError for non-ASCII input (e.g. ``sig=%C3%A9``), which
    # reached the client as HTTP 500 instead of 403 (found by verification 2026-10-15)
    try:
        sig_b = str(sig).encode("utf-8")
    except UnicodeEncodeError:  # lone surrogate from a malformed percent-escape
        return "invalid"
    if not hmac.compare_digest(sign(key, artifact_id, exp_i).encode("ascii"), sig_b):
        return "invalid"
    return "ok" if exp_i > (time.time() if now is None else now) else "expired"


def _relpath_of(base: Path, candidate: Path) -> tuple[str, Path]:
    """(run-relative posix path, real file path) of ``candidate`` which must lie in ``base`` (symlink-aware)."""
    real_base = os.path.realpath(base)
    # the last component is kept unresolved so a symlink is judged by its target below, not silently followed here
    parent_real = os.path.realpath(candidate.parent)
    rel = os.path.relpath(os.path.join(parent_real, candidate.name), real_base)
    if rel == "." or rel.startswith(".."):
        raise ArtifactError("ACCESS_DENIED", "artifact path is outside the run directory")
    real = Path(os.path.realpath(candidate))
    if not is_within(str(real), real_base):
        raise ArtifactError("ACCESS_DENIED", "artifact path resolves outside the run directory")
    return Path(rel).as_posix(), real


def register_file(conn: sqlite3.Connection, run: Mapping[str, Any], *, kind: str, path: str,
                  candidate_id: str | None = None, evaluation_id: str | None = None, sample_id: str | None = None,
                  split_hash: str | None = None, mime_type: str | None = None, sha256: str | None = None,
                  meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Register an existing file of the run directory as a READY artifact and return its row.

    ``path`` is absolute or relative to the run directory. Raises :class:`ArtifactError` for ``..`` segments, paths
    outside the run directory, symlinks that leave it (ACCESS_DENIED), a missing file (MISSING), a ``sha256`` that
    disagrees with the bytes (LOAD_FAILED) or an unknown ``kind`` (ValueError). The id is derived from
    (run, relpath, evaluation), so registering the same file again for the same evaluation updates that row.
    Must be called inside the caller's transaction.

    재구현(2026-10-15), 원본 누락, 근거: engine._register_artifacts(호출·예외 처리), test_missing_modified_symlink_
    and_not_ready (심링크 탈출·`../../etc/passwd` 거부), db.repo.insert_artifact (relpath 규칙).
    """
    if kind not in ARTIFACT_KINDS:
        raise ValueError(f"unknown artifact kind {kind!r}")
    raw = str(path)
    if not raw or "\x00" in raw:
        raise ArtifactError("ACCESS_DENIED", "empty or NUL-containing artifact path")
    if ".." in raw.replace("\\", "/").split("/"):
        raise ArtifactError("ACCESS_DENIED", "'..' segments are not accepted in artifact paths")
    base = run_dir(run)
    candidate = Path(raw) if os.path.isabs(raw) else base / raw
    relpath, real = _relpath_of(base, candidate)
    if not real.is_file():
        raise ArtifactError("MISSING", f"artifact file does not exist: {relpath}")
    digest = sha256_file(real)
    if sha256 and sha256 != digest:
        raise ArtifactError("LOAD_FAILED", f"sha256 of {relpath} differs from the reported value")
    mime = mime_type or mimetypes.guess_type(real.name)[0] or "application/octet-stream"
    art_id = "art-" + hashlib.sha256(f"{run['id']}\0{relpath}\0{evaluation_id or ''}".encode()).hexdigest()[:20]
    size = real.stat().st_size
    if repo.get_artifact(conn, run["id"], art_id) is not None:
        return repo.update_artifact(conn, run["id"], art_id, state="READY", sha256=digest, bytes=size, mime_type=mime)
    return repo.insert_artifact(conn, id=art_id, run_id=run["id"], kind=kind, relpath=relpath,
                                evaluation_id=evaluation_id, candidate_id=candidate_id, sha256=digest,
                                mime_type=mime, bytes=size, sample_id=sample_id, split_hash=split_hash,
                                state="READY", meta=meta or {})


def _core_local_base(run: Mapping[str, Any], core_output_root: str | None) -> Path:
    """``<core_output_root>/<run id>`` (the layout of ``owner_worker.paths.core_run_dir``)."""
    rid = str(run["id"])
    if not rid or rid in (".", "..") or "/" in rid or "\x00" in rid:
        raise ArtifactError("ACCESS_DENIED", f"unsafe run id {rid!r}")
    if not core_output_root:
        raise ArtifactError("MISSING", "core-local artifact: no core output root is configured to resolve it")
    return Path(str(core_output_root)) / rid


def _core_local_guards(rel: str, path: Path, real: str, row: Mapping[str, Any]) -> None:
    """The registration guards of a core-local generated YAML, applied again when it is served: relpath
    ``architectures/<name>.yaml``, the file itself is no symlink, at most :data:`CORE_LOCAL_MAX_BYTES`, and its bytes
    still hash to the registered ``sha256`` (byte identity with what was registered = the candidate's recorded YAML)."""
    parts = rel.split("/")
    if len(parts) != 2 or parts[0] != "architectures" or not parts[1].endswith(".yaml"):
        raise ArtifactError("ACCESS_DENIED", "a core-local artifact is a generated architecture YAML only")
    if os.path.islink(path):
        raise ArtifactError("ACCESS_DENIED", "a core-local artifact is never served through a symlink")
    if os.path.getsize(real) > CORE_LOCAL_MAX_BYTES:
        raise ArtifactError("LOAD_FAILED", "core-local artifact exceeds its size bound")
    if not row.get("sha256") or sha256_file(real) != row["sha256"]:
        raise ArtifactError("LOAD_FAILED", "core-local artifact bytes no longer match the registered sha256")


def resolve_file(run: Mapping[str, Any], row: Mapping[str, Any], output_roots: Sequence[str], *,
                 core_output_root: str | None = None) -> Path:
    """Real path of a registered artifact's file, or :class:`ArtifactError`.

    NOT_READY rows are refused before the disk is touched; the run directory must lie below an output root; the
    file must resolve below the run directory (symlink escape → ACCESS_DENIED) and exist (else MISSING).
    DD-A62 (c): a ``meta.location = core_local`` row is resolved against ``<core_output_root>/<run id>`` ONLY (never
    ``run_dir(run)``; without ``core_output_root`` it is MISSING), with the same checks plus the registration guards
    (:func:`_core_local_guards`); every other row is resolved against ``run_dir(run)`` only, as before.

    재구현(2026-10-15), 원본 누락, 근거: routes_files.get_artifact (STATUS 매핑)과 tests/api/test_artifacts_export.py
    (a-pending→409 NOT_READY, 삭제→404 MISSING, 심링크→403 이고 본문에 secret 없음).
    """
    if row.get("state") == "NOT_READY":
        raise ArtifactError("NOT_READY", "artifact is not ready yet")
    rel = str(row.get("relpath") or "")
    if (not rel or os.path.isabs(rel) or "\x00" in rel
            or ".." in rel.replace("\\", "/").split("/")):
        raise ArtifactError("ACCESS_DENIED", "registry holds an unsafe artifact path")
    meta = row.get("meta") if isinstance(row.get("meta"), Mapping) else {}
    core_local = meta.get("location") == CORE_LOCAL
    base = _core_local_base(run, core_output_root) if core_local else run_dir(run)
    real_base = os.path.realpath(base)
    if not any(is_within(real_base, os.path.realpath(r)) for r in output_roots):
        raise ArtifactError("ACCESS_DENIED", "run directory is outside the server output roots")
    real = os.path.realpath(base / rel)
    if not is_within(real, real_base):
        raise ArtifactError("ACCESS_DENIED", "artifact resolves outside the run directory")
    if not os.path.isfile(real):
        raise ArtifactError("MISSING", "artifact file is missing on the server")
    if core_local:
        _core_local_guards(rel, base / rel, real, row)
    return Path(real)


def artifact_view(row: Mapping[str, Any], *, base_url: str, key: bytes | None, ttl_s: int,
                  now: float | None = None) -> dict[str, Any]:
    """Contract ``Artifact`` view of a registry row with a fresh signed URL when ``key`` is set.

    READY artifacts with a signing key get ``auth_mode=signed_url`` (``?exp=&sig=``, ``expires_at`` = now + ttl,
    ``refresh_url``); everything else is ``bearer_fetch`` with the bare API path.

    재구현(2026-10-15), 원본 누락, 근거: api/views.evaluation_views, routes_files.refresh_artifact,
    contracts/openapi.json Artifact(auth_mode, expires_at, refresh_url, matching), test_signed_and_bearer_access.
    """
    t = time.time() if now is None else now
    path = artifact_path(str(row["run_id"]), str(row["id"]))
    base = base_url.rstrip("/")
    signed = key is not None and row.get("state") == "READY"
    if signed:
        exp = int(t) + int(ttl_s)
        url = f"{base}{path}?exp={exp}&sig={sign(key, str(row['id']), exp)}"
        expires_at: str | None = format_iso(datetime.fromtimestamp(exp, tz=timezone.utc))
    else:
        url, expires_at = f"{base}{path}", None
    meta = row.get("meta") or {}
    public_meta = {k: meta[k] for k in PUBLIC_META_KEYS if k in meta and
                   (meta[k] is None or isinstance(meta[k], (str, int, float, bool)))}
    return {"id": row["id"], "kind": row["kind"], "url": url, "auth_mode": "signed_url" if signed else "bearer_fetch",
            "expires_at": expires_at, "sample_id": row.get("sample_id"), "split_hash": row.get("split_hash"),
            "sha256": row.get("sha256"), "mime_type": row.get("mime_type"), "state": row.get("state"),
            "refresh_url": f"{base}{path}/refresh" if signed else None, "evaluation_id": row.get("evaluation_id"),
            "matching": meta.get("matching") or None, "metadata": public_meta}

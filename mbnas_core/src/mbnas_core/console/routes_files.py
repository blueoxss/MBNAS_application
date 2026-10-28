"""Artifacts (registry-mediated bytes, signed URLs) and the run export bundle.

``GET /runs/{rid}/artifacts/{aid}`` accepts either the caller's credential or a valid signed query (``exp``, ``sig``;
see ``artifacts.registry``). It looks the artifact up **by id in the run's registry** — the request never carries
a filesystem path — and answers 200 bytes (+ ``X-Artifact-Sha256``), 401/403 ACCESS_DENIED, 404 MISSING,
409 NOT_READY / LOAD_FAILED (bytes no longer match the registry) or 410 EXPIRED (authentic but stale signed URL).
It serves only files of the core's own run directories (``origin = core``: the owner worker / all-in-one
profile; also the generated architecture YAMLs a split deployment keeps in the core-local run directory,
``meta.location = core_local``, resolved against ``settings.output_root``); runner-produced bytes live in the gateway's
artifact store and are served by the gateway. For an
artifact a runner registered (a ``runner_local`` location) it answers 404 ``ARTIFACT_NOT_READY``
without looking at its disk and without changing the row (a core-local lookup would flip such rows to
MISSING).

Routes for the gateway (scope ``server``/``admin`` on ``/core/v1/console``; static bearer on the legacy mount):

* ``GET /runs/{rid}/artifacts/{id}/record`` - the run-scoped record of an artifact or, failing that, a checkpoint of
  the run (:func:`mbnas_core.artifacts.locations.gateway_record`: digest, size, media type, public metadata, ``origin``
  runner|core, ``state`` from the locations). With ``exp``/``sig`` the signed query is verified first with the core's
  signing key (403 ACCESS_DENIED / 410 EXPIRED before any lookup, so a guessed id gives no existence signal): the
  gateway delegates URLs that the core signed in its views. 404 MISSING for an id the run does not have.
* ``POST /artifacts/{id}/locations`` - body ``{run_id, sha256, bytes, state: PRESENT|LOAD_FAILED|MISSING, store:
  "server_store", verified_by?}``: the gateway's verdict about its store copy after a download re-hash
  (:func:`mbnas_core.artifacts.locations.report_server_store`; PRESENT makes a runner object READY).
* ``GET /runs/{rid}/export-manifest`` - the export bundle's text members (the same ``bundle_files`` output as ``GET
  /runs/{rid}/export``, without ``SHA256SUMS``) plus ``objects``: the registered architecture YAMLs and evaluation
  images (id, kind, digest, size, origin, member name) whose bytes the gateway adds from its store (or, for a core
  object, from the download route above) when it assembles the zip.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from mbnas_core.artifacts import locations
from mbnas_core.artifacts.registry import ArtifactError, artifact_view, resolve_file, verify_signature
from mbnas_core.db import repo
from mbnas_core.db.core import tx
from mbnas_core.export.bundle import build_zip, bundle_files
from mbnas_model_runtime.provenance import env_lock
from mbnas_contracts.schemas.records import Artifact
from mbnas_contracts.util.atomic import sha256_file
from mbnas_contracts.util.canon import sha256_hex
from mbnas_core.api.auth import bearer_ok
from mbnas_core.console.deps import (
    _bind_request, bind_legacy_scope, console_principal, contract_json, db, get_run_or_404, legacy_principal, services,
)
from mbnas_core.runner_api import auth as principal_auth
from mbnas_contracts.errors import ApiError
from mbnas_core.console.services import AppServices
from mbnas_core.console.views import signing_key

def _recorded_lock() -> dict[str, Any] | None:
    """Full ``environment.lock.json`` recorded by provenance.env_lock (package/driver/GPU versions), if present."""
    try:
        return json.loads(Path(env_lock.DEFAULT_LOCK_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
STATUS = {"NOT_READY": 409, "LOAD_FAILED": 409, "MISSING": 404, "ACCESS_DENIED": 403, "EXPIRED": 410}
EXPORT_MANIFEST_FORMAT = "mbnas.export-manifest/1"
EXPORT_OBJECT_KINDS = {"architecture": "architectures", "gt": "images", "tp": "images", "fn": "images", "fp": "images",
                       "pr_curve": "images", "confusion_matrix": "images"}
"""Artifact kinds whose bytes the gateway may add to the export zip, and their member directory
(``architectures/*.yaml`` always, images on request)."""
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/svg+xml": ".svg", "image/webp": ".webp",
               "application/x-yaml": ".yaml", "application/yaml": ".yaml", "text/yaml": ".yaml",
               "application/json": ".json", "text/plain": ".txt"}
LOCATION_BODY_KEYS = frozenset({"run_id", "sha256", "bytes", "state", "store", "verified_by"})


def _credential(request: Request, conn: sqlite3.Connection, mode: str) -> None:
    """``legacy``: the static bearer token; ``console``: a scoped principal of kind server|admin."""
    if mode == "legacy":
        bearer_ok(request)
        return
    p = principal_auth.authenticate(conn, principal_auth.bearer_token(request))
    if p["kind"] not in principal_auth.SCOPE_ACCEPTS["server"]:
        raise ApiError(403, "ACCESS_DENIED", f"token scope {p['kind']} cannot use this route")
    _bind_request(request, "admin" if p["kind"] == "admin" else "server")


def _authorize(request: Request, svc: AppServices, artifact_id: str, exp: str | None, sig: str | None,
               conn: sqlite3.Connection, mode: str = "legacy") -> str:
    if exp is None and sig is None:
        _credential(request, conn, mode)
        return "bearer"
    key = signing_key(svc)
    verdict = "invalid" if key is None else verify_signature(key, artifact_id, exp, sig)
    if verdict == "expired":
        raise ApiError(410, "EXPIRED", "signed artifact URL expired; refresh it")
    if verdict != "ok":
        raise ApiError(403, "ACCESS_DENIED", "invalid artifact signature")
    return "signed_url"


def _bundle(conn: sqlite3.Connection, run_id: str, svc: AppServices) -> dict[str, str]:
    """Text members of the run's export bundle (inside the caller's read transaction); one place for both the zip
    route and the gateway's export manifest, so both always carry the same members."""
    src = svc.source()
    return bundle_files(conn, run_id, manifest_dir=f"{svc.settings.output_root}/manifests",
                        environment={"api_process": svc.versions(), "recorded_environment_lock": _recorded_lock()},
                        provenance={"source_tree": {k: src.get(k) for k in ("algorithm", "sha256", "files")},
                                    "bundle_format": "mbnas.export/1"})


def _member_name(kind: str, artifact_id: str, mime: Any) -> str:
    ext = _EXTENSIONS.get(str(mime or "").split(";")[0].strip().lower(), "")
    if kind == "architecture":
        ext = ".yaml"
    return f"{EXPORT_OBJECT_KINDS[kind]}/{artifact_id}{ext or '.bin'}"


def _export_objects(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """Gateway-fetchable objects of the export (digest-addressed; no path)."""
    out = []
    for row in repo.list_artifacts(conn, run_id):
        if row["kind"] not in EXPORT_OBJECT_KINDS:
            continue
        rec = locations.artifact_record(row, repo.list_artifact_locations(conn, "artifact", row["id"]))
        if rec["sha256"] is None:
            continue
        out.append({"name": _member_name(row["kind"], row["id"], row.get("mime_type")), "id": row["id"],
                    "kind": row["kind"], "candidate_id": row.get("candidate_id"),
                    "evaluation_id": row.get("evaluation_id"), "sha256": rec["sha256"], "bytes": rec["bytes"],
                    "mime_type": row.get("mime_type"), "origin": rec["origin"], "state": rec["state"]})
    return out


def _location_body(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ApiError(422, "VALIDATION_FAILED", "location report must be a JSON object")
    unknown = sorted(str(k) for k in body if k not in LOCATION_BODY_KEYS)
    if unknown:
        raise ApiError(422, "VALIDATION_FAILED", "unknown location report keys",
                       errors=[{"field": k[:64], "code": "FIELD_UNKNOWN", "message": "not accepted"} for k in unknown[:20]])
    if body.get("store", locations.SERVER_STORE) != locations.SERVER_STORE:
        raise ApiError(422, "VALIDATION_FAILED", "only the gateway store (server_store) can be reported here")
    if not isinstance(body.get("run_id"), str) or not body["run_id"]:
        raise ApiError(422, "VALIDATION_FAILED", "run_id is required",
                       errors=[{"field": "run_id", "code": "FIELD_REQUIRED", "message": "run id of the object"}])
    return body


def build_router(mode: str) -> APIRouter:
    """Artifact/export routes for one mount: ``console`` (``/core/v1/console``, scoped principals) or ``legacy``
    (``/api/v1`` owner debug mount, static bearer token)."""
    if mode not in ("console", "legacy"):
        raise ValueError(mode)
    router = APIRouter()
    guard = console_principal if mode == "console" else legacy_principal

    async def scope_dep(request: Request) -> None:  # signed-URL downloads: bind the default (server) projection
        await bind_legacy_scope(request)

    @router.get("/runs/{run_id}/artifacts/{artifact_id}", dependencies=[Depends(scope_dep)])
    def get_artifact(request: Request, run_id: str, artifact_id: str, exp: str | None = Query(None),
                     sig: str | None = Query(None), conn: sqlite3.Connection = Depends(db),
                     svc: AppServices = Depends(services)):
        _authorize(request, svc, artifact_id, exp, sig, conn, mode)
        row = repo.get_artifact(conn, run_id, artifact_id)
        if row is None:
            raise ApiError(404, "MISSING", "artifact not found in this run's registry")
        if locations.is_runner_object(repo.list_artifact_locations(conn, "artifact", artifact_id)):
            # a runner's bytes are never on this host. No disk lookup, and above all no
            # MISSING / LOAD_FAILED flip of the row (the gateway serves the bytes from its artifact store).
            raise ApiError(404, "ARTIFACT_NOT_READY", "this artifact was registered by a runner: the gateway serves "
                                                      "its bytes from the artifact store, the core host has none")
        run = get_run_or_404(conn, run_id)
        try:
            path = resolve_file(run, row, svc.settings.output_roots, core_output_root=svc.settings.output_root)
            digest = sha256_file(path)
            if row.get("sha256") and digest != row["sha256"]:
                raise ArtifactError("LOAD_FAILED", "artifact bytes no longer match the registry sha256")
        except ArtifactError as exc:
            if exc.code in ("MISSING", "LOAD_FAILED") and row["state"] == "READY":
                with tx(conn):
                    repo.update_artifact(conn, run_id, artifact_id, state=exc.code)
            raise ApiError(STATUS.get(exc.code, 409), exc.code, exc.message) from exc
        return FileResponse(path, media_type=row.get("mime_type") or "application/octet-stream",
                            headers={"X-Artifact-Sha256": digest, "Cache-Control": "private, no-store",
                                     "X-Content-Type-Options": "nosniff"})

    @router.get("/runs/{run_id}/artifacts/{artifact_id}/record", dependencies=[Depends(guard)])
    def artifact_record(run_id: str, artifact_id: str, exp: str | None = Query(None), sig: str | None = Query(None),
                        conn: sqlite3.Connection = Depends(db), svc: AppServices = Depends(services)):
        if exp is not None or sig is not None:  # a URL the core signed in a view: verified before any lookup
            key = signing_key(svc)
            verdict = "invalid" if key is None else verify_signature(key, artifact_id, exp, sig)
            if verdict == "expired":
                raise ApiError(410, "EXPIRED", "signed artifact URL expired; refresh it")
            if verdict != "ok":
                raise ApiError(403, "ACCESS_DENIED", "invalid artifact signature")
        with tx(conn, immediate=False):
            record = locations.gateway_record(conn, run_id, artifact_id)
        if record is None:
            raise ApiError(404, "MISSING", "artifact not found in this run's registry")
        return JSONResponse(record, headers={"Cache-Control": "no-store"})

    @router.post("/artifacts/{artifact_id}/locations", dependencies=[Depends(guard)])
    def report_location(artifact_id: str, body: Any = Body(...), conn: sqlite3.Connection = Depends(db)):
        body = _location_body(body)
        try:
            with tx(conn):
                out = locations.report_server_store(conn, body["run_id"], artifact_id, sha256=body.get("sha256"),
                                                    size=body.get("bytes"), state=body.get("state"),
                                                    verified_by=body.get("verified_by") or "store")
        except locations.LocationError as exc:
            raise ApiError(exc.status, exc.code, exc.message) from exc
        return JSONResponse(out)

    @router.get("/runs/{run_id}/export-manifest", dependencies=[Depends(guard)])
    def export_manifest(run_id: str, conn: sqlite3.Connection = Depends(db), svc: AppServices = Depends(services)):
        with tx(conn, immediate=False):
            get_run_or_404(conn, run_id)
            files = _bundle(conn, run_id, svc)
            objects = _export_objects(conn, run_id)
        files.pop("SHA256SUMS", None)  # the gateway recomputes it over every member it writes
        return JSONResponse({"format": EXPORT_MANIFEST_FORMAT, "run_id": run_id, "filename": f"{run_id}-export.zip",
                             "members": files, "objects": objects}, headers={"Cache-Control": "no-store"})

    @router.post("/runs/{run_id}/artifacts/{artifact_id}/refresh", dependencies=[Depends(guard)])
    def refresh_artifact(request: Request, run_id: str, artifact_id: str, conn: sqlite3.Connection = Depends(db),
                         svc: AppServices = Depends(services)):
        row = repo.get_artifact(conn, run_id, artifact_id)
        if row is None:
            raise ApiError(404, "MISSING", "artifact not found in this run's registry")
        view = artifact_view(row, base_url=str(request.base_url), key=signing_key(svc), ttl_s=svc.settings.signed_url_ttl_s)
        return contract_json("Artifact", Artifact, view)

    @router.get("/runs/{run_id}/export", dependencies=[Depends(guard)])
    def export_run(run_id: str, conn: sqlite3.Connection = Depends(db), svc: AppServices = Depends(services)):
        with tx(conn, immediate=False):
            get_run_or_404(conn, run_id)
            files = _bundle(conn, run_id, svc)
        data = build_zip(files)
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{run_id}-export.zip"', "X-Bundle-Sha256": sha256_hex(data),
            "Cache-Control": "no-store"})

    return router

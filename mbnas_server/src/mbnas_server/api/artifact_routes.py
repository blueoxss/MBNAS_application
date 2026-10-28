"""Artifact bytes, signed-URL refresh, the export zip and the configuration revisions of the gateway.

Runner-produced bytes live in the gateway's content-addressed artifact store (``app.state.artifact_store``,
:class:`mbnas_server.artifacts.store.ArtifactStore`, written by the runner's ``local`` transfer); the core only
knows ids, digests and locations. The core answers the run-scoped record (``GET <core>/runs/{run}/artifacts/{id}/record``:
artifact or checkpoint of the run, digest, size, media type, public metadata, ``origin`` runner|core, ``state``).

``GET /api/v1/runs/{run_id}/artifacts/{artifact_id}`` (bearer or signed query):

1. authorization: without ``exp``/``sig`` the bearer token (401/403); with them the gateway's signature
   (:func:`mbnas_server.artifacts.signing.verify_signature`, key ``artifact_signing_key``): valid -> authorized,
   authentic but stale -> 410 ``EXPIRED``; any other verdict (including a gateway without a key) is delegated to the
   core, which verifies URLs signed in its own views with its key before any lookup (403 / 410), so URLs of core views
   keep working when the two services have different keys and a guessed id never gives an existence signal;
2. the record (a 404 ``MISSING`` of the core is relayed); an artifact id or a checkpoint id of the run;
3. the store holds the digest -> :func:`mbnas_server.artifacts.download.serve_artifact` streams it with the digest
   re-check (409 ``LOAD_FAILED`` on a mismatch, ``X-Artifact-Sha256`` / ``X-Artifact-Verification``). A completely
   verified download is reported to the core once (``POST <core>/artifacts/{id}/locations``, PRESENT@server_store,
   ``verified_by=store``; a mismatch is reported as LOAD_FAILED), best effort after the response;
4. not in the store: an artifact the core registered from its own run directory (``origin = core``: owner worker /
   all-in-one, core-written architecture YAMLs) is relayed from the core's download route (its MISSING / NOT_READY /
   LOAD_FAILED / ACCESS_DENIED answers unchanged); anything else is 404 ``ARTIFACT_NOT_READY`` (registered by a runner,
   not transferred yet). Checkpoints are only ever served from the store.

``POST .../refresh`` renders the contract ``Artifact`` view with the gateway's key and TTL
(:func:`mbnas_server.artifacts.signing.artifact_view`); a runner object counts as READY once its digest is in the
store. ``GET /api/v1/runs/{run_id}/export`` assembles the zip from the core's export manifest (the text members) plus
the bytes of every architecture YAML (``architectures/<artifact id>.yaml``; images too with ``?include=images``) taken
from the store or, for core objects, from the core; ``export_objects.json`` lists every object with its digest and
whether it was included; the split manifests the core does not hold (validated on this host) are added
from the gateway's manifest cache (``<manifest_cache_root>/manifests/<hex>.json``, only for the run's own split hashes)
in the public export form of the core (``export.bundle._public_manifest``: no ``roots``, samples without image/label
paths, issue texts without absolute paths);
``SHA256SUMS`` covers all members; the archive is deterministic (sorted names, fixed timestamps) and carries
``X-Bundle-Sha256``. The two configuration-revision routes (contract 1.5.0) are relayed; when the core holds no bytes
of a revision (404 ``ARTIFACT_NOT_READY``) the gateway serves them by the revision's ``source_sha256`` from its own
stores - the model-config upload store (``uploads/<sha256>.yaml``, ``artifact_id = upl-<sha256>``) or the
content-addressed artifact store - after re-hashing them (409 ``LOAD_FAILED`` when they no longer match).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import zipfile
from collections.abc import AsyncIterator, Mapping
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from mbnas_contracts.errors import ErrorLocation
from mbnas_contracts.schemas.records import Artifact
from mbnas_server.api.auth import bearer_ok
from mbnas_server.api.core_client import CoreClient
from mbnas_server.api.deps import contract_json
from mbnas_server.api.errors import GatewayError
from mbnas_server.api.path_guard import segment
from mbnas_server.api.relay import relay
from mbnas_server.artifacts.download import BASE_HEADERS, ArtifactBody, serve_artifact
from mbnas_server.artifacts.signing import SignatureError, artifact_view, signing_key, verify_signature
from mbnas_server.artifacts.store import ArtifactStore, StoreError, is_sha256
from mbnas_server.config import GatewaySettings

log = logging.getLogger(__name__)

router = APIRouter()  # under the bearer-protected /api/v1 router
open_router = APIRouter()  # artifact bytes: bearer or signed URL, decided in the route

RECORD_OBJECTS = ("artifact", "checkpoint")
EXPORT_FORMAT = "mbnas.export-manifest/1"
EXPORT_IMAGE_KINDS = frozenset({"gt", "tp", "fn", "fp", "pr_curve", "confusion_matrix"})
EXPORT_INDEX = "export_objects.json"
EXPORT_OBJECTS_MAX_BYTES = 512 << 20
"""Total bytes of the objects one export zip carries (the zip is built in memory); further objects are listed in
``export_objects.json`` with ``reason = EXPORT_SIZE_LIMIT``."""
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)


# ------------------------------------------------------------------------------------------------ helpers
def _core(request: Request) -> CoreClient:
    return request.app.state.core


def _settings(request: Request) -> GatewaySettings:
    return request.app.state.settings


def _store(request: Request) -> ArtifactStore:
    return request.app.state.artifact_store


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _hex(value: Any) -> str | None:
    """Lower-case hex digest of a record value (``sha256:`` prefix removed), or None."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    text = text[7:] if text.startswith("sha256:") else text
    return text if is_sha256(text) else None


def _bad_core(message: str) -> GatewayError:
    return GatewayError(502, "CORE_BAD_RESPONSE", message, location=ErrorLocation.CORE)


def _not_ready(record: Mapping[str, Any]) -> StoreError:
    return StoreError("ARTIFACT_NOT_READY", f"{record.get('object', 'artifact')} {record.get('id')} of run "
                                            f"{record.get('run_id')} is not in the artifact store yet (not transferred)")


async def fetch_record(request: Request, run_id: str, artifact_id: str,
                       signed: dict[str, str] | None = None) -> dict[str, Any]:
    """The core's run-scoped record of ``artifact_id`` (an artifact or a checkpoint), digest normalised to hex.
    ``signed`` = ``{exp, sig}`` the core must verify first (URLs signed in core views)."""
    resp = await _core(request).request("GET", f"/runs/{segment(run_id)}/artifacts/{segment(artifact_id)}/record",
                                        request_id=_rid(request), params=signed or None)
    try:
        record = json.loads(resp.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _bad_core("core artifact record is not JSON") from exc
    if (not isinstance(record, dict) or record.get("object") not in RECORD_OBJECTS
            or str(record.get("id")) != artifact_id or str(record.get("run_id")) != run_id
            or not isinstance(record.get("kind"), str)):
        raise _bad_core("core artifact record does not describe the requested object")
    record["sha256"] = _hex(record.get("sha256"))
    size = record.get("bytes")
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        record["bytes"] = None
    return record


async def report_location(core: CoreClient, record: Mapping[str, Any], state: str, size: int | None,
                          request_id: str | None) -> None:
    """Best effort ``POST <core>/artifacts/{id}/locations`` (never raises: a download never fails because of it)."""
    body = {"run_id": record["run_id"], "sha256": record["sha256"], "bytes": size if size is not None else record.get(
        "bytes"), "state": state, "store": "server_store", "verified_by": "store"}
    try:
        await core.request("POST", f"/artifacts/{segment(str(record['id']))}/locations", request_id=request_id,
                           json_body=body)
    except Exception as exc:  # noqa: BLE001
        log.warning("artifact location report %s for %s failed: %s", state, record.get("id"),
                    getattr(exc, "code", None) or type(exc).__name__)


async def _next_chunk(body: ArtifactBody) -> bytes | None:
    return await run_in_threadpool(next, body, None)


def _stream(request: Request, record: dict[str, Any], headers: dict[str, str], body: ArtifactBody) -> Response:
    core, request_id = _core(request), _rid(request)
    size = int(headers["Content-Length"])

    async def chunks() -> AsyncIterator[bytes]:
        try:
            while True:
                block = await _next_chunk(body)
                if block is None:
                    break
                yield block
        except StoreError as exc:  # trailing mismatch of a streamed object (download.py: limitation)
            if exc.code == "LOAD_FAILED":
                await report_location(core, record, "LOAD_FAILED", size, request_id)
            raise
        finally:
            body.close()

    background = None
    if record.get("server_store") != "PRESENT":  # report the first complete, verified download only
        background = BackgroundTask(report_location, core, record, "PRESENT", size, request_id)
    return StreamingResponse(chunks(), headers=headers, background=background)


async def _from_core(request: Request, record: Mapping[str, Any]) -> Response:
    """Bytes of a core object (its own download route, service token); the digest is re-checked here too."""
    path = f"/runs/{segment(str(record['run_id']))}/artifacts/{segment(str(record['id']))}"
    resp = await _core(request).request("GET", path, request_id=_rid(request))
    data = resp.content
    digest = hashlib.sha256(data).hexdigest()
    if record.get("sha256") and digest != record["sha256"]:
        raise StoreError("LOAD_FAILED", f"artifact {record['id']}: the bytes core served do not match the recorded "
                                        "sha256")
    headers = {**BASE_HEADERS, "X-Artifact-Sha256": digest, "X-Artifact-Verification": "core"}
    ctype = resp.headers.get("content-type") or "application/octet-stream"
    return Response(data, status_code=resp.status_code, headers=headers, media_type=ctype)


# ------------------------------------------------------------------------------------------------ download
@open_router.get("/runs/{run_id}/artifacts/{artifact_id}")
async def download(request: Request, run_id: str, artifact_id: str) -> Response:
    settings = _settings(request)
    q = request.query_params
    signed: dict[str, str] | None = None
    if q.get("exp") is None and q.get("sig") is None:
        bearer_ok(request)
    else:
        key = signing_key(settings)
        verdict = "invalid" if key is None else verify_signature(key, artifact_id, q.get("exp"), q.get("sig"))
        if verdict == "expired":
            raise SignatureError(410, "EXPIRED", "signed artifact URL expired; refresh it")
        if verdict != "ok":  # maybe signed by the core in one of its views: the core verifies it (403/410)
            signed = {k: str(q.get(k)) for k in ("exp", "sig") if q.get(k) is not None}
    record = await fetch_record(request, run_id, artifact_id, signed)
    store = _store(request)
    if record["sha256"] is not None:
        try:
            headers, body = await run_in_threadpool(
                serve_artifact, store, record, verify_first_max_bytes=settings.artifact_verify_first_max_bytes)
        except StoreError as exc:
            if exc.code == "LOAD_FAILED":
                await report_location(_core(request), record, "LOAD_FAILED", None, _rid(request))
            if exc.code != "ARTIFACT_NOT_READY":
                raise
        else:
            return _stream(request, record, headers, body)
    if record["object"] == "artifact" and record.get("origin") == "core":
        return await _from_core(request, record)
    raise _not_ready(record)


@router.post("/runs/{run_id}/artifacts/{artifact_id}/refresh")
async def refresh(request: Request, run_id: str, artifact_id: str) -> Response:
    settings = _settings(request)
    record = await fetch_record(request, run_id, artifact_id)
    view_row = dict(record)
    if record.get("state") != "READY" and record["sha256"] is not None:
        stored = await run_in_threadpool(_store(request).get, record["sha256"])
        if stored is not None and (record.get("bytes") is None or stored.bytes == record["bytes"]):
            view_row["state"] = "READY"  # in the store: downloadable (re-verified when it is downloaded)
    view = artifact_view(view_row, base_url=str(request.base_url), key=signing_key(settings),
                         ttl_s=settings.signed_url_ttl_s, run_id=run_id)
    return contract_json("Artifact", Artifact, view)


# ------------------------------------------------------------------------------------------------ export
def build_zip(files: Mapping[str, bytes]) -> bytes:
    """Deterministic zip (sorted names, fixed timestamps, deflate, mode 0644) - the core's ``export.bundle`` layout."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[name])
    return buf.getvalue()


def _member_ok(name: Any) -> bool:
    return (isinstance(name, str) and 0 < len(name) <= 255 and not name.startswith("/") and "\\" not in name
            and "\x00" not in name and ".." not in name.split("/"))


def _read_verified(store: ArtifactStore, sha: str, limit: int) -> tuple[bytes | None, str | None]:
    """Bytes of a stored object when its digest still matches (``(None, reason)`` otherwise)."""
    stored = store.get(sha)
    if stored is None:
        return None, "ARTIFACT_NOT_READY"
    if stored.bytes > limit:
        return None, "TOO_LARGE_FOR_EXPORT"
    try:
        with store.open_stream(sha) as fh:
            data = fh.read(limit + 1)
    except StoreError as exc:
        return None, exc.code
    if len(data) > limit:
        return None, "TOO_LARGE_FOR_EXPORT"
    if hashlib.sha256(data).hexdigest() != sha:
        return None, "LOAD_FAILED"
    return data, None


SPLIT_HASH_KEYS = ("train_manifest_hash", "search_validation_manifest_hash", "final_evaluation_manifest_hash")
_ABS_PATH = re.compile(r"(?<![\w.~/:%@-])/(?![/\s])[^\s'\"`,;()\[\]{}<>|]+")  # the core projection's path rule


def _path_sub(m: re.Match[str]) -> str:
    return m.group(0) if m.group(0).startswith("/api/v1") else "<path>"


def public_manifest(doc: dict[str, Any]) -> bytes:
    """A split manifest as the core's public export writes it: no ``roots``, samples without ``image`` / ``label``
    paths, absolute paths in issue texts replaced by ``<path>`` (same JSON layout as the core's members)."""
    out = dict(doc)
    out.pop("roots", None)
    out["samples"] = [{k: v for k, v in s.items() if k not in ("image", "label")}
                      for s in doc.get("samples") or [] if isinstance(s, dict)]
    out["issues"] = [{**i, "message": _ABS_PATH.sub(_path_sub, i["message"])}
                     if isinstance(i, dict) and isinstance(i.get("message"), str) else i for i in doc.get("issues") or []]
    return (json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def gateway_manifests(settings: GatewaySettings, members: Mapping[str, str]) -> dict[str, bytes]:
    """``manifests/<hex>.json`` members the core could not add: the run's split manifests from this gateway's
    DatasetService cache (the validate-time measurement of a split deployment), in the public form
    (:func:`public_manifest`). Only the three split hashes named in ``manifests/hashes.json``; a cache file must be
    that split's manifest (its ``split_hash``)."""
    try:
        resolved = json.loads(members.get("manifests/hashes.json") or "{}").get("resolved") or {}
    except (ValueError, AttributeError):
        return {}
    out: dict[str, bytes] = {}
    cache = os.path.join(settings.manifest_cache_root, "manifests")
    for key in SPLIT_HASH_KEYS:
        sha = _hex(resolved.get(key)) if isinstance(resolved, dict) else None
        name = f"manifests/{sha}.json"
        if sha is None or name in members or name in out:
            continue
        try:
            with open(os.path.join(cache, f"{sha}.json"), "rb") as fh:
                data = fh.read((64 << 20) + 1)
            doc = json.loads(data.decode("utf-8"))
        except (OSError, ValueError):
            continue
        if len(data) <= (64 << 20) and isinstance(doc, dict) and _hex(doc.get("split_hash")) == sha:
            out[name] = public_manifest(doc)
    return out


async def _object_bytes(request: Request, run_id: str, obj: Mapping[str, Any]) -> tuple[bytes | None, str | None]:
    sha = _hex(obj.get("sha256"))
    if sha is None:
        return None, "NO_DIGEST"
    limit = _settings(request).artifact_verify_first_max_bytes
    data, reason = await run_in_threadpool(_read_verified, _store(request), sha, limit)
    if data is not None or reason != "ARTIFACT_NOT_READY" or obj.get("origin") != "core":
        return data, reason
    try:  # a core object: its own download route (owner worker / all-in-one)
        resp = await _core(request).request("GET", f"/runs/{segment(run_id)}/artifacts/{segment(str(obj['id']))}",
                                            request_id=_rid(request))
    except GatewayError as exc:
        return None, exc.code
    if hashlib.sha256(resp.content).hexdigest() != sha:
        return None, "LOAD_FAILED"
    return resp.content, None


@router.get("/runs/{run_id}/export")
async def export(request: Request, run_id: str) -> Response:
    resp = await _core(request).request("GET", f"/runs/{segment(run_id)}/export-manifest", request_id=_rid(request))
    try:
        manifest = json.loads(resp.content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _bad_core("core export manifest is not JSON") from exc
    members = manifest.get("members") if isinstance(manifest, dict) else None
    objects = manifest.get("objects") if isinstance(manifest, dict) else None
    if (manifest.get("format") != EXPORT_FORMAT or not isinstance(members, dict) or not isinstance(objects, list)
            or not all(_member_ok(n) and isinstance(t, str) for n, t in members.items())):
        raise _bad_core("core export manifest has an unexpected shape")
    include = {piece.strip() for value in request.query_params.getlist("include") for piece in value.split(",")}
    files: dict[str, bytes] = {name: text.encode("utf-8") for name, text in members.items()}
    files.pop("SHA256SUMS", None)
    files.update(await run_in_threadpool(gateway_manifests, _settings(request), members))
    index, added = [], 0
    for obj in objects:
        if not isinstance(obj, dict) or not _member_ok(obj.get("name")) or obj["name"] in files:
            continue
        kind = obj.get("kind")
        if not (kind == "architecture" or (kind in EXPORT_IMAGE_KINDS and "images" in include)):
            continue
        entry = {k: obj.get(k) for k in ("name", "id", "kind", "candidate_id", "evaluation_id", "sha256", "bytes",
                                         "mime_type")}
        data, reason = await _object_bytes(request, run_id, obj)
        if data is not None and added + len(data) > EXPORT_OBJECTS_MAX_BYTES:
            data, reason = None, "EXPORT_SIZE_LIMIT"
        if data is not None:
            files[obj["name"]] = data
            added += len(data)
        entry.update(included=data is not None, reason=reason)
        index.append(entry)
    files[EXPORT_INDEX] = (json.dumps(index, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    files["SHA256SUMS"] = "".join(f"{hashlib.sha256(files[n]).hexdigest()}  {n}\n" for n in sorted(files)).encode()
    data = build_zip(files)
    filename = manifest.get("filename") if _member_ok(manifest.get("filename")) else f"{run_id}-export.zip"
    filename = "".join(c for c in str(filename) if c.isalnum() or c in "-_.") or "export.zip"
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "X-Bundle-Sha256": hashlib.sha256(data).hexdigest(),
        "Cache-Control": "no-store"})


# ------------------------------------------------------------------------------------------------ config revisions
@router.get("/runs/{run_id}/candidates/{candidate_id}/config-revisions")
async def config_revisions(request: Request, run_id: str, candidate_id: str) -> Response:
    return await relay(request, "GET", f"/runs/{segment(run_id)}/candidates/{segment(candidate_id)}/config-revisions")


def _revision_bytes(settings: GatewaySettings, store: ArtifactStore, sha: str) -> tuple[bytes | None, str | None]:
    """Bytes of digest ``sha`` from the upload store (``uploads/<sha>.yaml``) or the artifact store, re-hashed."""
    upload = os.path.join(settings.artifact_store, "uploads", f"{sha}.yaml")
    try:
        with open(upload, "rb") as fh:
            data = fh.read(settings.upload_max_bytes + 1)
    except FileNotFoundError:
        data = None
    except OSError:
        return None, "STORE_UNAVAILABLE"
    if data is not None:
        return (data, None) if hashlib.sha256(data).hexdigest() == sha else (None, "LOAD_FAILED")
    return _read_verified(store, sha, settings.artifact_verify_first_max_bytes)


def _safe_name(value: str) -> str:
    return "".join(c for c in value if c.isalnum() or c in "-_.")[:120] or "revision"


@router.get("/runs/{run_id}/candidates/{candidate_id}/config-revisions/{revision_id}/yaml")
async def config_revision_yaml(request: Request, run_id: str, candidate_id: str, revision_id: str) -> Response:
    base = f"/runs/{segment(run_id)}/candidates/{segment(candidate_id)}/config-revisions"
    try:
        return await relay(request, "GET", f"{base}/{segment(revision_id)}/yaml")
    except GatewayError as exc:
        if exc.code != "ARTIFACT_NOT_READY":
            raise
        not_ready = exc
    listing = await _core(request).request("GET", base, request_id=_rid(request))
    try:
        revisions = json.loads(listing.content.decode("utf-8")).get("revisions") or []
    except (UnicodeDecodeError, ValueError, AttributeError):
        raise not_ready from None
    rev = next((r for r in revisions if isinstance(r, dict) and r.get("revision_id") == revision_id), None)
    sha = _hex(rev.get("source_sha256")) if rev is not None else None
    if sha is None:
        raise not_ready
    data, reason = await run_in_threadpool(_revision_bytes, _settings(request), _store(request), sha)
    if data is None:
        if reason == "LOAD_FAILED":
            raise StoreError("LOAD_FAILED", f"stored bytes of configuration revision {revision_id} no longer match "
                                            "its source_sha256")
        raise not_ready
    return Response(data, media_type="application/x-yaml", headers={
        **{k: v for k, v in BASE_HEADERS.items() if k != "Accept-Ranges"}, "X-Artifact-Sha256": sha,
        "X-Artifact-Verification": "pre-send",
        "Content-Disposition": f'attachment; filename="{_safe_name(candidate_id)}_{_safe_name(revision_id)}.yaml"'})

"""Signed artifact URLs and the contract ``Artifact`` view of the gateway (WP-5 contract section 1; DD-A11).

Ported from the original single-host artifact registry (``artifacts/registry.py`` of the source snapshot, HEAD 18c4815;
origin path and sha256 in docs/internal/provenance/PLACEMENT_MANIFEST.jsonl): :func:`artifact_path`, :func:`sign`,
:func:`verify_signature` and :func:`artifact_view`. The HMAC scheme is unchanged, so a URL signed with the same key by
the previous code verifies here:

* ``sig`` = hex HMAC-SHA256(key, ``"<artifact_id>.<exp>"``), ``exp`` = unix seconds. The signature binds the artifact id
  and the expiry only (artifact ids are unique across runs; the download route still looks the id up inside the run
  named in the path).
* Verification checks the signature first, so a forged signature is never reported as merely expired; missing,
  malformed or non-ASCII input is ``"invalid"`` (403), never an exception (HTTP 500).
* The key is ``GatewaySettings.artifact_signing_key`` (``MBNAS_GW_ARTIFACT_SIGNING_KEY``, :func:`signing_key`). Without a
  key every view is ``bearer_fetch`` and every signed query is refused.

Gateway additions: :func:`artifact_view` also accepts the record core returns once artifacts are id-based (WP-5
contract section 2: ``metadata`` instead of ``meta``, top-level ``matching``, no URL and possibly no ``run_id``, which the
caller then passes as ``run_id=``); :func:`require_valid_signature` raises the route errors (403 ACCESS_DENIED,
410 EXPIRED) as :class:`SignatureError`, an :class:`mbnas_contracts.errors.ApiError` with ``location="gateway"``.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from mbnas_contracts.errors import ApiError, ErrorLocation
from mbnas_contracts.util.time import format_iso

API_PREFIX = "/api/v1"
PUBLIC_META_KEYS = ("origin", "category", "filename", "stage", "candidate_id", "batch_index", "stage_epoch_start",
                    "stage_epoch_target", "epoch", "checkpoint_id", "identifier_note", "configuration_role",
                    "configuration_kind", "yaml_hash", "architecture_hash")
"""Scalar metadata keys an ``Artifact`` view may carry (allowlist of the original registry, unchanged)."""


class SignatureError(ApiError):
    """A signed artifact query that must be refused (403 ACCESS_DENIED or 410 EXPIRED); ``location = "gateway"``."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(status, code, message)
        self.location = ErrorLocation.GATEWAY.value


def artifact_path(run_id: str, artifact_id: str) -> str:
    """API path of an artifact (no host, no query): ``/api/v1/runs/{run_id}/artifacts/{artifact_id}``."""
    return f"{API_PREFIX}/runs/{run_id}/artifacts/{artifact_id}"


def sign(key: bytes, artifact_id: str, exp: int) -> str:
    """Hex HMAC-SHA256 of ``"<artifact_id>.<exp>"`` (unchanged from the original registry)."""
    return hmac.new(key, f"{artifact_id}.{int(exp)}".encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(key: bytes, artifact_id: str, exp: Any, sig: Any, *, now: float | None = None) -> str:
    """``"ok"``, ``"expired"`` (authentic but stale) or ``"invalid"`` (bad/missing parts, wrong signature).

    The signature is checked first so a forged signature is never reported as merely expired (unchanged from the
    original registry, including the bytes comparison that keeps non-ASCII input from raising).
    """
    if exp is None or sig is None:
        return "invalid"
    try:
        exp_i = int(str(exp))
    except ValueError:
        return "invalid"
    # bytes, not str: hmac.compare_digest(str, str) raises TypeError for non-ASCII input (e.g. ``sig=%C3%A9``), which
    # reached the client as HTTP 500 instead of 403 in the original code before its 2026-10-15 fix
    try:
        sig_b = str(sig).encode("utf-8")
    except UnicodeEncodeError:  # lone surrogate from a malformed percent-escape
        return "invalid"
    if not hmac.compare_digest(sign(key, artifact_id, exp_i).encode("ascii"), sig_b):
        return "invalid"
    return "ok" if exp_i > (time.time() if now is None else now) else "expired"


def require_valid_signature(key: bytes | None, artifact_id: str, exp: Any, sig: Any, *,
                            now: float | None = None) -> str:
    """``"signed_url"`` for a valid signed query; :class:`SignatureError` 410 EXPIRED for an authentic but stale one,
    403 ACCESS_DENIED for anything else (including a gateway without a signing key). Same mapping as the original
    download route."""
    verdict = "invalid" if key is None else verify_signature(key, artifact_id, exp, sig, now=now)
    if verdict == "expired":
        raise SignatureError(410, "EXPIRED", "signed artifact URL expired; refresh it")
    if verdict != "ok":
        raise SignatureError(403, "ACCESS_DENIED", "invalid artifact signature")
    return "signed_url"


def signing_key(settings: Any) -> bytes | None:
    """The artifact URL signing key of a ``GatewaySettings``-like object (``artifact_signing_key``), or None."""
    key = getattr(settings, "artifact_signing_key", None)
    if key is None:
        return None
    raw = key.get_secret_value() if hasattr(key, "get_secret_value") else str(key)
    return raw.encode("utf-8") if raw else None


def artifact_view(row: Mapping[str, Any], *, base_url: str, key: bytes | None, ttl_s: int, now: float | None = None,
                  run_id: str | None = None) -> dict[str, Any]:
    """Contract ``Artifact`` view of an artifact record with a fresh signed URL when ``key`` is set.

    READY artifacts with a signing key get ``auth_mode=signed_url`` (``?exp=&sig=``, ``expires_at`` = now + ttl,
    ``refresh_url``); everything else is ``bearer_fetch`` with the bare API path. ``row`` is a registry row (``id``,
    ``run_id``, ``meta``) or the id-based record of core (``id``, ``metadata``, ``matching``); ``run_id`` supplies the
    run when the record has none and must agree with it when it has one.
    """
    t = time.time() if now is None else now
    art_id = row.get("id") if row.get("id") is not None else row.get("artifact_id")
    if art_id is None:
        raise ValueError("artifact record has no id")
    row_run = row.get("run_id")
    if run_id is not None and row_run is not None and str(row_run) != str(run_id):
        raise ValueError("artifact record belongs to another run")
    rid = row_run if row_run is not None else run_id
    if rid is None:
        raise ValueError("artifact_view needs the run id (record run_id or run_id=)")
    path = artifact_path(str(rid), str(art_id))
    base = base_url.rstrip("/")
    signed = key is not None and row.get("state") == "READY"
    if signed:
        exp = int(t) + int(ttl_s)
        url = f"{base}{path}?exp={exp}&sig={sign(key, str(art_id), exp)}"  # type: ignore[arg-type]
        expires_at: str | None = format_iso(datetime.fromtimestamp(exp, tz=timezone.utc))
    else:
        url, expires_at = f"{base}{path}", None
    meta = row.get("meta") if row.get("meta") is not None else row.get("metadata")
    if not isinstance(meta, Mapping):
        meta = {}
    public_meta = {k: meta[k] for k in PUBLIC_META_KEYS if k in meta and
                   (meta[k] is None or isinstance(meta[k], (str, int, float, bool)))}
    matching = row.get("matching") if row.get("matching") is not None else meta.get("matching")
    return {"id": art_id, "kind": row["kind"], "url": url, "auth_mode": "signed_url" if signed else "bearer_fetch",
            "expires_at": expires_at, "sample_id": row.get("sample_id"), "split_hash": row.get("split_hash"),
            "sha256": row.get("sha256"), "mime_type": row.get("mime_type"), "state": row.get("state"),
            "refresh_url": f"{base}{path}/refresh" if signed else None, "evaluation_id": row.get("evaluation_id"),
            "matching": matching or None, "metadata": public_meta}

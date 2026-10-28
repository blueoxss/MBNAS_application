"""Model-config uploads and the resolution of ``upload_id`` / ``model_path`` to YAML text.

``POST /api/v1/uploads/model-config`` accepts either a raw body (``Content-Type`` anything but multipart; the file name
comes from ``X-Filename`` or ``?filename=``) or ``multipart/form-data`` with one file part (field ``file``, or the first
part that has a filename). Rules: file name ending ``.yaml``/``.yml`` (else 422 ``UPLOAD_TYPE_UNSUPPORTED``),
1..``upload_max_bytes`` bytes (default 1 MiB; else 413 ``PAYLOAD_TOO_LARGE`` / 422 ``UPLOAD_EMPTY``), UTF-8 text (422
``UPLOAD_NOT_UTF8``). The original bytes are stored unchanged at ``<artifact_store>/uploads/<sha256>.yaml`` (content
addressed, written atomically, an identical re-upload is a no-op) and the answer is ``{upload_id, source_sha256, bytes}``
with ``upload_id = "upl-<sha256 hex>"``; the server path is not returned (no host paths to the browser).

:func:`carry_model_source` rewrites a ``POST /structure-check`` body or a ``user`` training source for core: an
``upload_id`` (gateway feature: 422 ``UPLOAD_ID_INVALID``, 404 ``UPLOAD_NOT_FOUND``, 409 ``UPLOAD_CORRUPT``) or a
legacy absolute ``model_path`` under ``model_yaml_roots`` / ``output_roots`` (read on this host) becomes
``model_yaml_text`` + ``source_sha256`` (+ ``model_name``): core never receives a path for a translated request.
Error-code rule: gateway codes only for the gateway's own features; a legacy ``model_path`` keeps the codes
core used before the split (the shared path-check codes ``PATH_*``, and 422 ``MODEL_CONFIG_INVALID`` for a file that is
not ``.yaml``/``.yml``, larger than ``upload_max_bytes`` or not UTF-8), and a body problem (not an object, unknown key,
empty / relative / non-string ``model_path``, no or several model sources without ``upload_id``) is not pre-validated:
the body goes to core unchanged and core's ``VALIDATION_FAILED`` (or path code) is relayed. Multipart is parsed with the
stdlib ``email`` parser (no ``python-multipart`` dependency).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mbnas_contracts.errors import ErrorLocation
from mbnas_contracts.util.paths import check_server_path
from mbnas_server.api.errors import GatewayError
from mbnas_server.config import GatewaySettings

router = APIRouter(tags=["uploads"])
UPLOAD_ID = re.compile(r"^upl-([0-9a-f]{64})$")
YAML_EXT = (".yaml", ".yml")
MODEL_SOURCES = ("upload_id", "model_path", "model_yaml_text")


def upload_dir(settings: GatewaySettings) -> Path:
    return Path(settings.artifact_store) / "uploads"


def _bad(code: str, message: str, status: int = 422) -> GatewayError:
    return GatewayError(status, code, message, location=ErrorLocation.GATEWAY)


def _multipart_file(content_type: str, body: bytes) -> tuple[str, bytes]:
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    if not msg.is_multipart():
        raise _bad("UPLOAD_INVALID", "multipart body could not be parsed")
    parts = [p for p in msg.iter_parts() if p.get_filename()]
    named = [p for p in parts if p.get_param("name", header="content-disposition") == "file"]
    part = (named or parts or [None])[0]
    if part is None:
        raise _bad("UPLOAD_INVALID", "multipart body has no file part")
    payload = part.get_payload(decode=True)
    return str(part.get_filename()), payload if isinstance(payload, bytes) else b""


def _store(settings: GatewaySettings, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    target = upload_dir(settings) / f"{digest}.yaml"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".upl-", suffix=".tmp")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
    except OSError as exc:
        raise GatewayError(507, "STORE_UNAVAILABLE", f"upload store is not writable ({type(exc).__name__})",
                           location=ErrorLocation.STORE) from exc
    return digest


@router.post("/uploads/model-config")
async def upload_model_config(request: Request) -> JSONResponse:
    settings: GatewaySettings = request.app.state.settings
    body = await request.body()
    ctype = request.headers.get("content-type", "")
    if ctype.lower().startswith("multipart/form-data"):
        filename, data = _multipart_file(ctype, body)
    else:
        filename = request.headers.get("x-filename") or request.query_params.get("filename") or ""
        data = body
    name = os.path.basename(filename.replace("\\", "/"))
    if not name.lower().endswith(YAML_EXT):  # 422 (the 1.5.0 draft lists 415)
        raise _bad("UPLOAD_TYPE_UNSUPPORTED", "model config must be a .yaml or .yml file (give its file name)")
    if len(data) > settings.upload_max_bytes:
        raise _bad("PAYLOAD_TOO_LARGE", f"model config larger than {settings.upload_max_bytes} bytes", 413)
    if not data:
        raise _bad("UPLOAD_EMPTY", "model config is empty")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _bad("UPLOAD_NOT_UTF8", "model config must be UTF-8 text") from exc
    digest = _store(settings, data)
    return JSONResponse({"upload_id": f"upl-{digest}", "source_sha256": f"sha256:{digest}", "bytes": len(data)},
                        status_code=200)


def _read_upload(settings: GatewaySettings, upload_id: Any) -> bytes:
    m = UPLOAD_ID.match(upload_id) if isinstance(upload_id, str) else None
    if m is None:
        raise _bad("UPLOAD_ID_INVALID", "upload_id must look like upl-<64 hex>")
    path = upload_dir(settings) / f"{m.group(1)}.yaml"
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise _bad("UPLOAD_NOT_FOUND", "no upload with this upload_id", 404) from exc
    except OSError as exc:
        raise GatewayError(507, "STORE_UNAVAILABLE", "upload store is not readable", location=ErrorLocation.STORE) from exc
    if hashlib.sha256(data).hexdigest() != m.group(1):
        raise GatewayError(409, "UPLOAD_CORRUPT", "stored upload no longer matches its sha256",
                           location=ErrorLocation.STORE)
    return data


def _model_config_invalid(field: str, message: str) -> GatewayError:
    return GatewayError(422, "MODEL_CONFIG_INVALID", message, location=ErrorLocation.GATEWAY,
                        errors=[{"field": field, "code": "MODEL_CONFIG_INVALID", "message": message}])


def text_from_upload(settings: GatewaySettings, upload_id: Any) -> dict[str, str]:
    """``{model_yaml_text, source_sha256}`` of a stored upload."""
    data = _read_upload(settings, upload_id)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _bad("UPLOAD_NOT_UTF8", "model YAML must be UTF-8 text") from exc
    return {"model_yaml_text": text, "source_sha256": "sha256:" + hashlib.sha256(data).hexdigest()}


def text_from_model_path(settings: GatewaySettings, model_path: str, field: str) -> dict[str, str]:
    """``{model_yaml_text, source_sha256, model_name}`` of a legacy ``model_path`` read on this host, with the checks
    core applied before the split: the shared server path check against ``model_yaml_roots`` + ``output_roots`` (its
    ``PATH_*`` code; the message names no path), then ``.yaml``/``.yml`` (of the resolved file), at most
    ``upload_max_bytes`` and UTF-8, else 422 ``MODEL_CONFIG_INVALID``. ``model_name`` is the resolved file name, so core
    derives the same name/scale as it did from the path."""
    roots = list(settings.model_yaml_roots) + list(settings.output_roots)
    check = check_server_path(model_path, kind="file", allow_roots=roots, must_exist=True)
    if not check.ok:
        code = str(check.code or "PATH_INVALID")
        raise GatewayError(422, code, f"{field} rejected by the server path check ({code})",
                           location=ErrorLocation.GATEWAY,
                           errors=[{"field": field, "code": code, "message": "model_path rejected"}])
    real, limit = str(check.realpath), settings.upload_max_bytes
    too_big = f"model configuration must be a .yaml or .yml file of at most {limit} bytes"
    if not real.lower().endswith(YAML_EXT):
        raise _model_config_invalid(field, too_big)
    try:
        with open(real, "rb") as fh:
            data = fh.read(limit + 1)
    except OSError as exc:
        raise _model_config_invalid(field, "model configuration could not be read") from exc
    if len(data) > limit:
        raise _model_config_invalid(field, too_big)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _model_config_invalid(field, "invalid detector YAML configuration (not UTF-8)") from exc
    return {"model_yaml_text": text, "source_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "model_name": os.path.basename(real)}


def carry_model_source(settings: GatewaySettings, obj: dict[str, Any], core_keys: frozenset[str],
                       prefix: str) -> dict[str, Any] | None:
    """``obj`` (a structure-check body or a ``user`` training source) for core, or ``None`` = forward it unchanged.

    * ``upload_id`` given (not null): exclusive with a non-null ``model_path`` / ``model_yaml_text`` (else 422
      ``VALIDATION_FAILED``, the gateway's own field); replaced by the upload's text and sha256;
    * else a legacy request whose keys are all known to core (``core_keys``) and whose ``model_path`` is an absolute
      string without ``model_yaml_text``: the file is read here (:func:`text_from_model_path`); a caller's
      ``model_name`` is kept unchanged. A gateway without ``model_yaml_roots`` and
      ``output_roots`` (owner all-in-one) has no roots to check the path against, so it forwards the request unchanged
      and core checks the path against its own roots (the same fallback as validate without ``dataset_roots``);
    * anything else is a body problem (or a text core reads itself): ``None``.
    """
    present = {k for k in MODEL_SOURCES if obj.get(k) is not None}
    if "upload_id" in present:
        if len(present) > 1:
            message = "give exactly one of upload_id, model_yaml_text or model_path"
            raise GatewayError(422, "VALIDATION_FAILED", message, location=ErrorLocation.GATEWAY,
                               errors=[{"field": prefix + "upload_id", "code": "EXCLUSIVE", "message": message}])
        out = {k: v for k, v in obj.items() if k not in MODEL_SOURCES}
        out.update(text_from_upload(settings, obj["upload_id"]))
        return out
    base = {k: v for k, v in obj.items() if k != "upload_id"}  # upload_id: null = absent (core does not know the key)
    path = base.get("model_path")
    if set(base) <= core_keys and present == {"model_path"} and isinstance(path, str) and os.path.isabs(path):
        if not (settings.model_yaml_roots or settings.output_roots):  # core checks its own roots
            return base if len(base) != len(obj) else None
        out = {k: v for k, v in base.items() if k not in MODEL_SOURCES}
        carried = text_from_model_path(settings, path, prefix + "model_path")
        if base.get("model_name") is not None:
            carried.pop("model_name")
        out.update(carried)
        return out
    return base if len(base) != len(obj) else None


def dumps(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

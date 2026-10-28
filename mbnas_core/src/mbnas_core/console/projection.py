"""Public projection of everything the core console sends out.

The public contract ``mbnas_contracts/openapi/openapi.json`` (1.5.0) is the only authority on what leaves core through
``/core/v1/console/*`` (and the ``/api/v1`` owner debug mount), its SSE events and the public export bundle. Owner audit
data (raw rows) is served only by :mod:`mbnas_core.console.owner_routes` (``/core/v1/owner/*``), which never calls this
module. Stored rows are never rewritten: every function here works on the response being built.

:func:`project_public` follows ``x-public-projection.rules`` of the contract, schema-driven:

* a closed object (``additionalProperties: false``) keeps only its declared properties; an open object keeps every member
  except the names of its ``propertyNames.not.enum`` deny list; map values follow ``additionalProperties`` schemas;
* the walk recurses through ``$ref``, ``items``, map values, the ``anyOf``/``oneOf`` branches that fit the value and the
  ``allOf`` ``if``/``then`` branch whose condition matches (``Event.payload`` by ``Event.type``);
* ``None`` is dropped where the schema does not admit ``null`` (what the 1.4.x ``prune`` did).

Before a value is copied, the component's value rules (``x-public-projection.value_rules``) are applied to a copy:

* **scrub** - free text (error and validation messages, warnings, registry reasons, log ``message`` /
  ``message_detail`` / ``checks``, structure diagnostics, ``audit.fallback_reason``, evidence errors) and every string
  of ``CandidateEvaluation.raw_metrics``: absolute paths become ``<path>``, ``pid=<n>`` becomes ``pid=<pid>``, this
  host's name becomes ``<host>`` (:func:`scrub_text`); in ``raw_metrics``, where the runner's resolved recipe paths are
  kept, a string value that is a path (first non-blank ``/``, not ``/api/v1``) becomes ``<path>`` whole, blanks and
  unicode included (:func:`_raw_metrics`);
* **ids** - an owner-worker ``hostname:pid`` id becomes an opaque ``wrk-<hex>`` id, a runner row keeps its core-issued
  runner id (``runner_id``), every device UUID becomes the core-issued ``device_id`` (``dev-<hex>``; HMAC with the
  per-database salt :data:`mbnas_core.db.core.PUBLIC_ID_SALT_KEY`, :func:`configure`) and ``device_uuid`` is omitted;
* **paths** - ``checkpoint_path`` / ``architecture_path`` / ``configuration.path`` become run-relative (the prefix up to
  and including the run id segment removed) or the file name; registry evidence paths become ``<file>@sha256:<hex>``;
* **urls** - absolute artifact ``url`` / ``refresh_url`` / ``snapshot_url`` become same-origin ``/api/v1/...`` paths;
* **repair** - a block row whose ``source_kind`` / ``operation`` says ``repair`` is folded into the source it was
  repaired from (``parent_a`` / ``parent_b`` when the source is a parent, else ``random``; operation ``inherit``);
* **labels** - ``RegistryList.source`` / ``capabilities.registry_source`` become ``live`` or ``example``;
* **derived** - ``training_status`` (UNTRAINED|TRAINED) is filled in where the contract declares it.

``principal_scope`` is accepted for call-site compatibility and ignored: an admin principal on the console
mount gets the same projected bodies as the gateway (raw payloads exist only under ``/core/v1/owner/*``).
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import os
import re
import secrets
import socket
import threading
from contextvars import ContextVar
from functools import lru_cache
from typing import Any, Callable
from urllib.parse import urlsplit

from mbnas_contracts.openapi import contract

SCOPES = ("server", "admin")
_scope: ContextVar[str] = ContextVar("mbnas_console_scope", default="server")
_REF = "#/components/schemas/"


def set_scope(scope: str) -> None:
    _scope.set(scope if scope in SCOPES else "server")


def current_scope() -> str:
    return _scope.get()


# ------------------------------------------------------------------------------------------------ public ids
class PublicIds:
    """Opaque public identifiers derived with a secret salt (stable per database, unlinkable across deployments)."""

    def __init__(self, salt: bytes) -> None:
        self._salt = bytes(salt)

    def _digest(self, kind: str, value: Any) -> str:
        return hmac.new(self._salt, f"{kind}|{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:16]

    def device_id(self, uuid: Any) -> str | None:
        return None if uuid in (None, "") else "dev-" + self._digest("device", uuid)

    def worker_id(self, worker_id: Any) -> str:
        return "wrk-" + self._digest("worker", worker_id)


_ids_lock = threading.Lock()
_ids = PublicIds(secrets.token_bytes(32))  # process-local fallback until the app configures the database salt


def configure(salt: bytes | str) -> None:
    """Install the per-database salt (``meta.public_id_salt``); the core app calls this once at startup."""
    global _ids
    with _ids_lock:
        _ids = PublicIds(salt.encode("utf-8") if isinstance(salt, str) else salt)


def public_ids() -> PublicIds:
    return _ids


# ------------------------------------------------------------------------------------------------ scrub
_ABS_PATH = re.compile(r"(?<![\w.~/:%@-])/(?![/\s])[^\s'\"`,;()\[\]{}<>|]+")
_PID = re.compile(r"(?i)\b(pid|child_pid)(\s*[=:]\s*|\s+)\d+")
_PUBLIC_PATH_PREFIXES = ("/api/v1/", "/api/v1")


@lru_cache(maxsize=1)
def _host_names() -> tuple[str, ...]:
    names = set()
    for name in (socket.gethostname(), os.uname().nodename if hasattr(os, "uname") else ""):
        if name:
            names.add(name)
            names.add(name.split(".", 1)[0])
    return tuple(sorted((n for n in names if len(n) >= 4 and n.lower() not in ("localhost",)), key=len, reverse=True))


def _host_re() -> re.Pattern[str] | None:
    names = _host_names()
    if not names:
        return None
    return re.compile(r"(?<![\w.-])(?:" + "|".join(re.escape(n) for n in names) + r")(?::\d+)?(?!\w)")


_HOST_RE: re.Pattern[str] | None = None


def _path_sub(m: re.Match[str]) -> str:
    text = m.group(0)
    return text if text.startswith(_PUBLIC_PATH_PREFIXES) else "<path>"


def scrub_text(text: Any) -> Any:
    """``text`` without absolute paths, process ids and this host's name (non-strings are returned unchanged)."""
    global _HOST_RE
    if not isinstance(text, str) or not text:
        return text
    out = _ABS_PATH.sub(_path_sub, text)
    out = _PID.sub(lambda m: f"{m.group(1)}=<pid>", out)
    if _HOST_RE is None:
        _HOST_RE = _host_re() or re.compile(r"(?!x)x")
    return _HOST_RE.sub("<host>", out)


def scrub_deep(value: Any) -> Any:
    """:func:`scrub_text` on every string of a JSON value (keys unchanged)."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [scrub_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub_deep(v) for k, v in value.items()}
    return value


_RUN_SEGMENT = re.compile(r"^(?:run|train)-[0-9a-f]{12}$")


def public_path(value: Any, run_ids: set[str] | frozenset[str] = frozenset()) -> Any:
    """Run-relative form of an absolute host path (prefix through the run id segment removed), else its file name."""
    if not isinstance(value, str) or not value.startswith("/"):
        return value
    parts = [p for p in value.split("/") if p]
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] in run_ids or _RUN_SEGMENT.match(parts[i]):
            rest = parts[i + 1:]
            return "/".join(rest) if rest else parts[i]
    return parts[-1] if parts else value


def public_url(value: Any) -> Any:
    """Same-origin path (+ query) of an absolute http(s) URL; relative URLs are returned unchanged."""
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        return value
    u = urlsplit(value)
    return (u.path or "/") + (f"?{u.query}" if u.query else "")


@lru_cache(maxsize=1024)
def _file_sha256(path: str, mtime_ns: int, size: int) -> str | None:  # noqa: ARG001 - cache key
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def evidence_id(value: Any) -> Any:
    """``<file name>@sha256:<hex>`` of an absolute evidence path (file name only when it cannot be read)."""
    if not isinstance(value, str) or not value.startswith("/"):
        return value
    name = value.rstrip("/").rsplit("/", 1)[-1] or "evidence"
    try:
        st = os.stat(value)
        digest = _file_sha256(value, st.st_mtime_ns, st.st_size)
    except OSError:
        digest = None
    return f"{name}@sha256:{digest}" if digest else name


def registry_source_label(value: Any) -> Any:
    if value in (None, "live", "example"):
        return value
    text = str(value)
    return "example" if ("example" in text or text.startswith("/")) else "live"


# ------------------------------------------------------------------------------------------------ derived values
TRAINED_STATUSES = frozenset({"EVALUATED", "PROMOTION_ELIGIBLE", "PROMOTION_ADMITTED", "SEARCH_COMPLETE",
                              "FINAL_COMPLETE", "REJECTED_BY_GATE", "ELIGIBLE_BUDGET_EXHAUSTED"})
PRETRAINED_GROUPS = frozenset({"native_pretrained", "official_evaluation"})


def evaluation_training_status(row: dict[str, Any]) -> str:
    """``TRAINED`` when the trial trained at least one epoch or evaluated existing trained weights, else UNTRAINED.

    Evidence (any one): explicit training mode ``evaluate_only``/``pretrained`` or comparison group
    ``native_pretrained``/``official_evaluation`` (trained weights); a committed measurement row (status in
    :data:`TRAINED_STATUSES`) with ``epochs >= 1``; live progress with ``epoch >= 1``; a rung checkpoint. Provisional
    rows carry the rung target in ``epochs``, so ``epochs`` alone is never evidence.
    """
    if row.get("training_mode") in ("evaluate_only", "pretrained") or row.get("comparison_group") in PRETRAINED_GROUPS:
        return "TRAINED"
    epochs = row.get("epochs")
    if row.get("status") in TRAINED_STATUSES and isinstance(epochs, int) and not isinstance(epochs, bool) and epochs >= 1:
        return "TRAINED"
    progress = row.get("progress") if isinstance(row.get("progress"), dict) else {}
    epoch = progress.get("epoch")
    if isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= 1:
        return "TRAINED"
    return "TRAINED" if row.get("checkpoint_path") else "UNTRAINED"


def fold_block(block: Any, parents: list[Any]) -> Any:
    """A block row with its repair folded into the source it was repaired from."""
    if not isinstance(block, dict) or (block.get("source_kind") != "repair" and block.get("operation") != "repair"):
        return block
    out = dict(block)
    if out.get("source_kind") == "repair":
        source = out.get("source")
        out["source_kind"] = ("parent_a" if parents and source == parents[0] else
                              "parent_b" if len(parents) > 1 and source == parents[1] else "random")
    if out.get("operation") == "repair":
        out["operation"] = "inherit"
    return out


def _fold_blocks(value: dict[str, Any], parents: Any) -> None:
    blocks = value.get("blocks")
    if isinstance(blocks, list):
        plist = [p for p in parents if isinstance(p, str)] if isinstance(parents, list) else []
        value["blocks"] = [fold_block(b, plist) for b in blocks]


# ------------------------------------------------------------------------------------------------ component value rules
class _Ctx:
    __slots__ = ("run_ids",)

    def __init__(self, run_ids: set[str] | None = None) -> None:
        self.run_ids: set[str] = set(run_ids or ())

    def add_run(self, run_id: Any) -> None:
        if isinstance(run_id, str) and run_id:
            self.run_ids.add(run_id)


_HOST_PID = re.compile(r"^.+:\d+$")


def _worker(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    v = dict(v)
    ctx.add_run(v.get("run_id"))
    wid = v.get("id")
    owner_row = v.get("pid") is not None or v.get("child_pid") is not None or (
        isinstance(wid, str) and bool(_HOST_PID.match(wid)))
    if isinstance(wid, str):
        if owner_row:  # the core-host proposal worker: hostname:pid is never public
            v["id"], v["runner_id"] = public_ids().worker_id(wid), None
        else:  # a runner row: workers.id is the core-issued runner id
            v.setdefault("runner_id", wid)
    uuid = v.pop("device_uuid", None)
    if uuid and not v.get("device_id"):
        v["device_id"] = public_ids().device_id(uuid)
    for key in ("device", "device_name"):
        if isinstance(v.get(key), str):
            v[key] = scrub_text(v[key])
    return v


def _path_values(value: Any) -> Any:
    """``value`` with every string that IS a path - first non-blank character ``/``, not a public ``/api/v1`` path -
    replaced whole by ``<path>`` (blanks, unicode whitespace and unicode names included), in dict values and list items
    at any depth; keys and every other value unchanged (used for ``raw_metrics`` only)."""
    if isinstance(value, str):
        head = value.lstrip()
        return "<path>" if head.startswith("/") and not head.startswith(_PUBLIC_PATH_PREFIXES) else value
    if isinstance(value, list):
        return [_path_values(v) for v in value]
    if isinstance(value, dict):
        return {k: _path_values(v) for k, v in value.items()}
    return value


def _raw_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """Public ``raw_metrics``. The core keeps the runner's evaluation payload here, including the runner's
    resolved recipe (``resolved_recipe.data`` / ``model`` / ``project`` / ``save_dir`` are runner-local absolute paths;
    this copy in the evaluation row is not named in the storage list of ``mbnas_contracts.runner_protocol``), so this
    is where those values would reach a public response. Device uuids become ``device_id``; a string value that is a
    path becomes ``<path>`` whole (:func:`_path_values`: no tail of a path with blanks or unicode); every other
    string gets :func:`scrub_text` (:func:`scrub_deep`: a path inside longer text follows ``_ABS_PATH``). Dict
    values and list items at any depth; keys unchanged; the stored row is not modified."""
    raw = dict(raw)
    uuid = raw.pop("device_uuid", None)
    if uuid and not raw.get("device_id"):
        raw["device_id"] = public_ids().device_id(uuid)
    if isinstance(raw.get("latency"), dict):
        raw["latency"] = _uuid_to_device_id(raw["latency"])
    return scrub_deep(_path_values(raw))


def _uuid_to_device_id(value: Any) -> Any:
    """Every ``uuid`` / ``device_uuid`` member of a nested record replaced by ``device_id``."""
    if isinstance(value, list):
        return [_uuid_to_device_id(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for k, v in value.items():
        if k in ("uuid", "device_uuid"):
            if v and "device_id" not in value:
                out["device_id"] = public_ids().device_id(v)
            continue
        out[k] = _uuid_to_device_id(v)
    return out


def _evaluation(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    v = dict(v)
    if "training_status" not in v:
        v["training_status"] = evaluation_training_status(v)
    for key in ("checkpoint_path", "architecture_path"):
        if key in v:
            v[key] = public_path(v[key], ctx.run_ids)
    _fold_blocks(v, v.get("parents"))
    if isinstance(v.get("raw_metrics"), dict):
        v["raw_metrics"] = _raw_metrics(v["raw_metrics"])
    return v


def _audit(audit: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    audit = dict(audit)
    gen = audit.get("generation")
    if isinstance(gen, dict) and gen.get("worker_id") is not None:
        gen = dict(gen)
        wid = gen["worker_id"]
        gen["worker_id"] = public_ids().worker_id(wid) if _HOST_PID.match(str(wid)) else wid
        audit["generation"] = gen
    if "fallback_reason" in audit:
        audit["fallback_reason"] = scrub_text(audit["fallback_reason"])
    if isinstance(audit.get("structure"), dict):
        audit["structure"] = _structure(audit["structure"], ctx)
    return audit


def _candidate_detail(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    v = dict(v)
    ctx.add_run(v.get("run_id"))
    if "architecture_path" in v:
        v["architecture_path"] = public_path(v["architecture_path"], ctx.run_ids)
    _fold_blocks(v, v.get("parents"))
    evals = [e for e in v.get("evaluations") or [] if isinstance(e, dict)]
    if "training_status" not in v:
        v["training_status"] = "TRAINED" if any(
            (e.get("training_status") or evaluation_training_status(e)) == "TRAINED" for e in evals) else "UNTRAINED"
    if isinstance(v.get("audit"), dict):
        v["audit"] = _audit(v["audit"], ctx)
    return v


def _generated(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    v = dict(v)
    ctx.add_run(v.get("run_id"))
    v["training_status"] = "UNTRAINED"  # generation never trains (contract GeneratedCandidate.training_status)
    if "architecture_path" in v:
        v["architecture_path"] = public_path(v["architecture_path"], ctx.run_ids)
    _fold_blocks(v, v.get("parents"))
    return v


def _diagnostics(items: Any) -> Any:
    if not isinstance(items, list):
        return items
    out = []
    for d in items:
        if isinstance(d, dict):
            d = dict(d)
            for key in ("cause", "suggestion", "message", "detail"):
                if isinstance(d.get(key), str):
                    d[key] = scrub_text(d[key])
        out.append(d)
    return out


def _structure(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    v = dict(v)
    ctx.add_run(v.get("run_id"))
    conf = v.get("configuration")
    if isinstance(conf, dict) and "path" in conf:
        v["configuration"] = {**conf, "path": public_path(conf.get("path"), ctx.run_ids)}
    lineage = v.get("lineage") if isinstance(v.get("lineage"), dict) else {}
    _fold_blocks(v, lineage.get("parents") or [])
    for key in ("diagnostics", "checks"):
        if key in v:
            v[key] = _diagnostics(v[key])
    if isinstance(v.get("reason"), str):
        v["reason"] = scrub_text(v["reason"])
    return v


def _event(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    v = dict(v)
    ctx.add_run(v.get("run_id"))
    payload = v.get("payload")
    if not isinstance(payload, dict):
        return v
    if v.get("type") == "log":
        payload = dict(payload)
        for key in ("message", "message_detail", "reason"):
            if isinstance(payload.get(key), str):
                payload[key] = scrub_text(payload[key])
        if "checks" in payload:
            payload["checks"] = scrub_deep(payload["checks"])
        if "source" in payload and isinstance(payload["source"], (dict, list, str)):
            payload["source"] = scrub_deep(payload["source"])
        v["payload"] = payload
    elif v.get("type") == "snapshot.required" and "snapshot_url" in payload:
        v["payload"] = {**payload, "snapshot_url": public_url(payload["snapshot_url"])}
    return v


def _codes_only(items: list[Any]) -> list[Any]:
    """Generation failure diagnostics reduced to codes (DTO allowlist: rule sentences of the structure gate are
    owner-only): ``{attempt, status, code, diagnostics: [{code}]}``."""
    out: list[Any] = []
    for d in items:
        if not isinstance(d, dict):
            out.append(d)
            continue
        entry = {k: d[k] for k in ("attempt", "status", "code") if k in d}
        if isinstance(d.get("diagnostics"), list):
            entry["diagnostics"] = [{"code": x.get("code")} if isinstance(x, dict) else x for x in d["diagnostics"]]
        out.append(entry)
    return out


def _run(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:
    v = dict(v)
    ctx.add_run(v.get("id"))
    budget = v.get("budget")
    if isinstance(budget, dict) and isinstance(budget.get("generation_results"), dict):
        results = {}
        for seed, summary in budget["generation_results"].items():
            if isinstance(summary, dict) and isinstance(summary.get("diagnostics"), list):
                summary = {**summary, "diagnostics": _codes_only(summary["diagnostics"])}
            results[seed] = summary
        v["budget"] = {**budget, "generation_results": results}
    return v


def _capability_object(value: Any) -> Any:
    """Registry capability object(s): evidence paths -> evidence ids, measured_on uuid -> device_id, reasons scrubbed."""
    if isinstance(value, list):
        return [_capability_object(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for k, v in value.items():
        if k == "evidence" and isinstance(v, str):
            out[k] = evidence_id(v)
        elif k == "measured_on" and isinstance(v, dict):
            out[k] = _uuid_to_device_id(v)
        elif k in ("reason", "detail", "error", "message") and isinstance(v, str):
            out[k] = scrub_text(v)
        else:
            out[k] = _capability_object(v)
    return out


def _registry_entry(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:  # noqa: ARG001
    v = dict(v)
    if isinstance(v.get("reason"), str):
        v["reason"] = scrub_text(v["reason"])
    if isinstance(v.get("capabilities"), dict):
        v["capabilities"] = _capability_object(v["capabilities"])
    evidence = v.get("evidence")
    if isinstance(evidence, dict):
        ev = {}
        for k, item in evidence.items():
            if isinstance(item, dict) and isinstance(item.get("error"), str):
                item = {**item, "error": scrub_text(item["error"])}
            elif k == "source" and isinstance(item, str):
                item = scrub_text(item)  # example registry label
            ev[k] = item
        v["evidence"] = ev
    return v


def _registry_list(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:  # noqa: ARG001
    v = dict(v)
    if "source" in v:
        v["source"] = registry_source_label(v["source"])
    if isinstance(v.get("warning"), str):
        v["warning"] = scrub_text(v["warning"])
    return v


def _device_item(d: Any) -> Any:
    if not isinstance(d, dict):
        return d
    d = dict(d)
    uuid = d.pop("uuid", None)
    if uuid and not d.get("device_id"):
        d["device_id"] = public_ids().device_id(uuid)
    if isinstance(d.get("aliases"), list):
        d["aliases"] = [a for a in d["aliases"] if a != uuid]
    wid = d.get("worker_id")
    if isinstance(wid, str) and _HOST_PID.match(wid):
        d["worker_id"] = public_ids().worker_id(wid)
    for key in ("id", "name"):
        if isinstance(d.get(key), str):
            d[key] = scrub_text(d[key])
    return d


def _device_target(d: Any) -> Any:
    if not isinstance(d, dict):
        return d
    d = _device_item(d) if "uuid" in d else dict(d)
    if isinstance(d.get("reason"), str):
        d["reason"] = scrub_text(d["reason"])
    return d


def _health(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:  # noqa: ARG001
    v = dict(v)
    if isinstance(v.get("warnings"), list):
        v["warnings"] = [scrub_text(w) for w in v["warnings"]]
    caps = v.get("capabilities")
    if isinstance(caps, dict):
        caps = dict(caps)
        if "registry_source" in caps:
            caps["registry_source"] = registry_source_label(caps["registry_source"])
        for key in ("full_state_resume", "export_onnx", "export_trt", "devices", "adapter_class"):
            if key in caps:
                caps[key] = _capability_object(caps[key])
        space = caps.get("search_space")
        if isinstance(space, dict) and isinstance(space.get("reason"), str):
            caps["search_space"] = {**space, "reason": scrub_text(space["reason"])}
        if isinstance(caps.get("device_targets"), list):
            caps["device_targets"] = [_device_target(d) for d in caps["device_targets"]]
        v["capabilities"] = caps
    if isinstance(v.get("devices"), list):
        v["devices"] = [_device_item(d) for d in v["devices"]]
    return v


def _issues(items: Any) -> Any:
    if not isinstance(items, list):
        return items
    return [{**i, "message": scrub_text(i["message"])} if isinstance(i, dict) and isinstance(i.get("message"), str)
            else i for i in items]


def _validate_response(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:  # noqa: ARG001
    v = dict(v)
    for key in ("errors", "warnings"):
        if key in v:
            v[key] = _issues(v[key])
    caps = v.get("capabilities")
    if isinstance(caps, dict):
        caps = dict(caps)
        if isinstance(caps.get("device"), dict):
            caps["device"] = _uuid_to_device_id(caps["device"])
        if isinstance(caps.get("baselines"), dict):
            caps["baselines"] = {k: _capability_object(b) for k, b in caps["baselines"].items()}
        space = caps.get("search_space")
        if isinstance(space, dict):
            space = dict(space)
            if isinstance(space.get("detail"), str):
                space["detail"] = scrub_text(space["detail"])
            if isinstance(space.get("probe"), dict) and isinstance(space["probe"].get("reason"), str):
                space["probe"] = {**space["probe"], "reason": scrub_text(space["probe"]["reason"])}
            caps["search_space"] = space
        v["capabilities"] = caps
    return v


def _error(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:  # noqa: ARG001
    v = dict(v)
    if isinstance(v.get("message"), str):
        v["message"] = scrub_text(v["message"])
    if "errors" in v:
        v["errors"] = _issues(v["errors"])
    return v


def _artifact(v: dict[str, Any], ctx: _Ctx) -> dict[str, Any]:  # noqa: ARG001
    v = dict(v)
    for key in ("url", "refresh_url"):
        if key in v:
            v[key] = public_url(v[key])
    return v


_HOOKS: dict[str, Callable[[dict[str, Any], _Ctx], dict[str, Any]]] = {
    "Worker": _worker, "CandidateEvaluation": _evaluation, "CandidateDetail": _candidate_detail,
    "GeneratedCandidate": _generated, "StructureReport": _structure, "Event": _event, "Run": _run,
    "RegistryEntry": _registry_entry, "RegistryList": _registry_list, "Health": _health,
    "ValidateResponse": _validate_response, "Error": _error, "Artifact": _artifact,
}


# ------------------------------------------------------------------------------------------------ schema walk
def _schemas() -> dict[str, Any]:
    return contract()["components"]["schemas"]


def _resolve(schema: Any) -> tuple[str | None, dict[str, Any]]:
    if not isinstance(schema, dict):
        return None, {}
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(_REF):
        name = ref[len(_REF):]
        return name, _schemas()[name]
    return None, schema


def _types(schema: dict[str, Any]) -> set[str]:
    t = schema.get("type")
    return set(t) if isinstance(t, list) else ({t} if isinstance(t, str) else set())


def allows_null(schema: Any) -> bool:
    _, schema = _resolve(schema)
    if not schema:
        return True
    if "null" in _types(schema) or (isinstance(schema.get("enum"), list) and None in schema["enum"]):
        return True
    if schema.get("const", 0) is None:
        return True
    return any(allows_null(s) for key in ("anyOf", "oneOf") for s in schema.get(key, []))


def _matches(cond: dict[str, Any], value: dict[str, Any]) -> bool:
    """Minimal ``if`` evaluation used by the contract: required keys + ``const``/``enum`` on properties."""
    if any(k not in value for k in cond.get("required", [])):
        return False
    for key, rule in (cond.get("properties") or {}).items():
        if key not in value:
            continue
        if "const" in rule and value[key] != rule["const"]:
            return False
        if "enum" in rule and value[key] not in rule["enum"]:
            return False
    return True


def _branches(schema: dict[str, Any], kind: str) -> list[tuple[str | None, dict[str, Any]]]:
    """``anyOf``/``oneOf`` branches of ``schema`` that can hold a value of JSON ``kind`` (object | array)."""
    out = []
    for key in ("anyOf", "oneOf"):
        for s in schema.get(key, []):
            name, sub = _resolve(s)
            types = _types(sub)
            if kind in types or (not types and (kind == "object" and ("properties" in sub or "additionalProperties" in sub)
                                                or kind == "array" and "items" in sub)):
                out.append((name, sub))
    return out


def _object_parts(schema: dict[str, Any], value: dict[str, Any]) -> list[dict[str, Any]]:
    parts = [schema]
    for _, sub in _branches(schema, "object"):
        parts.append(sub)
    for part in schema.get("allOf", []):
        _, part = _resolve(part)
        if "if" in part and "then" in part:
            if _matches(part["if"], value):
                parts.append(_resolve(part["then"])[1])
        elif "if" not in part:
            parts.append(part)
    return parts


def _copy_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _copy_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_json(v) for v in value]
    return value


def _project(value: Any, schema: Any, ctx: _Ctx, name: str | None = None) -> Any:
    ref_name, schema = _resolve(schema)
    name = ref_name or name
    if isinstance(value, dict) and name in _HOOKS:
        value = _HOOKS[name](value, ctx)
    if value is None:
        return None
    if isinstance(value, list):
        items = schema.get("items")
        if items is None:
            for _, sub in _branches(schema, "array"):
                items = sub.get("items")
                if items is not None:
                    break
        if not isinstance(items, dict):
            return _copy_json(value)
        return [_project(v, items, ctx) for v in value]
    if not isinstance(value, dict):
        return value
    parts = _object_parts(schema, value)
    props: dict[str, Any] = {}
    closed, mapped, deny = False, None, set()
    for part in parts:
        props.update(part.get("properties") or {})
        extra = part.get("additionalProperties")
        if extra is False:
            closed = True
        elif isinstance(extra, dict):
            mapped = extra
        names = ((part.get("propertyNames") or {}).get("not") or {}).get("enum")
        if isinstance(names, list):
            deny.update(names)
    out: dict[str, Any] = {}
    for key, val in value.items():
        if key in deny:
            continue
        sub = props.get(key)
        if sub is None:
            if closed:
                continue
            if mapped is None:
                out[key] = _copy_json(val)
                continue
            sub = mapped
        if val is None and not allows_null(sub):
            continue
        out[key] = _project(val, sub, ctx)
    return out


def known_component(component: str) -> bool:
    return component in _schemas()


def project_public(component: str, payload: Any, principal_scope: str | None = None, *,
                   run_id: str | None = None) -> Any:
    """``payload`` projected onto contract component ``component`` (module docstring); ``run_id`` helps the path rule.

    Raises ``KeyError`` for a name that is not a contract component (a route bug, never a silent pass-through).
    ``principal_scope`` is ignored (see module docstring).
    """
    del principal_scope
    if not known_component(component):
        raise KeyError(f"{component!r} is not a component of the public contract")
    ctx = _Ctx({run_id} if run_id else None)
    return _project(payload, {"$ref": _REF + component}, ctx)


def schema_at(component: str, *path: str) -> dict[str, Any]:
    """Sub-schema of ``component`` along ``path`` (property names; ``[]`` = array items)."""
    _, node = _resolve({"$ref": _REF + component})
    for step in path:
        _, node = _resolve(node)
        node = node["items"] if step == "[]" else node["properties"][step]
    return node


def project_member(component: str, path: tuple[str, ...], value: Any, *, run_id: str | None = None) -> Any:
    """``value`` projected onto the sub-schema ``schema_at(component, *path)`` (export members, owner tools); an empty
    ``path`` is :func:`project_public`."""
    ctx = _Ctx({run_id} if run_id else None)
    return _project(value, schema_at(component, *path), ctx, name=None if path else component)


def public_budget(budget: Any) -> Any:
    """``Run.budget`` as the public projection sends it (admission policy text removed, generation diagnostics reduced
    to their codes)."""
    if not isinstance(budget, dict):
        return budget
    return _project(_run({"budget": budget}, _Ctx())["budget"], schema_at("Run", "budget"), _Ctx())


def deep_copy(value: Any) -> Any:
    return copy.deepcopy(value)

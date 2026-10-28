"""Runner journal: the on-disk outbox of one job attempt (A3 section 6.2) and its restart replay (A3 section 5.5).

Layout ``<work_root>/journal/<job_id>/a<attempt>/``::

    envelope.json          claim response ``{envelope, ticket, server_time}`` (immutable)
    spec.json              the child's JobSpec derived from the envelope (immutable)
    started.json           {started_at, lease_expires_at} from the start response
    progress.cursor        {"seq": <last accepted_seq>}
    checkpoints/<n>.json   {report, verified_sha256, response | null}
    artifacts/<n>.json     {batch, response | null}
    result.json            {result, started_at, finished_at, cancel_observed, runner_shutting_down, artifact_ids,
                            device_uuid, payload_digest} (immutable; the ``ResultCommit`` body minus the fence)
    response.json          the core's 200 body for the result
    acked                  empty file: the ack reached the core
    discarded              {reason, code, at}: the attempt is closed without a commit (LEASE_LOST, RESULT_CONFLICT)
    transfers/<n>.json     {mode, items: [...]}: files of this attempt the core accepted a registration for, copied into
                           the artifact store (``artifact_transfer=local``, :mod:`mbnas_runner.transfer`); written before
                           the store write, rewritten with each try's outcome (state pending|ok|failed|refused|abandoned)

Every file is written with :func:`mbnas_contracts.util.atomic.atomic_write_json` (temp file + fsync + ``os.replace`` +
directory fsync) *before* the request it describes is sent (outbox rule). ``result.json`` is never rewritten once it
exists: a resend carries the same bytes and therefore the same ``payload_digest`` (no ``RESULT_CONFLICT``).

Transfer entries never change the stage of an attempt (a CLOSED attempt can still hold items to retry) and never touch
``result.json``.

Retention prune orders (DD-A66, runner protocol 1.1.0) are not attempt-scoped - an order may be reported after its lease
ended - so they have their own outbox, :class:`PruneJournal` = ``<work_root>/prunes/<request_id>.json``::

    {order, report | null, refused | null, response | null, updated_at}

``report`` (the E11 ``PruneReport`` body) is written once, right after the deletion (or the already-absent finding) and
BEFORE it is sent; a re-delivered order (same ``request_id``) never deletes again, it resends that same report.
``refused`` records an order the runner would not execute (nothing deleted, nothing reported); ``response`` holds the
core's answer (``{"ack": ...}`` or ``{"error": code, "status": n}``). Entries are never deleted (they are small).

``payload_digest`` = ``canonical_sha256({result, started_at, finished_at, cancel_observed, artifact_ids})``
(A3 section 7.1; ``sha256:``-prefixed, identical to ``mbnas_contracts.runner_protocol.compute_payload_digest``).
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from mbnas_contracts.util.atomic import atomic_write_bytes, atomic_write_json
from mbnas_contracts.util.canon import canonical_sha256
from mbnas_contracts.util.time import utcnow_iso

DIGEST_FIELDS = ("result", "started_at", "finished_at", "cancel_observed", "artifact_ids")


def payload_digest(commit: dict[str, Any]) -> str:
    """Digest of the result commit (A3 section 7.1; the core recomputes it)."""
    return canonical_sha256({k: commit.get(k) for k in DIGEST_FIELDS})


def _safe(component: str) -> str:
    if not component or "/" in component or "\\" in component or component in (".", "..") or "\x00" in component:
        raise ValueError(f"unsafe journal path component {component!r}")
    return component


class Stage(str, Enum):
    """Where an attempt stopped, in replay order (A3 section 5.5)."""

    CLAIMED = "claimed"          # envelope.json, no started.json -> try start (409 -> discard)
    STARTED = "started"          # started.json, no result.json -> the child is gone: CHILD_EXIT_NO_RESULT
    RESULT = "result"            # result.json, no response.json -> resend the same commit
    RESPONDED = "responded"      # response.json, not acked -> resend ack
    CLOSED = "closed"            # acked or discarded: nothing left to send


@dataclass
class AttemptJournal:
    """The journal directory of one (job, attempt)."""

    root: Path

    @classmethod
    def create(cls, work_root: str | os.PathLike[str], job_id: str, attempt: int) -> "AttemptJournal":
        root = Path(work_root) / "journal" / _safe(job_id) / f"a{int(attempt)}"
        root.mkdir(parents=True, exist_ok=True)
        return cls(root)

    # -- generic -----------------------------------------------------------------------------------------------
    def _read(self, name: str) -> Any:
        path = self.root / name
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_once(self, name: str, obj: Any) -> Any:
        """Write ``name`` unless it exists; return what is on disk (the first write wins)."""
        existing = self._read(name)
        if existing is not None:
            return existing
        atomic_write_json(self.root / name, obj)
        return obj

    # -- files -------------------------------------------------------------------------------------------------
    @property
    def claim(self) -> dict[str, Any] | None:
        """The claim response as received (``{envelope, ticket, server_time}``)."""
        return self._read("envelope.json")

    def record_claim(self, claim: dict[str, Any]) -> dict[str, Any]:
        return self._write_once("envelope.json", claim)

    @property
    def spec_path(self) -> Path:
        return self.root / "spec.json"

    @property
    def spec(self) -> dict[str, Any] | None:
        """The child's JobSpec as recorded (``spec.json``), or None before it was derived."""
        return self._read("spec.json")

    def record_spec(self, spec: dict[str, Any]) -> Path:
        self._write_once("spec.json", spec)
        return self.spec_path

    @property
    def started(self) -> dict[str, Any] | None:
        return self._read("started.json")

    def record_started(self, started: dict[str, Any]) -> dict[str, Any]:
        return self._write_once("started.json", {"started_at": started.get("started_at"),
                                                 "lease_expires_at": started.get("lease_expires_at")})

    @property
    def progress_seq(self) -> int:
        cur = self._read("progress.cursor")
        return int(cur.get("seq", 0)) if isinstance(cur, dict) else 0

    def record_progress_seq(self, seq: int) -> None:
        atomic_write_json(self.root / "progress.cursor", {"seq": int(seq)})

    def _next(self, folder: str) -> Path:
        d = self.root / folder
        d.mkdir(exist_ok=True)
        n = 1 + max((int(p.stem) for p in d.glob("*.json") if p.stem.isdigit()), default=0)
        return d / f"{n}.json"

    def record_checkpoint(self, report: dict[str, Any], verified_sha256: str) -> Path:
        path = self._next("checkpoints")
        atomic_write_json(path, {"report": report, "verified_sha256": verified_sha256, "response": None})
        return path

    def record_artifacts(self, batch: list[dict[str, Any]]) -> Path:
        path = self._next("artifacts")
        atomic_write_json(path, {"batch": batch, "response": None})
        return path

    def record_response_of(self, path: Path, response: Any) -> None:
        """Fill ``response`` of an outbox entry written by :meth:`record_checkpoint` / :meth:`record_artifacts`."""
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["response"] = response
        atomic_write_json(path, entry)

    def outbox(self, folder: str) -> Iterator[tuple[Path, dict[str, Any]]]:
        d = self.root / folder
        for path in sorted(d.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0):
            yield path, json.loads(path.read_text(encoding="utf-8"))

    @property
    def result(self) -> dict[str, Any] | None:
        return self._read("result.json")

    def record_result(self, commit: dict[str, Any]) -> dict[str, Any]:
        """Freeze the result commit (adds ``payload_digest``); an existing ``result.json`` is returned unchanged."""
        existing = self.result
        if existing is not None:
            return existing
        body = {**commit, "payload_digest": payload_digest(commit)}
        atomic_write_json(self.root / "result.json", body)
        return body

    @property
    def response(self) -> dict[str, Any] | None:
        return self._read("response.json")

    def record_response(self, response: dict[str, Any]) -> None:
        atomic_write_json(self.root / "response.json", response)

    @property
    def acked(self) -> bool:
        return (self.root / "acked").exists()

    def record_acked(self) -> None:
        atomic_write_bytes(self.root / "acked", b"")

    @property
    def discarded(self) -> dict[str, Any] | None:
        return self._read("discarded")

    def record_discarded(self, reason: str, code: str | None = None) -> None:
        atomic_write_json(self.root / "discarded", {"reason": reason, "code": code, "at": utcnow_iso()})

    # -- artifact transfers (WP-5C, mbnas_runner.transfer) -----------------------------------------------------------
    def record_transfers(self, mode: str, items: list[dict[str, Any]]) -> Path:
        """Outbox entry ``transfers/<n>.json`` = ``{mode, items}``, written before any store write."""
        path = self._next("transfers")
        atomic_write_json(path, {"mode": mode, "items": items})
        return path

    @staticmethod
    def read_transfers(path: Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    @staticmethod
    def update_transfers(path: Path, entry: dict[str, Any]) -> None:
        """Rewrite a transfer entry with the latest outcome of its items."""
        atomic_write_json(path, entry)

    def transfers(self) -> Iterator[tuple[Path, dict[str, Any]]]:
        return self.outbox("transfers")

    @property
    def transfers_open(self) -> bool:
        """True while an item of a transfer entry may still be retried (state pending or failed)."""
        return any(it.get("state") in ("pending", "failed")
                   for _, entry in self.transfers() for it in entry.get("items") or [])

    # -- state -------------------------------------------------------------------------------------------------
    @property
    def stage(self) -> Stage:
        if self.discarded is not None or self.acked:
            return Stage.CLOSED
        if self.response is not None:
            return Stage.RESPONDED
        if self.result is not None:
            return Stage.RESULT
        if self.started is not None:
            return Stage.STARTED
        return Stage.CLAIMED

    @property
    def job_id(self) -> str:
        return self.root.parent.name

    @property
    def attempt(self) -> int:
        return int(self.root.name[1:])


def open_attempts(work_root: str | os.PathLike[str]) -> list[AttemptJournal]:
    """Every journal attempt that is not CLOSED (oldest job directory first), for restart replay."""
    base = Path(work_root) / "journal"
    out: list[AttemptJournal] = []
    if not base.is_dir():
        return out
    for job_dir in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime):
        if not job_dir.is_dir():
            continue
        for att in sorted(job_dir.glob("a*"), key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else 0):
            if att.is_dir() and att.name[1:].isdigit() and (att / "envelope.json").is_file():
                j = AttemptJournal(att)
                if j.stage != Stage.CLOSED:
                    out.append(j)
    return out


def transfer_entries(work_root: str | os.PathLike[str]) -> Iterator[tuple[AttemptJournal, Path]]:
    """Every ``transfers/<n>.json`` entry under ``<work_root>/journal`` (any stage), for the transfer retry pass."""
    base = Path(work_root) / "journal"
    if not base.is_dir():
        return
    for path in sorted(base.glob("*/a*/transfers/*.json")):
        att = path.parent.parent
        if path.stem.isdigit() and att.name[1:].isdigit():
            yield AttemptJournal(att), path


class PruneJournal:
    """Outbox of the retention prune round trip (module docstring): one JSON file per prune request id."""

    def __init__(self, work_root: str | os.PathLike[str]) -> None:
        self.root = Path(work_root) / "prunes"

    def path(self, request_id: str) -> Path:
        return self.root / f"{_safe(str(request_id))}.json"

    def get(self, request_id: str) -> dict[str, Any] | None:
        path = self.path(request_id)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _write(self, request_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        entry = {**entry, "updated_at": utcnow_iso()}
        atomic_write_json(self.path(request_id), entry)
        return entry

    def record_report(self, order: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
        """Freeze the report of ``order`` (first write wins: an existing report is returned unchanged)."""
        rid = str(order["request_id"])
        existing = self.get(rid)
        if existing is not None and existing.get("report") is not None:
            return existing
        return self._write(rid, {"order": order, "report": report, "refused": None, "response": None})

    def record_refused(self, order: dict[str, Any], refusal: dict[str, Any]) -> dict[str, Any]:
        """An order the runner did not execute (nothing deleted); never overwrites a recorded report."""
        rid = str(order["request_id"])
        existing = self.get(rid)
        if existing is not None and existing.get("report") is not None:
            return existing
        return self._write(rid, {"order": order, "report": None, "refused": {**refusal, "at": utcnow_iso()},
                                 "response": None})

    def record_response(self, request_id: str, response: dict[str, Any]) -> dict[str, Any] | None:
        existing = self.get(request_id)
        if existing is None:
            return None
        return self._write(request_id, {**existing, "response": response})

    def unanswered(self) -> list[dict[str, Any]]:
        """Entries whose report was never answered by the core (resent on the idle loop)."""
        if not self.root.is_dir():
            return []
        out = []
        for path in sorted(self.root.glob("*.json")):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(entry, dict) and entry.get("report") is not None and entry.get("response") is None:
                out.append(entry)
        return out


def prune_closed(work_root: str | os.PathLike[str], keep_s: float, now: float) -> int:
    """Delete CLOSED attempt directories older than ``keep_s`` seconds (housekeeping); returns how many. An attempt
    whose artifact transfers may still be retried is kept."""
    base = Path(work_root) / "journal"
    removed = 0
    if not base.is_dir():
        return 0
    for att in base.glob("*/a*"):
        if att.is_dir() and AttemptJournal(att).stage == Stage.CLOSED and now - att.stat().st_mtime > keep_s \
                and not AttemptJournal(att).transfers_open:
            shutil.rmtree(att, ignore_errors=True)
            removed += 1
    return removed

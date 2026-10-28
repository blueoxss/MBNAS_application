"""Runner-side artifact transfer into the customer-side artifact store (WP-5C; DD-A6, DD-A38, DD-A39).

Modes (``RunnerSettings.artifact_transfer``):

* ``none`` (default) - nothing is transferred and the daemon behaves exactly as before WP-5: no journal entries, no
  ``outputs.artifact_transfer`` in results;
* ``local`` - the store is a directory on this host (``RunnerSettings.artifact_store_dir`` = the gateway's
  ``GatewaySettings.artifact_store``, same-host deployment). Every file the core accepted a registration for - E6
  checkpoints, and the E7 evaluation artifacts, executed ``architecture.yaml``, trainer plots and ``evaluation_detail``
  JSON - is placed at ``<store>/<sha256[:2]>/<sha256>`` with the DD-A39 meta ``<store>/<sha256[:2]>/<sha256>.json``
  ``{run_id, artifact_id, relpath, kind, bytes, mime, registered_by, job_id, attempt, received_at, source: "local"}``
  (``artifact_id`` = the core's artifact id, or the checkpoint id of an E6 checkpoint whose ``kind`` is
  ``checkpoint``; ``registered_by`` = this runner's id; ``relpath`` is run-directory relative);
* ``upload`` (runner -> gateway ``PUT /api/v1/internal/artifacts/{sha256}``) is reserved: upload mode is not
  implemented in 0.1.0 (the settings refuse it; ``gateway_url`` / ``gateway_upload_token_file`` are never read).

Writing one object (:meth:`LocalArtifactStore.put`):

1. ``<sha>`` already present: its bytes are re-read and hashed. Identical -> no-op (``exists``; the meta is written only
   when it is missing - the first registration's meta stays). Different -> ``STORE_CONFLICT``: refused, logged and
   recorded in the journal, never overwritten and never retried;
2. otherwise the source is staged under a temp name ``.tmp-<sha256>.<pid>.<random>.part`` in the shard directory (the
   gateway's readers ignore ``.tmp-*`` names and its ``sweep_tmp`` removes crash leftovers) - a hard link first (same
   inode, no byte copy; skipped for items registered with ``link=False``, see below), a byte copy when the link is
   impossible (``EXDEV``: another filesystem or mount, ``EPERM``, ``EMLINK``, ...); the copy is fsynced;
3. the staged bytes are re-read: sha256 and size must equal the registration, else ``SOURCE_CHANGED`` (the file changed
   after it was registered) and nothing is published;
4. the temp name is published with ``link(tmp, final)``, which never overwrites (``EEXIST`` -> step 1 against the
   object that won the race), then removed; the shard directory is fsynced;
5. the meta is written the same way, after the bytes (temp + fsync + no-overwrite publish, mode 0644): exactly the
   DD-A39 keys, ``bytes``/``attempt`` integers, ``received_at`` fixed-width UTC (``2026-10-27T04:16:21.407324Z``), a
   ``mime`` that is not a plain media type replaced by ``application/octet-stream``; an existing meta is never touched.

Permission bits (``RunnerSettings.artifact_store_mode``; checkpoints are written 0600 by their writer, a hard link
shares the inode and so its mode): ``same-uid`` (default) - the gateway runs as the runner's user: links and copies keep
the source's bits (a copy without group/other write); ``link-0644`` - the gateway runs as another user and the operator accepts that linking makes the
run-directory file world-readable: a linked object that is not readable by others is set to 0644 (the run-directory
file with it), copies are 0644; ``copy-0644`` - the gateway runs as another user and run-directory files keep their
mode: a source that is not readable by others is copied (0644) instead of linked.

A store object never appears with unverified bytes and is never replaced. A hard-linked object shares its inode with
the run-directory file, so a writer that later rewrites that file *in place* would change the stored bytes under their
digest name: the daemon therefore registers files whose writer rewrites them in place (the executed
``architecture.yaml``, ``ultralytics_adapter._write_arch_yaml`` uses ``write_text``; the trainer's own plots) with
``link=False`` (always copied); checkpoints, evaluation panels/JSON and weights are written by atomic replace (new
inode) and are linked. The gateway still recomputes the sha256 on download (DD-A39, 409 ``LOAD_FAILED``).

Journal and retries (:class:`ArtifactTransfer`): the items of one registration (one checkpoint, one E7 batch) are
written to ``<journal>/transfers/<n>.json`` *before* any store write (outbox rule), tried once right after the
registration, and their outcome is written back. A failure never fails the job: the result commit carries
``outputs.artifact_transfer = {mode, ok, transferred, failed: [...]}`` (the core treats an artifact whose bytes are
not in the store as not downloadable) and the failed items are retried by :meth:`ArtifactTransfer.retry_pending`, which
the daemon calls on its heartbeat (in a background thread, never in the heartbeat thread itself) and in its idle loop:
exponential backoff from the heartbeat interval, at most ``artifact_transfer_max_tries`` tries per item, then
``abandoned``. ``STORE_CONFLICT`` and malformed registrations are ``refused`` at once. ``result.json`` stays frozen; a
later successful retry only makes the bytes appear in the store.

Nothing that crosses the wire carries a host path: the result summary has artifact ids, run-relative ``relpath``,
digests and codes; absolute paths and error texts stay in the local journal and log.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from mbnas_contracts.util.time import utcnow_iso
from mbnas_runner.journal import AttemptJournal, transfer_entries

MODES: tuple[str, ...] = ("none", "local")
RESERVED_MODES: tuple[str, ...] = ("upload",)
STORE_MODES: tuple[str, ...] = ("same-uid", "link-0644", "copy-0644")
SOURCE = "local"
TMP_PREFIX = ".tmp-"  # the store's temp-name prefix (DD-A39 store readers skip it; the gateway sweeps stale ones)
READABLE_MODE = 0o644
#: a media type the gateway accepts as a header value (type/subtype + at most four simple parameters); same rule as the
#: gateway store's meta check, restated here by specification (the runner imports nothing from the gateway)
MIME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}"
                     r"(?: *; *[A-Za-z0-9!#$&^_.+-]{1,64}=[A-Za-z0-9!#$&^_.+-]{1,64}){0,4}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")  # used with fullmatch (a "$" anchor would accept a trailing newline)
DEFAULT_MIME = "application/octet-stream"
META_KEYS: tuple[str, ...] = ("run_id", "artifact_id", "relpath", "kind", "bytes", "mime", "registered_by", "job_id",
                              "attempt", "received_at", "source")
CHUNK = 1 << 20
MAX_FAILED_LISTED = 100
MAX_BACKOFF_S = 3600.0
#: roles whose files are rewritten in place by their writer: never hard-linked (see module docstring)
IN_PLACE_ROLES = frozenset({"execution_configuration", "native_image"})

PENDING, OK, FAILED, REFUSED, ABANDONED = "pending", "ok", "failed", "refused", "abandoned"
#: errno values of link() that mean "this filesystem/mount cannot hard-link here" (publish falls back to rename)
_NO_LINK = {errno.EPERM, errno.EMLINK, errno.EXDEV, errno.ENOSYS, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EACCES}


class TransferError(Exception):
    """One store write failed. ``code`` is the journal/result code, ``retryable`` False means refused for good."""

    def __init__(self, code: str, message: str, *, retryable: bool = True, errno_name: str | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.errno_name = errno_name


def _errno_name(exc: BaseException) -> str | None:
    num = getattr(exc, "errno", None)
    return errno.errorcode.get(num) if isinstance(num, int) else None


def _unlink_quiet(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def sha256_nofollow(path: str | os.PathLike[str], *, fsync: bool = False, chmod: int | None = None,
                    expect: tuple[str, int] | None = None) -> tuple[str, int]:
    """(hex sha256, size) of the regular file ``path`` read without following a final symlink (``O_NOFOLLOW``);
    ``fsync`` also flushes it to stable storage (allowed on a read-only descriptor on Linux); ``chmod`` sets these
    permission bits through the same descriptor (no path race), only when the bytes match ``expect`` (sha256, size)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        h = hashlib.sha256()
        size = 0
        while True:
            block = os.read(fd, CHUNK)
            if not block:
                break
            h.update(block)
            size += len(block)
        if fsync:
            os.fsync(fd)
        if chmod is not None and (expect is None or expect == (h.hexdigest(), size)):
            os.fchmod(fd, chmod)
        return h.hexdigest(), size
    finally:
        os.close(fd)


class LocalArtifactStore:
    """The DD-A39 content-addressed directory store, written from the runner host (``local`` mode)."""

    def __init__(self, root: str | os.PathLike[str], *, mode: str = "same-uid") -> None:
        if mode not in STORE_MODES:
            raise ValueError(f"artifact store mode must be one of {list(STORE_MODES)}, got {mode!r}")
        self.root = Path(root)
        self.mode = mode
        self.last_link_errno: str | None = None  # why the latest hard-link attempt fell back to a copy (diagnostics)

    @staticmethod
    def _tmp_name(directory: Path, stem: str) -> Path:
        return directory / f"{TMP_PREFIX}{stem}.{os.getpid()}.{secrets.token_hex(6)}.part"

    def object_path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / sha256

    def meta_path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / f"{sha256}.json"

    # -- seams (tests replace these to simulate EXDEV and friends) ----------------------------------------------------
    def _link(self, src: Path, dst: Path) -> None:
        """Stage ``src`` as ``dst`` by hard link (the link itself if ``src`` were a symlink; checked afterwards)."""
        os.link(src, dst, follow_symlinks=False)

    # -- one object -------------------------------------------------------------------------------------------------
    def put(self, src: str | os.PathLike[str], sha256: str, size: int, meta: dict[str, Any], *,
            allow_link: bool = True) -> str:
        """Store ``src`` (registered as ``sha256`` / ``size``) and its meta; returns ``link`` | ``copy`` | ``exists``.

        Raises :class:`TransferError` (see module docstring for the codes)."""
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            raise TransferError("DIGEST_INVALID", "registered sha256 is not 64 lower-case hex digits", retryable=False)
        final = self.object_path(sha256)
        shard = final.parent
        try:
            shard.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TransferError("STORE_UNAVAILABLE", f"cannot create the store shard directory {shard}: {exc}",
                                errno_name=_errno_name(exc)) from exc
        if os.path.islink(shard) or not os.path.isdir(shard):  # the gateway refuses such a layout as well
            raise TransferError("STORE_UNAVAILABLE", f"store layout is damaged: {shard} is not a plain directory")
        if self._existing(final, sha256):
            method = "exists"
        else:
            tmp, method = self._stage(Path(src), shard, sha256, int(size), allow_link)
            try:
                published = self._publish(tmp, final)
            finally:
                _unlink_quiet(tmp)
            if not published:  # another writer published the same digest first: it must hold the same bytes
                if not self._existing(final, sha256):
                    raise TransferError("STORE_WRITE_FAILED", f"{final.name} vanished while it was being published")
                method = "exists"
        self._write_meta(sha256, meta)
        return method

    def _existing(self, final: Path, sha256: str) -> bool:
        """False when absent; True when present with bytes hashing to its name; ``STORE_CONFLICT`` otherwise."""
        try:
            st = os.lstat(final)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise TransferError("STORE_UNAVAILABLE", f"cannot stat {final}: {exc}", errno_name=_errno_name(exc)) from exc
        if not stat.S_ISREG(st.st_mode):
            raise TransferError("STORE_CONFLICT", f"store entry {final} exists and is not a regular file",
                                retryable=False)
        try:
            digest, _ = sha256_nofollow(final)
        except OSError as exc:
            raise TransferError("STORE_UNAVAILABLE", f"existing store object {final} unreadable: {exc}",
                                errno_name=_errno_name(exc)) from exc
        if digest != sha256:
            raise TransferError("STORE_CONFLICT", f"store object {final} exists but its bytes hash to {digest}; "
                                "refusing to overwrite it", retryable=False)
        return True

    def _stage(self, src: Path, shard: Path, sha256: str, size: int, allow_link: bool) -> tuple[Path, str]:
        """(temp path in ``shard`` holding verified bytes, ``link`` | ``copy``)."""
        self.last_link_errno = None
        try:
            st = os.lstat(src)
        except FileNotFoundError as exc:
            raise TransferError("SOURCE_MISSING", f"registered file {src} is gone", errno_name="ENOENT") from exc
        except OSError as exc:
            raise TransferError("SOURCE_UNREADABLE", f"cannot stat {src}: {exc}", errno_name=_errno_name(exc)) from exc
        if not stat.S_ISREG(st.st_mode):
            raise TransferError("SOURCE_MISSING", f"registered path {src} is not a regular file")
        if st.st_size != size:
            raise TransferError("SOURCE_CHANGED", f"{src} has {st.st_size} bytes, {size} were registered")
        tmp = self._tmp_name(shard, sha256)
        readable = bool(st.st_mode & stat.S_IROTH)  # the gateway can read it under another uid
        if self.mode == "copy-0644" and not readable:
            allow_link = False  # linking would hand the gateway a file it cannot read (or need a chmod of the source)
        method = ""
        if allow_link:
            try:
                self._link(src, tmp)
            except OSError as exc:
                self.last_link_errno = _errno_name(exc) or type(exc).__name__
                _unlink_quiet(tmp)
            else:
                method = "link"
                try:
                    if not stat.S_ISREG(os.lstat(tmp).st_mode):  # src was swapped for a symlink after the lstat
                        _unlink_quiet(tmp)
                        method = ""
                except OSError:
                    _unlink_quiet(tmp)
                    method = ""
        if not method:
            keep = self.mode == "same-uid" or readable
            # a copy is never writable by group/others (nothing may modify a stored object)
            self._copy(src, tmp, (stat.S_IMODE(st.st_mode) if keep else READABLE_MODE) & ~0o022)
            method = "copy"
        chmod = READABLE_MODE if method == "link" and self.mode == "link-0644" and not readable else None
        try:
            digest, n = sha256_nofollow(tmp, fsync=True, chmod=chmod, expect=(sha256, size))
        except OSError as exc:
            _unlink_quiet(tmp)
            raise TransferError("STORE_WRITE_FAILED", f"staged copy of {src} unreadable: {exc}",
                                errno_name=_errno_name(exc)) from exc
        if digest != sha256 or n != size:
            _unlink_quiet(tmp)
            raise TransferError("SOURCE_CHANGED", f"{src} no longer matches its registration (sha256 {digest}, "
                                f"{n} bytes); nothing stored")
        return tmp, method

    def _copy(self, src: Path, tmp: Path, mode: int) -> None:
        try:
            fd_in = os.open(src, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as exc:
            raise TransferError("SOURCE_MISSING", f"registered file {src} is gone", errno_name="ENOENT") from exc
        except OSError as exc:  # ELOOP: a symlink now sits at the registered path
            raise TransferError("SOURCE_UNREADABLE", f"cannot open {src}: {exc}", errno_name=_errno_name(exc)) from exc
        try:
            if not stat.S_ISREG(os.fstat(fd_in).st_mode):
                raise TransferError("SOURCE_MISSING", f"registered path {src} is not a regular file")
            try:
                fd_out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode or 0o600)
            except OSError as exc:
                raise TransferError("STORE_WRITE_FAILED", f"cannot create {tmp}: {exc}",
                                    errno_name=_errno_name(exc)) from exc
            try:
                os.fchmod(fd_out, mode or 0o600)  # the umask must not change what the hard link would have kept
                while True:
                    block = os.read(fd_in, CHUNK)
                    if not block:
                        break
                    _write_all(fd_out, block)
                os.fsync(fd_out)
            except OSError as exc:
                os.close(fd_out)
                _unlink_quiet(tmp)
                raise TransferError("STORE_WRITE_FAILED", f"copying {src} into the store failed: {exc}",
                                    errno_name=_errno_name(exc)) from exc
            os.close(fd_out)
        finally:
            os.close(fd_in)

    def _publish(self, tmp: Path, final: Path) -> bool:
        """Give ``tmp`` the name ``final`` without overwriting; False when ``final`` already existed."""
        try:
            os.link(tmp, final, follow_symlinks=False)
        except FileExistsError:
            return False
        except OSError as exc:
            if exc.errno not in _NO_LINK:
                raise TransferError("STORE_WRITE_FAILED", f"cannot publish {final}: {exc}",
                                    errno_name=_errno_name(exc)) from exc
            # a filesystem without hard links: rename when absent (a racing writer can only hold the same verified bytes)
            if os.path.lexists(final):
                return False
            try:
                os.rename(tmp, final)
            except OSError as exc2:
                raise TransferError("STORE_WRITE_FAILED", f"cannot publish {final}: {exc2}",
                                    errno_name=_errno_name(exc2)) from exc2
        try:
            _fsync_dir(final.parent)
        except OSError:
            pass
        return True

    def _write_meta(self, sha256: str, meta: dict[str, Any]) -> bool:
        """Write ``<sha>.json`` unless it exists (first registration wins); True when written."""
        path = self.meta_path(sha256)
        if os.path.lexists(path):
            return False
        data = (json.dumps(meta, indent=1, sort_keys=True, allow_nan=False, ensure_ascii=False) + "\n").encode("utf-8")
        tmp = self._tmp_name(path.parent, path.name)
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, READABLE_MODE)
            try:
                os.fchmod(fd, READABLE_MODE)
                _write_all(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            return self._publish(tmp, path)
        except OSError as exc:
            raise TransferError("STORE_WRITE_FAILED", f"cannot write the meta {path}: {exc}",
                                errno_name=_errno_name(exc)) from exc
        finally:
            _unlink_quiet(tmp)

    def read_meta(self, sha256: str) -> dict[str, Any] | None:
        try:
            return json.loads(self.meta_path(sha256).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def normal_sha(value: Any) -> str:
    """Lower-case hex digest (an optional ``sha256:`` prefix is tolerated)."""
    text = str(value or "").strip().lower()
    return text[7:] if text.startswith("sha256:") else text


def transfer_item(*, obj: str, artifact_id: str, run_id: str, job_id: str, attempt: int, relpath: str,
                  path: str | os.PathLike[str], sha256: str, size: int, kind: str, mime: str | None, link: bool,
                  registered_by: str, **detail: Any) -> dict[str, Any]:
    """One journal item: what was registered (``obj`` = ``checkpoint`` (E6) | ``artifact`` (E7)) and where the bytes are
    on this host (``path``, absolute; journal only - never sent)."""
    item: dict[str, Any] = {"object": obj, "artifact_id": str(artifact_id), "run_id": str(run_id), "job_id": str(job_id),
                            "attempt": int(attempt), "relpath": str(relpath), "path": str(path),
                            "sha256": normal_sha(sha256), "bytes": int(size), "kind": str(kind),
                            "mime": mime or DEFAULT_MIME, "link": bool(link), "registered_by": str(registered_by),
                            "state": PENDING, "tries": 0, "method": None, "code": None, "error": None, "errno": None,
                            "next_try_at": None, "last_try_at": None, "done_at": None}
    if detail:
        item["detail"] = {k: v for k, v in detail.items() if v is not None}
    return item


def store_meta(item: dict[str, Any], received_at: str | None = None) -> dict[str, Any]:
    """The DD-A39 meta of one stored object (exactly :data:`META_KEYS`)."""
    mime = item.get("mime")
    return {"run_id": item["run_id"], "artifact_id": item["artifact_id"], "relpath": item["relpath"],
            "kind": item["kind"], "bytes": int(item["bytes"]),
            "mime": mime if isinstance(mime, str) and MIME_RE.fullmatch(mime) else DEFAULT_MIME,
            "registered_by": item["registered_by"], "job_id": item["job_id"], "attempt": int(item["attempt"]),
            "received_at": received_at or utcnow_iso(), "source": SOURCE}


class ArtifactTransfer:
    """Journalled, retried transfers of registered files for one runner (see module docstring).

    ``settings`` needs ``artifact_transfer``, ``artifact_store_dir``, ``runner_id`` and optionally
    ``artifact_store_mode`` (default ``same-uid``), ``artifact_transfer_max_tries`` (default 6) and ``heartbeat_s``
    (the first retry delay, doubled per try)."""

    def __init__(self, settings: Any, *, log: Callable[[str], None] | None = None) -> None:
        self.mode = str(getattr(settings, "artifact_transfer", "none") or "none")
        store_dir = getattr(settings, "artifact_store_dir", None)
        self.store = LocalArtifactStore(store_dir, mode=str(getattr(settings, "artifact_store_mode", "same-uid")
                                                           or "same-uid")) if self.mode == "local" and store_dir else None
        self.runner_id = str(getattr(settings, "runner_id", "") or "")
        self.max_tries = max(1, int(getattr(settings, "artifact_transfer_max_tries", 6) or 6))
        self.retry_base_s = max(0.1, float(getattr(settings, "heartbeat_s", 10) or 10))
        self._log = log or (lambda _msg: None)
        self._lock = threading.RLock()        # one writer of the store and of the transfers/ journal entries at a time
        self._retry_lock = threading.Lock()   # one retry pass at a time
        self.pending_hint = self.enabled      # unknown journal state at start: the first retry pass scans everything

    @property
    def enabled(self) -> bool:
        return self.mode == "local" and self.store is not None

    # -- the first try, right after a registration ---------------------------------------------------------------
    def transfer(self, journal: AttemptJournal, items: Iterable[dict[str, Any]], *,
                 now: float | None = None) -> list[dict[str, Any]]:
        """Journal ``items`` (before any store write), try each once; returns them with their outcome."""
        batch = [dict(it) for it in items]
        if not self.enabled or not batch:
            return []
        with self._lock:
            path = journal.record_transfers(self.mode, batch)
            entry = {"mode": self.mode, "items": batch}
            self._attempt(journal, path, entry, range(len(batch)), time.time() if now is None else now)
            return entry["items"]

    # -- later tries ---------------------------------------------------------------------------------------------
    def retryable(self, item: dict[str, Any]) -> bool:
        return item.get("state") in (PENDING, FAILED) and int(item.get("tries") or 0) < self.max_tries

    def due(self, item: dict[str, Any], now: float) -> bool:
        return self.retryable(item) and float(item.get("next_try_at") or 0.0) <= now

    def retry_pending(self, work_root: str | os.PathLike[str], *, now: float | None = None) -> dict[str, int]:
        """Retry every due item of every journal attempt under ``work_root``; returns counts (``busy`` when another pass
        is running). Clears :attr:`pending_hint` when nothing retryable is left."""
        counts = {"tried": 0, "ok": 0, "failed": 0, "refused": 0, "abandoned": 0, "left": 0}
        if not self.enabled:
            return counts
        if not self._retry_lock.acquire(blocking=False):
            return {**counts, "busy": 1}
        try:
            when = time.time() if now is None else now
            left = 0
            for journal, path in transfer_entries(work_root):
                with self._lock:
                    try:
                        entry = journal.read_transfers(path)
                    except (OSError, ValueError) as exc:
                        self._log(f"artifact transfer journal entry {path} unreadable: {exc}")
                        continue
                    items = entry.get("items") or []
                    due = [i for i, it in enumerate(items) if self.due(it, when)]
                    if due:
                        self._attempt(journal, path, entry, due, when)
                        for i in due:
                            counts["tried"] += 1
                            state = items[i]["state"]
                            counts[state if state in counts else "failed"] += 1
                    left += sum(1 for it in items if self.retryable(it))
            counts["left"] = left
            self.pending_hint = left > 0
            return counts
        finally:
            self._retry_lock.release()

    # -- result summary ------------------------------------------------------------------------------------------
    def summary(self, journal: AttemptJournal) -> dict[str, Any] | None:
        """``outputs.artifact_transfer`` of this attempt's result: ``{mode, ok, transferred, failed: [...]}`` (plus
        ``failed_total`` when the list is cut at :data:`MAX_FAILED_LISTED`); None when transfers are off."""
        if not self.enabled:
            return None
        best: dict[tuple[str, str, str], dict[str, Any]] = {}
        for _, entry in journal.transfers():
            for it in entry.get("items") or []:
                key = (str(it.get("object")), str(it.get("artifact_id")), str(it.get("sha256")))
                if key not in best or it.get("state") == OK:
                    best[key] = it
        done = [it for it in best.values() if it.get("state") == OK]
        bad = [it for it in best.values() if it.get("state") != OK]
        failed = [{"artifact_id": it.get("artifact_id"), "object": it.get("object"), "kind": it.get("kind"),
                   "relpath": it.get("relpath"), "sha256": it.get("sha256"), "code": it.get("code") or "PENDING",
                   "errno": it.get("errno"), "retry": self.retryable(it)} for it in bad[:MAX_FAILED_LISTED]]
        out: dict[str, Any] = {"mode": self.mode, "ok": not bad, "transferred": len(done), "failed": failed}
        if len(bad) > MAX_FAILED_LISTED:
            out["failed_total"] = len(bad)
        return out

    # -- internals -----------------------------------------------------------------------------------------------
    def _attempt(self, journal: AttemptJournal, path: Path, entry: dict[str, Any], indices: Iterable[int],
                 now: float) -> None:
        """Try the items at ``indices`` once each and write the entry back (one journal write per entry)."""
        assert self.store is not None
        items = entry["items"]
        retrying: list[dict[str, Any]] = []
        for i in indices:
            it = items[i]
            it["tries"] = int(it.get("tries") or 0) + 1
            it["last_try_at"] = utcnow_iso()
            try:
                method = self.store.put(it["path"], it["sha256"], int(it["bytes"]), store_meta(it),
                                        allow_link=bool(it.get("link", True)))
            except TransferError as exc:
                self._failed(it, exc.code, exc.message, exc.errno_name, exc.retryable, now)
            except Exception as exc:  # noqa: BLE001 - an unexpected error is recorded like a retryable failure
                self._failed(it, "TRANSFER_ERROR", f"{type(exc).__name__}: {exc}", _errno_name(exc), True, now)
            else:
                it.update(state=OK, method=method, code=None, error=None, errno=None, next_try_at=None,
                          done_at=utcnow_iso())
                if method == "copy" and it.get("link", True) and self.store.last_link_errno:
                    it["link_fallback"] = self.store.last_link_errno
                continue
            if it["state"] == FAILED:
                retrying.append(it)
        journal.update_transfers(path, entry)
        if retrying:  # one line per entry (a batch can hold hundreds of files); each item's error is in the journal
            first = retrying[0]
            codes = sorted({str(it["code"]) for it in retrying})
            self._log(f"artifact transfer failed for {len(retrying)} file(s) of job {first['job_id']} attempt "
                      f"{first['attempt']} (codes {codes}, e.g. {first['error']}); retrying with backoff, at most "
                      f"{self.max_tries} tries per file")

    def _failed(self, it: dict[str, Any], code: str, message: str, errno_name: str | None, retryable: bool,
                now: float) -> None:
        it.update(code=code, error=message[:500], errno=errno_name)
        if not retryable:
            it["state"], it["next_try_at"] = REFUSED, None
            self._log(f"artifact transfer refused: {it['object']} {it['artifact_id']} sha256 {it['sha256'][:16]} "
                      f"({code}): {message}")
            return
        if int(it["tries"]) >= self.max_tries:
            it["state"], it["next_try_at"] = ABANDONED, None
            self._log(f"artifact transfer abandoned after {it['tries']} tries: {it['object']} {it['artifact_id']} "
                      f"({code}): {message}")
            return
        delay = min(self.retry_base_s * (2 ** (int(it["tries"]) - 1)), MAX_BACKOFF_S)
        it["state"], it["next_try_at"] = FAILED, now + delay
        self.pending_hint = True

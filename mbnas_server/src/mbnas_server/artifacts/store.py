"""Content-addressed artifact store of the gateway (WP-5 contract section 1; DD-A6, DD-A38, DD-A39).

Layout (DD-A39; the runner ``local`` transfer mode of WP-5C writes the same layout into the same directory)::

    <root>/<sha256[:2]>/<sha256>        the bytes: a regular file (never followed when it is a symlink)
    <root>/<sha256[:2]>/<sha256>.json   meta: run_id, artifact_id, relpath, kind, bytes, mime, registered_by, job_id,
                                        attempt, received_at, source ("local" | "upload")

Rules:

* **Keys only.** The one key is the lowercase hex SHA-256 of the bytes, checked with ``re.fullmatch("[0-9a-f]{64}")``
  by every public method before a path is built (``SHA256_INVALID`` otherwise). No caller string other than a valid
  digest ever becomes a path component; ``meta.relpath`` is recorded data (validated as a run-relative path) and is
  never resolved by the store.
* **Verified, atomic writes.** Bytes stream into ``<root>/<sha256[:2]>/.tmp-*`` while SHA-256 is recomputed; the temp
  file is fsynced and compared with the expected digest (and the declared size) and only then linked to its final
  name, after which the directory is fsynced. A digest or size mismatch raises ``StoreError("LOAD_FAILED")`` and stores
  nothing. The meta is written the same way after the bytes.
* **Idempotent.** A put of a digest that is already stored is a no-op that returns the stored object without reading
  the stream; it only writes a missing meta (repair after a crash between the two writes). Final names are created
  with ``os.link`` (no clobber), so of several concurrent puts of one digest exactly one reports ``created=True`` and
  the meta written first wins; on a file system without hard links ``os.replace`` is used (same bytes, but the
  ``created`` flag and the meta then follow the last writer). A stored file whose size differs from freshly verified
  bytes is corrupt and is replaced; a declared size that differs from the stored size therefore makes a put stream.
* **Crash safety.** An interrupted put leaves at most a ``.tmp-*`` file. Readers only see 64-hex names, so it is
  ignored; :meth:`ArtifactStore.sweep_tmp` removes temp files older than an hour.
* **Size cap.** ``max_bytes`` (default 4 GiB; ``GatewaySettings.artifact_max_bytes`` when that field exists) is enforced
  while streaming and, when the size is declared, before anything is read (``PAYLOAD_TOO_LARGE``).
* **Errors.** :class:`StoreError` is an :class:`mbnas_contracts.errors.ApiError` with ``location="store"``, so a gateway
  route can let it reach the existing Error handler: ARTIFACT_NOT_READY 404, LOAD_FAILED 409, PAYLOAD_TOO_LARGE 413,
  SHA256_INVALID 422, META_INVALID 422, STORE_UNAVAILABLE 507. Messages never contain a filesystem path.

Entries outside the layout (the ``uploads/`` directory of the model-config upload route, doctor probe files, any name
that is not a 2-hex fan-out directory holding 64-hex objects) are not artifacts and are ignored.

Source: new in WP-5A (no counterpart in the original single-host code, which served files from run directories).
"""
from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from mbnas_contracts.errors import ApiError, ErrorLocation
from mbnas_contracts.util.paths import nearest_existing_ancestor
from mbnas_contracts.util.time import utcnow_iso

log = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 4 << 30
"""Default size cap of one object (4 GiB, WP-5 contract section 1)."""
DEFAULT_CHUNK_BYTES = 1 << 20
TMP_PREFIX = ".tmp-"
META_SUFFIX = ".json"
META_MAX_BYTES = 64 << 10
TMP_MAX_AGE_S = 3600.0
META_KEYS = ("run_id", "artifact_id", "relpath", "kind", "bytes", "mime", "registered_by", "job_id", "attempt",
             "received_at", "source")
"""DD-A39 meta keys; every stored meta has all of them (``None`` when unknown)."""
SOURCES = ("local", "upload")
STATUS = {"ARTIFACT_NOT_READY": 404, "LOAD_FAILED": 409, "PAYLOAD_TOO_LARGE": 413, "SHA256_INVALID": 422,
          "META_INVALID": 422, "STORE_UNAVAILABLE": 507}
MIME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}"
                     r"(?: *; *[A-Za-z0-9!#$&^_.+-]{1,64}=[A-Za-z0-9!#$&^_.+-]{1,64}){0,4}")
"""Media type (``type/subtype`` plus at most four simple ``; key=value`` parameters); safe as an HTTP header value."""

_SHA256 = re.compile(r"[0-9a-f]{64}")
_FANOUT = re.compile(r"[0-9a-f]{2}")
_TEXT_KEYS = ("run_id", "artifact_id", "kind", "registered_by", "job_id")
_TEXT_MAX = 256
_RELPATH_MAX = 1024
_NO_HARDLINK = frozenset({errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EMLINK})
_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


class StoreError(ApiError):
    """Store failure: contract error ``code``, the HTTP ``status`` a route answers with, ``location = "store"``."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(STATUS.get(code, 409) if status is None else status, code, message)
        self.location = ErrorLocation.STORE.value


@dataclass(frozen=True)
class StoredArtifact:
    """One stored object. ``path`` is a server-side location: never put it in a client response."""

    sha256: str
    bytes: int
    path: Path
    meta: dict[str, Any] | None
    created: bool = False


# --------------------------------------------------------------------------------------------------- validation
def is_sha256(value: object) -> bool:
    """True for a lowercase hex SHA-256 string (exactly 64 characters ``[0-9a-f]``, no trailing newline)."""
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def check_sha256(value: object) -> str:
    """``value`` when it is a valid key, else ``StoreError("SHA256_INVALID")`` (the value is not echoed)."""
    if not is_sha256(value):
        raise StoreError("SHA256_INVALID", "artifact key must be a lowercase hex SHA-256 (64 characters 0-9a-f)")
    return value  # type: ignore[return-value]


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _plain_text(value: str, limit: int) -> bool:
    return 0 < len(value) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def relpath_ok(value: object) -> bool:
    """Run-relative path rule of the runner protocol (no NUL, no leading ``/`` or ``\\``, no drive letter, no ``..``
    segment) plus a length limit and no control characters."""
    if not isinstance(value, str) or not _plain_text(value, _RELPATH_MAX):
        return False
    if value.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", value):
        return False
    return ".." not in value.replace("\\", "/").split("/")


def _bad_meta(message: str) -> StoreError:
    return StoreError("META_INVALID", message)


def clean_meta(meta: Mapping[str, Any] | None, *, default_source: str = "upload") -> dict[str, Any]:
    """Validated caller meta (every DD-A39 key except the store-owned ``received_at``; unknown keys are refused).

    ``bytes`` is the declared size (compared with the received bytes); ``source`` defaults to ``default_source``.
    """
    if meta is None:
        meta = {}
    if not isinstance(meta, Mapping):
        raise _bad_meta("meta must be an object")
    unknown = sorted(str(k) for k in meta if k not in META_KEYS or k == "received_at")
    if unknown:
        raise _bad_meta(f"meta keys not accepted: {', '.join(unknown)[:200]}")
    out: dict[str, Any] = {}
    for key in _TEXT_KEYS:
        value = meta.get(key)
        if value is not None and not (isinstance(value, str) and _plain_text(value, _TEXT_MAX)):
            raise _bad_meta(f"meta.{key} must be a string of 1..{_TEXT_MAX} characters without control characters")
        out[key] = value
    relpath = meta.get("relpath")
    if relpath is not None and not relpath_ok(relpath):
        raise _bad_meta("meta.relpath must be a run-relative path without '..', NUL, a drive or a leading '/'")
    out["relpath"] = relpath
    mime = meta.get("mime")
    if mime is not None and not (isinstance(mime, str) and MIME_RE.fullmatch(mime)):
        raise _bad_meta("meta.mime must be a media type such as image/png")
    out["mime"] = mime
    for key in ("attempt", "bytes"):
        value = meta.get(key)
        if value is not None and not _is_count(value):
            raise _bad_meta(f"meta.{key} must be a non-negative integer")
        out[key] = value
    source = meta.get("source", default_source)
    if source not in SOURCES:
        raise _bad_meta("meta.source must be 'local' or 'upload'")
    out["source"] = source
    return out


def _declared_size(declared_bytes: object, meta_bytes: int | None) -> int | None:
    if declared_bytes is None:
        return meta_bytes
    if not _is_count(declared_bytes):
        raise _bad_meta("declared size must be a non-negative integer")
    if meta_bytes is not None and meta_bytes != declared_bytes:
        raise _bad_meta("meta.bytes disagrees with the declared size")
    return declared_bytes  # type: ignore[return-value]


def _meta_doc(clean: Mapping[str, Any], size: int) -> dict[str, Any]:
    doc = {key: clean.get(key) for key in META_KEYS}
    doc.update(bytes=size, received_at=utcnow_iso(), source=clean.get("source", "upload"))
    return doc


# --------------------------------------------------------------------------------------------------- helpers
def _unavailable(message: str, exc: OSError | None = None) -> StoreError:
    detail = ""
    if exc is not None:  # class and errno name only: str(exc) carries the path
        detail = f" ({type(exc).__name__}{': ' + errno.errorcode.get(exc.errno, '') if exc.errno else ''})"
    return StoreError("STORE_UNAVAILABLE", message + detail)


def _not_ready(sha: str) -> StoreError:
    return StoreError("ARTIFACT_NOT_READY", f"artifact {sha} is not in the artifact store (not transferred yet)")


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    except OSError as exc:  # a few file systems refuse fsync on directories
        if exc.errno != errno.EINVAL:
            raise
    finally:
        os.close(fd)


def _unlink_quiet(path: str | None) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:  # left for sweep_tmp
        log.warning("artifact store: temp file could not be removed (%s)", type(exc).__name__)


def _iter_chunks(stream: Any, chunk_bytes: int) -> Iterator[Any]:
    """Chunks of a binary file-like object (``read``), a bytes-like object or a sync iterable of bytes-like chunks."""
    if isinstance(stream, (bytes, bytearray, memoryview)):
        yield stream
        return
    read = getattr(stream, "read", None)
    if callable(read):
        while True:
            block = read(chunk_bytes)
            if not block:
                return
            yield block
    if not isinstance(stream, Iterable):
        if hasattr(stream, "__aiter__"):
            raise TypeError("async streams cannot be read here: use ArtifactStore.begin_put() and write() each chunk")
        raise TypeError(f"unsupported stream type {type(stream).__name__}")
    yield from stream


# --------------------------------------------------------------------------------------------------- writer
class PendingPut:
    """One put in progress (:meth:`ArtifactStore.begin_put`): :meth:`write` chunks, then :meth:`commit`.

    When the digest is already stored the put is a no-op: :attr:`reused` holds the stored object, nothing is written
    and :meth:`commit` returns it (callers skip reading the request body). As a context manager an exception or a
    missing :meth:`commit` aborts (the temp file is removed). Async routes read chunks from the request stream and run
    :meth:`write` / :meth:`commit` in a worker thread (blocking file I/O).
    """

    def __init__(self, store: "ArtifactStore", sha256: str, meta: dict[str, Any], declared: int | None,
                 reused: StoredArtifact | None) -> None:
        self.sha256 = sha256
        self.reused = reused
        self._store = store
        self._meta = meta
        self._declared = declared
        self._size = 0
        self._hasher = hashlib.sha256()
        self._fh: BinaryIO | None = None
        self._tmp: str | None = None
        self._state = "reused" if reused is not None else "open"
        if reused is None:
            directory = store._fanout_dir(sha256, create=True)
            try:
                fd, self._tmp = tempfile.mkstemp(prefix=f"{TMP_PREFIX}{sha256}.", suffix=".part", dir=directory)
            except OSError as exc:
                raise _unavailable("artifact store: temporary file cannot be created", exc) from exc
            self._fh = os.fdopen(fd, "wb")

    @property
    def received_bytes(self) -> int:
        return self._size

    def write(self, chunk: bytes | bytearray | memoryview) -> None:
        """Append one chunk (size cap and declared size are checked before it is written)."""
        if self._state != "open":
            raise RuntimeError(f"nothing to write: the put is {self._state}")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            self.abort()
            raise TypeError(f"artifact chunks must be bytes-like, got {type(chunk).__name__}")
        size = memoryview(chunk).nbytes
        if not size:
            return
        total = self._size + size
        if total > self._store.max_bytes:
            self.abort()
            raise StoreError("PAYLOAD_TOO_LARGE", f"artifact is larger than the store limit of {self._store.max_bytes} bytes")
        if self._declared is not None and total > self._declared:
            self.abort()
            raise StoreError("LOAD_FAILED", f"artifact {self.sha256}: more bytes than the declared {self._declared}")
        try:
            self._fh.write(chunk)  # type: ignore[union-attr]
        except OSError as exc:
            self.abort()
            raise _unavailable("artifact store: write failed", exc) from exc
        self._hasher.update(chunk)
        self._size = total

    def commit(self) -> StoredArtifact:
        """fsync, verify size and SHA-256, link to the final name, fsync the directory, write the meta."""
        if self._state == "reused":
            return self.reused  # type: ignore[return-value]
        if self._state != "open":
            raise RuntimeError(f"cannot commit: the put is {self._state}")
        fh = self._fh
        try:
            fh.flush()  # type: ignore[union-attr]
            os.fchmod(fh.fileno(), self._store.file_mode)  # type: ignore[union-attr]
            os.fsync(fh.fileno())  # type: ignore[union-attr]
            fh.close()  # type: ignore[union-attr]
        except OSError as exc:
            self.abort()
            raise _unavailable("artifact store: write failed", exc) from exc
        if self._declared is not None and self._size != self._declared:
            self.abort()
            raise StoreError("LOAD_FAILED", f"artifact {self.sha256}: received {self._size} bytes, declared {self._declared}")
        digest = self._hasher.hexdigest()
        if digest != self.sha256:
            self.abort()
            raise StoreError("LOAD_FAILED", f"sha256 of the received bytes is {digest}, expected {self.sha256}")
        try:
            created = self._store._publish(self._tmp, self.sha256, self._size)  # type: ignore[arg-type]
        except OSError as exc:
            self.abort()
            raise _unavailable("artifact store: the object cannot be stored", exc) from exc
        self._state = "committed"
        meta = self._store._settle_meta(self.sha256, _meta_doc(self._meta, self._size))
        if created:
            log.info("artifact store: stored %s (%d bytes, source=%s)", self.sha256, self._size, self._meta.get("source"))
        return StoredArtifact(self.sha256, self._size, self._store._object_path(self.sha256), meta, created=created)

    def abort(self) -> None:
        """Discard the put (idempotent): close and remove the temp file."""
        if self._state not in ("open", "reused"):
            return
        self._state = "aborted"
        if self._fh is not None and not self._fh.closed:
            try:
                self._fh.close()
            except OSError:
                pass
        _unlink_quiet(self._tmp)

    def __enter__(self) -> "PendingPut":
        return self

    def __exit__(self, *exc: object) -> None:
        if self._state == "open":
            self.abort()


# --------------------------------------------------------------------------------------------------- store
class ArtifactStore:
    """Content-addressed store rooted at ``root`` (absolute; created on the first put). See the module docstring."""

    def __init__(self, root: str | os.PathLike[str], *, max_bytes: int = DEFAULT_MAX_BYTES,
                 chunk_bytes: int = DEFAULT_CHUNK_BYTES, file_mode: int = 0o644) -> None:
        raw = os.fspath(root)
        if not isinstance(raw, str) or not os.path.isabs(raw):
            raise ValueError("artifact store root must be an absolute path")
        if not _is_count(max_bytes) or max_bytes <= 0 or not _is_count(chunk_bytes) or chunk_bytes <= 0:
            raise ValueError("max_bytes and chunk_bytes must be positive integers")
        self.root = Path(os.path.normpath(raw))
        self.max_bytes = int(max_bytes)
        self.chunk_bytes = int(chunk_bytes)
        self.file_mode = int(file_mode)

    @classmethod
    def from_settings(cls, settings: Any) -> "ArtifactStore":
        """Store of a ``GatewaySettings``-like object: root ``artifact_store`` (``artifact_store_dir`` if a later
        settings revision names it so), cap ``artifact_max_bytes`` when present, else 4 GiB."""
        root = getattr(settings, "artifact_store_dir", None) or getattr(settings, "artifact_store")
        max_bytes = getattr(settings, "artifact_max_bytes", None) or DEFAULT_MAX_BYTES
        return cls(root, max_bytes=max_bytes)

    def __repr__(self) -> str:
        return f"ArtifactStore(max_bytes={self.max_bytes})"

    # ---------------------------------------------------------------------------------------------- paths
    def path(self, sha256: str) -> Path:
        """``<root>/<sha256[:2]>/<sha256>`` of a valid key (existence is not checked)."""
        return self._object_path(check_sha256(sha256))

    def _object_path(self, sha: str) -> Path:
        return self.root / sha[:2] / sha

    def _meta_path(self, sha: str) -> Path:
        return self.root / sha[:2] / (sha + META_SUFFIX)

    def _fanout_dir(self, sha: str, *, create: bool = False) -> Path:
        directory = self.root / sha[:2]
        if create:
            try:
                if not os.path.isdir(self.root):
                    os.makedirs(self.root, exist_ok=True)
                    _fsync_dir(self.root.parent)
                try:
                    os.mkdir(directory)
                    _fsync_dir(self.root)
                except FileExistsError:
                    pass
            except OSError as exc:
                raise _unavailable("artifact store directory cannot be created", exc) from exc
            if os.path.islink(directory) or not os.path.isdir(directory):
                raise StoreError("STORE_UNAVAILABLE", "artifact store layout is damaged (fan-out entry is not a directory)")
        return directory

    def _object_size(self, sha: str) -> int | None:
        """Size of the stored object, or None when it is absent or not a regular file (symlink, FIFO, directory)."""
        directory = self.root / sha[:2]
        try:
            if os.path.islink(directory):
                return None
            st = os.lstat(directory / sha)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:
            raise _unavailable("artifact store is not readable", exc) from exc
        return st.st_size if stat.S_ISREG(st.st_mode) else None

    # ---------------------------------------------------------------------------------------------- reads
    def exists(self, sha256: str) -> bool:
        """True when the object is stored as a regular file (temp files, symlinks and other entries do not count)."""
        return self._object_size(check_sha256(sha256)) is not None

    def get(self, sha256: str) -> StoredArtifact | None:
        """The stored object (meta None when missing or unreadable), or None."""
        sha = check_sha256(sha256)
        size = self._object_size(sha)
        if size is None:
            return None
        return StoredArtifact(sha, size, self._object_path(sha), self._load_meta(sha, strict=False))

    def meta(self, sha256: str) -> dict[str, Any] | None:
        """Meta of a stored object; None when the object has none. ``ARTIFACT_NOT_READY`` when the object is absent,
        ``LOAD_FAILED`` when the meta file is unreadable."""
        sha = check_sha256(sha256)
        if self._object_size(sha) is None:
            raise _not_ready(sha)
        return self._load_meta(sha, strict=True)

    def _load_meta(self, sha: str, *, strict: bool) -> dict[str, Any] | None:
        try:
            fd = os.open(self._meta_path(sha), _OPEN_FLAGS)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:
            if strict:
                raise StoreError("LOAD_FAILED", f"meta of artifact {sha} cannot be read") from exc
            log.warning("artifact store: meta of %s cannot be read (%s)", sha, type(exc).__name__)
            return None
        try:
            with os.fdopen(fd, "rb") as fh:
                if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                    raise ValueError("meta is not a regular file")
                data = fh.read(META_MAX_BYTES + 1)
            if len(data) > META_MAX_BYTES:
                raise ValueError("meta file too large")
            doc = json.loads(data.decode("utf-8"))
            if not isinstance(doc, dict):
                raise ValueError("meta is not a JSON object")
        except (OSError, ValueError) as exc:
            if strict:
                raise StoreError("LOAD_FAILED", f"meta of artifact {sha} is unreadable") from exc
            log.warning("artifact store: meta of %s is unreadable (%s)", sha, type(exc).__name__)
            return None
        return doc

    def open_stream(self, sha256: str) -> BinaryIO:
        """Binary file object of the stored bytes (caller closes it). Symlinks are not followed and FIFOs do not
        block: anything but a regular file is ``LOAD_FAILED``; an absent object is ``ARTIFACT_NOT_READY``."""
        sha = check_sha256(sha256)
        directory = self.root / sha[:2]
        if os.path.islink(directory):
            raise StoreError("LOAD_FAILED", f"artifact {sha}: store layout is damaged (fan-out entry is a symlink)")
        try:
            fd = os.open(directory / sha, _OPEN_FLAGS)
        except (FileNotFoundError, NotADirectoryError):
            raise _not_ready(sha) from None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise StoreError("LOAD_FAILED", f"artifact {sha} is not a regular file in the store") from None
            raise _unavailable("artifact store is not readable", exc) from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise StoreError("LOAD_FAILED", f"artifact {sha} is not a regular file in the store")
        except BaseException:
            os.close(fd)
            raise
        return os.fdopen(fd, "rb")

    def verify(self, sha256: str) -> bool:
        """Re-hash the stored bytes: True when they still match their key."""
        sha = check_sha256(sha256)
        hasher = hashlib.sha256()
        with self.open_stream(sha) as fh:
            for block in iter(lambda: fh.read(self.chunk_bytes), b""):
                hasher.update(block)
        return hasher.hexdigest() == sha

    def iter_all(self) -> Iterator[StoredArtifact]:
        """Every stored object in key order (meta None when missing or unreadable). Only ``<2 hex>/<64 hex>`` regular
        files whose name starts with their directory count; temp files, metas without bytes, ``uploads/`` and any
        other entry are skipped."""
        try:
            prefixes = sorted(os.listdir(self.root))
        except FileNotFoundError:
            return
        except OSError as exc:
            raise _unavailable("artifact store is not readable", exc) from exc
        for prefix in prefixes:
            directory = self.root / prefix
            if not _FANOUT.fullmatch(prefix) or os.path.islink(directory) or not os.path.isdir(directory):
                continue
            try:
                names = sorted(os.listdir(directory))
            except OSError as exc:
                raise _unavailable("artifact store is not readable", exc) from exc
            for name in names:
                if not _SHA256.fullmatch(name) or name[:2] != prefix:
                    continue
                size = self._object_size(name)
                if size is not None:
                    yield StoredArtifact(name, size, directory / name, self._load_meta(name, strict=False))

    def sweep_tmp(self, max_age_s: float = TMP_MAX_AGE_S, *, now: float | None = None) -> list[str]:
        """Remove ``.tmp-*`` files older than ``max_age_s`` (left by crashed or killed writers; a live writer keeps
        its temp file's mtime fresh). Returns the removed entries as ``<2 hex>/<name>``.

        Age of a temp file with more than one link (the runner stages a hard link of its run-directory file, which
        keeps the SOURCE's old mtime): ``max(st_mtime, st_ctime)`` - ``link()`` sets the inode's ctime, so a temp
        link made a moment ago is never mistaken for a stale one. A single-link temp file (written here) is aged by
        its mtime."""
        cutoff = (time.time() if now is None else now) - max_age_s
        removed: list[str] = []
        try:
            prefixes = sorted(os.listdir(self.root))
        except FileNotFoundError:
            return removed
        except OSError as exc:
            raise _unavailable("artifact store is not readable", exc) from exc
        for prefix in prefixes:
            directory = self.root / prefix
            if not _FANOUT.fullmatch(prefix) or os.path.islink(directory) or not os.path.isdir(directory):
                continue
            try:
                names = sorted(os.listdir(directory))
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise _unavailable("artifact store is not readable", exc) from exc
            for name in names:
                if not name.startswith(TMP_PREFIX):
                    continue
                try:
                    st = os.lstat(directory / name)
                    touched = max(st.st_mtime, st.st_ctime) if st.st_nlink > 1 else st.st_mtime
                    if not stat.S_ISREG(st.st_mode) or touched > cutoff:
                        continue
                    os.unlink(directory / name)
                except FileNotFoundError:
                    continue
                removed.append(f"{prefix}/{name}")
        if removed:
            log.info("artifact store: removed %d stale temp file(s)", len(removed))
        return removed

    # ---------------------------------------------------------------------------------------------- writes
    def begin_put(self, sha256_expected: str, meta: Mapping[str, Any] | None = None, *,
                  declared_bytes: int | None = None) -> PendingPut:
        """Start a put of ``sha256_expected`` (see :class:`PendingPut`). Validates the key and the meta, refuses a
        declared size above the cap or above the free space, and returns a no-op put (``reused``) when the object is
        stored already with the declared size (or no size was declared), writing its meta if it has none."""
        sha = check_sha256(sha256_expected)
        clean = clean_meta(meta)
        declared = _declared_size(declared_bytes, clean.get("bytes"))
        if declared is not None and declared > self.max_bytes:
            raise StoreError("PAYLOAD_TOO_LARGE", f"declared size {declared} exceeds the store limit of {self.max_bytes} bytes")
        size = self._object_size(sha)
        if size is not None and (declared is None or declared == size):
            stored_meta = self._settle_meta(sha, _meta_doc(clean, size))
            return PendingPut(self, sha, clean, declared,
                              StoredArtifact(sha, size, self._object_path(sha), stored_meta, created=False))
        if declared is not None:
            try:
                free = shutil.disk_usage(nearest_existing_ancestor(str(self.root))).free
            except OSError:
                free = None
            if free is not None and declared > free:
                raise StoreError("STORE_UNAVAILABLE", f"not enough free space in the artifact store for {declared} bytes")
        return PendingPut(self, sha, clean, declared, None)

    def put_stream(self, sha256_expected: str, stream: Any, meta: Mapping[str, Any] | None = None, *,
                   declared_bytes: int | None = None) -> StoredArtifact:
        """Store the bytes of ``stream`` (binary file-like, bytes-like or sync iterable of chunks) under
        ``sha256_expected`` after recomputing their SHA-256 while streaming (module docstring has the rules).

        Returns the stored object (``created`` False for a no-op re-put, which does not read the stream). Raises
        :class:`StoreError`: SHA256_INVALID, META_INVALID, PAYLOAD_TOO_LARGE, LOAD_FAILED (digest or declared size
        mismatch; nothing is stored), STORE_UNAVAILABLE; ``TypeError`` for an unsupported stream or chunk type.
        """
        with self.begin_put(sha256_expected, meta, declared_bytes=declared_bytes) as pending:
            if pending.reused is None:
                for chunk in _iter_chunks(stream, self.chunk_bytes):
                    pending.write(chunk)
            return pending.commit()

    def _publish(self, tmp: str, sha: str, size: int) -> bool:
        """Give the verified temp file its final name; True when this call created the object."""
        final = self._object_path(sha)
        try:
            created = self._link_or_replace(tmp, final, size)
        finally:
            _unlink_quiet(tmp)
        _fsync_dir(final.parent)
        return created

    def _link_or_replace(self, tmp: str, final: Path, size: int) -> bool:
        try:
            os.link(tmp, final)
            return True
        except FileExistsError:
            pass
        except OSError as exc:
            if exc.errno not in _NO_HARDLINK:
                raise
            if self._object_size(final.name) == size:  # no hard links here: keep the object a writer already stored
                return False
            os.replace(tmp, final)
            return True
        try:
            st = os.lstat(final)
        except FileNotFoundError:  # removed between link() and lstat(): store ours
            os.replace(tmp, final)
            return True
        if stat.S_ISREG(st.st_mode) and st.st_size == size:  # same digest stored by an earlier or concurrent put
            return False
        log.warning("artifact store: replacing an inconsistent entry for %s (%s)", final.name,
                    "size differs" if stat.S_ISREG(st.st_mode) else "not a regular file")
        os.replace(tmp, final)
        return True

    def _settle_meta(self, sha: str, doc: dict[str, Any]) -> dict[str, Any] | None:
        """Write ``doc`` as the meta unless a readable meta exists (no clobber: the first meta wins); returns the
        stored meta. An unreadable meta is replaced."""
        path = self._meta_path(sha)
        present = os.path.lexists(path)
        if present:
            current = self._load_meta(sha, strict=False)
            if current is not None:
                return current
        data = (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        tmp: str | None = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=f"{TMP_PREFIX}{sha}.json.", suffix=".part", dir=path.parent)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fchmod(fh.fileno(), self.file_mode)
                os.fsync(fh.fileno())
            if present:
                os.replace(tmp, path)
            else:
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    pass  # a concurrent put wrote the meta first: keep it
                except OSError as exc:
                    if exc.errno not in _NO_HARDLINK:
                        raise
                    if not os.path.lexists(path):
                        os.replace(tmp, path)
            _unlink_quiet(tmp)
            tmp = None
            _fsync_dir(path.parent)
        except OSError as exc:
            raise _unavailable("artifact store: meta cannot be written", exc) from exc
        finally:
            _unlink_quiet(tmp)
        return self._load_meta(sha, strict=False)

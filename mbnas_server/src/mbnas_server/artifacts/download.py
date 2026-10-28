"""Framework-agnostic download of a stored artifact.

``serve_artifact(store, record) -> (headers, body)``: ``record`` is the artifact record core returns for
``(run_id, artifact_id)`` (``artifact_id`` or ``id``, ``sha256``, ``bytes``, ``mime_type``, ``run_id``). The gateway route
authorizes the request first (bearer, or a signed query via :func:`mbnas_server.artifacts.signing.require_valid_signature`),
looks the record up in core with the run scope, runs this helper in a worker thread (blocking file I/O) and answers
``200`` with ``headers`` and the ``body`` iterator (e.g. Starlette ``StreamingResponse(body, headers=headers)``). The
helper never takes a path: the store is addressed by the record's digest only.

Checks, in order:

1. the record has a digest (else 404 ``ARTIFACT_NOT_READY``: nothing transferred yet) that is a valid key (else 409
   ``LOAD_FAILED``: the record itself is damaged);
2. the store holds the object as a regular file (else 404 ``ARTIFACT_NOT_READY``; a symlink, FIFO or damaged layout is
   409 ``LOAD_FAILED``) whose size equals ``record.bytes`` when given (else 409 ``LOAD_FAILED``);
3. ``verify_first`` (default True): an object of at most ``verify_first_max_bytes`` (default 64 MiB) is hashed
   completely before the headers are returned, so a digest mismatch is a clean 409 ``LOAD_FAILED`` with an Error body.
   Objects that fit in one chunk are always verified first (holding back their only chunk would cost the same);
4. every object is re-hashed while it streams. The final chunk is held back until the digest is known; on a mismatch
   (or when the file shrinks or grows while it is read) the iterator raises ``StoreError("LOAD_FAILED")`` instead of
   yielding it.

**Limitation (trailing mismatch).** For an object that is not verified first, the status line and the headers
(``200``, ``Content-Length``, ``X-Artifact-Sha256``) are already on the wire when a mismatch is found, so the client
cannot receive a 409. It receives a body that ends before ``Content-Length`` (the server aborts the connection; HTTP
clients report an incomplete body, e.g. ``httpx.RemoteProtocolError``), at most ``size - len(last chunk)`` bytes, and
never the complete tampered file. Clients that save large artifacts must check the received length and compare the
SHA-256 of the saved bytes with ``X-Artifact-Sha256``; ``X-Artifact-Verification`` says which mode was used
(``pre-send`` | ``streaming``). Range requests are not supported (``Accept-Ranges: none``): verification needs the
whole object.

Response hardening: ``Cache-Control: private, no-store``, ``X-Content-Type-Options: nosniff`` (as the original download
route) and ``Content-Security-Policy: default-src 'none'; sandbox`` so that runner-provided bytes opened as a document
cannot run script on the gateway origin. ``Content-Type`` is the recorded ``mime_type`` when it is a plain media type,
else ``application/octet-stream`` (no header injection through the record).
"""
from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterator, Mapping
from typing import Any, BinaryIO

from mbnas_server.artifacts.store import MIME_RE, ArtifactStore, StoreError, is_sha256

log = logging.getLogger(__name__)

DEFAULT_VERIFY_FIRST_MAX_BYTES = 64 << 20
FALLBACK_MIME = "application/octet-stream"
BASE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Accept-Ranges": "none",
}


def content_type(mime: object) -> str:
    """The recorded media type when it is safe as a header value, else ``application/octet-stream``."""
    return mime if isinstance(mime, str) and MIME_RE.fullmatch(mime) else FALLBACK_MIME


class ArtifactBody(Iterator[bytes]):
    """Iterator over the bytes of one stored object that re-hashes them and holds back the final chunk until the
    digest matches (see the module docstring). Owns the open file: closed at the end, on error and by :meth:`close`
    (e.g. when the client disconnects)."""

    def __init__(self, fh: BinaryIO, sha256: str, size: int, chunk_bytes: int, label: str) -> None:
        self.sha256 = sha256
        self.size = size
        self._fh: BinaryIO | None = fh
        self._chunk = chunk_bytes
        self._label = label
        self._remaining = size
        self._held: bytes | None = None
        self._hasher = hashlib.sha256()

    @property
    def closed(self) -> bool:
        return self._fh is None

    def close(self) -> None:
        fh, self._fh = self._fh, None
        if fh is not None:
            fh.close()

    def __enter__(self) -> "ArtifactBody":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # an abandoned response must not keep the descriptor open
        try:
            self.close()
        except Exception:
            pass

    def __iter__(self) -> "ArtifactBody":
        return self

    def _mismatch(self, reason: str) -> StoreError:
        self.close()
        log.warning("artifact download: %s (sha256 %s): %s", self._label, self.sha256, reason)
        return StoreError("LOAD_FAILED", f"{self._label}: {reason}")

    def __next__(self) -> bytes:
        fh = self._fh
        if fh is None:
            raise StopIteration
        try:
            while self._remaining > 0:
                block = fh.read(min(self._chunk, self._remaining))
                if not block:
                    raise self._mismatch("the stored object is shorter than its recorded size")
                self._remaining -= len(block)
                self._hasher.update(block)
                held, self._held = self._held, block
                if held is not None:
                    return held
            # every byte has been read: release the held-back chunk only when nothing changed
            if fh.read(1):
                raise self._mismatch("the stored object grew while it was served")
            if self._hasher.hexdigest() != self.sha256:
                raise self._mismatch("the stored bytes no longer match the recorded sha256")
        except OSError as exc:
            self.close()
            raise StoreError("LOAD_FAILED", f"{self._label}: the stored object cannot be read ({type(exc).__name__})") from exc
        held, self._held = self._held, None
        self.close()
        if held:
            return held
        raise StopIteration


def _hash_to_end(fh: BinaryIO, chunk_bytes: int) -> str:
    hasher = hashlib.sha256()
    for block in iter(lambda: fh.read(chunk_bytes), b""):
        hasher.update(block)
    return hasher.hexdigest()


def serve_artifact(store: ArtifactStore, record: Mapping[str, Any], *, verify_first: bool = True,
                   verify_first_max_bytes: int = DEFAULT_VERIFY_FIRST_MAX_BYTES,
                   chunk_bytes: int | None = None) -> tuple[dict[str, str], ArtifactBody]:
    """``(headers, body)`` for one stored artifact; raises :class:`StoreError` (ARTIFACT_NOT_READY 404, LOAD_FAILED
    409) before any header exists for every check that can run before the bytes are sent. See the module docstring."""
    if not isinstance(record, Mapping):
        raise TypeError("record must be a mapping")
    artifact_id = record.get("artifact_id") if record.get("artifact_id") is not None else record.get("id")
    label = f"artifact {artifact_id}" if isinstance(artifact_id, str) and artifact_id else "artifact"
    if isinstance(record.get("run_id"), str) and record.get("run_id"):
        label += f" of run {record['run_id']}"
    sha = record.get("sha256")
    if sha is None or sha == "":
        raise StoreError("ARTIFACT_NOT_READY", f"{label} has no stored bytes yet")
    if not is_sha256(sha):
        raise StoreError("LOAD_FAILED", f"{label}: the artifact record holds no valid sha256")
    expected = record.get("bytes")
    if expected is not None and (not isinstance(expected, int) or isinstance(expected, bool) or expected < 0):
        raise StoreError("LOAD_FAILED", f"{label}: the artifact record holds no valid size")
    chunk = int(chunk_bytes or store.chunk_bytes)
    if chunk <= 0:
        raise ValueError("chunk_bytes must be positive")
    fh = store.open_stream(sha)
    try:
        size = os.fstat(fh.fileno()).st_size
        if expected is not None and size != expected:
            log.warning("artifact download: %s (sha256 %s): stored size %d, recorded %d", label, sha, size, expected)
            raise StoreError("LOAD_FAILED", f"{label}: the stored object has {size} bytes, the record says {expected}")
        pre_send = size <= chunk or (verify_first and size <= verify_first_max_bytes)
        if pre_send:
            if _hash_to_end(fh, chunk) != sha:
                log.warning("artifact download: %s (sha256 %s): stored bytes no longer match", label, sha)
                raise StoreError("LOAD_FAILED", f"{label}: the stored bytes no longer match the recorded sha256")
            fh.seek(0)
    except OSError as exc:
        fh.close()
        raise StoreError("LOAD_FAILED", f"{label}: the stored object cannot be read ({type(exc).__name__})") from exc
    except BaseException:
        fh.close()
        raise
    headers = {"Content-Type": content_type(record.get("mime_type")), "Content-Length": str(size),
               "X-Artifact-Sha256": sha, "X-Artifact-Verification": "pre-send" if pre_send else "streaming",
               **BASE_HEADERS}
    return headers, ArtifactBody(fh, sha, size, chunk, label)

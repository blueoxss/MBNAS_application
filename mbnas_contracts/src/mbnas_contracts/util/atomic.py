"""Crash-safe file writes and file hashing.

Every writer creates a temporary file in the destination directory, fsyncs it, ``os.replace``s it over the target
and fsyncs the directory, so readers see either the old or the new complete file, never a torn one.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_stream(path: str | os.PathLike[str], write: Callable[[IO[bytes]], None], *,
                        mode: int = 0o644) -> Path:
    """Atomically write ``path`` by calling ``write(fileobj)`` on a temp file (e.g. ``lambda f: torch.save(s, f)``).

    The final file gets permission bits ``mode`` (default 0644; pass 0o600 for private files).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as fh:
            write(fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    _fsync_dir(target.parent)
    return target


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> Path:
    """Atomically replace ``path`` with ``data``."""
    return atomic_write_stream(path, lambda fh: fh.write(data))


def atomic_write_json(path: str | os.PathLike[str], obj: Any, *, indent: int | None = 1) -> Path:
    """Atomically write ``obj`` as sorted-key JSON (NaN/Infinity rejected)."""
    text = json.dumps(obj, indent=indent, sort_keys=True, allow_nan=False, ensure_ascii=False)
    return atomic_write_bytes(path, (text + "\n").encode("utf-8"))


def sha256_file(path: str | os.PathLike[str], chunk: int = 1 << 20) -> str:
    """Hex SHA-256 of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()

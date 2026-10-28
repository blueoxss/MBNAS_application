"""Server-side path validation for every user-supplied path (protocol console.paths, artifact roots).

``check_server_path`` never trusts the browser: it rejects relative/home/placeholder/NUL/``..`` inputs, resolves
symlinks with ``os.path.realpath`` and requires the *resolved* path to sit under one of the (also resolved)
allow roots. Both the requested string and the realpath are returned so callers can record them.

Codes (PLAN.md §4): PATH_NOT_ABSOLUTE, PATH_PLACEHOLDER, PATH_TRAVERSAL, PATH_OUTSIDE_ALLOWLIST, PATH_NOT_FOUND,
PATH_NOT_READABLE, OUTPUT_NOT_WRITABLE. A NUL byte is reported as PATH_PLACEHOLDER (not a real path value); a
file/dir kind mismatch is PATH_NOT_FOUND with an explicit message.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import Literal

PathKind = Literal["file", "dir"]
PLACEHOLDER_MARKERS: tuple[str, ...] = ("<", ">", "YOUR_")
PLACEHOLDER_PREFIXES: tuple[str, ...] = ("/ABSOLUTE",)


@dataclass(frozen=True)
class PathCheck:
    """Outcome of one path check; ``code``/``message`` are None when ``ok``."""

    requested: str
    realpath: str | None
    ok: bool
    code: str | None = None
    message: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _fail(requested: str, code: str, message: str, realpath: str | None = None) -> PathCheck:
    return PathCheck(requested=requested, realpath=realpath, ok=False, code=code, message=message)


def _syntax_error(requested: object) -> tuple[str, str] | None:
    if not isinstance(requested, str) or not requested:
        return "PATH_NOT_ABSOLUTE", "path must be a non-empty absolute string"
    if "\x00" in requested:
        return "PATH_PLACEHOLDER", "path contains a NUL byte"
    if requested.startswith("~"):
        return "PATH_NOT_ABSOLUTE", "home-relative paths ('~') are not accepted; give the absolute server path"
    if any(requested.startswith(p) for p in PLACEHOLDER_PREFIXES) or any(m in requested for m in PLACEHOLDER_MARKERS):
        return "PATH_PLACEHOLDER", "path still contains a placeholder (/ABSOLUTE, <...>, YOUR_...)"
    if not os.path.isabs(requested):
        return "PATH_NOT_ABSOLUTE", "path must be absolute on the server (start with '/')"
    if ".." in requested.split("/"):
        return "PATH_TRAVERSAL", "'..' path segments are not accepted"
    return None


def is_within(path: str, root: str) -> bool:
    """True when realpath ``path`` equals or is below realpath ``root``."""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def nearest_existing_ancestor(path: str) -> str:
    """Closest existing directory at or above ``path``."""
    cur = path
    while not os.path.exists(cur):
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return cur


def free_disk_gb(path: str) -> float:
    """Free space (GB, decimal) of the filesystem holding ``path`` or its nearest existing ancestor."""
    return shutil.disk_usage(nearest_existing_ancestor(path)).free / 1e9


def check_server_path(
    requested: str,
    *,
    kind: PathKind,
    allow_roots: list[str],
    must_exist: bool = True,
    writable: bool = False,
) -> PathCheck:
    """Validate one server path; see module docstring for the rule order and codes."""
    syntax = _syntax_error(requested)
    if syntax:
        return _fail(str(requested), *syntax)
    real = os.path.realpath(requested)
    roots = [os.path.realpath(r) for r in allow_roots]
    if not any(is_within(real, r) for r in roots):
        return _fail(requested, "PATH_OUTSIDE_ALLOWLIST",
                     f"resolved path {real} is outside the allowed roots {roots}", real)
    if os.path.exists(real):
        if kind == "file" and not os.path.isfile(real):
            return _fail(requested, "PATH_NOT_FOUND", f"expected a file but {real} is not a regular file", real)
        if kind == "dir" and not os.path.isdir(real):
            return _fail(requested, "PATH_NOT_FOUND", f"expected a directory but {real} is not a directory", real)
        mode = os.R_OK | (os.X_OK if kind == "dir" else 0)
        if not os.access(real, mode):
            return _fail(requested, "PATH_NOT_READABLE", f"{real} is not readable by the server process", real)
    elif must_exist:
        return _fail(requested, "PATH_NOT_FOUND", f"{real} does not exist on the server", real)
    if writable:
        probe = real if os.path.exists(real) else nearest_existing_ancestor(real)
        if not os.path.isdir(probe) or not os.access(probe, os.W_OK | os.X_OK):
            return _fail(requested, "OUTPUT_NOT_WRITABLE", f"{probe} is not a writable directory", real)
    return PathCheck(requested=requested, realpath=real, ok=True)

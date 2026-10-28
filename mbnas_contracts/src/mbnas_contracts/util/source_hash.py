"""Deterministic source-tree SHA-256 (``mbnas-source-tree/2``), used as ``artifact_ref``/``source_commit`` while
the workspace has no git commits.

A root is a directory or a single file. Digest = SHA-256 over the sorted lines ``<path>\\0<sha256 of content>\\n``,
where ``<path>`` is ``<label>/<relative posix path>`` for every regular file under a directory root and ``<label>``
for a file root. Symlinks inside a directory root, and a file root that is a symlink, contribute their link target
string instead of being followed. Excluded inside directory roots: directories ``node_modules``, ``.venv``,
``__pycache__``, ``.pytest_cache``, ``.git``, ``dist``, ``.vite``, ``test-results``, ``playwright-report``,
``*.egg-info`` and ``*.pyc`` files. A missing root contributes
``<label>\\0missing`` (it changes the digest) instead of failing.

There are no default roots: every caller names the trees it hashes, e.g. a host passes the directories of the
packages it has installed. :func:`package_roots` builds such ``(label, directory)`` pairs with the
import name as label, so the digest does not depend on where the packages are installed. Digests over different root
sets are not comparable.

``/1`` → ``/2``: ``/1`` hashed only server ``src scripts contracts config
tests`` and web ``src``, so a change to a lock file, a build config or the web tests left ``artifact_ref`` unchanged.
``/1`` and ``/2`` digests are not comparable.

``roots`` is a required argument of :func:`compute_source_tree_hash`: ``(label, path)`` pairs, or paths with a
parallel ``labels`` list.
"""
from __future__ import annotations

import fnmatch
import hashlib
import importlib.util
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Union

ALGORITHM = "mbnas-source-tree/2"
EXCLUDED_DIRS = frozenset({"node_modules", ".venv", "__pycache__", ".pytest_cache", ".git", "dist", ".vite",
                           "test-results", "playwright-report"})
EXCLUDED_DIR_GLOBS = ("*.egg-info",)
EXCLUDED_FILE_GLOBS = ("*.pyc",)
#: a root: a ``(label, path)`` pair, or a bare path when ``labels`` are given separately
Root = Union[tuple[str, Union[str, os.PathLike]], str, os.PathLike]


def package_roots(packages: Iterable[str]) -> list[tuple[str, str]]:
    """``(import name, package directory)`` of each installed top-level package, for :func:`compute_source_tree_hash`.

    The directory comes from the import system (``importlib.util.find_spec``; the package is not executed), i.e. the
    tree the interpreter would import (an editable install resolves to its source directory). A package that is not
    installed raises ``ModuleNotFoundError`` and a namespace package spread over several directories raises
    ``ValueError``: a host must not silently hash a smaller tree.
    """
    out: list[tuple[str, str]] = []
    for name in packages:
        spec = importlib.util.find_spec(name)
        locations = list(spec.submodule_search_locations or []) if spec is not None else []
        if not locations:
            raise ModuleNotFoundError(f"package {name!r} is not installed (no package directory)", name=name)
        if len(locations) > 1:
            raise ValueError(f"package {name!r} spans several directories: {locations}")
        out.append((name, locations[0]))
    return out


def _pairs(roots: Sequence[Root], labels: Sequence[str] | None) -> list[tuple[str, str]]:
    """Normalise the two call forms of :func:`compute_source_tree_hash` to ``(label, path string)`` pairs."""
    if roots is None:
        raise TypeError("compute_source_tree_hash() needs explicit roots (there are no default roots)")
    if labels is None:
        out = []
        for item in roots:
            if not (isinstance(item, (tuple, list)) and len(item) == 2):
                raise TypeError(f"root {item!r} has no label: pass (label, path) pairs or a parallel labels list")
            out.append((item[0], os.fspath(item[1])))
        return out
    if len(labels) != len(roots):
        raise ValueError(f"{len(roots)} roots but {len(labels)} labels")
    return [(label, os.fspath(path)) for label, path in zip(labels, roots)]


def _excluded_dir(name: str) -> bool:
    return name in EXCLUDED_DIRS or any(fnmatch.fnmatch(name, g) for g in EXCLUDED_DIR_GLOBS)


def _file_digest(path: str) -> str:
    if os.path.islink(path):
        return "symlink:" + hashlib.sha256(os.readlink(path).encode()).hexdigest()
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _entries(label: str, root: str) -> list[tuple[str, str]]:
    if not os.path.isdir(root):  # file root; a symlink to a file (or a dangling one) contributes its target string
        return [(label, _file_digest(root))]
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not _excluded_dir(d))
        for name in sorted(filenames):
            if any(fnmatch.fnmatch(name, g) for g in EXCLUDED_FILE_GLOBS):
                continue
            path = os.path.join(dirpath, name)
            out.append((f"{label}/{Path(os.path.relpath(path, root)).as_posix()}", _file_digest(path)))
    return out


def compute_source_tree_hash(roots: Sequence[Root], labels: Sequence[str] | None = None) -> dict[str, Any]:
    """Hash the given (label, directory or file) roots; returns
    {algorithm, sha256, files, roots[{label,path,present,files}]}.

    ``roots`` is required: ``(label, path)`` pairs, or paths together with a parallel ``labels`` list.
    """
    roots = _pairs(roots, labels)
    entries: list[tuple[str, str]] = []
    summary = []
    for label, path in roots:
        present = os.path.isdir(path) or os.path.isfile(path) or os.path.islink(path)
        found = _entries(label, path) if present else [(label, "missing")]
        entries.extend(found)
        summary.append({"label": label, "path": path, "present": present, "files": len(found) if present else 0})
    h = hashlib.sha256()
    for rel, digest in sorted(entries):
        h.update(f"{rel}\0{digest}\n".encode())
    return {"algorithm": ALGORITHM, "sha256": "sha256:" + h.hexdigest(),
            "files": sum(r["files"] for r in summary), "roots": summary}

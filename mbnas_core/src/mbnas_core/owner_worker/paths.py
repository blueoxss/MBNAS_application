"""Where the owner worker writes its own files on the core host (DD-A60 (2)).

``run_dir(run)`` is ``<console.paths.output_root>/<run id>`` of the run's protocol. In a split deployment that output
root is a customer-host path: the gateway checked it on its host (DD-A46) and the core host may not be able to create
it at all (T5: ``PermissionError`` on ``mkdir``, the worker process died and the run stayed QUEUED). The owner worker
therefore keeps its own files on the core host:

* :func:`core_run_dir` = ``<MBNAS_CORE_OUTPUT_ROOT>/<run id>`` (default ``MBNAS_HOME/core/runs/<run>``) holds the
  control files (``control/<job>.stop``) always, and the per-attempt stderr logs and the PROPOSE_GENERATION
  ``architectures/`` directory whenever the run's own directory is not usable on this host;
* :func:`usable_here` decides that with the shared server path check (inside ``output_roots``, creatable/writable).
  In the owner all-in-one setup the run directory is usable and nothing moves.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mbnas_contracts.util.paths import check_server_path


def core_run_dir(settings: Any, run_id: str) -> Path:
    """``<settings.output_root>/<run id>``: the core-local directory of one run's owner-worker files."""
    rid = str(run_id)
    if not rid or rid in (".", "..") or "/" in rid or "\x00" in rid:
        raise ValueError(f"unsafe run id {rid!r}")
    return Path(str(settings.output_root)) / rid


def usable_here(path: Any, settings: Any) -> bool:
    """True when ``path`` is inside this host's output allowlist and can be created/written here
    (``check_server_path(kind="dir", must_exist=False, writable=True)``)."""
    return check_server_path(str(path), kind="dir", allow_roots=[str(r) for r in settings.output_roots],
                             must_exist=False, writable=True).ok

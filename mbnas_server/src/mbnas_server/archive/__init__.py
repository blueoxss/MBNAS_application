"""Read-only access to the ETRI_NAS ``yolo_ga_full`` run artifacts (contract 1.3.0, ``/api/v1/archive/...``).

Nothing in this package writes to the archive roots: files are opened with ``open(..., "rb"/"r")`` only, every path is
built from a validated run id plus a fixed relative name, and every resolved path must stay inside the run's real
directory. See ``docs/ARCHIVE_API.md`` for endpoints, formulas and limits.
"""
from __future__ import annotations

from mbnas_server.archive.scanner import ArchiveError, ArchiveStore

__all__ = ["ArchiveError", "ArchiveStore"]

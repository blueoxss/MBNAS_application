"""Fixtures of the structure-check sandbox tests (WP-3 contract 1.4; REMOTE_PROTOCOL_DESIGN 10.2; T3-35). CPU only."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SHARED = str(Path(__file__).resolve().parents[2] / "_shared")
if _SHARED not in sys.path:
    sys.path.append(_SHARED)


def _rss_kib() -> int:
    """Current resident set size of THIS (API) process in KiB (``/proc/self/status`` VmRSS)."""
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    raise AssertionError("VmRSS not found")


@pytest.fixture
def make_sandbox():
    from mbnas_core.structure import sandbox as sb

    def build(**kw):
        lim = dict(cpu_s=120, as_bytes=16 << 30, nofile=1024, fsize_bytes=64 << 20, wall_s=180.0, queue_s=60.0,
                   max_output_bytes=1 << 20, unshare=True)
        lim.update(kw)
        return sb.StructureSandbox(sb.Limits(**lim))

    return build


@pytest.fixture
def rss_kib():
    return _rss_kib

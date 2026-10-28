"""Fixtures of the WP-4C projection suite (WP-4 contract section 3). All SYNTHETIC; see ``projection_world.py``.

* ``world`` (session): the canary-seeded core + gateway world with every public / owner response captured once.
* ``contract150``: the 1.5.0 contract document (live ``openapi.json`` after the WP-4B promotion, else the WP-4A draft).

The world is built once per session (a run drained by the in-process owner worker, then SQL seeding and capture: about
20-40 s); tests only read the captured responses, so a failing assertion names the route and the leaking seed path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = str(Path(__file__).resolve().parent)
_SHARED = str(Path(__file__).resolve().parents[2] / "_shared")
for _p in (_SHARED, _HERE):
    if _p not in sys.path:  # appended, never inserted at 0
        sys.path.append(_p)

import projection_world as pw  # noqa: E402


@pytest.fixture(scope="session")
def world(tmp_path_factory) -> pw.World:
    return pw.build_world(tmp_path_factory.mktemp("wp4c_world"))


@pytest.fixture(scope="session")
def contract150() -> dict:
    return pw.contract_150()[0]

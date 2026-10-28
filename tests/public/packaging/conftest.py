"""Fixtures of the packager tests (WP-7D); the helpers live in ``pkgtest_support.py`` (this directory is appended to
``sys.path``, never inserted at 0)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)

from pkgtest_support import REPO, make_tree, run_build  # noqa: E402


def _need_node() -> None:
    # a source tree with the packager inputs (an extracted bundle has no build/allowlists: build/ never ships)
    if not list((REPO / "build" / "allowlists").glob("*.txt")):
        pytest.skip("not a source tree: build/allowlists/*.txt absent (packaging tests run where package.sh runs)")
    # a staged mbnas_web/dist must pass the WP-6A bundle gate, which runs on node
    if shutil.which("node") is None:
        pytest.skip("node not on PATH: the web bundle gate of the packager cannot run")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    """The synthetic tree packaged once per profile (public, owner, registration)."""
    _need_node()
    root = make_tree(tmp_path_factory.mktemp("wp7d_built"))
    out = {"root": root}
    for profile in ("public", "owner", "registration"):
        proc = run_build(root, "--profile", profile)
        out[profile] = proc
        assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    return out


@pytest.fixture
def tree(tmp_path) -> Path:
    _need_node()
    return make_tree(tmp_path)

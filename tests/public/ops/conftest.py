"""Fixtures of the ops-script tests (WP-7A); helpers live in ``opstest_support.py`` (this directory is appended to
``sys.path``, never inserted at 0)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)

from opstest_support import kill_own, make_root  # noqa: E402


@pytest.fixture
def ops_root(tmp_path: Path):
    """Throw-away ROOT with the scripts, a fake venv with stub role programs and public env files."""
    root = make_root(tmp_path)
    yield root
    kill_own(root)


@pytest.fixture
def ext_cwd(tmp_path: Path) -> Path:
    """A working directory outside ROOT: every script is called from here."""
    path = tmp_path / "elsewhere"
    path.mkdir()
    return path

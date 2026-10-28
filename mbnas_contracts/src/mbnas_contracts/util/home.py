"""Common runtime root of every MBNAS host: :func:`mbnas_home`.

``mbnas_home()`` = ``$MBNAS_HOME`` when set and non-empty, otherwise ``<current working directory>/runtime``, made
absolute (``~`` expanded, ``..`` normalised, symlinks not resolved). Every default path of the three settings classes
(core, gateway, runner) lives below it (WP-1 contract §5, DD-A13); operational paths outside it are set explicitly.
The function only computes the path: it creates nothing and reads no other variable.

Source: new in WP-1 (contract §5, DD-A13). It replaces the checkout-relative ``SERVER_ROOT`` and the fixed host
defaults of the monolithic ``mbnas_server.config``.
"""
from __future__ import annotations

import os
from pathlib import Path

MBNAS_HOME_ENV = "MBNAS_HOME"
DEFAULT_HOME_DIRNAME = "runtime"


def mbnas_home() -> Path:
    """Absolute runtime root: ``$MBNAS_HOME`` or ``<cwd>/runtime`` (see the module docstring)."""
    raw = os.environ.get(MBNAS_HOME_ENV)
    home = Path(raw).expanduser() if raw else Path.cwd() / DEFAULT_HOME_DIRNAME
    return Path(os.path.abspath(home))

"""pytest plugin (scratch, WP-4B self-check): record every member the public projection drops, by component path.

Usage: PYTHONPATH=<scratch> pytest -p drop_audit_plugin ...   -> writes $DROP_AUDIT_OUT (json) at session end.
It wraps mbnas_core.console.projection.project_public / project_member and diffs input vs output trees.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict

_COUNTS: dict[str, Counter] = defaultdict(Counter)
_EXAMPLES: dict[str, dict[str, str]] = defaultdict(dict)


def _norm(path: str) -> str:
    return path


def _diff(before, after, path, comp):
    if isinstance(before, dict) and isinstance(after, dict):
        for k, v in before.items():
            p = f"{path}.{k}" if path else k
            if k not in after:
                _COUNTS[comp][p] += 1
                if p not in _EXAMPLES[comp]:
                    _EXAMPLES[comp][p] = json.dumps(v, default=str)[:160]
            else:
                _diff(v, after[k], p, comp)
    elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        for b, a in zip(before, after):
            _diff(b, a, path + "[]", comp)


def pytest_configure(config):
    import mbnas_core.console.projection as proj

    orig = proj.project_public

    def wrapped(component, payload, principal_scope=None, *, run_id=None):
        out = orig(component, payload, principal_scope, run_id=run_id)
        try:
            _diff(payload, out, "", component)
        except Exception:  # noqa: BLE001
            pass
        return out

    proj.project_public = wrapped
    import importlib
    for mod in ("mbnas_core.console.deps", "mbnas_core.console.routes_stream", "mbnas_core.console.routes_training",
                "mbnas_core.export.bundle"):
        try:
            m = importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            continue
        if hasattr(m, "project_public"):
            m.project_public = wrapped


def pytest_sessionfinish(session, exitstatus):
    out = os.environ.get("DROP_AUDIT_OUT")
    if not out:
        return
    data = {comp: {p: {"n": n, "example": _EXAMPLES[comp].get(p)} for p, n in sorted(c.items())}
            for comp, c in sorted(_COUNTS.items())}
    with open(out, "w") as fh:
        json.dump(data, fh, indent=1)

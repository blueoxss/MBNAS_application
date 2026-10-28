#!/usr/bin/env python3
"""T2 new-side runner (owner only; WORK_PACKAGES/T2_REFERENCE_PLAN.md section 1): runs one scenario with the split code.

Started by ``scripts/owner/t2_compare.py`` as ``<venv python> -B -s new_runner.py --snapshot <snapshot root> ...`` in a
subprocess of the same venv as the reference side. It imports ``mbnas_core`` + ``mbnas_model_runtime`` +
``mbnas_contracts`` (the installed editable packages of this repository; the ETRI mixed pool is the vendored
``mbnas_core.search.mixed_pool`` + ``mbnas_model_runtime.operators``, DD-A12/DD-A5) and never imports the snapshot
(``--snapshot`` is only used to report isolation: any module loaded from it, or any ``yolo_ga_runtime`` module, is a
violation). Harness measures are the same as on the reference side (``t2_core.install_determinism``). Output: canonical
JSON (``--out``); exit 0 ok, 2 scenario error, 3 isolation violation.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def _load_core():
    spec = importlib.util.spec_from_file_location("t2_core", HERE / "t2_core.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["t2_core"] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    snap_arg = None
    rest = []
    it = iter(argv)
    for a in it:
        if a == "--snapshot":
            snap_arg = next(it)
        else:
            rest.append(a)
    if not snap_arg:
        raise SystemExit("--snapshot <build/source_snapshot> is required (isolation report only)")
    snapshot = Path(snap_arg).resolve()
    core = _load_core()
    harness = core.install_determinism()
    sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != HERE]
    importlib.invalidate_caches()
    import mbnas_core  # noqa: PLC0415

    if not Path(mbnas_core.__file__).resolve().is_relative_to(REPO):
        raise SystemExit(f"mbnas_core resolved to {mbnas_core.__file__}, not this repository {REPO}")
    return core.runner_main("new", rest, repo_root=REPO, snapshot_root=snapshot, harness=harness,
                            path_note=sys.path[:2])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

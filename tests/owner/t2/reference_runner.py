#!/usr/bin/env python3
"""T2 reference runner (owner only; WORK_PACKAGES/T2_REFERENCE_PLAN.md section 1): runs one scenario with the ORIGINAL code.

Started by ``scripts/owner/t2_compare.py`` as ``<venv python> -B -s reference_runner.py --snapshot <snapshot root> ...``
in a subprocess of the same venv (third-party versions identical to the new side), with ``PYTHONDONTWRITEBYTECODE=1``,
``CUDA_VISIBLE_DEVICES=""`` and HOME/caches inside the work directory. Before anything else imports, this process:

* loads ``t2_core.py`` by file path and installs the harness measures (deterministic ``uuid4``, blocked ``pynvml``);
* sets ``sys.dont_write_bytecode = True`` and ``sys.path = [<snapshot>/application/MBNAS_server/src, <snapshot>,
  stdlib + site-packages]`` with the five editable ``mbnas_*`` source roots of the target repository REMOVED, so
  ``import mbnas_server`` is the original monolith and the new packages are not importable;
* sets ``MBNAS_ETRI_NAS_ROOT=<snapshot>`` (the ETRI mixed pool = the snapshot's ``yolo_ga_runtime``, read only).

The snapshot is never written (the orchestrator hashes the whole tree before and after every run and fails loudly on
any change). Output: canonical JSON (``--out``); exit 0 ok, 2 scenario error, 3 isolation violation.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
NEW_SRC = {(REPO / p / "src").resolve() for p in
           ("mbnas_contracts", "mbnas_core", "mbnas_model_runtime", "mbnas_runner", "mbnas_server")}


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
        raise SystemExit("--snapshot <build/source_snapshot> is required")
    snapshot = Path(snap_arg).resolve()
    core = _load_core()
    harness = core.install_determinism()
    keep = [p for p in sys.path if p and Path(p).resolve() not in NEW_SRC and Path(p).resolve() != HERE]
    sys.path[:] = [str(snapshot / "application" / "MBNAS_server" / "src"), str(snapshot)] + keep
    importlib.invalidate_caches()
    os.environ["MBNAS_ETRI_NAS_ROOT"] = str(snapshot)
    import mbnas_server  # noqa: PLC0415 - the original monolith of the snapshot

    if not Path(mbnas_server.__file__).resolve().is_relative_to(snapshot):
        raise SystemExit(f"mbnas_server resolved to {mbnas_server.__file__}, not the snapshot {snapshot}")
    for name in ("mbnas_core", "mbnas_contracts", "mbnas_model_runtime", "mbnas_runner"):
        if importlib.util.find_spec(name) is not None:
            raise SystemExit(f"{name} is importable in the reference process; sys.path isolation failed")
    return core.runner_main("ref", rest, repo_root=REPO, snapshot_root=snapshot, harness=harness,
                            path_note=sys.path[:2])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

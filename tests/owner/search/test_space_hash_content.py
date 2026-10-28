"""S1-F1 / S1-F4 under ``mbnas-mixed-space/3`` (WP-1 contract §6.5, DD-A12): space_hash / prior_hash depend on the
CONTENT of the vendored mixed-pool modules executed for seed variants and parser registration, never on where they are
installed, and each of the three vendored files is content-hashed into ``files_sha256``.

Each variant is described in its own CPU subprocess (the operators register process-wide). A variant is a copy of the
installed ``mbnas_core`` and ``mbnas_model_runtime`` package directories under a tmp dir, put first on ``PYTHONPATH``;
a tampered variant appends a comment to one vendored file of the copy (ETRI_RUNTIME_SCOPE §6.4).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# WP-1 (DD-A12, contract §6.4/§6.5): the three vendored mixed-pool files hashed by mbnas-mixed-space/3
VENDORED = {"etri_mixed_blocks.py": ("mbnas_model_runtime", "operators/etri_mixed_blocks.py"),
            "seed_variants.py": ("mbnas_core", "search/mixed_pool/seed_variants.py"),
            "runtime_constants.py": ("mbnas_core", "search/mixed_pool/runtime_constants.py")}


def _package_dir(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    assert spec is not None and spec.submodule_search_locations, name
    return Path(list(spec.submodule_search_locations)[0])

_CODE = """
import json, sys
import mbnas_core, mbnas_model_runtime
from mbnas_core.search.space import SearchSpace, SPACE_HASH_DEFINITION
s = SearchSpace.create(nc=1, imgsz=320, families=("yolo11n",))
d = s.describe()
print(json.dumps({"space_hash": d["space_hash"], "prior_hash": d["prior_hash"], "version": d["version"],
                  "definition": SPACE_HASH_DEFINITION, "etri": d["etri"],
                  "sources": [t["source"] for t in d["templates"]],
                  "imported_from": [mbnas_core.__file__, mbnas_model_runtime.__file__]}))
"""


def _variant(tmp: Path, name: str, *, tweak: str | None = None) -> Path:
    """Copies of the installed mbnas_core + mbnas_model_runtime package directories (put first on PYTHONPATH); with
    ``tweak`` one vendored file of the copy gets a trailing comment (a pure content change)."""
    root = tmp / name
    for pkg in ("mbnas_core", "mbnas_model_runtime"):
        shutil.copytree(_package_dir(pkg), root / pkg, ignore=shutil.ignore_patterns("__pycache__"))
    if tweak:
        pkg, rel = VENDORED[tweak]
        with open(root / pkg / rel, "a", encoding="utf-8") as fh:
            fh.write("\n# content change for the MBNAS space-hash regression test\n")
    return root


def _describe(root: Path | None) -> dict:
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
    if root is not None:  # the copy shadows the editable install (.pth entries come after PYTHONPATH)
        env["PYTHONPATH"] = os.pathsep.join([str(root), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    proc = subprocess.run([sys.executable, "-c", _CODE], capture_output=True, text=True, timeout=600,
                          env=env)
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


# 2026-10-21 (D4): tests that genuinely build real ultralytics models / need the ETRI pool skip (not fail/error) on a
# host without the ml extras. Torch-free tests in this module keep running.
needs_ml = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None or importlib.util.find_spec("ultralytics") is None,
    reason="needs torch + ultralytics (ml extras)")


@needs_ml
@pytest.mark.slow
def test_same_content_elsewhere_same_hash_and_content_change_changes_it(tmp_path: Path) -> None:
    a = _describe(None)  # the installed packages
    b_root = _variant(tmp_path, "b")
    b = _describe(b_root)  # byte-identical copies at another location
    assert all(Path(f).resolve().is_relative_to(b_root.resolve()) for f in b["imported_from"])  # precondition
    # the install location is provenance only, like the ETRI git HEAD under /2 (contract §6.4: no root, no HEAD)
    assert a["etri"]["git_head"] is None and b["etri"]["git_head"] is None
    assert a["space_hash"] == b["space_hash"] and a["prior_hash"] == b["prior_hash"]
    for src in a["sources"]:
        assert "etri_git_head" not in src
    # S1-F4: every vendored mixed-pool module is content-hashed (the bytes of the installed files) ...
    files = a["etri"]["files_sha256"]
    assert sorted(k.rsplit("/", 1)[-1] for k in files) == sorted(VENDORED)
    for key, digest in files.items():
        pkg, rel = VENDORED[key.rsplit("/", 1)[-1]]
        assert digest.removeprefix("sha256:") == hashlib.sha256((_package_dir(pkg) / rel).read_bytes()).hexdigest()
    # ... so a content change in any one of them changes exactly its entry and both hashes
    for name in VENDORED:
        c = _describe(_variant(tmp_path, f"c_{name}", tweak=name))
        assert {k.rsplit("/", 1)[-1] for k in files if c["etri"]["files_sha256"][k] != files[k]} == {name}
        assert c["space_hash"] != a["space_hash"] and c["prior_hash"] != a["prior_hash"]
    assert a["version"] == "mbnas-mixed-space/3" and "content" in a["definition"]


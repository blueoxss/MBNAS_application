"""Evaluator probe (C04 EVAL_3): reuse the project's owner + runner_protocol fixtures without touching the project tree."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application")
if str(ROOT / "tests" / "_shared") not in sys.path:
    sys.path.append(str(ROOT / "tests" / "_shared"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


for _mod in (_load("c04e3_owner_conftest", ROOT / "tests/owner/conftest.py"),
             _load("c04e3_rp_conftest", ROOT / "tests/owner/runner_protocol/conftest.py")):
    for _k, _v in vars(_mod).items():
        if not _k.startswith("__"):
            globals()[_k] = _v

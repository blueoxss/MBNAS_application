"""C02 evaluator scratch plumbing: reuse tests/owner/conftest.py + tests/owner/runner_protocol/conftest.py fixtures
(read-only import of the project's test fixtures; nothing in the project tree is written)."""
import importlib.util
import sys
from pathlib import Path

T = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application/tests")
if str(T / "_shared") not in sys.path:
    sys.path.append(str(T / "_shared"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


for _m in (_load("c02_owner_root_conftest", T / "owner" / "conftest.py"),
           _load("c02_owner_rp_conftest", T / "owner" / "runner_protocol" / "conftest.py")):
    for _k, _v in vars(_m).items():
        if hasattr(_v, "_pytestfixturefunction") or getattr(_v, "_fixture_function_marker", None) is not None \
                or type(_v).__name__ == "FixtureFunctionDefinition" or _k.startswith("pytest_"):
            globals()[_k] = _v

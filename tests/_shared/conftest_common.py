"""Plumbing shared by the two root conftests, ``tests/owner/conftest.py`` and ``tests/public/conftest.py``.

Split out of the old single ``tests/conftest.py`` (WP-1 contract §9, DD-A22). Each root conftest puts ``tests/_shared``
on ``sys.path`` (appended, never inserted at 0) and does ``from conftest_common import *``; the names re-exported are
listed in ``__all__`` (fixtures and hooks, including the GPU guard hooks of ``gpu_guard``).

* Host paths of a test run are ``MBNAS_TEST_*`` variables (``clean_env`` keeps them). They are read, and their defaults
  set, at import time, before any test module is imported: ``MBNAS_TEST_ROOT`` (default
  ``$TARGET_ROOT/runtime/test_root``, created, no temp-dir fallback), ``MBNAS_TEST_RUNS_ROOT`` (``$MBNAS_TEST_ROOT/runs``),
  ``MBNAS_TEST_DATA_ROOT`` (``$MBNAS_TEST_ROOT/data``; the PILOT subset is ``pilot_visdrone_v1`` under it),
  ``MBNAS_TEST_WEIGHTS_DIR`` (``$MBNAS_TEST_ROOT/weights``) and ``PYTEST_GPU_LOCK``
  (``$MBNAS_TEST_ROOT/locks/gpu.lock``). ``MBNAS_TEST_ETRI_ROOT``, ``MBNAS_ETRI_NAS_ROOT`` and
  ``MBNAS_TEST_ASSUME_ULTRALYTICS`` no longer exist (the ETRI operators are vendored; ultralytics is installed).
* ``clean_env`` (autouse): removes every ``MBNAS_*`` variable except ``MBNAS_TEST_*``, so tests never read the
  operator's environment, and points ``MBNAS_HOME`` at a directory of this test below pytest's basetemp (created only
  when something writes there): no ``$MBNAS_HOME`` default of a test - the runner id file ``runner/runner_id``, gateway
  store, core DB - lands in ``<repo>/runtime`` or in an operator's installation. Code that runs outside a test function
  (collection, module-scoped fixtures) sees ``MBNAS_HOME=$MBNAS_TEST_ROOT/mbnas_home``, set at import time. A test about
  ``MBNAS_HOME`` itself sets or deletes it.
* ``host_data`` (marker): a test that needs real host data (the PILOT subset, probe/bench evidence, real archive runs,
  official weights, an ETRI reference checkout) is marked ``host_data`` and skipped unless ``MBNAS_TEST_HOST_DATA=1``.
  Tests that request ``pilot_required``, and tests carrying a ``skipif(..., host_data=True)`` data-presence mark, get the
  marker automatically (``pytest_collection_modifyitems`` below).
* ``make_dataset``: factory for tiny synthetic YOLO datasets (real PNG files; ``tests/_shared/datasets.py``).
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Callable

import pytest

SHARED_DIR = Path(__file__).resolve().parent
TESTS_DIR = SHARED_DIR.parent
REPO_ROOT = TESTS_DIR.parent  # TARGET_ROOT

# DD-A29: no __pycache__ in the package trees; child interpreters inherit this (children started with -I get -B)
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
TEST_ROOT = os.environ.get("MBNAS_TEST_ROOT") or str(REPO_ROOT / "runtime" / "test_root")
os.environ["MBNAS_TEST_ROOT"] = TEST_ROOT  # test modules that read MBNAS_TEST_ROOT themselves (imported later) agree
Path(TEST_ROOT).mkdir(parents=True, exist_ok=True)
_OPERATOR_WEIGHTS_DIR = os.environ.get("MBNAS_TEST_WEIGHTS_DIR")  # named by the operator (not one of our defaults)
for _key, _default, _dir in (("MBNAS_TEST_RUNS_ROOT", f"{TEST_ROOT}/runs", None),
                             ("MBNAS_TEST_DATA_ROOT", f"{TEST_ROOT}/data", None),
                             ("MBNAS_TEST_WEIGHTS_DIR", f"{TEST_ROOT}/weights", None),
                             ("PYTEST_GPU_LOCK", f"{TEST_ROOT}/locks/gpu.lock", f"{TEST_ROOT}/locks")):
    if not os.environ.get(_key):  # only our own defaults are created; an operator-named path is used as given
        os.environ[_key] = _default
        Path(_dir or _default).mkdir(parents=True, exist_ok=True)
# outside a test function (collection, module-scoped fixtures) every $MBNAS_HOME default stays below the test root;
# clean_env pins a per-test directory (never <repo>/runtime nor an operator's installation)
os.environ["MBNAS_HOME"] = os.path.join(TEST_ROOT, "mbnas_home")
TEST_RUNS_ROOT = os.environ["MBNAS_TEST_RUNS_ROOT"]
TEST_DATA_ROOT = os.environ["MBNAS_TEST_DATA_ROOT"]
TEST_WEIGHTS_DIR = os.environ["MBNAS_TEST_WEIGHTS_DIR"]
PILOT_ROOT = Path(TEST_DATA_ROOT) / "pilot_visdrone_v1"
HOST_DATA = os.environ.get("MBNAS_TEST_HOST_DATA") == "1"
# only when the operator named a weights dir via MBNAS_TEST_WEIGHTS_DIR: code that loads its settings from the
# environment sees it (core and runner settings prefixes, contract §5)
_HOST_ENV = ({k: _OPERATOR_WEIGHTS_DIR for k in ("MBNAS_CORE_WEIGHTS_DIR", "MBNAS_RUNNER_WEIGHTS_DIR")}
             if _OPERATOR_WEIGHTS_DIR else {})
# SYNTHETIC device (C05 D8): the uuid is made up; the real UUID of the original host GPU is a host fact
# (scripts/dev/canary_scan.py HOST_FACT#6) and must not appear in the public / registration bundles
A100 = {"id": "host-gpu-0", "uuid": "GPU-a1000000-0000-4000-8000-000000000000", "name": "NVIDIA A100 80GB PCIe",
        "aliases": ["GPU-a1000000-0000-4000-8000-000000000000", "NVIDIA A100 80GB PCIe", "cuda:0"],
        "precisions": ["FP32", "FP16"]}


# DD-A43(b): owner-only tools the public bundles do not ship (scripts/dev/*, scripts/runner/resume_equivalence.py and the
# in-process core + gateway harness tests/_shared/harness.py, which imports the private core). A public test that needs
# one of them asks for it through ``owner_tool`` / ``owner_harness``: present (owner bundle, source tree) -> the test runs
# unchanged; absent (public / registration bundle) -> the test, or at import time its whole module (or conftest
# directory), is skipped with OWNER_TOOL_SKIP. No assertion is removed or relaxed; only the tool's presence is checked.
OWNER_TOOL_SKIP = "owner-only tool not in this bundle"


def owner_tool(rel: str) -> Path:
    """``REPO_ROOT / rel`` when that owner-only tool file exists, else ``pytest.skip(OWNER_TOOL_SKIP)``."""
    path = REPO_ROOT / rel
    if not path.is_file():
        pytest.skip(OWNER_TOOL_SKIP, allow_module_level=True)
    return path


def owner_harness():
    """``tests/_shared/harness.py`` imported as ``harness`` (see ``owner_tool``)."""
    owner_tool("tests/_shared/harness.py")
    import harness  # noqa: PLC0415  (tests/_shared is on sys.path)

    return harness


def _load_alias(alias: str, filename: str):
    """Import a helper of this directory under a unique module name (``datasets`` must not shadow site-packages)."""
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, SHARED_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


_datasets = _load_alias("mbnas_test_datasets", "datasets.py")
if str(SHARED_DIR) not in sys.path:
    sys.path.append(str(SHARED_DIR))
from gpu_guard import pytest_runtest_setup, pytest_runtest_teardown, pytest_unconfigure  # noqa: E402  (PYTEST_GPU_LOCK set above)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
              tmp_path_factory: pytest.TempPathFactory) -> None:
    for key in list(os.environ):
        if key.upper().startswith("MBNAS_") and not key.upper().startswith("MBNAS_TEST_"):  # MBNAS_TEST_* = host paths of
            monkeypatch.delenv(key, raising=False)                                              # this test run
    for key, value in _HOST_ENV.items():  # only when the operator named host paths via MBNAS_TEST_*
        monkeypatch.setenv(key, value)
    # this test's MBNAS_HOME (module docstring): one directory per test id, not created here
    test_id = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:16]
    monkeypatch.setenv("MBNAS_HOME", str(tmp_path_factory.getbasetemp() / "mbnas_home" / test_id))


@pytest.fixture
def tmp_roots(tmp_path: Path) -> dict[str, Path]:
    roots = {"data": tmp_path / "data", "runs": tmp_path / "runs"}
    for p in roots.values():
        p.mkdir()
    return roots


@pytest.fixture
def make_dataset(tmp_roots: dict[str, Path]) -> Callable[..., dict[str, str]]:
    """Factory: make_dataset(name, counts={'train':4,'val':2,'test':2}, label=..., names=['drone']) -> paths dict."""

    def build(name: str = "ds", counts: dict[str, int] | None = None, label: str = "0 0.5 0.5 0.25 0.25\n",
              names: list[str] | None = None, seed0: int = 1) -> dict[str, str]:
        return _datasets.build_dataset(tmp_roots["data"], name, counts, label, names, seed0)

    return build


@pytest.fixture
def pilot_required() -> None:
    try:
        present = (PILOT_ROOT / "data.yaml").is_file()
    except OSError:  # e.g. PermissionError on an unreadable data root
        present = False
    if not present:
        pytest.skip(f"real PILOT_SMOKE subset missing at {PILOT_ROOT}")


# ultralytics replaces PIL.Image.open process-wide when it is first imported (ultralytics/utils/patches.py); its wrapper
# raises ModuleNotFoundError('pi_heif') instead of PIL's UnidentifiedImageError for unreadable files. Restore PIL's own
# function after every test so test order cannot change the behaviour other modules' tests observe.
@pytest.fixture(autouse=True)
def _restore_pil_image_open():
    yield
    patches = sys.modules.get("ultralytics.utils.patches")
    if patches is not None and hasattr(patches, "_image_open"):
        from PIL import Image

        Image.open = patches._image_open


_HOST_DATA_SEEN = pytest.StashKey[bool]()
_HOST_DATA_SKIP = ("host_data: needs real host data (PILOT subset, probe/bench evidence, real runs, weights or an "
                   "ETRI reference checkout); set MBNAS_TEST_HOST_DATA=1 and the MBNAS_TEST_* roots to run it")


@pytest.hookimpl(tryfirst=True)  # before the mark plugin applies -m, so the added marker can be selected on
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Contract §9 ``host_data``: mark data-dependent tests and skip them unless ``MBNAS_TEST_HOST_DATA=1``."""
    for item in items:
        if item.stash.get(_HOST_DATA_SEEN, False):  # both root conftests re-export this hook
            continue
        item.stash[_HOST_DATA_SEEN] = True
        if item.get_closest_marker("host_data") is None and (
                "pilot_required" in getattr(item, "fixturenames", ())
                or any(m.kwargs.get("host_data") for m in item.iter_markers("skipif"))):
            item.add_marker(pytest.mark.host_data)
        if item.get_closest_marker("host_data") is not None and not HOST_DATA:
            item.add_marker(pytest.mark.skip(reason=_HOST_DATA_SKIP))


__all__ = ["clean_env", "tmp_roots", "make_dataset", "pilot_required", "_restore_pil_image_open",
           "pytest_collection_modifyitems", "pytest_runtest_setup", "pytest_runtest_teardown", "pytest_unconfigure"]

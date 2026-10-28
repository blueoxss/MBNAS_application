"""The static parse_model copy and its installation gate (WP-1 contract §6.2/§6.11, DD-A12; C-cross-6).

* the installed ``ultralytics.nn.tasks.parse_model`` source hashes to ``EXPECTED_PARSE_MODEL_SHA256`` (8.4.173);
* the static copy's module sets are 37 base / 17 repeat names, with the 3 / 2 ETRI operators added;
* the copy is AST-identical to the ETRI AST patch applied to the installed source (``verify_static_copy``);
* registration with a different installed source fails with ``OperatorsUnavailable("ultralytics parse_model source
  mismatch ...")`` and leaves ``parse_model`` untouched (no silent fallback); registration is idempotent.
Registration runs in child interpreters (``-I -B``) so this process' ultralytics stays unpatched.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import sys

import pytest

pytest.importorskip("torch")
ultralytics = pytest.importorskip("ultralytics")

from mbnas_model_runtime.operators import parse_model_8_4_173 as pm  # noqa: E402


def _child(code: str) -> dict:
    proc = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=300,
                          env={"CUDA_VISIBLE_DEVICES": "", "PATH": "/usr/bin:/bin", "YOLO_OFFLINE": "1"})
    assert proc.returncode == 0, proc.stderr[-3000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_installed_parser_matches_the_pinned_source() -> None:
    from ultralytics.nn import tasks

    original = getattr(tasks.parse_model, "_nas_mixed_original", tasks.parse_model)
    assert ultralytics.__version__ == pm.ULTRALYTICS_VERSION == "8.4.173"
    assert hashlib.sha256(inspect.getsource(original).encode()).hexdigest() == pm.EXPECTED_PARSE_MODEL_SHA256
    assert pm.PARSE_MODEL_EXTENSION_MODE == "static_copy"


def test_static_module_sets_are_37_and_17() -> None:
    sets = pm.static_module_sets()
    assert len(sets["base_modules"]) == 37 and len(sets["repeat_modules"]) == 17
    assert len(set(sets["base_modules"])) == 37 and len(set(sets["repeat_modules"])) == 17
    assert set(pm.ADDED_BASE_MODULES) <= set(sets["base_modules"])
    assert set(pm.ADDED_REPEAT_MODULES) <= set(sets["repeat_modules"])
    assert pm.ADDED_BASE_MODULES == ("NASPatchMerge", "NASWindowTransformer", "NASGlobalTransformer")
    assert pm.ADDED_REPEAT_MODULES == ("NASWindowTransformer", "NASGlobalTransformer")


def test_static_copy_equals_the_etri_ast_patch_of_the_installed_parser() -> None:
    report = pm.verify_static_copy()
    assert report["sha256_match"] is True and report["equivalent"] is True, report["difference"]
    assert (report["base_modules"], report["repeat_modules"]) == (37, 17)
    assert sorted(report["added_base_modules"]) == sorted(pm.ADDED_BASE_MODULES)
    assert sorted(report["added_repeat_modules"]) == sorted(pm.ADDED_REPEAT_MODULES)


def test_registration_installs_the_copy_and_is_idempotent() -> None:
    out = _child(
        "import json; from ultralytics.nn import tasks; original = tasks.parse_model\n"
        "from mbnas_model_runtime.operators import ensure_registered, operator_files_sha256\n"
        "a = ensure_registered(); b = ensure_registered()\n"
        "print(json.dumps({'a': a, 'b_already': b['already_installed'], 'patched': tasks.parse_model is not original,\n"
        "  'kept_original': tasks.parse_model._nas_mixed_original is original,\n"
        "  'classes': [isinstance(getattr(tasks, n, None), type) for n in a['custom_modules']],\n"
        "  'files': operator_files_sha256()}, default=str))")
    caps = out["a"]
    assert caps["installed"] is True and caps["already_installed"] is False and out["b_already"] is True
    assert out["patched"] and out["kept_original"] and all(out["classes"])
    assert caps["parser_source_sha256"] == pm.EXPECTED_PARSE_MODEL_SHA256
    assert caps["replacement_source_sha256"] == out["files"]["mbnas_model_runtime/operators/parse_model_8_4_173.py"]
    assert caps["custom_modules"] == list(pm.ADDED_BASE_MODULES)
    assert caps["repeat_modules"] == list(pm.ADDED_REPEAT_MODULES)


def test_source_mismatch_is_refused_without_fallback() -> None:
    out = _child(
        "import json; from ultralytics.nn import tasks; original = tasks.parse_model\n"
        "from mbnas_model_runtime.operators import parse_model_8_4_173 as pm, OperatorsUnavailable, ensure_registered\n"
        "pm.EXPECTED_PARSE_MODEL_SHA256 = '0' * 64\n"
        "try:\n    ensure_registered(); err = None\n"
        "except OperatorsUnavailable as exc:\n    err = str(exc)\n"
        "print(json.dumps({'err': err, 'untouched': tasks.parse_model is original,\n"
        "  'flag': getattr(tasks.parse_model, '_nas_mixed_installed', False),\n"
        "  'classes': [hasattr(tasks, n) for n in ('NASPatchMerge', 'NASWindowTransformer', 'NASGlobalTransformer')]}))")
    assert out["err"] and out["err"].startswith("ultralytics parse_model source mismatch")
    assert out["untouched"] is True and out["flag"] is False and not any(out["classes"])

"""provenance: deterministic source-tree hash and environment lock / device inventory."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mbnas_model_runtime.provenance import env_lock
from mbnas_contracts.util.source_hash import ALGORITHM, compute_source_tree_hash, package_roots


def _tree(tmp_path: Path) -> list[tuple[str, str]]:
    src = tmp_path / "srv" / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "a.py").write_text("A = 1\n")
    (src / "pkg" / "b.txt").write_text("b")
    return [("MBNAS_server/src", str(src)), ("MBNAS_web/src", str(tmp_path / "web" / "src"))]


def test_source_hash_deterministic_and_content_sensitive(tmp_path) -> None:
    roots = _tree(tmp_path)
    a = compute_source_tree_hash(roots)
    assert a["algorithm"] == ALGORITHM and a["sha256"].startswith("sha256:") and a["files"] == 2
    assert a["roots"][1] == {"label": "MBNAS_web/src", "path": roots[1][1], "present": False, "files": 0}
    assert compute_source_tree_hash(roots) == a
    src = Path(roots[0][1])
    for junk in ("pkg/__pycache__/a.cpython-310.pyc", "node_modules/x.js", ".venv/bin/python", "pkg/c.pyc"):
        (src / junk).parent.mkdir(parents=True, exist_ok=True)
        (src / junk).write_text("ignored")
    assert compute_source_tree_hash(roots)["sha256"] == a["sha256"]
    (src / "pkg" / "a.py").write_text("A = 2\n")
    assert compute_source_tree_hash(roots)["sha256"] != a["sha256"]


def test_source_hash_rename_and_symlink(tmp_path) -> None:
    roots = _tree(tmp_path)
    base = compute_source_tree_hash(roots)["sha256"]
    src = Path(roots[0][1])
    os.rename(src / "pkg" / "b.txt", src / "pkg" / "c.txt")
    renamed = compute_source_tree_hash(roots)["sha256"]
    assert renamed != base
    os.symlink("/etc/hostname", src / "pkg" / "link")
    linked = compute_source_tree_hash(roots)
    assert linked["sha256"] != renamed and linked["files"] == 3  # link target string, not followed


# build and test inputs that must move artifact_ref (criterion set 1, finding S1-F6)
SERVER_INPUTS = ["MBNAS_server/src", "MBNAS_server/scripts", "MBNAS_server/contracts", "MBNAS_server/config",
                 "MBNAS_server/deploy", "MBNAS_server/tests", "MBNAS_server/pyproject.toml",
                 "MBNAS_server/requirements.lock.txt", "MBNAS_server/environment.lock.json", "MBNAS_server/.env.example",
                 "MBNAS_server/.gitignore"]
WEB_INPUTS = ["MBNAS_web/src", "MBNAS_web/tests", "MBNAS_web/e2e", "MBNAS_web/scripts", "MBNAS_web/public",
              "MBNAS_web/package.json", "MBNAS_web/package-lock.json", "MBNAS_web/index.html", "MBNAS_web/tsconfig.json",
              "MBNAS_web/vite.config.ts", "MBNAS_web/playwright.config.ts", "MBNAS_web/playwright.real.config.ts",
              "MBNAS_web/playwright.archive-real.config.ts", "MBNAS_web/.gitignore"]  # 2026-10-15: + archive-real e2e config
NOT_INPUTS = ["MBNAS_server/docs/ACCEPTANCE_RESULTS.md", "MBNAS_server/README.md", "MBNAS_server/.env",
              "MBNAS_web/README.md", "MBNAS_web/evidence/shot.png", "MBNAS_web/node_modules/vite/index.js",
              "MBNAS_web/dist/assets/app.js", "MBNAS_web/test-results/x.txt", "MBNAS_server/.venv/bin/python"]


def _fake_projects(tmp_path: Path) -> tuple[Path, Path]:
    """Every input of SERVER_INPUTS/WEB_INPUTS plus the non-inputs, as files (a directory input gets one file)."""
    base = tmp_path / "detection"
    for label in SERVER_INPUTS + WEB_INPUTS + NOT_INPUTS:
        rel = Path(label)
        target = base / rel if rel.suffix or rel.name.startswith(".") else base / rel / "f.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"v1 {label}\n")
    return base / "MBNAS_server", base / "MBNAS_web"


def _input_file(server: Path, web: Path, label: str) -> Path:
    project, _, rest = label.partition("/")
    rel = Path(rest)
    root = server if project == "MBNAS_server" else web
    return root / rel if rel.suffix or rel.name.startswith(".") else root / rel / "f.txt"


def _explicit_roots(server: Path, web: Path) -> list[tuple[str, str]]:
    """WP-1 (A9, DD-A29): the root set the old default_roots() built, named explicitly by the caller."""
    return [(label, str(_input_file(server, web, label) if Path(label).suffix or Path(label).name.startswith(".")
                        else (server if label.startswith("MBNAS_server/") else web) / label.partition("/")[2]))
            for label in SERVER_INPUTS + WEB_INPUTS]


def test_every_build_and_test_input_moves_the_digest(tmp_path) -> None:
    server, web = _fake_projects(tmp_path)
    roots = _explicit_roots(server, web)
    base = compute_source_tree_hash(roots)
    assert base["files"] == len(SERVER_INPUTS) + len(WEB_INPUTS)
    for label in SERVER_INPUTS + WEB_INPUTS:
        f = _input_file(server, web, label)
        f.write_text(f"v2 {label}\n")
        assert compute_source_tree_hash(roots)["sha256"] != base["sha256"], f"{label} does not move artifact_ref"
        f.write_text(f"v1 {label}\n")
    assert compute_source_tree_hash(roots)["sha256"] == base["sha256"]
    for label in NOT_INPUTS:  # docs (they record artifact_ref), secrets, captures, caches and build outputs
        f = _input_file(server, web, label)
        f.write_text("changed")
        assert compute_source_tree_hash(roots)["sha256"] == base["sha256"], f"{label} must not move artifact_ref"


def test_missing_file_root_is_recorded_not_fatal(tmp_path) -> None:
    server, web = _fake_projects(tmp_path)
    roots = _explicit_roots(server, web)
    base = compute_source_tree_hash(roots)
    (server / "requirements.lock.txt").unlink()
    gone = compute_source_tree_hash(roots)
    assert gone["sha256"] != base["sha256"] and gone["files"] == base["files"] - 1
    assert next(r for r in gone["roots"] if r["label"] == "MBNAS_server/requirements.lock.txt")["present"] is False


def test_package_roots_name_the_installed_package_directories() -> None:
    """WP-1 (A9, DD-A29): replaces the default_roots()/NOT_HASHED layout checks of the monorepo checkout (removed by
    contract §4). Hosts hash the directories of the packages they import; labels are the import names."""
    import importlib

    names = ("mbnas_contracts", "mbnas_model_runtime")  # public suite: no mbnas_core import
    roots = package_roots(names)
    assert [label for label, _ in roots] == list(names)
    for name, path in roots:
        assert Path(path) == Path(importlib.import_module(name).__file__).resolve().parent
    with pytest.raises(ModuleNotFoundError):
        package_roots(("mbnas_no_such_package",))


def test_package_roots_digest() -> None:
    """WP-1 (A9): the digest a host records over its package roots is a sha256 over > 10 files (old CLI check moved to
    the API; the CLI lives in scripts/dev/source_tree_hash.py and is covered by tests/public/contracts)."""
    full = compute_source_tree_hash(package_roots(("mbnas_contracts", "mbnas_model_runtime")))
    assert full["sha256"].startswith("sha256:") and len(full["sha256"]) == 71 and full["files"] > 10
    assert full["algorithm"] == ALGORITHM


def test_validation_devices_from_inventory(monkeypatch) -> None:
    inv = [{"index": 0, "id": "host-gpu-0", "uuid": "GPU-1", "name": "NVIDIA A100 80GB PCIe", "memory_total_mb": 81920}]
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    (dev,) = env_lock.validation_devices(inv)
    assert dev["id"] == "host-gpu-0" and "cuda:0" in dev["aliases"] and dev["precisions"] == ["FP32", "FP16"]
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert "cuda:0" not in env_lock.validation_devices(inv)[0]["aliases"]


def test_collect_shape() -> None:
    doc = env_lock.collect()
    if doc["packages"].get("torch") is None or doc["packages"].get("ultralytics") is None:  # 2026-10-15: ml extras absent
        pytest.skip("ml extras (torch/ultralytics/rfdetr) are not installed in this venv")
    assert doc["schema"] == env_lock.LOCK_SCHEMA and doc["python"]["version"].startswith("3.10")
    assert doc["packages"]["ultralytics"] == "8.4.173" and doc["packages"]["rfdetr"] == "1.11.2"
    assert doc["packages"]["torch"].startswith("2.6.0") and isinstance(doc["errors"], list)
    assert set(doc["nvidia"]) == {"driver", "cuda_driver"}

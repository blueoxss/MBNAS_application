"""scripts/source_tree_hash.py ``--manifest`` / ``--diff``: a per-file sha256 manifest at a phase boundary and the
before/after file-change list of a phase (criterion set 2, finding S2-F4: the last mtime alone cannot show which files a
phase changed). The manifest reuses the artifact_ref walk, so its digest equals artifact_ref over the same roots.

WP-1 (A9, DD-A29): the script moved to scripts/dev/source_tree_hash.py and the monorepo default roots are gone (contract
§4), so the root set is named explicitly: the manifest is built with the script's ``manifest()`` over explicit roots
(what ``--server-root/--web-root`` used to select), and ``--diff`` (root independent) still runs through the CLI."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

from conftest_common import owner_tool  # DD-A43(b)
from mbnas_contracts.util.source_hash import compute_source_tree_hash

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "dev" / "source_tree_hash.py"  # contract §11


def _script():
    spec = importlib.util.spec_from_file_location("source_tree_hash_under_test",
                                                  owner_tool("scripts/dev/source_tree_hash.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _run(*args: str) -> dict:
    out = subprocess.run([sys.executable, "-B", str(owner_tool("scripts/dev/source_tree_hash.py")), *args],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _roots(srv: Path, web: Path) -> list[tuple[str, str]]:
    """The inputs the old default_roots() selected for these fake projects (src, tests, pyproject, lock; web src)."""
    return [("MBNAS_server/src", str(srv / "src")), ("MBNAS_server/tests", str(srv / "tests")),
            ("MBNAS_server/pyproject.toml", str(srv / "pyproject.toml")),
            ("MBNAS_server/requirements.lock.txt", str(srv / "requirements.lock.txt")),
            ("MBNAS_web/src", str(web / "src"))]


def _doc_roots(srv: Path) -> list[tuple[str, str]]:
    return [("MBNAS_server/docs", str(srv / "docs"))]


def _manifest(srv: Path, web: Path, with_docs: bool = False) -> dict:
    mod, roots = _script(), _roots(srv, web)
    ref = compute_source_tree_hash(roots)["sha256"]
    return mod.manifest(roots + (_doc_roots(srv) if with_docs else []), ref)


def _projects(tmp_path: Path) -> tuple[Path, Path]:
    srv, web = tmp_path / "MBNAS_server", tmp_path / "MBNAS_web"
    for rel, text in {"src/pkg/a.py": "A = 1\n", "tests/test_a.py": "def test(): pass\n", "pyproject.toml": "[x]\n",
                      "docs/NOTES.md": "notes\n"}.items():
        (srv / rel).parent.mkdir(parents=True, exist_ok=True)
        (srv / rel).write_text(text)
    (web / "src").mkdir(parents=True)
    (web / "src" / "main.ts").write_text("export {}\n")
    return srv, web


def test_manifest_digest_equals_artifact_ref_and_lists_every_file(tmp_path: Path) -> None:
    srv, web = _projects(tmp_path)
    man = _manifest(srv, web)
    ref = compute_source_tree_hash(_roots(srv, web))
    assert man["sha256"] == man["artifact_ref"] == ref["sha256"] and man["algorithm"] == ref["algorithm"]
    assert man["files"]["MBNAS_server/src/pkg/a.py"] == __import__("hashlib").sha256(b"A = 1\n").hexdigest()
    assert man["files"]["MBNAS_server/requirements.lock.txt"] == "missing"  # a missing root is listed, not dropped
    assert not any(k.startswith("MBNAS_server/docs") for k in man["files"])  # docs are not in artifact_ref
    assert all(re.fullmatch(r"[0-9a-f]{64}|missing|symlink:[0-9a-f]{64}", d) for d in man["files"].values())
    docs = _manifest(srv, web, with_docs=True)
    assert docs["files"]["MBNAS_server/docs/NOTES.md"] and docs["artifact_ref"] == ref["sha256"] != docs["sha256"]


def test_diff_lists_added_removed_and_changed_files(tmp_path: Path) -> None:
    srv, web = _projects(tmp_path)
    before = tmp_path / "before.json"
    before.write_text(json.dumps(_manifest(srv, web, with_docs=True)))
    (srv / "src" / "pkg" / "a.py").write_text("A = 2\n")
    (srv / "src" / "pkg" / "b.py").write_text("B = 1\n")
    (srv / "tests" / "test_a.py").unlink()
    (srv / "docs" / "NOTES.md").write_text("changed\n")
    after = tmp_path / "after.json"
    after.write_text(json.dumps(_manifest(srv, web, with_docs=True)))
    diff = _run("--diff", str(before), str(after))
    assert diff["changed"] == ["MBNAS_server/docs/NOTES.md", "MBNAS_server/src/pkg/a.py"]
    assert diff["added"] == ["MBNAS_server/src/pkg/b.py"] and diff["removed"] == ["MBNAS_server/tests/test_a.py"]
    assert diff["old_sha256"] == json.loads(before.read_text())["sha256"] != diff["new_sha256"]
    assert diff["unchanged"] == len(json.loads(before.read_text())["files"]) - 3
    same = _run("--diff", str(before), str(before))
    assert same["added"] == same["removed"] == same["changed"] == []

"""util.paths.check_server_path: every rejection code and the happy paths."""
from __future__ import annotations

import os

import pytest

from mbnas_contracts.util.paths import check_server_path, free_disk_gb, nearest_existing_ancestor


@pytest.fixture
def tree(tmp_path):
    allowed = tmp_path / "allowed"
    (allowed / "d").mkdir(parents=True)
    (allowed / "d" / "f.txt").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    os.symlink(outside, allowed / "escape")
    return {"allowed": str(allowed), "outside": str(outside)}


@pytest.mark.parametrize("value,code", [
    ("relative/path", "PATH_NOT_ABSOLUTE"),
    ("", "PATH_NOT_ABSOLUTE"),
    ("~/data", "PATH_NOT_ABSOLUTE"),
    ("/ABSOLUTE/path/to/data.yaml", "PATH_PLACEHOLDER"),
    ("/data/<dataset>/data.yaml", "PATH_PLACEHOLDER"),
    ("/data/YOUR_DATASET/data.yaml", "PATH_PLACEHOLDER"),
    ("/data/a\x00b", "PATH_PLACEHOLDER"),
])
def test_syntax_rejections(tree, value: str, code: str) -> None:
    r = check_server_path(value, kind="file", allow_roots=[tree["allowed"]])
    assert not r.ok and r.code == code and r.message


def test_non_string_rejected(tree) -> None:
    r = check_server_path(None, kind="dir", allow_roots=[tree["allowed"]])  # type: ignore[arg-type]
    assert r.code == "PATH_NOT_ABSOLUTE"


def test_traversal_rejected_even_if_it_resolves_inside(tree) -> None:
    r = check_server_path(f"{tree['allowed']}/d/../d/f.txt", kind="file", allow_roots=[tree["allowed"]])
    assert r.code == "PATH_TRAVERSAL"


def test_outside_allowlist(tree) -> None:
    r = check_server_path(f"{tree['outside']}/secret.txt", kind="file", allow_roots=[tree["allowed"]])
    assert r.code == "PATH_OUTSIDE_ALLOWLIST" and r.realpath.endswith("secret.txt")


def test_symlink_escaping_allowlist_rejected(tree) -> None:
    r = check_server_path(f"{tree['allowed']}/escape/secret.txt", kind="file", allow_roots=[tree["allowed"]])
    assert r.code == "PATH_OUTSIDE_ALLOWLIST"
    assert r.requested.endswith("escape/secret.txt") and r.realpath == os.path.realpath(f"{tree['outside']}/secret.txt")


def test_prefix_sibling_is_not_inside(tree, tmp_path) -> None:
    sibling = tmp_path / "allowed2"
    sibling.mkdir()
    r = check_server_path(str(sibling), kind="dir", allow_roots=[tree["allowed"]])
    assert r.code == "PATH_OUTSIDE_ALLOWLIST"


def test_missing_and_kind_mismatch(tree) -> None:
    assert check_server_path(f"{tree['allowed']}/nope", kind="dir", allow_roots=[tree["allowed"]]).code == "PATH_NOT_FOUND"
    r = check_server_path(f"{tree['allowed']}/d", kind="file", allow_roots=[tree["allowed"]])
    assert r.code == "PATH_NOT_FOUND" and "not a regular file" in r.message
    r = check_server_path(f"{tree['allowed']}/d/f.txt", kind="dir", allow_roots=[tree["allowed"]])
    assert r.code == "PATH_NOT_FOUND"


def test_not_readable(tree, monkeypatch) -> None:
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    r = check_server_path(f"{tree['allowed']}/d/f.txt", kind="file", allow_roots=[tree["allowed"]])
    assert r.code == "PATH_NOT_READABLE"


def test_output_not_writable(tree, monkeypatch) -> None:
    real_access = os.access
    monkeypatch.setattr(os, "access", lambda path, mode: real_access(path, mode) if not mode & os.W_OK else False)
    r = check_server_path(f"{tree['allowed']}/new_out", kind="dir", allow_roots=[tree["allowed"]],
                          must_exist=False, writable=True)
    assert r.code == "OUTPUT_NOT_WRITABLE"


def test_ok_paths(tree) -> None:
    assert check_server_path(f"{tree['allowed']}/d/f.txt", kind="file", allow_roots=[tree["allowed"]]).ok
    r = check_server_path(f"{tree['allowed']}/new/out", kind="dir", allow_roots=[tree["allowed"]], must_exist=False,
                          writable=True)
    assert r.ok and r.code is None and r.as_dict()["realpath"].endswith("new/out")
    assert nearest_existing_ancestor(f"{tree['allowed']}/new/out") == tree["allowed"]
    assert free_disk_gb(f"{tree['allowed']}/new/out") > 0

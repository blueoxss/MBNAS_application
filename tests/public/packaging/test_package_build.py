"""Packager tests (WP-7D; WP-7 contract §1 package.sh row and §4): allowlist staging, wheels, metadata, checks, ZIP.

Runs ``scripts/dev/package_build.py`` (what ``scripts/package.sh`` executes) on a synthetic tree that uses the real
allowlists (``conftest.make_tree``). No network, no GPU: wheels are built with ``pip wheel --no-index``.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sysconfig
import zipfile
from pathlib import Path

import pytest

from pkgtest_support import CORE, PUBLIC_PKGS, REPO, VERSION, load_dev, members, report, run_build, zip_of

TOP = "mbnas_application/"
TAG = load_dev("package_build").platform_tag("auto")


def _rels(zpath: Path) -> set[str]:
    return {n[len(TOP):] for n in members(zpath)}


def _read(zpath: Path, rel: str) -> bytes:
    with zipfile.ZipFile(zpath) as zf:
        return zf.read(TOP + rel)


def _no_private_component(rels: set[str]) -> None:
    for rel in rels:
        assert not any(part.lower().startswith(CORE) or part.lower().startswith("mbnas-core")
                       for part in rel.split("/")), rel


def test_platform_tag_is_derived_from_the_host() -> None:
    assert TAG == sysconfig.get_platform().replace("-", "_").replace(".", "_").lower()


def test_public_zip_name_layout_and_sha256_file(built: dict) -> None:
    root = built["root"]
    z = zip_of(root, "public")
    assert z.name == f"mbnas_application-{VERSION}-public-runtime-{TAG}.zip"
    sha_line = (z.parent / (z.name + ".sha256")).read_text()
    assert sha_line == f"{hashlib.sha256(z.read_bytes()).hexdigest()}  {z.name}\n"
    names = list(members(z))
    assert names == sorted(names) and len(names) == len(set(names))
    assert all(n.startswith(TOP) and ".." not in n.split("/") and not n.startswith("/") for n in names)


def test_public_zip_holds_exactly_the_public_parts(built: dict) -> None:
    rels = _rels(zip_of(built["root"], "public"))
    for pkg in PUBLIC_PKGS:
        assert f"wheels/{pkg}-{VERSION}-py3-none-any.whl" in rels
        assert f"{pkg}/src/{pkg}/__init__.py" in rels and f"{pkg}/pyproject.toml" in rels
    for rel in ("mbnas_web/dist/index.html", "mbnas_web/dist/assets/app.js", "mbnas_web/package.json",
                "mbnas_web/src/main.ts", "mbnas_web/scripts/check-bundle.mjs",  # web sources ship (DD-A41)
                "tests/public/test_synthetic.py", "tests/_shared/conftest_common.py",  # public tests (DD-A41)
                "config/examples/gateway.env.example", "config/examples/runner.env.example", "deploy/public/Dockerfile",
                "docs/public/INSTALL.md", "examples/sample.txt", "scripts/install.sh", "scripts/verify.sh",
                "README.md", "VERSION", "constraints.txt", "THIRD_PARTY_NOTICES.md", "requirements-public.txt",
                "requirements-cpu.txt", "pyproject.toml",  # pytest configuration (DD-A43 follow-up)
                ".dockerignore",  # docker build from the unpacked ZIP (WP-7B)
                "BUILD_INFO.json", "PACKAGE_PROFILE.json", "FILE_MANIFEST.jsonl"):
        assert rel in rels, rel
    _no_private_component(rels)
    forbidden_prefixes = ("tests/owner/", "docs/internal/", "docs/registration/", "deploy/owner/", "runtime/", "build/",
                          "scripts/dev/")
    assert not [r for r in rels if r.startswith(forbidden_prefixes)]
    assert not [r for r in rels if "node_modules" in r or "__pycache__" in r or r.endswith((".pyc", ".env"))]
    assert not {"config/examples/core.env.example", "config/examples/owner-all-in-one.env.example",
                "requirements-owner.txt", ".env", "LICENSE_STATUS.md",  # owner only (DD-A41)
                "tests/public/infra/test_no_host_facts.py", "tests/_shared/harness.py"} & rels


def test_shell_scripts_are_executable_and_modes_normalised(built: dict) -> None:
    infos = members(zip_of(built["root"], "public"))
    for name, info in infos.items():
        mode = (info.external_attr >> 16) & 0o7777
        assert stat.S_ISREG(info.external_attr >> 16), name
        assert mode == (0o755 if name.endswith(".sh") else 0o644), (name, oct(mode))


def test_file_manifest_lists_every_other_entry_with_digest(built: dict) -> None:
    z = zip_of(built["root"], "public")
    lines = _read(z, "FILE_MANIFEST.jsonl").decode().splitlines()
    entries = {e["relpath"]: e for e in map(json.loads, lines)}
    assert "FILE_MANIFEST.jsonl" not in entries  # no self reference
    assert set(entries) == _rels(z) - {"FILE_MANIFEST.jsonl"}
    with zipfile.ZipFile(z) as zf:
        for rel, e in entries.items():
            data = zf.read(TOP + rel)
            assert e["sha256"] == hashlib.sha256(data).hexdigest() and e["bytes"] == len(data), rel
            assert set(e) == {"relpath", "bytes", "sha256", "role", "origin", "licence"}
    assert entries["wheels/mbnas_server-0.1.0-py3-none-any.whl"]["origin"] == "built:pip-wheel:mbnas_server"
    assert entries["wheels/mbnas_server-0.1.0-py3-none-any.whl"]["role"] == "wheel"
    assert entries["BUILD_INFO.json"]["origin"] == "generated:package_build"
    assert entries["docs/public/INSTALL.md"]["origin"] == "tree"


def test_build_info_and_package_profile(built: dict) -> None:
    root = built["root"]
    z = zip_of(root, "public")
    info = json.loads(_read(z, "BUILD_INFO.json"))
    prof = json.loads(_read(z, "PACKAGE_PROFILE.json"))
    assert info["version"] == VERSION and info["profile"] == "public" and info["build_id"]
    assert prof["profile"] == "public"  # install.sh reads the top-level profile key
    assert info["platform"]["tag"] == TAG and info["source_tree"]["git"] is None
    assert info["source_tree"]["roots"] == list(PUBLIC_PKGS)
    # the recorded digest is the source_tree_hash.py algorithm over the staged package directories
    sth = load_dev("source_tree_hash")
    staged = root / "build" / "staging" / "public" / "mbnas_application"
    expect = sth.compute_source_tree_hash([(p, str(staged / p / "src" / p)) for p in PUBLIC_PKGS])["sha256"]
    assert info["source_tree"]["sha256"] == expect
    assert info["checks"]["counts"]["violation"] == 0
    assert info["web_dist"]["present"] is True and info["web_dist"]["bundle_gate"]["status"] == "pass"
    assert [w["import_name"] for w in info["packages"]] == list(PUBLIC_PKGS)
    assert prof["core_connection_required"] is True and prof["core_included"] is False
    assert prof["packages"]["included"] == list(PUBLIC_PKGS)
    noted = {e["pattern"]: e["reason"] for e in prof["exclusions_with_reason"]}
    assert "host-fact" in noted["tests/public/infra/test_no_host_facts.py"] and "LICENSE_STATUS.md" in noted
    for doc in (_read(z, "BUILD_INFO.json"), _read(z, "PACKAGE_PROFILE.json"), _read(z, "FILE_MANIFEST.jsonl")):
        text = doc.decode()
        assert str(root) not in text and "/home/" not in text  # no absolute host paths
        assert CORE not in text  # the private package is not named in public metadata


def test_owner_zip_adds_the_private_parts_but_no_canary_or_snapshot(built: dict) -> None:
    z = zip_of(built["root"], "owner")
    assert z.name == f"mbnas_application-{VERSION}-owner-private-runtime-{TAG}.zip"
    rels = _rels(z)
    for rel in (f"wheels/{CORE}-{VERSION}-py3-none-any.whl", f"{CORE}/src/{CORE}/search/space.py",
                "tests/owner/conftest.py", "tests/public/test_synthetic.py", "tests/_shared/harness.py",
                "tests/public/infra/test_no_host_facts.py", "LICENSE_STATUS.md",
                "docs/internal/DECISIONS.md", "deploy/owner/Dockerfile", "config/examples/core.env.example",
                "requirements-owner.txt", "pyproject.toml", "scripts/dev/tool.py"):
        assert rel in rels, rel
    assert not [r for r in rels if r.startswith("docs/internal/canary/")]
    assert "docs/internal/provenance/SOURCE_SNAPSHOT_MANIFEST.jsonl" not in rels
    assert not [r for r in rels if r.startswith(("runtime/", "build/")) or r.endswith(".env") or "node_modules" in r]
    prof = json.loads(_read(z, "PACKAGE_PROFILE.json"))
    assert prof["core_included"] is True and CORE in prof["packages"]["included"]


def test_registration_zip_has_docs_and_generated_file_list(built: dict) -> None:
    z = zip_of(built["root"], "registration")
    assert z.name == f"mbnas_application-{VERSION}-registration-runtime-{TAG}.zip"
    rels = _rels(z)
    assert "docs/registration/PROGRAM_OVERVIEW.md" in rels and "docs/registration/FILE_LIST.md" in rels
    _no_private_component(rels)
    assert not [r for r in rels if r.startswith(("tests/owner/", "docs/internal/", "deploy/owner/"))]
    assert "tests/public/test_synthetic.py" in rels and "LICENSE_STATUS.md" not in rels
    listing = _read(z, "docs/registration/FILE_LIST.md").decode()
    assert "mbnas_contracts/pyproject.toml" in listing and "docs/registration/PROGRAM_OVERVIEW.md" in listing
    assert CORE + "/" not in listing
    entries = {e["relpath"]: e for e in map(json.loads, _read(z, "FILE_MANIFEST.jsonl").decode().splitlines())}
    assert entries["docs/registration/FILE_LIST.md"]["origin"] == "generated:gen_registration_docs"
    assert entries["docs/registration/FILE_LIST.md"]["sha256"] == hashlib.sha256(
        _read(z, "docs/registration/FILE_LIST.md")).hexdigest()


def test_web_tests_importing_the_fitness_oracle_stay_in_the_owner_bundle(built: dict) -> None:
    root = built["root"]
    oracle_users = {"mbnas_web/tests/fitness_oracle.ts", "mbnas_web/tests/fixtures.ts",
                    "mbnas_web/tests/uses_fixtures.test.ts", "mbnas_web/e2e/oracle.spec.ts"}
    for profile in ("public", "registration"):
        rels = _rels(zip_of(root, profile))
        assert not oracle_users & rels and "mbnas_web/tests/standalone.test.ts" in rels, profile
        prof = json.loads(_read(zip_of(root, profile), "PACKAGE_PROFILE.json"))
        entry = [e for e in prof["exclusions_with_reason"] if e.get("files")]
        assert entry and set(entry[0]["files"]) == oracle_users and "DD-A43" in entry[0]["reason"]
    assert oracle_users <= _rels(zip_of(root, "owner"))


def test_prose_mention_of_the_private_package_is_reported_not_failed(built: dict) -> None:
    findings = report(built["root"], "public")["findings"]
    assert any(f["check"] == "private_mention" and f["path"] == "docs/public/INSTALL.md" and f["severity"] == "info"
               for f in findings)


def _violations(root: Path, profile: str) -> list[dict]:
    return [f for f in report(root, profile)["findings"] if f["severity"] == "violation"]


def test_private_import_in_a_public_package_fails_the_build(tree: Path) -> None:
    init = tree / "mbnas_server" / "src" / "mbnas_server" / "__init__.py"
    init.write_text(init.read_text() + f"import {CORE}.search\nHOOK = '{CORE}.protocol.fitness:score'\n")
    proc = run_build(tree, "--profile", "public")
    assert proc.returncode == 1, proc.stdout[-3000:]
    bad = [f for f in _violations(tree, "public") if f["check"] == "private_import"]
    assert {f["detail"].split(" ")[0] for f in bad} >= {"import", "string"}, bad
    assert not list((tree / "dist" / "public").glob("*.zip"))  # nothing published


def test_private_requirement_in_a_public_text_file_fails_the_build(tree: Path) -> None:
    (tree / "requirements-cpu.txt").write_text(f"./wheels/{CORE}-0.1.0-py3-none-any.whl\n")
    proc = run_build(tree, "--profile", "public")
    assert proc.returncode == 1
    assert any(f["check"] == "private_import" and f["path"].startswith("requirements-cpu.txt")
               for f in _violations(tree, "public"))


def test_host_fact_canary_token_private_key_and_secret_value_fail(tree: Path) -> None:
    scan = load_dev("canary_scan")
    host_fact = scan.host_facts()[1].decode()
    token = scan.PREFIX + "TESTAREA-" + "0123456789abcdef"[::-1]
    (tree / "docs" / "public" / "LEAK.md").write_text(f"path {host_fact}/x\ntoken {token}\n")
    (tree / "examples" / "key.txt").write_text("-----BEGIN " + "OPENSSH PRIVATE KEY-----\nabc\n")
    (tree / "config" / "examples" / "gateway.env.example").write_text("MBNAS_GW_TOKEN=Zq8" + "x7Lp2Vb9Nw4Rt6Ky1\n")
    proc = run_build(tree, "--profile", "public")
    assert proc.returncode == 1
    checks = {f["check"] for f in _violations(tree, "public")}
    assert {"host_fact", "canary_token", "private_key", "secret_like_value"} <= checks, checks
    assert "Zq8x7Lp2" not in proc.stdout  # the secret value itself is never printed


def test_web_dist_missing_fails_public_unless_allowed(tree: Path) -> None:
    import shutil

    shutil.rmtree(tree / "mbnas_web" / "dist")
    proc = run_build(tree, "--profile", "public")
    assert proc.returncode == 1 and "web dist missing" in proc.stdout
    assert any(f["check"] == "web_dist_missing" for f in _violations(tree, "public"))
    proc = run_build(tree, "--profile", "public", "--allow-missing-web")
    assert proc.returncode == 0, proc.stdout[-3000:]
    info = json.loads(_read(zip_of(tree, "public"), "BUILD_INFO.json"))
    assert info["web_dist"]["present"] is False and "allowed by --allow-missing-web" in info["web_dist"]["status"]
    assert run_build(tree, "--profile", "owner").returncode == 0  # optional for the owner bundle


def test_web_bundle_gate_rejects_a_forbidden_string_in_the_staged_dist(tree: Path) -> None:
    js = tree / "mbnas_web" / "dist" / "assets" / "app.js"
    js.write_text(js.read_text() + "const host = '" + "pc" + "2300" + "';\n")  # a host fact of the WP-6A needle list
    proc = run_build(tree, "--profile", "public")
    assert proc.returncode == 1
    bad = [f for f in _violations(tree, "public") if f["check"] == "web_bundle_gate"]
    assert bad and bad[0]["path"] == "mbnas_web/dist/app.js", bad


def test_symlink_and_case_colliding_entries_fail(tree: Path) -> None:
    os.symlink("INSTALL.md", tree / "docs" / "public" / "link.md")
    (tree / "docs" / "public" / "install.md").write_text("# lower-case twin\n")
    proc = run_build(tree, "--profile", "public")
    assert proc.returncode == 1
    checks = {f["check"] for f in _violations(tree, "public")}
    assert {"symlink", "case_collision"} <= checks, checks


def test_reproducible_build_is_identical_twice_and_across_invocations(tree: Path) -> None:
    first = run_build(tree, "--profile", "public", "--reproducible")
    assert first.returncode == 0, first.stdout[-3000:]
    z = zip_of(tree, "public")
    sha1 = hashlib.sha256(z.read_bytes()).hexdigest()
    info = json.loads(_read(z, "BUILD_INFO.json"))
    rep = info["reproducibility"]
    assert rep["equal"] is True and rep["builds"] == 2 and rep["zip_sha256"][0] == rep["zip_sha256"][1]
    assert info["build_id"].startswith("r-") and info["built_at"] == "1980-01-01T00:00:00Z"
    assert {i.date_time for i in members(z).values()} == {(1980, 1, 1, 0, 0, 0)}
    # leftovers of an earlier run (other agents' files staged in a previous wave) never reach the next build
    stale_stage = tree / "build" / "staging" / "public" / "mbnas_application" / "docs" / "public" / "STALE.md"
    stale_wheel = tree / "build" / "wheel_build" / "public.pass1" / "src" / "mbnas_server" / "src" / "mbnas_server"
    stale_stage.write_text("stale\n")
    stale_wheel.mkdir(parents=True, exist_ok=True)
    (stale_wheel / "stale_module.py").write_text("STALE = 1\n")
    second = run_build(tree, "--profile", "public", "--reproducible")
    assert second.returncode == 0
    assert hashlib.sha256(zip_of(tree, "public").read_bytes()).hexdigest() == sha1
    assert not stale_stage.exists() and not (stale_wheel / "stale_module.py").exists()
    with zipfile.ZipFile(zip_of(tree, "public")) as zf:
        whl = zf.read(TOP + "wheels/mbnas_server-0.1.0-py3-none-any.whl")
    with zipfile.ZipFile(__import__("io").BytesIO(whl)) as wf:
        assert "mbnas_server/stale_module.py" not in wf.namelist()


def test_unknown_allowlist_section_is_a_usage_error(tree: Path) -> None:
    al = tree / "build" / "allowlists" / "public.txt"
    al.write_text(al.read_text() + "\n[surprise]\nx\n")
    proc = run_build(tree, "--profile", "public")
    assert proc.returncode == 2 and "unknown section" in proc.stderr


@pytest.mark.parametrize("pattern,path,expected", [
    ("docs/public/**", "docs/public/a/b.md", True),
    ("docs/public/", "docs/public/x.md", True),
    ("**/__pycache__/**", "a/b/__pycache__/c.pyc", True),
    ("**/*.env", "config/public/gateway.env", True),
    ("**/*.env", "config/examples/gateway.env.example", False),
    ("mbnas_web/dist/**/*.map", "mbnas_web/dist/assets/app.js.map", True),
    ("LICENSE*", "LICENSE_STATUS.md", True),
    ("dist/**", "mbnas_web/dist/index.html", False),
    ("scripts/*.sh", "scripts/dev/x.sh", False),
])
def test_glob_semantics(pattern: str, path: str, expected: bool) -> None:
    assert load_dev("package_build").Glob(pattern).match(path) is expected


def _sections(profile: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = "include"
    for line in (REPO / "build" / "allowlists" / f"{profile}.txt").read_text().splitlines():
        text = "" if line.lstrip().startswith("#") else line.split(" #", 1)[0].strip()
        if not text:
            continue
        text = text.split(" :: ", 1)[0].strip()  # '<glob> :: <reason>' / '<glob> :: <literal>' 
        if text.startswith("[") and text.endswith("]"):
            cur = text[1:-1]
            continue
        out.setdefault(cur, []).append(text)
    return out


def test_real_allowlists_keep_the_profile_boundaries() -> None:
    if not (REPO / "build" / "allowlists" / "public.txt").is_file():
        pytest.skip("not a source tree: build/allowlists absent")
    pub, own, reg = (_sections(p) for p in ("public", "owner", "registration"))
    assert set(pub["include"]) <= set(own["include"]) and set(pub["include"]) <= set(reg["include"])
    assert set(reg["include"]) - set(pub["include"]) == {"docs/registration/**"}
    everywhere = {"runtime/**", "build/**", "dist/**", "**/node_modules/**", "**/__pycache__/**", "**/*.pyc",
                  "**/.env", "**/*.env", "docs/internal/canary/**"}
    for sec in (pub, own, reg):
        assert everywhere <= set(sec["deny"])
    for sec in (pub, reg):
        assert {f"{CORE}/**", "tests/owner/**", "docs/internal/**", "deploy/owner/**", "requirements-owner.txt",
                "LICENSE_STATUS.md", "tests/public/infra/test_no_host_facts.py", "tests/_shared/harness.py"} <= set(
            sec["deny"])
        assert "tests/public/**" in sec["include"] and "mbnas_web/src/**" in sec["include"]  # DD-A41
        assert "pyproject.toml" in sec["include"]  # DD-A43 follow-up
        assert sec["deny-importers"] == ["mbnas_web/tests/fitness_oracle.ts"]  # DD-A43 (a)
        assert CORE not in sec["wheels"] and set(sec["wheels"]) == set(PUBLIC_PKGS)
        assert "private_package_check = strict" in sec["profile"] and "host_facts = fail" in sec["profile"]
    for sec in (pub, own, reg):
        assert ".dockerignore" in sec["include"] and ".dockerignore" in sec["required"]  # WP-7B docker context
    assert CORE in own["wheels"] and f"{CORE}/src/**" in own["include"]

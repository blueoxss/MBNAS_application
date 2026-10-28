"""Packager round 4 (C09 D3, C10 D2-D5/D7, C11 D7, DD-A65): PACKAGE_CONTENTS.md in every bundle, a licence column that
names only shipped files, a registration build independent of the source FILE_LIST.md placeholder, the per-check
report, the 'test' role of web test rows, the wheel-contents / package-data gate with its member listing, and a
FILE_LIST.md that the shipped registration ZIP regenerates byte for byte.

Runs ``scripts/dev/package_build.py`` on the synthetic tree of ``pkgtest_support`` (real allowlists), as
``test_package_build.py`` does. The private package name is composed (``CORE``), never written as a module reference.
"""
from __future__ import annotations

import collections
import hashlib
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from pkgtest_support import CORE, DEV, PUBLIC_PKGS, REPO, VERSION, load_dev, make_tree, members, report, run_build, \
    zip_of

TOP = "mbnas_application/"
PB = load_dev("package_build")
PROFILES = ("public", "registration", "owner")
ROW = re.compile(r"^\| \d+ \| `")


def _read(z: Path, rel: str) -> bytes:
    with zipfile.ZipFile(z) as zf:
        return zf.read(TOP + rel)


def _rels(z: Path) -> set[str]:
    return {n[len(TOP):] for n in members(z)}


def _manifest(z: Path) -> dict[str, dict]:
    return {e["relpath"]: e for e in map(json.loads, _read(z, "FILE_MANIFEST.jsonl").decode().splitlines())}


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split(" | ")]


# ------------------------------------------------------------------------------------------- C10 D2: PACKAGE_CONTENTS
def test_package_contents_md_in_every_profile_matches_the_manifest(built: dict) -> None:
    for profile in PROFILES:
        z = zip_of(built["root"], profile)
        man, info = _manifest(z), json.loads(_read(z, "BUILD_INFO.json"))
        assert "PACKAGE_CONTENTS.md" in _rels(z) and man["PACKAGE_CONTENTS.md"]["role"] == "build-metadata"
        data = _read(z, "PACKAGE_CONTENTS.md")
        assert man["PACKAGE_CONTENTS.md"]["sha256"] == hashlib.sha256(data).hexdigest()
        text = data.decode()
        assert text.startswith(f"# PACKAGE_CONTENTS - {z.name}\n")
        for needle in (f"| ZIP | `{z.name}`", f"| 버전 (version) | {VERSION} |", f"| 프로필 (profile) | {profile} |",
                       f"| build id | `{info['build_id']}` |", f"| 플랫폼 (platform) | {info['platform']['tag']} |"):
            assert needle in text, (profile, needle)
        # every ZIP entry except the manifest and the document itself, in path order, with the manifest's values
        rows = [_cells(line) for line in text.splitlines() if ROW.match(line)]
        expect = sorted(r for r in man if r != "PACKAGE_CONTENTS.md")
        assert len(rows) == len(expect) == len(_rels(z)) - 2, profile
        for cells, rel in zip(rows, expect):
            e = man[rel]
            assert cells[1:] == [f"`{rel}`", f"{e['bytes']:,}", f"`{e['sha256'][:16]}`", e["role"], e["licence"]], cells
        assert f"ZIP 항목 {len(_rels(z)):,}개" in text
        # totals per role: one line per role of the table, adding up to the table
        section = text.split("## 역할별 합계 (totals per role)", 1)[1].split("##", 1)[0]
        per_role = {c[0]: (int(c[1].replace(",", "")), int(c[2].replace(",", "")))
                    for c in (_cells(line) for line in section.splitlines() if line.startswith("| ") and "**" not in line
                              and not line.startswith("| 역할"))}
        roles = collections.Counter(man[r]["role"] for r in expect)
        assert {k: v[0] for k, v in per_role.items()} == dict(roles), profile
        assert sum(v[1] for v in per_role.values()) == sum(man[r]["bytes"] for r in expect)
        # the packager's own renderer gives the same bytes from the shipped manifest (what verify_zip re-checks)
        header = {"zip": z.name, "version": VERSION, "profile": profile, "build_id": info["build_id"],
                  "platform": info["platform"]["tag"]}
        assert PB.render_package_contents(list(man.values()), header) == data


def test_payload_and_build_id_do_not_cover_the_manifest_rendered_documents(built: dict) -> None:
    info = json.loads(_read(zip_of(built["root"], "registration"), "BUILD_INFO.json"))
    assert "PACKAGE_CONTENTS.md" in info["payload_sha256_covers"] and "FILE_LIST.md" in info["payload_sha256_covers"]
    assert set(PB.LATE) == {"docs/registration/FILE_LIST.md", "PACKAGE_CONTENTS.md"}


# ------------------------------------------------------------------------------------------ C10 D3: licence column
def test_manifest_licence_column_names_only_files_of_the_same_bundle(built: dict) -> None:
    for profile in PROFILES:
        z = zip_of(built["root"], profile)
        rels, man = _rels(z), _manifest(z)
        for rel, e in man.items():
            assert "LICENSE_STATUS" not in e["licence"], (profile, rel, e["licence"])
            for ref in re.findall(r"\b(LICENSE|THIRD_PARTY_NOTICES(?:_WEB)?\.md)\b", e["licence"]):
                assert ref in rels, (profile, rel, ref)
        assert man["README.md"]["licence"] == "project (licence undetermined; LICENSE)"
        assert "THIRD_PARTY_NOTICES_WEB.md" in man["mbnas_web/dist/index.html"]["licence"]
        assert man["wheels/mbnas_model_runtime-0.1.0-py3-none-any.whl"]["licence"].endswith("(THIRD_PARTY_NOTICES.md)")
        assert man["LICENSE"]["role"] == man["THIRD_PARTY_NOTICES_WEB.md"]["role"] == "licence-notice"
    assert "LICENSE_STATUS.md" in _rels(zip_of(built["root"], "owner"))  # still owner only (DD-A41 c)
    for profile in ("public", "registration"):
        assert "LICENSE_STATUS.md" not in _rels(zip_of(built["root"], profile))


@pytest.mark.parametrize("rel,ref", [
    ("mbnas_model_runtime/src/mbnas_model_runtime/operators/parse_model_8_4_173.py", "THIRD_PARTY_NOTICES.md"),
    ("mbnas_model_runtime/src/mbnas_model_runtime/operators/etri_mixed_blocks.py", "THIRD_PARTY_NOTICES.md"),
    ("mbnas_model_runtime/src/mbnas_model_runtime/adapters/etri_hybrid/model.py", "THIRD_PARTY_NOTICES.md"),
    ("examples/models/yolo11n_user.yaml", "THIRD_PARTY_NOTICES.md"),
    ("examples/models/yolo11n_user_broken.yaml", "THIRD_PARTY_NOTICES.md"),
    ("wheels/mbnas_model_runtime-0.1.0-py3-none-any.whl", "THIRD_PARTY_NOTICES.md"),
    ("mbnas_web/dist/assets/index-abc.js", "THIRD_PARTY_NOTICES_WEB.md"),
    ("mbnas_server/src/mbnas_server/api/app.py", "LICENSE"),
    ("docs/registration/FILE_LIST.md", "LICENSE"),
])
def test_licence_rules_send_third_party_derived_files_to_the_notices(rel: str, ref: str) -> None:
    lic = PB.first_match(PB.LICENCE_RULES, rel)
    assert ref in lic and "LICENSE_STATUS" not in lic, lic
    if ref == "LICENSE":
        assert lic == "project (licence undetermined; LICENSE)"
    else:
        assert "THIRD_PARTY_NOTICES" in lic


# ------------------------------------------------------------------------------------------------- C10 D7: roles
def test_web_test_and_e2e_rows_have_role_test(built: dict) -> None:
    for profile in PROFILES:
        man = _manifest(zip_of(built["root"], profile))
        web_tests = {r: e["role"] for r, e in man.items() if r.startswith(("mbnas_web/tests/", "mbnas_web/e2e/"))}
        assert web_tests and set(web_tests.values()) == {"test"}, (profile, web_tests)
    assert _manifest(zip_of(built["root"], "owner"))["mbnas_web/e2e/oracle.spec.ts"]["role"] == "test"
    for rel, role in (("mbnas_web/playwright.config.ts", "web-config"), ("mbnas_web/tsconfig.json", "web-config"),
                      ("mbnas_web/vite.config.ts", "web-config"), ("mbnas_web/package-lock.json", "web-provenance"),
                      ("mbnas_web/scripts/check-bundle.mjs", "script"), ("PACKAGE_CONTENTS.md", "build-metadata"),
                      ("mbnas_web/index.html", "package-source"), ("mbnas_web/src/main.tsx", "package-source"),
                      ("THIRD_PARTY_NOTICES_WEB.md", "licence-notice")):
        assert PB.first_match(PB.ROLE_RULES, rel) == role, rel


# -------------------------------------------------------------------------------------- C10 D4: per-check report
def test_report_and_output_name_every_check_with_its_status(built: dict) -> None:
    ids = [c[0] for c in PB.CHECKS]
    for profile in PROFILES:
        rows = {c["id"]: c for c in report(built["root"], profile)["checks"]}
        assert [c for c in rows if c != "other"] == ids, profile
        assert {c["status"] for c in rows.values()} <= {"pass", "warn", "fail", "not run"}
        assert rows["reproducibility"]["status"] == "not run"  # this build was not --reproducible
        for cid in ("selection", "deny_patterns", "required_files", "web_dist", "web_bundle_gate", "path_safety",
                    "case_collision", "content_scan", "wheel_build", "wheel_contents", "versions", "generated_declared",
                    "zip_entries", "manifest", "package_contents"):
            assert rows[cid]["status"] in ("pass", "warn") and rows[cid]["violations"] == 0, (profile, rows[cid])
        assert rows["private_package"]["status"] == ("not run" if profile == "owner" else "pass")
        out = built[profile].stdout
        for cid in ids:
            assert re.search(rf"^\[package\] check {cid} +(PASS|WARN|FAIL|NOT RUN) ", out, re.M), (profile, cid)
        assert re.search(rf"^\[package\] checks: 0 violation\(s\), .* of {len(ids)} checks ran", out, re.M), out[-800:]
        info = json.loads(_read(zip_of(built["root"], profile), "BUILD_INFO.json"))
        assert [r["id"] for r in info["checks"]["run"]] == [c for c in ids if c not in PB.POST_METADATA_CHECKS]
        assert info["checks"]["after_this_file"]["checks"] == list(PB.POST_METADATA_CHECKS)


def test_a_failing_check_is_named_fail_and_later_checks_not_run(tree: Path) -> None:
    (tree / "docs" / "public" / "install.md").write_text("# lower-case twin of INSTALL.md\n")
    assert run_build(tree, "--profile", "public").returncode == 1
    rows = {c["id"]: c for c in report(tree, "public")["checks"]}
    assert rows["case_collision"]["status"] == "fail" and rows["case_collision"]["violations"] == 1
    for cid in ("wheel_build", "wheel_contents", "zip_entries", "manifest", "package_contents"):
        assert rows[cid]["status"] == "not run", rows[cid]


# ------------------------------------------------------------------------- C09 D3: wheel contents / package-data
def test_wheel_contents_gate_passes_and_the_listing_ships_in_the_owner_bundle(built: dict) -> None:
    root = built["root"]
    by = {w["wheel"].split("/")[-1].split("-")[0]: w for w in report(root, "owner")["wheel_contents"]}
    assert set(by) == set(PUBLIC_PKGS) | {CORE}
    for w in by.values():
        assert w["status"] == "pass" and not (w["missing"] or w["extra"] or w["mismatch"]), w
    core = by[CORE]
    assert core["package_data_declared"] == {f"{CORE}.db": ["schema.sql"]}
    src = (root / CORE / "src" / CORE / "db" / "schema.sql").read_bytes()
    assert core["package_data"] == [{"package": f"{CORE}.db", "pattern": "schema.sql", "path": f"{CORE}/db/schema.sql",
                                     "sha256": hashlib.sha256(src).hexdigest(), "in_wheel": True}]
    z = zip_of(root, "owner")
    listing = (root / "build" / "package_reports" / "owner_wheels.txt").read_bytes()
    assert _read(z, "docs/internal/evidence/package_build/owner_wheels.txt") == listing  # same text, shipped (owner)
    text = listing.decode()
    assert f"[data] {CORE}/db/schema.sql" in text and text.count("gate: PASS") == 5 and str(root) not in text
    with zipfile.ZipFile(z) as zf:  # the listing names exactly the members of the shipped wheels
        for name in (n for n in zf.namelist() if n.endswith(".whl")):
            with zipfile.ZipFile(io.BytesIO(zf.read(name))) as wf:
                for m in wf.namelist():
                    if ".dist-info/" not in m and not m.endswith("/"):
                        assert f"  {hashlib.sha256(wf.read(m)).hexdigest()}  " in text and m in text, m
    info = json.loads(_read(z, "BUILD_INFO.json"))
    pk = {p["import_name"]: p for p in info["packages"]}
    assert pk[CORE]["package_data"] == [{"path": f"{CORE}/db/schema.sql", "sha256": hashlib.sha256(src).hexdigest()}]
    for profile in ("public", "registration"):
        assert (root / "build" / "package_reports" / f"{profile}_wheels.txt").is_file()
        assert not [r for r in _rels(zip_of(root, profile)) if r.startswith("docs/internal/")]


def test_wheel_contents_gate_fails_on_undeclared_and_on_missing_package_data(tree: Path) -> None:
    pp = tree / CORE / "pyproject.toml"
    text = pp.read_text(encoding="utf-8")
    assert f'"{CORE}.db" = ["schema.sql"]' in text
    # schema.sql no longer declared (so it cannot reach the wheel) and a declared file that does not exist
    pp.write_text(text.replace(f'"{CORE}.db" = ["schema.sql"]', f'"{CORE}.protocol" = ["handoff.json"]'),
                  encoding="utf-8")
    proc = run_build(tree, "--profile", "owner")
    assert proc.returncode == 1, proc.stdout[-3000:]
    rep = report(tree, "owner")
    bad = {(f["check"], f["path"]) for f in rep["findings"] if f["severity"] == "violation"}
    assert ("wheel_member_missing", f"wheels/{CORE}-{VERSION}-py3-none-any.whl!{CORE}/db/schema.sql") in bad, bad
    assert ("package_data_missing", f"{CORE}/pyproject.toml") in bad, bad
    assert {c["id"]: c["status"] for c in rep["checks"]}["wheel_contents"] == "fail"
    assert not list((tree / "dist" / "owner").glob("*.zip"))  # nothing published


def test_package_data_reader_agrees_with_tomli_on_the_real_pyprojects() -> None:
    tomli = pytest.importorskip("tomli")
    seen = 0
    for pkg in (*PUBLIC_PKGS, CORE):
        pp = REPO / pkg / "pyproject.toml"
        if not pp.is_file():
            continue
        seen += 1
        text = pp.read_text(encoding="utf-8")
        expect = tomli.loads(text).get("tool", {}).get("setuptools", {}).get("package-data", {})
        assert PB.declared_package_data(text) == expect, pkg
    if not seen:
        pytest.skip("no package sources in this tree")
    multi = ('[project]\nname = "x"\n\n[tool.setuptools.package-data]\n# comment\n"a.b" = [\n  "x.json",  # c\n'
             '  "y/*.sql",\n]\nc = ["z.txt"]\n\n[tool.other]\nk = 1\n')
    assert PB.declared_package_data(multi) == tomli.loads(multi)["tool"]["setuptools"]["package-data"]
    assert PB.declared_package_data('[project]\nname = "x"\n') == {}
    for bad in ('[tool.setuptools.package-data]\n"a" = "x.json"\n', '[tool.setuptools.package-data]\n"a" = ["x"\n',
                '[tool.setuptools.package-data]\n"a" = [\'x.json\']\n'):
        with pytest.raises(ValueError):
            PB.declared_package_data(bad)


# ------------------------------------------------------------- C10 D5: no dependency on the source placeholder
def test_registration_build_does_not_depend_on_the_file_list_placeholder(tmp_path: Path) -> None:
    if not (REPO / "build" / "allowlists" / "registration.txt").is_file():
        pytest.skip("not a source tree: build/allowlists absent")  # bundles do not ship build/ (same rule as below)
    digests = {}
    for label, placeholder in (("without", None), ("with", "# FILE_LIST (source placeholder)\n생성 대기\n"),
                               ("altered", "# a different placeholder text\n")):
        root = make_tree(tmp_path / label)
        if placeholder is not None:
            (root / "docs" / "registration" / "FILE_LIST.md").write_text(placeholder, encoding="utf-8")
        proc = run_build(root, "--profile", "registration", "--reproducible")
        assert proc.returncode == 0, proc.stdout[-3000:]
        z = zip_of(root, "registration")
        digests[label] = hashlib.sha256(z.read_bytes()).hexdigest()
        shadows = [f for f in report(root, "registration")["findings"] if f["check"] == "generated_shadow"]
        assert [f["path"] for f in shadows] == (["docs/registration/FILE_LIST.md"] if placeholder else [])
        info = json.loads(_read(z, "BUILD_INFO.json"))
        assert not [k for k in info["checks"]["by_check"] if k.endswith(":generated_shadow")]
        assert _read(z, "docs/registration/FILE_LIST.md") != (placeholder or "").encode()
    assert len(set(digests.values())) == 1, digests


# ------------------------------------------------------------------- C11 D7: FILE_LIST regenerated from the ZIP
def test_shipped_registration_file_list_regenerates_identically(built: dict, tmp_path: Path) -> None:
    z = zip_of(built["root"], "registration")
    with zipfile.ZipFile(z) as zf:
        zf.extractall(tmp_path)
    top = tmp_path / "mbnas_application"
    before = (top / "docs" / "registration" / "FILE_LIST.md").read_bytes()
    proc = subprocess.run([sys.executable, "-I", "-B", str(DEV / "gen_registration_docs.py"), "--only", "file-list",
                           "--check", "--root", str(top), "--manifest", str(top / "FILE_MANIFEST.jsonl")],
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0 and "check: OK" in proc.stdout, proc.stdout + proc.stderr
    assert (top / "docs" / "registration" / "FILE_LIST.md").read_bytes() == before  # --check writes nothing
    man = _manifest(z)
    listed = len(man) - 2  # the final manifest also holds FILE_LIST.md and PACKAGE_CONTENTS.md, never listed
    text = before.decode()
    assert f"목록 대상 항목 {listed}개" in text
    assert len([line for line in text.splitlines() if ROW.match(line)]) == listed
    assert "LICENSE_STATUS" not in text  # licence column follows the manifest (C11 D8)


# ------------------------------------------------------------------------------- allowlists of the source tree
def test_real_allowlists_ship_licence_notices_and_contents_in_every_profile() -> None:
    if not (REPO / "build" / "allowlists" / "public.txt").is_file():
        pytest.skip("not a source tree: build/allowlists absent")
    assert (REPO / "LICENSE").is_file()  # the LICENSE* include matches an existing file (DD-A65)
    assert (REPO / "THIRD_PARTY_NOTICES_WEB.md").is_file()  # npm notices (DD-A65); required by every allowlist
    als = {p: PB.load_allowlist(REPO / "build" / "allowlists" / f"{p}.txt", p) for p in PROFILES}
    for profile, al in als.items():
        assert {"LICENSE", "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES_WEB.md"} <= set(al.required), profile
        assert al.included("LICENSE") and al.included("THIRD_PARTY_NOTICES_WEB.md"), profile
        assert al.is_generated("PACKAGE_CONTENTS.md"), profile
        assert al.included("LICENSE_STATUS.md") is (profile == "owner"), profile
        assert al.included("scripts/dev/gen_npm_notices.py") is (profile == "owner"), profile
    member = als["owner"].settings["wheel_listing_member"]
    assert member.startswith("docs/internal/") and als["owner"].is_generated(member)
    assert all("wheel_listing_member" not in als[p].settings for p in ("public", "registration"))

"""T8 canary tools (WP-7D; SECURITY_BASELINE §5): ``canary_scan.py``, ``canary_plant.py`` and the ``--canary`` test
build of ``package_build.py`` (positive control in the owner bundle, leak detection in a public bundle).

Tokens are composed at run time; no literal token appears in this file.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import secrets
import tarfile
import zipfile
from pathlib import Path

import pytest

from pkgtest_support import REPO, load_dev, make_tree, run_build

scan = load_dev("canary_scan")
plant = load_dev("canary_plant")


def _token(area: str) -> str:
    return f"{scan.PREFIX}{area}-{secrets.token_hex(8)}"


def _manifest(path: Path, **areas: tuple[list[str], list[str]]) -> dict:
    doc = {"schema": "mbnas.canary_manifest/1", "areas": [
        {"area": name, "value": _token(name), "expected_in": exp, "must_not_in": mnot, "planted_in": []}
        for name, (exp, mnot) in areas.items()]}
    path.write_text(json.dumps(doc))
    return {a["area"]: a["value"] for a in doc["areas"]}


def _scan(targets: list[Path], manifest: Path | None = None, **kw) -> dict:
    m = scan.load_manifest(manifest) if manifest else None
    needles = scan.build_needles(m, manifest_path=str(manifest) if manifest else None)
    return scan.scan_targets([str(t) for t in targets], needles, **kw)


@pytest.mark.parametrize("shift", [0, 1, 2, 3, 4, 5])
def test_base64_and_hex_forms_are_found_at_every_alignment(tmp_path: Path, shift: int) -> None:
    mpath = tmp_path / "m.json"
    tok = _manifest(mpath, AREA_X=(["owner"], ["public"]))["AREA_X"]
    blob = secrets.token_bytes(shift) + tok.encode() + secrets.token_bytes(7)
    (tmp_path / "b64.txt").write_bytes(base64.b64encode(blob))
    (tmp_path / "b64url.txt").write_bytes(base64.urlsafe_b64encode(blob))
    (tmp_path / "hex.txt").write_bytes(blob.hex().encode())
    (tmp_path / "utf16.bin").write_bytes(b"\x00" * shift + tok.encode("utf-16-le"))
    rep = _scan([tmp_path / n for n in ("b64.txt", "b64url.txt", "hex.txt", "utf16.bin")], mpath)
    found = {(Path(h["location"].split(":")[0].split("@")[0]).name, h["area"]) for h in rep["hits"]}
    assert found == {(n, "AREA_X") for n in ("b64.txt", "b64url.txt", "hex.txt", "utf16.bin")}, rep["hits"]
    assert rep["status"] == "FAIL"


def test_nested_containers_are_opened(tmp_path: Path) -> None:
    mpath = tmp_path / "m.json"
    tok = _manifest(mpath, DEEP=(["owner"], ["public"]))["DEEP"]
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:  # a wheel inside the bundle
        zf.writestr("pkg/mod.py", f"X = '{tok}'\n")
    outer = tmp_path / "bundle.zip"
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mbnas_application/wheels/pkg-0.1.0-py3-none-any.whl", inner.getvalue())
        zf.writestr("mbnas_application/README.md", "clean\n")
    (tmp_path / "log.gz").write_bytes(gzip.compress(f"line\n{tok}\n".encode()))
    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w") as tf:
        data = f"tar {tok}".encode()
        ti = tarfile.TarInfo("a/b.txt")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    (tmp_path / "x.tar").write_bytes(tbuf.getvalue())
    rep = _scan([outer, tmp_path / "log.gz", tmp_path / "x.tar"], mpath)
    locs = sorted(h["location"] for h in rep["hits"])
    assert any("bundle.zip!mbnas_application/wheels/pkg-0.1.0-py3-none-any.whl!pkg/mod.py:1" in x for x in locs), locs
    assert any("log.gz!gunzip:2" in x for x in locs) and any("x.tar!a/b.txt:1" in x for x in locs), locs
    assert rep["scanned"]["containers"] >= 4


def test_generic_pattern_needs_no_manifest_and_main_exit_codes(tmp_path: Path, capsys) -> None:
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "a.txt").write_text("nothing to see\n")
    assert scan.main([str(clean), "--no-host-facts"]) == 0
    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "resp.json").write_text(json.dumps({"x": _token("UNKNOWN")}))
    assert scan.main([str(dirty)]) == 1
    out = capsys.readouterr().out
    assert "FAIL pattern" in out and "resp.json:1" in out and "canary_scan: FAIL" in out
    assert scan.main([str(tmp_path / "missing")]) == 2


def test_profile_judgement_and_positive_control(tmp_path: Path) -> None:
    mpath = tmp_path / "m.json"
    toks = _manifest(mpath, POLICY=(["owner"], ["public", "registration"]), SELF=([], ["owner", "public"]))
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "core.py").write_text(f"P = '{toks['POLICY']}'\n")
    assert _scan([owner], mpath, profile="owner", expect_present=True)["status"] == "PASS"
    assert _scan([owner], mpath, profile="public")["status"] == "FAIL"
    (owner / "leak.md").write_text(toks["SELF"])  # the manifest's own marker is never allowed
    rep = _scan([owner], mpath, profile="owner", expect_present=True)
    assert rep["status"] == "FAIL" and rep["failing_hits"] == 1
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "x.txt").write_text("x")
    rep = _scan([empty], mpath, profile="owner", expect_present=True)
    assert rep["status"] == "FAIL" and rep["unmet_expectations"] == 1


def test_host_facts_fail_except_in_the_owner_bundle(tmp_path: Path) -> None:
    mpath = tmp_path / "m.json"
    _manifest(mpath, A=(["owner"], ["public"]))
    d = tmp_path / "d"
    d.mkdir()
    (d / "doc.md").write_text("see " + scan.host_facts()[0].decode() + "/runs\n")
    assert _scan([d], mpath, profile="public")["status"] == "FAIL"
    assert _scan([d], mpath, profile="owner")["status"] == "PASS"
    assert _scan([d])["status"] == "FAIL"
    assert scan.main([str(d), "--no-host-facts"]) == 0


def test_secret_needles_are_reported_by_label_only(tmp_path: Path, capsys) -> None:
    value = "v" + secrets.token_urlsafe(18)
    env = tmp_path / "real.env"
    env.write_text(f"MBNAS_GW_TOKEN={value}\nMBNAS_GW_PORT=8710\n")
    d = tmp_path / "d"
    d.mkdir()
    (d / "log.txt").write_text(f"auth {value}\n")
    assert scan.main([str(d), "--secrets-env", str(env), "--no-host-facts", "--json", str(tmp_path / "r.json")]) == 1
    out = capsys.readouterr().out
    assert "SECRET:MBNAS_GW_TOKEN" in out and value not in out
    assert value not in (tmp_path / "r.json").read_text()


def _need_allowlists() -> None:
    if not list((REPO / "build" / "allowlists").glob("*.txt")):
        pytest.skip("not a source tree: build/allowlists absent")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_plant_refuses_unmarked_trees_and_the_project_root(tmp_path: Path) -> None:
    _need_allowlists()
    src = make_tree(tmp_path / "src")
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(plant.Refused):
        plant.plant(other, tmp_path / "m.json", source=src)
    with pytest.raises(plant.Refused):
        plant.make_tree(other, source=src)  # exists, not a canary tree: never deleted
    with pytest.raises(plant.Refused):
        plant.make_tree(plant.PROJECT_ROOT, source=src)
    with pytest.raises(plant.Refused):
        plant.make_tree(tmp_path, source=src)  # an ancestor of the source


def test_fresh_tree_plant_is_idempotent_rotatable_and_strippable(tmp_path: Path) -> None:
    _need_allowlists()
    src = make_tree(tmp_path / "src")
    tree, mpath = tmp_path / "canary_tree", tmp_path / "manifest" / "CANARY_MANIFEST.json"
    plant.make_tree(tree, source=src)
    assert (tree / plant.MARK).is_file() and (tree / "build" / "allowlists" / "public.txt").is_file()
    for absent in ("runtime", ".env", "mbnas_web/node_modules", "build/junk.txt", "docs/internal/canary"):
        assert not (tree / absent).exists(), absent
    originals = {rel: _sha(tree / rel) for _, rel, *_ in plant.PLANTS if (tree / rel).is_file()}
    doc1 = plant.plant(tree, mpath, source=src)
    planted = {p["path"] for a in doc1["areas"] for p in a["planted_in"] if p.get("status") == "planted"}
    snap1 = {rel: (tree / rel).read_bytes() for rel in planted}
    for a in doc1["areas"]:
        for p in a["planted_in"]:
            if p.get("status") == "planted" and p["kind"] != "manifest":
                assert (tree / p["path"]).read_text().count(a["value"]) >= 1
    doc2 = plant.plant(tree, mpath, source=src)  # idempotent: same tokens, same bytes
    assert [a["value"] for a in doc2["areas"]] == [a["value"] for a in doc1["areas"]]
    assert {rel: (tree / rel).read_bytes() for rel in planted} == snap1
    doc3 = plant.plant(tree, mpath, rotate=True, source=src)
    assert all(x["value"] != y["value"] for x, y in zip(doc1["areas"], doc3["areas"]))
    for a in doc3["areas"]:
        for p in a["planted_in"]:
            if p.get("status") == "planted" and p["kind"] != "manifest":
                text = (tree / p["path"]).read_text()
                assert text.count(">>> MBNAS canary") == 1 and a["value"] in text  # replaced, not appended
    assert plant.strip(tree, mpath, source=src) == []
    assert {rel: _sha(tree / rel) for rel in originals} == originals
    created = [p["path"] for a in doc3["areas"] for p in a["planted_in"] if p.get("created")]
    assert created and not any((tree / c).exists() for c in created)
    assert _scan([tree])["hits"] == [] or all(h["kind"] == "host_fact" for h in _scan([tree])["hits"])


# areas planted in files that the owner bundle ships (positive control) and in files that no bundle ships (clean
# distribution, DD-A76: tests and internal documents stay in the repository)
OWNER_SHIPPED_AREAS = {"POLICY_PRIOR", "FITNESS", "OWNER_SECRET", "OWNER_DEPLOY", "CORE_STATE"}
NEVER_SHIPPED_AREAS = {"OWNER_TEST", "OWNER_DOC"}


@pytest.mark.xfail(strict=True, reason="scripts/dev/canary_plant.py still records OWNER_TEST / OWNER_DOC as expected "
                   "in the owner bundle (expected_in=['owner']); the clean distribution ships neither, so the owner "
                   "positive control reports them unmet until canary_plant.py moves them to must_not_in")
def test_canary_test_build_owner_positive_control(tree: Path) -> None:
    owner = run_build(tree, "--profile", "owner", "--canary")
    reports = sorted((tree / "build" / "canary" / "owner" / "tree" / "dist" / "owner").glob("*.canary.json"))
    assert reports, owner.stdout[-4000:] + owner.stderr[-2000:]
    rep = json.loads(reports[0].read_text())
    found = {e["area"]: e["ok"] for e in rep["expectations"]}
    assert OWNER_SHIPPED_AREAS <= set(found) and all(found[a] for a in OWNER_SHIPPED_AREAS), rep["expectations"]
    assert not [h for h in rep["hits"] if h.get("area") in NEVER_SHIPPED_AREAS]  # not in the owner ZIP either
    assert not list((tree / "dist").glob("*/*.zip"))  # a canary build never publishes into dist/
    assert not NEVER_SHIPPED_AREAS & set(found), rep["expectations"]  # not expected in any bundle any more
    assert owner.returncode == 0 and rep["status"] == "PASS", owner.stdout[-4000:]


def test_canary_test_build_public_leak_detection(tree: Path) -> None:
    public = run_build(tree, "--profile", "public", "--canary")
    assert public.returncode == 0, public.stdout[-4000:]
    pdir = tree / "build" / "canary" / "public" / "tree" / "dist" / "public"
    prep = json.loads(sorted(pdir.glob("*.canary.json"))[0].read_text())
    assert prep["status"] == "PASS" and prep["hits"] == []
    assert not list((tree / "dist").glob("*/*.zip"))  # a canary build never publishes into dist/
    # a broken public allowlist that lets owner tests through is caught by the planted OWNER_TEST marker
    al = tree / "build" / "allowlists" / "public.txt"
    lines = al.read_text().splitlines()
    assert "tests/** :: test suites are not shipped in this distribution" in lines
    text = "\n".join(line for line in lines if not line.startswith("tests/")) + "\n"  # drops the tests/** deny
    al.write_text(text.replace("[include]\n", "[include]\ntests/owner/**\n", 1))
    leak = run_build(tree, "--profile", "public", "--canary")
    assert leak.returncode == 1
    assert "canary_token" in leak.stdout and "OWNER_TEST" in leak.stdout

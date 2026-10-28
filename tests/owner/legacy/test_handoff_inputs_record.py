"""QA, criterion set 1 (docs evidence): the handoff-input record, the missing-source record, the read-only GitHub
comparison and the live evaluation summary agree with the real host (findings S1-F1, S1-F2, S1-F4, S1-F5).

The record is ``docs/ACCEPTANCE_RESULTS.md`` §8. These tests read /root/detection (read-only); they never contact the
network. ``docs/orchestration/*`` is commander-owned: it is read (STATE.json) but not asserted on.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.legacy_records  # DD-A22: record test of an earlier integration round; excluded by default

ROOT = Path(os.environ.get("MBNAS_TEST_ROOT", "/root/detection"))  # 2026-10-15: injectable (MBNAS_TEST_ROOT / _ETRI_ROOT)
# 2026-10-15: the projects and the handoff moved into ETRI_NAS/application; "MBNAS_server/..." record paths are
# relative to APP
ETRI = ROOT / "ETRI_NAS"  # WP-1: MBNAS_TEST_ETRI_ROOT removed (contract §9); legacy record of the old host layout
APP = ETRI / "application"
SERVER, WEB = APP / "MBNAS_server", APP / "MBNAS_web"
HANDOFF = APP / "MBNAS_Server_Handoff_20261006"
ACCEPTANCE = SERVER / "docs" / "ACCEPTANCE_RESULTS.md"
SUMMARY = SERVER / "docs" / "evaluation" / "EVALUATION_SUMMARY.md"
EVAL_LOG = SERVER / "docs" / "evaluation" / "evaluation_log.jsonl"
STATE = SERVER / "docs" / "orchestration" / "STATE.json"
SITES_URL = "https://mbnas-research-console.hanjan-lab.chatgpt.site"
SITES_COMMIT = "37e23a42ff48fe0c7ab2870b71c2059e5415bd01"
# the six inputs master prompt §3 says to read (references/ holds the DOCX and the JSON)
REQUIRED_INPUTS = ("NAS_Console_Server_Implementation_Prompt_20261006.md", "NAS_Console_API_Contract_20261006.json",
                   "MBNAS_Protocol_20261006.json", "ETRI_NAS_Reuse_Audit_20261006.md", "MBNAS_Handoff_README.md",
                   "references/MBNAS_Training_Promotion_WebUI_Design_20261006(1).docx",
                   "references/MBNAS_Training_Policy_Interface_Example_20261006(1).json")
PAST_RESULTS = ("MBNAS_Review_Criteria.md", "MBNAS_Test_Results.txt", "MBNAS_Independent_Event_Checks.md")
CODE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".zip", ".whl", ".tar", ".gz"}


def _section(title: str) -> str:
    """Body of the ACCEPTANCE_RESULTS heading that starts with ``title`` (up to the next heading of the same level)."""
    text = ACCEPTANCE.read_text()
    m = re.search(rf"^(#+) {re.escape(title)}.*$", text, re.M)
    assert m, f"{ACCEPTANCE} has no heading starting with {title!r}"
    rest = text[m.end():]
    end = re.search(rf"^#{{1,{len(m.group(1))}}} ", rest, re.M)
    return rest[: end.start()] if end else rest


def _handoff_files() -> list[Path]:
    if not HANDOFF.is_dir():
        pytest.skip("handoff directory not present on this host")
    return sorted(p for p in HANDOFF.rglob("*") if p.is_file())


def _row(section: str, name: str) -> str:
    rows = [line for line in section.splitlines()
            if line.startswith("|") and line.strip().strip("|").split("|")[0].strip() == f"`{name}`"]
    assert len(rows) == 1, f"expected exactly one table row whose first cell is `{name}`, found {len(rows)}"
    return rows[0]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- S1-F1: missing sources and how they were handled ------------------------------------------------------------
def test_handoff_really_lacks_the_sites_build_and_any_server_source():
    files = _handoff_files()
    assert not [p for p in HANDOFF.rglob("*") if p.is_dir() and p.name == "dist"]
    assert not [p for p in files if p.name in ("core.js", "app.js", "index.html", "package.json", "pyproject.toml")]
    assert not [p for p in files if p.suffix.lower() in CODE_SUFFIXES], "handoff unexpectedly contains code/archives"


def test_missing_source_record_names_each_gap_and_its_replacement():
    s = _section("8.1 Handoff inputs and missing sources")
    for needle in ("dist/core.js", "dist/app.js", "server source", SITES_URL, SITES_COMMIT, "visual reference", "login",
                   "MBNAS_server/contracts/openapi.json", "§5", "rfdetr.py", "mixed_block_nas",
                   "MBNAS_server/src/mbnas_server/adapters/rfdetr_adapter.py",
                   "MBNAS_server/src/mbnas_server/search/etri_bridge.py"):
        assert needle in s, f"missing-source record does not mention {needle!r}"
    for rel in ("MBNAS_server/src/mbnas_server/adapters/rfdetr_adapter.py", "MBNAS_server/src/mbnas_server/search/etri_bridge.py"):
        assert (APP / rel).is_file(), rel


def test_no_web_or_server_source_depends_on_the_sites_page():
    """The record says the Sites page is a visual reference only; nothing executable may point at it."""
    roots = [WEB / "src", WEB / "e2e", WEB / "scripts", SERVER / "src", SERVER / "scripts", SERVER / "config", SERVER / "deploy"]
    files = [p for r in roots for p in r.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    files += [WEB / n for n in ("package.json", "index.html", "vite.config.ts", "playwright.config.ts", "playwright.real.config.ts")]
    hits = [str(p) for p in files if "chatgpt.site" in p.read_text(errors="ignore") or "dist/core.js" in p.read_text(errors="ignore")]
    assert not hits, hits


# -- S1-F2: every handoff input with its hash, how it was read, what it contributed, where it is reflected --------
def test_every_handoff_file_is_listed_with_its_real_sha256():
    s = _section("8.1 Handoff inputs and missing sources")
    for p in _handoff_files():
        row = _row(s, p.relative_to(HANDOFF).as_posix())
        assert _sha256(p) in row, f"{p.name}: recorded sha256 differs from the file"


def test_required_inputs_record_how_read_what_was_taken_and_where_it_is_reflected():
    s = _section("8.1 Handoff inputs and missing sources")
    _handoff_files()
    for name in REQUIRED_INPUTS:
        assert (HANDOFF / name).is_file(), name
        cells = [c.strip() for c in _row(s, name).strip().strip("|").split("|")]
        assert len(cells) == 6 and all(cells), f"{name}: expected 6 filled cells, got {cells}"
        reflected = re.findall(r"`(MBNAS_(?:server|web)/[^`:]+)", cells[4])
        assert reflected, f"{name}: 'reflected in' names no project path"
        for rel in reflected:
            assert (APP / rel).exists(), f"{name}: reflected path {rel} does not exist"
    docx = _row(s, "references/MBNAS_Training_Promotion_WebUI_Design_20261006(1).docx")
    assert "zipfile" in docx and "not executed" in docx


def test_bundled_protocol_is_byte_identical_as_recorded():
    _handoff_files()
    bundled = SERVER / "src" / "mbnas_core" / "protocol" / "handoff_protocol_20261006.json"
    assert _sha256(bundled) == _sha256(HANDOFF / "MBNAS_Protocol_20261006.json")
    assert "byte-identical" in _row(_section("8.1 Handoff inputs and missing sources"), "MBNAS_Protocol_20261006.json")


def test_past_results_are_marked_as_not_evidence():
    s = _section("8.1 Handoff inputs and missing sources")
    _handoff_files()
    for name in PAST_RESULTS:
        assert "not evidence" in _row(s, name), name


# -- S1-F4: read-only GitHub comparison ----------------------------------------------------------------------------
def test_read_only_github_comparison_is_recorded_against_the_real_local_head():
    s = _section("8.2 ETRI_NAS read-only GitHub comparison")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ETRI, capture_output=True, text=True, check=True).stdout.strip()
    # 2026-10-15: ETRI_NAS HEAD also moves through the user's own commits and pulls (outside this work). The record is a
    # point-in-time measurement, so a HEAD that is not named in it is a documented skip -- but only when the newest HEAD
    # the record does name is an ancestor of the current one (history moved forward). An unrelated HEAD still fails.
    # The skip is raised after every other assertion below, which therefore always run.
    head_drift = None
    if head not in s:
        recorded = [h for h in dict.fromkeys(re.findall(r"\b[0-9a-f]{40}\b", s))
                    if subprocess.run(["git", "cat-file", "-e", h + "^{commit}"], cwd=ETRI, capture_output=True).returncode == 0]
        forward = [h for h in recorded if subprocess.run(["git", "merge-base", "--is-ancestor", h, head], cwd=ETRI,
                                                         capture_output=True).returncode == 0]
        assert forward, f"recorded local HEAD differs from ETRI_NAS HEAD {head} and no recorded commit is its ancestor"
        head_drift = (f"ETRI_NAS HEAD {head[:7]} is not named in ACCEPTANCE_RESULTS 8.2 (record is point-in-time; "
                      f"newest recorded ancestor {forward[-1][:7]}); record the move or re-run the comparison")
    remote = re.search(r"remote `main`[^`]*`([0-9a-f]{40})`", s)
    assert remote, "no 40-hex remote main SHA recorded"
    assert re.search(r"20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", s), "no commit date recorded"
    for needle in ("git ls-remote", "--filter=blob:none", "scratch", "yolo_ga_runtime", "yolo_ga_full_runtime",
                   "drone_bench", "notebooks", "never fetched into"):
        assert needle in s, needle
    assert re.search(r"\b0 changed files under the reuse paths", s)
    assert re.search(r"\b21 of 21\b", s), "blob comparison of the 21 audited reuse files not recorded"
    if head_drift:
        pytest.skip(head_drift)


# -- S1-F5: the live summary does not contradict STATE.json ------------------------------------------------------
def _done_milestones() -> set[int]:
    milestones = json.loads(STATE.read_text())["milestones"]
    return {int(k[1:].split("_")[0]) for k, v in milestones.items() if str(v).startswith("DONE")}


def test_evaluation_summary_does_not_call_done_milestones_pending():
    text, done = SUMMARY.read_text(), _done_milestones()
    if done - {1}:
        assert "not yet started" not in text, "summary says implementation not started; STATE.json reports milestones DONE"
    for a, b in re.findall(r"M(\d+)\s*[–-]\s*M(\d+)\s+pending", text):
        assert not set(range(int(a), int(b) + 1)) & done, f"M{a}–M{b} called pending, STATE.json reports DONE"
    for m in re.findall(r"M(\d+)\s+pending", text):
        assert int(m) not in done, f"M{m} called pending, STATE.json reports DONE"


def test_evaluation_summary_matches_an_empty_log():
    if not EVAL_LOG.exists():  # 2026-10-15: *.jsonl is git-ignored (.gitignore:197); a fresh checkout has no log at all
        pytest.skip("evaluation_log.jsonl is not present in this checkout (git-ignored *.jsonl)")
    rows = [line for line in EVAL_LOG.read_text().splitlines() if line.strip()]
    if rows:
        pytest.skip("evaluation_log.jsonl has rows; the commander's write_eval_log.py owns the table")
    text = SUMMARY.read_text()
    # S2-F5 (set 2, iteration 2): a set the reviewers already scored must not be shown as NOT_RUN. While the log is
    # empty, such a row is marked "(not yet logged)" and backed by the dated interim section, whose numbers
    # tests/qa/test_evaluation_summary_record.py checks against the workflow journal. Nothing may look logged.
    head = text.split("\n## ", 1)[0]  # the live table; the interim section follows it
    table = [line for line in head.splitlines() if re.match(r"^\| \d+ \|", line)]
    assert len(table) == 12 and all("| NOT_RUN |" in line or "(not yet logged)" in line for line in table)
    assert "Passed sets: 0 / 12" in text or "0 / 12 logged" in text
    if any("(not yet logged)" in line for line in table):
        assert "\n## Reviews completed but not yet logged (as of " in text

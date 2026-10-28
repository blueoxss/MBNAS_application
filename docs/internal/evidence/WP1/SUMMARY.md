# WP-1V verification round 1 — SUMMARY (2026-10-26)

Interpreter: `runtime/venv/bin/python` (3.10.22), always `-B`, `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`,
`MBNAS_HOME=runtime`, `MBNAS_TEST_ROOT=runtime/test_root`. cwd = TARGET_ROOT unless noted. No GPU, no ports 8000-8002/5173-5175,
nothing written under etri_nas or build/source_snapshot (0 .pyc there; project __pycache__ removed before evidence).

| §13 | command | time | exit | duration | result | file |
|---|---|---|---|---|---|---|
| 1 | `pip install --no-index --find-links build/wheelhouse -c constraints.txt --no-deps -e <pkg>` ×5; import; 4 console `--help`; `pip check` | 18:44 | 0 all | ~20 s | OK | install.log |
| 2 | `scripts/dev/check_import_closure.py --format text` | 18:45 | 0 | <5 s | 0 violations / 0 unresolved / 0 forbidden; 7 allowlisted (3 transitional edges, expire WP-2), 2 excepted (DD-A26, expire WP-4) | import_closure.txt, import_closure.json |
| 2 | forbidden-string grep + `tests/public/infra/test_no_host_facts.py` | 18:47 | 0 | 1 s | package src: only DD-A26 openapi.json hit; 8 passed | host_facts.txt |
| 3 | `pytest -p no:cacheprovider --timeout=600 -rfEsx -m "not gpu and not live" tests/public` | 18:45 | **1** | 79 s | 1 failed, 563 passed, 31 skipped, 12 deselected, 1 xfailed (strict, DD-A29) | pytest_public.txt |
| 4 | `pytest ... -m "not gpu and not live and not legacy_records and not host_data" tests/owner` (nohup) | 18:45–19:21 | 0 | 2140 s | **1116 passed, 1 skipped, 47 deselected, 0 failed, 0 error** = T1 baseline | pytest_owner_gate.txt |
| 3/4 | skip reasons | — | — | — | all host-data / real-run absence | pytest_skips.txt |
| 5 | repair_evidence_seed (import lines only differ from snapshot) + operators parity + parse_model gate | 18:48 | 0 | 18 s | 17 passed | parity.txt |
| 5 | `SearchSpace.create(nc=1, imgsz=640)` (2 runs, identical) | 18:49 | 0 | — | /3 space_hash f50d4904…, prior_hash 0fbc5413… | hash_map.txt, ../../provenance/MIXED_SPACE_HASH_MAP.json |
| 6 | `npm run check:types`, `npm run typecheck`, `npx vitest run` (cwd mbnas_web) | 18:50 | 0/0/0 | 1/8/12 s | vitest 35 files, 488 passed (after DD-A28 console.paths exclusion) | web_checks.txt |
| 7 | etri_nas unchanged — designer's check (not run here) | | | | 확인 못 함 | |

## Remaining defect (not hidden)
- `tests/public/infra/test_import_safety.py::test_no_module_runs_code_at_import_time[mbnas_model_runtime]`: `block_catalog.py` builds its
  table with 78 top-level `_ul/_add/_torch(...)` calls. Pre-existing: the snapshot `search/block_catalog.py` (delta 1.4.2) has 96 such
  calls and fails the same original rule. Needs designer decision (wrap table construction in WP-1C vs documented exception). Not weakened.

## Changes in this round
checker (DD-A26/A27), `scripts/dev/source_tree_hash.py` (package_roots CLI), `scripts/runner/{bench_host_gpu,resume_equivalence}.py`
(package_roots, default_rf_home), `tests/public/tools/api_bearer_only.py` (MBNAS_GW_*, sys.executable), `mbnas_server/pyproject.toml`
(extras gpu), `mbnas_web/tests/preset_parity.test.ts` (console.paths excluded). Manifest: 7 WP-1V rows appended (446 rows, 0 stale).
TEST_ADAPTATIONS.md §6 added.

## Round 2 (2026-10-26 19:23 KST)
No project source file changed after round 1 (find -newermt "18:44" only lists round-1 edits + evidence). No designer decision on
block_catalog exists in DECISIONS.md / WP-1_RESOLUTIONS.md, so no source edit was made. Re-ran (same interpreter/env):
- `check_import_closure.py --format text` exit 0: 0 violations / 0 unresolved / 0 forbidden / 0 errors.
- public suite (same command) exit 1, 76 s: 1 failed, 563 passed, 31 skipped, 12 deselected, 1 xfailed — same single failure
  (block_catalog top-level calls, pending designer decision).
- Owner gate not re-run: code identical to the 18:45–19:21 run (0 failed / 1116 passed). Web, parity and hash map also unchanged since round 1.

## Round 3 (2026-10-26 19:27:09)
No source change and no designer decision on block_catalog since round 2. Re-ran: import checker exit see closure.txt; public suite: 1 failed, 563 passed, 31 skipped, 12 deselected, 1 xfailed, 2 warnings in 72.68s (0:01:12). Logs: /tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/v3

## Round 4 (2026-10-26T19:29:31+09:00)
No source or decision change since round 3 (DECISIONS.md / WP-1_RESOLUTIONS.md last modified 10-26 17:48; block_catalog.py 10-23; no .py newer than SUMMARY.md). cwd TARGET_ROOT, interpreter runtime/venv/bin/python, CUDA_VISIBLE_DEVICES="".
- check_import_closure.py (-I -B): exit 0
- pytest -q -p no:cacheprovider --timeout=600 -m "not gpu and not live" tests/public: exit 1; 1 failed, 563 passed, 31 skipped, 12 deselected, 1 xfailed (71 s). Failure: test_import_safety[mbnas_model_runtime] (block_catalog top-level calls, waiting on a designer decision).
- Owner gate, parity and web checks were not re-run because the code is unchanged since round 1.

## Designer round (2026-10-26 20:0x, claude-fable-5-1)
- Independent re-run: `check_import_closure.py` exit 0 (0 violations/0 unresolved/0 forbidden; 7 allowlisted transitional, 2 DD-A26 data exceptions); public suite before fix: 1 failed/563 passed/31 skipped/12 deselected/1 xfailed (same single failure).
- Decision on the last defect: `block_catalog.py` table construction wrapped in `_build_tables()` (module constants `_LW`, `_E_MODEL` hoisted; tables verified identical to the previous module object by object). Not a documented exception: the import-safety rule stays strict.
- After fix: `tests/public/infra/test_import_safety.py` + block_catalog owner tests (53 passed); full public suite re-run → see `pytest_public_designer_rerun.txt`.
- Original `etri_nas` check (manifest sha compare, 10-26 19:5x): 40 files differ, all consistent with the concurrent session's Phase G work (contract 1.4.3, structure.py fix, web components, docs); snapshot 597 files unmodified; 0 `__pycache__` under build/source_snapshot.
- Accepted designer items: DD-A16 test expectation change (A2, 3 tests) approved; AGPL licence metadata for the parse_model copy → WP-7 NOTICE; retention CLI env rename (MBNAS_CORE_*) is DD-A13, approved.

## Note on pytest_owner_gate_after_fix.txt (designer, 2026-10-26 22:1x)
This confirmation run (19:3x–20:13) overlapped the WP-2 agents' edits (e.g. db/schema.sql v4 at 20:03), so its 4 failures reflect a tree in transition, not the accepted WP-1 or WP-2 state. The authoritative post-change gate is docs/internal/evidence/WP2/2V/owner_gate.txt (1158 passed, 0 failed, full command, exit 0).

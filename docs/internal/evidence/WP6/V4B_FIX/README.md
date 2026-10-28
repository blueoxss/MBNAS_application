# V4-B fix: checkpoint download + axe nested-interactive (DD-A55), 2026-10-27 18:2x–18:46 UTC

Result: **the four V4-B failures pass on the real CPU stack** (V4-02, V4-06, V4-10, V4-11; all 12 V4 steps pass, 0 failed).
axe serious/critical = 0 (and 0 violations of any impact) on all six audited screens: server, settings,
structure-check dialog, monitor, candidate dialog, lineage. The 1.5.0 contract is unchanged (openapi.json sha256
70e0cffa…eebc2a; `check:types` = "types.gen.ts matches the contract").

## Fixes
1. Core, `CandidateDetail.checkpoints[].artifact_id`. `orchestration/retention.py` adds `store_verified_checkpoints(conn, run_id)`.
   It returns the checkpoint ids whose latest `server_store` location for the recorded digest is PRESENT. This is the READY
   rule of `artifacts/locations.checkpoint_record`, evaluated in one query. `checkpoint_views(rows, located, stored=…)`
   then sets `artifact_id` to the checkpoint id (the id the gateway serves at `GET /api/v1/runs/{run}/artifacts/{ck-id}`)
   for those rows and to `null` for all others. `console/routes_runs.py` (candidate detail) passes `stored=`. Without
   `stored`, the views have no `artifact_id` member, so the export bundle (`export/bundle.py`) is unchanged.
2. Web, `CheckpointTable` (`mbnas_web/src/components/CandidateView.tsx`). The new `checkpointDownloadId()` downloads by
   `artifact_id` when it is set. Otherwise it downloads by the checkpoint id when the state is PRESENT or READY (DD-A51 c).
   "저장소 미등록" appears only when neither applies. 404 `ARTIFACT_NOT_READY` reads "아직 저장소에 없음 (NOT_READY) ·
   ARTIFACT_NOT_READY" (existing ArtifactDownload mapping, now tested on checkpoints).
3. Web, `ModelStructure.tsx:175`. The structure graph `svg` is now `role="group"` with an aria-label instead of `role="img"`,
   whose children are presentational. The gene nodes remain `role="button"` with `tabIndex=0` and Enter/Space selection.
4. Found by the new mock e2e at 375 px, so outside V4-B, which ran desktop only: axe serious `scrollable-region-focusable`
   on the four scrolling `.table-wrap` blocks of `ModelStructure.tsx` (structure checks, repeats, inputs, internal layers).
   They are now `role="region"` with an aria-label and `tabIndex=0`, the same pattern as DataTable.

## Real stack (`stack_log.txt`, `e2e_real_v4.txt`, `e2e_steps.jsonl`, `v4_state.json`, `screenshots*/`)
- `runtime/venv/bin/python -B scripts/dev/web_real_stack.py --evidence docs/internal/evidence/WP6/V4B_FIX/stack_log.txt`
  was run with `CUDA_VISIBLE_DEVICES=""`. The gateway served `dist/assets/index-BnpZcnwU.js` (`same_bytes: true`). Stop
  check: `still_alive: []`.
- E2E: `scratchpad/v4b/run_e2e.sh` (the V4-B command) was run with `MBNAS_E2E_EVIDENCE_DIR=../docs/internal/evidence/WP6/V4B_FIX/screenshots`.
  First `-g "V4-(0|1[01])"` (9 tests, 9 passed), then `-g "V4-10"` again after store verification (1 passed, screenshots in
  `screenshots_rerun/`), then `-g "V4-12"` (1 passed).
- V4-10, first run: the dialog opened before the gateway had verified anything, so all 3 checkpoints were PRESENT with
  `artifact_id` null. The UI offered "id로 다운로드" by checkpoint id (`artifact_id 미기재 · checkpoint id로 요청`). The saved
  31,600,161-byte file has sha256 9a583668… = recorded.
- After V4-10's gateway-level fetches, `real_stack_artifact_id.json` shows the core fix: `artifact_id == id` for all 3
  checkpoints, and a download by that `artifact_id` returns 200 with body sha = `X-Artifact-Sha256` = recorded. In the V4-10
  re-run the UI shows the `ck-…` id in the artifact_id column (`screenshots_rerun/desktop-10-checkpoint-download.png`).
- axe: `e2e_steps.jsonl` `axe:*` lines are all `serious_critical: 0, all_violations: 0`.

## Gates and tests
- `owner_tests.txt`: `tests/owner/artifacts` 15 passed (12 existing + 3 new in `test_checkpoint_artifact_id.py`);
  `tests/owner/api` 201 passed; other consumers of the checkpoint views (`tests/owner/orchestration/test_retention.py`,
  `tests/owner/projection`, `tests/owner/qa/test_smoke_script_final_phases.py`) 275 passed; `tests/owner/qa/test_api_matrix.py`,
  `tests/owner/t2`, `tests/owner/unit/test_db.py` 62 passed, 7 skipped (T2 heavy scenarios need `MBNAS_TEST_T2=1`, not run).
- `web_gates.txt`: check:types 0, typecheck 0, vitest 591/591 (585 + 6 new), build 0, check:bundle 0, final dist hashes.
- `mock_e2e_full.txt`: full mock suite, 208/208 passed (desktop-1280 + mobile-375), including the new
  `e2e/checkpoint_structure.spec.ts`. Its screenshots are in `screenshots_mock/`.
- Mutation checks: the new tests fail on the pre-fix code. `mutation_check_core.txt`: 2 route tests fail when the route is
  patched back to no store lookup. `mutation_checks_web.txt`: vitest fails 5 of 6 on the old components; the mock e2e
  reports `serious nested-interactive: svg[role="img"]` with the old ModelStructure and passes with the new one.
- `source_changes.diff`: the code and test changes of this fix.

## Limits
- A detail fetched before the gateway has downloaded (verified) a checkpoint still has `artifact_id` null. The gateway
  reports the store location only when it verifies on download, so the web fallback to the checkpoint id is what makes
  the first download possible. This is by design (DD-A51 c) and was observed in V4-10's first run.
- The export bundle's `checkpoints.json` does not carry `artifact_id`, because `export/bundle.py` is not part of this fix.
  It would need `stored=store_verified_checkpoints(conn, run_id)` if wanted.
- Mobile dialog screenshots capture the viewport only, as in V4-B.

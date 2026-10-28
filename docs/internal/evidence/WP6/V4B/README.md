# V4-B: web console against a real CPU stack (WP-6 §2, DD-A50), 2026-10-27 18:02–18:17 UTC

Result: **9 of 12 functional steps PASS in the UI. 1 functional defect (checkpoint download is not offered in the UI). 1 accessibility defect (axe serious `nested-interactive`, shown on 3 screens).** The Playwright run exits 1 with 4 tests failed; 3 of those 4 fail only on the axe assertion (soft).

## Stack and commands
- Stack: `runtime/venv/bin/python -B scripts/dev/web_real_stack.py --evidence docs/internal/evidence/WP6/V4B/stack_log.txt`, run with `CUDA_VISIBLE_DEVICES=""`. It starts core, the owner worker, a CPU runner (local transfer into the gateway store) and a gateway that serves the built `mbnas_web/dist`. Every process was stopped at the end and the exit check is recorded (`stack_log.txt`, step `stop-own-processes`).
- E2E: `npx playwright test -c playwright.real.config.ts -g "V4-"` with `MBNAS_E2E_REAL=1 MBNAS_E2E_V4=1`, plus the URL, bearer, dataset, output root and stack pid that the stack printed, and `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64`. The full log is in `e2e_real_v4.txt` (bearer redacted). Per-step facts are in `e2e_steps.jsonl` and the run ids in `v4_state.json`.
- Web gates were run before the E2E: `web_gates.txt` (check:types 0, typecheck 0, vitest 585/585, build 0, check:bundle 0). The contract had not changed, so `gen:types` was not needed.

## Steps
| # | Step | Result | Evidence |
|---|---|---|---|
| 01 | Connect with the bearer; DEFAULT_BASELINE14 auto-applied | PASS | screenshots 01-* |
| 02 | Upload `yolo11n_user.yaml`. upload_id and sha256 match in the browser; structure check PASS | PASS (axe FAIL) | 02-* |
| 03 | Upload `yolo11n_user_broken.yaml`. Structure check FAIL with SHAPE_MISMATCH and MERGE_INPUT_MISMATCH shown | PASS | 03-* |
| 04 | CPU explicit training from the upload (PILOT_SMOKE recipe, 1 epoch, 64 px, batch 2, SGD, validation off) | PASS (train-99237537f83b) | 04-* |
| 05 | Monitor until finished | PASS (COMPLETED in about 8 s) | 05-* |
| 06 | Candidate detail: gene diagram (24 nodes), block table (24 rows), configuration YAML | PASS (axe FAIL) | 06-* |
| 07 | Revisions tab: original, selected and executed rows; original = the uploaded sha | PASS | 07-* |
| 08 | Diff selected↔executed; both sides re-hashed and matched | PASS | 08-* |
| 09 | Download the exact bytes of all 3 revisions; sha256 of each saved file = its source_sha256 | PASS | 09-* |
| 10 | Download a runner checkpoint by id through the gateway **in the UI** | **FAIL (defect)** | 10-checkpoint-table |
| 11 | OFF generation run: UNTRAINED · UNEVALUATED in GeneratedTable (6/6) and Lineage (6/6) | PASS (axe FAIL) | 11-* |
| 12 | Core stopped: health shows `degraded`, Validate shows `HTTP 503 CORE_UNAVAILABLE`, no verdict, Run disabled | PASS | 12-* |

axe serious/critical = 0 on: server, settings, monitor. It is 1 on: the structure-check dialog, the candidate dialog and lineage. All three are the same node, `svg[role=img][data-testid=structure-graph]` with focusable descendants (`mbnas_web/src/components/ModelStructure.tsx:173`).

## Defects
1. **The checkpoint download is not offered in the UI.** The candidate detail lists the checkpoints with `state=PRESENT` and `artifact_id=null`. `CheckpointTable` shows "id로 다운로드" only when `artifact_id` is set (`mbnas_web/src/components/CandidateView.tsx:123`), so the row reads "저장소 미등록". The gateway does serve the same checkpoints by their `ck-…` id: `GET /api/v1/runs/{run}/artifacts/{ck-id}` answers 200, and the body sha256, `X-Artifact-Sha256` and the recorded sha256 agree for all 3 checkpoints (`e2e_steps.jsonl` "V4-10-gateway"). The core never sets the contract field `CandidateDetail.checkpoints[].artifact_id`, even when a `server_store` location is PRESENT (`mbnas_core/src/mbnas_core/orchestration/retention.py:52` `checkpoint_views`, called at `mbnas_core/src/mbnas_core/console/routes_runs.py:353`). One of two fixes is needed. Either the core fills `artifact_id` (the checkpoint id) once the gateway store holds the bytes, or the web uses `k.id` for the download (DD-A51(c)).
2. **axe serious `nested-interactive`**: `mbnas_web/src/components/ModelStructure.tsx:173`. An `svg role="img"` contains focusable gene nodes. The mock e2e did not catch this.

## Limits and observations
- On mobile, dialog screenshots capture only the 375×812 viewport. Some show the area above the element of interest, for example 08-diff, where the diff result is below the fold.
- The OFF run requested `candidate_count=2` and produced 6 generated rows. The likely cause is the default seeds list (3 seeds × 2), but this was not checked (확인 못 함).
- The Monitor header for the training run reads "kind: SEARCH … PILOT / MODEL_TRAINING". This comes from the page snapshot only; I did not check the run's `kind` field.
- The legacy GPU tests in `real-server.spec.ts` are now gated by `MBNAS_E2E_GPU=1`. They were not run (no GPU in this task).

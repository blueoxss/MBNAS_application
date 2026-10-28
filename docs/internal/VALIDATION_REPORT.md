# VALIDATION_REPORT — MBNAS Research Console 0.1.0

The WP-8 §1 tests T1–T8 plus the offline install check. Each test has one row; anything not verified is written as
"확인 못 함". A row's owner fills it, and the evidence paths are relative to `docs/internal/evidence/`.
Status vocabulary: PASS | FAIL | NOT_APPLICABLE | BLOCKED_BY_ENV | 확인 못 함. The environment column says where the
result was measured: CPU (this host, `CUDA_VISIBLE_DEVICES=''`), GPU (the approved GPU window, DD-A35) or BLOCKED_BY_ENV.
Round 4 (2026-10-28 08:3x–10:xx KST) filled the T1/T2/T3/T7 rows, added the environment column and the ADMIN_BOUNDARY
row, and re-ran the affected suites on the current tree (section "Round 4" below).

| test | target | 환경 (CPU / GPU / BLOCKED_BY_ENV) | status | evidence | filled by |
|---|---|---|---|---|---|
| T1 baseline | `runtime/venv`, the tree right after the WP-1 split (2026-10-26) | CPU | **PASS** (WP-1 acceptance, 2026-10-26 19:2x KST): owner gate 1116 passed / 0 failed / 1 skipped / 47 deselected (exit 0, 2140 s); public 564 passed / 0 failed (designer re-run, exit 0; 31 skipped host data, 1 xfail = the WP-3 health case, later resolved by DD-A59 a); import closure 0 violations; operator parity 17; vitest 488. This is the baseline the later gates are compared with; the latest full gates are the "Regression gates" row | `WP1/pytest_owner_gate.txt`, `WP1/pytest_public_designer_rerun.txt`, `WP1/SUMMARY.md`, `REVIEW_LOG.jsonl` WP-1 `T1_baseline` | WP-8 (round 4, from the WP-1 records) |
| T2 core equivalence | owner venv: the original monolith from `build/source_snapshot` (reference) vs `mbnas_core` + `mbnas_model_runtime` + `mbnas_contracts` (new), same inputs | CPU (the GPU part of T2 is in T7, first pass) | **PASS**: 10/10 scenarios, 0 MISMATCH (EXACT_MATCH T2-4/5/9; only documented ALLOWED_DIFF rules in the other 7), snapshot guard unchanged. P1-final (2026-10-28 07:0x, tree `5a7bc592…`, core3 `27d6934b…`): `t2_compare.py` exit 0 in 77 s, `MBNAS_TEST_T2=1 pytest tests/owner/t2` 32 passed. **Round-4 re-run on the current tree** (`01c7d785…`, core3 `c7f60481…`, after DD-A64 and DD-A66): `t2_compare.py` exit 0, 10/10, the same allowed counts (30/78/136/0/0/12/1396/970/0/261), snapshot `1475baa5…` unchanged; T2 pytest 32 passed. C03 EVAL_2 reproduced it independently. The `evidence/T2/` folder is the first run on the pre-W4 tree (history) | `WP7/7V_FINAL/t2_compare.txt`, `WP7/7V_FINAL/t2/`, `WP7/7V_FINAL/pytest_t2.txt`, `WP7/ROUND4/t2/`, `WP7/ROUND4/final_suites/owner_t2_pytest.*`; history `T2/` | WP-8 (round 4) |
| T3 distribution/security matrix | owner venv: in-process core + `runner_sim` + stub handlers (`tests/owner/runner_protocol`), artifact wiring (`tests/owner/artifacts`), structure sandbox (`tests/owner/structure_sandbox`) | CPU | **PASS** on the current tree (`01c7d785…`, 2026-10-28 09:50 KST): `tests/owner/runner_protocol` + `tests/owner/artifacts` **97 passed / 0 failed** (exit 0, 230 s). Includes the DD-A64 claim-reclaim tests (`test_dd_a64_claim_reclaims_first.py`, 9) and **T3-34 prune round trip** (`test_t3_34_prune_roundtrip.py`; implemented by fix round 5 / DD-A66: 33 new tests passed, real-process smoke PASS in 38.4 s). **T3-35** structure-check sandbox: `tests/owner/structure_sandbox/test_sandbox.py`, `test_sandbox_api.py` (re-run in C05 EVAL_2, E2). History: WP-2 acceptance 40/40 (`WP2/2V/`), C04 EVAL_2 T3 id ↔ test map (13/13 PASSED at `5a7bc592…`), P1-final owner gate 1569/0 includes the suite. Not run: real multi-process HTTP concurrency (TestClient threads only) | `WP7/ROUND4/final_suites/owner_runner_protocol_artifacts.*`, `WP7/FIX5_T3_34/`, `WP7/FIX3_DD_A64/`, `CRITERIA/C04/EVAL_2.md` appendix A, `WP2/2V/` | WP-8 (round 4) |
| T4 owner standalone | FINAL owner ZIP `79cce02a…` in bwrap (no GPU device nodes, loopback only, original tree invisible, offline wheelhouse) | CPU (bwrap, no GPU device nodes) | **PASS** (re-run 07:4x KST): install/doctor/start/stop exit 0; web `/`; structure PASS/FAIL; OFF generation COMPLETED + architecture artifact download (core-local); CPU training pause→full-state resume→finish; artifact/checkpoint downloads sha match; export; validation-ON training → NOT_EVALUATED/`EVALUATION_REQUIRES_CUDA` with checkpoint; evaluation-ON search refused at validate (PASS-by-design); device source `local` (CPU-runner fallback) and `runners` both pass; 153 request ids with one gateway + one core access line. Run 1 on `ba8ed91f…` was PARTIAL/FAIL (kept as history). The ZIP holds source tree `5a7bc592…`; the core changed afterwards (DD-A64 claim reclaim, DD-A66 prune round trip), so a re-run with the rebuilt owner ZIP is part of P-final-2 | `T4/RERUN_README.md` (run 1: `T4/README.md`) | WP-8 (P2) |
| T5 public standalone | FINAL public ZIP `ce34b821…` → `mbnas-public-cpu:final` (`403a1b43…`, 2 972 583 681 B, `--no-cache`) gateway+runner `--network host` + owner core on the host (final owner tree, `DEVICE_SOURCE=runners`) | CPU (docker on the same host) | **PASS** (re-run 07:5x KST): customer-only output root → OFF generation COMPLETED + `core_local` architecture download; upload→check→user-YAML CPU training (pause/resume)→result rows→web `/`→downloads/export sha match; validation-ON training NOT_EVALUATED/`EVALUATION_REQUIRES_CUDA`; evaluation-ON search refused (PASS-by-design); core down ⇒ degraded + 503 `CORE_UNAVAILABLE`; `mbnas_core` absent; env/history/14-layer scans 0 hits; runner id `runner-0a6ab6db` (random, no host name); health shows only the gateway's own roots; 49 request ids linked gateway↔core access logs. Run 1 on `4174060d…` was PARTIAL/FAIL (kept as history). **Same host, same operator uid, `--network host`: functional separation, not an administrative boundary (ADMIN_BOUNDARY row).** Runner-side core-down evidence (claim fails after 6 attempts, no fallback) is cited in the README notes (T6 owner integration log, run-1 runner log). The host core came from the owner tree `5a7bc592…` (before DD-A64/A66); the public side is unchanged by those fixes. The T5 web check is HTTP-level (index, asset bytes, SPA fallback, API JSON); browser-level UI evidence is the single-host real stack `WP6/V4B_FINAL/` (final dist `index-CY9ctObG.js`, V4-01..12 PASS) and `WP6/V4B_FIX/` (C11 EVAL_1 D5) | `T5/RERUN_README.md` (run 1: `T5/README.md`) | WP-8 (P2) |
| **P-final-2 regression gates (source tree `f00987be…`, 2026-10-28 10:1x–10:5x KST)** | tests/public, tests/owner, closure, placement, generators, snapshot, T2 | CPU | **PASS**: public **1190 passed / 0 failed** (1 skipped, 44 deselected); owner gate **1601 passed / 0 failed** (8 skipped, 47 deselected); closure 0/0 (1 documented exception); placement 221/221; gen_runner_openapi/gen_registration_docs/gen_npm_notices --check 0; openapi.json 1.5.0 `70e0cffa…` unchanged; snapshot_compare 603/603; t2_compare 10/10 (0 MISMATCH), T2 pytest 32. The tree hash is written in every gate `.meta`. | `WP7/PFINAL2/gates/`, `WP7/PFINAL2/SUMMARY.md` | P1 (P-final-2) |
| **P-final-2 builds** | public / registration / owner, each built twice with `--reproducible` | CPU | **PASS**: byte-identical per profile; FILE_MANIFEST rows = members (475/476, 485/486, 2784/2785), 0 digest mismatches; PACKAGE_CONTENTS.md, LICENSE, THIRD_PARTY_NOTICES_WEB.md in all three; wheels/ at root; `.sha256` OK; web dist `index-7f__3l0I.js`; wheel gate 4/4, 4/4, 5/5. public `bc0500bd…` (2,716,913 B, 476), registration `dea510df…` (2,792,584 B, 486) are FINAL; owner `a43a8a2e…` superseded by the post-DD-A72 owner rebuild (see the owner row below). | `WP7/PFINAL2/build/`, `dist/*/*.sha256` | P1 |
| T6 relocation — public ZIP (rerun2) | final public ZIP `bc0500bd…`, bwrap: spaced path `/srv/mbnas public r2`, loopback only, no /home, project tree and etri_nas invisible, PYTHONPATH unset, 0 editable installs | CPU (bwrap) | **PASS**: doctor 24 PASS / 1 WARN (no GPU); start, API (validate → evaluation-OFF run COMPLETED with the customer-only output root `/srv/outputs`), loader check, stop exit 0; verify unit **761 passed / 0 failed** (223 skipped, 44 deselected); relocation 8/8 (`module_origins` 126 rows all ROOT, `caller_env_python_vars` []); verify package 475/475, 0 hits. Attempt 1 without `MBNAS_GW_DATASET_ROOTS` was a harness configuration error (kept as history). | `T6/rerun2/public/` | P1 |
| T6 relocation — registration ZIP (rerun2) | final registration ZIP `dea510df…`, same bwrap sandbox | CPU (bwrap) | **PASS**: same steps and unit counts, all exit 0; relocation 8/8 (126 rows ROOT); verify package 485/485, 0 hits. (`unshare -rn` offline install of the registration ZIP: not run.) | `T6/rerun2/registration/` | P1 |
| T6 relocation — owner ZIP (rerun2) | owner ZIP after the DD-A72 test fix (rebuild in progress at 11:3x) | CPU | **PENDING**: attempt 1 on `a43a8a2e…` → unit 2744 passed / **1 failed** (`test_registration_build_does_not_depend_on_the_file_list_placeholder`: round-4 test lacked the bundle skip — test defect, fixed by the designer), integration 17/17, relocation 8/8 (215 rows ROOT), doctor 32 PASS; the re-invocation from cwd `/` was not run. Result of the rerun on the rebuilt ZIP: [to be filled by the designer from the P1 report]. | `T6/rerun2/owner/` | P1 |
| T5 public standalone (rerun2, pre-DD-A73 core) | public `bc0500bd…` → `mbnas-public-cpu:final2` (`a97cb3dd…`, 2,972,655,226 B, `--no-cache`) gateway+runner `--network host` + host owner core installed offline from owner ZIP `a43a8a2e…` | CPU (docker + host) | **PASS except one step**: health = own roots; web = dist; upload + structure PASS/FAIL; OFF run with customer-only root; CPU training pause → full-state resume; downloads by id (sha == header == listed); export zip (SHA256SUMS verified); SSE replay/resume/live (ids strictly increasing); validation-ON → NOT_EVALUATED / EVALUATION_REQUIRES_CUDA; eval-ON search refused; prune round trip (e1/e2 DONE `already_absent`, e3 kept + downloaded); generated → selected → executed revisions downloadable (non-ETRI candidate); core down → 503 CORE_UNAVAILABLE (no fallback); `mbnas_core` absent in the image; random runner id; T8 runtime **0 hits** (9,458 responses, 24,518 files, `/proc` environ, image layers); request_id linkage 86 one-to-one. **FAIL**: generated-config training on the ETRI-block candidate c000 → 422 STRUCTURE_INVALID (`KeyError: 'NASGlobalTransformer'`) = **DD-A73 core defect, fixed**; the step is re-run on the fixed owner core (row below, pending). | `T5/rerun2/` | WP-8 (P2) |
| T5 generated-config step + T4 owner (rerun2 on the DD-A73 owner ZIP) | owner ZIP after fix 6 | CPU | **PENDING** (P2 after the owner rebuild): T4 full (fresh DB schema v6; v5→v6 migration check), T5 generated-config training from an ETRI-block candidate without a prior explicit check, T8 runtime. | `T4/rerun2/`, `T5/rerun2/` | WP-8 (P2) |
| T8 leak — release ZIPs (P-final-2) | the P-final-2 ZIPs | CPU (static) | **PASS for public/registration**: 0 canary hits (624 / 634 files), 0 failing (19 test-fixture literals each); owner: 0 canary hits, 0 failing, 38 host-fact hits outside `docs/internal` in 12 owner-only files (checker literals, test literals, legacy `/root/detection` defaults, t2 GPU UUID) — accepted for the owner-private profile (DD-A72); positive control 8/8 planted areas found. No `.env`, DB, key or weight file in any ZIP. | `WP7/PFINAL2/canary/`, `WP7/PFINAL2/t8/` | P1 |
| T8 leak — runtime (P-final-2) | public/registration stacks on the P-final-2 ZIPs against the owner core | CPU | **PASS**: 0 hits in all 4 scans (responses, logs, `/proc` environ; 124 needles; no normalisation needed). | `WP7/PFINAL2/t8/` | P1 |
| OFFLINE install (P-final-2) | release public ZIP under `unshare -rn`, external cu124 wheelhouse | CPU (`unshare -rn`) | **PASS for `requirements-public.txt`**: install.sh exit 0, `pip --no-index --find-links` exit 0, `pip check` 0 (pre-build A `69de59e2…` and the release ZIP, same members). **`requirements-cpu.txt`: exit 1** (`onnxruntime==1.23.2` not in the cu124 wheelhouse) → the CPU variant is an online/Docker path only (DD-A72). | `WP7/PFINAL2/offline/` | P1 |
| T6 relocation — public ZIP (history: P1 final 07:4x, superseded by P-final-2) | final public ZIP `ce34b821…`, fresh spaced path, external cwd | CPU | **PASS** (P1 final): install/doctor/start/API/stop exit 0; verify unit 717 passed / **0 failed** / 222 skipped, relocation 8/8, package 465/465 + 0 hits. The first pass's 1 failure is fixed by DD-A59 b | `WP7/7V_FINAL/T6/`, `WP7/7V_FINAL/README.md` §3 | WP-7V |
| T6 relocation — registration ZIP (history: P1 final 07:4x, superseded by P-final-2) | final registration ZIP `95b541e9…`, fresh spaced path, external cwd | CPU | **PASS** (P1 final): install/doctor/start/API/stop exit 0; verify unit 717 / **0 failed** / 222 skipped, relocation 8/8, package 475/475 + 0 hits | `WP7/7V_FINAL/T6/` | WP-7V |
| T6 relocation — owner ZIP (history: P1 final 07:4x, superseded by P-final-2) | final owner ZIP `79cce02a…`, fresh spaced path, external cwd | CPU | **PASS** (P1 final): install/start/doctor (28 PASS)/stop exit 0; verify unit 2657 passed / **0 failed** / 45 skipped, integration 17/17, relocation 8/8 | `WP7/7V_FINAL/T6/` | WP-7V |
| T6 re-invocation from a second external cwd (history: P1 final 07:4x, superseded by P-final-2) | owner core + public gateway, cwd `/` | CPU | **PASS** (P1 final): starts 0, health 200 (own roots), relative doctor 0, stops 0, 0 leftover processes | `WP7/7V_FINAL/T6/summary_reinvoke.json` | WP-7V |
| T7 GPU | approved GPU window (DD-A35, 2×RTX A6000), owner install of the frozen copy `runtime/test_root/g2_tree2` | GPU | **PASS — GPU_PATH_VALIDATED = YES (with limits)** on source tree **`9dafe4d0…`** (2026-10-27 18:02–18:27Z, 62/62 steps ok): baseline training, evaluation-ON search (32 CANDIDATE_TRAIN with measured latency), final retrain, pause → full-state resume, cancel, runner SIGKILL → lease expiry → attempt+1 with idempotent commit, local transfer + gateway downloads (sha match), device_used/device_uuid, official-weight evaluation, a GPU 1 runner (uuid lookup), owner-script benchmark. Limits: TensorRT **BLOCKED_BY_ENV** (not in the wheelhouse); BENCHMARK only through the owner script; CANDIDATE_TRAIN on a non-zero GPU not run; T2-GPU equivalence, bitwise resume equivalence, RLIMIT and the Docker GPU smoke come from the first pass (tree `164292c6…`) only. **After `9dafe4d0…` the package sources changed (38 files modified, 1 added, 0 removed (220 → 221 files; C04 EVAL_3 tree diff vs `9dafe4d0…`): NOT_EVALUATED/CPU-runner handling DD-A62/A63, claim reclaim DD-A64, prune round trip DD-A66, docstrings) and were validated on CPU only** (DD-A56 c; GPU use has been forbidden since the campaign resumed) | `T7/RERUN_README.md`, `T7/steps_rerun.jsonl`, `T7/README.md` (first pass), `WP7/ROUND4/t7_post_gpu_tree_diff.txt`, DD-A54 | WP-8 (round 4, from the G2 records) |
| T8 leak — release ZIPs (history: P1 final 07:4x, superseded by P-final-2) | the 3 final ZIPs | CPU (static scan) | **PASS** (P1 final): public 614 files / registration 624, 0 hits incl. the GPU-UUID needle; owner 2382 files, 0 canary hits, 0 failing (host facts informational for the owner profile) | `WP7/7V_FINAL/30_canary_scan_*` | WP-7V |
| T8 leak — runtime (responses, logs, process env) (history: P1 final 07:4x, superseded by P-final-2) | final public and registration stacks against the final owner core | CPU | **PASS** (P1 final): health allowlists = the gateway's own roots; 0 secret/canary/owner-string hits; normalized responses and logs 0 host-fact hits; environ only `OLDPWD` (caller cwd; 0 hits from cwd `/`; since round 4 `start.sh` unsets `OLDPWD` and the Python path variables — re-check after the rebuild). Not captured: the environ of a runner job child (no training job in the capture; static strip list + one unit test only) — adding a CPU job to the T8 §2 driver is a 1.5.1 item (C02 EVAL_1 D3) First-pass leak fixed (DD-A59 a) | `WP7/7V_FINAL/T8/README.md` | WP-7V |
| T8 import closure / private package absent at runtime (history: P1 final 07:4x, superseded by P-final-2) | source tree + public/registration venvs | CPU | **PASS** (P1 final): closure exit 0 (0 violations, 1 documented exception); `mbnas_core` has no loader/spec and is not in `sys.modules` or `/proc/<pid>/maps` | `WP7/7V_FINAL/import_closure.txt`, `WP7/7V_FINAL/T6/*_no_mbnas_core_loader.txt` | WP-7V |
| OFFLINE public install (`unshare -rn`) (history: P1 final 07:4x, superseded by P-final-2) | final public ZIP (and its build A), external wheelhouse | CPU (`unshare -rn`) | **PASS** (P1 final): both installs exit 0, network removed; PACKAGE_PROFILE `offline_install: true` (third-party wheels external, `--wheelhouse`); registration `false` (not tested) | `WP7/7V_FINAL/20_…`, `WP7/7V_FINAL/21_…`, `WP7/7V_FINAL/22_…`, `WP7/7V_FINAL/offline_install_public_final.json` | WP-7V |
| Regression gates (P1 final, source tree) (history: P1 final 07:4x, superseded by P-final-2) | tests/public, tests/owner, closure, placement, T2 | CPU | **PASS**: public 1124 passed / 0 failed; owner gate 1569 passed / 0 failed; closure exit 0; placement 221/221 ok; t2_compare 10/10, 0 MISMATCH; T2 pytest 32 passed The gate .meta files do not record the source tree hash; it is inferred as `5a7bc592…` from the owner ZIP BUILD_INFO (C12 EVAL_1 D4; P-final-2 records start/end hashes) | `WP7/7V_FINAL/gate_*.txt`, `WP7/7V_FINAL/t2_compare.txt`, `WP7/7V_FINAL/pytest_t2.txt`, `WP7/7V_FINAL/placement_coverage.txt` | WP-7V |
| ADMIN_BOUNDARY (separate administrative trust zone for the core) | core on a host administered by a party other than the customer host's | BLOCKED_BY_ENV | **BLOCKED_BY_ENV — 미충족** (no second, separately administered host). T4/T5/T6/T8 ran on one host under one operator account; the T5 containers used `--network host` and the operator's uid (`T5/rerun/harness/t5f_run.sh:21,93,101`, `T5/rerun/run/t5f_container_checks.txt`): functional separation only. Required before this can pass (C02 EVAL_1 §4): (1) the core on a separately administered machine or VM (own kernel) whose accounts, ssh keys, docker rights and mounts the customer host's root does not have; (2) the hosts connected through the documented TLS reverse proxy with certificate verification (private CA via `MBNAS_RUNNER_CA_FILE` / `MBNAS_GW_CORE_CA_FILE`) and `/core/v1/owner/*` blocked at the proxy; (3) in that layout, the T5 flow and T8 §2 run as customer-host root: enumerate `/core/v1/console`, `/core/v1/runner`, `/core/v1/owner` with the server and runner tokens (owner → 401/403 or blocked by the proxy) and scan every response including runner envelopes, the customer host's process environments, logs and file system (no `core.env`, core DB or `mbnas_core`); (4) the result recorded here and in ACCEPTANCE_MATRIX with its date | `CRITERIA/C02/EVAL_1.md` §4, `T5/RERUN_README.md`, `docs/public/SECURITY_MODEL.md` §8-3 and §9 | WP-8 (round 4) |

## Round 4 (2026-10-28 08:3x–10:2x KST): current tree, re-run subsets, what P-final-2 must do

**Tree.** Source tree (mbnas-source-tree/2, 221 files) `sha256:01c7d785347fea7e63387a650e8aef968b123bf2555dc26db5907265d334bcfd`,
unchanged at the start and end of every run below; the four public packages hash to `9859adb2…`. The final ZIPs above hold
`5a7bc592…` (owner) and `83af6d5f…` (public/registration), so **all three ZIPs are stale**: fix 3 (DD-A64, claim reclaim in
`runner_api/routes.py`), fix 5 + apply (DD-A66/A68: runner protocol 1.1.0, schema v6, retention, runner prune) and round 4
(four `mbnas_core` docstrings; docs, web dist, scripts, packager, LICENSE, `THIRD_PARTY_NOTICES_WEB.md`, tests) came after them.

**Round-4 changes** (implementation agent claude-opus-5-5 with four forks; evidence `WP7/ROUND4/`, `WP6/ROUND4_WEB/`, `WP6/V4B_FINAL/`):
- docs: `README.md`, `docs/public/{SECURITY_MODEL,FEATURES,COMPATIBILITY,OPERATIONS,INSTALL,TROUBLESHOOTING,API}.md`,
  `docs/registration/{PROGRAM_OVERVIEW,KEY_CONTRIBUTIONS,INTERFACES,SUBMISSION_SCOPE_TABLE,REVIEW_CHECKLIST,RIGHTS_REVIEW}.md` (final
  T4/T5/T6/T8 results instead of "재실행/검증 예정"; owner API = admin scope + proxy block instead of "loopback 전용"; prune round
  trip, runner protocol 1.1.0, schema v6; licence placeholder and npm notices; scope table re-measured from the allowlists);
  generated `SYSTEM_STRUCTURE.md`/diagram (owner-API edge label) by `gen_registration_docs.py`;
- `deploy/public/nginx.example.conf`: commented owner-host core proxy block that forwards only `/core/v1/console/` and
  `/core/v1/runner/` and returns 403 for `/core/v1/owner/` (nginx 1.26 `nginx -t` exit 0; routing probe: every owner variant incl.
  `../`, `%2e%2e`, `..%2F`, `//` → 403, SSE regex wins over the console prefix) — `WP7/ROUND4/checks/nginx_example_check.txt`;
- `LICENSE` (DD-A65 placeholder, no licence grant), `THIRD_PARTY_NOTICES_WEB.md` (166 npm packages, runtime 5 MIT, UNKNOWN 0;
  `scripts/dev/gen_npm_notices.py`, `--check` exit 0), `THIRD_PARTY_NOTICES.md`, `LICENSE_STATUS.md` (footnote quoted in full);
- scripts: `start.sh` (unset `PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE OLDPWD`, `PYTHONNOUSERSITE=1`), `stop.sh`
  (start-time re-check right before SIGKILL), `doctor.sh` (`env:<role>`), `verify.sh` (per-module origins and `.pth` lines in
  `relocation.json`), `package.sh` + `scripts/dev/package_build.py` (PACKAGE_CONTENTS.md, licence column without
  LICENSE_STATUS.md, `test` role for web tests, check table, wheel package-data gate + `<profile>_wheels.txt`, registration digest
  independent of the FILE_LIST placeholder), `gen_registration_docs.py` (FILE_LIST count idempotent on a shipped ZIP),
  `scripts/dev/wp3_three_process_smoke.py` (evidence header from `sys.argv[0]`), `scripts/owner/t2_compare.py` (report header from
  the actual `--out-dir`), allowlists ×3 (LICENSE, THIRD_PARTY_NOTICES_WEB.md, PACKAGE_CONTENTS.md);
- web: block-table Pos button opens the same block detail as the graph/gene (`?block=`, `model_tab=structure`), reason-specific
  badge tooltip (CandidateView, Monitor); new dist `index-7f__3l0I.js` `df0d6b2d…`, `index.html` `9e8c8f76…`, css unchanged;
- tests: `tests/public/runner/test_runner_settings_no_db.py` (7), `test_runner_entrypoint_redaction.py` (5),
  `tests/owner/projection/test_runner_route_canary.py` (2; 128 runner-route bodies, 0 planted markers),
  `tests/owner/orchestration/test_final_retrain_excludes_not_evaluated.py` (2), `tests/public/ops/test_ops_role_env.py` (2),
  `test_ops_stop_identity.py` (3), `test_ops_relocation_origins.py` (1), `tests/public/packaging/test_package_contents_and_gates.py`
  (21); negative controls recorded in `WP7/ROUND4/{tests,ops,packaging}/`.

**Re-runs on `01c7d785…`** (markers `not gpu and not live and not legacy_records and not host_data`, CPU only; `.txt` + `.meta`
with command, UTC times, exit and start/end tree hash in `WP7/ROUND4/final_suites/`):

| suite | result |
|---|---|
| `tests/public/ops` | 65 passed, 2 deselected, exit 0 |
| `tests/public/runner` | 141 passed, 9 deselected, exit 0 |
| `tests/public/infra` + `tests/public/contracts` | 268 passed, exit 0 |
| `tests/public/packaging` | 63 passed, exit 0 |
| `tests/owner/projection` | 261 passed, exit 0 |
| `tests/owner/orchestration` | 48 passed, exit 0 |
| `tests/owner/runner_protocol` + `tests/owner/artifacts` | 97 passed, exit 0 |
| `t2_compare.py` (`WP7/ROUND4/t2/`) / `MBNAS_TEST_T2=1 pytest tests/owner/t2` | 10/10, 0 MISMATCH, exit 0 / 32 passed |
| `check_import_closure.py`, `placement_coverage.py`, `check_docs.py`, `gen_registration_docs.py --check`, `check_env_examples.py`, `gen_npm_notices.py --check` | exit 0 each; placement 221/221 (4 rows appended for the docstring files) — `WP7/ROUND4/checks/docs_static_gates.txt` |
| web: `check:types`, `typecheck`, `vitest`, `build`, `check:bundle`, mock e2e | exit 0 each; vitest 46 files / 600 tests; mock e2e 210 passed; a fresh scratch build is byte-identical to the shipped dist (`WP6/ROUND4_WEB/`, `WP7/ROUND4/checks/web_recheck.txt`) |

Not run in round 4 (P-final-2): the full `tests/owner` and `tests/public` gates with the tree hash in `.meta`; the reproducible
rebuild of the three ZIPs and the T8 ZIP scan; T6 (scripts changed); T4/T5 on the rebuilt ZIPs (core claim and prune changed;
C11 also asks for a `source.kind = "generated"` training step); the real-stack V4 browser run on the new dist (`WP6/V4B_FINAL/`
covers the previous dist `index-CY9ctObG.js` only); the runner-child environ capture in T8 §2 (1.5.1); the C12 regression record.

Open web items for 1.5.1 (documented in `docs/public/FEATURES.md` limits): the History screen's failure table lists every row that has a `reason_code`, so trained-but-not-evaluated rows (`EVALUATION_REQUIRES_CUDA`) appear there; the Lineage screen's "학습 완료 · 미평가" badge has no tooltip (CandidateView and Monitor got the reason-specific tooltip in round 4).

## P1-final pass (WP-7V, 2026-10-28 06:4x–07:4x KST): the release state
After all fix rounds, with no one editing the tree. Full details and exact commands: `WP7/7V_FINAL/README.md`.

| final ZIP (`dist/<profile>/`) | sha256 | staged files |
|---|---|---|
| `mbnas_application-0.1.0-public-runtime-linux_x86_64.zip` | `ce34b821806a27cd2b2ff461d8020e85e0350d846d9fae6188c15f0d09bc2a2b` | 466 |
| `mbnas_application-0.1.0-registration-runtime-linux_x86_64.zip` | `95b541e9aa8a95556bb4ae3b74c4ffa59e47977ad1c260d5656dc493144fecb1` | 476 |
| `mbnas_application-0.1.0-owner-private-runtime-linux_x86_64.zip` | `79cce02a8d36eec17a24a5d43228b02fa32832ff7ca9499a9035c3c0f56d9c20` | 2115 |

1. **Regression gates** (`run_gates_final.sh`, the WP-4C commands):
   - `pytest tests/public -m "not gpu and not live and not legacy_records and not host_data" --timeout=180`:
     1124 passed, 0 failed, 1 skipped, 44 deselected.
   - The same for `tests/owner`: 1569 passed, 0 failed, 8 skipped, 47 deselected.
   - `check_import_closure.py` exit 0. `placement_coverage.py` ok (221/221).
   - `t2_compare.py --work-root <scratch>`: 10/10 non-mismatch (3 EXACT_MATCH, 7 ALLOWED_DIFF), 0 MISMATCH; the
     source-snapshot guard is unchanged.
   - `MBNAS_TEST_T2=1 pytest tests/owner/t2`: 32 passed.
2. **Builds** (`--reproducible`, in the order public → registration → owner): each produced two identical builds
   with 0 violations and 0 warnings. `dist/` holds only the final files, and `sha256sum -c` is OK for all three.
   - Canary scans: public and registration 0 hits; owner 0 canary hits and 0 failing.
   - Packaging tests: 52 passed.
   - The public ZIP was built twice. Build A (`bbe5e29f…`) was installed under `unshare -rn`, and its record then
     went into the final ZIP's PACKAGE_PROFILE. The two builds differ only in PACKAGE_PROFILE/BUILD_INFO/FILE_MANIFEST.
3. **T6 from the final ZIPs**: fresh spaced paths, external cwd. Every install, doctor `--strict`, start, API step
   and stop exited 0. Verify:
   - public: unit 717 passed / 0 failed / 222 skipped (221 DD-A43 b, 1 mini run absent), relocation 8/8, package
     465/465 with 0 hits;
   - registration: the same unit counts, relocation 8/8, package 475/475 with 0 hits;
   - owner: unit 2657 passed / 0 failed / 45 skipped, integration 17/17, relocation 8/8.

   Re-invocation from cwd `/` succeeded.
4. **T8 §2 runtime**: the public gateway health returns only its own roots, all empty. There are 0 secret, canary
   and owner-side hits in responses, service logs and process environments. Two strings are normalized: the tree's
   own root and the output_root the client supplied (the core's own output root is not normalized). After that, the
   responses and logs have 0 host-fact hits, and the environments contain only `OLDPWD`, the cwd of the shell that
   ran `start.sh`; from cwd `/` it has 0 hits. Install logs echo only the operator's `--wheelhouse`/`--python`
   arguments.
5. **Offline**: `unshare -rn bash scripts/install.sh --profile public --offline --wheelhouse …` exited 0 for build A
   and for the final ZIP. Inside the namespace only `lo` exists and the 4 public packages import.
   `offline_install: true` is kept.

Remaining minors (no FAIL):
- `start.sh` passed `OLDPWD` (the caller's cwd) into the service environment. Fixed in source in round 4: `start.sh` now unsets
  `OLDPWD`, `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP` and `PYTHONUSERBASE` and sets `PYTHONNOUSERSITE=1` before every role;
  the final ZIPs above still carry the old script until the P-final-2 rebuild.
- 221 public tests skip in the public bundle (DD-A43 b / DD-A58, documented).
- The owner ZIP carries informational host facts, including 75 GPU-UUID hits, in owner-only `docs/internal/evidence`
  and `tests/owner/t2`.

## First pass (WP-7V, 2026-10-28 03:xx KST): superseded by the P1-final pass above, kept as history
`bash scripts/package.sh --profile <p> --reproducible`. Order: public, registration, owner, one at a time. Each build
gave exit 0, two identical builds, and 0 violations / 0 warnings. Logs are in `WP7/7V/package_<p>.log`.

| ZIP | sha256 | files | used by |
|---|---|---|---|
| `dist/public/mbnas_application-0.1.0-public-runtime-linux_x86_64.zip` (tested build) | `4174060dde93ce8aa941f2cbd36fcb8b0230f5f184e68f8ccbc71d49bcd9ae86` | 454 staged | T6 public, offline install, T8 |
| same name, **current file** (rebuilt with the offline record) | `529243cfe506ee6aa39ed74eb8939e34844eeda0562367b16a77d46180e96792` | 454 staged | see note |
| `dist/registration/mbnas_application-0.1.0-registration-runtime-linux_x86_64.zip` | `d5748c136086081522c05f4f91440a9407d435623700fcdc7de16eedfe4e4e9b` | 464 staged | T6 registration, T8 |
| `dist/owner/mbnas_application-0.1.0-owner-private-runtime-linux_x86_64.zip` | `ba8ed91f601330e1272bc89fd217bba81fbc5ee943cfb8f45f353795da787df3` | 1350 staged | T6 owner (core for the public stack), T8 |

The public rebuild changed PACKAGE_PROFILE.json, BUILD_INFO.json and FILE_MANIFEST.jsonl. It also picked up edits that other
agents made in the meantime to README.md, six files in docs/public and `mbnas_web/e2e/real-server.spec.ts`. The wheels and
code are identical (`WP7/7V/public_rebuild_diff.txt`), and the rebuilt ZIP scanned clean (602 files, 0 hits). The final
P-stage rebuild of all three ZIPs, after every wave has landed, needs a T8 ZIP scan again; T6 needs re-running only if code changed.

## T6 — relocation, first pass (history — superseded by the P1-final pass; its one public/registration unit failure was fixed by DD-A59 b)
Trees (paths with spaces): `runtime/test_root/t6/{mbnas public test, mbnas registration test, mbnas owner test, mbnas offline test}/mbnas_application`.
Every command was started from an external working directory, the session scratchpad `…/scratchpad/t6`. The
scripts there are copied to `T6/t6_t8_driver.py`, `T6/t6_registration.py` and `T6/configure_t6.py`. Ports: core 18700,
owner gateway 18711, public gateway 18710, registration gateway 18712. Exit codes for every step are in
`T6/transcript.jsonl`.

| step | command (from the external cwd) | public | registration | owner |
|---|---|---|---|---|
| install | `bash "<tree>/scripts/install.sh" --profile <public\|owner> --offline --wheelhouse <SRC>/build/wheelhouse --python <py3.10>` | 0 | 0 | 0 |
| configure | owner: core port 18700 and core URLs. public: server and runner principals issued by the owner core (`mbnas-core-principal create`), tokens moved file to file, never printed (`T6/configure_t6.py`) | 0 | 0 | 0 |
| doctor before start | `bash "<tree>/scripts/doctor.sh" --profile public --strict` | 0 | 0 | — |
| start | `bash "<tree>/scripts/start.sh" --profile <p>` (owner: core + owner worker + gateway + runner; public: gateway + runner) | 0 | 0 | 0 |
| doctor --strict | `… doctor.sh --profile <p> --strict --json …` | 0 (22 PASS) | 0 (22 PASS) | 0 (27 PASS) |
| API via the public gateway | health 200 (core ok, reachable), no token → 401, presets 200, `/` 200, upload 200, structure-check 200 PASS / broken YAML 200 FAIL with `SHAPE_MISMATCH` at row 12, validate 200, run create 200 → COMPLETED (2 candidates), snapshot / candidate detail / config-revisions / export (11 623 B zip) / runs 200 | all as listed | health, presets, `/`, upload, structure-check: 200 | (owner core served the public stack) |
| verify | `bash "<tree>/scripts/verify.sh" --profile public --suite unit,relocation --json …` (owner: `--profile owner --suite unit,integration,relocation`) | exit 1: unit FAIL 633 passed / **1 failed** / 215 skipped / 44 deselected; relocation PASS 8/8 | exit 1: same | exit 0: unit PASS 2537 passed / 0 failed / 46 skipped (45 + 1 xfail) / 91 deselected (21 min); integration PASS 17/17; relocation PASS 8/8 |
| verify package | `MBNAS_CANARY_SCAN=<SRC>/scripts/dev/canary_scan.py bash … verify.sh --profile public --suite package` | 0: manifest 453/453, scan 0 hits | — | — |
| stop | `bash "<tree>/scripts/stop.sh" --profile <p>` | 0 | 0 | 0 |
| re-invocation | from a second external cwd `…/scratchpad/t6/second cwd`: owner `start.sh --role core`, public `start.sh --role gateway`, health, `doctor.sh` (relative, from inside ROOT), both `stop.sh` | start 0, health 200 (core ok), doctor 0, stop 0 | — | start 0, stop 0 |

After the stops, no listener remained on 18700/18710/18711/18712, every `runtime/pids/` was empty and no service process of the t6 trees was left.

The failing unit test is `tests/public/model_runtime/test_datasets.py::test_subset_cli_unchanged_interface`. It runs
`scripts/public/make_pilot_subset.py`, which no bundle ships, so it fails with "can't open file". The script's
argparse defaults hold original-host paths (`/root/detection/...`), so the packager's host-fact gate keeps it out of
public/registration. Fix options: (a) the script owner removes the defaults, by requiring `--source`/`--out`, and
then `scripts/public/**` can be added to the public/registration allowlists; or (b) the test owner applies the
DD-A43(b) pattern (`pytest.skip("owner-only tool not in this bundle")` when the script is absent). Of the 215 skips,
214 are DD-A43(b) "owner-only tool not in this bundle" (tests/_shared/harness.py is not shipped). Most are in
test_security.py (148), test_routes.py (29) and test_campaign_adapter.py (15). These tests run in the owner bundle.

My own check script failed once: `T6/21_public_no_mbnas_core_loader.txt` exited 1 because it named a module that
does not exist (`mbnas_server.app`). The re-run with the real entry module `mbnas_server.api.app` exited 0
(`T6/00_public_no_mbnas_core_loader_rerun.txt`).

In the owner bundle the same test passes, because the owner ZIP ships `scripts/public/` (verified in the owner unit junit).

## T8 — leak, first pass (history — superseded: the health leak below was fixed by DD-A59 a and the P1-final re-run PASSED)
Details are in `T8/README.md`.
- ZIPs: public 602 files / registration 612 / owner 1596 (nested wheels included), 0 canary hits. Public and
  registration have 0 host facts. Owner host facts do not fail for the owner profile. Command:
  `python -I scripts/dev/canary_scan.py --manifest docs/internal/canary/CANARY_MANIFEST.json --profile <p> --json … <zip>`.
- Runtime: the scan covered responses (health, presets, upload, structure-check, validate, run, snapshot,
  candidate detail, config-revisions, export), the public gateway/runner/audit logs and the public processes'
  `/proc/<pid>/environ` and cmdline. Needles were the secret values of all five env files plus owner-side strings.
  Result: **0 secret hits, 0 canary hits**, and **1 leak**: `GET /api/v1/health` returns the owner core's absolute
  `allowlists.dataset_roots` and `allowlists.output_roots`. The gateway relays the core's allowlists whenever it
  has no roots of its own, which is the default public install. Cause:
  `mbnas_server/src/mbnas_server/api/relay.py` `health()`.
  The 1.5.0 contract says "Health.allowlists customer-host roots only".
- Private package: no loader, no spec, and not in `sys.modules` in the public venv after importing the public apps.
  Absent from `/proc/<pid>/maps`. `scripts/dev/check_import_closure.py` exit 0 (0 violations, 1 documented data exception).

## Offline install, first pass (history — repeated on the final ZIP in the P1-final pass)
`unshare -rn bash "<T6>/mbnas offline test/mbnas_application/scripts/install.sh" --profile public --offline --wheelhouse <SRC>/build/wheelhouse --python <py3.10>`
was run from the external cwd into a fresh path with spaces and exited 0 (138 wheels processed). Network proof
(`WP7/7V/unshare_network_proof.txt`): inside the namespace only `lo` exists, DNS fails and 1.1.1.1:443 is
unreachable. Under `unshare -rn` the public packages import and `mbnas_core` is absent. The result was recorded through the
packager, not by editing the ZIP. `build/allowlists/public.txt` `[profile] offline_install_evidence =
docs/internal/evidence/WP7/7V/offline_install_public.json` makes `scripts/dev/package_build.py` write
`install.offline_install: true` with a note that the third-party wheels are **not bundled** and must be passed with
`--wheelhouse`. A failing, other-profile or network-on record stops the build with exit 2; a missing record keeps
`false` (`WP7/7V/offline_flag_negative_checks.txt`). Registration keeps `offline_install: false` because it was not
tested under `unshare -rn`. The optional `-offline` ZIP variant with bundled wheels was not built (확인 못 함).

## Allowlist change for the final build (coordinator request, 2026-10-28; history — applied in the P1-final build)
WP-4C removed the host facts from `tests/public/contracts/test_contract_history.py` and
`tests/public/tools/smoke_api_only.py`. Their `[deny]` lines have been dropped from `build/allowlists/public.txt` and
`registration.txt`, and nothing else was excluded or deleted.
- `canary_scan.py --manifest … --profile public` on the two files: 2 files, 0 hits, PASS (`WP7/7V/hostfact_scan_two_test_files.txt`).
- End-to-end check without touching `dist/`: `bash scripts/package.sh --profile public --canary` and the same for
  `--profile registration`. Each copies the tree, plants canaries and builds into `build/canary/<p>/tree`. Both exited
  0 with 0 violations (`host_facts = fail` is in force); the scans checked 607 and 617 files and found 0 hits. Both
  files are in the test ZIPs (`WP7/7V/canary_build_<p>_after_deny_drop.log`, `WP7/7V/canary_build_public_vs_tested_members.txt`).
- The ZIPs in `dist/` above still exclude the two files. The final P-stage rebuild picks the change up.

## T4 / T5 — run 1 (history — superseded by the FINAL re-run below; WP-8 §1, C11 `real_core_runner`; agent P2, 2026-10-28 KST)

ZIPs used (snapshot 03:05 KST, all mtimes after the run start 03:02:23): owner `ba8ed91f601330e1272bc89fd217bba81fbc5ee943cfb8f45f353795da787df3`,
public `4174060dde93ce8aa941f2cbd36fcb8b0230f5f184e68f8ccbc71d49bcd9ae86` (the later 03:14 rebuild `529243cf…` differs in
docs/metadata only; image inputs identical, `T5/public_zip_rebuild_diff.txt`), registration `d5748c13…` (not used).
CPU only; no GPU compute (T4 variant B exposes NVML nodes without `/dev/nvidia-uvm`; the T5 host core reads the NVML
inventory for validation).

| item | T4 (owner ZIP, bwrap) | T5 (public image + host core) |
|---|---|---|
| install / build | `install.sh --profile owner --offline` exit 0 (105 s) | `docker build -f deploy/public/Dockerfile.cpu` exit 0 (227 s, online) |
| start / stop | `start.sh`/`stop.sh --profile owner` exit 0/0 (both variants), nothing left | core+owner worker on 127.0.0.1:18750, containers `--rm` on :18760; all stopped, none left |
| web console `/` + asset (byte-identical to `mbnas_web/dist`) | PASS | PASS |
| structure check valid / invalid example YAML | PASS / PASS (`SHAPE_MISMATCH`, `MERGE_INPUT_MISMATCH`) | PASS / PASS |
| OFF generation → COMPLETED, candidates UNTRAINED | PASS | PASS (same-path output root); **FAIL** with a customer-only output root (owner worker crashes, run stays QUEUED) |
| explicit CPU training 64 px 2 ep, pause → full-state resume → finish | PASS with NVML nodes; **FAIL** without (`DEVICE_UNAVAILABLE`) | PASS (also COMPLETED with the customer-only output root, DD-A46) |
| result rows, artifact + checkpoint download (sha256 body == header == listed), export zip | PASS (NVML variant) | PASS |
| decision step (promotion / final retrain) | **FAIL** (CPU: EVAL_FAILED → `NO_SUCCESSFUL_EVALUATION`; final retrain 409) | **FAIL** (same; plus `DATASET_YAML_INVALID` for a container dataset path) |
| core down → degraded + 503 `CORE_UNAVAILABLE`, no fallback | — | PASS |
| `mbnas_core` absent / no secrets or canaries in env, history, layers | — | PASS (import fails; 0 hits over 52 005 files) |
| request ids echoed / propagated / linked gateway log ↔ core log | 84/84 echoed; core DB events 4, audit 5; linked **0 → FAIL** | 94/95 echoed; core DB events 5, audit 7, gateway log 3; linked **0 → FAIL** |

Product findings for the designer (details in the READMEs): CPU runners are excluded from the validation device
inventory (`device_uuid` NULL) so a GPU-less owner host cannot train; the owner worker writes `<output_root>/<run>/control`
on the core host and crashes on a customer-only root; PROPOSE_GENERATION reads the dataset YAML on the core host; no
per-request request-id logging in core/gateway.

## T4 / T5 — FINAL re-run (agent P2, 2026-10-28 07:4x–08:0x KST; supersedes the run-1 section above for the release state)

ZIPs: owner `79cce02a8d36eec17a24a5d43228b02fa32832ff7ca9499a9035c3c0f56d9c20`, public
`ce34b821806a27cd2b2ff461d8020e85e0350d846d9fae6188c15f0d09bc2a2b` (copied to `runtime/test_root/t4t5/final/zips`; same methods as
run 1, fresh scratch under `runtime/test_root/t4t5/final/`). CPU only. The T4 sandbox has no `/dev/nvidia*`. The T5 core uses
`MBNAS_CORE_DEVICE_SOURCE=runners`, so it makes no NVML read.

| coordinator expectation | T4 | T5 |
|---|---|---|
| (a) strict sandbox: training / OFF generation validate via the CPU runner entry (default `local` fallback; `runners` once) | PASS (`local`: `[cpu]` immediately; `runners`: `[cpu]` after 24.5 s, within the 30 s cache) | PASS (`runners`) |
| (b) customer-only output root: OFF generation COMPLETED + architecture artifact via the gateway (core_local) | n/a (owner all-in-one); architecture download PASS (`X-Artifact-Verification: core`) | PASS (`run-81d06544bf80`, 985 B `6999dc2b…`, verification core) |
| (c) validation-ON CPU training → COMPLETED, NOT_EVALUATED (`EVALUATION_REQUIRES_CUDA`), checkpoint kept; evaluation-ON search refused at validate | PASS / PASS-by-design (both device sources) | PASS / PASS-by-design |
| (d) same request_id in the gateway access log and the core access log, one line each, ≥ 3 requests | PASS: 153 ids (9 more have 2 core lines = two core calls per download; 9 are gateway-only) | PASS: 49 ids (7 more have 2 core lines; 10 are gateway-only, including 3 made while the core was down) |
| (e) T5 runner id random `runner-<8 hex>`, no host name | install.sh issued `runner-d3c27c94` (no RUNNER_ID line) | PASS: `runner-0a6ab6db`, generated by the image; 0 host-name hits in public responses |
| (f) public gateway health shows only its own roots | owner: own (empty) roots | PASS: `{dataset_roots: [/srv/mbnas/data], output_roots: [/srv/mbnas/runtime/outputs], archive_roots: []}`; core-only roots absent |
| as run 1: downloads sha match, export, core down → 503, `mbnas_core` absent, env/layer leak scans 0 | PASS (core-down n/a) | PASS (52 005 files, 104 needles, 0 hits; env 0 unexpected) |

Counts: T4 harness 11/11 items (`local`) + 6/6 (`runners`), 171 request ids, 171 echoed; T5 harness 10/10 + `coredown` 1/1,
66 request ids, 66 echoed. Image `mbnas-public-cpu:final` = `sha256:403a1b435390fa2951a36bef01d33b7e6657461376bc36eecfde49cf1c98d8d1`,
2 972 583 681 B, 14 layers, `--no-cache` build exit 0 in 247 s. Not re-run on the final ZIP: run 1's NVML-node variant
(not needed); the gateway dataset-summary path of an evaluation-ON PROPOSE_GENERATION (needs a GPU fleet; 확인 못 함).

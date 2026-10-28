# PFINAL2 SUMMARY (written by the designer from the P1 agent's report; the harness blocked the agent from writing this file) — 2026-10-28 10:1x–11:2x KST

Tree hash (5 packages, 221 files): `sha256:f00987be7994203fc16f52140bd98b3f89e1b548b6a346255066727a36dcc40c`; public 4-package hash `9859adb2…` (129 files). Written in every gate .meta; unchanged after the gates.

| step | check | result |
|---|---|---|
| 1 | stale bytecode | 1 file deleted (tests/owner/t2/__pycache__/t2_core.cpython-310.pyc); 0 left |
| 2 | import closure | exit 0: 0 violations, 0 unresolved, 1 documented exception |
| 2 | placement coverage | 221/221 |
| 2 | gen_runner_openapi --check / public contract / gen_registration_docs --check / gen_npm_notices --check | exit 0 / openapi.json 70e0cffa… unchanged, gen-types --check OK / OK / OK |
| 2 | snapshot_compare | 603/603, delta chain ok |
| 2 | t2_compare + T2 pytest | 10/10 (3 EXACT, 7 ALLOWED_DIFF), 0 MISMATCH; 32 passed |
| 2 | public suite | **1190 passed / 0 failed**, 1 skipped, 44 deselected |
| 2 | owner gate | **1601 passed / 0 failed**, 8 skipped, 47 deselected |
| 3 | builds (each profile twice, --reproducible) | byte-identical per profile; 0 violations, 0 warnings, every check PASS |
| 3 | BUILD_INFO source tree | public/registration = 9859adb2…, owner = f00987be… |
| 3 | FILE_MANIFEST rows vs members | 475/476, 485/486, 2784/2785; 0 digest mismatches |
| 3 | required files | PACKAGE_CONTENTS.md, LICENSE, THIRD_PARTY_NOTICES_WEB.md in all three; LICENSE_STATUS.md owner only; wheels/ at root (4/4/5); .sha256 OK; web dist index-7f__3l0I.js (df0d6b2d…) in all three; wheel gate 4/4, 4/4, 5/5 PASS |
| 4 | canary | public 624 files 0 hits; registration 634 files 0 hits; owner 0 canary hits, 0 failing, 38 host-fact hits outside docs/internal in 12 owner-only files (see DD-A72); positive control: 8/8 planted areas found; FILE_LIST check on the shipped registration ZIP OK |
| 5 | T6 public (bwrap, spaced path, loopback only, no /home, no project tree) | **PASS**: doctor 24 PASS/1 WARN, start/API/loader/stop exit 0, verify unit 761/0 (223 skipped, 44 deselected), relocation 8/8, package 475/475 |
| 5 | T6 registration (bwrap) | **PASS**: same steps; verify package 485/485 |
| 5 | T6 owner | **FAIL (test defect)**: unit 2744 passed / 1 failed (`test_registration_build_does_not_depend_on_the_file_list_placeholder` — round-4 test missing the 'not a source tree' skip; bundles do not ship build/); integration 17/17, relocation 8/8, doctor 32 PASS. Fixed by the designer (DD-A72) → owner rebuild + owner T6 rerun pending |
| 6 | T8 static rescan | public/registration 0 failing (19 test-fixture literals each); owner 25 failing outside docs/internal (legacy /root/detection defaults, checker literals, t2 GPU UUID — owner-only, DD-A72); no .env/DB/key/weight in any ZIP |
| 6 | T8 §2 runtime | **0 hits** in all 4 scans (responses, logs, /proc environ; 124 needles) |
| 7 | offline (release public ZIP, unshare -rn) | install.sh exit 0; pip --no-index -r requirements-public.txt exit 0; pip check 0; **requirements-cpu.txt dry run exit 1** (onnxruntime==1.23.2 not in the cu124 wheelhouse → CPU variant is online/Docker only) |

T6 rerun2 caller-env facts: public/registration shells had only PYTHONDONTWRITEBYTECODE=1 (harness); owner additionally OLDPWD=<ROOT> (harness shell), no PYTHONPATH/PYTHONHOME/PYTHONSTARTUP/PYTHONUSERBASE/VIRTUAL_ENV; relocation.json caller_env_python_vars [] for all; module_origins all ROOT (126/126/215 rows).

dist/ (this pass): public `bc0500bdfa43ddf5fb74bde4cf51a967e06f45bd2b901ba26da0794c1f0853d3` (2,716,913 B, 476 members); registration `dea510dfb54dd85da556ac38423f747a0a7d1afbff7398c501127bd0f8b92f6e` (2,792,584 B, 486); owner `a43a8a2ef9fddfa51ed64f3c9053723065a5d274f6126d77c6ecda7c9cb1e138` (80,204,033 B, 2785) — **owner to be rebuilt after the test fix**; public/registration do not ship the fixed test and stay final.

Not verified in this pass: owner re-invocation from cwd `/` (stopped), owner unit suite after the test fix, training job + SSE in the T6/T8 capture (T4/T5 cover), registration ZIP under unshare -rn.

## Addendum (2026-10-28 13:03 KST, written by the designer from P1's report): gates_r2 on tree 1b4bd1a4… and the DD-A73 owner ZIP

Trigger: DD-A72 (packaging-test bundle skip) and DD-A73 (`bind_structure` registers the ETRI mixed blocks; new test `tests/owner/api/test_dd_a73_bind_structure_mixed.py`). Tree hash `sha256:1b4bd1a4a45196e3c558d62507f109301973eddc180df5968ed7014a72e6708c` (221 files; unchanged after the gates); public 4-package hash 9859adb2… unchanged. Evidence: `PFINAL2/gates_r2/` (every .meta carries the tree hash), `PFINAL2/owner_c/{build,canary,t8}`, `docs/internal/evidence/T6/rerun2/owner/attempt3_dd_a73_final/`.

| step | result |
|---|---|
| stale bytecode | none found, nothing deleted |
| import closure | exit 0: 0 violations, 0 unresolved, 0 forbidden, 1 documented exception |
| placement coverage | 221/221, 0 findings |
| gen_runner_openapi --check / gen_registration_docs --check / gen_npm_notices --check | all OK |
| public contract | openapi.json 70e0cffa… unchanged; gen-types --check matches |
| snapshot_compare | ok (603/603) |
| t2_compare + T2 pytest | 10/10 (3 EXACT, 7 ALLOWED_DIFF), 0 MISMATCH; 32 passed |
| public suite | **1190 passed / 0 failed**, 1 skipped, 44 deselected |
| owner gate | **1604 passed / 0 failed**, 8 skipped, 47 deselected (+3 from the DD-A73 tests) |
| public / registration ZIPs | **not rebuilt**: every non-generated member of bc0500bd… (468/475 rows) and dea510df… (477/485 rows) is byte-identical to the current tree; none of the DD-A72/A73 files is a member (`gates_r2/public_registration_member_check.txt`) → both stay final |
| owner ZIP (owner_c) | `53e6a1f0cf100cb813ff500d4936a4dcf03feea623886b43879c51de572434c9`, 82,702,282 B, 3246 members; built twice with --reproducible, byte-identical; 0 violations, 0 warnings, every check PASS; BUILD_INFO source tree 1b4bd1a4…, build id r-99eeb029485c9de8; FILE_MANIFEST 3245 rows, 0 digest mismatches; wheel gate 5/5; mbnas_core wheel a2d97415… |
| FILE_MANIFEST diff vs a43a8a2e… | 461 added, 0 removed, 17 changed. Outside docs/internal exactly: `mbnas_core/src/mbnas_core/training_runs/selection.py` (changed), `tests/public/packaging/test_package_contents_and_gates.py` (changed), `tests/owner/api/test_dd_a73_bind_structure_mixed.py` (added), `wheels/mbnas_core-0.1.0-py3-none-any.whl` (changed, follows from selection.py), build metadata BUILD_INFO.json / PACKAGE_PROFILE.json / PACKAGE_CONTENTS.md / evidence/package_build/owner_wheels.txt. Inside docs/internal: records (ACCEPTANCE_MATRIX, DECISIONS, RELEASE_1_5_1_CARRYOVER, REVIEW_LOG, TASK_STATE, VALIDATION_REPORT, provenance/PLACEMENT_MANIFEST.jsonl, FINAL_REPORT.md added) + 460 evidence files (T5/rerun2 63, T6/rerun2 207, WP7/FIX6_DD_A73 27, WP7/PFINAL2 161, CRITERIA/C08 1; C02/C07/C09 changed). Vs 867cc577…: 15 T6 evidence paths moved into the attempt history folders |
| canary (owner_c) | 3553 files, 0 canary hits, 0 failing; 38 host-fact hits in 12 owner-only files outside docs/internal — the identical list to the a43a8a2e scan (DD-A72); positive control 8/8 planted areas, dist/ untouched |
| T8 static rescan (owner_c) | 25 failing outside docs/internal, identical list (24 legacy /root/detection paths + 1 smoke-script fixture key); no .env, DB, key or weight file |
| T6 owner rerun2 attempt3 (53e6a1f0…; fresh extraction into the spaced path `runtime/test_root/t6/rerun2/mbnas owner r2c`, external cwd) | install --offline 0; start (service-like env) 0; doctor --strict 0 with 32 PASS; verify unit **2747 passed / 0 failed**, 56 skipped (bundle skips of the fixed packaging tests), 91 deselected; integration 17/17; relocation 8/8; package 3245/3245, 0 hits; stop 0; re-invocation from cwd `/`: start --role core 0, doctor 0, stop 0, 0 leftover processes; caller shell had only PYTHONDONTWRITEBYTECODE=1 and OLDPWD (no PYTHONPATH/PYTHONHOME/PYTHONSTARTUP/PYTHONUSERBASE/VIRTUAL_ENV); relocation.json caller_env_python_vars []; module_origins 215 rows all ROOT; wheel gate 5/5 |
| secret-value self-scan | PFINAL2/ + T6/rerun2/ (513 files): 0 hits |

History kept: `T6/rerun2/owner/attempt1_test_defect/` (a43a8a2e…, unit 1 failed, the packaging-test defect) and `attempt2_pre_dd_a73/` (867cc577…, DD-A72 only: unit 2744/0/56, integration 17/17, relocation 8/8, package 3033/3033 0 hits, cwd `/` re-invocation all 0). The DD-A72 packaging rerun in the source tree: 63 passed / 0 failed (`PFINAL2/gates/pytest_packaging_after_dd_a72.*`, tree still f00987be… because the test lies outside the 5 hashed roots). Superseded owner ZIPs: a43a8a2e… and 867cc577….

dist/ after this pass (designer re-ran `sha256sum -c` in each dist/<profile>/ at 12:59: all three OK): public `bc0500bdfa43ddf5fb74bde4cf51a967e06f45bd2b901ba26da0794c1f0853d3` (2,716,913 B, 476 members, unchanged); registration `dea510dfb54dd85da556ac38423f747a0a7d1afbff7398c501127bd0f8b92f6e` (2,792,584 B, 486, unchanged); owner `53e6a1f0cf100cb813ff500d4936a4dcf03feea623886b43879c51de572434c9` (82,702,282 B, 3246, mtime 12:28).

Not verified in this addendum: public/registration T6/T8 were not re-run on this tree (members byte-identical → the earlier passing runs stand); no training job or SSE stream in the T6/T8 captures (T4/T5 cover them); requirements-cpu.txt offline and registration under unshare -rn remain untested; the 38 owner-only host-fact hits and the 25 static-rescan items are reported, not fixed (DD-A72 → 1.5.1).

Next: T4 full + T5 generated-config step + T8 runtime on the DD-A73 owner ZIP (P2); C07/C09 owner addenda; C10–C12 re-scores; final docs-only owner rebuild (DD-A70) whose FILE_MANIFEST diff must be limited to docs/internal/** and build metadata.

## Addendum 2 (2026-10-28 15:50 KST, designer from P1's reports): fix round 7 (DD-A74/DD-A75) — gates_r3/gates_r4, owner_d, T6 attempt4

Trigger: DD-A74 (owner allowlist denies the T8 owner scan dumps), DD-A75 F1/F1b (projection.py: public raw_metrics path scrub, whole-value replacement for path-valued strings), F2 (fleet-aware integration smoke). Tree hash history: `1b4bd1a4…` (DD-A73) → `26e02259…` (F1) → **`8fc93f2a596891174c2884e1697fd6003377b713102c0b21b01eb45d3f6f7312`** (F1b; release-candidate tree). Public 4-package hash `9859adb2…` unchanged throughout → public `bc0500bd…` and registration `dea510df…` stay final (member check in gates_r3/gates_r4).

| step | result |
|---|---|
| gates_r3 (F1 tree `26e02259…`) | public 1190/0 (clean); owner 1663/0 (F1b landed mid-run → F1+F2 regression evidence only); static gates exit 0 |
| gates_r4 (tree `8fc93f2a…`, hash before == after) | public **1190 passed / 0 failed** (1 skipped, 44 deselected); owner **1686 passed / 0 failed** (8 skipped, 47 deselected = 1604 + 19 + 40 + 23); closure 0/0/0 + 1 exception; placement 221/221 (675 manifest rows); generators --check OK; openapi.json `70e0cffa…` + gen-types; snapshot 603/603; t2 10/10 + 32 passed; stale bytecode removed first |
| owner_d build | `9f03c484bbc8c0a2227cc325658892e3efe286d08cca9a1978383158075c2f2a`, 85,188,530 B, 3693 members, build id r-07c7cef95cd73775; x2 byte-identical (~60 s each after DD-A74, was 280 s); 0 violations / 0 warnings; FILE_MANIFEST 3692 rows, 0 digest mismatches; wheel gate 5/5 (`mbnas_core` wheel `2348b6e3…`) |
| FILE_MANIFEST diff vs `53e6a1f0…` | 451 added / 4 removed / 21 changed; outside docs/internal exactly projection.py, wp3_three_process_smoke.py, the mbnas_core wheel, 5 new owner tests, build metadata; removed = 4 T8 owner scan dumps (DD-A74); README_DD_A74.md x3 added; no unexpected member |
| canary (owner_d) | 4000 files, 0 canary hits, 0 failing (9 s); identical 38-hit owner-only legacy list; positive control 8/8 |
| T8 static (owner_d) | 3937 files, 17 s; dump 4.6 + 2.8 MB kept in owner_d/t8 (never shipped, DD-A74); 25 legacy items + 5 made-up `/home/<name>/` fixture lines in the new owner tests (scrubber inputs, not host facts) |
| T6 owner attempt4 (`9f03c484…`, fresh spaced-path extraction, external cwd) | install --offline 0; start 0; doctor --strict 32 PASS; unit **2829 / 0 failed** / 56 skipped / 91 deselected; integration **17/17**; relocation 8/8 (215 rows ROOT, caller env []); package 3692/3692; stop 0; cwd `/` re-invocation 0/0/0; secret self-scan 227 files 0 hits; interrupted first run kept as attempt4_interrupted/ (stale HOLD message; its stack was shut down cleanly at 15:22:10-12 by an unidentified stop — 원인 미상) |
| P2 T4/T5 attempt_dd_a75 | F1/F1b: 0 runner/owner path fragments (blank+unicode roots, non-circular detector) on snapshot / detail / SSE / export in both stacks; F2: verify integration 17/17 in the GPU-less sandbox; ETRI generated-config 200 → COMPLETED both stacks; relocation 8/8 at long (blank) and short paths; T8 0 hits; DB v6 |

dist/ after this round: public `bc0500bd…` (unchanged), registration `dea510df…` (unchanged), owner **`9f03c484…`** (85,188,530 B, 3693) — code-final; a docs-only owner rebuild (DD-A70) will follow the record closure and must show a FILE_MANIFEST diff limited to docs/internal/** + build metadata. Superseded owner ZIPs: a43a8a2e…, 867cc577…, 53e6a1f0….

Not verified: public/registration T6/T8 not re-run on this tree (members byte-identical); F3 (long blank-free install path entry_points false FAIL) not re-tested on this ZIP; no training job / SSE in T6 (T4/T5 cover); requirements-cpu.txt offline and registration under unshare -rn untested.

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

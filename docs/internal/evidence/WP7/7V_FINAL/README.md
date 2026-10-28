# WP-7V P1-final evidence — regression gates, final ZIPs, T6, T8 §2, offline (2026-10-28 06:4x–07:4x KST)

> **설계자 주석(2026-10-28 09:5x):** 이 패스의 게이트 `.meta`에는 트리 해시가 기록되지 않았다. 빌드 시각의 소스 트리는 owner ZIP의 BUILD_INFO/PACKAGE_PROFILE 값과 fix-3 이전 상태로부터 `5a7bc592…`로 **추정**된다(직접 기록 아님). 이후 fix 3·4·5(DD-A64/A65/A66/A68)로 트리가 바뀌어 이 패스의 ZIP 3종은 **구판**이며, 릴리스 상태는 `docs/internal/evidence/WP7/PFINAL2/`(P-final-2, 트리 해시를 모든 .meta에 기록)가 대체한다.


The final packaging pass ran after the coordinator reported that all fix rounds had landed and no one was editing
the tree. Everything ran on the CPU only (`CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`,
`PIP_CONFIG_FILE=/dev/null`). pip was limited to `--no-index --find-links build/wheelhouse`, there was no GPU work,
and nothing under /data was touched. The ports were 18800/18810/18811/18812, and only processes started here were
stopped. Results table: `docs/internal/VALIDATION_REPORT.md`.

**Overall: every gate, build, scan, T6 step, T8 check and offline install passed. There are 0 failed tests.**

## 1. Regression gates on the source tree (must be 0 failed before any build)
| check | command | result |
|---|---|---|
| public suite | `run_gates_final.sh`, the same commands as WP-4C `run_gates.sh`: `pytest tests/public -m "not gpu and not live and not legacy_records and not host_data" --timeout=180` | **1124 passed, 0 failed**, 1 skipped, 44 deselected (294 s), exit 0 (`gate_public.txt/.meta`) |
| owner gate | same script: `pytest tests/owner -m "…" --timeout=180` | **1569 passed, 0 failed**, 8 skipped, 47 deselected (880 s), exit 0 (`gate_owner.txt/.meta`) |
| import closure | `python -B scripts/dev/check_import_closure.py` | exit 0, ok: 0 violations / 0 unresolved / 0 forbidden strings, 1 documented exception (base.py, DD-A58) (`import_closure.txt`) |
| placement coverage | `python -B scripts/dev/placement_coverage.py --format text` | `ok`, exit 0: 221/221 package files covered (0 missing, 0 deleted-but-present, 0 unrecorded deletions, 0 row errors; sha drift 48, informational) (`placement_coverage.txt`) |
| T2 equivalence | `python -B scripts/owner/t2_compare.py --work-root <scratchpad>/t2_work --out-dir docs/internal/evidence/WP7/7V_FINAL/t2` | exit 0 in 77 s: **10/10 non-mismatch**, 3 EXACT_MATCH (T2-4/5/9), 7 ALLOWED_DIFF, **0 MISMATCH**; source-snapshot guard unchanged (sha 1475baa5…) (`t2_compare.txt`, `t2/`) |
| T2 pytest | `MBNAS_TEST_T2=1 python -B -m pytest tests/owner/t2 --timeout=900` | **32 passed**, exit 0 (`pytest_t2.txt`) |

## 2. Final ZIPs (`bash scripts/package.sh --profile <p> --reproducible`, built in the order public → registration → owner, one at a time)
| ZIP (`dist/<profile>/`) | sha256 | bytes | staged files | checks |
|---|---|---|---|---|
| `mbnas_application-0.1.0-public-runtime-linux_x86_64.zip` | `ce34b821806a27cd2b2ff461d8020e85e0350d846d9fae6188c15f0d09bc2a2b` | 2 638 943 | 466 | two builds IDENTICAL, 0 violations, 0 warnings |
| `mbnas_application-0.1.0-registration-runtime-linux_x86_64.zip` | `95b541e9aa8a95556bb4ae3b74c4ffa59e47977ad1c260d5656dc493144fecb1` | 2 709 965 | 476 | IDENTICAL, 0 violations, 0 warnings |
| `mbnas_application-0.1.0-owner-private-runtime-linux_x86_64.zip` | `79cce02a8d36eec17a24a5d43228b02fa32832ff7ca9499a9035c3c0f56d9c20` | 50 212 669 | 2115 | IDENTICAL, 0 violations, 0 warnings |

- Every `dist/<profile>/` holds only the final ZIP and its `.sha256`, and `sha256sum -c` reports OK for all three
  (`31_dist_listing.txt`, `SHA256SUMS_final.txt`).
- The public ZIP was built twice:
  - **Build A** (`10_package_public_A.log`, sha `bbe5e29f…`) was installed under `unshare -rn`, which exited 0
    (§5).
  - Its record `offline_install_public_final.json` went into `build/allowlists/public.txt`
    `[profile] offline_install_evidence`, and the **final** public ZIP was then built (`11_package_public_final.log`).
  - `12_public_A_vs_final_members.txt`: A and the final ZIP have the same members. Only `PACKAGE_PROFILE.json`,
    `BUILD_INFO.json` and `FILE_MANIFEST.jsonl` differ.
  - The final PACKAGE_PROFILE says `offline_install: true`, with `offline_install_verified_zip_sha256` = A.
- Scope changes since the first pass, all decided by others and only checked here:
  - `scripts/public/**` is now shipped in all three ZIPs (DD-A59 b).
  - The deny lines for `tests/public/contracts/test_contract_history.py` and `tests/public/tools/smoke_api_only.py`
    are gone (coordinator request).
  - The web README is included (DD-A50).
  - The oracle closure is still 4 files (DD-A43 a / DD-A50).
- Canary scans, manifest + profile mode (`30_canary_scan_<p>.{txt,json}`):
  - public: 614 files, **0 hits**. registration: 624 files, **0 hits**. Both include the new HOST_FACT#6 (original
    host GPU UUID) needle.
  - owner: 2382 files, **0 canary hits, 0 failing**. There are 3439 informational host-fact hits: account name 2982,
    original root 241, GPU UUID 75, data root 68, host name 63, address 10. The owner profile does not fail on host
    facts by design.
  - The 75 GPU-UUID hits are all in owner-only `docs/internal/evidence/**` and `tests/owner/t2/t2_core.py`: T2
    reference/new ledgers under T2/, WP4/4C/t2 and this pass's `t2/`, plus gate logs.
- Packaging tests: `pytest tests/public/packaging tests/public/infra/test_import_closure.py` gave **52 passed**, and the
  `dist/` sha256s were unchanged afterwards (`32_pytest_packaging.txt`).

## 3. T6 relocation from the final ZIPs (`T6/`)
- **Layout.** Fresh paths with spaces: `runtime/test_root/t6/final/{mbnas public final, mbnas registration final,
  mbnas owner final}/mbnas_application`. Every command ran from the external cwd `<scratchpad>/t6final`.
- **Install.** `install_final.sh` ran in parallel:
  `bash "<tree>/scripts/install.sh" --profile <public|owner> --offline --wheelhouse <SRC>/build/wheelhouse --python <py3.10>`.
  All installs exited 0 (`40_install_<p>.txt`).
- **Configuration.** Ports go into the env files. The public and registration trees get server and runner principals
  issued by the owner core; the tokens are moved file to file and never printed. Script: `scripts/final_driver.py configure`.
- **Starts.** Every service was started with a service-like environment (see `T8/README.md`).

| step | public | registration | owner |
|---|---|---|---|
| doctor --strict before start | 0 | 0 | — |
| `start.sh --profile <p>` | 0 | 0 | 0 (core + owner worker + gateway + runner) |
| `doctor.sh --strict` | 0 (23 PASS / 0 WARN / 0 FAIL) | 0 (23 PASS) | 0 (28 PASS) |
| API through the gateway | health 200 (core ok; allowlists = own empty roots), no token 401, presets/registry/`/` 200, upload 200, structure-check PASS / broken YAML FAIL `SHAPE_MISMATCH`, validate 200 valid, run create 200 → COMPLETED with 2 candidates, snapshot / candidate detail / config-revisions / export (11 637 B) / runs 200 | health 200 (own roots), presets 200, `/` 200, upload 200, structure-check PASS | (served both public stacks) |
| `mbnas_core` not loadable in the venv | 0 | 0 | — |
| `verify.sh --suite unit,relocation` (owner: `unit,integration,relocation`) | exit 0: **unit 717 passed / 0 failed** / 222 skipped (221 DD-A43(b) "owner-only tool not in this bundle", 1 "mini run absent") / 44 deselected; relocation 8/8 | exit 0: **unit 717 / 0 failed** / 222 skipped / 44 deselected; relocation 8/8 | exit 0: **unit 2657 passed / 0 failed** / 45 skipped / 91 deselected (19 min); integration 17/17; relocation 8/8 |
| `verify.sh --suite package` (`MBNAS_CANARY_SCAN=<SRC>/scripts/dev/canary_scan.py`) | exit 0: manifest 465/465, scan 0 hits | exit 0: manifest 475/475, scan 0 hits | — |
| `stop.sh` | 0 | 0 | 0 |

Two more points:
- `test_subset_cli_unchanged_interface`, the first pass's only failure, now passes in the public bundle because
  `make_pilot_subset.py` is shipped.
- Re-invocation from a second external cwd `/` succeeded: owner `start.sh --role core` 0, public
  `start.sh --role gateway` 0, health 200 (own roots), relative `bash scripts/doctor.sh` from inside ROOT 0, both
  stops 0. Afterwards no service process of the final trees was left, ports 18800/18810/18811/18812 were free and
  every `runtime/pids/` was empty.

## 4. T8 §2 runtime (`T8/README.md`)
**PASS.**
- Health allowlists = the gateway's own roots.
- 0 secret, canary or owner-side hits in responses, service logs and `/proc` environments.
- After the tree's own root and the client-supplied output_root are normalized, responses and service logs have 0
  host-fact hits.
- The /proc environments contain only `OLDPWD`, the invoking shell's cwd. From cwd `/` there are 0 hits.
- Install logs echo only the operator's `--wheelhouse`/`--python` paths.

## 5. Offline install (`20_…`, `21_…`, `22_…`, `offline_install_public_final.json`)
- `unshare -rn bash "<T6>/final/mbnas offline A/mbnas_application/scripts/install.sh" --profile public --offline
  --wheelhouse <SRC>/build/wheelhouse --python <py3.10>` from the external cwd exited 0, with 138 wheels processed.
  That run used build A.
- The same command on the **final** ZIP, in `mbnas offline final`, also exited 0 (`22_…`).
- Network proof (`21_unshare_network_proof.txt`): only `lo` exists, DNS fails and 1.1.1.1 is unreachable. Both
  offline trees import the 4 public packages under `unshare -rn`, and `find_spec("mbnas_core")` is None.
- `offline_install: true` is kept for public: the third-party wheels are external and passed with `--wheelhouse`.
  Registration stays `false` (not tested under `unshare -rn`).

## Files changed in this pass (WP-7V-owned)
- `build/allowlists/public.txt`: `offline_install_evidence` now points to `WP7/7V_FINAL/offline_install_public_final.json`.
- `docs/internal/VALIDATION_REPORT.md`: rows T6, T8, offline and regression.
- `docs/internal/evidence/WP7/7V_FINAL/**`.
- `dist/{public,registration,owner}/`: the final ZIPs.
- Scratch trees: `runtime/test_root/t6/final/*`. Other agents' trees were not touched.

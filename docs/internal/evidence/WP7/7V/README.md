# WP-7V evidence — release ZIPs, T6 relocation, T8 leak, offline install (2026-10-28 KST)

Scope: the P1 task, covering WP-7 §4, WP-8 §1 T6/T8, DD-A41, DD-A43 and DD-A50. Everything ran on the CPU
(`CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `PIP_CONFIG_FILE=/dev/null`) with pip limited to
`--no-index --find-links build/wheelhouse`. No GPU was used. The ports were 18700/18710/18711/18712, and only processes started by
this run were stopped. Results table: `docs/internal/VALIDATION_REPORT.md`. Run details: `../../T6/` and `../../T8/`.

## 1. Pre-build changes (P stage, first 10 minutes)
| change | file | check |
|---|---|---|
| `mbnas_web/README.md` added to `[include]` (DD-A50 P stage), in public, registration and owner | `build/allowlists/{public,registration,owner}.txt` | present in every ZIP. Grep for host facts in the README: 0 |
| obsolete DATA_EXCEPTION `openapi.json` / `MBNAS_server` removed. The 1.5.0 contract has 0 occurrences, and the WP-4C note lists only the base.py exception as left | `scripts/dev/check_import_closure.py` | `closure_before.txt` / `closure_after.txt`: both exit 0, 0 violations / unresolved / forbidden strings, `excepted` 1 (base.py legacy hook) |

## 2. ZIPs
Built one profile at a time with `bash scripts/package.sh --profile <p> --reproducible`, in the order public,
registration, owner. Every build exited 0, produced two IDENTICAL builds and had 0 violations and 0 warnings. Info
counts: public 45, registration 52, owner 2294. Logs: `package_<p>.log`. Checksums: `SHA256SUMS.txt`.

| ZIP | sha256 | bytes | staged files |
|---|---|---|---|
| public (tested: T6, offline, T8) | `4174060dde93ce8aa941f2cbd36fcb8b0230f5f184e68f8ccbc71d49bcd9ae86` | 2 610 337 | 454 |
| public (current `dist/`, rebuilt after the offline record: `package_public_offline_flag.log`) | `529243cfe506ee6aa39ed74eb8939e34844eeda0562367b16a77d46180e96792` | 2 620 121 | 454 |
| registration | `d5748c136086081522c05f4f91440a9407d435623700fcdc7de16eedfe4e4e9b` | 2 678 548 | 464 |
| owner | `ba8ed91f601330e1272bc89fd217bba81fbc5ee943cfb8f45f353795da787df3` | 18 403 394 | 1350 |

- `public_rebuild_diff.txt` compares the tested public ZIP with the current one. No member was added or removed. The
  differences are the 3 generated metadata files plus docs and README edits that other agents made between the two
  builds (README.md, 6 × docs/public/*.md, `mbnas_web/e2e/real-server.spec.ts`). Wheels and code are identical.
- DD-A50 oracle closure: PACKAGE_PROFILE `exclusions_with_reason` lists exactly 4 files. They are `tests/fitness_oracle.ts`,
  `tests/fixtures_oracle.ts`, `tests/fixtures_oracle.test.ts` and `e2e/weights.spec.ts`, the same list as the
  `OWNER_ONLY` list of `mbnas_web/tests/oracle_isolation.test.ts`.
- Canary scans use manifest + profile mode (`canary_scan_<p>.{txt,json}`, plus `canary_scan_public_tested_4174060d.*`).
  Public scanned 602 files and registration 612, with 0 hits each. Owner scanned 1596 files with 0 canary hits; its
  host facts are informational for the owner profile. All four scans PASS.
- Packaging tests: `pytest tests/public/packaging tests/public/infra/test_import_closure.py` gave 52 passed before the
  packager change (`pytest_packaging.txt`) and 52 passed after it (`pytest_packaging_after_offline_flag.txt`).

## 3. Offline install
- `install_all.sh` runs the three T6 installs in parallel from the external cwd: public, owner, and the
  `unshare -rn` public install. All three exited 0. Logs: `install_public_t6.txt`, `install_owner_t6.txt`,
  `install_public_offline_unshare.txt`, and later `install_registration_t6.txt`, also exit 0.
- `unshare_network_proof.txt`: inside the namespace only `lo` exists, DNS fails and 1.1.1.1:443 is unreachable. The
  offline tree imports its 4 packages under `unshare -rn` from an external cwd, and `find_spec("mbnas_core")` is None.
- `offline_install_public.json` is the record the packager reads, through `[profile] offline_install_evidence` in
  `build/allowlists/public.txt`. The current public ZIP's PACKAGE_PROFILE therefore says `offline_install: true`,
  with a note that the third-party wheels are external and passed with `--wheelhouse`, plus the sha of the tested ZIP.
  Negative checks are in `offline_flag_negative_checks.txt`: a record for another profile, with exit≠0 or with the
  network on stops the build with exit 2, and a missing record keeps `false`. Registration stays `false` because it was not
  tested under `unshare -rn`.

## 3b. Allowlist change for the final build (coordinator request after WP-4C)
- The `[deny]` lines for `tests/public/contracts/test_contract_history.py` and `tests/public/tools/smoke_api_only.py`
  have been dropped from `public.txt` and `registration.txt`. Nothing else was excluded or deleted.
- `hostfact_scan_two_test_files.{txt,json}`: the two files, manifest + `--profile public`, 0 hits, PASS.
- `canary_build_{public,registration}_after_deny_drop.log`: `bash scripts/package.sh --profile <p> --canary` (tree
  copy, planted canaries, build in `build/canary/<p>/tree`; `dist/` untouched). Both exited 0 with 0 violations
  (`host_facts = fail`), and the scans checked 607 and 617 files with 0 hits. The two files are in the test ZIPs.
  `canary_build_public_vs_tested_members.txt` lists the member delta against the tested public ZIP: those two files
  plus web files that other agents added meanwhile (a rebuilt dist bundle and 3 checkpoint test/e2e files).
- The ZIPs in `dist/` (table above) were built before this change. The final rebuild comes after the fix round, on
  the coordinator's signal.

## 3c. T6 results (details in `../../T6/`, table in VALIDATION_REPORT)
- public: install, doctor --strict before and after start, start and the API flow all succeeded. `verify unit,relocation` exited 1:
  unit had 633 passed and 1 failed, `test_subset_cli_unchanged_interface`, because `scripts/public/make_pilot_subset.py` is not
  shipped. 215 tests skipped, 214 of them with the DD-A43(b) reason. Relocation passed 8/8. stop 0.
- registration: the same results.
- owner: `verify unit,integration,relocation` exited 0 (2537 passed / 0 failed; integration 17/17; relocation 8/8).
  start, doctor --strict and stop all exited 0.
- Re-invocation from a second external cwd: every step exited 0, health returned 200, and no processes were left over.

## 4. Changed files (owned by WP-7V)
- `build/allowlists/{public,registration,owner}.txt`: web README include. `public.txt` also gets
  `offline_install_evidence`. `public.txt` and `registration.txt` lose the two WP-4C test deny lines (§3b).
- `scripts/dev/check_import_closure.py`: the expired DATA_EXCEPTION is dropped.
- `scripts/dev/package_build.py`: optional `[profile]` key `offline_install_evidence` plus `offline_install_doc()`.
- `docs/internal/VALIDATION_REPORT.md` (new), `docs/internal/evidence/{WP7/7V,T6,T8}/**`.

## 5. Not verified here (확인 못 함)
- The optional `-public-runtime-linux_x86_64-offline` variant with bundled wheels was not built.
- Registration under `unshare -rn` was not tested.
- An offline install of the owner ZIP under `unshare -rn` was not run. The owner tree was installed `--offline` with the
  network present.

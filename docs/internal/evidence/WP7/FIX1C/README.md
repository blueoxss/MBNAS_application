# FIX1C: fix round 1c (DD-A59 a/b), evidence

Date 2026-10-28 KST. CPU only (`CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `PIP_CONFIG_FILE=/dev/null`, interpreter
`runtime/venv/bin/python -B`). No GPU work, nothing under the research data root was touched, and no service was started.
Paths in the logs are normalised: `<ROOT>` is this project, `<SCRATCH>` is the session scratchpad, and host-fact strings
are redacted.

Result: both DD-A59 defects are fixed in the source tree, and the requested suites and the public packager pass. Not
covered: the T8 section 2 runtime re-run and a T6 re-install (section 5).

## 1. Changes (diffs: `01_health_fix.diff`, `02_subset_cli.diff`, `03_allowlists.diff`)

| file | change |
|---|---|
| `mbnas_server/src/mbnas_server/api/relay.py` `health()` | `allowlists` is always the gateway's own `dataset_roots` / `output_roots` (empty lists when unset) plus its `archive_roots`. The fallback that passed the core's `allowlists` through when the gateway had no roots (the T8 leak) is gone. The rest of the merged health is unchanged. |
| `tests/public/server/test_health_allowlists_split.py` (new) | A stub core (`tests/_shared/stub_core.py` through `httpx.ASGITransport`) answers health with absolute core-host roots plus a key that is not in the contract (`model_yaml_roots`). The test is parametrized over gateway roots `empty` and `set`. It asserts that `allowlists` equals the gateway's roots exactly, that no core needle appears anywhere in the response text, that the rest of the merged health is kept (core summary, status, warnings, versions, commit, gateway self status, request id), and that the result passes the `Health` contract. It does not use the owner harness, so it also runs from the public and registration bundles. |
| `tests/public/server/test_routes.py` | The strict xfail is removed from `test_health_reports_archive_roots`, which now passes. One assertion was added: the exact `allowlists` (archive roots from the gateway settings, empty dataset and output roots). |
| `scripts/public/make_pilot_subset.py` | `--source` and `--out` are `required=True` (with help text), and the host default paths are removed. All other options, defaults and outputs are unchanged. The docstring names the real module (`mbnas_model_runtime.datasets.subset`) and gives a placeholder example. Host-fact grep: 0. |
| `tests/public/model_runtime/test_datasets.py::test_subset_cli_unchanged_interface` | All original assertions are kept. Added: the exact option set taken from `--help`; the three missing-argument calls must exit 2 with argparse's message and create no output; the exact stdout lines; the default names and role counts; the `[skip]` line on a re-run; parsing of `--names`. |
| `build/allowlists/public.txt`, `registration.txt` | `scripts/public/**` added to `[include]`. |
| `build/allowlists/owner.txt` | The same line in its "public-runtime part (same as public.txt [include])" block. This edit is outside the task's file list (section 4). |

## 2. Verification (commands actually run)

| check | result | evidence |
|---|---|---|
| `pytest -m "<standard>" tests/public/server` | 530 passed, 1 skipped (`mini run absent`), 25 deselected, 0 failed, 0 xfail | `08_pytest_public_server.log` |
| `pytest -m "<standard>" tests/public/model_runtime/test_datasets.py` | 31 passed, 1 deselected | `09_pytest_test_datasets.log` |
| `pytest -m "<standard>" tests/public/packaging` | 42 passed | `10_pytest_packaging.log` |
| owner cross-check (run only): `tests/owner/api/test_contract_responses.py`, `test_set3_health.py` | 7 passed. The combined harness gateway mirrors the core roots, so `allowlists.output_roots` is still non-empty there. | `11_pytest_owner_health_crosscheck.log` |
| `bash scripts/package.sh --profile public` (the packager has no dry-run or scan-only option), run once | exit 0. 0 violations, 0 warnings, 47 info. 461 staged files. Content scan of 605 files: 0 hits. Findings for `scripts/public/**`: none at any severity (private reference, host fact, secret). ZIP sha256 `b095c565…`. PACKAGE_PROFILE still has `install.offline_install = true`. dist was left as built. | `06_package_public.log`, `06_package_public_summary.json` |
| subset CLI before/after on the same synthetic source | Same option set. Same stdout, `[skip]` re-run and "not enough images" error. `data.yaml` identical after path normalisation, `manifest.json` byte-identical, images and labels identical. A call missing `--source` or `--out` now exits 2. | `04_cli_before_after.txt` |
| tests run from the extracted rebuilt public ZIP | `test_subset_cli_unchanged_interface` PASSED (this was the T6 failure), and both split-health rows PASSED. `test_health_reports_archive_roots` was skipped (owner harness not shipped, DD-A43 b). | `07_bundle_tests.txt` |
| `canary_scan.py --manifest docs/internal/canary/CANARY_MANIFEST.json` over this directory and the 8 changed files | 22 files (14 here, 8 changed files), 0 hits (canary tokens and host facts) | `12_canary_self_scan.txt` |

`<standard>` = `not gpu and not live and not legacy_records and not host_data` (the marker set of `scripts/verify.sh`).

## 3. Negative controls (`05_negative_controls.txt`)

- Health: the new tests were run against a scratch copy of the package that still has the pre-fix `relay.py`, put on
  `PYTHONPATH`; the repository was not touched. Result: `[empty]` FAILED because the core's roots and its
  `model_yaml_roots` came through, `test_health_reports_archive_roots` FAILED with `KeyError: 'output_roots'` (the
  reason for the old xfail), and `[set]` passed (the old code already replaced the allowlists when the gateway had
  roots). The new test therefore catches the T8 case.
- Subset CLI: the updated test was pointed at a tree that holds the pre-fix script. It FAILED: without `--source` and
  `--out` the old script used its host default paths and exited 1, where exit 2 is expected.

## 4. Deviation: `build/allowlists/owner.txt`

`tests/public/packaging/test_package_build.py::test_real_allowlists_keep_the_profile_boundaries` requires every literal
in public `[include]` to appear in owner `[include]`. With `scripts/public/**` added only to public and registration,
that test failed (`Extra items in the left set: 'scripts/public/**'`). I added the same line, with the same comment,
to owner.txt's public-runtime mirror block. For the owner ZIP this changes nothing, because `scripts/**` is already
included. The packager agent is idle, so no edit can collide. If the designer does not want this, revert the 3-line
hunk in `03_allowlists.diff`; the boundary test will then fail again.

## 5. Not covered (확인 못 함)

- T8 section 2 runtime re-run (`T6/t6_t8_driver.py up` followed by `python -I T6/t8_scan.py`), required by DD-A59 (a)
  after the fix: not run in this task. The unit test plus the negative control cover the relay logic only, not a
  running public stack.
- T6 public and registration `verify.sh --suite unit` from a ZIP installed into a fresh tree: not run. Only the targeted
  tests ran from the extracted public ZIP, using the build venv (mbnas packages from the source tree's editable
  installs, not the ZIP's wheels).
- The registration ZIP was not rebuilt (the task asked for the public build only). `dist/registration` (03:03) and its
  generated `docs/registration/FILE_LIST.md` still lack `scripts/public/make_pilot_subset.py` until the next
  registration build.

## 6. Notes

- `make_pilot_subset.py` still has its ImportError fallback `sys.path.insert(0, parents[1] / "src")`, which points at
  the pre-split layout (`scripts/src`, already noted in PLACEMENT_MANIFEST at WP-1). It is left unchanged because it is
  outside "options and output". It does no harm, since the installed packages are imported first.
- In an owner all-in-one deployment that leaves `MBNAS_GW_DATASET_ROOTS` / `MBNAS_GW_OUTPUT_ROOTS` empty, the web
  Settings line "Server allowlists" now shows "—" where it used to show the core's roots. DD-A59 intends this. The
  core's roots remain visible owner-side (core doctor `allowlists`, `/core/v1/owner/*`).
- `docs/registration/SYSTEM_STRUCTURE.md:182` still lists `scripts/public/make_pilot_subset.py` as "owner 전용(공개 ZIP
  미포함)". That is no longer true; the doc owner needs to update it.

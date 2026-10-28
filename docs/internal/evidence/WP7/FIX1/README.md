# Fix round 1 - evidence of agent FIX1 (2026-10-28 KST)

Scope: the items of `docs/internal/FIX_ROUND_1.md` assigned to this agent (ownership list of the task). Conditions:
CPU only (`CUDA_VISIBLE_DEVICES=""`), `python -B`, `PYTHONDONTWRITEBYTECODE=1`, `PIP_CONFIG_FILE=/dev/null`; pip only
through `install.sh --offline` (`--no-index --find-links build/wheelhouse -c constraints.txt`); pytest with
`MBNAS_TEST_ROOT` and `--basetemp` in the session scratchpad and the standard markers
`not gpu and not live and not legacy_records and not host_data`. No GPU, nothing under /data or etri_nas, no git
command that changes the index, no process left running.

## Items

| item | status | change | evidence |
|---|---|---|---|
| C04-D1 | done | new `tests/owner/search/test_generation_recovery_independent_journal.py` (GENERATION_STATE_INVALID: other protocol_hash / other run_id / corrupt journal; GENERATION_BUSY + release control) | `pytest/owner_search.log`, `line_traces.json` (generation.py 107-109, 113-118 executed) |
| C04-D3 | done (docstring) | `mbnas_core/.../runner_api/routes.py` module docstring: the PROCESSING row is never committed by the single-tx E8 route; 202 + sweeper cleanup = defensive path for external/older writers | compile check; `pytest/owner_runner_protocol.log` |
| C03-D1 | done | `docs/internal/evidence/WP4/TEST_ADAPTATIONS.md` part A (DD-A57) | - |
| C03-D3 | done | new `tests/owner/api/test_generation_real_cpu_training_status.py` (real OFF run: snapshot + every detail UNTRAINED) | `c03d3_run.txt`, `pytest/extras.log` |
| C03-D4 | done | new `tests/owner/search/test_ga_same_family_crossover.py` | `pytest/owner_search.log`, `line_traces.json` (ga.py 126, 141-142 executed) |
| C03-D5 | done | new `tests/owner/orchestration/test_semantics_baseline_ungated.py` | `pytest/owner_orchestration.log`, `line_traces.json` (engine.py 577-578, 700-704 executed) |
| common, 4C-6 | done | 18 stale `__pycache__` directories deleted (51 tracked `.pyc`, every one embedding the account name in `co_filename`); `scripts/dev/__pycache__` did not exist any more | `deleted_pycache.txt` |
| 4C-1 | already applied before this round (VALIDATION_REPORT "Allowlist change for the final build"); verified | none by FIX1 | both deny lines absent from `build/allowlists/{public,registration}.txt`; canary_scan host facts in the two files: 0 |
| 4C-4 | done | draft contract and the empty `openapi/drafts/` deleted; `WP4/4A/DRAFT_DELETED_FIX1.md`; PLACEMENT_MANIFEST `deleted` row | `pytest/public_infra_rerun.log` (placement coverage 65/65) |
| 4C-5 | the openapi.json exception was already gone (P1); the base.py exception stays, label now `1.6` (C06-D3) | - | `import_closure.json.txt` |
| 4C-3 | already recorded in DD-A58; nothing to change | - | - |
| C06-D1 | done | `mbnas_runner/config.py` (rule below), `scripts/install.sh` (runner principal id), `test_value_rules.py` (rule), `projection_world.py` (default-id runner), `test_settings_split.py` (adapted), docs COMPATIBILITY.md:25, API.md:98, SECURITY_MODEL.md:71 | `c06d1_repro_before_fix.txt` (FAIL on `runner-dev1`), `c06d1_after_fix.txt` (12 passed), `install_*` |
| C06-D3 | done | `scripts/dev/check_import_closure.py` DATA_EXCEPTIONS `expires` WP-4 -> `1.6` + DD-A58 basis (and the two comment/docstring mentions) | `import_closure.json.txt` |
| C06-D4 | done | docstrings: model_runtime checkpoint.py:16, genotype.py:17, core retention.py:17 (docstring line only; file edited concurrently by the DD-A55 agent), contracts protocol_hash.py:18 | compile checks |
| C06-D5 | done | COMPATIBILITY.md:12, SECURITY_MODEL.md:102: GPU result of DD-A54 (validated, with limits) | - |
| C06-D6 | done | `docs/internal/criteria/C06.json` 1.0.1: two test paths corrected, reason in `version`; criteria unchanged | - |
| C03-D2, C06-D2, P1-T6, P1-T8 | not this agent (P stage / P2 / P1) | - | - |

## Runner id rule implemented (C06-D1)

`RunnerSettings.runner_id` = `MBNAS_RUNNER_RUNNER_ID` (or an explicit value) when given; otherwise
`mbnas_runner.config.persistent_runner_id()`: the id stored in `mbnas_home()/runner/runner_id` (`runtime/runner/runner_id`
for an install.sh tree), created on first need as `runner-` + `secrets.token_hex(4)` (8 lower-case hex digits), written
to a temp file (mode 0644 under umask) and hard-linked into place (concurrent first starts agree; no partial read),
then read back by every later settings object. A file that does not hold `runner-[0-9a-f]{8,}` or cannot be
created/read is a `ValueError` (settings validation error), never silently replaced, because the runner principal is
bound to the stored id. Nothing derived from the host goes into the id. `scripts/install.sh` resolves the id with the
runner's own `RunnerSettings.load(config/<profile>/runner.env)` (helper `runner_id_of`): owner profile - the runner
principal is issued for it; public profile - the "NOT SET" hint prints `--runner-id <that id>` for the core operator.
`runner.env` is not changed (no `MBNAS_RUNNER_RUNNER_ID` line is written).

Probe (scratch): id stable across settings objects, file content equal, mode 0644; explicit/env id wins; 32 concurrent
first resolutions -> 1 distinct id, no temp file left; a file holding `runner-<hostname>` refused; read-only runtime root
refused; `RunnerSettings()` surfaces both as `ValidationError`.

## Test runs (tree `sha256:2b888cb7...3c8cfd` before and after the main run)

| run | result | log |
|---|---|---|
| `tests/owner/search` | exit 0, 153 passed | `pytest/owner_search.log` |
| `tests/owner/orchestration` | exit 0, 34 passed | `pytest/owner_orchestration.log` |
| `tests/owner/projection` | exit 1, 258 passed, 1 failed: `test_t8_canary_scan.py::test_t8_no_original_host_fact_in_public_responses` `assert len(facts) == 4` - another agent added HOST_FACT#6 to `scripts/dev/canary_scan.py` at 04:03:49 (C05 D8); the leak check itself on the world built with this round's changes: 0 hits for facts #1-#4 and #6 in 49 public captures (`t8_hits.py`) | `pytest/owner_projection.log` |
| `tests/owner/runner_protocol` | exit 0, 55 passed | `pytest/owner_runner_protocol.log` |
| `tests/public/infra` | first run exit 1 (placement coverage: the deleted draft had no `deleted` row); after appending it: exit 0, 65 passed | `pytest/public_infra.log`, `pytest/public_infra_rerun.log` |
| `tests/public/runner` | exit 0, 110 passed, 9 deselected | `pytest/public_runner.log` |
| extras: `tests/owner/unit/test_settings_split.py`, `tests/owner/api/test_generation_real_cpu_training_status.py`, `tests/owner/api/test_generation_real_cpu.py` | exit 0, 17 passed | `pytest/extras.log` |
| `tests/public/ops` (install.sh changed) | exit 0, 59 passed, 2 deselected (live) | `pytest/public_ops.log` |
| `tests/owner/training` (uses the default runner id via `runner_settings`) | exit 0, 18 passed, 4 deselected (gpu) | `pytest/owner_training.log` |
| `tests/owner/worker` (extra; owner_worker files were being edited by the round-2 agent at 04:15-04:17, before this run) | exit 0, 34 passed | `pytest/owner_worker.log` |
| `scripts/dev/check_import_closure.py` | exit 0: ok, violations 0, unresolved 0, forbidden 0, excepted 1 (`expires 1.6`) | `import_closure.json.txt` |
| `bash scripts/install.sh --help` | exit 0 | `install_help.txt` |

## Install smoke (scratch copy `runtime/test_root/fix1/owner/mbnas_application`, rsync of the source tree at 04:08:22)

`bash scripts/install.sh --profile owner --offline --wheelhouse <ROOT>/build/wheelhouse --python <mbnas310 python3.10>`:
exit 0, 108 s (`install_owner_offline.txt`). `verify_install_runner_id.py` (`install_runner_id_check.txt`, ALL_PASS):
`runtime/runner/runner_id` = `runner-88fb1032` (0644); the only runner principal `pr-dbbb89b0b7339eb8` is bound to it
(runners row `runner-88fb1032`); `RunnerSettings.load(config/owner/runner.env)` gives the same id; runner.env sets no
`MBNAS_RUNNER_RUNNER_ID`; the host name occurs in neither the id, the principal, runner.env nor the id file. Then
`--profile public` on the same root (venv reused, exit 0, `install_public_same_root.txt`) printed
`mbnas-core-principal create --kind runner --runner-id runner-88fb1032`. No secret value occurs in any transcript
(checked against the 9 generated/issued values). Before the fix the same flow produced `runner-<hostname>` (T4 capture
`runner-t4-sandbox`, C06 EVAL_1 D1).

## Deleted (working tree only; git index entries are the user's step)

- `mbnas_contracts/src/mbnas_contracts/openapi/drafts/openapi-1.5.0.draft.json` (+ empty `drafts/`): see
  `docs/internal/evidence/WP4/4A/DRAFT_DELETED_FIX1.md`.
- 18 `__pycache__` directories, 51 tracked `.pyc`: list with per-file sha256 prefixes in `deleted_pycache.txt`.

## Files in this directory

`README.md`; `c06d1_repro_before_fix.txt`, `c06d1_after_fix.txt`; `c03d3_run.txt`; `line_traces.json` +
`fix1_linetrace.py` (scratch pytest plugin used for it); `t8_hits.py`; `pytest/` (summary, logs, junit);
`import_closure.json.txt`; `install_help.txt`; `install_owner_offline.txt`, `install_public_same_root.txt`,
`install_runner_id_check.txt` + `verify_install_runner_id.py`; `deleted_pycache.txt`.

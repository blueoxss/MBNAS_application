# WP-2A core — evidence (2026-10-26)

Interpreter `runtime/venv/bin/python` (-B), env `CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 MBNAS_HOME=$TARGET_ROOT/runtime
MBNAS_TEST_ROOT=$TARGET_ROOT/runtime/test_root`, cwd = TARGET_ROOT. No GPU, no ports opened (TestClient only).

| step | command | exit | duration | result | file |
|---|---|---|---|---|---|
| reinstall core (new console script) | `python -m pip install --no-index --find-links build/wheelhouse -c constraints.txt --no-deps -e mbnas_core` | 0 | 3 s | `mbnas-core-principal` installed | pip_reinstall_core.log |
| import | `python -B -c "import mbnas_core.api.app, mbnas_core.runner_api.routes"` | 0 | <5 s | ok | import_check.txt |
| import closure | `python -I -B scripts/dev/check_import_closure.py --format text` | 0 | <5 s | 0 violations; transitional allowlist now matches 0 edges (allowlisted: 0) | import_closure.txt |
| self-tests (scratch, not under tests/) | `pytest -p no:cacheprovider -q --rootdir <scratch>/selftest <scratch>/selftest` | 0 | 9 s | 10 passed (fencing, idempotent commit/replay/conflict/ack, ticket verify, sweeper reclaim + late LEASE_LOST, PROCESSING 202 + orphan clear, cancel -> CANCELLED + discarded_result, auth scopes/rotation/revoke, body limits, pre-registered artifacts + execution_configuration without core hashing) | selftest.txt, selftest_test_wp2a.py.txt, selftest_conftest.py.txt |
| targeted owner tests | `pytest ... -m "not gpu and not live and not legacy_records and not host_data" tests/owner/worker tests/owner/api/test_state_machine.py tests/owner/unit/test_db.py tests/owner/unit/test_db_migration.py` | 1 | 149 s | 54 passed, 9 failed (see below) | pytest_targeted_round1.txt |
| full owner gate | same markers, `tests/owner` | 1 | 577 s | 1112 passed, 1 skipped, 47 deselected, **9 failed = the same 9** | pytest_owner_gate.txt / .meta |
| public suite | `pytest ... -m "not gpu and not live" tests/public` | 0 | 75 s | 670 passed, 31 skipped, 12 deselected, 1 xfailed | pytest_public.txt |
| --kinds probe (scratch copy of test_subprocess.py with `--kinds <all six>` added to `_spawn_worker`) | pytest on the scratch copy | 0 | 39 s | 5 passed | (scratch only) |

## The 9 failures are consequences of contract changes (not hidden, tests not edited: outside WP-2A ownership)
- `tests/owner/unit/test_db.py:47`, `tests/owner/unit/test_db_migration.py:69,105`: literal `schema_version == "3"`; contract §1.1 sets `SCHEMA_VERSION="4"`.
- `tests/owner/worker/test_gpu_occupancy.py::{test_tick_waits_for_stale_pid_before_leasing, test_execute_waits_before_spawning_and_logs_to_the_run, test_wait_is_bounded_logged_and_a_live_foreign_pid_is_waited_for_once}`: the NVML wait was removed from the owner worker (sub-task: drop `mbnas_runner.gpu`; owner mode needs no GPU wait). The behaviour belongs to the runner daemon now.
- `tests/owner/worker/test_subprocess.py::{test_worker_kill9_lease_reclaimed_once_by_new_worker, test_pause_at_epoch_boundary_then_resume, test_cancel_terminates_running_child}`: they spawn the default CLI `python -m mbnas_core.owner_worker`, which is now owner mode (PROPOSE_GENERATION only), so the BASELINE_TRAIN stub job is never leased. With `--kinds PROPOSE_GENERATION,BASELINE_TRAIN,CANDIDATE_TRAIN,FINAL_RETRAIN,EVALUATE,BENCHMARK` added to `_spawn_worker` all 5 tests of the file pass unchanged otherwise (probe above).

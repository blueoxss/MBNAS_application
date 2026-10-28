# WP-2D - T3 matrix, runner unit tests, two-process smoke (2026-10-26)

Interpreter `runtime/venv/bin/python -B`; env `CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 MBNAS_HOME=$TARGET_ROOT/runtime
MBNAS_TEST_ROOT=$TARGET_ROOT/runtime/test_root`; cwd TARGET_ROOT. No GPU. Ports: only ephemeral 127.0.0.1 ports opened by
the smoke and by the T3-19 test (uvicorn in-process), all stopped by their owner.

| what | command | exit | duration | result | file |
|---|---|---|---|---|---|
| T3 matrix | `pytest -p no:cacheprovider --timeout=600 -rfEsx -v -m "not gpu and not live and not legacy_records and not host_data" tests/owner/runner_protocol` | 1 | 86 s | 38 passed, 2 failed | pytest_runner_protocol.txt/.meta |
| T3 matrix, re-run (flake check) | same, `-q` | 1 | 85 s | 38 passed, same 2 failed | pytest_runner_protocol_rerun2.txt/.meta |
| public runner + infra | `pytest ... -m "not gpu and not live" tests/public/runner tests/public/infra` | 1 | 75 s | 98 passed, 3 skipped (host data), 6 deselected, 1 failed | pytest_public_runner_infra.txt/.meta |
| import closure | `python -I -B scripts/dev/check_import_closure.py --format text` | 0 | <5 s | 0 violations / 0 forbidden | import_closure.txt |
| two-process smoke | `python -B scripts/dev/wp2_two_process_smoke.py --timeout 600` | 1 | 31 s | a PASS, b FAIL, b2 (synthetic) PASS, c PASS | ../two_process_smoke.txt, smoke_console.txt, smoke.meta |

## T3 coverage (design A3 section 14)
T3-01..T3-33, T3-36, T3-37 implemented (40 tests in tests/owner/runner_protocol, file per group: claim, control, commit,
files, auth, body_limit, restart, owner_generation). T3-34 (prune round trip, WP-5) and T3-35 (structure-check sandbox,
WP-3): NOT_IN_SCOPE of WP-2 (contract section 4) - not implemented, not xfailed.

## Failures (all reproduce on re-run; tests not weakened)
1. `test_t3_files.py::test_runner_recomputes_checkpoint_sha_and_rejects_tampered_file` (T3-23) - **WP-2A/WP-2C field
   mismatch**: the runner daemon sends the rejection text in `ProgressMessage.message` (daemon.py `_message`), the core
   E5 handler logs `msg.payload["message"]` (routes.py progress, `entry = msg.payload`) and falls back to the reason
   code, so the run log says `CHECKPOINT_REPORT_REJECTED` instead of `checkpoint report rejected (sha256 mismatch ...)`.
   Everything else of the test (no registration, reason_code, reported/computed sha, normalisation) passes.
   Normative source A3 6.3 only says "log message (level error, CHECKPOINT_REPORT_REJECTED)"; ProgressMessage has a
   top-level `message` field, so the core should read `msg.message` (proposal; designer decides).
2. `test_t3_body_limit.py::test_result_body_over_limit_is_413_and_runner_externalises_per_sample` (T3-33, D6) - **WP-2C**:
   the core part passes (413 PAYLOAD_TOO_LARGE above the runner limit); the runner daemon does not move `per_sample` to
   an `evaluation.json` artifact, so after the 413 it discards the attempt locally and the job stays RUNNING until the
   lease expires.
3. `tests/public/runner/test_runner_daemon_unit.py::test_wait_is_bounded_logged_and_a_live_foreign_pid_is_waited_for_once`
   - **WP-2C**: the S7-F1 behaviour "a live foreign pid still listed after a full wait is remembered and not waited for
   again" (former owner `Worker._gpu_ignored`) was not carried over to `RunnerDaemon.wait_gpu_clear`.

## Two-process smoke
* (a) generation-only (OFF, CPU): PASS - run COMPLETED, 1 PROPOSE_GENERATION job SUCCEEDED by `smoke-owner-1`,
  2 generated candidates; runner idle (never saw it).
* (b) real yolo11n, 1 epoch, 64 px, explicit training via POST /api/v1/training-runs (`validation_enabled=false`):
  **FAIL**. The protocol path worked end to end over real HTTP (runner `smoke-runner-1` claimed BASELINE_TRAIN,
  started it, pre-registered `architecture.yaml` (artifact_locations runner_local, verified_by runner:smoke-runner-1),
  committed the result, ledger `failed/TRAIN_FAILED` 3.9 s), but the real training failed: `ValueError: Invalid CUDA
  'device=0' requested ... torch.cuda.is_available(): False`. Cause: the explicit-training recipe pins
  `device="cuda:0"` (core `training_runs/selection.py` raises DEVICE_UNSUPPORTED for anything else; `native_params`
  forwards it) and the runner passes it to ultralytics unchanged on a CPU-only host. No CPU path for a real training
  job exists through the queue today, so no EVALUATED row and no real-training checkpoint were produced.
  Dataset-mount remapping (`MBNAS_RUNNER_DATASET_MOUNTS`) during a real training: 확인 못 함 (the job failed at the
  device before data loading; the runner registered the mount as present).
* (b2) SYNTHETIC extra step (not a substitute for b): same explicit training request with validation enabled,
  executed by a second runner process whose child uses the stub handlers: PASS - claimed by `smoke-runner-stub`,
  checkpoint + 4 artifacts registered as runner_local, result committed, EVALUATED row present, run COMPLETED.
* (c) all own processes stopped: runner(s)/owner exit 0 (runner deregistered), core (uvicorn) returncode -15 after
  SIGTERM (it stopped; graceful exit code 확인 못 함).
* Earlier development runs of the smoke left roots under runtime/test_root/wp2_smoke/ (20261026T1158..1200); the
  evidence is the 20261026T120653Z run. Not deleted (deletion needs the user's confirmation).

## Interpretations / limits
* Claim-time baseline resolution reads the registry from disk (`runner_api.envelope.baseline_arch` ->
  `registry.load_registry(settings.output_root)`), not the app's registry provider that validated the protocol. The
  T3 fixtures therefore write a SYNTHETIC probe file (`runner_sim.write_synthetic_probe`) marking yolo11n buildable;
  without it every Baseline job fails at claim with BASELINE_UNAVAILABLE although validation accepted it.
* T3-04 compares 18 jobs (baseline + candidates over two rungs, 5 with resume_from); stub candidates without a YAML
  fail identically on both sides (ARCHITECTURE_MISSING), half of them get a real yolo11 genotype so the search path is
  compared on content.
* Runner-side execution in T3 tests is in-process (TestClient ASGI transport); only T3-19 and the smoke use real
  sockets. TLS was not exercised (확인 못 함); T3-32 checks the refusal of plain http to non-loopback hosts only.

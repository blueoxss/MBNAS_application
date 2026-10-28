ROUND 4 (FIX_ROUND_1 "최종 문서/주석 잔여") - tests track: C02-D1, C02-D2, C05-O2, C03-D6. Evidence index.
Time: 2026-10-27T23:35Z-23:48Z (2026-10-28 08:35-08:48 KST). CPU only: CUDA_VISIBLE_DEVICES="", PYTHONDONTWRITEBYTECODE=1,
PIP_CONFIG_FILE=/dev/null, interpreter runtime/venv/bin/python -B -m pytest -p no:cacheprovider, MBNAS_TEST_ROOT and
--basetemp in the session scratchpad (<SCRATCH>), markers "not gpu and not live and not legacy_records and not host_data",
--timeout=180. No GPU, /data, etri_nas or source-snapshot access; no __pycache__ created in the tree; no product file and no
existing test / conftest was edited. <ROOT> = TARGET_ROOT. Each run has <label>.meta (command, cwd, env, start/end UTC,
exit, source tree hash at start and end) and <label>.txt (tail of the pytest output with the summary line).

Source tree hash (scripts/dev/source_tree_hash.py --digest-only, mbnas-source-tree/2, the 5 packages; tests are not part of
it): sha256:e220e72137165c57b08cd24388c11d64762b689b376a17911a774b8a3bb45236 at the start and end of every run.

NEW FILES (sha256)
  tests/public/runner/test_runner_settings_no_db.py                     8aea606e887586837df4f2498b1c146517ab8b6205da0761c234c39e945db25b
  tests/public/runner/test_runner_entrypoint_redaction.py               211ac540738417dedb824e2c8d33b1e336bf2e9df7344991b4386bb98594ba1b
  tests/owner/projection/test_runner_route_canary.py                    b1a194d02af3345d85461bcd33284663240308170dcf7e60de7beaa331db211f
  tests/owner/orchestration/test_final_retrain_excludes_not_evaluated.py 31ba19ddb48a47bdea4da1e206c7461702a83ac87c746cfe2d4e748dae7aa2fc

WHAT EACH FILE ASSERTS
  C02-D1 test_runner_settings_no_db.py (public bundle; public packages only, no tests/_shared harness) - 7 tests
    RunnerSettings (MBNAS_RUNNER_) and GatewaySettings (MBNAS_GW_): no db_path / ticket_key / named core credential; no field
    whose name tokens name core state or a foreign credential (db, sqlite, database, dsn, ticket, admin, owner, principal,
    salt, hmac, password, credential, private); the secret-bearing fields are exactly the service's own (runner: token,
    token_file, reserved gateway_upload_token_file; gateway: api_token, core_token = the service token the core issued to the
    gateway, artifact_signing_key = the gateway's own key); env prefix is the service's own, no alias into MBNAS_CORE_*, own
    secrets have no alias; MBNAS_CORE_{DB_PATH,API_TOKEN,TICKET_KEY,ARTIFACT_SIGNING_KEY} in the process environment and in
    the services' env files, plus misnamed MBNAS_RUNNER_/MBNAS_GW_ DB_PATH / TICKET_KEY / ADMIN_TOKEN, land in no field
    (secret values revealed before the scan). Template: the C02 evaluator's public_venv_probe.py.
  C05-O2 test_runner_entrypoint_redaction.py (public bundle) - 5 tests
    `mbnas-runner run` / `register` install RedactSecretsFilter on httpx, httpcore, mbnas_runner, root and logging.lastResort
    before the settings are read, and the filter rewrites token/sig/exp query values and an Authorization value (ported from
    the C05 fix-1d scratch test d10); --spec-file installs nothing. E12 form: a fresh `-I -B` interpreter calls
    install_redaction() and logs 6 WARNING records with synthetic secrets (httpx, httpcore, mbnas_runner, mbnas_runner.daemon,
    a third-party logger, root) with no handler configured (-> lastResort) and 3 INFO records after a root handler is added
    (httpx, mbnas_runner, root): 0 secrets in stdout+stderr, every line carries ***, no record dropped. Control: the same
    script without install_redaction() shows every secret (test_control_without_redaction_shows_the_secrets). The in-process
    tests restore every logger / handler / lastResort filter list (scratch check: 0 leftovers, negative_control.txt D).
  C02-D2 test_runner_route_canary.py (owner) - 2 tests; port of the C02 evaluator probe (wp3/c02/probe_rp, R13)
    The T3 fixtures of tests/owner/runner_protocol/conftest.py are loaded by path under a private module name and bound in
    this module only (no conftest edited). GA + Random search run: owner-only markers seeded into the params of every queued
    job and the lineage / audit of every candidate; 18 claims (BASELINE_TRAIN 2, CANDIDATE_TRAIN 16); all 128
    /core/v1/runner response bodies (8 routes: register, claim, start, progress, checkpoints, artifacts, result, ack; the 8
    routes are asserted present) hold 0 markers and 0 owner-only member names; positive control: admin
    /core/v1/owner/runs/{run}/candidates/{cid}/audit = 200 with the marker. PROPOSE_GENERATION: explicit claim = 400
    KIND_NOT_ALLOWED (stronger than the probe's "not 200 or not that kind"), default claim = 200 BASELINE_TRAIN, the two
    queued PROPOSE_GENERATION jobs stay queued. Printed summaries: file_route_canary_summary.txt.
  C03-D6 test_final_retrain_excludes_not_evaluated.py (owner) - 2 tests
    One-rung protocol (L0 is the last rung, so NOT_EVALUATED rows sit at the last rung). GA NOT_EVALUATED
    (EVALUATION_REQUIRES_CUDA, SEARCH_COMPLETE, non-immutable, ap None, latency pending, checkpoint kept):
    final_retrain.eligible_rows excludes them, POST final-retrain = 200 with finalists = [IndependentRandom] only (positive
    control: the measured arm is selected), no GA finalist. Every search row NOT_EVALUATED: 409 NO_FINALIST with the frozen
    message, no child run, eligible_rows empty, the measured Baseline is not substituted.

RUNS (exit / result)
  file_settings                 tests/public/runner/test_runner_settings_no_db.py               exit 0, 7 passed
  file_redaction                tests/public/runner/test_runner_entrypoint_redaction.py         exit 0, 5 passed
  file_route_canary             tests/owner/projection/test_runner_route_canary.py              exit 0, 2 passed
  file_route_canary_summary     the same with -s (printed JSON summaries kept)                  exit 0, 2 passed
  file_final_retrain            tests/owner/orchestration/test_final_retrain_excludes_not_evaluated.py  exit 0, 2 passed
  suite_public_runner           tests/public/runner         exit 0, 127 passed, 9 deselected (115 before + 12 new), 0 skipped
  suite_owner_projection        tests/owner/projection      exit 0, 261 passed (259 before + 2 new)
  suite_owner_orchestration     tests/owner/orchestration   exit 0, 38 passed (36 before + 2 new)
  combo_canary_rp               test_t3_auth.py + test_runner_route_canary.py + test_t3_claim.py + test_owner_routes.py in one
                                session (both conftests active): exit 0, 29 passed - no fixture clash. (A first combo attempt
                                named a file that does not exist, test_t3_kinds.py: exit 4, no tests ran; discarded and rerun.)
  public_bundle_new_tests       the two public files run with the FINAL PUBLIC tree's venv (installed public wheels, no
                                mbnas_core: find_spec None) from <SCRATCH>/pubbundle = pyproject.toml + tests/ copied from the
                                unpacked final public ZIP (T6 tree) + the two new files: exit 0, 12 passed, 0 skipped.
  negative_control.txt          scratch-only controls: R4_NEGCTL=nofilter (envelope param filter disabled) -> canary test FAILS
                                (18 claim envelopes carry the marker); allowpropose (PROPOSE_GENERATION allowed) -> policy-job
                                test FAILS (200 != 400); finalfilter (finalist filters removed) -> both final-retrain tests
                                FAIL; settings checks catch 4/4 planted subclasses; redaction control built into the file.

NOT VERIFIED (확인 못 함)
  - Real multi-process HTTP and GPU paths: the canary and final-retrain tests use the in-process TestClient harness with
    SYNTHETIC stub handlers (as T3).
  - The public-bundle run used the final public ZIP's tests plus the two new files, not a rebuilt ZIP that contains them.
  - The redaction subprocess test drives the logging module with synthetic records; real httpx request lines of a running
    daemon were not captured. Records of loggers other than httpx / httpcore / mbnas_runner / root are rewritten only on the
    lastResort path, i.e. while no handler is configured (the runner configures none) - observation, not a product defect.

LEAK SCAN OF THE NEW FILES (scripts/dev/canary_scan.py --manifest docs/internal/canary/CANARY_MANIFEST.json, host facts on)
  --profile public  on copies of the two tests/public files: scanned 2 files, hits 0, failing 0, PASS (exit 0)
  --profile owner   on copies of the two tests/owner files:  scanned 2 files, hits 0, failing 0, PASS (exit 0)
  --profile owner   on this evidence folder:                 scanned 22 files, hits 0, failing 0, PASS (exit 0)

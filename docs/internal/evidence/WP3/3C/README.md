# WP-3C evidence (tests + harness), 2026-10-27

Interpreter `runtime/venv/bin/python`, `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `MBNAS_HOME=runtime`,
`MBNAS_TEST_ROOT=runtime/test_root`; marker filter as WP-2 2V (`not gpu and not live and not legacy_records and not
host_data`); `--timeout=180` (pytest-timeout is installed in runtime/venv). Other work packages edited `mbnas_core`,
`mbnas_server`, `tests/public/{ops,packaging}`, `tests/owner/runner_protocol/test_artifact_transfer.py` and built
`mbnas_web/dist` while these runs happened; counts are snapshots.

| run | files | result |
|---|---|---|
| owner gate (final) | owner_gate.txt/.meta, run_owner_gate.sh | 15 failed, 1182 passed, 1 skipped, 47 deselected, 1 xfailed, 771 s |
| public (final) | pytest_public.txt/.meta, run_public.sh | 17 failed, 940 passed, 1 skipped, 44 deselected, 1 xfailed, 265 s |
| three-process smoke (final) | ../three_process_smoke.txt, three_process_smoke_console.log | PASS, 15/15 steps, 36.7 s, exit 0 |
| sandbox RSS | sandbox_rss.txt | API RSS delta <= 156 KiB for every limit breach |
| scratch hang | scratch_hang.txt | WP-3A scratch SSE row: pytest-timeout fires at 60 s, process still alive at 240 s (killed) |
| AS-limit stall | as_breach_pyspy_raw.txt | pre-fix: child inside importlib from block_catalog.py:350 until the wall limit |

All 15 owner failures and 16 of the 17 public failures are gateway (WP-3B) defects pinned by tests (see the final
WP-3C report, cross_package_requests). The 17th public failure, `tests/public/ops/test_ops_start_stop.py::
test_web_role_preview`, belongs to another work package (its file was being edited during the run; it is not
touched by WP-3C).

## Why the full suite ran > 20 minutes
1. `tests/owner/api/test_zz_wp3a_scratch.py::test_sse_on_console_mount` (WP-3A's temporary scratch copy) streams an
   endless SSE through Starlette's TestClient, which collects the whole body first: the test times out and the
   session then hangs forever in teardown (scratch_hang.txt). The gate here runs with `--ignore` of that file.
2. `tests/public/server/test_wp3_gateway_relay.py::test_sse_concurrency_per_token` had the same pitfall (fixed: the
   held-open stream now runs in a thread).
3. `tests/owner/structure_sandbox/test_sandbox.py::test_address_space_breach_is_unverified_code` waited for the 180 s
   sandbox wall limit (the child stalled under RLIMIT_AS); bounded to 30 s, and since WP-3A's check_worker.py change
   (13:51 KST) the sandbox answers RESOURCE_LIMIT after 0.17 s and the row passes.

Slowest owner tests (final gate): qa/test_search_fairness_engine.py (16.7 s, 15.5 s, 11.2 s, 10.9 s, 10.4 s),
search/test_space_hash_content.py (15.3 s), runner_protocol/test_t3_control.py pause/resume (11.6 s),
runner_protocol/test_t3_owner_generation.py SIGKILL recovery (11.5 s), worker/test_subprocess.py pause/resume (11.1 s).
Slowest public tests: packaging/test_package_build.py (18.8 s call, 15.6 s setup, 11.1 s), packaging/
test_canary_tools.py (11.4 s). No test over 20 s in either final run.

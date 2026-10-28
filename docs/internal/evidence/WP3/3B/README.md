# WP-3B evidence (gateway), fix round 2026-10-27 (DD-A36, DD-A37, DD-A41 (d), DD-A44 (d)(e), DD-A45)

Interpreter `runtime/venv/bin/python`, `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `MBNAS_HOME=runtime`,
`MBNAS_TEST_ROOT=runtime/test_root`. No GPU, no fixed port (ephemeral 127.0.0.1 only). No existing test was changed
(nothing to add to ../TEST_ADAPTATIONS.md); new rows live in `tests/public/server/test_wp3b_gateway_fixes.py`.

| run | file(s) | result |
|---|---|---|
| import closure | `scripts/dev/check_import_closure.py` | exit 0 at 14:22 KST (violations 0, unresolved 0, forbidden 0, excepted 2 (DD-A26)); exit 1 at 14:5x KST: 2 `resolve` findings, both string literals in `scripts/owner/t2_compare.py` (edited 14:42 by another session: `MODULE_SUBS` table of legacy module paths), none in WP-3B files |
| named tests (coordinator list + new file) | named_tests.txt | 96 passed: owner 29, WP-3C public 20, new WP-3B 47 |
| tests/public/server (gate markers) | (console) | 498 passed, 1 skipped, 25 deselected, 1 xfailed, 0 failed |
| public suite, `bash ../3C/run_public.sh` | pytest_public_after_fix_round.{txt,meta} (copy of ../3C/pytest_public.*) | 1006 passed, 1 skipped, 44 deselected, 1 xfailed, 0 failed, 248 s |
| public suite before this round (WP-3C run) | pytest_public_before_fix_round_3C_run.{txt,meta} | 17 failed, 940 passed |
| owner gate (WP-3C command, evidence here) | owner_gate.{txt,meta}, run_owner_gate.sh | 1 failed, 1196 passed, 1 skipped, 47 deselected, 1 xfailed, 696 s (WP-3C before this round: 15 failed / 1182 passed) |
| real wire (uvicorn h11 + httptools) | wire_check.py, wire_check.txt | 16/16 PASS |
| three-process smoke (core + owner worker + runner + gateway, real processes) | three_process_smoke.txt, three_process_smoke_console.log | PASS, 15/15 steps, 35.1 s, exit 0 |

The one owner-gate failure, `tests/owner/runner_protocol/test_t3_auth.py::test_expired_or_revoked_token_is_401`, has no
gateway in its fixture chain (core TestClient + `RunnerSim` only; no `mbnas_server` code runs). It issues a runner token
with `ttl_s=0.5` and then calls `RunnerSim.register()`, whose body builds `reports.runtime_manifest()`; the first call in a
process imports the runtime (measured 1.71 s, then 0.005 s), so with a cold import the token is expired before the
request: the row fails 3/3 when run alone, passes 6/6 with its file and 42/42 with `tests/owner/runner_protocol`
(load average 8-9 from concurrent sessions during the gate). Test fragility for its owner, not a gateway defect.

`wire_check.py` serves the gateway and the stub core with real uvicorn servers (both HTTP implementations) and sends raw
`http.client` requests: double-encoded dots reach core as the literal `%2E%2E` in one segment, `?`/`#` stay inside the
segment, a dot segment or a NUL id is a gateway 404 without relay, a latin-1 `Idempotency-Key` byte reaches core
unchanged, a malformed `Last-Event-ID` is dropped (decimal forwarded), a `NaN` body is 422 without relay or echo.

## Deviations from the literal instruction (reasoned; for the designer)
1. `ArtifactStore.sweep_tmp`: ageing is `max(st_mtime, st_ctime)` only for temp files with `st_nlink > 1` (the runner's
   hard-linked staging), mtime otherwise. A plain `max()` breaks `tests/public/server/test_artifact_store.py::
   test_killed_writer_leaves_only_an_ignored_temp_file`, which ages its leftover with `os.utime` (that sets ctime to
   now; measured). The hard-link case the instruction targets is covered by the new row
   `test_sweep_keeps_a_fresh_hard_link_of_an_old_file`.
2. `CORE_URL_INSECURE` is enforced where the gateway opens its network client to core (`CoreClient` without an injected
   transport, i.e. app start) and in the `mbnas-gateway` CLI (exit 2), like the runner (its rule lives in its client,
   not in `RunnerSettings`). `GatewaySettings` carries the flag (`core_allow_insecure_http`) and the check
   (`core_url_problem()` / `require_secure_core_url()`), but no pydantic validator: the WP-3C harness builds
   `GatewaySettings(core_url="http://core.invalid")` as a label for its in-process transport, and a validator would
   fail every combined-harness test.
3. A relative/empty `model_path` is relayed to core unchanged (DD-A44 (d)); core then answers `PATH_NOT_ABSOLUTE`
   (relative) or `VALIDATION_FAILED` (empty) - the owner test pins `PATH_NOT_ABSOLUTE` for the relative row.
4. Upload extension refusal is now 422 `UPLOAD_TYPE_UNSUPPORTED` (DD-A44 (d)); the 1.5.0 draft still lists it under 415.

# WP-3 test adaptations (WP-3C)

Rule: no assertion weakened, no silent skip, no policy stub. Every change below names file:line and the reason.

## Harness (tests/_shared)
- tests/_shared/harness.py (appended, after `wait_for`): `StreamingASGITransport`, `issue_principals`,
  `core_settings_for_gateway`, `gateway_settings_for_core`, `gateway_app_over`, `Combined`, `combined_app`.
  Reason: WP-3 contract 2.8. `httpx.ASGITransport` (0.28) buffers the whole body, so an SSE relay over it never yields;
  the streaming transport delivers chunks and `http.disconnect` (self-test: tests/public/server/test_wp3_streaming_transport.py).
- tests/_shared/combined_fixtures.py (new): fixtures `combined`, `core_app`, `app` (= gateway), `core_client`, `client`
  (= gateway TestClient; core lifespan entered first so the core sweeper still runs as before).
- tests/_shared/stub_core.py (new): stub upstream for the gateway unit tests (public suite, no mbnas_core import).

## Owner conftests (client -> gateway)
- tests/owner/{api,worker,orchestration}/conftest.py `api_settings`: `make_settings(tmp_roots, expose_legacy_api=False)`;
  tests/owner/qa/conftest.py `api_settings`: same plus the existing `max_replay_events=5000`.
  The local `app`/`client` fixtures were replaced by `from combined_fixtures import *`. Reason: contract 2.8 — the
  existing `/api/v1` assertions now run through the gateway relay; core no longer serves `/api/v1`.

## Resume pass (2026-10-27, WP-3C)
- tests/owner/qa/test_wp3_security_negatives.py `_tree` + `test_api_bad_module_fails_before_build_without_side_effects`:
  the "no side effect" snapshot is now RECURSIVE (was top-level names only, i.e. stronger) and is taken AFTER the upload
  (the upload legitimately writes `gateway/artifacts/uploads/<sha>.yaml`); `db/` (core SQLite + `-wal`/`-shm`, written by
  any request's audit row) is excluded. Reason: the harness places the gateway store under the runs root.
- tests/owner/structure_sandbox/test_sandbox.py `test_address_space_breach_is_unverified_code`: `wall_s=30.0` added (was
  the fixture default 180 s > pytest-timeout). Assertion unchanged (RESOURCE_LIMIT / RUNTIME_UNAVAILABLE). Under
  RLIMIT_AS=96 MiB the worker HANGS until the wall limit (measured: STRUCTURE_CHECK_TIMEOUT after 25.0 s; 512 MiB and
  1 GiB PASS); py-spy showed the child inside `importlib.import_module` from `block_catalog.py:350
  resolve_source_path` (3C/as_breach_pyspy_raw.txt). After WP-3A's check_worker.py change (2026-10-27 13:51 KST) the row
  PASSES: RESOURCE_LIMIT after 0.17 s, API RSS delta 0 KiB (3C/sandbox_rss.txt). The wall_s=30 bound stays so a future
  regression fails in 30 s instead of stalling the suite.
- tests/public/server/test_wp3_gateway_relay.py `test_sse_concurrency_per_token`: Starlette TestClient collects the whole
  body before `stream()` returns, so the held-open first stream blocked forever (pytest-timeout hit). The first stream
  now runs in a thread; the 429 RATE_LIMITED assertion is unchanged, plus two NEW assertions (first stream delivered
  frames; slot released afterwards).
- tests/public/server/test_wp3_gateway_relay.py `test_validate_attaches_gateway_manifests_and_path_checks`: DD-A37 wire
  format `{format, splits{train,search_val,final_eval}, cross_split_overlap[], cross_split_overlap_total}`; per-split
  summary keys assertion unchanged, format/overlap assertions added.
- tests/public/server/test_gateway_skeleton.py `test_health_with_and_without_bearer`: WP-3 contract 2.2 merged health
  replaces the WP-1 minimal `{status: ok, gateway: true}` (DD-A29 superseded). With core unreachable (closed ephemeral
  port, not the default 8700) the row asserts 200 + `degraded` + `core: unreachable` + gateway status/version +
  request_id + archive_roots allowlist. Auth assertions unchanged.
- tests/public/server/conftest.py `make_client` factory: `**overrides` passed to `make_gateway_settings`.
  tests/public/server/test_perf.py `test_no_file_handle_growth_and_bounded_caches`: `rate_limit_enabled=False` (480
  back-to-back archive GETs exceed the R13 bucket, burst 50 @ 10 rps; the bucket itself is tested in
  test_wp3_gateway_relay.py::test_rate_limit_token_bucket_429). fd/cache assertions unchanged.
- tests/owner/qa/test_wp3_security_negatives.py `test_projection_strips_unknown_keys_for_server_scope`:
  `xfail(strict=True)`. SECURITY_BASELINE section 4 item 9 (unknown keys stripped by the public projection) is NOT met
  in WP-3: the contract (WP-3_GATEWAY.md, intro) makes `project_public(component, payload, scope)` a pass-through hook
  that WP-4 replaces with the DTO allowlist. The row is written to the final requirement and strict, so it turns into a
  failure (forcing removal of the marker) as soon as WP-4 lands. Not a weakened assertion: a known, dated gap.
- tests/owner/api/test_registry_structure.py:221-240 `test_weights_dir_and_model_yaml_roots_are_allowed_roots` (WP-3A
  edit of 2026-10-26 22:04, recorded here by WP-3C): WP-3 contract 1.6 sets the user-YAML allow roots to
  `model_yaml_roots ∪ output_roots`; `weights_dir/m.yaml` moved from the PASS loop to the PATH_OUTSIDE_ALLOWLIST loop
  (output_root added to the PASS loop). This is a contract change of the policy under test, not a weakening: the
  rejection assertion is applied to one more path.
- tests/owner/api/test_zz_wp3a_scratch.py: WP-3A's temporary copy of docs/internal/evidence/WP3/3A/test_wp3a_scratch.py
  (byte-identical; its docstring says "rm" after the run). Left in place for WP-3A to remove (it is their scratch);
  it runs inside the owner api gate and is counted there.
- tests/owner/api/test_registry_roots_skipped.py:17-28 (new module fixture `api_settings`): the core DB is placed outside
  the output root for this module. Behind the gateway every core request authenticates the gateway's server principal
  against the DB (`runner_api/auth.py:163 principal_db` -> `db/core.py:145 connect`), so
  `test_a_really_unreadable_output_root_is_named` (chmod 000 on the output root, which also held `db/`) answered 500
  `INTERNAL_ERROR` (PermissionError on `<runs>/db`) before the registry route ran. Assertions unchanged; the test still
  makes the real output root unreadable.
- tests/owner/api/test_error_trace_id.py:16-41 `test_unhandled_error_has_a_trace_id_and_no_traceback_or_path`: now
  parametrized over `gateway` and `core` (was: the single app). `app` is the gateway, whose static/SPA fallback is
  registered before a route added afterwards (the probe answered 404), so the probe route is inserted at position 0 of
  the router; the core probe sits on `/core/v1/console/_trace_probe` with the server principal and logger
  `mbnas_core.api`, the gateway probe uses logger `mbnas_server.api`. Every original assertion runs on both apps.
- tests/owner/api/test_registry_structure.py:49-51 `no_build` guard: `StructureSandbox.run` added to the guarded
  callables (since WP-3 checks run in the sandbox CHILD, which the in-process patches cannot see; a GET must not even
  start a check) - a strengthening.
- tests/owner/api/test_registry_structure.py:148-167 `test_post_rfdetr_fixed_resolution_is_accepted_by_the_size_rule`:
  the in-process spy is kept and a second spy sits on the sandbox hand-off (`StructureSandbox.run`, op + payload), since
  the adapter call now happens in the child. Assertion `size == 384` unchanged; op asserted to be `native_structure`.
- tests/owner/api/test_registry_structure.py:254-271 `test_the_no_build_guard_really_trips_on_a_check`: through the
  gateway the core's AssertionError becomes a relayed 500 (two ASGI apps), so the row asserts that 500
  (`INTERNAL_ERROR`, `location: core`) plus the guard's exception in the core error log, AND keeps the original
  `pytest.raises(AssertionError, match="never build")` on the core app directly (`core_client`, server principal).
- tests/owner/api/test_set3_health.py:30-38 `test_health_marks_host_gpu_unavailable_when_not_in_inventory`: the bare
  core app no longer serves `/api/v1` (`expose_legacy_api=False`); the same empty-inventory core is reached through the
  gateway of `harness.combined_app(..., devices=[])`. Assertion unchanged.
- tests/owner/unit/test_schemas_contract.py:276-293 `test_training_request_mirror_has_the_route_model_schema`: the core
  route model `TrainingSource` gained the gateway-carried fields `model_yaml_text`, `source_sha256`, `model_name`
  (WP-3 contract 2.5; mbnas_core/training_runs/selection.py:46-49) while the public mirror changes only in WP-4. The
  delta is pinned EXACTLY (route - mirror == those three, mirror - route == {}) and the remaining shape comparison is
  unchanged.
- tests/_shared/harness.py:58-66 `make_gateway_settings`: default `web_dist` = `<runs>/gateway/no-web-dist` (never
  created) unless a test passes one; tests/public/server/test_gateway_skeleton.py:48-56 `make_client`: same default
  (`NO_WEB_DIST`). Reason: the GatewaySettings default is `<repo>/mbnas_web/dist` (DD-A30), which appeared on this host
  at 2026-10-27 13:22 KST (bundle built by another work package); from then on the SPA fallback answered `/docs` with
  index.html and `test_docs_disabled_and_unknown_routes_are_contract_errors` failed depending on host state.
- tests/public/server/test_gateway_skeleton.py `test_docs_disabled_and_unknown_routes_are_contract_errors`: original
  four 404 assertions unchanged (no bundle); NEW second phase with a bundle: `/docs`, `/redoc`, `/openapi.json` are
  either JSON 404 or exactly index.html, never Swagger/OpenAPI content; `/api/v1/nope` stays a JSON 404.

## WP-3A resume pass (2026-10-27)
- tests/owner/api/test_zz_wp3a_scratch.py REMOVED by WP-3A (scratch copy; its SSE row used TestClient.stream on an
  endless stream and hung the owner api gate). The evidence copy docs/internal/evidence/WP3/3A/test_wp3a_scratch.py now
  uses a real uvicorn thread for SSE (same pattern as tests/owner/api/test_sse.py) and adds an unauthenticated-SSE 401
  assertion; it runs outside tests/ (3A/README.md). No owner test assertion was changed by WP-3A in this pass.
- 3C request "worker hangs under RLIMIT_AS=96 MiB": re-measured 2026-10-27, test_address_space_breach_is_unverified_code
  passes in 0.14 s (fails fast); worker now also preloads torch/ultralytics under the limits.

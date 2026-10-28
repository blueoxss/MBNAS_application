# WP-3 RESUME_STATE (2026-10-27, designer)

The WP-3 build workflow wf_4b13dd70-0b8 died with the Claude session on 2026-10-26 22:10 before any of the three build agents (A core, B gateway, C tests) reported. The tree below is their partial work; it imports but is unverified.

## Files modified/created by the interrupted attempt (mtime >= 2026-10-26 21:40)
- docs/internal/evidence/WP3/3A/sandbox_rlimit_as.txt
- docs/internal/evidence/WP3/3A/test_wp3a_scratch.py
- docs/internal/evidence/WP3/RESUME_STATE.md
- docs/internal/evidence/WP3/TEST_ADAPTATIONS.md
- mbnas_core/src/mbnas_core/api/app.py
- mbnas_core/src/mbnas_core/config.py
- mbnas_core/src/mbnas_core/console/deps.py
- mbnas_core/src/mbnas_core/console/model_services.py
- mbnas_core/src/mbnas_core/console/projection.py
- mbnas_core/src/mbnas_core/console/routes_files.py
- mbnas_core/src/mbnas_core/console/routes_registry_structure.py
- mbnas_core/src/mbnas_core/console/routes_runs.py
- mbnas_core/src/mbnas_core/console/routes_stream.py
- mbnas_core/src/mbnas_core/console/routes_training.py
- mbnas_core/src/mbnas_core/console/services.py
- mbnas_core/src/mbnas_core/ops/principals.py
- mbnas_core/src/mbnas_core/orchestration/engine.py
- mbnas_core/src/mbnas_core/protocol/gateway_inputs.py
- mbnas_core/src/mbnas_core/protocol/training_validation.py
- mbnas_core/src/mbnas_core/protocol/validator.py
- mbnas_core/src/mbnas_core/structure/check_worker.py
- mbnas_core/src/mbnas_core/structure/__init__.py
- mbnas_core/src/mbnas_core/structure/sandbox.py
- mbnas_core/src/mbnas_core/training_runs/selection.py
- mbnas_model_runtime/src/mbnas_model_runtime/genotype.py
- mbnas_server/pyproject.toml
- mbnas_server/src/mbnas_server/api/app.py
- mbnas_server/src/mbnas_server/api/core_client.py
- mbnas_server/src/mbnas_server/api/limits.py
- mbnas_server/src/mbnas_server/api/relay.py
- mbnas_server/src/mbnas_server/api/static_web.py
- mbnas_server/src/mbnas_server/api/uploads.py
- mbnas_server/src/mbnas_server/api/validate_prep.py
- mbnas_server/src/mbnas_server/doctor.py
- scripts/dev/wp3_three_process_smoke.py
- tests/owner/api/test_registry_structure.py
- tests/owner/api/test_zz_wp3a_scratch.py
- tests/owner/qa/test_wp3_security_negatives.py
- tests/owner/structure_sandbox/conftest.py
- tests/owner/structure_sandbox/test_sandbox.py
- tests/_shared/harness.py

## Known defect at resume time
check_import_closure.py: 2 'deps' violations in mbnas_server/src/mbnas_server/api/validate_prep.py (lines 77, 93): mbnas_server imports mbnas_model_runtime.datasets.{yaml_safe,manifest}. The gateway-side manifest computation (WP-3B) needs the dependency rule updated (mbnas_server -> mbnas_model_runtime datasets helpers is allowed by DD-A34/WP-3 contract 2.3) OR the helper must be re-homed; the owner of the closure rule file decides and records it in DECISIONS.md.

## Quick state
- tests/public/infra: 5 passed, 1 failed (test_no_boundary_violation) at resume time
- tests/owner, negative security tests, three-process smoke: not yet run

## WP-3C resume result (2026-10-27 ~14:05 KST)
- Harness/tests/smoke verified and completed; evidence in docs/internal/evidence/WP3/3C/README.md, adaptations in
  TEST_ADAPTATIONS.md. Final: owner gate 15 failed / 1182 passed (all 15 = gateway defects pinned for WP-3B), public
  17 failed / 940 passed (16 = new gateway rows for WP-3B, 1 = another WP's ops test), three-process smoke PASS.
- tests/owner/api/test_zz_wp3a_scratch.py (WP-3A scratch copy) still present; its SSE row hangs a session -> gate runs
  with --ignore of that file.

# WP-1 test adaptations (WP-1G)

작성: WP-1G (tests relocation), 2026-10-26. 기준: WP-1 계약 §9/§14, DD-A22, TEST_INVENTORY 적응 코드 A1–A20,
WP-1_RESOLUTIONS.md의 WP-1G 항목(G1–G4), DD-A26–A29. 모든 경로는 `TARGET_ROOT=/home/mgjeong/Desktop/etri_hd/mbnas_application` 기준.

이 문서는 스냅샷 `build/source_snapshot/application/MBNAS_server/tests/**`(130 파일)을 `tests/{owner,public,_shared}`로 옮기며
바꾼 모든 단언·skip·삭제를 file:line, 사유, 적응 코드와 함께 적는다. 적응 코드 외 코드:
`C-cross-N`/`E-cross-N` = 설계자가 판정한 교차 요청(WP-1_RESOLUTIONS G1–G4), `DD-Axx` = 결정 문서 항목,
`import-map` = AST 재작성이 닿지 않는 문자열 안 import(`-c` 코드 등)의 MODULE_MAP §8.1 매핑, `layout` = docstring/주석만 변경,
`xfail(DD-A29)` = 설계자가 지정한 strict xfail.

## 0. 요약

- 배치: moved 124, split 3(`conftest.py` → 5 파일, `worker/test_import_safety.py` → 2, `api/test_auth_cors.py` → 2), deleted 3 (합 130), 신규 9. legacy_records 3개는 `tests/owner/legacy`(마커 `legacy_records`), live 1개는 `tests/owner/live`(마커 `live`).
- import 재작성: `scripts/dev/rewrite_imports.py`의 Mapper(§4 심볼표 + MODULE_MAP §8.1)를 WP-1G 스크래치 도구가 그대로 호출(파일별 컨텍스트 = owner→core, public→model_runtime/runner/contracts/server 뷰). 재작성 오류 1건(`tests/owner/legacy/test_registry_workspace_evidence.py:56` `import mbnas_server` — 옛 모놀리스 레이아웃을 검사하는 legacy 기록이라 그대로 둠, 기본 게이트 제외).
- conftest 분할(§9): 스냅샷 `tests/conftest.py` → `tests/_shared/{conftest_common.py, datasets.py, gpu_guard.py}` + `tests/owner/conftest.py`(settings=`CoreSettings`) + `tests/public/conftest.py`(settings=`RunnerSettings`); `clean_env`는 `MBNAS_*` 전부 제거(`MBNAS_TEST_*` 보존); `MBNAS_TEST_ROOT` 기본 `$TARGET_ROOT/runtime/test_root`(생성, tmp fallback 없음), `MBNAS_TEST_WEIGHTS_DIR`/`MBNAS_TEST_RUNS_ROOT`/`MBNAS_TEST_DATA_ROOT`/`PYTEST_GPU_LOCK`(`$MBNAS_TEST_ROOT/locks/gpu.lock`) 기본값; `MBNAS_TEST_ETRI_ROOT`·`MBNAS_ETRI_NAS_ROOT`·`MBNAS_TEST_ASSUME_ULTRALYTICS` 삭제; `host_data` 마커 = `MBNAS_TEST_HOST_DATA=1` 없으면 skip(`pilot_required` 사용 테스트와 `skipif(..., host_data=True)` 표시는 자동 마킹); 자식 인터프리터용 `PYTHONDONTWRITEBYTECODE=1`(DD-A29).
- 하네스(§9): `tests/_shared/harness.py` — `InlineWorker`는 `mbnas_core.owner_worker.main.Worker` + `owner_worker.child.load_context/run_job`, `make_settings`=`CoreSettings`, `runner_settings_for`(=`child_settings_env`의 RunnerSettings), `make_gateway_settings`=`GatewaySettings`; `handler_override`는 `mbnas_core.owner_worker.contract`, `Emitter/JobFailure/JobPaused`는 `mbnas_contracts.jobs_protocol`.
- 신규 테스트(§9): `tests/public/infra/test_import_closure.py`, `tests/public/infra/test_no_host_facts.py`, `tests/public/model_runtime/test_parse_model_gate.py`, `tests/owner/search/test_operators_parity.py`(+ 자식 프로그램 `tests/_shared/operators_parity.py`), `tests/public/runner/test_child_spec_file.py`, `tests/owner/unit/test_settings_split.py`; 승계 `tests/owner/search/test_mixed_pool_bridge.py`; WP-1F 초안 채택 `tests/public/server/test_gateway_skeleton.py`.


## 1. Placement (130 snapshot files)

| snapshot `application/MBNAS_server/tests/…` | target | note |
|---|---|---|
| `adapters/test_adapters_cpu.py` | `tests/public/model_runtime/test_adapters_cpu.py` | import view: mbnas_model_runtime |
| `adapters/test_coco_convert.py` | `tests/public/model_runtime/test_coco_convert.py` | import view: mbnas_model_runtime |
| `adapters/test_predict_geometry_gpu.py` | `tests/public/model_runtime/test_predict_geometry_gpu.py` | import view: mbnas_model_runtime |
| `adapters/test_rfdetr_sampler_generator.py` | `tests/public/model_runtime/test_rfdetr_sampler_generator.py` | import view: mbnas_model_runtime |
| `adapters/test_structure_native_cpu.py` | `tests/owner/adapters/test_structure_native_cpu.py` | import view: mbnas_core |
| `api/conftest.py` | `tests/owner/api/conftest.py` | import view: mbnas_core |
| `api/test_access_log_redaction.py` | `tests/owner/api/test_access_log_redaction.py` | import view: mbnas_core |
| `api/test_artifact_signature_non_ascii.py` | `tests/owner/api/test_artifact_signature_non_ascii.py` | import view: mbnas_core |
| `api/test_artifacts_export.py` | `tests/owner/api/test_artifacts_export.py` | import view: mbnas_core |
| `api/test_block_catalog_route.py` | `tests/owner/api/test_block_catalog_route.py` | import view: mbnas_core |
| `api/test_candidate_detail_lineage.py` | `tests/owner/api/test_candidate_detail_lineage.py` | import view: mbnas_core |
| `api/test_checkpoint_state_row.py` | `tests/owner/api/test_checkpoint_state_row.py` | import view: mbnas_core |
| `api/test_contract_responses.py` | `tests/owner/api/test_contract_responses.py` | import view: mbnas_core |
| `api/test_final_retrain.py` | `tests/owner/api/test_final_retrain.py` | import view: mbnas_core |
| `api/test_generation_freshness.py` | `tests/owner/api/test_generation_freshness.py` | import view: mbnas_core |
| `api/test_generation_multi_seed.py` | `tests/owner/api/test_generation_multi_seed.py` | import view: mbnas_core |
| `api/test_generation_real_cpu.py` | `tests/owner/api/test_generation_real_cpu.py` | import view: mbnas_core |
| `api/test_off_run_guards.py` | `tests/owner/api/test_off_run_guards.py` | import view: mbnas_core |
| `api/test_postprocess_runtime_row.py` | `tests/owner/api/test_postprocess_runtime_row.py` | import view: mbnas_core |
| `api/test_registry_roots_skipped.py` | `tests/owner/api/test_registry_roots_skipped.py` | import view: mbnas_core |
| `api/test_registry_structure.py` | `tests/owner/api/test_registry_structure.py` | import view: mbnas_core |
| `api/test_runs_idempotency.py` | `tests/owner/api/test_runs_idempotency.py` | import view: mbnas_core |
| `api/test_set3_health.py` | `tests/owner/api/test_set3_health.py` | import view: mbnas_core |
| `api/test_sse.py` | `tests/owner/api/test_sse.py` | import view: mbnas_core |
| `api/test_state_machine.py` | `tests/owner/api/test_state_machine.py` | import view: mbnas_core |
| `api/test_structure_history.py` | `tests/owner/api/test_structure_history.py` | import view: mbnas_core |
| `api/test_training_selection.py` | `tests/owner/api/test_training_selection.py` | import view: mbnas_core |
| `archive/conftest.py` | `tests/public/server/conftest.py` | import view: mbnas_server |
| `archive/realdata.py` | `tests/public/server/realdata.py` | import view: mbnas_server |
| `archive/synth.py` | `tests/public/server/synth.py` | import view: mbnas_server |
| `archive/test_analysis.py` | `tests/public/server/test_analysis.py` | import view: mbnas_server |
| `archive/test_campaign_adapter.py` | `tests/public/server/test_campaign_adapter.py` | import view: mbnas_server |
| `archive/test_compare_conditions.py` | `tests/public/server/test_compare_conditions.py` | import view: mbnas_server |
| `archive/test_perf.py` | `tests/public/server/test_perf.py` | import view: mbnas_server |
| `archive/test_routes.py` | `tests/public/server/test_routes.py` | import view: mbnas_server |
| `archive/test_scanner.py` | `tests/public/server/test_scanner.py` | import view: mbnas_server |
| `archive/test_security.py` | `tests/public/server/test_security.py` | import view: mbnas_server |
| `benchmark/conftest.py` | `tests/public/runner/conftest.py` | import view: mbnas_runner |
| `benchmark/test_bench_doc.py` | `tests/owner/legacy/test_bench_doc.py` | import view: mbnas_runner |
| `benchmark/test_benchmark_rfdetr.py` | `tests/public/runner/test_benchmark_rfdetr.py` | import view: mbnas_runner |
| `benchmark/test_benchmark_unit.py` | `tests/public/runner/test_benchmark_unit.py` | import view: mbnas_runner |
| `evaluation/test_evaluator_images.py` | `tests/public/model_runtime/test_evaluator_images.py` | import view: mbnas_model_runtime |
| `evaluation/test_fitness_gate_integration.py` | `tests/owner/evaluation/test_fitness_gate_integration.py` | import view: mbnas_core |
| `evaluation/test_latency_gpu.py` | `tests/public/model_runtime/test_latency_gpu.py` | import view: mbnas_model_runtime |
| `evaluation/test_latency_operating_point.py` | `tests/public/model_runtime/test_latency_operating_point.py` | import view: mbnas_model_runtime |
| `evaluation/test_matching.py` | `tests/public/model_runtime/test_matching.py` | import view: mbnas_model_runtime |
| `orchestration/conftest.py` | `tests/owner/orchestration/conftest.py` | import view: mbnas_core |
| `orchestration/test_engine_flow.py` | `tests/owner/orchestration/test_engine_flow.py` | import view: mbnas_core |
| `orchestration/test_final_selection.py` | `tests/owner/orchestration/test_final_selection.py` | import view: mbnas_core |
| `orchestration/test_open_generation_progress.py` | `tests/owner/orchestration/test_open_generation_progress.py` | import view: mbnas_core |
| `orchestration/test_retention.py` | `tests/owner/orchestration/test_retention.py` | import view: mbnas_core |
| `orchestration/test_semantics.py` | `tests/owner/orchestration/test_semantics.py` | import view: mbnas_core |
| `qa/conftest.py` | `tests/owner/qa/conftest.py` | import view: mbnas_core |
| `qa/qa_handlers.py` | `tests/_shared/qa_handlers.py` | import view: mbnas_core |
| `qa/test_api_matrix.py` | `tests/owner/qa/test_api_matrix.py` | import view: mbnas_core |
| `qa/test_benchmark_evidence.py` | `tests/owner/qa/test_benchmark_evidence.py` | import view: mbnas_core |
| `qa/test_engine_negatives.py` | `tests/owner/qa/test_engine_negatives.py` | import view: mbnas_core |
| `qa/test_handoff_inputs_record.py` | `tests/owner/legacy/test_handoff_inputs_record.py` | import view: mbnas_core |
| `qa/test_immutable.py` | `tests/owner/qa/test_immutable.py` | import view: mbnas_core |
| `qa/test_live_stack.py` | `tests/owner/live/test_live_stack.py` | import view: mbnas_core |
| `qa/test_registry_workspace_evidence.py` | `tests/owner/legacy/test_registry_workspace_evidence.py` | import view: mbnas_core |
| `qa/test_search_fairness_engine.py` | `tests/owner/qa/test_search_fairness_engine.py` | import view: mbnas_core |
| `qa/test_security_negatives.py` | `tests/owner/qa/test_security_negatives.py` | import view: mbnas_core |
| `qa/test_smoke_script_final_phases.py` | `tests/owner/qa/test_smoke_script_final_phases.py` | import view: mbnas_core |
| `qa/test_sse_server.py` | `tests/owner/qa/test_sse_server.py` | import view: mbnas_core |
| `qa/test_validator_negatives.py` | `tests/owner/qa/test_validator_negatives.py` | import view: mbnas_core |
| `registry/test_probe_registry_snapshot.py` | `tests/owner/registry/test_probe_registry_snapshot.py` | import view: mbnas_core |
| `registry/test_registry.py` | `tests/owner/registry/test_registry.py` | import view: mbnas_core |
| `registry/test_registry_unreadable_roots.py` | `tests/owner/registry/test_registry_unreadable_roots.py` | import view: mbnas_core |
| `registry/test_set3_registry_devices.py` | `tests/owner/registry/test_set3_registry_devices.py` | import view: mbnas_core |
| `registry/test_weights_manifest_message.py` | `tests/public/model_runtime/test_weights_manifest_message.py` | import view: mbnas_model_runtime |
| `search/conftest.py` | `tests/owner/search/conftest.py` | import view: mbnas_core |
| `search/test_block_catalog.py` | `tests/owner/search/test_block_catalog.py` | import view: mbnas_core |
| `search/test_generation_only.py` | `tests/owner/search/test_generation_only.py` | import view: mbnas_core |
| `search/test_generation_recovery_independent.py` | `tests/owner/search/test_generation_recovery_independent.py` | import view: mbnas_core |
| `search/test_proposers.py` | `tests/owner/search/test_proposers.py` | import view: mbnas_core |
| `search/test_repair_evidence_seed.py` | `tests/owner/search/test_repair_evidence_seed.py` | import view: mbnas_core |
| `search/test_space_fallback_optin.py` | `tests/owner/search/test_space_fallback_optin.py` | import view: mbnas_core |
| `search/test_space_genotype.py` | `tests/owner/search/test_space_genotype.py` | import view: mbnas_core |
| `search/test_space_hash_content.py` | `tests/owner/search/test_space_hash_content.py` | import view: mbnas_core |
| `search/test_structure_adversarial.py` | `tests/owner/search/test_structure_adversarial.py` | import view: mbnas_core |
| `search/test_structure_cpu.py` | `tests/owner/search/test_structure_cpu.py` | import view: mbnas_core |
| `search/test_structure_observation.py` | `tests/owner/search/test_structure_observation.py` | import view: mbnas_core |
| `search/test_structure_runtime_cpu.py` | `tests/owner/search/test_structure_runtime_cpu.py` | import view: mbnas_core |
| `training/test_checkpoint.py` | `tests/public/model_runtime/test_checkpoint.py` | import view: mbnas_model_runtime |
| `training/test_explicit_cpu.py` | `tests/owner/training/test_explicit_cpu.py` | import view: mbnas_core |
| `training/test_final_job_gpu.py` | `tests/owner/training/test_final_job_gpu.py` | import view: mbnas_core |
| `training/test_job_gpu.py` | `tests/owner/training/test_job_gpu.py` | import view: mbnas_core |
| `training/test_model_artifacts.py` | `tests/owner/training/test_model_artifacts.py` | import view: mbnas_core |
| `training/test_recipe_view_handlers.py` | `tests/owner/training/test_recipe_view_handlers.py` | import view: mbnas_core |
| `training/test_resume_equivalence_script.py` | `tests/public/runner/test_resume_equivalence_script.py` | import view: mbnas_runner |
| `training/test_rfdetr_housekeeping_gpu.py` | `tests/owner/training/test_rfdetr_housekeeping_gpu.py` | import view: mbnas_core |
| `training/test_stage_trainer.py` | `tests/public/runner/test_stage_trainer.py` | import view: mbnas_runner |
| `unit/test_artifacts_registry.py` | `tests/owner/unit/test_artifacts_registry.py` | import view: mbnas_core |
| `unit/test_config.py` | `tests/owner/unit/test_config.py` | import view: mbnas_core |
| `unit/test_contract_history.py` | `tests/public/contracts/test_contract_history.py` | import view: mbnas_contracts |
| `unit/test_datasets.py` | `tests/public/model_runtime/test_datasets.py` | import view: mbnas_model_runtime |
| `unit/test_db.py` | `tests/owner/unit/test_db.py` | import view: mbnas_core |
| `unit/test_db_migration.py` | `tests/owner/unit/test_db_migration.py` | import view: mbnas_core |
| `unit/test_gpu_lock_guard.py` | `tests/public/infra/test_gpu_lock_guard.py` | import view: mbnas_contracts |
| `unit/test_latency_record_fields.py` | `tests/public/model_runtime/test_latency_record_fields.py` | import view: mbnas_model_runtime |
| `unit/test_log_redact.py` | `tests/public/contracts/test_log_redact.py` | import view: mbnas_contracts |
| `unit/test_protocol_fitness.py` | `tests/owner/unit/test_protocol_fitness.py` | import view: mbnas_core |
| `unit/test_protocol_presets.py` | `tests/owner/unit/test_protocol_presets.py` | import view: mbnas_core |
| `unit/test_protocol_promotion.py` | `tests/owner/unit/test_protocol_promotion.py` | import view: mbnas_core |
| `unit/test_protocol_resolution_v12.py` | `tests/owner/unit/test_protocol_resolution_v12.py` | import view: mbnas_core |
| `unit/test_protocol_validator.py` | `tests/owner/unit/test_protocol_validator.py` | import view: mbnas_core |
| `unit/test_provenance.py` | `tests/public/model_runtime/test_provenance.py` | import view: mbnas_model_runtime |
| `unit/test_schemas_contract.py` | `tests/owner/unit/test_schemas_contract.py` | import view: mbnas_core |
| `unit/test_schemas_jobs.py` | `tests/public/contracts/test_schemas_jobs.py` | import view: mbnas_contracts |
| `unit/test_source_tree_manifest.py` | `tests/public/contracts/test_source_tree_manifest.py` | import view: mbnas_contracts |
| `unit/test_util_canon_atomic_time.py` | `tests/public/contracts/test_util_canon_atomic_time.py` | import view: mbnas_contracts |
| `unit/test_util_paths.py` | `tests/public/contracts/test_util_paths.py` | import view: mbnas_contracts |
| `worker/conftest.py` | `tests/owner/worker/conftest.py` | import view: mbnas_core |
| `worker/harness.py` | `tests/_shared/harness.py` | import view: mbnas_core |
| `worker/stub_handlers.py` | `tests/_shared/stub_handlers.py` | import view: mbnas_runner |
| `worker/test_blocked_result.py` | `tests/owner/worker/test_blocked_result.py` | import view: mbnas_core |
| `worker/test_generation_crash.py` | `tests/owner/worker/test_generation_crash.py` | import view: mbnas_core |
| `worker/test_gpu_occupancy.py` | `tests/owner/worker/test_gpu_occupancy.py` | import view: mbnas_core |
| `worker/test_lease.py` | `tests/owner/worker/test_lease.py` | import view: mbnas_core |
| `worker/test_native_handlers.py` | `tests/owner/worker/test_native_handlers.py` | import view: mbnas_core |
| `worker/test_progress_metrics.py` | `tests/owner/worker/test_progress_metrics.py` | import view: mbnas_core |
| `worker/test_subprocess.py` | `tests/owner/worker/test_subprocess.py` | import view: mbnas_core |
| `worker/test_worker_integrity.py` | `tests/owner/worker/test_worker_integrity.py` | import view: mbnas_core |
| `api/test_auth_cors.py` | `tests/owner/api/test_auth_cors.py`, `tests/public/server/test_auth_cors_gateway.py` | split (hand-written from the snapshot text) |
| `conftest.py` | `tests/_shared/conftest_common.py`, `tests/_shared/datasets.py`, `tests/_shared/gpu_guard.py`, `tests/owner/conftest.py`, `tests/public/conftest.py` | split (hand-written from the snapshot text) |
| `worker/test_import_safety.py` | `tests/owner/worker/test_import_safety.py`, `tests/public/infra/test_import_safety.py` | split (hand-written from the snapshot text) |
| `qa/test_evaluation_summary_record.py` | — (deleted) | DD-A22 / TEST_INVENTORY §8.5: deleted (A7: checks /root/.claude journal and docs/evaluation records of the old host, not product behaviour) |
| `qa/test_orchestration_records_set2.py` | — (deleted) | DD-A22 / TEST_INVENTORY §8.5: deleted (A7: Codex CLI paths and past agent-id records; its contract-history invariant is covered by tests/public/contracts/test_contract_history.py) |
| `search/test_etri_bridge_permissions.py` | — (deleted) | contract §9: deleted (ETRI root concept removed by vendoring, DD-A12); the 'bridge exception is a diagnostic, not a 500' meaning moves to tests/owner/search/test_mixed_pool_bridge.py |

비고: `tests/owner/registry/test_probe_registry_snapshot.py`와 `tests/owner/registry/test_set3_registry_devices.py`는 TEST_INVENTORY가 `public(host_data)`로 제안했지만 core registry(`mbnas_core.registry.load_registry`, `scripts/owner/probe_registry.py`)를 쓰므로 owner에 두었다(공개 테스트는 mbnas_core를 import할 수 없다, §13 host 규칙). 둘 다 host_data다. `unit/test_config.py`(both(infra))는 owner(CoreSettings)로, 3 Settings 공통 규칙은 신규 `test_settings_split.py`가 맡는다.

## 2. Adapted lines (file:line, code, reason)

Line numbers are those of the file as committed in `tests/`; `—` = the change removed lines (no anchor). Import-path rewrites done by `scripts/dev/rewrite_imports.py` (MODULE_MAP §8.1 + §4 symbol table) are not listed line by line; their per-file record is `stage_report.json` of the relocation run (see §5).

| file | line(s) | code | reason |
|---|---|---|---|
| `tests/_shared/conftest_common.py` | 36 | DD-A29 | test children inherit PYTHONDONTWRITEBYTECODE=1 |
| `tests/_shared/harness.py` | 1 | layout | docstring: helpers moved from tests/worker to tests/_shared |
| `tests/_shared/harness.py` | 20 | A4 A8 | host-root defaults /root/detection -> MBNAS_TEST_* under runtime/test_root; TEST_ETRI_ROOT removed (A3: ETRI root concept gone, contract §9 deletes MBNAS_TEST_ETRI_ROOT) |
| `tests/_shared/harness.py` | 33 | A11 A3 | CoreSettings has no etri_nas_root (DD-A13); log_dir pinned to the value the old Settings derived (output_root/logs) because the CoreSettings default is $MBNAS_HOME/core/logs |
| `tests/_shared/harness.py` | 35 | A11 A5 | per-package settings (DD-A13): RunnerSettings mirror for owner tests that call runner handlers, GatewaySettings for the gateway app used by the archive tests (contract §9 harness bullet) |
| `tests/_shared/harness.py` | 68 | layout | tests/_shared also holds datasets.py (name fixed by contract §9) |
| `tests/_shared/qa_handlers.py` | 3 | layout | docstring: new location |
| `tests/_shared/stub_handlers.py` | 3 | layout | docstring: new location and the runner child use |
| `tests/_shared/stub_handlers.py` | 24 | A11 | the stub runs on the runner side: RunnerSettings (DD-A13) |
| `tests/_shared/stub_handlers.py` | 121 | A2 A11 | secret env names of the owner worker are MBNAS_CORE_* (contract §5 SECRET_ENV); the legacy names are still reported so the check cannot become vacuous |
| `tests/owner/api/conftest.py` | 1 | A1 A5 | docstring: which app / worker the WP-1 harness uses |
| `tests/owner/api/conftest.py` | 13 | A1 A5 | harness path tests/worker -> tests/_shared |
| `tests/owner/api/test_auth_cors.py` | 16 | A5 | the core app (WP-1 harness) no longer mounts /archive; the same bearer rows are asserted on the gateway app in tests/public/server/test_auth_cors_gateway.py |
| `tests/owner/api/test_auth_cors.py` | 45 | A11 | the core API token variable is MBNAS_CORE_API_TOKEN (DD-A13) |
| `tests/owner/api/test_candidate_detail_lineage.py` | 22 | A4 | host root /root/detection -> MBNAS_TEST_RUNS_ROOT |
| `tests/owner/api/test_candidate_detail_lineage.py` | 37 | A3 A8 | ETRI root plumbing removed (contract §9 deletes MBNAS_TEST_ETRI_ROOT / MBNAS_ETRI_NAS_ROOT) |
| `tests/owner/api/test_final_retrain.py` | 274 | A2 A11 | the stub's real split_manifests call loads RunnerSettings (DD-A13): runner env names as handed to children by the owner worker |
| `tests/owner/api/test_generation_real_cpu.py` | 16 | A15 | PROPOSE_GENERATION is owner-worker only (DD-A4); in WP-1 the owner worker is the same in-process Worker, so only the import path changed (mbnas_core.owner_worker.main) and the test is marked owner_worker |
| `tests/owner/api/test_registry_structure.py` | 220 | A3 A11 | etri_nas_root is deleted (DD-A13/DD-A12); the user-YAML allow root it provided is model_yaml_roots (contract §5); the weights_dir half and the outside-root rejection are unchanged |
| `tests/owner/api/test_registry_structure.py` | 255 | A17 A19 | the GET driver rebuilds the WP-1 harness: tests/_shared |
| `tests/owner/api/test_registry_structure.py` | 268 | A17 | the GET path is served by the core app in WP-1 (contract §3.1(b), DD-A22) |
| `tests/owner/api/test_registry_structure.py` | 274 | A19 | example registry moved to config/examples (contract §4 last row) |
| `tests/owner/api/test_registry_structure.py` | 304 | A19 | tests/owner/api -> tests/ |
| `tests/owner/api/test_registry_structure.py` | 307 | A3 A8 | MBNAS_TEST_ETRI_ROOT no longer exists (contract §9) |
| `tests/owner/api/test_registry_structure.py` | 356 | A19 | import inside the subprocess code string (MODULE_MAP §8.1) |
| `tests/owner/api/test_training_selection.py` | 36 | A2 (DD-A16) | baseline native params now carry the core-computed ArchSpec (contract §7, DD-A16); the registry lookup is fed the synthetic all-AVAILABLE registry fixture instead of host probe evidence |
| `tests/owner/api/test_training_selection.py` | 64 | A2 (DD-A16) | expected `arch is None` replaced by the ArchSpec the owner child must inject for a baseline (contract §7 'baseline arch 주입'); settings passed explicitly (A11) |
| `tests/owner/api/test_training_selection.py` | 74 | A2 (DD-A16) | baseline native params need an AVAILABLE registry entry (DD-A16); synthetic registry fixture |
| `tests/owner/api/test_training_selection.py` | 83 | A11 | CoreSettings passed explicitly (the baseline ArchSpec uses its weights_dir) |
| `tests/owner/legacy/test_bench_doc.py` | 21 | A7 A9 | legacy_records marker (DD-A22); bench_host_gpu.py moved to scripts/runner |
| `tests/owner/legacy/test_bench_doc.py` | 29 | A4 | host root -> MBNAS_TEST_RUNS_ROOT |
| `tests/owner/legacy/test_handoff_inputs_record.py` | 16 | A7 | DD-A22: kept with the legacy_records marker (default gate excludes it); content otherwise unchanged |
| `tests/owner/legacy/test_handoff_inputs_record.py` | 23 | A3 (legacy_records) | MBNAS_TEST_ETRI_ROOT no longer exists (contract §9); the record keeps its old-host layout and stays deselected by the legacy_records marker (DD-A22) |
| `tests/owner/legacy/test_registry_workspace_evidence.py` | 14 | A7 | DD-A22: kept with the legacy_records marker (default gate excludes it); content otherwise unchanged |
| `tests/owner/legacy/test_registry_workspace_evidence.py` | 152 | layout | import rewrite by hand (the automatic pass stops at this file's bare `import mbnas_server`, which is kept: it now imports the gateway package) |
| `tests/owner/legacy/test_registry_workspace_evidence.py` | 185 | layout | import rewrite by hand (see above) |
| `tests/owner/legacy/test_registry_workspace_evidence.py` | 209 | layout | import rewrite by hand (see above) |
| `tests/owner/legacy/test_registry_workspace_evidence.py` | 20 | A3 (legacy_records) | MBNAS_TEST_ETRI_ROOT no longer exists (contract §9); the record keeps its old-host layout and stays deselected by the legacy_records marker (DD-A22) |
| `tests/owner/live/test_live_stack.py` | 1 | A4 | docstring: WP-1 paths |
| `tests/owner/live/test_live_stack.py` | 18 | A4 | DD-A7/DD-A22 ports (core 8700) and env file location; marker live (contract §9) |
| `tests/owner/live/test_live_stack.py` | 38 | A11 | DD-A13 core variable names in the stack env file |
| `tests/owner/orchestration/conftest.py` | 1 | A1 A5 | docstring: which app / worker the WP-1 harness uses |
| `tests/owner/orchestration/conftest.py` | 13 | A1 A5 | harness path tests/worker -> tests/_shared |
| `tests/owner/orchestration/test_retention.py` | 131 | A2 A11 | retention CLI settings come from the MBNAS_CORE_* environment (DD-A13) |
| `tests/owner/qa/conftest.py` | 3 | A1 A5 | docstring: new harness location |
| `tests/owner/qa/conftest.py` | 13 | A1 A5 | harness/qa_handlers moved to tests/_shared |
| `tests/owner/qa/conftest.py` | 94 | A2 A11 | env names of the split settings (DD-A13; contract §5 child_settings_env) |
| `tests/owner/qa/test_benchmark_evidence.py` | 21 | A4 | host root /root/detection -> MBNAS_TEST_RUNS_ROOT |
| `tests/owner/qa/test_benchmark_evidence.py` | 70 | A8 | recorded bench evidence is host data |
| `tests/owner/qa/test_benchmark_evidence.py` | 246 | A19 | test path in the evidence record |
| `tests/owner/qa/test_search_fairness_engine.py` | 113 | A11 | runner handlers take RunnerSettings (contract §5) |
| `tests/owner/qa/test_search_fairness_engine.py` | 127 | A11 | runner code takes RunnerSettings (contract §5) |
| `tests/owner/qa/test_search_fairness_engine.py` | 140 | A11 | runner handlers take RunnerSettings (contract §5) |
| `tests/owner/qa/test_security_negatives.py` | 301 | A11 | secret variable names of the core host are MBNAS_CORE_* (DD-A13); the legacy names are still checked |
| `tests/owner/qa/test_security_negatives.py` | 310 | A11 | DD-A13 core token name |
| `tests/owner/qa/test_security_negatives.py` | 322 | A11 | secret names of the split settings (DD-A13) |
| `tests/owner/qa/test_security_negatives.py` | 314 | A11 | secret names of the split settings (DD-A13 / contract §5) |
| `tests/owner/qa/test_security_negatives.py` | 327 | A4 A8 | real secrets/logs are host data (host_data marker); the old ETRI checkout layout is replaced by this checkout (TARGET_ROOT) and the env file location by MBNAS_TEST_ENV_FILE |
| `tests/owner/qa/test_security_negatives.py` | 346 | A4 A8 | the same kinds of locations in the WP-1 layout (five package sources instead of MBNAS_server/src) |
| `tests/owner/qa/test_smoke_script_final_phases.py` | 17 | A9 | script moved to scripts/owner |
| `tests/owner/qa/test_smoke_script_final_phases.py` | 36 | A2 A11 | the retention CLI the script runs loads CoreSettings from this env file (DD-A13 MBNAS_CORE_ prefix) |
| `tests/owner/registry/test_probe_registry_snapshot.py` | 17 | A9 | probe_registry.py moved to scripts/owner |
| `tests/owner/registry/test_probe_registry_snapshot.py` | 32 | A8 | real probe evidence is host data |
| `tests/owner/registry/test_probe_registry_snapshot.py` | 37 | A4 | host root -> MBNAS_TEST_RUNS_ROOT |
| `tests/owner/registry/test_probe_registry_snapshot.py` | 53 | A19 | src layout of the WP-1 packages |
| `tests/owner/registry/test_registry.py` | 4 | A6 | importlib.resources for the packaged contract |
| `tests/owner/registry/test_registry.py` | 18 | A6 A4 | contract from mbnas_contracts package data; host root -> MBNAS_TEST_RUNS_ROOT |
| `tests/owner/registry/test_registry.py` | 94 | A8 | real probe evidence is host data (host_data marker added by conftest_common) |
| `tests/owner/registry/test_set3_registry_devices.py` | 21 | A4 | host root -> MBNAS_TEST_RUNS_ROOT |
| `tests/owner/registry/test_set3_registry_devices.py` | 62 | A8 | real bench evidence is host data |
| `tests/owner/registry/test_set3_registry_devices.py` | 109 | A19 | import inside the subprocess code string (registry evidence functions live in mbnas_core.registry) |
| `tests/owner/search/conftest.py` | 9 | A4 | host root /root/detection -> MBNAS_TEST_RUNS_ROOT |
| `tests/owner/search/conftest.py` | 26 | A3 A8 | ETRI root plumbing removed (contract §9 deletes MBNAS_TEST_ETRI_ROOT / MBNAS_ETRI_NAS_ROOT) |
| `tests/owner/search/test_block_catalog.py` | 24 | A19 | src layout of the WP-1 packages (the subprocesses run with -I, so PYTHONPATH stays informational) |
| `tests/owner/search/test_block_catalog.py` | 68 | A3 A19 | catalog source strings point at the vendored / relocated files (contract §6.8) |
| `tests/owner/search/test_block_catalog.py` | 76 | A3 | ETRI_MIXED_SOURCE is the vendored file (contract §6.8) |
| `tests/owner/search/test_block_catalog.py` | 176 | A19 | import inside the subprocess code string (MODULE_MAP §8.1) |
| `tests/owner/search/test_block_catalog.py` | 191 | A19 | imports inside the subprocess code string (MODULE_MAP §8.1) |
| `tests/owner/search/test_block_catalog.py` | 183,201 | C-cross-8 | -I ignores PYTHONDONTWRITEBYTECODE; -B keeps __pycache__ out of the package src trees |
| `tests/owner/search/test_mixed_pool_bridge.py` | — | new (§9 successor) | successor of the deleted test_etri_bridge_permissions.py (see §3) |
| `tests/owner/search/test_operators_parity.py` | — | new (§6.11, DD-A29) | snapshot original vs vendored operators: 8 YAML variants, class names, params, prefix-mapped structural_hash, layers, state, 64 px forward; snapshot mixed_blocks.py sha 4233dd32… asserted |
| `tests/owner/search/test_proposers.py` | 4 | A6 | importlib.resources for the packaged contract |
| `tests/owner/search/test_proposers.py` | 25 | A6 | contracts/openapi.json -> mbnas_contracts package data (DD-A10) |
| `tests/owner/search/test_repair_evidence_seed.py` | 10 | A6 | importlib.resources for the packaged contract |
| `tests/owner/search/test_repair_evidence_seed.py` | 23 | A6 | contracts/openapi.json -> mbnas_contracts package data (DD-A10) |
| `tests/owner/search/test_space_fallback_optin.py` | 1 | A3 A8 | how unavailability is induced after vendoring |
| `tests/owner/search/test_space_fallback_optin.py` | 23 | A3 A8 | unavailability induced by monkeypatching the loader / probe (ETRI_RUNTIME_SCOPE §6.4 table) |
| `tests/owner/search/test_space_fallback_optin.py` | 61 | A3 | the unavailability cause is the simulated one (was the missing root /nonexistent) |
| `tests/owner/search/test_space_fallback_optin.py` | 73 | A16 | DD-A22 deletes MBNAS_TEST_ASSUME_ULTRALYTICS; the ML skip condition itself is kept |
| `tests/owner/search/test_space_fallback_optin.py` | 79 | A3 | simulated unavailability (was the missing root /nonexistent) |
| `tests/owner/search/test_space_fallback_optin.py` | 93 | A3 | the unavailable half is produced by hiding ultralytics in the subprocess; the in-process probe_mixed_pool('/nonexistent') line is dropped because the probe has no root argument any more (the subprocess b-check asserts the same 'available is False' outcome) |
| `tests/owner/search/test_space_fallback_optin.py` | 39 | A3 A8 | simulated unavailable probe instead of MBNAS_ETRI_NAS_ROOT=/nonexistent |
| `tests/owner/search/test_space_fallback_optin.py` | 39 | A3 A8 | simulated unavailable probe instead of MBNAS_ETRI_NAS_ROOT=/nonexistent |
| `tests/owner/search/test_space_fallback_optin.py` | 39 | A3 A8 | simulated unavailable probe instead of MBNAS_ETRI_NAS_ROOT=/nonexistent |
| `tests/owner/search/test_space_fallback_optin.py` | 160 | A3 | simulated unavailability; etri_root stays a (required, ignored) keyword of shared_space |
| `tests/owner/search/test_space_genotype.py` | 23 | A3 | hashed file set = the three vendored modules (contract §6.4/§6.5) |
| `tests/owner/search/test_space_hash_content.py` | 1 | A3 | docstring of the /3 hash definition |
| `tests/owner/search/test_space_hash_content.py` | 11 | A3 | hashlib for the installed-bytes check |
| `tests/owner/search/test_space_hash_content.py` | 22 | A3 A8 | no ETRI checkout any more (vendored, always present): the ETRI-root skip is removed and the hashed file set is the three vendored modules (contract §6.5) |
| `tests/owner/search/test_space_hash_content.py` | 34 | A3 A19 | subprocess imports the WP-1 modules; no root argument |
| `tests/owner/search/test_space_hash_content.py` | 41 | A3 | reports which copy was imported (precondition of the location check) |
| `tests/owner/search/test_space_hash_content.py` | 46 | A3 | contract §9: 'content tampering -> hash change' rewritten as tampering of a copy of the vendored modules |
| `tests/owner/search/test_space_hash_content.py` | 59 | A3 | variants are selected with PYTHONPATH instead of an ETRI root argument |
| `tests/owner/search/test_space_hash_content.py` | 76 | A3 | expectations follow the /3 definition (contract §6.5): version /3, the 3 vendored files (sha of the installed bytes), location independence instead of HEAD independence, content change of each vendored file changes the hashes; marked slow (5 CPU subprocesses) |
| `tests/owner/search/test_space_hash_content.py` | — | A3 | deleted by contract §9: there is no second ETRI root any more ('다른 루트 거부' 삭제) |
| `tests/owner/search/test_structure_observation.py` | — | A8 | MBNAS_TEST_ETRI_ROOT / MBNAS_ETRI_NAS_ROOT no longer exist (contract §9) |
| `tests/owner/search/test_structure_observation.py` | 372 | A3 A8 | ETRI_RUNTIME_SCOPE §6.4: the fixture registers unconditionally (stricter: no skip left) |
| `tests/owner/training/test_explicit_cpu.py` | 10 | A11 | the runner handler gets RunnerSettings (owner conftest runner_settings = what the owner worker hands to a child); validation/DB keep CoreSettings |
| `tests/owner/training/test_explicit_cpu.py` | 45 | A11 | RunnerSettings for the handler |
| `tests/owner/training/test_explicit_cpu.py` | 55 | A11 | RunnerSettings for the handler |
| `tests/owner/training/test_final_job_gpu.py` | 9 | A11 | os (data roots) and harness.runner_settings_for |
| `tests/owner/training/test_final_job_gpu.py` | 15 | A4 A8 | host root -> MBNAS_TEST_RUNS_ROOT; host_data marker |
| `tests/owner/training/test_final_job_gpu.py` | 32 | A4 | host data root -> MBNAS_TEST_DATA_ROOT |
| `tests/owner/training/test_final_job_gpu.py` | 55 | A2 | params.arch is required by the runner resolve_model (contract §7, DD-A16) |
| `tests/owner/training/test_final_job_gpu.py` | 43 | A2 | arch_spec for the core-side baseline ArchSpec |
| `tests/owner/training/test_final_job_gpu.py` | 58 | A11 | RunnerSettings for the handler |
| `tests/owner/training/test_final_job_gpu.py` | 69 | A20 | DD-A15 removes gate/fitness_rung/fitness_weights from the runner evaluation payload (contract §8); replaced by the DD-A15 check |
| `tests/owner/training/test_final_job_gpu.py` | 56 | A2 (DD-A16) | ArchSpec from the registry entry (E-cross-1) |
| `tests/owner/training/test_job_gpu.py` | 16 | A11 | harness.runner_settings_for |
| `tests/owner/training/test_job_gpu.py` | 20 | A4 A8 | host root -> MBNAS_TEST_RUNS_ROOT; host_data marker |
| `tests/owner/training/test_job_gpu.py` | 37 | A4 | host data root -> MBNAS_TEST_DATA_ROOT |
| `tests/owner/training/test_job_gpu.py` | 68,138 | A2 A11 | runner handlers get RunnerSettings; the baseline ArchSpec is computed core-side and passed as params.arch as the owner child now does (DD-A16, contract §7) |
| `tests/owner/training/test_job_gpu.py` | 77,107,146 | A2 | params.arch is required by the runner resolve_model (contract §7, DD-A16) |
| `tests/owner/training/test_job_gpu.py` | 81,96,99,109 | A11 | RunnerSettings for the handlers |
| `tests/owner/training/test_job_gpu.py` | 148,155,160 | A11 | RunnerSettings for the handlers |
| `tests/owner/training/test_job_gpu.py` | 90 | A20 | DD-A15: fitness/gate are no longer computed by the runner (core recomputes them in commit_evaluation; covered by tests/owner/evaluation/test_fitness_gate_integration.py); replaced by the DD-A15 check |
| `tests/owner/training/test_job_gpu.py` | 45,69 | A2 (DD-A16) | ArchSpec computed from the registry entry exactly as the owner child does (E-cross-1: arch_spec(id, weights_dir, entry)) |
| `tests/owner/training/test_recipe_view_handlers.py` | 62 | A11 | runner handlers take RunnerSettings (contract §5) |
| `tests/owner/training/test_recipe_view_handlers.py` | 64,67,69 | A11 | RunnerSettings for the handler |
| `tests/owner/training/test_recipe_view_handlers.py` | 64 | A11 | RunnerSettings for the handler |
| `tests/owner/training/test_recipe_view_handlers.py` | 84 | A11 | runner handlers take RunnerSettings (contract §5) |
| `tests/owner/training/test_recipe_view_handlers.py` | 91,111 | A11 | RunnerSettings for the handler |
| `tests/owner/training/test_recipe_view_handlers.py` | 106 | A11 | runner handlers take RunnerSettings (contract §5) |
| `tests/owner/training/test_rfdetr_housekeeping_gpu.py` | 8 | A11 | os (data roots) and harness.runner_settings_for |
| `tests/owner/training/test_rfdetr_housekeeping_gpu.py` | 14 | A4 A8 | host root -> MBNAS_TEST_RUNS_ROOT; host_data marker |
| `tests/owner/training/test_rfdetr_housekeeping_gpu.py` | 41 | A4 | host data root -> MBNAS_TEST_DATA_ROOT |
| `tests/owner/training/test_rfdetr_housekeeping_gpu.py` | 31 | A2 | arch_spec for the core-side baseline ArchSpec |
| `tests/owner/training/test_rfdetr_housekeeping_gpu.py` | 52 | A2 | params.arch is required by the runner resolve_model (contract §7, DD-A16) |
| `tests/owner/training/test_rfdetr_housekeeping_gpu.py` | 56 | A11 | RunnerSettings (owner profile: pickle resume allowed) for the handler |
| `tests/owner/training/test_rfdetr_housekeeping_gpu.py` | 20 | A11 (C-cross-7) | clean_env removes MBNAS_*; native RF-DETR checkpoint loading needs MBNAS_RUNNER_ALLOW_PICKLE_RESUME=1 as on the owner host (DD-A20) |
| `tests/owner/training/test_rfdetr_housekeeping_gpu.py` | 53 | A2 (DD-A16) | ArchSpec from the registry entry, as the owner child computes it (E-cross-1) |
| `tests/owner/unit/test_config.py` | 1 | A11 | the module tests CoreSettings |
| `tests/owner/unit/test_config.py` | 8 | A4 | sys.executable default |
| `tests/owner/unit/test_config.py` | 16 | A4 A3 A11 | DD-A13 defaults ($MBNAS_HOME tree, port 8700, worker_python = sys.executable) replace the /root/detection / MBNAS_server/.venv / 8000 defaults; etri_nas_root is deleted |
| `tests/owner/unit/test_config.py` | 33 | A4 A11 | env prefix MBNAS_CORE_ (DD-A13); the log_dir default is $MBNAS_HOME/core/logs, no longer derived from output_root (contract §5 table) |
| `tests/owner/unit/test_config.py` | 47 | A11 | env prefix MBNAS_CORE_ (DD-A13) |
| `tests/owner/unit/test_config.py` | 54 | A11 | env prefix MBNAS_CORE_ (DD-A13); the startup error names the variable the operator has to set |
| `tests/owner/unit/test_config.py` | 79 | A11 | env prefix MBNAS_CORE_ (DD-A13) |
| `tests/owner/unit/test_config.py` | 82 | A11 | contract §5 env-file variable |
| `tests/owner/unit/test_config.py` | 86 | A4 A11 | DD-A7 core port 8700 |
| `tests/owner/unit/test_protocol_presets.py` | 13 | A8 A19 | the handoff original is read from the owner snapshot (no ETRI checkout, MBNAS_TEST_ETRI_ROOT deleted); example configs moved to config/examples |
| `tests/owner/unit/test_protocol_presets.py` | 44 | A4 | preset path constants become placeholders (contract §8 presets row: /ABSOLUTE/PATH/TO/... instead of /root/detection/...); same keys and suffixes as before |
| `tests/owner/unit/test_protocol_presets.py` | 66 | A4 | PILOT_SMOKE default paths are placeholders (contract §8, DD-A19); every other value unchanged |
| `tests/owner/unit/test_protocol_validator.py` | 18 | A4 A8 | host root /root/detection -> MBNAS_TEST_DATA_ROOT; the preset paths (placeholders since DD-A19) are set to the real subset the way the old host defaults pointed at it |
| `tests/owner/unit/test_protocol_validator.py` | 61 | A4 A8 | real PILOT subset paths (were the preset defaults) |
| `tests/owner/unit/test_protocol_validator.py` | 94 | A4 A8 | the subset location of this test run |
| `tests/owner/unit/test_protocol_validator.py` | 102 | A4 A8 | real PILOT subset paths |
| `tests/owner/unit/test_protocol_validator.py` | 105 | A4 A8 | real PILOT subset paths |
| `tests/owner/unit/test_protocol_validator.py` | 108 | A4 A8 | real PILOT subset paths |
| `tests/owner/unit/test_protocol_validator.py` | 114 | A4 A8 | real PILOT subset paths (default_protocol() carried the host pilot paths as examples) |
| `tests/owner/unit/test_protocol_validator.py` | 117 | A4 A8 | real PILOT subset paths |
| `tests/owner/unit/test_protocol_validator.py` | 338 | A4 | inside the data allow root of this test run, missing |
| `tests/owner/unit/test_schemas_contract.py` | 5 | A6 | importlib.resources for the packaged contract |
| `tests/owner/unit/test_schemas_contract.py` | 16 | A6 | contracts/openapi.json -> mbnas_contracts package data (DD-A10) |
| `tests/owner/unit/test_schemas_contract.py` | 573 | A6 | contract history lives in docs/public/contracts/history (DD-A10) |
| `tests/owner/unit/test_settings_split.py` | — | new (§5) | 3 Settings defaults under MBNAS_HOME (and <cwd>/runtime), env prefixes, *_ENV_FILE, removed fields/legacy names, original validation rules |
| `tests/owner/worker/conftest.py` | 1 | A1 A5 | docstring: which app / worker the WP-1 harness uses |
| `tests/owner/worker/conftest.py` | 13 | A1 A5 | harness path tests/worker -> tests/_shared |
| `tests/owner/worker/test_generation_crash.py` | 38 | A2 A3 A11 | owner worker env: MBNAS_CORE_* names (DD-A13); MBNAS_ETRI_NAS_ROOT/etri_nas_root removed (DD-A12) |
| `tests/owner/worker/test_generation_crash.py` | 80 | A15 | PROPOSE_GENERATION is owner-worker only (DD-A4): the spawned process is `-m mbnas_core.owner_worker --cpu` |
| `tests/owner/worker/test_gpu_occupancy.py` | 33 | A9 | repository root of the WP-1 layout |
| `tests/owner/worker/test_gpu_occupancy.py` | 276 | A9 | dev_stack.py moved to scripts/dev (contract §11) |
| `tests/owner/worker/test_import_safety.py` | — | A10 A11 A19 | split (owner half): walk over mbnas_core only (parametrized per package), child env MBNAS_CORE_* + MBNAS_HOME instead of MBNAS_DB_PATH/MBNAS_OUTPUT_ROOT, DB check = no *.sqlite3 under the tmp dir, `-m mbnas_server.worker` -> `-m mbnas_core.owner_worker` (DD-A14) and its help title 'MBNAS GPU worker' -> 'MBNAS owner worker' (the entry point's argparse description in core); '--gpu-index' kept; children with -B |
| `tests/owner/worker/test_native_handlers.py` | 13 | A15 | PROPOSE_GENERATION is owner-worker only (DD-A4); the inline worker is the owner worker in WP-1 |
| `tests/owner/worker/test_native_handlers.py` | 32 | A2 (DD-A16) | baseline ArchSpec lookup fed the synthetic all-AVAILABLE registry (contract §7) |
| `tests/owner/worker/test_native_handlers.py` | 51 | A11 | CoreSettings passed explicitly to the owner child loader |
| `tests/owner/worker/test_native_handlers.py` | 55 | A2 (DD-A16) | baseline params.arch is the core-computed ArchSpec (contract §7), not None |
| `tests/owner/worker/test_subprocess.py` | 35 | A2 A11 | the owner worker process loads CoreSettings from MBNAS_CORE_* (DD-A13); harness.HERE is tests/_shared |
| `tests/owner/worker/test_subprocess.py` | 69 | A11 | the secret the owner worker holds is MBNAS_CORE_API_TOKEN (contract §5 SECRET_ENV) |
| `tests/owner/worker/test_worker_integrity.py` | 104 | A11 | core token name of the split settings (DD-A13) |
| `tests/public/contracts/test_contract_history.py` | 14 | A6 | contract/history paths: mbnas_contracts.openapi.CONTRACT_PATH/HISTORY_PATH + docs/public/contracts/history (B-cross-3) |
| `tests/public/contracts/test_contract_history.py` | 14,18,40,43 | A6 | contract file = package data CONTRACT_PATH |
| `tests/public/contracts/test_contract_history.py` | 54 | C-cross-8 | child interpreter must not write __pycache__ (DD-A29 -B rule) |
| `tests/public/contracts/test_source_tree_manifest.py` | 3 | A9 | script path scripts/dev/source_tree_hash.py; the manifest is built over explicit roots via the script's manifest() because default_roots() was removed (contract §4, DD-A29) |
| `tests/public/contracts/test_source_tree_manifest.py` | 65 | A9 | explicit roots instead of --server-root/--web-root + default_roots |
| `tests/public/contracts/test_source_tree_manifest.py` | 73 | A9 | documentation root named explicitly (was --with-docs) |
| `tests/public/contracts/test_source_tree_manifest.py` | 79 | A9 | manifest over explicit roots |
| `tests/public/contracts/test_source_tree_manifest.py` | 86 | A9 | manifest over explicit roots |
| `tests/public/infra/test_import_closure.py` | — | new (§13.2, DD-A26/A27) | checker run() with only the documented exceptions (DD-A26 data literals, DD-A27 main->mbnas_runner.gpu, §7 transitional edges, all with expiry); dynamic import checks per public package. Package names are composed strings so the checker does not read the test's own tables as references |
| `tests/public/infra/test_import_safety.py` | — | A10 A19 (E-cross-1) | split (public half): same static and dynamic checks per public package (contracts, model_runtime, runner, server); the entry-point check runs `-m mbnas_runner --help` and asserts 'mbnas-runner' and '--spec-file' (E-cross-1) instead of 'MBNAS GPU worker'/'--gpu-index'; the __main__-was-imported check applies to packages that have one |
| `tests/public/infra/test_no_host_facts.py` | — | new (§0, DD-A26) | forbidden strings in every file under */src/** (any type) except the two DD-A26 (file, literal) pairs; yolo_ga_runtime import statements/string references |
| `tests/public/model_runtime/test_adapters_cpu.py` | 19,20,21 | A4 A8 | notebook location: MBNAS_TEST_ETRI_ROOT and /root/detection fallback removed (contract §9) -> $MBNAS_TEST_DATA_ROOT/etri_reference |
| `tests/public/model_runtime/test_adapters_cpu.py` | 30 | A19 | package source path in the new layout |
| `tests/public/model_runtime/test_adapters_cpu.py` | 68 | C-cross-2 (§6.7) | third assertion (any importable callable accepted) contradicts the exact allowlist of contract §6.7; replaced by the designer-approved assertions (WP-1_RESOLUTIONS G4) |
| `tests/public/model_runtime/test_adapters_cpu.py` | 92 | A8 | needs the ETRI reference checkout (host data) |
| `tests/public/model_runtime/test_adapters_cpu.py` | 73 | C-cross-2 (§6.7) | legacy hook key read from REGISTER_HOOKS (DD-A26 data exception lives only in the package); refused examples are importable callables outside the allowlist |
| `tests/public/model_runtime/test_datasets.py` | 20 | A9 | repository root in the new layout |
| `tests/public/model_runtime/test_datasets.py` | 203 | A9 C-cross-8 | script moved to scripts/public (contract §11); child runs with -B |
| `tests/public/model_runtime/test_datasets.py` | 210 | A9 | argv prefix now has the extra -B element (same command) |
| `tests/public/model_runtime/test_datasets.py` | 149 | A4 A8 | PILOT subset location -> $MBNAS_TEST_DATA_ROOT/pilot_visdrone_v1 (the directory pilot_required checks; test is host_data via pilot_required) |
| `tests/public/model_runtime/test_latency_operating_point.py` | 49 | import-map | old module paths inside the child script string -> MODULE_MAP §8.1 (adapters -> model_runtime, evaluation.handlers -> runner) |
| `tests/public/model_runtime/test_latency_operating_point.py` | — | A19 | SERVER_ROOT/.venv interpreter no longer exists; the child uses the test interpreter |
| `tests/public/model_runtime/test_latency_operating_point.py` | 143 | A19 | child interpreter = the isolated venv running pytest (was MBNAS_server/.venv) |
| `tests/public/model_runtime/test_parse_model_gate.py` | — | new (§9, C-cross-6) | installed parse_model sha == EXPECTED, static sets 37/17, verify_static_copy equivalent, registration idempotent, source mismatch refused without fallback |
| `tests/public/model_runtime/test_predict_geometry_gpu.py` | 9 | A4 | os needed for the MBNAS_TEST_WEIGHTS_DIR lookup |
| `tests/public/model_runtime/test_predict_geometry_gpu.py` | 17 | A4 | literal /root/detection weights path -> MBNAS_TEST_WEIGHTS_DIR (contract §9) |
| `tests/public/model_runtime/test_provenance.py` | 10 | A9 | default_roots() was removed by contract §4 (roots are always explicit); SERVER (old checkout root) no longer used |
| `tests/public/model_runtime/test_provenance.py` | 80,89 | A9 | default_roots(server, web) -> the same 25 (label, path) roots named explicitly (DD-A29); assertions unchanged |
| `tests/public/model_runtime/test_provenance.py` | 106 | A9 | explicit roots (DD-A29); assertions unchanged |
| `tests/public/model_runtime/test_provenance.py` | 114,128 | A9 (DD-A29) | the two tests of the removed default_roots/NOT_HASHED/DEFAULT_*_ROOT API are rewritten for the replacement package_roots() API (B-cross-3, DD-A29); the monorepo top-level layout check has no counterpart (no checkout-relative roots exist any more) |
| `tests/public/model_runtime/test_provenance.py` | — | A9 | unused json/subprocess/sys imports removed after the CLI check moved to the package_roots() API |
| `tests/public/runner/conftest.py` | 1 | layout | docstring: tests/benchmark -> tests/public/runner |
| `tests/public/runner/conftest.py` | 8 | A4 | host root /root/detection -> MBNAS_TEST_RUNS_ROOT |
| `tests/public/runner/test_benchmark_rfdetr.py` | 88 | import-map | old module path inside a -c string (not reached by the AST import rewrite): benchmark.capability -> mbnas_model_runtime (MODULE_MAP §8.1) |
| `tests/public/runner/test_benchmark_rfdetr.py` | 90 | C-cross-8 | child with -B |
| `tests/public/runner/test_benchmark_rfdetr.py` | 6 | A4 | docstring follows the new host root |
| `tests/public/runner/test_benchmark_rfdetr.py` | 30 | A4 A8 | /root/detection host root -> MBNAS_TEST_WEIGHTS_DIR / MBNAS_TEST_DATA_ROOT / MBNAS_TEST_RUNS_ROOT; official weights are host_data |
| `tests/public/runner/test_benchmark_rfdetr.py` | 310 | A8 | host_data marker |
| `tests/public/runner/test_benchmark_unit.py` | 23 | A4 A8 | host default /root/detection removed; PILOT under $MBNAS_TEST_DATA_ROOT |
| `tests/public/runner/test_benchmark_unit.py` | 61 | A8 | host_data marker |
| `tests/public/runner/test_child_spec_file.py` | — | new (§7, E-cross-1) | `python -m mbnas_runner.child --spec-file` with a stub HANDLERS module: SUCCEEDED JSONL + exit 0; failure/bad spec/PROPOSE_GENERATION -> FAILED + exit 3; no mbnas_core import |
| `tests/public/runner/test_resume_equivalence_script.py` | 9 | A9 | script moved to scripts/runner (contract §11) |
| `tests/public/server/conftest.py` | 3 | A8 | host run root no longer a literal host path; real-run tests are host_data |
| `tests/public/server/conftest.py` | 16 | A19 | harness.py moved from tests/worker to tests/_shared |
| `tests/public/server/conftest.py` | 32 | A19 | harness location comment |
| `tests/public/server/conftest.py` | 46 | A5 | gateway app takes no AppServices (registry/devices were only for the old single app's services) |
| `tests/public/server/conftest.py` | 55 | A5 | archive tests run against the gateway: create_app(GatewaySettings) (contract §9, WP-1_RESOLUTIONS G1) |
| `tests/public/server/realdata.py` | 4 | A8 | real archive run root: literal host path -> $MBNAS_TEST_ARCHIVE_RUNS_ROOT (default $MBNAS_TEST_ROOT/archive_runs) |
| `tests/public/server/realdata.py` | 30 | A8 | real-run skips also carry the host_data marker (conftest_common adds it from host_data=True) |
| `tests/public/server/test_auth_cors_gateway.py` | — | A5 (split) | the 13 archive rows removed from tests/owner/api/test_auth_cors.py run unchanged against the gateway app, plus the gateway health row and 'refuses to start without MBNAS_GW_API_TOKEN' |
| `tests/public/server/test_campaign_adapter.py` | 30 | A8 | real campaign root: literal host path -> $MBNAS_TEST_ARCHIVE_RUNS_ROOT; host_data marker |
| `tests/public/server/test_campaign_adapter.py` | 143 | A5 | gateway app has no services argument |
| `tests/public/server/test_campaign_adapter.py` | 154 | A5 | create_app(GatewaySettings) (WP-1_RESOLUTIONS G1) |
| `tests/public/server/test_campaign_adapter.py` | 527 | A11 | env prefix MBNAS_GW_ (DD-A13, WP-1_RESOLUTIONS G1) |
| `tests/public/server/test_campaign_adapter.py` | 529 | A11 | env prefix MBNAS_GW_ |
| `tests/public/server/test_campaign_adapter.py` | 534 | A5 | gateway app has no services argument |
| `tests/public/server/test_campaign_adapter.py` | 540 | A5 | create_app(GatewaySettings) |
| `tests/public/server/test_gateway_skeleton.py` | — | new (G1) | WP-1F draft adopted: SYNTH_PY -> tests/public/server/synth.py, local clean_env dropped (the shared one removes MBNAS_* too), child interpreter -I -B; assertions unchanged |
| `tests/public/server/test_routes.py` | 292 | xfail(DD-A29) | gateway GET /health has only {status, version, gateway} until the WP-3 relay; strict xfail per DD-A29 |
| `tests/public/server/test_security.py` | 17 | A19 | archive package source path in the new layout (WP-1_RESOLUTIONS G1) |
| `tests/public/tools/smoke_api_only.py` | 44 | A19 | stub handler directory moved to tests/_shared (WP-1A follow-up request); the tool stays unrunnable until WP-2/3 (DD-A29) |

## 3. 삭제된 테스트와 그 의미의 승계

| 삭제 | 사유 | 승계 |
|---|---|---|
| `qa/test_evaluation_summary_record.py` | DD-A22 (A7: 옛 호스트 `/root/.claude` journal·docs 기록 검사, 제품 동작 아님) | 없음(기록 테스트) |
| `qa/test_orchestration_records_set2.py` | DD-A22 (A7: Codex CLI 경로·과거 agent-id 기록) | 계약 history 불변식은 `tests/public/contracts/test_contract_history.py` |
| `search/test_etri_bridge_permissions.py` | 계약 §9 (ETRI 루트·`MBNAS_ETRI_NAS_ROOT`·mode-000 루트 개념 소멸, DD-A12) | "bridge 예외는 500이 아닌 진단" → `tests/owner/search/test_mixed_pool_bridge.py`(probe 원인 보고·키 집합, `EtriUnavailable`, 읽을 수 없는 vendored 파일 → `SearchSpaceUnavailable`, `/health` 200) |
| `search/test_space_hash_content.py`의 "다른 루트 거부" 케이스 | 계약 §9 (두 번째 ETRI 루트가 없음) | 없음; "내용 변조 → 해시 변화"는 vendored 모듈 사본 변조로 재작성(§2 표의 A3 행) |
| `test_provenance.py`의 default_roots/NOT_HASHED 레이아웃 검사 2건 | 계약 §4가 `default_roots()`·`NOT_HASHED`·`DEFAULT_*_ROOT`를 삭제 | DD-A29: 명시 roots(같은 25개 입력)로 재작성 + `package_roots()` 검사 2건 |

## 4. 알려진 미통과·확인 못 함 (은폐 없음)

2026-10-26 WP-1G 확인 실행(cwd `TARGET_ROOT`, 인터프리터 `runtime/venv/bin/python -B`, `PYTHONDONTWRITEBYTECODE=1`, `CUDA_VISIBLE_DEVICES=""`):

- `pytest -m "not gpu and not live" tests/public` → 4 failed / 560 passed / 31 skipped / 12 deselected / 1 xfailed (74.7 s).
  - `tests/public/contracts/test_source_tree_manifest.py` 2건, `tests/public/infra/test_import_closure.py::test_no_unresolved_reference`: `scripts/dev/source_tree_hash.py`·`scripts/runner/bench_host_gpu.py`가 아직 삭제된 `default_roots`를 import한다. 수정 주체는 WP-1V(WP-1_RESOLUTIONS V2). 테스트는 수정 후의 계약(명시 roots, `manifest()`/`--diff` 유지)을 기준으로 썼다.
  - `tests/public/infra/test_import_safety.py::test_no_module_runs_code_at_import_time[mbnas_model_runtime]`: `mbnas_model_runtime/block_catalog.py`의 모듈 최상위 `_ul(...)`/`_add(...)`/`_torch(...)` 호출(카탈로그 표 생성)을 원래 규칙(최상위 bare call 금지)이 잡는다. 스냅샷 `src/mbnas_server/search/block_catalog.py:82-250`에도 같은 호출이 있어 **스냅샷에서도 실패했을 기존 결함**이며 단언은 완화하지 않았다. 설계자/WP-1C 판단 필요(교차 요청).
- `pytest -m "not gpu and not live and not legacy_records and not host_data" tests/owner` (DD-A16 적응 전 1회) → 4 failed / 1077 passed / 1 skipped / 47 deselected (35분 50초). 실패 4건 중 3건(DD-A16 baseline ArchSpec)은 §2의 `A2 (DD-A16)` 적응 후 해당 파일 재실행 39 passed; 1건(`test_protocol_resolution_v12.py::test_retention_defaults_from_the_server_setting`)은 1차 배치에서 기대 문구를 `MBNAS_CORE_FULL_STATE_RETENTION`으로 바꿨던 것을 **되돌렸다**(원문 기대값 유지, 현 core 문구와 일치해 통과). 문구 변경 여부는 WP-1D 결정(교차 요청).
- 실행하지 않음(확인 못 함): gpu/live/legacy_records/host_data 마커 테스트(GPU 금지·호스트 데이터 없음; 수집만 확인 — 전체 1772 수집, 오류 0), 웹 테스트(WP-1H/V), 적응 후 owner 전체 재실행(WP-1V의 T1 기준선 실행에 맡김).

## 5. 재현

WP-1G 스크래치 도구(프로젝트 밖, `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp1g/`): `placement.py`(배치표), `relocate.py`(스냅샷 sha 검증 + `rewrite_imports.py` Mapper로 import 재작성 → `stage2/`, 보고 `stage2_report.json`), `patches_1..4.py` + `apply.py`(1차 적응, 개수 검증 exact replace → `final2/`), `r2/ed.py` 기록(2차 적응, `r2/adapt2.jsonl`), `r2/gen_md.py`(이 문서), `r2/manifest_rows.py`(PLACEMENT_MANIFEST의 WP-1G 행). 손으로 쓴 파일(conftest 분할, 신규 테스트)은 `tests/`의 본문이 원본이다.


## 6. WP-1V round 1 additions (2026-10-26, WP-1_RESOLUTIONS V1–V3)

| file:line | code | change | reason |
|---|---|---|---|
| `mbnas_web/tests/preset_parity.test.ts:33` | DD-A28 | `'console.paths'` removed from `FIELDS` (comment left in place) | core `preset_paths()` emits `/ABSOLUTE/PATH/TO/...` placeholders and `config/examples/protocol.pilot_smoke.example.json` was regenerated from it (WP-1D, resolution D-5); the web template is deleted in WP-6. All other 22 fields and the baseline-id check are unchanged. |
| `tests/public/tools/api_bearer_only.py:29-37` | V2 | `MBNAS_*` env names → `MBNAS_GW_*`; `settings.worker_python` → `sys.executable` | `GatewaySettings` has prefix `MBNAS_GW_` and no `worker_python` (F-cross-3). Tool, not a test; not collected. |
| `scripts/dev/source_tree_hash.py` (CLI) | DD-A29 | `--server-root/--web-root` + `default_roots` → `--package NAME` (default: 5 packages via `package_roots`) and `--root LABEL=PATH`; `manifest()` and `--diff` unchanged | consumed by `tests/public/contracts/test_source_tree_manifest.py` (no test change). |
| `scripts/dev/check_import_closure.py` | DD-A26/A27 | `TRANSITIONAL_ALLOW` + `owner_worker.main → mbnas_runner` (`names == ["gpu"]`, expires WP-2); new `DATA_EXCEPTIONS` (2 file/literal pairs, expire WP-4) reported under `excepted` | consumed by `tests/public/infra/test_import_closure.py` (no test change). |

No test assertion was changed in round 1.

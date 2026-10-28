# TEST_INVENTORY — 원본 테스트 전수 목록과 분할 후 배치안 (WP-0 / A5)

작성: 2026-10-23, 분석자 A5(claude-fable-5-1). 대상: 스냅샷 `build/source_snapshot/application` (HEAD 18c4815 + 작업 트리, provenance 참조). **본 문서의 모든 실행 수치는 원본 문서가 보고한 값이며 이 환경에서는 어떤 테스트도 실행하지 않았다.** 정적 수치(파일 수·함수 수)는 스냅샷을 AST/정규식으로 센 값이다. 기계 판독용 전체 레코드는 같은 디렉터리의 `TEST_INVENTORY.json`(배열; 원소 0 = meta, 이후 server_test_file 130건, web_test_file 55건)에 있다.

## 0. 결론

- 서버 테스트 117개 파일(정적 test 함수 943개) 중 **owner(mbnas_core 필요) 계열 82개, public 계열 32개, 양쪽 인프라 3개**. public으로 바로 갈 수 있는 것은 contracts/util·datasets·adapters·evaluation(matching/latency 필드)·checkpoint·stage_trainer·benchmark·archive 계열이고, API/orchestration/qa/worker/search 대부분은 단일 프로세스에서 "API 앱 + SQLite + inline worker(engine 포함)"를 결합한 하네스(`tests/worker/harness.py:155-187`) 위에 서 있어서 core 없이는 성립하지 않는다.
- 분할 때문에 손봐야 하는 유형은 20가지(A1~A20, §4)이며 가장 넓게 걸리는 것은 A5(단일 앱 팩토리, 51개 파일), A1(API→worker 직접 구동, 31개), A16(ML skip 게이트, 36개), A8(호스트 데이터, 37개), A3(ETRI 파일 SHA·루트 개념, 15개)이다. 수정 없이는 틀리게 되는 단언은 A3(`test_space_hash_content.py:84-86`의 6개 yolo_ga_runtime 파일명 — DD-A2는 그중 crossover_v22/mixed_hooks를 가져오지 않음), A4(`tests/unit/test_config.py:14-21`의 /root/detection·`.venv`·8000 기본값), A15(runner 허용목록 밖 PROPOSE_GENERATION을 runner worker 프로세스로 실행하는 3개 테스트)다.
- 웹: vitest 파일 35개(정적 it 호출 398개), e2e 파일 10개(mock 정적 test 호출 77개, real 11개). DD-A8 직접 영향은 `tests/fitness.test.ts`, `tests/fixtures.ts:7,68`, `e2e/weights.spec.ts:11,46`(computeFitness), `tests/preset_parity.test.ts`(policy.ts handoff embed), 그리고 `tests/contract.test.ts:9`·`tests/archive_contract.test.ts:27`·`scripts/gen-types.mjs:15`의 `../../MBNAS_server/contracts/openapi.json` 경로다.
- 원본 문서 보고값(§6): 최신은 WORKLOG 2026-10-23 "게이트 1263 passed/1 failed/1 error, outside-gate 294 passed", 웹 "vitest 470, e2e 186/186". 증거 로그 파일(`evidence/`, `evidence_impl/`)은 스냅샷에 없어 **확인 못 함**.

## 1. 입력과 방법

| 항목 | 값 |
|---|---|
| 서버 테스트 루트 | `application/MBNAS_server/tests` — test_*.py 117개 + conftest/harness/stub/helper 13개 (= .py 130개) |
| 웹 | `application/MBNAS_web/tests` 44개 파일, `application/MBNAS_web/e2e` 11개 파일 |
| 읽은 설정·문서 | `tests/conftest.py`, `tests/{api,worker,orchestration,qa,search,archive,benchmark}/conftest.py`, `tests/worker/harness.py`, `tests/worker/stub_handlers.py`, `tests/qa/qa_handlers.py`, `tests/archive/{realdata,synth}.py`, `scripts/run_tests_host.sh`, `docs/TESTING_HOST_20261015.md`, `pyproject.toml [tool.pytest.ini_options]`, `docs/integration_20261021/{TEST_BASELINE_20261021,WORKLOG,PLAN_20261021}.md`, `docs/TEST_EVIDENCE.md`, 웹 `vite.config.ts`, `playwright*.config.ts`, `package.json` |
| 방법 | AST로 import/marker/importorskip/fixture 인자/skip 호출/환경변수 읽기 추출 + 정규식으로 경로·서브프로세스·ETRI 참조 탐지 + 핵심 파일 본문 직접 확인(§3 비고 열의 행 번호). 패키지 매핑은 DD-A1 규칙을 모듈 prefix 표(§2)로 고정해 적용 |
| 하지 않은 것 | pytest/vitest/playwright 실행, GPU, 네트워크, 원본 디렉터리 쓰기 |

원본 pytest 설정(`application/MBNAS_server/pyproject.toml:56-63`): `testpaths=["tests"]`, `addopts="-ra --import-mode=importlib"`, `timeout=300`, 마커 `gpu`, `slow`. 표준 게이트(`scripts/run_tests_host.sh:9-18`): `tests/api tests/unit tests/qa tests/worker tests/archive -m "not gpu" --deselect tests/qa/test_registry_workspace_evidence.py::test_original_research_repository_is_untouched --timeout=600`, 환경 `MBNAS_TEST_ETRI_ROOT=/home/mgjeong/Desktop/etri_hd/etri_nas`, `MBNAS_TEST_ROOT=/data/mgjeong/etri_nas_runs/mbnas_console/test_root`, `MBNAS_TEST_WEIGHTS_DIR=/data/mgjeong/etri_nas_runs/weights`, `MBNAS_TEST_RUNS_ROOT=/data/mgjeong/etri_nas_runs/mbnas_console`, `MBNAS_TEST_ASSUME_ULTRALYTICS=1`, `PYTEST_GPU_LOCK=/data/mgjeong/etri_nas_runs/mbnas_console/locks/gpu.lock`. 게이트 밖 디렉터리: `tests/adapters, benchmark, evaluation, orchestration, registry, search, training`(TESTING_HOST_20261015.md:63).

## 2. 모듈 → 미래 패키지 매핑(이 목록이 적용한 규칙)

DD-A1 (a)~(e)를 모듈 prefix에 적용했다(긴 prefix 우선). `split:`은 한 모듈이 두 패키지로 갈라지는 경우, `ambiguous`는 설계자 결정이 필요한 경우다. 다른 분석(A1 모듈 분류)과 충돌하면 그쪽을 우선하고 본 표의 해당 행만 바꾸면 된다(JSON의 `module_to_future_package`가 파일별로 같은 규칙을 담는다).

| 모듈 prefix | → 패키지 | 비고 |
|---|---|---|
| mbnas_server.adapters | model_runtime | — |
| mbnas_server.datasets | model_runtime | — |
| mbnas_server.registry | model_runtime | ambiguous: specs/probe=model_runtime, evidence scan·/registry route=server/core |
| mbnas_server.search.genotype | model_runtime | DD-A1(b) genotype CPU 관측 |
| mbnas_server.search.block_catalog | model_runtime | DD-A1(b) block catalog 데이터 |
| mbnas_server.search.structure | split:core+model_runtime | gate 규칙=core, 관측/describe=model_runtime |
| mbnas_server.search.etri_bridge | split:model_runtime+core | DD-A2: operator 등록=model_runtime.operators, probe/mixed_search_space=core.search.mixed_pool |
| mbnas_server.search | core | — |
| mbnas_server.protocol.presets | core | ambiguous: 데이터 자체는 공개(GET /protocols/presets), 제공 주체는 core |
| mbnas_server.protocol | core | — |
| mbnas_server.orchestration | core | — |
| mbnas_server.scheduling | core | — |
| mbnas_server.db | core | — |
| mbnas_server.events | core | — |
| mbnas_server.api.contract | contracts | openapi.json 바인딩 유틸 |
| mbnas_server.api.services | core | — |
| mbnas_server.api.model_services | core | ambiguous: declared-only 뷰는 model_runtime로 분리 가능, structure-check 실행은 owner worker |
| mbnas_server.api.app | split:core+server | DD-A3: core 제어면 + server gateway 두 앱 |
| mbnas_server.api | split:core+server | DD-A3 |
| mbnas_server.worker.child | runner | — |
| mbnas_server.worker.contract | runner | — |
| mbnas_server.worker.gpu_guard | runner | — |
| mbnas_server.worker.gpu | runner | — |
| mbnas_server.worker.main | split:runner+core | DD-A4: runner worker loop + owner worker(PROPOSE_GENERATION/structure-check) |
| mbnas_server.worker | split:runner+core | DD-A4 |
| mbnas_server.training.checkpoint | model_runtime | DD-A1(b) checkpoint 포맷 |
| mbnas_server.training.selection | core | trial run 생성(DB) |
| mbnas_server.training.model_artifacts | core | ambiguous: DB·artifact 기록 |
| mbnas_server.training | runner | — |
| mbnas_server.evaluation.handlers | runner | 단, fitness_and_gate는 DD-A5에 따라 core로 이동 대상 |
| mbnas_server.evaluation | runner | — |
| mbnas_server.benchmark | runner | — |
| mbnas_server.provenance.env_lock | runner | ambiguous: 실행 호스트 환경 lock; contracts util 후보 |
| mbnas_server.provenance | contracts | ambiguous: source_hash는 공유 util 후보 |
| mbnas_server.artifacts | server | DD-A6 artifact store; ambiguous: core 등록 측도 사용 |
| mbnas_server.export | server | — |
| mbnas_server.archive | server | DD-A1(e) |
| mbnas_server.config | split:per-package Settings | DD-A7 runtime/ 기본값 |
| mbnas_server.schemas | contracts | — |
| mbnas_server.util | contracts | — |
| mbnas_server | contracts | 패키지 루트 import(`from mbnas_server import schemas`) |

## 3. 서버 테스트 전수 표

열 설명 — `#t`: 정적 test 함수 수(parametrize 확장 제외). `→ 패키지`: import한 모듈의 매핑 집합. `요구사항`: torch/ultralytics/rfdetr/pycocotools/onnx·ort/trt 의존, **gpu**·slow 마커, `host:`(호스트 데이터 종류), `net:`(loopback uvicorn / live stack / HF 오프라인 설정), `sub:`(서브프로세스 종류), `contracts`(openapi.json 파일 읽기), `script:`(scripts/*.py import). `제안 home`: owner(mbnas_core 필요) / public / public(server)=gateway 단독 / public(host_data) / owner(live) / owner(legacy_records) / both(infra) / support. `적응`: §4 코드. 파일별 근거 행 번호는 JSON `notes`/`evidence`에 있다.

| path | kind | #t | mbnas_server import(모듈) | → 패키지 | 요구사항 | conftest fixture | 제안 home | 적응 |
|---|---|---|---|---|---|---|---|---|
| tests/adapters/test_adapters_cpu.py | test | 8 | adapters.base, adapters.etri_hybrid, adapters.etri_hybrid.adapter, adapters.etri_hybrid.criterion, adapters.etri_hybrid.loss, adapters.rfdetr_adapter, adapters.ultralytics_adapter | model_runtime | torch, host:/root/detection/MBNAS_TEST_ETRI_ROOT, sub:git | — | public(host_data) | A4 A8 A16 A19 |
| tests/adapters/test_coco_convert.py | test | 2 | datasets.coco_convert, datasets.manifest | model_runtime | — | make_dataset | public | — |
| tests/adapters/test_predict_geometry_gpu.py | test | 1 | adapters.ultralytics_adapter, evaluation.matching, training.checkpoint | model_runtime, runner | torch, ultralytics, **gpu**, host:/root/detection | — | public | A4 A16 |
| tests/adapters/test_rfdetr_sampler_generator.py | test | 3 | adapters.base, adapters.rfdetr_adapter | model_runtime | torch, rfdetr | — | public | A16 |
| tests/adapters/test_structure_native_cpu.py | test | 13 | adapters.base, adapters.etri_hybrid.adapter, adapters.etri_hybrid.model, adapters.structure_native, api.contract, api.model_services, registry | contracts, core, model_runtime | torch, ultralytics, rfdetr, net:HF_HUB_OFFLINE=1 설정(다운로드 차단) | — | owner | A16 |
| tests/api/conftest.py | conftest | 0 | api.app | split:core+server | — | api_settings, app, host_devices, make_dataset, registry, services, stub, tmp_roots | owner | A1 A5 |
| tests/api/test_access_log_redaction.py | test | 1 | — | — | net:loopback uvicorn 스레드 | app | owner | A5 A14 |
| tests/api/test_artifact_signature_non_ascii.py | test | 1 | — | — | — | client, inline_worker, protocol | owner | A1 A5 |
| tests/api/test_artifacts_export.py | test | 5 | artifacts.registry, db.core, db.repo | core, server | — | api_settings, client, inline_worker, protocol | owner | A1 A5 |
| tests/api/test_auth_cors.py | test | 7 | api.app, api.contract | contracts, split:core+server | contracts | client, tmp_roots | owner | A5 A6 |
| tests/api/test_block_catalog_route.py | test | 4 | search.block_catalog | model_runtime | ultralytics, net:HF_HUB_OFFLINE=1 설정(다운로드 차단) | client | owner | A5 A16 |
| tests/api/test_candidate_detail_lineage.py | test | 3 | api.contract, search.lineage, search.space | contracts, core | ultralytics, host:/root/detection/MBNAS_TEST_ETRI_ROOT/MBNAS_TEST_ROOT | api_settings, client, inline_worker, protocol | owner | A1 A3 A4 A5 A8 A16 |
| tests/api/test_checkpoint_state_row.py | test | 2 | api.contract, api.views, db.core, db.repo | contracts, core, split:core+server | — | client, inline_worker, protocol | owner | A1 A5 |
| tests/api/test_contract_responses.py | test | 4 | api.contract | contracts | contracts | client, inline_worker, protocol | owner | A1 A5 A6 |
| tests/api/test_final_retrain.py | test | 10 | api.contract, artifacts.registry, db.core, db.repo, orchestration.engine, orchestration.final_retrain, training.context | contracts, core, runner, server | — | api_settings, client, conn, inline_worker, make_dataset, protocol, tmp_roots | owner | A1 A2 A5 |
| tests/api/test_generation_freshness.py | test | 2 | artifacts.registry | server | — | client, services, tmp_roots | owner | A5 |
| tests/api/test_generation_multi_seed.py | test | 4 | api.contract, db.core, db.repo, orchestration.engine, scheduling.jobs, search.generation | contracts, core | — | api_settings, client, tmp_roots | owner | A5 |
| tests/api/test_generation_real_cpu.py | test | 1 | api.contract, db.core, db.repo, search.genotype, search.structure, worker.main | contracts, core, model_runtime, split:core+model_runtime, split:runner+core | torch, ultralytics | api_settings, client, tmp_roots | owner | A1 A5 A15 A16 |
| tests/api/test_off_run_guards.py | test | 9 | adapters.ultralytics_adapter, api.contract, artifacts.registry, db.core, db.repo, orchestration.engine, orchestration.final_retrain, protocol.validator, schemas.api, schemas.jobs, training.model_artifacts | contracts, core, model_runtime, server | torch | api_settings, client, ctx_factory, db, protocol, tmp_roots | owner | A5 A16 |
| tests/api/test_postprocess_runtime_row.py | test | 3 | api.contract, orchestration.engine | contracts, core | — | client, inline_worker, protocol | owner | A1 A5 |
| tests/api/test_registry_roots_skipped.py | test | 5 | api.contract, api.routes_meta, schemas | contracts, split:core+server | — | api_settings, client | owner | A5 |
| tests/api/test_registry_structure.py | test | 18 | adapters.rfdetr_adapter, api.app, api.contract, api.model_services | contracts, core, model_runtime, split:core+server | torch, ultralytics, rfdetr, host:MBNAS_TEST_ETRI_ROOT, net:HF_HUB_OFFLINE=1 설정(다운로드 차단), sub:python 자식 프로세스 | api_settings, client, tmp_roots | owner | A3 A5 A8 A16 A17 |
| tests/api/test_runs_idempotency.py | test | 8 | db.core, db.repo, protocol.validator | core | — | api_settings, client, protocol | owner | A5 |
| tests/api/test_set3_health.py | test | 3 | api.app, api.contract | contracts, split:core+server | — | api_settings, client, registry | owner | A3 A5 |
| tests/api/test_sse.py | test | 7 | api.contract, db.core, db.repo | contracts, core | net:loopback uvicorn 스레드 | api_settings, app, client, protocol | owner | A5 A14 |
| tests/api/test_state_machine.py | test | 9 | api.contract, db.core, db.repo, orchestration.engine, scheduling.jobs, schemas.jobs, util.time | contracts, core | — | api_settings, client, inline_worker, protocol | owner | A1 A5 |
| tests/api/test_structure_history.py | test | 1 | api.contract, db.core, db.repo | contracts, core | — | api_settings, client, tmp_roots | owner | A5 |
| tests/api/test_training_selection.py | test | 31 | api.model_services, db.core, db.repo, orchestration.engine, schemas.jobs, search.genotype, training.selection, worker.child | contracts, core, model_runtime, runner | torch, ultralytics, rfdetr, net:HF_HUB_OFFLINE=1 설정(다운로드 차단) | api_settings, client, make_dataset, protocol, tmp_roots | owner | A5 A16 |
| tests/archive/conftest.py | conftest | 0 | api.app | split:core+server | host:/data/mgjeong | host_devices, make_client, registry, synth_root, tmp_roots | public(server) | A5 A8 |
| tests/archive/realdata.py | helper | 0 | — | — | host:/data/mgjeong | — | public(server) | A8 |
| tests/archive/synth.py | helper | 0 | — | — | — | — | public(server) | — |
| tests/archive/test_analysis.py | test | 18 | archive, archive.analysis, archive.parsers, archive.views | server | host:/data/mgjeong | — | public(server) | A8 |
| tests/archive/test_campaign_adapter.py | test | 18 | api.app, api.contract, archive.analysis, archive.campaign, archive.views, config | contracts, server, split:core+server, split:per-package Settings | host:/data/mgjeong, sub:python 자식 프로세스 | host_devices, registry, space, synth_root, tmp_roots | public(server) | A5 A8 A11 |
| tests/archive/test_compare_conditions.py | test | 2 | api.contract | contracts | — | client, synth_root | public(server) | A5 |
| tests/archive/test_perf.py | test | 5 | — | — | host:/data/mgjeong | make_client, real_client, synth_root | public(server) | A5 A8 A18 |
| tests/archive/test_routes.py | test | 25 | api.contract | contracts | host:/data/mgjeong | client, make_client, real_client, synth_root | public(server) | A5 A8 |
| tests/archive/test_scanner.py | test | 19 | api.contract, archive.live, archive.parsers, archive.scanner, archive.views | contracts, server | sub:python 자식 프로세스 | make_client | public(server) | A5 |
| tests/archive/test_security.py | test | 18 | archive.views, util.log_redact | contracts, server | — | client, make_client, synth_root | public(server) | A5 A19 |
| tests/benchmark/conftest.py | conftest | 0 | — | — | host:/root/detection/MBNAS_TEST_ROOT | — | public | A4 A8 |
| tests/benchmark/test_bench_doc.py | test | 6 | — | — | onnx/ort, trt, host:/root/detection/MBNAS_TEST_ROOT/bench/docs, script:bench_host_gpu.py | — | owner(legacy_records) | A4 A7 A8 A9 |
| tests/benchmark/test_benchmark_rfdetr.py | test | 17 | benchmark.accuracy, benchmark.capability, benchmark.handlers, benchmark.pipeline, benchmark.profile, benchmark.subjects, datasets.manifest, schemas.jobs, training.checkpoint | contracts, model_runtime, runner | torch, rfdetr, onnx/ort, trt, **gpu**, slow, host:/root/detection/MBNAS_TEST_ROOT/PILOT, sub:python 자식 프로세스 | settings | public(host_data) | A4 A8 A11 A16 |
| tests/benchmark/test_benchmark_unit.py | test | 11 | adapters.ultralytics_adapter, benchmark.accuracy, benchmark.capability, benchmark.handlers, benchmark.onnx_export, benchmark.onnx_runtime, benchmark.pipeline, benchmark.subjects, benchmark.trt_build, benchmark.trt_runtime, datasets.manifest, schemas.jobs | contracts, model_runtime, runner | torch, ultralytics, onnx/ort, trt, **gpu**, slow, host:/root/detection/MBNAS_TEST_ROOT/PILOT/bench | make_dataset, settings | public(host_data) | A4 A8 A11 A16 |
| tests/conftest.py | conftest | 0 | config, datasets.manifest, db.core, protocol.validator | core, model_runtime, split:per-package Settings | ultralytics, host:/root/detection/MBNAS_TEST_ETRI_ROOT/MBNAS_TEST_ROOT/MBNAS_TEST_WEIGHTS_DIR/PILOT | host_devices, registry, settings, tmp_roots | both(infra) | A3 A4 A8 A11 A12 |
| tests/evaluation/test_evaluator_images.py | test | 7 | datasets.manifest, evaluation.evaluator, evaluation.images, evaluation.latency | model_runtime, runner | pycocotools | make_dataset | public | — |
| tests/evaluation/test_fitness_gate_integration.py | test | 3 | evaluation.handlers, protocol.presets | core, runner | — | — | owner | A20 |
| tests/evaluation/test_latency_gpu.py | test | 1 | evaluation.latency | runner | torch, **gpu** | — | public | A16 |
| tests/evaluation/test_latency_operating_point.py | test | 7 | adapters.etri_hybrid.adapter, adapters.rfdetr_adapter, adapters.ultralytics_adapter, evaluation.evaluator, evaluation.handlers, evaluation.images, evaluation.latency, evaluation.matching, schemas.records, util.atomic | contracts, model_runtime, runner | torch, **gpu** | — | public | A16 A19 |
| tests/evaluation/test_matching.py | test | 7 | evaluation.matching | runner | — | — | public | — |
| tests/orchestration/conftest.py | conftest | 0 | api.app | split:core+server | — | api_settings, app, host_devices, make_dataset, registry, services, stub, tmp_roots | owner | A1 A5 |
| tests/orchestration/test_engine_flow.py | test | 1 | api.contract, db.core, db.repo | contracts, core | — | api_settings, client, inline_worker, protocol | owner | A1 A5 |
| tests/orchestration/test_final_selection.py | test | 4 | db.core, db.repo, orchestration.final_retrain | core | — | conn, settings | owner | A11 |
| tests/orchestration/test_open_generation_progress.py | test | 1 | api.contract, db.core, db.repo | contracts, core | — | api_settings, client, inline_worker, protocol | owner | A1 A5 |
| tests/orchestration/test_retention.py | test | 11 | api.contract, db.core, db.repo, orchestration.engine, orchestration.retention, orchestration.state | contracts, core | — | api_settings, client, conn, inline_worker, make_dataset, protocol, settings, tmp_roots | owner | A1 A2 A5 A11 |
| tests/orchestration/test_semantics.py | test | 13 | db.core, db.repo, orchestration.engine, scheduling.admission, scheduling.planner | core | — | api_settings, client, conn, db, inline_worker, make_dataset, protocol, tmp_roots | owner | A1 A5 |
| tests/qa/conftest.py | conftest | 0 | api.app, db.core | core, split:core+server | torch | api_settings, app, client, conn, host_devices, inline_worker, make_dataset, qa_env, registry, services, stub, tmp_roots | owner | A1 A2 A5 A16 |
| tests/qa/qa_handlers.py | helper | 0 | schemas.jobs, search.handlers, worker.contract | contracts, core, runner | — | — | owner | — |
| tests/qa/test_api_matrix.py | test | 14 | db.core, protocol.validator | core | — | api_settings, client, inline_worker, protocol, registry, services | owner | A1 A5 |
| tests/qa/test_benchmark_evidence.py | test | 7 | adapters.base, adapters.etri_hybrid.adapter, benchmark.capability, benchmark.onnx_export, benchmark.pipeline, benchmark.subjects, datasets.manifest, protocol.presets, training.checkpoint, training.dataset_view, training.stage_trainer | core, model_runtime, runner | torch, trt, **gpu**, slow, host:/root/detection/MBNAS_TEST_ROOT/bench | make_dataset | owner | A4 A8 A13 |
| tests/qa/test_engine_negatives.py | test | 11 | db.core, db.repo, orchestration.engine, protocol.fitness, protocol.promotion, scheduling.costs, scheduling.planner, schemas.records, util.time | contracts, core | — | client, conn, db, inline_worker, make_dataset, run_qa, tmp_roots | owner | A1 A5 |
| tests/qa/test_evaluation_summary_record.py | test | 1 | — | — | — | — | owner(legacy_records) | A7 |
| tests/qa/test_handoff_inputs_record.py | test | 10 | — | — | host:/root/detection/MBNAS_TEST_ETRI_ROOT/MBNAS_TEST_ROOT/docs, sub:git, contracts | — | owner(legacy_records) | A3 A4 A6 A7 A8 |
| tests/qa/test_immutable.py | test | 5 | db.core, db.repo, orchestration.engine | core | — | client, conn, inline_worker, protocol | owner | A1 A5 |
| tests/qa/test_live_stack.py | test | 7 | — | — | host:/root/detection, net:live stack (MBNAS_QA_LIVE) | — | owner(live) | A4 A16 |
| tests/qa/test_orchestration_records_set2.py | test | 8 | — | — | host:docs, contracts | — | owner(legacy_records) | A3 A6 A7 A8 A16 |
| tests/qa/test_registry_workspace_evidence.py | test | 9 | mbnas_server, protocol.presets, protocol.validator, registry | contracts, core, model_runtime | trt, host:/root/detection/MBNAS_TEST_ETRI_ROOT/MBNAS_TEST_ROOT/registry, sub:git, contracts | ctx_factory, make_dataset, registry, tmp_roots | owner(legacy_records) | A4 A6 A7 A8 |
| tests/qa/test_search_fairness_engine.py | test | 8 | config, db.repo, evaluation.handlers, schemas.jobs, training.context, training.handlers | contracts, core, runner, split:per-package Settings | — | conn, run_qa | owner | A1 A11 |
| tests/qa/test_security_negatives.py | test | 21 | api.contract | contracts | host:/root/detection/MBNAS_TEST_ETRI_ROOT/MBNAS_TEST_ROOT/MBNAS_TEST_RUNS_ROOT | api_settings, client, inline_worker, protocol, tmp_roots, validate | owner | A1 A4 A5 A8 |
| tests/qa/test_smoke_script_final_phases.py | test | 5 | — | — | script:smoke_end_to_end.py | api_settings, app, client, inline_worker, protocol | owner | A1 A2 A5 A9 |
| tests/qa/test_sse_server.py | test | 10 | api.contract, db.core, db.repo | contracts, core | net:loopback uvicorn 스레드 | api_settings, app, client, inline_worker, make_dataset, protocol, tmp_roots | owner | A1 A5 A14 |
| tests/qa/test_validator_negatives.py | test | 21 | — | — | **gpu** | client, protocol, registry, validate | owner | A5 |
| tests/registry/test_probe_registry_snapshot.py | test | 1 | registry | model_runtime | trt, slow, host:/root/detection/MBNAS_TEST_ROOT/registry, sub:python 자식 프로세스, script:probe_registry.py | — | public(host_data) | A4 A8 A9 A19 |
| tests/registry/test_registry.py | test | 5 | protocol.presets, protocol.validator, registry, registry.specs, schemas.api | contracts, core, model_runtime | host:/root/detection/MBNAS_TEST_ROOT/registry, contracts | ctx_factory | owner | A4 A6 A8 A13 A16 |
| tests/registry/test_registry_unreadable_roots.py | test | 7 | api.doctor, api.services, registry, schemas.api | contracts, core, model_runtime, split:core+server | host:registry | settings | owner | A8 A11 |
| tests/registry/test_set3_registry_devices.py | test | 7 | adapters.ultralytics_adapter, registry, registry.specs, schemas.api | contracts, model_runtime | trt, **gpu**, host:/root/detection/MBNAS_TEST_ROOT, sub:python 자식 프로세스 | — | public(host_data) | A4 A8 A16 |
| tests/registry/test_weights_manifest_message.py | test | 2 | benchmark.subjects | runner | — | — | public | — |
| tests/search/conftest.py | conftest | 0 | search.space | core | torch, ultralytics, host:/root/detection/MBNAS_TEST_ETRI_ROOT/MBNAS_TEST_ROOT | — | owner | A3 A4 A8 A16 |
| tests/search/test_block_catalog.py | test | 21 | adapters.base, adapters.etri_hybrid.adapter, adapters.structure_native, api.contract, registry, search.block_catalog, search.genotype, search.space, search.structure | contracts, core, model_runtime, split:core+model_runtime | torch, ultralytics, rfdetr, net:HF_HUB_OFFLINE=1 설정(다운로드 차단), sub:python 자식 프로세스 | — | owner | A3 A16 A19 |
| tests/search/test_etri_bridge_permissions.py | test | 5 | search.etri_bridge | split:model_runtime+core | host:MBNAS_TEST_ETRI_ROOT | — | owner | A3 A8 |
| tests/search/test_generation_only.py | test | 14 | db.core, db.repo, orchestration.engine, protocol.validator, schemas.jobs, schemas.protocol, search.generation, search.genotype, search.handlers, search.space, search.streams, search.structure, worker.contract | contracts, core, model_runtime, runner, split:core+model_runtime | — | ctx_factory, db, settings, tmp_roots | owner | A11 |
| tests/search/test_generation_recovery_independent.py | test | 6 | protocol.validator, schemas.jobs, search.generation, search.genotype, search.space, worker.contract | contracts, core, model_runtime, runner | — | settings | owner | A11 |
| tests/search/test_proposers.py | test | 12 | db.core, db.repo, protocol.presets, protocol.validator, schemas.jobs, search.ga, search.handlers, search.random, util.canon | contracts, core | ultralytics, contracts | ctx_factory, db, make_dataset, settings, space, tmp_roots | owner | A6 A11 |
| tests/search/test_repair_evidence_seed.py | test | 1 | protocol.presets, protocol.validator, schemas.jobs, search.handlers | contracts, core | ultralytics, contracts | — | owner | A6 |
| tests/search/test_space_fallback_optin.py | test | 10 | protocol.presets, protocol.validator, search.etri_bridge, search.handlers, search.space | core, split:model_runtime+core | ultralytics, host:MBNAS_TEST_ETRI_ROOT, sub:python 자식 프로세스 | ctx_factory, make_dataset, tmp_roots | owner | A3 A8 |
| tests/search/test_space_genotype.py | test | 8 | adapters.common, search.genotype, search.lineage, search.random, search.space | core, model_runtime | ultralytics | space | owner | A3 |
| tests/search/test_space_hash_content.py | test | 2 | search.etri_bridge, search.space | core, split:model_runtime+core | ultralytics, host:/root/detection/MBNAS_TEST_ETRI_ROOT, sub:python 자식 프로세스 | — | owner | A3 A4 A8 A16 |
| tests/search/test_structure_adversarial.py | test | 8 | search.genotype, search.structure | model_runtime, split:core+model_runtime | — | — | owner | — |
| tests/search/test_structure_cpu.py | test | 7 | api.model_services, search.genotype, search.structure | core, model_runtime, split:core+model_runtime | torch, ultralytics | — | owner | A16 |
| tests/search/test_structure_observation.py | test | 22 | search.etri_bridge, search.genotype, search.structure | model_runtime, split:core+model_runtime, split:model_runtime+core | torch, ultralytics, host:MBNAS_TEST_ETRI_ROOT | — | owner | A3 A8 A16 |
| tests/search/test_structure_runtime_cpu.py | test | 3 | search.genotype, search.structure | model_runtime, split:core+model_runtime | torch, ultralytics | — | owner | A16 |
| tests/training/test_checkpoint.py | test | 16 | adapters.base, training.checkpoint, util.atomic | contracts, model_runtime | torch, **gpu** | — | public | A16 |
| tests/training/test_explicit_cpu.py | test | 1 | db.core, db.repo, protocol.presets, protocol.validator, schemas.jobs, training.checkpoint, training.handlers, training.model_artifacts, training.selection | contracts, core, model_runtime, runner | torch, ultralytics | ctx_factory, make_dataset, settings | owner | A11 A16 |
| tests/training/test_final_job_gpu.py | test | 1 | config, datasets.manifest, datasets.yaml_safe, protocol.presets, protocol.validator, provenance.env_lock, registry, schemas.jobs, training.context, training.handlers | contracts, core, model_runtime, runner, split:per-package Settings | torch, **gpu**, slow, host:/root/detection/PILOT/registry | pilot_required | owner | A4 A8 A11 A13 A16 |
| tests/training/test_job_gpu.py | test | 2 | config, datasets.manifest, evaluation.handlers, protocol.presets, protocol.validator, provenance.env_lock, registry, schemas.jobs, training.checkpoint, training.handlers, util.time | contracts, core, model_runtime, runner, split:per-package Settings | torch, **gpu**, slow, host:/root/detection/PILOT/registry | pilot_required | owner | A4 A8 A11 A13 A16 |
| tests/training/test_model_artifacts.py | test | 8 | adapters.ultralytics_adapter, api.model_services, artifacts.registry, db.core, db.repo, training.model_artifacts, training.recipe | core, model_runtime, runner, server | — | db, settings | owner | A11 |
| tests/training/test_recipe_view_handlers.py | test | 8 | adapters.base, datasets.manifest, protocol.presets, protocol.validator, schemas.jobs, training.context, training.dataset_view, training.handlers, training.recipe | contracts, core, model_runtime, runner | — | make_dataset, settings, tmp_roots | owner | A11 A13 |
| tests/training/test_resume_equivalence_script.py | test | 1 | — | — | torch, script:resume_equivalence.py | — | public | A9 A16 |
| tests/training/test_rfdetr_housekeeping_gpu.py | test | 1 | config, datasets.manifest, protocol.presets, protocol.validator, provenance.env_lock, registry, schemas.jobs, training.checkpoint, training.handlers, util.atomic | contracts, core, model_runtime, runner, split:per-package Settings | torch, **gpu**, slow, host:/root/detection/PILOT/registry | pilot_required | owner | A4 A8 A11 A13 A16 |
| tests/training/test_stage_trainer.py | test | 18 | adapters.base, adapters.etri_hybrid.adapter, datasets.manifest, training.checkpoint, training.dataset_view, training.stage_trainer | model_runtime, runner | torch, slow | make_dataset | public | A16 |
| tests/unit/test_artifacts_registry.py | test | 5 | artifacts.registry, db.core, db.repo, protocol.presets | core, server | — | db | owner | — |
| tests/unit/test_config.py | test | 6 | config | split:per-package Settings | host:/root/detection/MBNAS_TEST_ETRI_ROOT | — | both(infra) | A3 A4 A8 A11 |
| tests/unit/test_contract_history.py | test | 5 | — | — | sub:python 자식 프로세스, contracts | — | public | A6 |
| tests/unit/test_datasets.py | test | 15 | datasets.coco_convert, datasets.manifest, datasets.subset, datasets.yaml_safe | model_runtime | host:/root/detection/PILOT, sub:python 자식 프로세스, script:make_pilot_subset.py | make_dataset, pilot_required, settings, tmp_roots | public(host_data) | A4 A8 A9 A11 |
| tests/unit/test_db.py | test | 15 | db.core, db.repo, util.time | contracts, core | — | db, settings | owner | A11 |
| tests/unit/test_db_migration.py | test | 2 | db.core, db.repo | core | — | — | owner | — |
| tests/unit/test_gpu_lock_guard.py | test | 4 | — | — | sub:python 자식 프로세스 | — | both(infra) | A12 |
| tests/unit/test_latency_record_fields.py | test | 6 | evaluation.latency | runner | — | — | public | — |
| tests/unit/test_log_redact.py | test | 7 | util.log_redact | contracts | — | — | public | — |
| tests/unit/test_protocol_fitness.py | test | 7 | protocol.fitness | core | — | — | owner | — |
| tests/unit/test_protocol_presets.py | test | 7 | protocol.presets | core | host:/root/detection/MBNAS_TEST_ETRI_ROOT | — | owner | A4 A8 |
| tests/unit/test_protocol_promotion.py | test | 15 | evaluation.handlers, protocol.fitness, protocol.presets, protocol.promotion, schemas | contracts, core, runner | — | — | owner | A20 |
| tests/unit/test_protocol_resolution_v12.py | test | 12 | protocol.presets, protocol.validator | core | — | ctx_factory, make_dataset, settings, tmp_roots | owner | A11 |
| tests/unit/test_protocol_validator.py | test | 39 | protocol.presets, protocol.validator | core | **gpu**, host:/root/detection/MBNAS_TEST_ROOT/PILOT | ctx_factory, make_dataset, pilot_required, registry, tmp_roots | owner | A4 A8 |
| tests/unit/test_provenance.py | test | 8 | provenance.env_lock, provenance.source_hash | contracts, runner | sub:python 자식 프로세스, script:source_tree_hash.py | — | public | A4 A9 |
| tests/unit/test_schemas_contract.py | test | 33 | api.contract, api.model_services, protocol.presets, schemas, schemas.archive, search.genotype, search.structure, training.selection | contracts, core, model_runtime, split:core+model_runtime | torch, ultralytics, contracts | — | owner | A6 A16 |
| tests/unit/test_schemas_jobs.py | test | 4 | schemas.jobs | contracts | — | — | public | — |
| tests/unit/test_source_tree_manifest.py | test | 2 | provenance.source_hash | contracts | sub:python 자식 프로세스, script:source_tree_hash.py | — | public | A9 |
| tests/unit/test_util_canon_atomic_time.py | test | 12 | util.atomic, util.canon, util.time | contracts | — | — | public | — |
| tests/unit/test_util_paths.py | test | 10 | util.paths | contracts | — | — | public | — |
| tests/worker/conftest.py | conftest | 0 | api.app | split:core+server | — | api_settings, app, host_devices, make_dataset, registry, services, stub, tmp_roots | owner | A1 A5 |
| tests/worker/harness.py | helper | 0 | api.services, config, db.core, db.repo, orchestration.engine, protocol.presets, scheduling.jobs, worker.child, worker.contract, worker.main | core, runner, split:per-package Settings, split:runner+core | host:/root/detection/MBNAS_TEST_ETRI_ROOT/MBNAS_TEST_ROOT/MBNAS_TEST_WEIGHTS_DIR | — | owner | A1 A2 A4 A8 A11 |
| tests/worker/stub_handlers.py | helper | 0 | config, schemas.jobs, training.context, util.atomic, worker.contract | contracts, runner, split:per-package Settings | — | — | both(infra) | A11 |
| tests/worker/test_blocked_result.py | test | 1 | db.core, db.repo, scheduling.jobs | core | — | api_settings, client, inline_worker, protocol | owner | A1 A5 |
| tests/worker/test_generation_crash.py | test | 1 | artifacts.registry, db.core, db.repo, schemas.jobs, search.generation | contracts, core, server | torch, ultralytics, slow, host:MBNAS_TEST_ETRI_ROOT, sub:worker 프로세스 spawn;python 자식 프로세스 | api_settings, client, tmp_roots | owner | A2 A3 A5 A8 A15 A16 |
| tests/worker/test_gpu_occupancy.py | test | 10 | db.core, db.repo, util.time, worker.gpu, worker.gpu_guard, worker.main | contracts, core, runner, split:runner+core | sub:worker 프로세스 spawn;python 자식 프로세스, script:dev_stack.py | api_settings, client, inline_worker, make_dataset, stub, tmp_roots | owner | A1 A2 A5 A9 |
| tests/worker/test_import_safety.py | test | 3 | — | — | sub:worker 프로세스 spawn;python 자식 프로세스 | — | both(infra) | A2 A10 A19 |
| tests/worker/test_lease.py | test | 10 | db.core, db.repo, scheduling.jobs, schemas.jobs, util.time, worker.main | contracts, core, split:runner+core | — | api_settings, client, inline_worker, protocol | owner | A1 A5 |
| tests/worker/test_native_handlers.py | test | 2 | adapters.base, adapters.ultralytics_adapter, db.core, db.repo, scheduling.jobs, worker.child, worker.main | core, model_runtime, runner, split:runner+core | torch, slow | api_settings, client, make_dataset, tmp_roots | owner | A1 A2 A5 A15 A16 |
| tests/worker/test_progress_metrics.py | test | 2 | schemas.jobs, worker.main | contracts, split:runner+core | — | — | owner | — |
| tests/worker/test_subprocess.py | test | 5 | db.core, db.repo, worker.main | core, split:runner+core | torch, slow, sub:worker 프로세스 spawn;python 자식 프로세스 | api_settings, client, make_dataset, tmp_roots | owner | A1 A2 A5 A16 |
| tests/worker/test_worker_integrity.py | test | 2 | artifacts.registry, db.core, db.repo, worker.main | core, server, split:runner+core | — | api_settings, client, protocol | owner | A1 A2 A5 |

### 3.1 집계

디렉터리별:

| 디렉터리 | 테스트 파일 수 | 정적 test 함수 수 |
|---|---|---|
| adapters | 5 | 27 |
| api | 22 | 138 |
| archive | 7 | 105 |
| benchmark | 3 | 34 |
| evaluation | 5 | 25 |
| orchestration | 5 | 30 |
| qa | 14 | 137 |
| registry | 5 | 22 |
| search | 13 | 119 |
| training | 9 | 56 |
| unit | 20 | 214 |
| worker | 9 | 36 |

제안 home별(테스트 파일):

| 제안 home | 테스트 파일 수 |
|---|---|
| both(infra) | 3 |
| owner | 76 |
| owner(legacy_records) | 5 |
| owner(live) | 1 |
| public | 19 |
| public(host_data) | 6 |
| public(server) | 7 |

테스트가 import하는 미래 패키지(파일 수; 한 파일이 여러 패키지에 걸칠 수 있음):

| → 패키지(테스트가 import) | 테스트 파일 수 |
|---|---|
| contracts | 60 |
| core | 61 |
| model_runtime | 39 |
| runner | 27 |
| server | 12 |
| split:core+model_runtime | 8 |
| split:core+server | 7 |
| split:model_runtime+core | 4 |
| split:per-package Settings | 6 |
| split:runner+core | 7 |

요구사항별:

| 요구사항 | 테스트 파일 수 |
|---|---|
| torch | 28 |
| ultralytics | 20 |
| rfdetr | 6 |
| pycocotools | 1 |
| gpu_marker | 13 |
| slow_marker | 11 |
| host_data | 33 |
| live_stack | 1 |
| loopback_uvicorn | 3 |
| subprocess | 21 |
| reads_contract_files | 10 |
| imports_script | 8 |

conftest fixture 사용 빈도(테스트 파일 기준):

| conftest fixture | 사용 테스트 파일 수 | 정의 위치 |
|---|---|---|
| client | 42 | 동상 + tests/archive/conftest.py:102 |
| api_settings | 29 | tests/{api,worker,orchestration,qa}/conftest.py |
| tmp_roots | 27 | tests/conftest.py:94 |
| protocol | 25 | 동상 |
| make_dataset | 22 | tests/conftest.py:119 |
| inline_worker | 21 | 동상 |
| settings | 14 | tests/conftest.py:102 |
| ctx_factory | 9 | tests/conftest.py:164 |
| db | 8 | tests/conftest.py:189 |
| conn | 7 | tests/qa/conftest.py:66 (+orchestration/test_final_selection 로컬) |
| registry | 6 | tests/conftest.py:153 |
| synth_root | 5 | tests/archive/conftest.py:70 |
| pilot_required | 5 | tests/conftest.py:179 |
| app | 4 | 동상 |
| make_client | 4 | tests/archive/conftest.py:78 |
| space | 3 | tests/search/conftest.py:22(session) |
| services | 2 | 동상 |
| real_client | 2 | tests/archive/conftest.py:107 |
| run_qa | 2 | tests/qa/conftest.py:101 |
| validate | 2 | tests/qa/conftest.py:75 |
| host_devices | 1 | tests/conftest.py:159 |
| stub | 1 | 동상 |

## 4. 분할 때문에 필요한 적응 목록(이유 포함)

| 코드 | 내용 | 파일 수 | 파일(발췌) |
|---|---|---|---|
| A1 | API/orchestration 테스트가 공유 SQLite 위에서 worker(inline_worker/Worker.tick)를 직접 구동 → 분할 후 core 앱 + owner worker(in-process) + runner 프로토콜 에뮬레이터(InlineRunner: claim→progress→result over /core/v1/runner)로 하네스 재작성 | 31 | tests/api/conftest.py, tests/api/test_artifact_signature_non_ascii.py, tests/api/test_artifacts_export.py, tests/api/test_candidate_detail_lineage.py, tests/api/test_checkpoint_state_row.py, tests/api/test_contract_responses.py, tests/api/test_final_retrain.py, tests/api/test_generation_real_cpu.py, tests/api/test_postprocess_runtime_row.py, tests/api/test_state_machine.py, tests/orchestration/conftest.py, tests/orchestration/test_engine_flow.py, tests/orchestration/test_open_generation_progress.py, tests/orchestration/test_retention.py, tests/orchestration/test_semantics.py, tests/qa/conftest |
| A2 | child/worker가 DB 경로(MBNAS_DB_PATH, child.load_context(db_path))로 컨텍스트를 읽거나 stub handler가 MBNAS_DB_PATH/MBNAS_OUTPUT_ROOT env로 Settings.load()함 → WP-2 파일/HTTP 기반 컨텍스트·runner Settings로 변경되면 테스트 입력 교체 | 11 | tests/api/test_final_retrain.py, tests/orchestration/test_retention.py, tests/qa/conftest.py, tests/qa/test_smoke_script_final_phases.py, tests/worker/harness.py, tests/worker/test_generation_crash.py, tests/worker/test_gpu_occupancy.py, tests/worker/test_import_safety.py, tests/worker/test_native_handlers.py, tests/worker/test_subprocess.py, tests/worker/test_worker_integrity.py |
| A3 | ETRI yolo_ga_runtime 파일명·SHA(files_sha256, reference 경로, ETRI root probe) 단언 → DD-A2 vendoring 후 해시 정의(공간 해시 입력 파일 집합)와 '루트' 개념을 재정의해야 함 | 15 | tests/api/test_candidate_detail_lineage.py, tests/api/test_registry_structure.py, tests/api/test_set3_health.py, tests/conftest.py, tests/qa/test_handoff_inputs_record.py, tests/qa/test_orchestration_records_set2.py, tests/search/conftest.py, tests/search/test_block_catalog.py, tests/search/test_etri_bridge_permissions.py, tests/search/test_space_fallback_optin.py, tests/search/test_space_genotype.py, tests/search/test_space_hash_content.py, tests/search/test_structure_observation.py, tests/unit/test_config.py, tests/worker/test_generation_crash.py |
| A4 | /root/detection·MBNAS_server/.venv·127.0.0.1:8000 기본값 단언 또는 하드코딩 → DD-A7(runtime/, 8700/8710/8720) 기본값으로 재작성 | 27 | tests/adapters/test_adapters_cpu.py, tests/adapters/test_predict_geometry_gpu.py, tests/api/test_candidate_detail_lineage.py, tests/benchmark/conftest.py, tests/benchmark/test_bench_doc.py, tests/benchmark/test_benchmark_rfdetr.py, tests/benchmark/test_benchmark_unit.py, tests/conftest.py, tests/qa/test_benchmark_evidence.py, tests/qa/test_handoff_inputs_record.py, tests/qa/test_live_stack.py, tests/qa/test_registry_workspace_evidence.py, tests/qa/test_security_negatives.py, tests/registry/test_probe_registry_snapshot.py, tests/registry/test_registry.py, tests/registry/test_set3_registry_devic |
| A5 | 단일 앱 팩토리 create_app(settings, services)+AppServices(DB) 사용 → owner: core 앱+gateway 앱 결합 하네스, public(archive): gateway 단독 팩토리 | 51 | tests/api/conftest.py, tests/api/test_access_log_redaction.py, tests/api/test_artifact_signature_non_ascii.py, tests/api/test_artifacts_export.py, tests/api/test_auth_cors.py, tests/api/test_block_catalog_route.py, tests/api/test_candidate_detail_lineage.py, tests/api/test_checkpoint_state_row.py, tests/api/test_contract_responses.py, tests/api/test_final_retrain.py, tests/api/test_generation_freshness.py, tests/api/test_generation_multi_seed.py, tests/api/test_generation_real_cpu.py, tests/api/test_off_run_guards.py, tests/api/test_postprocess_runtime_row.py, tests/api/test_registry_roots_ski |
| A6 | contracts/openapi.json·history를 MBNAS_server 루트 기준 상대경로로 읽음 → mbnas_contracts 패키지 데이터 경로 헬퍼로 교체 | 10 | tests/api/test_auth_cors.py, tests/api/test_contract_responses.py, tests/qa/test_handoff_inputs_record.py, tests/qa/test_orchestration_records_set2.py, tests/qa/test_registry_workspace_evidence.py, tests/registry/test_registry.py, tests/search/test_proposers.py, tests/search/test_repair_evidence_seed.py, tests/unit/test_contract_history.py, tests/unit/test_schemas_contract.py |
| A7 | 원본 저장소 문서/호스트 기록(ACCEPTANCE_RESULTS, ENVIRONMENT_AUDIT, evaluation_log, STATE.json, Codex CLI, /root/.claude journal) 검사 → 제품 테스트 아님; 기본 deselect 또는 제외 | 5 | tests/benchmark/test_bench_doc.py, tests/qa/test_evaluation_summary_record.py, tests/qa/test_handoff_inputs_record.py, tests/qa/test_orchestration_records_set2.py, tests/qa/test_registry_workspace_evidence.py |
| A8 | 실제 호스트 데이터 필요(PILOT 서브셋, registry probe/bench evidence, /data/mgjeong 실제 run, 가중치, ETRI checkout) → host_data 마커 + runtime/ 경로 주입, 없으면 skip | 37 | tests/adapters/test_adapters_cpu.py, tests/api/test_candidate_detail_lineage.py, tests/api/test_registry_structure.py, tests/archive/conftest.py, tests/archive/realdata.py, tests/archive/test_analysis.py, tests/archive/test_campaign_adapter.py, tests/archive/test_perf.py, tests/archive/test_routes.py, tests/benchmark/conftest.py, tests/benchmark/test_bench_doc.py, tests/benchmark/test_benchmark_rfdetr.py, tests/benchmark/test_benchmark_unit.py, tests/conftest.py, tests/qa/test_benchmark_evidence.py, tests/qa/test_handoff_inputs_record.py, tests/qa/test_orchestration_records_set2.py, tests/qa/t |
| A9 | scripts/*.py를 파일 경로로 import/실행 → 스크립트를 소유 패키지로 이동하고 경로 상수 갱신 | 8 | tests/benchmark/test_bench_doc.py, tests/qa/test_smoke_script_final_phases.py, tests/registry/test_probe_registry_snapshot.py, tests/training/test_resume_equivalence_script.py, tests/unit/test_datasets.py, tests/unit/test_provenance.py, tests/unit/test_source_tree_manifest.py, tests/worker/test_gpu_occupancy.py |
| A10 | mbnas_server 패키지 전체 import-walk / `-m mbnas_server.worker` → 패키지별(core/runner/server/model_runtime/contracts)로 복제 | 1 | tests/worker/test_import_safety.py |
| A11 | conftest `settings`(mbnas_server.config.Settings: etri_nas_root, weights_dir, dataset_roots, output_roots) 사용 → 패키지별 Settings fixture | 23 | tests/archive/test_campaign_adapter.py, tests/benchmark/test_benchmark_rfdetr.py, tests/benchmark/test_benchmark_unit.py, tests/conftest.py, tests/orchestration/test_final_selection.py, tests/orchestration/test_retention.py, tests/qa/test_search_fairness_engine.py, tests/registry/test_registry_unreadable_roots.py, tests/search/test_generation_only.py, tests/search/test_generation_recovery_independent.py, tests/search/test_proposers.py, tests/training/test_explicit_cpu.py, tests/training/test_final_job_gpu.py, tests/training/test_job_gpu.py, tests/training/test_model_artifacts.py, tests/trainin |
| A12 | GPU lock guard/CUDA teardown 감시(conftest)와 그 자체 테스트 → owner·public 양쪽 conftest에 유지 | 2 | tests/conftest.py, tests/unit/test_gpu_lock_guard.py |
| A13 | runner/model_runtime 테스트가 protocol.validator/presets(core)로 프로토콜을 만든 뒤 handler를 호출 → 고정 resolved-protocol JSON fixture로 대체하면 public 이동 가능 | 6 | tests/qa/test_benchmark_evidence.py, tests/registry/test_registry.py, tests/training/test_final_job_gpu.py, tests/training/test_job_gpu.py, tests/training/test_recipe_view_handlers.py, tests/training/test_rfdetr_housekeeping_gpu.py |
| A14 | 127.0.0.1 임시 포트 uvicorn 스레드 기동(SSE/access log) — 외부 네트워크 아님, 유지 | 3 | tests/api/test_access_log_redaction.py, tests/api/test_sse.py, tests/qa/test_sse_server.py |
| A15 | DD-A4 runner JobKind 허용목록 밖(PROPOSE_GENERATION)을 runner worker 프로세스로 실행 → owner worker 경로로 재지정 | 3 | tests/api/test_generation_real_cpu.py, tests/worker/test_generation_crash.py, tests/worker/test_native_handlers.py |
| A16 | 모듈 레벨 importorskip/skipif로 ML 부재 시 skip — 유지. 단 pycocotools·onnxruntime·rfdetr는 runner extras 정의와 일치시켜야 함 | 36 | tests/adapters/test_adapters_cpu.py, tests/adapters/test_predict_geometry_gpu.py, tests/adapters/test_rfdetr_sampler_generator.py, tests/adapters/test_structure_native_cpu.py, tests/api/test_block_catalog_route.py, tests/api/test_candidate_detail_lineage.py, tests/api/test_generation_real_cpu.py, tests/api/test_off_run_guards.py, tests/api/test_registry_structure.py, tests/api/test_training_selection.py, tests/benchmark/test_benchmark_rfdetr.py, tests/benchmark/test_benchmark_unit.py, tests/evaluation/test_latency_gpu.py, tests/evaluation/test_latency_operating_point.py, tests/qa/conftest.py,  |
| A17 | DD-41 GET import-light 검사(subprocess에서 harness+create_app 구동, torch.cuda._lazy_init 감시) → gateway/core 분리 후 GET 경로 소유자(core 또는 model_runtime 선언 뷰)에 맞춰 드라이버 재작성 | 1 | tests/api/test_registry_structure.py |
| A18 | DD-24/DD-40 성능 예산(cold 300 ms)·GC freeze — 부하 시 flake, 게이트 판정 규칙 유지 | 1 | tests/archive/test_perf.py |
| A19 | 테스트 내부 경로 상수(SRC=…/src/mbnas_server, SERVER_ROOT/.venv/bin/python, tests/worker PYTHONPATH) → 새 레이아웃 경로 | 6 | tests/adapters/test_adapters_cpu.py, tests/archive/test_security.py, tests/evaluation/test_latency_operating_point.py, tests/registry/test_probe_registry_snapshot.py, tests/search/test_block_catalog.py, tests/worker/test_import_safety.py |
| A20 | DD-A5: runner는 fitness/gate를 계산하지 않음 → evaluation.handlers.fitness_and_gate 사용 테스트는 core 측 테스트로 이동 | 2 | tests/evaluation/test_fitness_gate_integration.py, tests/unit/test_protocol_promotion.py |

세부 근거(가장 중요한 것부터):

1. **A1/A5 — 하네스가 core를 전제한다.** `tests/worker/harness.py:155-187`의 `InlineWorker.execute`는 `jobs.start_attempt`(scheduling) → `engine.job_started`(orchestration) → `child.load_context(self.settings.db_path, …)`(worker) → `child.run_job` → `Worker._finalize`를 한 프로세스에서 호출한다. `tests/{api,worker,orchestration,qa}/conftest.py`의 `client`/`inline_worker`/`protocol` fixture가 모두 이 위에 있고, `tests/qa/conftest.py:101-125`의 `run_qa`는 실제 proposer(`qa_handlers.py:16-18`)까지 돌린다. 분할 후 이 하네스는 (i) core 제어면 앱 + gateway 앱을 TestClient/ASGI transport로 묶고, (ii) owner worker는 in-process로, (iii) 학습·평가 kind는 `/core/v1/runner` 프로토콜을 흉내 내는 InlineRunner(stub handler 유지)로 교체해야 한다. 이 하네스가 바뀌면 `inline_worker`를 쓰는 21개 파일과 `client`를 쓰는 42개 파일이 한꺼번에 영향을 받는다.
2. **A2/A15 — child의 DB 읽기와 runner 허용목록.** `src/mbnas_server/worker/child.py:38-40,113-117`는 `file:{db}?mode=ro`로 SQLite를 직접 연다. `tests/worker/test_subprocess.py:33-46`은 `MBNAS_DB_PATH`를 넘겨 `python -m mbnas_server.worker`를 띄우고, `tests/worker/test_native_handlers.py:46`은 `child.load_context(api_settings.db_path, …)`를 직접 호출한다. WP-2(파일/HTTP 컨텍스트)가 들어오면 이 입력이 바뀐다. 또 `tests/worker/test_generation_crash.py:49-53`, `tests/api/test_generation_real_cpu.py:13,32-37`, `tests/worker/test_native_handlers.py:27`은 PROPOSE_GENERATION을 "worker"로 실행하는데 DD-A4는 이를 owner worker 전용으로 정했으므로 대상 프로세스를 바꿔야 한다. `tests/worker/test_lease.py`의 lease 의미론은 DD-A4가 `repo.acquire_lease`를 재사용하기로 했으니 core 측에서 그대로 살리고, runner 측 claim(HTTP)은 신규 테스트가 필요하다.
3. **A3 — ETRI 파일 SHA 정의.** `tests/search/test_space_hash_content.py:84-86`은 `files_sha256`에 `yolo_ga_runtime/{__init__,runtime,crossover_v22,mixed_hooks,mixed_blocks,mixed_search_space}.py` 6개가 모두 있어야 한다고 단언하고, 같은 파일 43-54행은 ETRI checkout의 `yolo_ga_runtime/*.py`를 복사해 가짜 `.git/HEAD`를 둔다. `tests/search/test_space_genotype.py:24-25`도 4개 파일명을 단언한다. DD-A2는 mixed_blocks/mixed_search_space와 runtime의 최소 심볼만 vendoring하고 crossover_v22·mixed_hooks·pipeline은 가져오지 않으므로 공간 해시의 "내용 해시 입력 집합"을 vendored 모듈 기준으로 재정의하지 않으면 이 단언은 성립할 수 없다. `tests/search/test_block_catalog.py:67,70-71`의 `reference == "yolo_ga_runtime/mixed_blocks.py"`, `tests/search/test_etri_bridge_permissions.py:36-54`의 "읽을 수 없는 ETRI 루트" 진단, `tests/search/conftest.py:34-35`·`tests/api/test_candidate_detail_lineage.py:39-40`의 `etri_root` 주입, `tests/search/test_structure_observation.py:372-384`의 ETRI_ROOT 의존 fixture, `tests/search/test_space_fallback_optin.py:85-102`의 fallback opt-in도 같은 축이다(vendoring 후에는 "ETRI 풀 부재" 시나리오 자체가 사라지거나 provenance 전용이 된다).
4. **A4 — /root/detection 기본값.** `tests/unit/test_config.py:14-21`은 `Settings()` 기본값이 `/root/detection/MBNAS_runs/db/mbnas.sqlite3`, `dataset_roots == ["/root/detection/datasets", "/root/detection/MBNAS_data"]`, `etri_nas_root == "/root/detection/ETRI_NAS"`, `worker_python.endswith("MBNAS_server/.venv/bin/python")`, `(host, port) == ("127.0.0.1", 8000)`이라고 단언한다. DD-A7(runtime/, 8700/8710/8720)과 패키지별 Settings 분리에 맞춰 다시 써야 한다. 주입이 불가능한 하드코딩은 `tests/training/test_job_gpu.py:22,36`, `test_final_job_gpu.py:15,29`, `test_rfdetr_housekeeping_gpu.py:14,36`(REAL_ROOT), `tests/adapters/test_predict_geometry_gpu.py:16`(WEIGHTS), `tests/unit/test_datasets.py:149`(pilot 경로 — conftest `PILOT_ROOT`와 불일치), `tests/qa/test_live_stack.py:18`(.env 경로)이다. `tests/unit/test_protocol_presets.py:60-64`는 PILOT_SMOKE preset의 기본 console.paths가 `/root/detection/MBNAS_data/pilot_visdrone_v1/...`임을 단언하므로 preset 기본 경로 자체의 결정이 필요하다. `tests/api/test_registry_roots_skipped.py:30,34`의 문자열은 가짜 진단값이라 변경 불필요.
5. **A6/A19 — 저장소 레이아웃 상수.** `tests/unit/test_contract_history.py:14-16`, `tests/unit/test_schemas_contract.py:15`, `tests/registry/test_registry.py:17`은 `parents[2]/contracts`를 읽고, `tests/worker/test_import_safety.py:19`·`tests/search/test_block_catalog.py:181,199`·`tests/registry/test_probe_registry_snapshot.py:53`은 `src/` 경로를 PYTHONPATH로 넘긴다. `tests/unit/test_provenance.py:54-62,129-130`은 `MBNAS_server/*`·`MBNAS_web/*` 입력 목록이 `default_roots()` 라벨과 같아야 한다고 단언하므로 5패키지 레이아웃에서 artifact_ref 정의를 다시 정해야 한다(`source_hash.py`의 소유 패키지 결정과 함께).
6. **A7 — 제품 테스트가 아닌 기록 검사.** `tests/qa/test_handoff_inputs_record.py`, `test_orchestration_records_set2.py`, `test_registry_workspace_evidence.py`, `test_evaluation_summary_record.py`, `tests/benchmark/test_bench_doc.py`(문서 부분)는 ACCEPTANCE_RESULTS/ENVIRONMENT_AUDIT/STATE.json/Codex CLI/`/root/.claude` 저널/ETRI git 상태를 검사한다. 표준 게이트도 이미 1건을 deselect한다(`scripts/run_tests_host.sh:17`). 새 저장소에서는 `legacy_records` 마커로 묶어 기본 제외하는 것을 권고한다. 단 `test_orchestration_records_set2.py::test_s2_f4`가 보던 계약 이력 불변식은 `tests/unit/test_contract_history.py`가 제품 테스트로 덮는다.
7. **A8/A11 — 호스트 데이터와 Settings fixture.** `tests/conftest.py:44-53,102-109`는 `MBNAS_TEST_ROOT` 아래 `MBNAS_data/MBNAS_runs/ETRI_NAS`를 가정하고 `settings` fixture의 dataset_roots/output_roots에 그 경로를 넣는다. TARGET_ROOT에서는 `runtime/test_root/{MBNAS_data,MBNAS_runs}`로 바꾸되 PILOT 서브셋·probe/bench evidence·`/data/mgjeong` 실제 run(`tests/archive/realdata.py:8-13`, `tests/archive/test_campaign_adapter.py:30`)은 있으면 쓰고 없으면 skip하는 `host_data` 마커로 명시한다.
8. **A13/A20 — runner 테스트의 core 의존.** `tests/training/test_recipe_view_handlers.py:11-17`, `tests/training/test_*_gpu.py`, `tests/benchmark/test_benchmark_rfdetr.py`는 `protocol.presets/validator`로 프로토콜을 만들고 handler를 돌린다. 고정 resolved-protocol JSON fixture(contracts에 두는 예시)를 쓰면 public으로 옮길 수 있다. `tests/evaluation/test_fitness_gate_integration.py:6`의 `evaluation.handlers.fitness_and_gate`는 DD-A5("runner는 fitness/gate를 계산하지 않는다")에 따라 core로 옮겨야 하므로 이 테스트는 owner다.
9. **A9 — 스크립트 import.** `smoke_end_to_end.py`(qa), `resume_equivalence.py`(training), `bench_host_gpu.py`(benchmark), `probe_registry.py`(registry), `dev_stack.py`(worker), `source_tree_hash.py`(unit), `make_pilot_subset.py`(unit)를 파일 경로로 import/실행한다. 스크립트가 어느 패키지로 가는지(§7 결정 4) 정해져야 경로 상수를 고칠 수 있다.
10. **A12/A14/A16/A17/A18 — 유지하되 위치만 옮길 것.** GPU lock guard와 CUDA teardown 감시(`tests/conftest.py:215-332`)는 public(runner에 gpu 마커 테스트가 있음)·owner 양쪽 conftest에 필요하다. loopback uvicorn(`tests/api/test_sse.py:25-34`, `tests/qa/test_sse_server.py:35-44`, `tests/api/test_access_log_redaction.py:46-48`)은 외부 네트워크가 아니다. DD-41 검사(`tests/api/test_registry_structure.py:253-330`)는 GET 경로 소유자가 정해진 뒤 드라이버를 다시 쓴다. perf 예산(`tests/archive/test_perf.py:13,31-48`)은 DD-24/DD-40 판정 규칙을 그대로 가져간다.

## 5. 웹 테스트·e2e 표

| path | kind | #t | src import | env | 요구사항 | 제안 home | 적응 |
|---|---|---|---|---|---|---|---|
| web/e2e/archive-real.spec.ts | playwright(real) | 2 | — | MBNAS_E2E_ARCHIVE_RUN, MBNAS_E2E_REAL, MBNAS_E2E_TOKEN | 실행 중 API(MBNAS_DEV_API 기본 http://127.0.0.1:8000)+vite preview 5173; MBNAS_E2E_REAL=1, MBNAS_E2E_TOKEN; evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-owner(live) | W6 |
| web/e2e/archive.spec.ts | playwright(mock) | 11 | — | — | Chromium + `vite preview` 127.0.0.1:4173 (playwright.config.ts) + page.route mock; @axe-core/playwright; evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-public | W9 |
| web/e2e/console.spec.ts | playwright(mock) | 21 | — | — | Chromium + `vite preview` 127.0.0.1:4173 (playwright.config.ts) + page.route mock; @axe-core/playwright; evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-public | W9 |
| web/e2e/final.spec.ts | playwright(mock) | 8 | — | — | Chromium + `vite preview` 127.0.0.1:4173 (playwright.config.ts) + page.route mock; @axe-core/playwright; evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-public | W9 |
| web/e2e/generation.spec.ts | playwright(mock) | 6 | lib/format, lib/policy | — | Chromium + `vite preview` 127.0.0.1:4173 (playwright.config.ts) + page.route mock; evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-public | W9 |
| web/e2e/hardening.spec.ts | playwright(mock) | 23 | api/types | — | Chromium + `vite preview` 127.0.0.1:4173 (playwright.config.ts) + page.route mock; @axe-core/playwright; evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-public | W9 |
| web/e2e/mobile.spec.ts | playwright(mock) | 4 | — | — | Chromium + `vite preview` 127.0.0.1:4173 (playwright.config.ts) + page.route mock; evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-public | W9 |
| web/e2e/mock.ts | helper | 0 | api/types | — | evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-public(support) | W10 |
| web/e2e/perf.spec.ts | playwright(mock) | 1 | — | — | Chromium + `vite preview` 127.0.0.1:4173 (playwright.config.ts) + page.route mock; evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-public | — |
| web/e2e/real-server.spec.ts | playwright(real) | 9 | — | MBNAS_E2E_FINAL, MBNAS_E2E_FINAL_BUDGET_GPU_HOURS, MBNAS_E2E_FINAL_PARENT_ID, MBNAS_E2E_REAL, MBNAS_E2E_RUN_ID, MBNAS_E2E_TOKEN, MBNAS_E2E_XORIGIN_API | 실행 중 API(MBNAS_DEV_API 기본 http://127.0.0.1:8000)+vite preview 5173; MBNAS_E2E_REAL=1, MBNAS_E2E_TOKEN; MBNAS_E2E_FINAL=1 시 실제 GPU FINAL_RETRAIN; evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-owner(live) | W6 |
| web/e2e/weights.spec.ts | playwright(mock) | 3 | lib/fitness, lib/policy | — | Chromium + `vite preview` 127.0.0.1:4173 (playwright.config.ts) + page.route mock; evidence/ 디렉터리에 스크린샷/측정 기록 쓰기 | web-public | W1 W9 |
| web/tests/api.test.ts | vitest(node) | 22 | api/client, lib/artifacts, lib/runflow | — | node + vitest 5 | web-public | — |
| web/tests/app.test.tsx | vitest(jsdom) | 9 | App, state/context | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/archive_candidate.test.tsx | vitest(jsdom) | 7 | App, state/context | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/archive_compare.test.tsx | vitest(jsdom) | 4 | App, screens/Compare, state/context | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/archive_contract.test.ts | vitest(node) | 5 | — | MBNAS_OPENAPI | node + vitest 5; 서버 contracts/openapi.json 파일(MBNAS_OPENAPI로 대체 가능) | web-public | W3 |
| web/tests/archive_fixtures.ts | helper | 0 | archive/pareto, archive/types | — | — | web-public(support) | W10 |
| web/tests/archive_format.test.ts | vitest(node) | 18 | archive/format, archive/hash, components/archive/ArchiveScatter, components/archive/BaselineTable, components/archive/OverviewCard | — | node + vitest 5 | web-public | — |
| web/tests/archive_pareto.test.ts | vitest(node) | 15 | archive/palette, archive/pareto | — | node + vitest 5 | web-public | — |
| web/tests/archive_screen.test.tsx | vitest(jsdom) | 24 | App, archive/types, components/archive/ArchiveScatter, state/context | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/block_catalog.test.tsx | vitest(jsdom) | 13 | App, components/ModelStructure, lib/structure, state/context | — | node + vitest 5 (jsdom 환경) | web-public | W5 |
| web/tests/block_catalog_server_fixture.json | fixture-json | 0 | — | — | — | web-public(support) | — |
| web/tests/cohort_lines.test.ts | vitest(node) | 17 | lib/cohort, lib/lines | — | node + vitest 5 | web-public | — |
| web/tests/compare_conditions.test.tsx | vitest(jsdom) | 6 | App, archive/types, screens/Compare, state/context | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/components.test.tsx | vitest(jsdom) | 21 | App, api/client, components/ArtifactImage, components/Modal, components/Nav, components/Scatter, lib/cohort, lib/policy, state/context | — | node + vitest 5 (jsdom 환경) | web-public | W8 |
| web/tests/context_restore.test.tsx | vitest(jsdom) | 3 | state/context | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/contract.test.ts | vitest(node) | 6 | — | MBNAS_OPENAPI | node + vitest 5; 서버 contracts/openapi.json 파일(MBNAS_OPENAPI로 대체 가능) | web-public | W3 |
| web/tests/contrast.test.ts | vitest(node) | 6 | — | — | node + vitest 5; src/styles.css 읽기 | web-public | — |
| web/tests/detail_fixtures.ts | helper | 0 | api/types | — | — | web-public(support) | W10 |
| web/tests/final_retrain.test.tsx | vitest(jsdom) | 28 | App, api/client, components/CandidateView, lib/cohort, lib/finalretrain, lib/policy, lib/runflow, lib/validate, screens/Monitor, screens/Server, state/context | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/fitness.test.ts | vitest(node) | 6 | lib/fitness | — | node + vitest 5 | web-public | W1 |
| web/tests/fixtures.ts | helper | 0 | api/types, lib/fitness | — | — | web-public(support) | W1 W10 |
| web/tests/fixtures_audit_etri_mixed.json | fixture-json | 0 | — | — | — | web-public(support) | — |
| web/tests/generated_detail_fixture.test.ts | vitest(node) | 3 | api/client, lib/structure | — | node + vitest 5 | web-public | — |
| web/tests/generation_workflow.test.tsx | vitest(jsdom) | 10 | api/client, api/types, components/CandidateModal, components/CandidateView, components/TrainingPanel, components/TrainingProgress, lib/generation, lib/policy, lib/validate, screens/Monitor, screens/Settings, state/context, state/reducer | — | node + vitest 5 (jsdom 환경) | web-public | W2 |
| web/tests/mockapi.ts | helper | 0 | api/types | — | — | web-public(support) | W10 |
| web/tests/model_detail_v2.test.tsx | vitest(jsdom) | 29 | api/client, components/CandidateView, components/ModelComparison, components/ModelStructure | — | node + vitest 5 (jsdom 환경) | web-public | W5 |
| web/tests/native_real_documents.test.tsx | vitest(jsdom) | 14 | api/client, components/CandidateView, components/ModelComparison, components/ModelStructure, lib/structure | — | node + vitest 5 (jsdom 환경) | web-public | W5 |
| web/tests/preset_parity.test.ts | vitest(node) | 2 | lib/policy | MBNAS_PILOT_PRESET | node + vitest 5; 서버 config/protocol.pilot_smoke.example.json(MBNAS_PILOT_PRESET) | web-public | W2 W4 |
| web/tests/qa_hardening.test.ts | vitest(node) | 22 | api/client, api/sse, api/sync, api/types, lib/validate, state/reducer | — | node + vitest 5; npm run build 산출물 dist/(없으면 통과) | web-public | W7 |
| web/tests/reducer.test.ts | vitest(node) | 9 | api/types, state/reducer | — | node + vitest 5 | web-public | — |
| web/tests/registry_structure_button.test.tsx | vitest(jsdom) | 10 | api/client, components/RegistryStructureButton, components/TrainingPanel | — | node + vitest 5 (jsdom 환경) | web-public | W5 |
| web/tests/s0910_fix1.test.tsx | vitest(jsdom) | 11 | components/CandidateView, lib/cohort, lib/lines | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/s11_fix1.test.tsx | vitest(jsdom) | 4 | App, state/context | — | node + vitest 5 (jsdom 환경); src/styles.css 읽기 | web-public | — |
| web/tests/s3_audit_init.test.tsx | vitest(jsdom) | 5 | api/client, components/CandidateView, screens/Settings | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/s3_f7_capabilities.test.tsx | vitest(jsdom) | 6 | api/client, components/CandidateView, components/Capabilities | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/setup.ts | helper | 0 | — | — | — | web-public(support) | — |
| web/tests/sse.test.ts | vitest(node) | 16 | api/client, api/sse, api/sync, api/types | — | node + vitest 5 | web-public | — |
| web/tests/structure.test.tsx | vitest(jsdom) | 12 | components/ModelStructure, lib/structure | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/structure_native_server_fixture.json | fixture-json | 0 | — | — | — | web-public(support) | — |
| web/tests/structure_parity.test.ts | vitest(node) | 4 | lib/structure | — | node + vitest 5 | web-public | W5 |
| web/tests/structure_v2_server_fixture.json | fixture-json | 0 | — | — | — | web-public(support) | — |
| web/tests/training_panel.test.tsx | vitest(jsdom) | 9 | api/client, api/types, components/TrainingPanel, lib/policy, lib/validate, screens/Monitor, screens/Settings, state/context | — | node + vitest 5 (jsdom 환경) | web-public | W2 |
| web/tests/training_progress_checkpoint.test.tsx | vitest(jsdom) | 8 | api/client, components/TrainingProgress | — | node + vitest 5 (jsdom 환경) | web-public | — |
| web/tests/validate.test.ts | vitest(node) | 14 | lib/policy, lib/validate | — | node + vitest 5 | web-public | W2 W7 |

웹 적응 목록:

| 코드·내용 | 파일 |
|---|---|
| W1 DD-A8: src/lib/fitness.ts(computeFitness) 제거 대상 → 테스트 전용 사본으로 이동하거나 서버값 fixture로 대체 | web/e2e/weights.spec.ts, web/tests/fitness.test.ts, web/tests/fixtures.ts |
| W10 합성 fixture/mock: 공개 DTO allowlist(WP-4) 변경 시 필드 동기화 | web/e2e/mock.ts, web/tests/archive_fixtures.ts, web/tests/detail_fixtures.ts, web/tests/fixtures.ts, web/tests/mockapi.ts |
| W2 DD-A8: policy.ts의 handoff 원문 embed(HANDOFF_JSON, policy.ts:41-42,372-396) 제거 → 템플릿은 GET /protocols/presets 응답 fixture 사용 | web/tests/generation_workflow.test.tsx, web/tests/preset_parity.test.ts, web/tests/training_panel.test.tsx, web/tests/validate.test.ts |
| W3 contract 경로 ../../MBNAS_server/contracts/openapi.json → mbnas_contracts 위치(MBNAS_OPENAPI) | web/tests/archive_contract.test.ts, web/tests/contract.test.ts |
| W4 preset 원본 경로 → mbnas_contracts 또는 core 응답 스냅샷 | web/tests/preset_parity.test.ts |
| W5 서버 생성 fixture(generator는 evidence_impl/*에 있으며 스냅샷 미포함 — 확인 못 함) → mbnas_model_runtime로 재생성 절차 필요 | web/tests/block_catalog.test.tsx, web/tests/model_detail_v2.test.tsx, web/tests/native_real_documents.test.tsx, web/tests/registry_structure_button.test.tsx, web/tests/structure_parity.test.ts |
| W6 실서버 e2e: 포트(8000→gateway 8710)·토큰 경로 조정; owner/라이브 전용 | web/e2e/archive-real.spec.ts, web/e2e/real-server.spec.ts |
| W7 '/root/detection/...' 문자열은 절대경로 예시일 뿐(호스트 접근 없음) — 변경 불필요(선택적 정리) | web/tests/qa_hardening.test.ts, web/tests/validate.test.ts |
| W8 'Client recompute' 행(CandidateView.tsx:479) 단언 → DD-A8 후 제거 | web/tests/components.test.tsx |
| W9 evidence/ 쓰기 경로를 TARGET_ROOT 기준으로 | web/e2e/archive.spec.ts, web/e2e/console.spec.ts, web/e2e/final.spec.ts, web/e2e/generation.spec.ts, web/e2e/hardening.spec.ts, web/e2e/mobile.spec.ts, web/e2e/weights.spec.ts |

보충: `src/lib/fitness.ts`는 `src/components/CandidateView.tsx:12,338,477-479`("Fitness (server)" / "Client recompute" 행)에서만 쓰이며, DD-A8 제거 후 `tests/fixtures.ts:68`이 fixture 행의 fitness를 만드는 데 쓰는 `computeFitness`는 테스트 전용 사본이 필요하다(수식은 서버 `protocol/fitness.py`와 동일하다고 `fitness.ts:10` 주석이 밝힘 — 공개 ZIP에 테스트 사본을 포함할지는 §7 결정 6). `tests/preset_parity.test.ts:12,41-51`은 `policy.ts`의 embed(`policy.ts:41-42`)와 서버 예시 파일을 대조하므로 DD-A8 이후에는 "GET /protocols/presets 응답 fixture ↔ 서버 예시 파일" 대조로 바꾸면 의미가 보존된다. 서버 생성 fixture 3종(`tests/structure_v2_server_fixture.json`, `structure_native_server_fixture.json`, `block_catalog_server_fixture.json`)의 생성기는 `evidence_impl/WPW1|WPS6/*.py`에 있다고 각 테스트 머리말이 밝히지만 스냅샷에 없다(확인 못 함) → mbnas_model_runtime에서 재생성하는 스크립트를 공개 저장소에 두어야 한다. e2e mock 설정(`playwright.config.ts`)은 `vite preview` 4173을 쓰고 real 설정은 5173 + `MBNAS_DEV_API`(기본 8000)이므로 DD-A7 포트(8710 gateway, 8720 preview)와의 정합은 §7 결정 7.

## 6. 원본 문서 보고값 — 본 환경 미실행

| 출처(스냅샷 경로:행) | 범위 | 보고값 | HEAD/시점 | 표시 |
|---|---|---|---|---|
| application/docs/integration_20261021/TEST_BASELINE_20261021.md:28 | 서버 표준 게이트(api,unit,qa,worker,archive), ML 설치 전 에뮬레이션(A) | 2 failed, 1118 passed, 31 skipped, 3 deselected, 1 error (323.6s) | 18c4815 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/TEST_BASELINE_20261021.md:29 | 서버 표준 게이트, ML 설치 후(B) | 1 failed, 1128 passed, 21 skipped, 3 deselected, 3 errors (407.4s) | 18c4815 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/TEST_BASELINE_20261021.md:30 | 서버 전체 tests/, ML 설치 전 에뮬레이션(C) | 26 failed, 1246 passed, 51 skipped, 4 deselected, 13 errors (346.4s) | 18c4815 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/TEST_BASELINE_20261021.md:31 | 서버 전체 tests/, ML 설치 후(D) | 7 failed, 1357 passed, 25 skipped, 19 deselected, 2 errors (488.4s) | 18c4815 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/TEST_BASELINE_20261021.md:60-62 | 웹 vitest / e2e(mock) | vitest 25 files 366 tests 통과; e2e 167 passed, 1 failed(mobile-375 LOAD_FAILED, D5) | 18c4815 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/WORKLOG.md:11 | WP-0 반복 1 표준 게이트 / 외부 디렉터리 | 2 failed(test_s2_f4=D2, test_perf 부하성)/1130 passed/0 errors; 외부 디렉터리 204 passed/0 errors | 작업 트리(미커밋) | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/WORKLOG.md:15 | Phase B: WP-W2 vitest / WP-W3 e2e | vitest 378; e2e 186/186 | 작업 트리 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/WORKLOG.md:16 | WP-S3 수용 시 표준 게이트 | 2 failed(D2+perf flake)/1162 passed/0 errors | 작업 트리 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/WORKLOG.md:18 | 계약 1.4.0(WP-C) 표준 게이트 | 0 failed/1215 passed/21 skipped/0 errors (exit 0, 27.8 min 부하 중) | 작업 트리 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/WORKLOG.md:19 | WP-W1 수용 시 웹 | vitest 452 · e2e 186 | 작업 트리 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/WORKLOG.md:20 | 계약 1.4.1(WP-E) focused | 541 passed | 작업 트리 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/WORKLOG.md:23 | Phase F 게이트(중단 시점) / outside-gate | 1263 passed/1 failed(perf (run) 470.6 ms)/1 error(GET registry structure CUDA init teardown); outside-gate 294 passed/0 failed | 작업 트리 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/integration_20261021/WORKLOG.md:24 | Phase F 완료 웹/e2e/live | check:types/typecheck/vitest 470/build 0; e2e 186/186; 구조 route live 33 checks 0 failed; probe 대조 ALL MATCH ×2 | 작업 트리 | 원본 문서 보고값 — 본 환경 미실행 |
| application/MBNAS_server/docs/TESTING_HOST_20261015.md:89-94 | 표준 명령 예상 skip 수(2026-10-15 기준선) | 31 skipped (torch/ultralytics 11, pilot 4, 증거 6, evaluation_log 2, Codex 1, live 7) | 2026-10-15 | 원본 문서 보고값 — 본 환경 미실행 |
| application/docs/TEST_EVIDENCE.md:35,43,51,59,109,115 | PR 증거(Python 3.12.14 시스템 인터프리터) | test_structure_adversarial 12 passed; test_generation_recovery_independent 7 passed; test_structure_runtime_cpu 3 passed; test_checkpoint+test_stage_trainer 37 passed 1 deselected; 343 passed 1 GPU deselected; vitest 366 passed/25 files | PR 작업공간 | 원본 문서 보고값 — 본 환경 미실행 |
| application/MBNAS_server/docs/ACCEPTANCE_RESULTS.md:523,273,261 | 통합 이전 역사(2026-10-0x, /root/detection 호스트) | 817 passed 7 skipped(326.6 s) 등 — 현재 코드 상태와 무관한 과거 기록 | ETRI_HEAD 1d3a459 시기 | 원본 문서 보고값 — 본 환경 미실행 |

스냅샷에 없어 확인 못 한 증거 파일:

- application/docs/integration_20261021/evidence/ (TEST_BASELINE §7 산출물: baseline_server_*.txt, baseline_web_*.txt)
- application/docs/integration_20261021/evidence_impl/ (WP별 게이트 로그, perf_generations_investigation.md, cuda_init_trace_rfdetr_declared_config.txt, 웹 fixture generator gen_server_structure_fixture.py 등)
- application/MBNAS_server/docs/evaluation/evaluation_log.jsonl (git-ignored)
- application/MBNAS_web/evidence/, dist/, test-results/

TEST_BASELINE §2.1·§5가 적은 결함 중 스냅샷에서 상태를 직접 확인한 것: D1(`tests/api/test_generation_real_cpu.py:50` PEP 701) — 스냅샷 50행은 `{candidate['candidate_id']}`(작은따옴표)라 3.10에서 유효(수정됨; 본 환경 Python 3.12.2로 파싱했으므로 3.10 컴파일은 미실행). D2(openapi 1.3.0 이력) — WORKLOG:18이 1.4.0에서 해소 보고. D4(ML 없는 호스트에서 FAIL/ERROR) — `tests/evaluation/test_evaluator_images.py:15`, `tests/search/test_space_hash_content.py:67-69` 등에 skipif가 들어 있음을 확인했으나 전수 보장은 못 함(§7 결정 8). E2(session fixture가 clean_env 이전에 ETRI 루트를 읽음) — `tests/search/conftest.py:31-35`에서 현재 코드 확인.

## 7. 제안: 분할 후 pytest 구성

디렉터리: `tests/owner/{api,orchestration,qa,search,worker,core_unit,training_core,registry_core,legacy_records}`, `tests/public/{contracts,model_runtime,runner,server,infra}`, 공용 `tests/_shared/`(stub_handlers, make_dataset, GPU guard, resolved-protocol fixture JSON).

owner(`mbnas_core` 호스트 venv):

```toml
[tool.pytest.ini_options]
testpaths = ["tests/owner", "tests/public"]          # owner 환경은 두 스위트를 모두 돈다
addopts = "-ra --import-mode=importlib -p no:cacheprovider -m 'not gpu and not live and not legacy_records'"
timeout = 600
markers = [
  "gpu: 호스트 CUDA GPU 필요(실측 증거) — PYTEST_GPU_LOCK 보유 중에만",
  "slow: 수 초 이상",
  "host_data: 실제 호스트 데이터(PILOT 서브셋·probe/bench evidence·실제 run·가중치) 필요, 없으면 skip",
  "live: 실행 중인 core/gateway 스택 필요(MBNAS_QA_LIVE=1)",
  "legacy_records: 원본 저장소·호스트 기록 검사(기본 제외)",
  "owner_worker: owner worker 경로(PROPOSE_GENERATION/structure-check)",
  "runner_protocol: /core/v1/runner 프로토콜 경유",
]
```

public(`mbnas_contracts`+`mbnas_model_runtime`+`mbnas_runner`+`mbnas_server`만 설치된 venv):

```toml
[tool.pytest.ini_options]
testpaths = ["tests/public"]
addopts = "-ra --import-mode=importlib -p no:cacheprovider -m 'not gpu and not live'"
timeout = 600
markers = ["gpu: ...", "slow: ...", "host_data: ...", "live: ...", "runner_protocol: ..."]
```
+ 캐너리 테스트 `tests/public/infra/test_no_private_import.py`: `importlib.util.find_spec("mbnas_core") is None` 이고 public 패키지 소스 어디에도 `mbnas_core` 문자열이 없음을 단언(T8 누출 검사의 테스트 측 대응).

환경변수(실행 스크립트가 고정; `MBNAS_TEST_*`는 `clean_env`가 보존):

| 원본 | 제안 |
|---|---|
| `MBNAS_TEST_ROOT=/data/mgjeong/.../test_root` 또는 `/root/detection` | `MBNAS_TEST_ROOT=$TARGET_ROOT/runtime/test_root` (하위 `MBNAS_data`, `MBNAS_runs`는 비어 있어도 됨; 없으면 host_data 테스트 skip) |
| `MBNAS_TEST_ETRI_ROOT` / `MBNAS_ETRI_NAS_ROOT` | 삭제. vendoring 후 provenance 대조 전용으로 `MBNAS_TEST_ETRI_REF_ROOT`(선택)만 남김 — 결정 2 |
| `MBNAS_TEST_WEIGHTS_DIR` | `$TARGET_ROOT/runtime/weights` |
| `MBNAS_TEST_RUNS_ROOT` | `$TARGET_ROOT/runtime` (security_negatives의 로그/evidence grep 루트) |
| `MBNAS_TEST_ASSUME_ULTRALYTICS=1` | 삭제 후보: probe가 `find_spec("ultralytics")`로 가용성을 판정하는 구조가 vendoring 후에도 남는지에 달림 — 결정 3 |
| `PYTEST_GPU_LOCK` | `$TARGET_ROOT/runtime/locks/gpu.lock` |
| `YOLO_CONFIG_DIR`(search/benchmark conftest가 `MBNAS_TEST_ROOT/MBNAS_runs/ultralytics_cfg/tests`에 생성) | `$TARGET_ROOT/runtime/test_root/ultralytics_cfg` |
| (신규) `MBNAS_TEST_CORE_URL`, `MBNAS_TEST_GATEWAY_URL` | live 테스트용(8700/8710) |

## 8. 결정 필요 사항

1. **하네스 교체 방식(A1/A5).** owner API 테스트를 (가) core 앱+gateway 앱을 같은 프로세스에서 ASGI로 묶는 방식, (나) gateway를 건너뛰고 core `/core/v1/console`에 직접 붙는 방식 중 무엇으로 할지. (가)는 `/api/v1` 계약 테스트(42개 파일)가 그대로 살고, (나)는 빠르지만 gateway 투영(DD-A3)을 검증하지 못한다. 권고: (가), 단 gateway 전용 검사(auth/CORS/limits/redaction: `tests/api/test_auth_cors.py`, `test_set3_health.py`, `test_access_log_redaction.py`)는 core stub으로 public(server)에도 복제.
2. **ETRI 해시·루트 의미론(A3).** vendoring 후 `space_hash/prior_hash`의 내용 해시 입력을 "vendored 모듈 파일 집합"으로 재정의할지, 원본 ETRI 파일 SHA(provenance)를 별도 필드로 보존할지. `test_space_hash_content.py`·`test_space_genotype.py`·`test_block_catalog.py:70-71`의 기대값이 여기에 달려 있다. `probe_mixed_pool`의 "루트 읽기 불가" 진단(`test_etri_bridge_permissions.py`)은 vendoring 후 삭제 대상인지 결정.
3. **`MBNAS_TEST_ASSUME_ULTRALYTICS` 존폐(A16).** `tests/conftest.py:68-91`과 `tests/search/test_space_fallback_optin.py:43-46`이 의존. vendoring 후 probe가 패키지 import 가능 여부를 어떻게 판정하는지에 따라 결정.
4. **스크립트 소유 패키지(A9).** `smoke_end_to_end.py`→owner tools, `resume_equivalence.py`·`bench_host_gpu.py`→runner, `probe_registry.py`·`make_pilot_subset.py`→model_runtime, `source_tree_hash.py`→contracts(또는 owner), `dev_stack.py`→폐기(새 start/stop 스크립트로 대체) 제안. 확정 필요.
5. **legacy_records 처리(A7).** 5개 파일(§4 표)을 owner에 `legacy_records` 마커로 보존할지 삭제할지. 권고: `test_evaluation_summary_record.py`(/root/.claude 저널)와 `test_orchestration_records_set2.py`(Codex CLI·과거 agent id)는 삭제, 나머지는 보존·기본 제외.
6. **웹 fitness 테스트 사본(DD-A8).** `computeFitness`를 `tests/_lib/fitness.ts`로 옮겨 fixture 생성에만 쓸지(공개 ZIP의 tests에 수식이 남음), fixture의 fitness를 상수로 박을지(`tests/fixtures.ts:68` 변경, `e2e/weights.spec.ts:46`의 라운드트립 검증은 약화).
7. **포트·URL 정합.** e2e mock 4173 유지 여부, real e2e의 `MBNAS_DEV_API` 기본값(8000→8710), `tests/qa/test_live_stack.py:19`의 BASE(8000→gateway 8710 또는 core 8700 둘 다 검사?).
8. **ML 부재 환경의 public 게이트 기준.** public venv에 ml extras가 없을 때 skip이 아니라 FAIL/ERROR가 나는 테스트가 남아 있는지(D4)는 실행 없이는 전수 확인 못 함. T1 기준선 실행 때 "skip 사유 목록"을 통과 기준에 포함할지 결정.
9. **owner↔public 경계가 애매한 모듈 7개**의 최종 소속. 본 목록은 아래 가정값을 적용했으며 바뀌면 JSON의 `future_packages`/`proposed_home`을 재계산해야 한다(생성 스크립트 재실행으로 가능). 영향 파일:

| ambiguous 모듈 | 가정한 패키지 | import 파일 수 | 파일 |
|---|---|---|---|
| mbnas_server.registry | model_runtime | 10 | tests/adapters/test_structure_native_cpu.py, tests/qa/test_registry_workspace_evidence.py, tests/registry/test_probe_registry_snapshot.py, tests/registry/test_registry.py, tests/registry/test_registry_unreadable_roots.py, tests/registry/test_set3_registry_devices.py, tests/search/test_block_catalog.py, tests/training/test_final_job_gpu.py, tests/training/test_job_gpu.py, tests/training/test_rfdetr_housekeeping_gpu.py |
| mbnas_server.protocol.presets | core | 19 | tests/evaluation/test_fitness_gate_integration.py, tests/qa/test_benchmark_evidence.py, tests/qa/test_registry_workspace_evidence.py, tests/registry/test_registry.py, tests/search/test_proposers.py, tests/search/test_repair_evidence_seed.py, tests/search/test_space_fallback_optin.py, tests/training/test_explicit_cpu.py, tests/training/test_final_job_gpu.py, tests/training/test_job_gpu.py, tests/training/test_recipe_view_handlers.py, tests/training/test_rfdetr_housekeeping_gpu.py, tests/unit/test_artifacts_registry.py, tests/unit/test_protocol_presets.py, tests/unit/test_protocol_promotion.py, tests/unit/test_protocol_resolution_v12.py, tests/unit/test_protocol_validator.py, tests/unit/test_s |
| mbnas_server.api.model_services | core | 6 | tests/adapters/test_structure_native_cpu.py, tests/api/test_registry_structure.py, tests/api/test_training_selection.py, tests/search/test_structure_cpu.py, tests/training/test_model_artifacts.py, tests/unit/test_schemas_contract.py |
| mbnas_server.training.model_artifacts | core | 3 | tests/api/test_off_run_guards.py, tests/training/test_explicit_cpu.py, tests/training/test_model_artifacts.py |
| mbnas_server.provenance | contracts | 5 | tests/training/test_final_job_gpu.py, tests/training/test_job_gpu.py, tests/training/test_rfdetr_housekeeping_gpu.py, tests/unit/test_provenance.py, tests/unit/test_source_tree_manifest.py |
| mbnas_server.artifacts | server | 8 | tests/api/test_artifacts_export.py, tests/api/test_final_retrain.py, tests/api/test_generation_freshness.py, tests/api/test_off_run_guards.py, tests/training/test_model_artifacts.py, tests/unit/test_artifacts_registry.py, tests/worker/test_generation_crash.py, tests/worker/test_worker_integrity.py |
| mbnas_server.evaluation.handlers | runner | 5 | tests/evaluation/test_fitness_gate_integration.py, tests/evaluation/test_latency_operating_point.py, tests/qa/test_search_fairness_engine.py, tests/training/test_job_gpu.py, tests/unit/test_protocol_promotion.py |

10. **GPU 마커 테스트의 공개 범위.** runner public에 gpu 마커 테스트(13개 파일 중 runner/model_runtime 계열)를 포함하면 고객 장비에서 GPU lock guard(`tests/conftest.py:215-332`)도 함께 배포된다. 포함 여부와 lock 경로 기본값 결정.

# MODULE_MAP — 모듈 분류와 패키지 간 import 그래프 (WP-0 / A1)

작성 2026-10-23 (세션 8da2e29b, 분석자 A1). 대상: `build/source_snapshot` (etri_nas HEAD 18c4815, 2026-10-23 21:07 KST 스냅샷). 인용 `path:line`은 `application/MBNAS_server/src/mbnas_server/` 기준이며, 스크립트·ETRI 파일은 스냅샷 루트 기준 전체 경로로 적는다. 기계 판독본은 같은 디렉터리의 `MODULE_MAP.json`(203 항목).

## 0. 결론

1. **배정 완료.** provenance 그룹 `server-source` 147개 = `.py` 139 + 패키지 데이터 2(`db/schema.sql`, `protocol/handoff_protocol_20261006.json`) + `src/mbnas_server.egg-info/*` 6(빌드 산출물, 폐기). `.py` 139개(26287행)의 배정: **mbnas_core 52**(10038행) · **mbnas_model_runtime 45**(8129행) · **mbnas_contracts 18**(2648행) · **mbnas_runner 13**(2294행) · **mbnas_server(gateway) 11**(3178행). 과제문의 "147 .py"는 manifest 집계(147 = 139 .py + 8 비-.py)를 가리킨다(§6).
2. **파일만 옮기면(그래프 A) 신뢰 규칙 위반 56건**: (i) 공개→core 42건, (ii) contracts 외부 의존 8건(내부 1 + third-party 7), 호스트 경계 6건, (iii) core→runner 0건. **split 24개 모듈의 심볼 이동을 적용하면(그래프 B) 잔여 26건**이며 전부 처리 유형이 정해져 있다: GATEWAY 15(api/app.py·api/routes_files.py의 core 직접 접근 → relay), HTTP 9(worker/main.py → runner HTTP client), DATA 2(training/context.resolve_model의 load_registry, scripts/resume_equivalence의 presets). 패키지 간 lazy import는 B에서 62건이며 모두 그래프에 포함했다(부록 A).
3. (iii)가 0건인 이유는 `worker/contract.py`의 JobFailure/JobPaused/Emitter를 과제 제안대로 `mbnas_contracts.jobs_protocol`에 두었기 때문이다. DD-A1(c)의 문구("worker/child/contract → runner")를 그대로 적용하면 `search/generation.py:28`, `search/handlers.py:251`이 (iii) 위반이 된다(§4.3).
4. DD와의 충돌·주의 9건(§9)과 결정 필요 사항 13건(§10)을 분리해 적었다. 가장 큰 설계 결과는 (a) `space_hash` 정체성이 vendoring으로 바뀐다(DD-A2 귀결), (b) DD-A2가 vendoring 대상으로 적은 `mutate_operators`는 mbnas_server 어디서도 호출되지 않는다, (c) validator가 보는 장치 inventory가 core 호스트 NVML에서 나오므로 원격 runner 구조에서는 바뀌어야 한다.

## 1. 방법

- 스냅샷의 .py 175개(src 139 + yolo_ga_runtime 9 + scripts 12 + contracts/history 15)를 `ast`로 파싱했다(스크립트: `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/extract_imports.py`, 매핑: `mapping_data.py`, 그래프: `build_module_map.py`). 상대 import는 절대 경로로 풀고, `from pkg import sub`는 하위 모듈 import로 분해했다. 함수/클래스 본문 안의 import(lazy)는 src 기준 155건이며 전부 의존 간선으로 계산했다(`if TYPE_CHECKING` 블록 import는 0건).
- **그래프 A** = 각 파일을 §2 대상 모듈로 옮겼을 때의 패키지 간 간선(309건). **그래프 B** = §3의 split 계획(심볼 이동 35개, import 삭제/동반 이동 7건, 함수 단위 이동 2개 모듈, config의 패키지별 분할)을 적용한 뒤의 간선(314건). JSON의 `imports_after_mapping`이 A, `imports_after_symbol_moves`가 B다(자기 패키지 제외).
- 규칙: (i) 공개 패키지(contracts/model_runtime/runner/server/web) → core 금지, (ii) contracts는 stdlib/pydantic/yaml 외 import 금지(내부 패키지 포함), (iii) core → runner 금지, (iv) 계층 역전(model_runtime → runner/server, runner ↔ server, core → server) 금지, (host) runner-host·public·gateway 스크립트의 core import 금지.
- 역할(role) 어휘: `core-policy|core-search|core-orchestration|core-db|core-protocol|core-registry|core-console|core-ops|core-config`, `model_runtime-adapter|operator|genotype|catalog|checkpoint|dataset|evaluation|benchmark|registry|provenance`, `runner-lifecycle|runner-handler`, `contracts-schema|util|protocol|openapi`, `server-gateway|server-archive`, `package-data|build-artifact|etri-vendored|etri-partially-vendored|etri-not-vendored|script`.

## 2. 패키지별 배정

| 패키지 | 모듈 수 | 행 수 | split 필요 모듈 |
|---|---:|---:|---:|
| mbnas_core (private) | 52 | 10038 | 10 |
| mbnas_model_runtime (public) | 45 | 8129 | 2 |
| mbnas_runner (public) | 13 | 2294 | 5 |
| mbnas_contracts (public) | 18 | 2648 | 4 |
| mbnas_server gateway (public) | 11 | 3178 | 3 |
| 합계 | 139 | 26287 | 24 |

### mbnas_core (private)

| 원본(src/mbnas_server/…) | 행 | 대상 모듈 | 역할 | split | 타 패키지 import(B) |
|---|---:|---|---|:-:|---|
| __init__.py | 6 | `mbnas_core` | package-init |  |  |
| api/__init__.py | 1 | `mbnas_core.console` | core-console |  |  |
| api/candidate_views.py | 46 | `mbnas_core.console.candidate_views` | core-console | ● |  |
| api/deps.py | 67 | `mbnas_core.console.deps` | core-console | ● | contracts |
| api/doctor.py | 96 | `mbnas_core.ops.doctor` | core-ops |  | contracts, model_runtime |
| api/model_services.py | 523 | `mbnas_core.console.model_services` | core-console |  | contracts, model_runtime |
| api/routes_meta.py | 82 | `mbnas_core.console.routes_meta` | core-console |  | contracts, model_runtime |
| api/routes_registry_structure.py | 81 | `mbnas_core.console.routes_registry_structure` | core-console |  | contracts, model_runtime |
| api/routes_runs.py | 377 | `mbnas_core.console.routes_runs` | core-console |  | contracts |
| api/routes_stream.py | 91 | `mbnas_core.console.routes_stream` | core-console |  | contracts |
| api/routes_training.py | 68 | `mbnas_core.console.routes_training` | core-console |  | contracts |
| api/services.py | 161 | `mbnas_core.console.services` | core-console |  | contracts, model_runtime |
| api/views.py | 54 | `mbnas_core.console.views` | core-console | ● |  |
| artifacts/__init__.py | 5 | `mbnas_core.artifacts` | core-orchestration |  |  |
| artifacts/registry.py | 223 | `mbnas_core.artifacts.registry` | core-orchestration | ● | contracts |
| config.py | 157 | `mbnas_core.config` | core-config | ● |  |
| db/__init__.py | 1 | `mbnas_core.db` | core-db |  |  |
| db/core.py | 153 | `mbnas_core.db.core` | core-db |  |  |
| db/repo.py | 701 | `mbnas_core.db.repo` | core-db |  | contracts |
| events/__init__.py | 1 | `mbnas_core.events` | core-db |  |  |
| events/bus.py | 100 | `mbnas_core.events.bus` | core-db |  |  |
| export/__init__.py | 1 | `mbnas_core.export` | core-console |  |  |
| export/bundle.py | 194 | `mbnas_core.export.bundle` | core-console |  | contracts |
| orchestration/__init__.py | 1 | `mbnas_core.orchestration` | core-orchestration |  |  |
| orchestration/engine.py | 994 | `mbnas_core.orchestration.engine` | core-orchestration |  | contracts |
| orchestration/final_retrain.py | 307 | `mbnas_core.orchestration.final_retrain` | core-orchestration |  | contracts |
| orchestration/retention.py | 212 | `mbnas_core.orchestration.retention` | core-orchestration |  | contracts |
| orchestration/state.py | 187 | `mbnas_core.orchestration.state` | core-orchestration |  | contracts |
| orchestration/views.py | 88 | `mbnas_core.orchestration.views` | core-orchestration |  | contracts |
| protocol/__init__.py | 1 | `mbnas_core.protocol` | core-protocol |  |  |
| protocol/fitness.py | 107 | `mbnas_core.protocol.fitness` | core-policy |  |  |
| protocol/presets.py | 156 | `mbnas_core.protocol.presets` | core-protocol | ● |  |
| protocol/promotion.py | 94 | `mbnas_core.protocol.promotion` | core-policy |  |  |
| protocol/validator.py | 1015 | `mbnas_core.protocol.validator` | core-protocol | ● | contracts, model_runtime |
| registry/__init__.py | 445 | `mbnas_core.registry` | core-registry | ● | contracts, model_runtime |
| scheduling/__init__.py | 1 | `mbnas_core.scheduling` | core-orchestration |  |  |
| scheduling/admission.py | 131 | `mbnas_core.scheduling.admission` | core-orchestration |  |  |
| scheduling/costs.py | 290 | `mbnas_core.scheduling.costs` | core-orchestration |  | contracts |
| scheduling/jobs.py | 118 | `mbnas_core.scheduling.jobs` | core-orchestration |  | contracts |
| scheduling/planner.py | 129 | `mbnas_core.scheduling.planner` | core-orchestration |  |  |
| search/__init__.py | 6 | `mbnas_core.search` | core-search |  |  |
| search/ga.py | 173 | `mbnas_core.search.ga` | core-search |  | model_runtime |
| search/generation.py | 191 | `mbnas_core.search.generation` | core-search |  | contracts |
| search/handlers.py | 265 | `mbnas_core.search.handlers` | core-search |  | contracts, model_runtime |
| search/lineage.py | 83 | `mbnas_core.search.lineage` | core-search |  | model_runtime |
| search/random.py | 20 | `mbnas_core.search.random` | core-search |  |  |
| search/space.py | 528 | `mbnas_core.search.space` | core-search |  | contracts, model_runtime |
| search/streams.py | 59 | `mbnas_core.search.streams` | core-search |  |  |
| search/structure.py | 537 | `mbnas_core.search.structure` | core-search | ● | model_runtime |
| training/model_artifacts.py | 115 | `mbnas_core.orchestration.model_artifacts` | core-orchestration |  | model_runtime |
| training/selection.py | 493 | `mbnas_core.training_runs.selection` | core-console | ● | contracts, model_runtime |
| training/validation.py | 103 | `mbnas_core.protocol.training_validation` | core-protocol |  | model_runtime |

### mbnas_model_runtime (public)

| 원본(src/mbnas_server/…) | 행 | 대상 모듈 | 역할 | split | 타 패키지 import(B) |
|---|---:|---|---|:-:|---|
| adapters/__init__.py | 5 | `mbnas_model_runtime.adapters` | model_runtime-adapter |  |  |
| adapters/base.py | 196 | `mbnas_model_runtime.adapters.base` | model_runtime-adapter |  |  |
| adapters/common.py | 103 | `mbnas_model_runtime.adapters.common` | model_runtime-adapter |  | contracts |
| adapters/etri_hybrid/__init__.py | 10 | `mbnas_model_runtime.adapters.etri_hybrid` | model_runtime-adapter |  |  |
| adapters/etri_hybrid/adapter.py | 448 | `mbnas_model_runtime.adapters.etri_hybrid.adapter` | model_runtime-adapter |  |  |
| adapters/etri_hybrid/boxes.py | 27 | `mbnas_model_runtime.adapters.etri_hybrid.boxes` | model_runtime-adapter |  |  |
| adapters/etri_hybrid/criterion.py | 60 | `mbnas_model_runtime.adapters.etri_hybrid.criterion` | model_runtime-adapter |  |  |
| adapters/etri_hybrid/data.py | 157 | `mbnas_model_runtime.adapters.etri_hybrid.data` | model_runtime-adapter |  |  |
| adapters/etri_hybrid/loss.py | 64 | `mbnas_model_runtime.adapters.etri_hybrid.loss` | model_runtime-adapter |  |  |
| adapters/etri_hybrid/model.py | 114 | `mbnas_model_runtime.adapters.etri_hybrid.model` | model_runtime-adapter |  |  |
| adapters/etri_hybrid/postprocess.py | 71 | `mbnas_model_runtime.adapters.etri_hybrid.postprocess` | model_runtime-adapter |  |  |
| adapters/rfdetr_adapter.py | 572 | `mbnas_model_runtime.adapters.rfdetr_adapter` | model_runtime-adapter |  | contracts |
| adapters/structure_native.py | 735 | `mbnas_model_runtime.adapters.structure_native` | model_runtime-adapter | ● | contracts |
| adapters/ultralytics_adapter.py | 696 | `mbnas_model_runtime.adapters.ultralytics_adapter` | model_runtime-adapter |  |  |
| benchmark/__init__.py | 4 | `mbnas_model_runtime.benchmark` | model_runtime-benchmark |  |  |
| benchmark/accuracy.py | 108 | `mbnas_model_runtime.benchmark.accuracy` | model_runtime-benchmark |  |  |
| benchmark/capability.py | 151 | `mbnas_model_runtime.benchmark.capability` | model_runtime-benchmark |  |  |
| benchmark/onnx_export.py | 167 | `mbnas_model_runtime.benchmark.onnx_export` | model_runtime-benchmark |  | contracts |
| benchmark/onnx_runtime.py | 67 | `mbnas_model_runtime.benchmark.onnx_runtime` | model_runtime-benchmark |  |  |
| benchmark/pipeline.py | 194 | `mbnas_model_runtime.benchmark.pipeline` | model_runtime-benchmark |  | contracts |
| benchmark/profile.py | 95 | `mbnas_model_runtime.benchmark.profile` | model_runtime-benchmark |  |  |
| benchmark/subjects.py | 309 | `mbnas_model_runtime.benchmark.subjects` | model_runtime-benchmark |  | contracts |
| benchmark/trt_build.py | 128 | `mbnas_model_runtime.benchmark.trt_build` | model_runtime-benchmark |  | contracts |
| benchmark/trt_runtime.py | 75 | `mbnas_model_runtime.benchmark.trt_runtime` | model_runtime-benchmark |  | contracts |
| datasets/__init__.py | 6 | `mbnas_model_runtime.datasets` | model_runtime-dataset |  |  |
| datasets/coco_convert.py | 103 | `mbnas_model_runtime.datasets.coco_convert` | model_runtime-dataset |  | contracts |
| datasets/manifest.py | 339 | `mbnas_model_runtime.datasets.manifest` | model_runtime-dataset |  | contracts |
| datasets/subset.py | 101 | `mbnas_model_runtime.datasets.subset` | model_runtime-dataset |  | contracts |
| datasets/yaml_safe.py | 111 | `mbnas_model_runtime.datasets.yaml_safe` | model_runtime-dataset |  |  |
| evaluation/__init__.py | 1 | `mbnas_model_runtime.evaluation` | model_runtime-evaluation |  |  |
| evaluation/evaluator.py | 185 | `mbnas_model_runtime.evaluation.evaluator` | model_runtime-evaluation |  | contracts |
| evaluation/images.py | 109 | `mbnas_model_runtime.evaluation.images` | model_runtime-evaluation |  | contracts |
| evaluation/latency.py | 249 | `mbnas_model_runtime.evaluation.latency` | model_runtime-evaluation |  | contracts |
| evaluation/matching.py | 82 | `mbnas_model_runtime.evaluation.matching` | model_runtime-evaluation |  |  |
| evaluation/size.py | 25 | `mbnas_model_runtime.evaluation.size` | model_runtime-evaluation |  |  |
| provenance/__init__.py | 1 | `mbnas_model_runtime.provenance` | package-init |  |  |
| provenance/env_lock.py | 125 | `mbnas_model_runtime.provenance.env_lock` | model_runtime-provenance |  | contracts |
| registry/probe.py | 248 | `mbnas_model_runtime.registry.probe` | model_runtime-registry |  | contracts |
| registry/specs.py | 116 | `mbnas_model_runtime.registry.specs` | model_runtime-registry |  | contracts |
| search/audit.py | 124 | `mbnas_model_runtime.genotype_audit` | model_runtime-genotype |  |  |
| search/block_catalog.py | 411 | `mbnas_model_runtime.block_catalog` | model_runtime-catalog |  |  |
| search/etri_bridge.py | 233 | `mbnas_model_runtime.operators` | model_runtime-operator | ● |  |
| search/genotype.py | 538 | `mbnas_model_runtime.genotype` | model_runtime-genotype |  | contracts |
| training/checkpoint.py | 386 | `mbnas_model_runtime.checkpoint` | model_runtime-checkpoint |  | contracts |
| training/dataset_view.py | 80 | `mbnas_model_runtime.datasets.yolo_view` | model_runtime-dataset |  | contracts |

### mbnas_runner (public)

| 원본(src/mbnas_server/…) | 행 | 대상 모듈 | 역할 | split | 타 패키지 import(B) |
|---|---:|---|---|:-:|---|
| benchmark/handlers.py | 173 | `mbnas_runner.benchmark.handlers` | runner-handler |  | contracts, model_runtime |
| evaluation/handlers.py | 259 | `mbnas_runner.evaluation.handlers` | runner-handler | ● | contracts, model_runtime |
| training/__init__.py | 1 | `mbnas_runner.training` | package-init |  |  |
| training/context.py | 196 | `mbnas_runner.training.context` | runner-handler | ● | contracts, core, model_runtime |
| training/handlers.py | 227 | `mbnas_runner.training.handlers` | runner-handler | ● | contracts, model_runtime |
| training/recipe.py | 66 | `mbnas_runner.training.recipe` | runner-handler |  | model_runtime |
| training/stage_trainer.py | 242 | `mbnas_runner.training.stage_trainer` | runner-handler |  | contracts, model_runtime |
| worker/__init__.py | 1 | `mbnas_runner` | runner-lifecycle |  |  |
| worker/__main__.py | 9 | `mbnas_runner.__main__` | runner-lifecycle |  |  |
| worker/child.py | 203 | `mbnas_runner.child` | runner-lifecycle | ● | contracts, model_runtime |
| worker/gpu.py | 123 | `mbnas_runner.gpu` | runner-lifecycle |  |  |
| worker/gpu_guard.py | 180 | `mbnas_runner.gpu_guard` | runner-lifecycle |  | contracts |
| worker/main.py | 614 | `mbnas_runner.worker` | runner-lifecycle | ● | contracts, core |

### mbnas_contracts (public)

| 원본(src/mbnas_server/…) | 행 | 대상 모듈 | 역할 | split | 타 패키지 import(B) |
|---|---:|---|---|:-:|---|
| api/contract.py | 126 | `mbnas_contracts.openapi` | contracts-openapi | ● |  |
| api/errors.py | 73 | `mbnas_contracts.errors` | contracts-util | ● |  |
| api/limits.py | 61 | `mbnas_contracts.asgi` | contracts-util |  |  |
| provenance/source_hash.py | 107 | `mbnas_contracts.util.source_hash` | contracts-util | ● |  |
| schemas/__init__.py | 40 | `mbnas_contracts.schemas` | contracts-schema |  |  |
| schemas/api.py | 187 | `mbnas_contracts.schemas.api` | contracts-schema |  |  |
| schemas/archive.py | 695 | `mbnas_contracts.schemas.archive` | contracts-schema |  |  |
| schemas/enums.py | 180 | `mbnas_contracts.schemas.enums` | contracts-schema |  |  |
| schemas/jobs.py | 130 | `mbnas_contracts.schemas.jobs` | contracts-schema |  |  |
| schemas/protocol.py | 92 | `mbnas_contracts.schemas.protocol` | contracts-schema |  |  |
| schemas/records.py | 438 | `mbnas_contracts.schemas.records` | contracts-schema |  |  |
| util/__init__.py | 1 | `mbnas_contracts.util` | contracts-util |  |  |
| util/atomic.py | 66 | `mbnas_contracts.util.atomic` | contracts-util |  |  |
| util/canon.py | 55 | `mbnas_contracts.util.canon` | contracts-util |  |  |
| util/log_redact.py | 74 | `mbnas_contracts.util.log_redact` | contracts-util |  |  |
| util/paths.py | 112 | `mbnas_contracts.util.paths` | contracts-util |  |  |
| util/time.py | 40 | `mbnas_contracts.util.time` | contracts-util |  |  |
| worker/contract.py | 171 | `mbnas_contracts.jobs_protocol` | contracts-protocol | ● |  |

### mbnas_server gateway (public)

| 원본(src/mbnas_server/…) | 행 | 대상 모듈 | 역할 | split | 타 패키지 import(B) |
|---|---:|---|---|:-:|---|
| api/app.py | 95 | `mbnas_server.api.app` | server-gateway | ● | contracts, core |
| api/auth.py | 56 | `mbnas_server.api.auth` | server-gateway | ● | contracts |
| api/routes_files.py | 103 | `mbnas_server.api.routes_files` | server-gateway | ● | contracts, core, model_runtime |
| archive/__init__.py | 11 | `mbnas_server.archive` | server-archive |  |  |
| archive/analysis.py | 422 | `mbnas_server.archive.analysis` | server-archive |  |  |
| archive/campaign.py | 508 | `mbnas_server.archive.campaign` | server-archive |  |  |
| archive/live.py | 97 | `mbnas_server.archive.live` | server-archive |  |  |
| archive/parsers.py | 262 | `mbnas_server.archive.parsers` | server-archive |  |  |
| archive/routes.py | 136 | `mbnas_server.archive.routes` | server-archive |  | contracts |
| archive/scanner.py | 615 | `mbnas_server.archive.scanner` | server-archive |  |  |
| archive/views.py | 873 | `mbnas_server.archive.views` | server-archive |  | contracts |


배정 원칙의 적용 메모(판단이 갈릴 수 있는 곳만):

- **평가·벤치 절차는 model_runtime, 핸들러는 runner.** `evaluation/evaluator.py`(evaluation_hash 정의)·`latency.py`·`images.py`·`matching.py`·`size.py`와 `benchmark/pipeline.py`·`subjects.py`·`onnx_*`·`trt_*`는 "승인 모델을 평가하는 백엔드 절차"로 보아 공개 model_runtime에 두고, job 수명주기만 다루는 `evaluation/handlers.py`·`training/handlers.py`·`benchmark/handlers.py`를 runner에 두었다. 고객이 평가기를 검증할 수 있고 runner가 얇아진다. 대안(전부 runner)은 §10 D2.
- **구조 검사 규칙은 core, 문서 상수는 contracts.** `search/structure.py`의 `check_structure`(L336)와 세 규칙(`_shape_rule` L213, `_merge_rule` L259, `_head_rule` L299)은 DD-A1(a) "구조 gate 규칙"이므로 core. `adapters/structure_native.py:23`이 쓰는 `SCHEMA`/`CHECK_NAMES`는 StructureReport 문서 계약(`schemas/records.py:348-349` "mbnas-structure/2")이므로 `mbnas_contracts.structure_doc`로 뺀다.
- **registry는 둘로.** `registry/specs.py`의 ModelSpec 선언과 `registry/__init__.py:53-86`의 `adapter_for`/`arch_spec`는 어댑터 팩토리라 runner·probe가 필요로 하므로 model_runtime; probe/GPU evidence 파일로 상태를 정하는 나머지(`registry/__init__.py:89-440`)는 core 호스트 상태다.
- **presets는 core.** `protocol/presets.py`는 handoff 원문을 읽어 제공한다(DD-A8: 웹은 `GET /protocols/presets` 사용). 단 `DEFAULT_BASELINE14`는 `registry/specs.py:12,109`가 import·assert하므로 `mbnas_contracts.constants`로 뺀다.
- **config는 셋으로.** 단일 `Settings`(config.py:45-157)의 필드별 소유 패키지는 부록 B.
- **`api/*`는 core 콘솔로, gateway는 새 relay.** 현재 라우트는 모두 DB·engine을 직접 호출한다(`api/routes_runs.py:32-50` import 목록). DD-A3에 따라 이 코드가 `/core/v1/console/*`가 되고, `/api/v1`을 유지하는 gateway 라우터는 WP-3 신규 코드다. 스냅샷에서 gateway에 남는 것은 `api/app.py`(앱 팩토리 골격), `api/auth.py`(사용자 bearer), `api/routes_files.py`(다운로드), `archive/*`뿐이다.

## 3. split이 필요한 모듈 (24개)

각 항목은 "주 대상"과 분리 계획이다. 라인 번호는 스냅샷 기준.

**adapters/structure_native.py** (735행) → 주 대상 `mbnas_model_runtime.adapters.structure_native` — 비-YAML 백엔드의 native 구조 관측(CPU forward hook). search.structure의 SCHEMA/CHECK_NAMES만 사용(adapters/structure_native.py:23,63,82) → 상수를 contracts로 옮기면 core 의존 제거.

import 'from ..search.structure import CHECK_NAMES, SCHEMA'(L23) → mbnas_contracts.structure_doc; block_catalog(L22,495) → mbnas_model_runtime.block_catalog; etri_hybrid NOTEBOOK_BLOB_SHA1(L726) 그대로.

**api/app.py** (95행) → 주 대상 `mbnas_server.api.app` — 브라우저 대면 관심사(사용자 bearer, CORS, request-id, body limit, archive 마운트; api/app.py:35-69)는 gateway. 현재 마운트하는 routes_*는 core 콘솔로 가므로 gateway는 relay 라우터를 마운트한다.

① mbnas_server.api.app: create_app(GatewaySettings) = errors.install + RequestId/BodyLimit/CORS + archive_router + gateway relay routers(WP-3 신규, /api/v1 유지) + routes_files(다운로드). ② mbnas_core.api.app(신규): console/runner/owner 라우터 + 서비스 토큰 인증(DD-A3). ③ uvicorn factory 문자열 'mbnas_server.api.app:app_from_env'(L92) 호스트별 재작성; FastAPI(version='1.4.1')(L47)은 contracts openapi 버전과 동기.

**api/auth.py** (56행) → 주 대상 `mbnas_server.api.auth` — 사용자 bearer 검사(api/auth.py:22-36)는 gateway. RequestIdMiddleware(L39-56)는 순수 ASGI → contracts.asgi로 공유; core는 서비스/runner/owner 토큰용 변형을 별도로 가진다.

bearer_ok/require_bearer → mbnas_server.api.auth; RequestIdMiddleware → mbnas_contracts.asgi; 서비스 토큰 dependency → mbnas_core.api.auth(신규).

**api/candidate_views.py** (46행) → 주 대상 `mbnas_core.console.candidate_views` — generation_candidates(DB) → core; candidate_artifacts의 artifact_view(L15) → gateway.

artifact_view/signing_key 호출(L7,10,15,41) 제거, 레코드만 반환.

**api/contract.py** (126행) → 주 대상 `mbnas_contracts.openapi` — contract()/component()/prune()은 stdlib+json으로 openapi.json을 읽는다(api/contract.py:23-107). openapi.json이 contracts 패키지 데이터가 되므로 함께 이동.

contract/component/prune → mbnas_contracts.openapi (CONTRACT_PATH=SERVER_ROOT/contracts/openapi.json(api/contract.py:17-19) 대신 importlib.resources). validator()/contract_errors()(jsonschema+referencing, api/contract.py:110-126) → mbnas_server.api.contract_check (규칙 ii: contracts는 jsonschema import 불가; 테스트·smoke 전용).

**api/deps.py** (67행) → 주 대상 `mbnas_core.console.deps` — 요청별 DB 연결/서비스 주입은 core. contract_json(L44-60)은 gateway(archive 라우트 archive/routes.py:12)도 필요.

db/bus/services/get_run_or_404 → mbnas_core.console.deps; contract_json → 양 호스트(공통 prune은 mbnas_contracts.openapi.prune).

**api/errors.py** (73행) → 주 대상 `mbnas_contracts.errors` — ApiError 예외·pydantic_errors·_HTTP_CODES는 순수 Python/pydantic(api/errors.py:18-45). gateway와 core 두 FastAPI 호스트가 공유.

ApiError/pydantic_errors/_HTTP_CODES → mbnas_contracts.errors; install(app)/error_response(fastapi 의존, api/errors.py:27-73) → mbnas_server.api.errors 와 mbnas_core.api.errors 각각(약 45행 중복).

**api/routes_files.py** (103행) → 주 대상 `mbnas_server.api.routes_files` — artifact 바이트 제공(서명 URL 검증, digest 대조 X-Artifact-Sha256; L44-78)은 gateway(DD-A6). 레코드 조회(repo.get_artifact L63)·export zip 생성(L91-103)은 core.

① gateway: _authorize/get_artifact(파일은 artifact store에서 artifact_id→경로, digest 대조)/refresh(서명 재발급)/export relay. ② core 콘솔: artifact 레코드 GET, export zip 생성(bundle_files/build_zip) 엔드포인트. ③ db/export/provenance import(L19-22,27) 제거 → core client.

**api/views.py** (54행) → 주 대상 `mbnas_core.console.views` — evaluation_views는 DB overlay(candidate_status, checkpoint_state)와 artifact 서명 URL 렌더링(L52)을 함께 한다.

DB overlay(L22-50) → core; artifact_view 호출(L52)·signing_key(L17-19) → gateway(mbnas_server.artifacts.signing; core 콘솔은 artifact 레코드만 반환).

**artifacts/registry.py** (223행) → 주 대상 `mbnas_core.artifacts.registry` — register_file/resolve_file/run_dir/ArtifactError는 DB 등록·경로 규칙(core). 서명 URL(sign/verify_signature/artifact_view, L78-108, 193-223)은 사용자 다운로드를 제공하는 gateway의 책임(DD-A6 '다운로드는 server가 digest 대조 후 제공').

① register_file/resolve_file/run_dir/artifact_path/ArtifactError/_relpath_of → mbnas_core.artifacts.registry (artifact_path의 API_PREFIX '/api/v1'(L36,75)은 gateway 공개 경로 → core는 artifact_id만 돌려주고 URL은 gateway가 만든다). ② sign/verify_signature/artifact_view → mbnas_server.artifacts.signing (artifact_signing_key는 gateway 설정). ③ ARTIFACT_KINDS(L37-38)는 contracts.schemas.enums.ArtifactKind와 중복 → enum 사용. ④ runner는 run_dir(run) 대신 envelope의 run_dir 문자열을 쓴다(worker/child.py:130 이미 전달).

**config.py** (157행) → 주 대상 `mbnas_core.config` — 단일 Settings(config.py:45-157)에 core(db_path, lease_ttl_s, heartbeat_s, max_replay_events, full_state_retention, worker_python), gateway(api_token, cors_origins, archive_*, signed_url_ttl_s, artifact_signing_key), runner(output_root(s), dataset_roots, weights_dir, worker_python) 설정이 섞여 있다.

① mbnas_core.config.CoreSettings: db_path, output_root(s), dataset_roots, weights_dir, worker_python(owner worker), host/port(8700), lease_ttl_s, heartbeat_s, max_replay_events, max_request_body_bytes, min_free_disk_gb, full_state_retention, log_dir, console/runner/owner 서비스 토큰. ② mbnas_server.config.GatewaySettings: api_token(사용자 bearer), cors_origins, host/port(8710), core_url+service token, artifact_signing_key, signed_url_ttl_s, archive_roots/archive_driver_dirs/archive_campaign_roots/archive_cache_ttl_s, max_request_body_bytes, artifact store 경로(DD-A6). ③ mbnas_runner.config.RunnerSettings: core_url+runner token/id, output_root(s), dataset_roots, weights_dir, worker_python, heartbeat_s, log_dir. ④ 제거: etri_nas_root(L66; DD-A2 원본 체크아웃 참조 금지), SERVER_ROOT 기준 경로(L19,57), 호스트 고정 기본값 /root/detection·/data/mgjeong(L20,26-27,52-56) → runtime/ 하위(DD-A7).

**evaluation/handlers.py** (259행) → 주 대상 `mbnas_runner.evaluation.handlers` — EVALUATE 핸들러·evaluate_checkpoint는 runner. fitness_and_gate(L169-185)·_fitness_rung(L188-194)와 그 import(protocol.fitness/promotion L29-30)는 core 정책(DD-A5).

① fitness_and_gate/_fitness_rung 삭제(core engine.commit_evaluation이 담당; FINAL 표시용 가중치도 RunContext.final_stage(orchestration/engine.py:150-154)가 rungs[-1]로 처리). run_evaluation_job L230-232의 payload.update(fitness...) 제거, metric emit(L236)에서 'fitness' 제거. ② import L29-30 삭제; Settings(L28) → mbnas_runner.config; adapters/evaluator/images/latency/size → model_runtime; training.context/recipe(L200-203, 252) → mbnas_runner.training. ③ selection.run_official_evaluation이 쓰는 measure_latency/device_identity(L64-86)는 그대로 runner에 남는다.

**protocol/presets.py** (156행) → 주 대상 `mbnas_core.protocol.presets` — handoff 원문(handoff_protocol_20261006.json)·PILOT_SMOKE 템플릿. DD-A8: 웹은 GET /protocols/presets 응답을 쓴다 → core 호스트가 보유·제공. DEFAULT_BASELINE14/BASELINE_FAMILIES(L24-36)는 model_runtime.registry.specs가 import(L12,109) → contracts.constants.

DEFAULT_BASELINE14/BASELINE_FAMILIES → mbnas_contracts.constants; PILOT_DATA_ROOT/DEFAULT_OUTPUT_ROOT/PILOT_OUTPUT_ROOT(L38-46, /root/detection 고정) → runtime/ 기본값 또는 설정.

**protocol/validator.py** (1015행) → 주 대상 `mbnas_core.protocol.validator` — protocol 검증·resolution(registry/dataset/device 조회, budget·rung 규칙) = core 내부 resolution(DD-A5). protocol_hash(L172-183)는 runner(training/context.py:44)·generation(search/generation.py:84)도 쓰는 순수 canon 해시 → contracts.

① protocol_hash + RESOLUTION_HASH_EXCLUDED(L71,172-183) → mbnas_contracts.protocol_hash. ② DEFAULT_POSTPROCESS(L73) → mbnas_contracts.constants. ③ L501 etri_bridge.probe_mixed_pool → mbnas_model_runtime.operators.probe; L678 benchmark.capability → model_runtime; L910 training.validation → mbnas_core.protocol.training_validation. ④ ValidationContext.devices(L131)는 로컬 NVML 대신 runner 등록 inventory(core DB)에서 와야 한다(WP-2 설계 항목).

**provenance/source_hash.py** (107행) → 주 대상 `mbnas_contracts.util.source_hash` — stdlib만 사용하는 소스트리 해시(artifact_ref). default_roots가 모노레포 경로(MBNAS_server/MBNAS_web, provenance/source_hash.py:39-46)를 가정하므로 패키지별 root 인자화 필요.

DEFAULT_SERVER_ROOT/DEFAULT_WEB_ROOT/SERVER_DIRS/WEB_DIRS 상수 제거, compute_source_tree_hash(roots)만 contracts에 두고 각 호스트가 자기 설치 트리를 넘긴다.

**registry/__init__.py** (445행) → 주 대상 `mbnas_core.registry` — probe/GPU evidence 파일(<output_root>/registry, evidence)로 RegistryEntry 상태를 결정 = core 호스트 상태. adapter_for/arch_spec(L53-86)은 어댑터 팩토리이며 runner의 baseline 해석에도 필요 → model_runtime.

① adapter_for(L53-71)·arch_spec(L74-86) → mbnas_model_runtime.registry (specs와 함께; ArchSpec 생성에 weights_dir 필요). ② build_entry/build_entries/load_registry/registry_lookup/scan_evidence/scan_trt_evidence/latest_probe_path/roots_skipped/device_capability/source_identity(L89-440) → mbnas_core.registry. ③ DEFAULT_OUTPUT_ROOT='/root/detection/MBNAS_runs'(L45)·ETRI_NOTEBOOK 경로(L50) 호스트 고정값 제거(DD-A2/A7).

**search/etri_bridge.py** (233행) → 주 대상 `mbnas_model_runtime.operators` — sys.path 삽입 import(search/etri_bridge.py:131-142)는 DD-A2로 제거. 등록/프로브/yaml_uses_mixed_modules/ensure_mixed_registered는 vendoring된 operators 패키지의 함수가 되고, seed variant/audit_config는 core.search.mixed_pool로 간다.

① register(=load_etri_mixed 의 등록 부분)·ensure_mixed_registered(L225)·probe(=probe_mixed_pool, L197: 파일 존재 검사 → 패키지 import 가능성 검사로 단순화)·yaml_uses_mixed_modules(L230)·MIXED_MODULES(L38)·EtriUnavailable(L42→OperatorsUnavailable)·audit_model 노출 → mbnas_model_runtime.operators. ② EtriMixed.make_seed_variants/audit_config(L61-62,157-162) → mbnas_core.search.mixed_pool(make_mixed_seed_variants/audit_config 포팅). ③ SearchSpaceUnavailable(L46-50)은 검색 정책 예외 → mbnas_core.search.space. ④ provenance()['files_sha256'](L149-150)·_git_head(L87)·ETRI_DEFAULT_ROOT/resolve_root(MBNAS_ETRI_NAS_ROOT, L37,107-109) 제거; 대신 vendored 파일 sha256 + 원본 SHA(provenance manifest) 기록. ⑤ 훅 문자열 'mbnas_server.search.etri_bridge:ensure_mixed_registered'(worker/child.py:52, training/selection.py:139) → 'mbnas_model_runtime.operators:ensure_registered'.

**search/structure.py** (537행) → 주 대상 `mbnas_core.search.structure` — 구조 gate 규칙(check_structure L336, _shape_rule/_merge_rule/_head_rule) = core(DD-A1 a). 문서 상수는 contracts로.

SCHEMA/BACKEND/REPRESENTATION/CHECK_NAMES/EVIDENCE_LEVELS/CHECK_EVIDENCE(search/structure.py:29-44) → mbnas_contracts.structure_doc (StructureReport 'mbnas-structure/2' 문서 계약, schemas/records.py:348-349). describe_genotype(L113, 선언 전용 렌더링)은 core 유지(대안: model_runtime로 내려 공개 선언 문서 생성 허용 — 결정 필요).

**training/context.py** (196행) → 주 대상 `mbnas_runner.training.context` — 핸들러 공용 plumbing. resolve_model(L141-157)이 registry.load_registry로 호스트 로컬 probe 증거를 읽고 AVAILABLE 여부를 판정(L154-156) → 원격 runner 불가.

① load_protocol의 protocol_hash(L44) → mbnas_contracts.protocol_hash. ② resolve_model: baseline도 envelope의 arch(ArchSpec dict, weights_path/sha 포함)를 받도록 바꾸고 adapter_for만 mbnas_model_runtime.registry에서 import; load_registry/AVAILABLE 판정은 core가 job 생성 시 수행(DD-A5 'resolution은 core'). ③ Settings → mbnas_runner.config(output_roots/dataset_roots/weights_dir).

**training/handlers.py** (227행) → 주 대상 `mbnas_runner.training.handlers` — 학습 job 핸들러(DD-A1 c). _evaluate(L186-220)가 evaluation.handlers.fitness_and_gate/_fitness_rung를 lazy import(L189)하여 fitness/gate를 계산·emit(L205-207, 218) → DD-A5 위반.

L189 import에서 fitness_and_gate/_fitness_rung 제거, L205-207 호출 삭제, metric payload(L218)에서 'fitness' 키 제거. core engine.commit_evaluation(orchestration/engine.py:538-551)이 이미 재계산하며 runner 값은 raw_metrics.handler_fitness(L547)로만 보관됨.

**training/selection.py** (493행) → 주 대상 `mbnas_core.training_runs.selection` — 명시적 단일 모델 학습 run 생성(DB·validator·engine·planner 사용) = core 콘솔 서비스. run_official_evaluation(L444-493)만 GPU 자식에서 실행되는 핸들러(worker/child.py:157-159).

① run_official_evaluation(L444-493) → mbnas_runner.evaluation.official_evaluation (TrainingSelectionError(L469) 대신 runner 예외; Settings(L446) → runner config). ② 나머지(TrainingSource/TrainingRunRequest L38-62, resolve_source, prepare_protocol, bind_structure, create_training_run, plan_training_run, on_training_result, settle_training_run) → mbnas_core.training_runs.selection. ③ TrainingSource/TrainingRunRequest는 schemas.api.TrainingSource/TrainingRequest(schemas/api.py:129-147)와 중복 → contracts DTO 사용 검토. ④ register_hook 문자열(L139)·etri_nas_root allow root(L72) 재작성/제거.

**worker/child.py** (203행) → 주 대상 `mbnas_runner.child` — 자식 프로세스 본체는 runner이지만 read-only SQLite로 JobSpec/run/candidate를 읽는다(worker/child.py:3-4, 38-41, 113-148). 원격 runner에는 DB가 없다.

① runner: _protocol_stream/safe_name/NMS_KINDS/run_job/main(L30-46, 49, 151-199) 유지; load_context 대신 runner.worker가 core claim 응답(envelope JSON 파일)을 --spec-file로 넘긴다. ② core: search_arch(L55-68)·native_params(L71-99)·latest_live_full_state(L102-110)·load_context(L113-148)는 orchestrator params→handler 네이티브 params 번역 = core가 envelope를 만들 때 수행 → mbnas_core.runner_api.envelope. ③ run_official_evaluation 분기(L157-159)는 runner 핸들러(mbnas_runner.evaluation.official_evaluation)로. ④ MIXED_HOOK 문자열(L52) 재작성; --db-path 인자(L181) 제거.

**worker/contract.py** (171행) → 주 대상 `mbnas_contracts.jobs_protocol` — JobFailure/JobPaused/Emitter/STOP_ENV/PAUSE_REASONS는 handler↔child 프로토콜 타입(DB 접촉 없음, worker/contract.py:2 'nothing in this module touches the DB'). core(search/generation.py:28, search/handlers.py:251)와 runner가 모두 쓰므로 contracts로.

① JobFailure, JobPaused, Emitter, Handler, STOP_ENV, PAUSED_REASON, PAUSE_REASONS, HANDLERS_ENV → mbnas_contracts.jobs_protocol. ② DEFAULT_HANDLERS(worker/contract.py:56-63)·resolve_handler·handler_override·_load → mbnas_runner.contract (허용 kind 5종만, DD-A4) 및 mbnas_core.owner_worker.contract (PROPOSE_GENERATION만). ③ 문자열 핸들러 경로 'mbnas_server.training.handlers:run_training_job' 등 6건을 새 패키지 경로로 재작성.

**worker/main.py** (614행) → 주 대상 `mbnas_runner.worker` — 한 파일에 (a) runner 루프(자식 spawn·JSONL 모니터·heartbeat·GPU 대기; worker/main.py:287-454)와 (b) owner 측 DB 상태 전이(acquire_lease/reclaim/_settle_job/apply_retention/publish; L172-199, 241-277, 455-537)가 섞여 있다.

① runner: Worker.run/tick(루프), wait_gpu_clear(L146-171), _spawn(L287-298), execute/_monitor/_pump(L300-454, 562-567), heartbeat 스레드, child_settings_env(L68-75, DB_PATH 제외) → mbnas_runner.worker; DB 호출은 mbnas_runner.client(HTTP: claim→heartbeat→progress→artifact→result→ack, DD-A3/A4)로 대체. ② core: tick의 repo.acquire_lease(L246)·reclaim(L254-277)·_finalize/_settle_job(L455-473, 495-537)·apply_retention(L475-493)·publish/_row/prune_worker_rows/idle_heartbeat(L172-239) → mbnas_core.runner_api.lease (lease_epoch=attempt + runner_id fencing, DD-A4). ③ owner worker(CPU, PROPOSE_GENERATION/structure-check): 같은 루프의 cpu=True 경로(L91-99 WorkerConfig.cpu, L246-247 generation_only)를 mbnas_core.owner_worker.main으로 유지(인프로세스 DB 허용). ④ argv '-m mbnas_server.worker.child'(L293) → 'mbnas_runner.child'; SECRET_ENV(L61)에 runner 토큰 추가.


## 4. 신뢰 규칙 위반과 수정

### 4.1 그래프 A(파일 단위 이동) 위반 56건

"수정" 열이 심볼 이동이면 §3 계획으로 해소되고, `[GATEWAY]/[HTTP]/[DATA]`면 코드 변경이 필요하다(§4.2에 잔여로 다시 나옴). `lazy` L = 함수 내부 import.

| # | 규칙 | 출발(패키지) | 도착(패키지) | import 위치 | 가져오는 이름 | lazy | 수정 |
|---:|:-:|---|---|---|---|:-:|---|
| 1 | host | application/MBNAS_server/scripts/api_bearer_only.py (tests(public tools)) | config (core) | L20 | Settings |  | Settings 분할: mbnas_server.config 사용(config.py split) |
| 2 | host | application/MBNAS_server/scripts/bench_host_gpu.py (scripts(runner-host)) | config (core) | L99 | Settings | L | Settings 분할: mbnas_runner.config 사용(config.py split) |
| 3 | host | application/MBNAS_server/scripts/resume_equivalence.py (scripts(runner-host)) | protocol.presets (core) | L41 | pilot_smoke_protocol | L | [DATA] pilot_smoke_protocol 대신 protocol JSON 파일 인자(owner가 생성해 전달) |
| 4 | host | application/MBNAS_server/scripts/resume_equivalence.py (scripts(runner-host)) | registry (core) | L62 | SPEC_BY_ID, adapter_for, arch_spec | L | 심볼 이동: SPEC_BY_ID → mbnas_model_runtime.registry, adapter_for → mbnas_model_runtime.registry, arch_spec → mbnas_model_runtime.registry |
| 5 | host | application/MBNAS_server/scripts/resume_equivalence.py (scripts(runner-host)) | registry (core) | L200 | SPEC_BY_ID, adapter_for, arch_spec | L | 심볼 이동: SPEC_BY_ID → mbnas_model_runtime.registry, adapter_for → mbnas_model_runtime.registry, arch_spec → mbnas_model_runtime.registry |
| 6 | host | application/MBNAS_server/scripts/resume_equivalence.py (scripts(runner-host)) | registry (core) | L248 | SPEC_BY_ID, adapter_for | L | 심볼 이동: SPEC_BY_ID → mbnas_model_runtime.registry, adapter_for → mbnas_model_runtime.registry |
| 7 | i | adapters/structure_native.py (model_runtime) | search.structure (core) | L23 | CHECK_NAMES, SCHEMA |  | 심볼 이동: CHECK_NAMES → mbnas_contracts.structure_doc, SCHEMA → mbnas_contracts.structure_doc |
| 8 | i | api/app.py (server) | config (core) | L18 | Settings |  | Settings 분할: mbnas_server.config 사용(config.py split) |
| 9 | i | api/app.py (server) | db.core (core) | L19 | connect |  | [GATEWAY] gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 10 | i | api/app.py (server) | events.bus (core) | L20 | EventBus |  | [GATEWAY] gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 11 | i | api/app.py (server) | api.routes_meta (core) | L24 | routes_meta |  | [GATEWAY] gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 12 | i | api/app.py (server) | api.routes_registry_structure (core) | L24 | routes_registry_structure |  | [GATEWAY] gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 13 | i | api/app.py (server) | api.routes_runs (core) | L24 | routes_runs |  | [GATEWAY] gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 14 | i | api/app.py (server) | api.routes_stream (core) | L24 | routes_stream |  | [GATEWAY] gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 15 | i | api/app.py (server) | api.routes_training (core) | L24 | routes_training |  | [GATEWAY] gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 16 | i | api/app.py (server) | api.services (core) | L27 | AppServices |  | [GATEWAY] gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 17 | i | api/routes_files.py (server) | artifacts.registry (core) | L18 | ArtifactError, artifact_view, resolve_file, verify_signature |  | [GATEWAY] 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 18 | i | api/routes_files.py (server) | db.repo (core) | L19 | repo |  | [GATEWAY] 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 19 | i | api/routes_files.py (server) | db.core (core) | L20 | tx |  | [GATEWAY] 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 20 | i | api/routes_files.py (server) | export.bundle (core) | L21 | build_zip, bundle_files |  | [GATEWAY] 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 21 | i | api/routes_files.py (server) | api.deps (core) | L27 | contract_json, db, get_run_or_404, services |  | [GATEWAY] 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 22 | i | api/routes_files.py (server) | api.services (core) | L29 | AppServices |  | [GATEWAY] 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 23 | i | api/routes_files.py (server) | api.views (core) | L30 | signing_key |  | [GATEWAY] 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 24 | i | archive/routes.py (server) | api.deps (core) | L12 | contract_json |  | 심볼 이동: contract_json → mbnas_contracts.openapi |
| 25 | i | benchmark/handlers.py (runner) | config (core) | L148 | Settings | L | Settings 분할: mbnas_runner.config 사용(config.py split) |
| 26 | i | evaluation/handlers.py (runner) | config (core) | L28 | Settings |  | Settings 분할: mbnas_runner.config 사용(config.py split) |
| 27 | i | evaluation/handlers.py (runner) | protocol.fitness (core) | L29 | FitnessAnchors, fitness |  | 삭제: fitness_and_gate 삭제(DD-A5): runner는 fitness를 계산하지 않음 |
| 28 | i | evaluation/handlers.py (runner) | protocol.promotion (core) | L30 | evaluate_gate |  | 삭제: fitness_and_gate 삭제(DD-A5): runner는 gate를 계산하지 않음 |
| 29 | i | evaluation/latency.py (model_runtime) | protocol.validator (core) | L38 | DEFAULT_POSTPROCESS |  | 심볼 이동: DEFAULT_POSTPROCESS → mbnas_contracts.constants |
| 30 | i | registry/specs.py (model_runtime) | protocol.presets (core) | L12 | DEFAULT_BASELINE14 |  | 심볼 이동: DEFAULT_BASELINE14 → mbnas_contracts.constants |
| 31 | i | training/context.py (runner) | config (core) | L22 | Settings |  | Settings 분할: mbnas_runner.config 사용(config.py split) |
| 32 | i | training/context.py (runner) | protocol.validator (core) | L44 | protocol_hash | L | 심볼 이동: protocol_hash → mbnas_contracts.protocol_hash |
| 33 | i | training/context.py (runner) | registry (core) | L143 | SPEC_BY_ID, adapter_for, arch_spec, load_registry | L | [DATA] load_registry/AVAILABLE 판정은 core가 job 생성 시 수행하고 ArchSpec(arch)을 envelope로 전달; runner는 adapter_for만 import |
| 34 | i | training/handlers.py (runner) | config (core) | L39 | Settings |  | Settings 분할: mbnas_runner.config 사용(config.py split) |
| 35 | i | training/selection.py (runner) | config (core) | L446 | Settings | L | Settings 분할: mbnas_runner.config 사용(config.py split) |
| 36 | i | worker/child.py (runner) | config (core) | L23 | Settings |  | Settings 분할: mbnas_runner.config 사용(config.py split) |
| 37 | i | worker/child.py (runner) | db.repo (core) | L24 | repo |  | 함수와 함께 이동: repo 사용처(child.py L104,119,122,127,137,144)는 모두 core로 가는 envelope 빌더 함수 안 → import 문도 함께 이동 |
| 38 | i | worker/child.py (runner) | training.selection (core) | L158 | run_official_evaluation | L | 심볼 이동: run_official_evaluation → mbnas_runner.evaluation.official_evaluation |
| 39 | i | worker/main.py (runner) | artifacts.registry (core) | L44 | run_dir |  | [HTTP] runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 40 | i | worker/main.py (runner) | config (core) | L45 | Settings |  | Settings 분할: mbnas_runner.config 사용(config.py split) |
| 41 | i | worker/main.py (runner) | db.repo (core) | L46 | repo |  | [HTTP] runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 42 | i | worker/main.py (runner) | db.core (core) | L47 | connect, tx |  | [HTTP] runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 43 | i | worker/main.py (runner) | events.bus (core) | L48 | emit |  | [HTTP] runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 44 | i | worker/main.py (runner) | orchestration.engine (core) | L49 | engine |  | [HTTP] runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 45 | i | worker/main.py (runner) | orchestration.retention (core) | L49 | retention |  | [HTTP] runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 46 | i | worker/main.py (runner) | orchestration.views (core) | L50 | worker_payload |  | [HTTP] runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 47 | i | worker/main.py (runner) | scheduling.costs (core) | L52 | costs |  | [HTTP] runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 48 | i | worker/main.py (runner) | scheduling.jobs (core) | L52 | jobs |  | [HTTP] runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 49 | ii | api/contract.py (contracts) | config (core) | L17 | SERVER_ROOT |  | 삭제: SERVER_ROOT 경로 대신 importlib.resources(openapi.json이 contracts 패키지 데이터) |
| 50 | ii | api/contract.py (contracts) | third-party `jsonschema` | L113 | — | L | validator/contract_errors(jsonschema/referencing, api/contract.py:110-126) → mbnas_server.api.contract_check |
| 51 | ii | api/contract.py (contracts) | third-party `referencing` | L114 | — | L | validator/contract_errors(jsonschema/referencing, api/contract.py:110-126) → mbnas_server.api.contract_check |
| 52 | ii | api/contract.py (contracts) | third-party `referencing.jsonschema` | L115 | — | L | validator/contract_errors(jsonschema/referencing, api/contract.py:110-126) → mbnas_server.api.contract_check |
| 53 | ii | api/errors.py (contracts) | third-party `fastapi` | L7 | — |  | install/error_response(fastapi/starlette 사용부, api/errors.py:27-73) → mbnas_server.api.errors 및 mbnas_core.api.errors; ApiError/pydantic_errors만 contracts.errors에 남김 |
| 54 | ii | api/errors.py (contracts) | third-party `fastapi.exceptions` | L8 | — |  | install/error_response(fastapi/starlette 사용부, api/errors.py:27-73) → mbnas_server.api.errors 및 mbnas_core.api.errors; ApiError/pydantic_errors만 contracts.errors에 남김 |
| 55 | ii | api/errors.py (contracts) | third-party `fastapi.responses` | L9 | — |  | install/error_response(fastapi/starlette 사용부, api/errors.py:27-73) → mbnas_server.api.errors 및 mbnas_core.api.errors; ApiError/pydantic_errors만 contracts.errors에 남김 |
| 56 | ii | api/errors.py (contracts) | third-party `starlette.exceptions` | L11 | — |  | install/error_response(fastapi/starlette 사용부, api/errors.py:27-73) → mbnas_server.api.errors 및 mbnas_core.api.errors; ApiError/pydantic_errors만 contracts.errors에 남김 |


### 4.2 그래프 B(split 적용 후) 잔여 26건

| # | 규칙 | 출발 | 도착 | 위치 | 이름 | lazy | 해소 유형 | 해소 방법 |
|---:|:-:|---|---|---|---|:-:|:-:|---|
| 1 | host | application/MBNAS_server/scripts/resume_equivalence.py (scripts(runner-host)) | `mbnas_core.protocol.presets` | L41 | pilot_smoke_protocol | L | DATA | pilot_smoke_protocol 대신 protocol JSON 파일 인자(owner가 생성해 전달) |
| 2 | i | api/app.py (server) | `mbnas_core.db.core` | L19 | connect |  | GATEWAY | gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 3 | i | api/app.py (server) | `mbnas_core.events.bus` | L20 | EventBus |  | GATEWAY | gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 4 | i | api/app.py (server) | `mbnas_core.console.routes_meta` | L24 | routes_meta |  | GATEWAY | gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 5 | i | api/app.py (server) | `mbnas_core.console.routes_registry_structure` | L24 | routes_registry_structure |  | GATEWAY | gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 6 | i | api/app.py (server) | `mbnas_core.console.routes_runs` | L24 | routes_runs |  | GATEWAY | gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 7 | i | api/app.py (server) | `mbnas_core.console.routes_stream` | L24 | routes_stream |  | GATEWAY | gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 8 | i | api/app.py (server) | `mbnas_core.console.routes_training` | L24 | routes_training |  | GATEWAY | gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 9 | i | api/app.py (server) | `mbnas_core.console.services` | L27 | AppServices |  | GATEWAY | gateway app은 core 콘솔 라우트 대신 relay 라우터(WP-3 신규)와 archive만 마운트; DB/EventBus 직접 접근 제거 |
| 10 | i | api/routes_files.py (server) | `mbnas_core.artifacts.registry` | L18 | ArtifactError, resolve_file |  | GATEWAY | 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 11 | i | api/routes_files.py (server) | `mbnas_core.db.repo` | L19 | repo |  | GATEWAY | 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 12 | i | api/routes_files.py (server) | `mbnas_core.db.core` | L20 | tx |  | GATEWAY | 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 13 | i | api/routes_files.py (server) | `mbnas_core.export.bundle` | L21 | build_zip, bundle_files |  | GATEWAY | 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 14 | i | api/routes_files.py (server) | `mbnas_core.console.deps` | L27 | db, get_run_or_404, services |  | GATEWAY | 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 15 | i | api/routes_files.py (server) | `mbnas_core.console.services` | L29 | AppServices |  | GATEWAY | 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 16 | i | api/routes_files.py (server) | `mbnas_core.console.views` | L30 | signing_key |  | GATEWAY | 레코드 조회/export zip은 core 콘솔 엔드포인트 호출(WP-3/WP-5); 바이트는 artifact store + digest 대조 |
| 17 | i | training/context.py (runner) | `mbnas_core.registry` | L143 | load_registry | L | DATA | load_registry/AVAILABLE 판정은 core가 job 생성 시 수행하고 ArchSpec(arch)을 envelope로 전달; runner는 adapter_for만 import |
| 18 | i | worker/main.py (runner) | `mbnas_core.artifacts.registry` | L44 | run_dir |  | HTTP | runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 19 | i | worker/main.py (runner) | `mbnas_core.db.repo` | L46 | repo |  | HTTP | runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 20 | i | worker/main.py (runner) | `mbnas_core.db.core` | L47 | connect, tx |  | HTTP | runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 21 | i | worker/main.py (runner) | `mbnas_core.events.bus` | L48 | emit |  | HTTP | runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 22 | i | worker/main.py (runner) | `mbnas_core.orchestration.engine` | L49 | engine |  | HTTP | runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 23 | i | worker/main.py (runner) | `mbnas_core.orchestration.retention` | L49 | retention |  | HTTP | runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 24 | i | worker/main.py (runner) | `mbnas_core.orchestration.views` | L50 | worker_payload |  | HTTP | runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 25 | i | worker/main.py (runner) | `mbnas_core.scheduling.costs` | L52 | costs |  | HTTP | runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |
| 26 | i | worker/main.py (runner) | `mbnas_core.scheduling.jobs` | L52 | jobs |  | HTTP | runner 루프의 DB 호출을 mbnas_runner.client(HTTP /core/v1/runner/*)로 대체; 상태 전이 코드는 mbnas_core.runner_api.lease로 이동(WP-2) |


잔여의 의미: `api/app.py`·`api/routes_files.py`는 gateway에 남는 파일이지만 현재 본문이 core 콘솔 라우터/DB를 직접 마운트·조회한다 → WP-3에서 relay 라우터와 core 콘솔 엔드포인트(artifact 레코드, export zip)로 대체. `worker/main.py`는 DB lease/상태 전이를 직접 수행한다 → WP-2에서 `/core/v1/runner/*` client로 대체하고 상태 전이 코드는 `mbnas_core.runner_api.lease`로 이동. `training/context.py:143`의 `load_registry`는 core가 job 생성 시 baseline을 해석해 `arch`로 전달하면 사라진다.

B의 패키지 간 간선 수(행=출발, 열=도착; 허용 방향만 남아야 함):

| from \ to | core | model_runtime | runner | contracts | server |
|---|---:|---:|---:|---:|---:|
| core | · | 51 | · | 73 | · |
| model_runtime | · | · | · | 33 | · |
| runner | 10 | 27 | · | 24 | · |
| contracts | · | · | · | · | · |
| server | 15 | 1 | · | 15 | · |


`runner → core` 10건이 위 잔여 표의 HTTP 9(worker/main.py) + DATA 1(training/context.py:143)이고(worker/child.py L24의 `db.repo`는 B에서 envelope 빌더 함수와 함께 core로 옮겨 집계에서 빠짐), `server → core` 15건이 GATEWAY 잔여다. 그 외 간선은 모두 허용 방향(core→contracts/model_runtime, runner→model_runtime/contracts, model_runtime→contracts, server→contracts)이다.

### 4.3 (iii) core → runner: 본 매핑 0건, 대안 비교

| core 모듈 | 위치 | 이름 | 현재 파일 | 본 매핑의 처리 |
|---|---|---|---|---|
| search/generation.py | L28 | JobFailure, JobPaused | worker/contract.py | `mbnas_contracts.jobs_protocol`로 이동 → core→contracts (iii 해소) |
| search/handlers.py | L251 | JobFailure, JobPaused | worker/contract.py | `mbnas_contracts.jobs_protocol`로 이동 → core→contracts (iii 해소) |


`worker/contract.py`를 runner에 두면 위 2건이 (iii)가 된다. 과제문 제안대로 예외/프로토콜 타입을 `mbnas_contracts.jobs_protocol`로 옮겼고, 핸들러 테이블(`DEFAULT_HANDLERS` L56-63)만 runner(5 kind)와 core owner worker(PROPOSE_GENERATION)로 나눈다.

### 4.4 lazy import

src 139개 모듈의 함수 내부 import 155건 중 B에서 패키지 간 간선이 되는 것은 62건(부록 A)이고, 그중 규칙 위반은 `training/context.py:143`(load_registry, DATA)과 `scripts/resume_equivalence.py:41`(presets, host) 2건뿐이다. 나머지 lazy import는 "torch를 API 프로세스에 들이지 않기"(예: `api/model_services.py:107,164`, `registry/__init__.py:58-77`)와 순환 회피(`orchestration/engine.py:265,434,442`)가 목적이며 방향은 모두 허용 범위다.

## 5. ETRI mixed pool(yolo_ga_runtime) 처리 — DD-A2 적용 결과

| yolo_ga_runtime 파일 | 행 | 처리 | 대상 | 근거 |
|---|---:|---|---|---|
| yolo_ga_runtime/__init__.py | 47 | etri-not-vendored | `(not vendored; provenance SHA only)` | 패키지 init이 crossover_v22/runtime/pipeline/ga_hooks를 재수출하고 import 시점에 mixed_hooks.install_runtime_hooks()를 실행(yolo_ga_runtime/__init__.py:10-26, 44-46) → 가져오지 않는다(DD-A2). |
| yolo_ga_runtime/mixed_blocks.py | 314 | etri-vendored | `mbnas_model_runtime.operators.mixed_blocks` | NASPatchMerge/NASWindowTransformer/NASGlobalTransformer(mixed_blocks.py:39-162) + register_ultralytics_blocks(parse_model 소스 확장, L188-263) + audit_model(L288): 승인 YAML 구축에 필요 → 공개 vendoring(DD-A2). torch/ultralytics만 import. |
| yolo_ga_runtime/mixed_search_space.py | 203 | etri-vendored | `mbnas_core.search.mixed_pool` | make_mixed_seed_variants(L154-203)/_legal_choices(L78-98)/audit_config(L39-50)/mutate_operators(L101-151) = 검색 정책 → private vendoring(DD-A2). runtime 의존은 R.REPEAT_MODULES(L123,176)와 R.symbolic_shape_propagate(L116, mutate_operators 전용); mixed_blocks.register_ultralytics_blocks(L60) → model_runtime.operators.register. mixed_enabled() ETRI_NAS_MIXED 게이트(L20-22)·ETRI_NAS_MIXED_IMAGE_SIZE(L115)는 인자로 대체. |
| yolo_ga_runtime/runtime.py | 1023 | etri-partially-vendored | `mbnas_core.search.mixed_pool (부분 이식)` | 필수: REPEAT_MODULES(runtime.py:60-63). 선택(mutate_operators를 가져올 때만): symbolic_shape_propagate(L365-472) + make_divisible(L85), NodeShape(L106), ValidationFailure(L123), _resolve_scale_and_depth_width(L290), _conv_stride_from_args(L299), _autopad(L306), _conv_hw(L312), _module_out_hw(L319), 분류 집합(L50-83). CompatibilityAwareSampler/TorchExecutor/evaluate_candidate 등 나머지는 가져오지 않음. |
| yolo_ga_runtime/crossover_v22.py | 617 | etri-not-vendored | `(not vendored; provenance SHA only)` | ETRI GA 교차(노트북 verbatim). mbnas_server 어디서도 import하지 않음(DD-A2 제외 대상). |
| yolo_ga_runtime/pipeline.py | 299 | etri-not-vendored | `(not vendored; provenance SHA only)` | ETRI random-validation 드라이버. 미참조. |
| yolo_ga_runtime/mixed_hooks.py | 249 | etri-not-vendored | `(not vendored; provenance SHA only)` | ETRI GA 런타임 훅(install_runtime_hooks). 미참조; yolo_ga_full_runtime를 import. |
| yolo_ga_runtime/mixed_cli.py | 519 | etri-not-vendored | `(not vendored; provenance SHA only)` | ETRI 서버 CLI. 미참조; yolo_ga_full_runtime를 import. |
| yolo_ga_runtime/ga_hooks.py | 60 | etri-not-vendored | `(not vendored; provenance SHA only)` | 비활성 GA 인터페이스. 미참조. |
| yolo_ga_runtime/runtime.py.before_smoke_nc_20261006_043702_dd7da4de.bak | — | etri-not-vendored | (not vendored; provenance SHA only) | runtime.py 백업본(diff는 L726 한 줄: Detect nc 참조). 가져오지 않음; provenance SHA만. |


확인 사실:

- mbnas_server가 ETRI 코드에서 실제로 호출하는 것은 `mixed_search_space.make_mixed_seed_variants`·`audit_config`, `mixed_blocks.register_ultralytics_blocks`·`audit_model` 네 함수다(`search/etri_bridge.py:61-63,139,157-162`; 사용처 `search/space.py:307,337`). **`mutate_operators`와 `runtime.symbolic_shape_propagate`를 부르는 코드는 mbnas_server에 없다**(grep: `mutate_operators`·`symbolic_shape_propagate` 0건; 호출자는 vendoring 제외 대상인 `yolo_ga_runtime/mixed_hooks.py:159,203`뿐). DD-A2는 `mutate_operators`를 vendoring 대상으로 적었으므로 결정 필요(§10 D7).
- `make_mixed_seed_variants`가 `runtime`에서 쓰는 것은 `REPEAT_MODULES`뿐(`mixed_search_space.py:176`). 따라서 `mutate_operators`를 빼면 runtime.py에서 가져올 것은 집합 하나(runtime.py:60-63)다.
- `search/space.py:48-56`의 `SPACE_HASH_DEFINITION`("mbnas-mixed-space/2")은 실행된 yolo_ga_runtime 파일의 **내용 sha256**(`etri_files_sha256`, `space.py:308`; 출처 `etri_bridge.py:149-150`)을 `space_hash`/`prior_hash`에 넣는다. vendoring 후에는 실행 파일이 달라지므로 모든 새 run의 `space_hash`가 원본 run과 달라진다. 새 배포는 `runtime/` 하위의 새 DB로 시작하므로(DD-A7) 과거 row 호환 문제는 없지만, 정체성 정의를 `mbnas-mixed-space/3`(vendored 파일 sha + provenance의 원본 sha)로 올리는 결정이 필요하다(§10 D7).
- `yolo_ga_runtime/__init__.py:44-46`은 import만으로 `install_runtime_hooks()`를 실행하므로 패키지 단위 import는 금지하고 파일 단위로만 vendoring한다. `mixed_hooks.py`·`mixed_cli.py`는 `yolo_ga_full_runtime`를 import한다(A7 범위).

## 6. 패키지 데이터 배치

| 원본 | 대상 | 비고 |
|---|---|---|
| src/mbnas_server/db/schema.sql | `mbnas_core/db/schema.sql` | SQLite 스키마 v3(db/core.py:42 schema_sql()이 importlib.resources로 읽음) → core 패키지 데이터(private). |
| src/mbnas_server/protocol/handoff_protocol_20261006.json | `mbnas_core/protocol/handoff_protocol_20261006.json` | handoff 설계 예시 원문(protocol/presets.py:49 _HANDOFF_FILE). DD-A8: 웹 번들에 embed 금지, core가 GET /protocols/presets로 제공 → core 패키지 데이터. |
| contracts/openapi.json | `mbnas_contracts/openapi/openapi.json` | 정본 계약(info.version 1.4.1; migrate_1.4.1_to_1.4.2.py는 EXPECTED sha '__FILL__' 상태로 미적용 → 스냅샷 계약은 1.4.1). prune()의 CONTRACT_PATH(api/contract.py:17-19) 대신 패키지 리소스. |
| src/mbnas_server.egg-info/* (6) | (discard) | setuptools egg-info(2026-10-15 설치 산출물). 소스가 아니므로 복사하지 않음; 패키지별 pyproject로 재생성. entry_points의 mbnas-api/mbnas-worker/mbnas-doctor는 패키지별 콘솔 스크립트로 재정의. |
| contracts/history/* (HISTORY.json 1 + openapi-<ver>.json 15 + migrate_*.py 15 = 31) | `mbnas_contracts/openapi/history/` (또는 리포 전용 `docs/public/contracts/history/`) | 계약 이력·마이그레이션 스크립트(15개 migrate_*.py는 stdlib만 import — AST 확인). wheel 동봉 범위는 결정 필요(§10 D9); 동봉 시 `history/`에 `__init__` 없이 데이터로 두어 모듈로 수집되지 않게 한다. |


보충: `contracts/openapi.json`의 `info.version`은 1.4.1이고 `contracts/history/migrate_1.4.1_to_1.4.2.py`는 `EXPECTED_1_4_2_SHA256 = '__FILL__'`(L18)인 미적용 스크립트다. `api/app.py:47`도 `version="1.4.1"`. 라이브 원본(병행 세션 Phase G)에서 1.4.2가 적용됐는지는 **확인 못 함** — WP-1 착수 전 contracts 디렉터리만 재스냅샷할지 결정 필요(§10 D9).

## 7. 스크립트 배치 (14 + 1)

| 스크립트 | 행 | 배치 | 새 경로 | 비고 | 배치 후 import(B) |
|---|---:|---|---|---|---|
| api_bearer_only | 45 | tests(public tools) | `tests/public/tools/api_bearer_only.py` | gateway 두 번째 프로세스(서명키 없음) 기동 도구. config.Settings → mbnas_server.config. |  |
| bench_host_gpu | 376 | scripts(runner-host) | `scripts/runner/bench_host_gpu.py` | BENCHMARK 핸들러로 호스트 GPU 증거 생성. runner+model_runtime만 필요; config → runner config; provenance.source_hash → contracts. | contracts, model_runtime, runner |
| dev_stack | 283 | scripts(dev, superseded) | `scripts/dev/dev_stack.py` | API+worker(+web) 개발 스택. WP-7 start/stop(3 프로세스: core 8700/server 8710/web 8720)로 대체; owner 개발 참고용. worker.gpu → runner. | contracts, runner |
| doctor | 6 | superseded | `(mbnas-core-doctor 콘솔 스크립트)` | api.doctor.main 래퍼 6행 → 패키지별 doctor 콘솔 스크립트(WP-7)로 대체. | core |
| download_weights | 95 | scripts(runner-host) | `scripts/runner/download_weights.py` | 공식 가중치 다운로드+manifest. ETRI_NAS/drone_bench 교차검증 참조(L4-5)는 제거(원본 체크아웃 참조 금지). |  |
| make_pilot_subset | 40 | scripts(public) | `scripts/public/make_pilot_subset.py` | datasets.subset 래퍼 → model_runtime만 필요. | model_runtime |
| probe_registry | 195 | scripts(owner) | `scripts/owner/probe_registry.py` | registry probe(CPU) + evidence JSON. core.registry(build_entries 등)+model_runtime.registry.probe. L109-124의 train-smoke 경로는 runner(stage_trainer)까지 import → owner 호스트에 runner 설치 필요 또는 분리. | contracts, core, model_runtime, runner |
| resume_equivalence | 340 | scripts(runner-host) | `scripts/runner/resume_equivalence.py` | GPU resume 동등성 증거. protocol.presets.pilot_smoke_protocol(L41) = core import → protocol JSON 파일 인자로 대체해야 runner 호스트에서 실행 가능. | contracts, core, model_runtime, runner |
| run_tests_host.sh | 18 | tests(owner) | `tests/owner/run_tests_host.sh` | 테스트 게이트. MBNAS_TEST_ETRI_ROOT 등 호스트 고정 env(L9-14) 재정의 필요. | —(shell) |
| search_dry_run | 114 | scripts(owner) | `scripts/owner/search_dry_run.py` | PROPOSE_GENERATION CPU dry run(core.search.handlers). | contracts, core |
| setup_venv.sh | 32 | superseded | `(scripts/install: wheelhouse --no-index, DD-A7)` | 온라인 pip 설치 스크립트 → 폐기. | —(shell) |
| smoke_api_only | 312 | tests(public tools) | `tests/public/tools/smoke_api_only.py` | 라이브 gateway+core+runner(stub 핸들러) smoke. api.contract.contract_errors → mbnas_server.api.contract_check; util.atomic → contracts. | contracts, server |
| smoke_end_to_end | 853 | scripts(owner) | `scripts/owner/smoke_end_to_end.py` | GPU 전 구간 smoke(owner 호스트). orchestration.final_retrain.NO_FINALIST_MESSAGE·presets → core. | contracts, core, server |
| source_tree_hash | 80 | scripts(dev) | `scripts/dev/source_tree_hash.py` | 소스트리 해시 CLI → contracts.util.source_hash(roots 인자화). | contracts |


`application/scripts/console_stack.sh`(stack-script, 14건 밖)는 API 8000 + vite 5173 2-프로세스 기동기이며 WP-7의 start/stop(core 8700 · server 8710 · web 8720)으로 대체된다. 호스트 경계 위반은 §4.1 #1-6(`api_bearer_only`·`bench_host_gpu`의 Settings → 호스트별 config, `resume_equivalence`의 registry → model_runtime 심볼 이동, presets → 프로토콜 JSON 인자)이다.

## 8. import 재작성

### 8.1 dotted 접두사 표 (옛 → 새)

| 옛 dotted 경로 | 새 dotted 경로 | 비고 |
|---|---|---|
| `mbnas_server` (루트 __init__) | 패키지별 __init__ | |
| `mbnas_server.adapters.*` | `mbnas_model_runtime.adapters.*` | 패키지 전체; split 모듈 포함: structure_native |
| `mbnas_server.api` | `mbnas_core.console` |  |
| `mbnas_server.api.app` | `mbnas_server.api.app` | split |
| `mbnas_server.api.auth` | `mbnas_server.api.auth` | split |
| `mbnas_server.api.candidate_views` | `mbnas_core.console.candidate_views` | split |
| `mbnas_server.api.contract` | `mbnas_contracts.openapi` | split |
| `mbnas_server.api.deps` | `mbnas_core.console.deps` | split |
| `mbnas_server.api.doctor` | `mbnas_core.ops.doctor` |  |
| `mbnas_server.api.errors` | `mbnas_contracts.errors` | split |
| `mbnas_server.api.limits` | `mbnas_contracts.asgi` |  |
| `mbnas_server.api.model_services` | `mbnas_core.console.model_services` |  |
| `mbnas_server.api.routes_files` | `mbnas_server.api.routes_files` | split |
| `mbnas_server.api.routes_meta` | `mbnas_core.console.routes_meta` |  |
| `mbnas_server.api.routes_registry_structure` | `mbnas_core.console.routes_registry_structure` |  |
| `mbnas_server.api.routes_runs` | `mbnas_core.console.routes_runs` |  |
| `mbnas_server.api.routes_stream` | `mbnas_core.console.routes_stream` |  |
| `mbnas_server.api.routes_training` | `mbnas_core.console.routes_training` |  |
| `mbnas_server.api.services` | `mbnas_core.console.services` |  |
| `mbnas_server.api.views` | `mbnas_core.console.views` | split |
| `mbnas_server.archive.*` | `mbnas_server.archive.*` | 패키지 전체 |
| `mbnas_server.artifacts.*` | `mbnas_core.artifacts.*` | 패키지 전체; split 모듈 포함: registry |
| `mbnas_server.benchmark` | `mbnas_model_runtime.benchmark` |  |
| `mbnas_server.benchmark.accuracy` | `mbnas_model_runtime.benchmark.accuracy` |  |
| `mbnas_server.benchmark.capability` | `mbnas_model_runtime.benchmark.capability` |  |
| `mbnas_server.benchmark.handlers` | `mbnas_runner.benchmark.handlers` |  |
| `mbnas_server.benchmark.onnx_export` | `mbnas_model_runtime.benchmark.onnx_export` |  |
| `mbnas_server.benchmark.onnx_runtime` | `mbnas_model_runtime.benchmark.onnx_runtime` |  |
| `mbnas_server.benchmark.pipeline` | `mbnas_model_runtime.benchmark.pipeline` |  |
| `mbnas_server.benchmark.profile` | `mbnas_model_runtime.benchmark.profile` |  |
| `mbnas_server.benchmark.subjects` | `mbnas_model_runtime.benchmark.subjects` |  |
| `mbnas_server.benchmark.trt_build` | `mbnas_model_runtime.benchmark.trt_build` |  |
| `mbnas_server.benchmark.trt_runtime` | `mbnas_model_runtime.benchmark.trt_runtime` |  |
| `mbnas_server.config` | `mbnas_core.config` | split |
| `mbnas_server.datasets.*` | `mbnas_model_runtime.datasets.*` | 패키지 전체 |
| `mbnas_server.db.*` | `mbnas_core.db.*` | 패키지 전체 |
| `mbnas_server.evaluation` | `mbnas_model_runtime.evaluation` |  |
| `mbnas_server.evaluation.evaluator` | `mbnas_model_runtime.evaluation.evaluator` |  |
| `mbnas_server.evaluation.handlers` | `mbnas_runner.evaluation.handlers` | split |
| `mbnas_server.evaluation.images` | `mbnas_model_runtime.evaluation.images` |  |
| `mbnas_server.evaluation.latency` | `mbnas_model_runtime.evaluation.latency` |  |
| `mbnas_server.evaluation.matching` | `mbnas_model_runtime.evaluation.matching` |  |
| `mbnas_server.evaluation.size` | `mbnas_model_runtime.evaluation.size` |  |
| `mbnas_server.events.*` | `mbnas_core.events.*` | 패키지 전체 |
| `mbnas_server.export.*` | `mbnas_core.export.*` | 패키지 전체 |
| `mbnas_server.orchestration.*` | `mbnas_core.orchestration.*` | 패키지 전체 |
| `mbnas_server.protocol.*` | `mbnas_core.protocol.*` | 패키지 전체; split 모듈 포함: presets, validator |
| `mbnas_server.provenance` | `mbnas_model_runtime.provenance` |  |
| `mbnas_server.provenance.env_lock` | `mbnas_model_runtime.provenance.env_lock` |  |
| `mbnas_server.provenance.source_hash` | `mbnas_contracts.util.source_hash` | split |
| `mbnas_server.registry` | `mbnas_core.registry` | split |
| `mbnas_server.registry.probe` | `mbnas_model_runtime.registry.probe` |  |
| `mbnas_server.registry.specs` | `mbnas_model_runtime.registry.specs` |  |
| `mbnas_server.scheduling.*` | `mbnas_core.scheduling.*` | 패키지 전체 |
| `mbnas_server.schemas.*` | `mbnas_contracts.schemas.*` | 패키지 전체 |
| `mbnas_server.search` | `mbnas_core.search` |  |
| `mbnas_server.search.audit` | `mbnas_model_runtime.genotype_audit` |  |
| `mbnas_server.search.block_catalog` | `mbnas_model_runtime.block_catalog` |  |
| `mbnas_server.search.etri_bridge` | `mbnas_model_runtime.operators` | split |
| `mbnas_server.search.ga` | `mbnas_core.search.ga` |  |
| `mbnas_server.search.generation` | `mbnas_core.search.generation` |  |
| `mbnas_server.search.genotype` | `mbnas_model_runtime.genotype` |  |
| `mbnas_server.search.handlers` | `mbnas_core.search.handlers` |  |
| `mbnas_server.search.lineage` | `mbnas_core.search.lineage` |  |
| `mbnas_server.search.random` | `mbnas_core.search.random` |  |
| `mbnas_server.search.space` | `mbnas_core.search.space` |  |
| `mbnas_server.search.streams` | `mbnas_core.search.streams` |  |
| `mbnas_server.search.structure` | `mbnas_core.search.structure` | split |
| `mbnas_server.training` | `mbnas_runner.training` |  |
| `mbnas_server.training.checkpoint` | `mbnas_model_runtime.checkpoint` |  |
| `mbnas_server.training.context` | `mbnas_runner.training.context` | split |
| `mbnas_server.training.dataset_view` | `mbnas_model_runtime.datasets.yolo_view` |  |
| `mbnas_server.training.handlers` | `mbnas_runner.training.handlers` | split |
| `mbnas_server.training.model_artifacts` | `mbnas_core.orchestration.model_artifacts` |  |
| `mbnas_server.training.recipe` | `mbnas_runner.training.recipe` |  |
| `mbnas_server.training.selection` | `mbnas_core.training_runs.selection` | split |
| `mbnas_server.training.stage_trainer` | `mbnas_runner.training.stage_trainer` |  |
| `mbnas_server.training.validation` | `mbnas_core.protocol.training_validation` |  |
| `mbnas_server.util.*` | `mbnas_contracts.util.*` | 패키지 전체 |
| `mbnas_server.worker` | `mbnas_runner` |  |
| `mbnas_server.worker.__main__` | `mbnas_runner.__main__` |  |
| `mbnas_server.worker.child` | `mbnas_runner.child` | split |
| `mbnas_server.worker.contract` | `mbnas_contracts.jobs_protocol` | split |
| `mbnas_server.worker.gpu` | `mbnas_runner.gpu` |  |
| `mbnas_server.worker.gpu_guard` | `mbnas_runner.gpu_guard` |  |
| `mbnas_server.worker.main` | `mbnas_runner.worker` | split |


### 8.2 문자열로 박힌 모듈 참조 (AST가 잡지 못하는 것, 반드시 수동 재작성)

| 위치 | 현재 문자열 참조 | 재작성 |
|---|---|---|
| worker/contract.py:57-62 | DEFAULT_HANDLERS 'mbnas_server.training.handlers:run_training_job' 등 6건 | runner: 'mbnas_runner.training.handlers:run_training_job', 'mbnas_runner.evaluation.handlers:run_evaluation_job', 'mbnas_runner.benchmark.handlers:run_benchmark_job'; owner: 'mbnas_core.search.handlers:propose_generation' |
| worker/child.py:52, training/selection.py:139 | 'mbnas_server.search.etri_bridge:ensure_mixed_registered' | 'mbnas_model_runtime.operators:ensure_registered' |
| adapters/base.py:188-193 | resolve_register_hook: 'mbnas_server.' 접두사만 허용 | 'mbnas_model_runtime.' 접두사만 허용(그 외 거부 유지) |
| worker/main.py:293 | argv '-m mbnas_server.worker.child' | '-m mbnas_runner.child' (+ --spec-file, --db-path 제거) |
| registry/specs.py:92,94 | import_class 'mbnas_server.adapters.etri_hybrid.model.ControlledDetector', config_file 'mbnas_server/adapters/etri_hybrid/model.py' | 'mbnas_model_runtime.adapters.etri_hybrid.model.ControlledDetector' / 'mbnas_model_runtime/adapters/etri_hybrid/model.py' |
| search/block_catalog.py:40,234 | _E_MODEL/ETRI_HYBRID_SOURCE 경로 문자열 | 'mbnas_model_runtime.adapters.etri_hybrid.model' / 'mbnas_model_runtime/adapters/etri_hybrid/model.py' |
| api/app.py:73,92 | uvicorn factory 'mbnas_server.api.app:app_from_env' | gateway 'mbnas_server.api.app:app_from_env', core 'mbnas_core.api.app:app_from_env' |
| api/services.py:62-75 | importlib.import_module('mbnas_server.registry') + 이름 탐색 | from mbnas_core import registry 직접 import(탐색 코드 삭제) |
| search/etri_bridge.py:137-138 | importlib.import_module('yolo_ga_runtime.mixed_search_space'/'mixed_blocks') + sys.path | 패키지 import: mbnas_model_runtime.operators.mixed_blocks / mbnas_core.search.mixed_pool |
| worker/contract.py:52, 152-164 | MBNAS_JOB_HANDLERS_MODULE 스텁 override(tests/worker/stub_handlers.py) | contracts.jobs_protocol.HANDLERS_ENV 유지; 테스트 스텁 모듈 경로 재배치(tests/public) |
| api/deps.py:20 | logger 'mbnas_server.api' | 호스트별 logger 이름 |


테스트(130 파일)의 import 접두사 분포는 `tests/` grep 기준 `mbnas_server.db` 72, `.search` 67, `.api` 57, `.protocol` 47, `.adapters` 40, `.training` 37, `.schemas` 37, `.benchmark` 27, `.worker` 22 …이며, 재배치 자체는 A5 범위다. 위 8.1 표를 그대로 적용하되 split 모듈(`training.*`, `worker.*`, `api.*`, `registry`)은 심볼 단위로 확인해야 한다.

## 9. DD 충돌·주의 사항 (근거 포함)

| # | 관련 DD | 사실 | 영향/제안 |
|---|---|---|---|
| 1 | DD-A2 | `space_hash`가 실행된 yolo_ga_runtime 파일 내용 sha를 포함(`search/space.py:48-52,308`). | vendoring 후 정체성 변화 → `mbnas-mixed-space/3` 정의와 provenance 기록(§5). |
| 2 | DD-A2 | `mutate_operators`·`symbolic_shape_propagate`는 mbnas_server 미사용(§5). | 이식 생략 시 runtime.py 의존은 `REPEAT_MODULES` 집합 하나로 줄어듦. DD-A2 문구 조정 필요. |
| 3 | DD-A1(c) | `worker/contract.py`의 타입을 core가 import(`search/generation.py:28`, `search/handlers.py:251`). | 본 매핑은 contracts로 배치(과제 제안). DD 문구와 다르므로 승인 필요(D1). |
| 4 | DD-A3/A4 | validator의 장치 inventory는 core 호스트 NVML(`api/services.py:134` `env_lock.validation_devices`; `protocol/validator.py:678-682` `device_targets(self.ctx.devices)`), `benchmark/capability.py:15-16`은 `host-gpu-0 = A100`을 상수로 선언, `api/routes_meta.py:39-46`도 같은 가정. | 원격 runner 구조에서는 inventory·측정 가능 target이 runner 등록(heartbeat) 데이터에서 와야 함. WP-2 프로토콜 설계 항목(D13). |
| 5 | DD-A6 | artifact URL은 core 측 코드가 만든다: `artifacts/registry.py:69-75` `artifact_path('/api/v1/...')`, `orchestration/engine.py:499`가 evaluation row의 `artifacts[].url`에 저장. 서명키 사용은 `api/views.py:17-19,52`. | URL 생성·서명은 gateway로. core가 row에 URL을 저장할지, 투영 시 gateway가 채울지 결정(D10; A2/A4 연계). |
| 6 | DD-A7 | 호스트 고정 경로: `config.py:20,26-27,52-57`(/root/detection, /data/mgjeong, SERVER_ROOT/.venv), `registry/__init__.py:45,50`, `protocol/presets.py:38-46`, `search/etri_bridge.py:37`, `provenance/env_lock.py:26`, `provenance/source_hash.py:39-40`, `api/services.py:33`, `api/contract.py:17-19`. | 전부 `runtime/` 기본값·패키지 리소스·설정으로 치환해야 설치 트리 독립(SOURCE_INDEPENDENCE)이 성립. |
| 7 | DD-A9 | 공개 `mbnas_model_runtime.operators.mixed_blocks`는 ultralytics `parse_model` 소스를 읽어 수정·exec 한다(`mixed_blocks.py:188-263`). ETRI 코드는 LICENSE 없음. | AGPL 결합·ETRI 권리 검토 대상에 명시적으로 포함(구현은 막지 않음). |
| 8 | DD-A5 | runner 측 fitness 계산 흔적: `evaluation/handlers.py:169-194,230`, `training/handlers.py:189,205-207,218`(metric emit에 fitness). core는 `orchestration/engine.py:538-551`에서 재계산하고 runner 값은 `raw_metrics.handler_fitness`(L547)로만 보관. | 삭제해도 core 경로 유지됨(FINAL 표시 가중치도 `engine.py:150-154` final_stage가 rungs[-1]로 처리). |
| 9 | 계약 버전 | 스냅샷 openapi 1.4.1, 1.4.2 마이그레이션 미적용(§6). | WP-1 전 contracts 재스냅샷 여부(D9). |

## 10. 결정 필요 사항

| # | 결정 | 선택지 | 분석자 추천 |
|---|---|---|---|
| D1 | `worker/contract.py` 프로토콜 타입(JobFailure/JobPaused/Emitter/STOP_ENV…)의 패키지 | contracts(본 매핑) / runner(DD-A1(c) 문구) | contracts. runner 배치 시 (iii) 2건 발생하고 owner worker도 같은 타입이 필요. |
| D2 | 평가·벤치 절차 모듈 위치 | model_runtime(본 매핑) / runner | model_runtime. 공개 검증 가능성·runner 최소화. |
| D3 | `search/structure.py`의 `describe_genotype`(선언 전용 렌더링, L113) | core 유지(본 매핑) / model_runtime로 내려 공개 | core 유지(단순). 공개 도구가 선언 문서를 만들 필요가 생기면 재검토. |
| D4 | `search/audit.py` 위치 | model_runtime(본 매핑) / core | model_runtime(관측·라벨링, 선택에 비사용). |
| D5 | registry 분할(specs·adapter_for·arch_spec·probe 공개) | 공개(본 매핑) / probe만 core | 공개. probe는 어댑터 CPU 빌드 증거라 정책이 없고 고객 자체 검증에 유용. |
| D6 | presets·handoff JSON 위치 | core(본 매핑) / contracts | core(DD-A8 취지). DEFAULT_BASELINE14만 contracts.constants. |
| D7 | ETRI: `mutate_operators`+`symbolic_shape_propagate` 이식 여부, space_hash 버전 | 이식 생략 + mbnas-mixed-space/3 / DD-A2 그대로 | 생략 + /3. 호출자 0건, 이식 시 runtime.py 약 180행 추가. |
| D8 | ASGI 미들웨어(BodyLimit, RequestId) 공유 위치 | `mbnas_contracts.asgi`(본 매핑) / 호스트별 복제 | contracts.asgi(stdlib만, 규칙 ii 충족). |
| D9 | contracts/history 동봉 범위 및 1.4.2 재스냅샷 | wheel에 openapi.json+HISTORY.json만 / 전부 동봉; 재스냅샷 여부 | wheel에는 openapi.json·HISTORY.json, 이력 파일은 `docs/public/contracts/`. 재스냅샷은 WP-1 직전 1회(contracts 디렉터리 한정). |
| D10 | artifact URL·서명 책임과 row 저장 형식 | gateway 생성(본 매핑; core는 artifact_id/digest만) / core가 gateway base URL을 알고 생성 | gateway. 저장 row에는 URL 대신 id만(투영 시 채움) — A2/A4와 함께 확정. |
| D11 | runner-host 스크립트 입력 규약 | 프로토콜 JSON 파일 인자(본 매핑) / presets를 contracts로 | JSON 파일 인자. probe_registry의 train-smoke 경로(L109-124)는 runner 스크립트로 분리. |
| D12 | gateway 패키지 이름 `mbnas_server` 재사용 | 유지(DD-A1(e)) / `mbnas_gateway` | 유지하되 배포명은 `mbnas-server-gateway`로 구분(원본 .venv의 `mbnas_server` 0.1.0과 혼동 방지). |
| D13 | 장치 inventory·측정 가능 target의 출처 | runner 등록 데이터(core DB) / core 호스트 NVML 유지 | runner 등록 데이터. WP-2 프로토콜에 `devices[]` 포함. |

## 11. 확인 못 함 / 범위 밖

- 실행·테스트는 하지 않았다(규칙 3). 그래프는 정적 분석이며 `importlib.import_module` 문자열 참조는 §8.2에 수동으로 적었다(grep `mbnas_server[./]` 문자열 리터럴 기준; 다른 형태의 동적 import는 확인 못 함).
- 테스트 130 파일의 재배치(A5), DTO allowlist(A2), 원격 프로토콜(A3), ETRI 범위(A7)는 다른 분석 산출물에 맡긴다. 본 문서는 그들이 쓸 접두사 표와 split 경계만 제공한다.
- 라이브 원본의 현재 상태(1.4.2 적용 여부 등)는 읽지 않았다.
- `yolo_ga_full_runtime`(reference 10 파일)은 분석하지 않았다(미참조 확인만: `mixed_hooks.py`·`mixed_cli.py`가 import).
- 패키지별 third-party 의존 목록은 JSON의 `third_party` 필드로 제공했으나 pyproject 핀은 WP-1에서 requirements.lock과 대조해야 한다.

## 12. 산출물

- `/home/mgjeong/Desktop/etri_hd/mbnas_application/docs/internal/analysis/MODULE_MAP.json` — 203 항목. 필드: `source_path, source_module, lines, target_package, target_module_path, role, reason, split_needed, split_plan, imports_after_mapping(A), imports_after_symbol_moves(B), lazy_internal_imports, third_party`. 구성: src .py 139 + src 비-.py 8 + yolo_ga_runtime 10 + scripts 14 + contracts 32. `split_needed=true`는 26개(src 24 + yolo_ga_runtime의 runtime.py·mixed_search_space.py 부분 이식 2).
- `/home/mgjeong/Desktop/etri_hd/mbnas_application/docs/internal/analysis/MODULE_MAP.md` — 이 문서.
- 재현 스크립트(스크래치, 산출물 아님): `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/extract_imports.py`, `mapping_data.py`, `build_module_map.py`, `assemble_md.py`; 중간 데이터 `raw_imports.json`, `edges.json`, `stats.json`.

## 부록 A. 패키지 간 lazy import (그래프 B)

총 62건 (B 그래프의 패키지 간 lazy import; 함수 내부 import도 의존으로 계산).

| 출발 모듈 | 위치 | 도착 모듈 | 이름 | 규칙 위반 |
|---|---|---|---|:-:|
| api/model_services.py (core) | L76 | `mbnas_model_runtime.registry` | SPEC_BY_ID, arch_spec |  |
| api/model_services.py (core) | L95 | `mbnas_model_runtime.registry` | SPEC_BY_ID, adapter_for |  |
| api/model_services.py (core) | L107 | `mbnas_model_runtime.adapters.structure_native` | declared_native_document |  |
| api/model_services.py (core) | L150 | `mbnas_model_runtime.datasets.yaml_safe` | load_dataset_yaml |  |
| api/model_services.py (core) | L164 | `mbnas_model_runtime.adapters.base` | ArchSpec |  |
| api/model_services.py (core) | L165 | `mbnas_model_runtime.registry` | SPEC_BY_ID, adapter_for |  |
| api/model_services.py (core) | L280 | `mbnas_model_runtime.operators` | load_etri_mixed |  |
| api/model_services.py (core) | L325 | `mbnas_model_runtime.registry` | SPEC_BY_ID |  |
| api/model_services.py (core) | L345 | `mbnas_model_runtime.registry` | SPEC_BY_ID |  |
| api/model_services.py (core) | L425 | `mbnas_model_runtime.registry` | adapter_for |  |
| api/model_services.py (core) | L445 | `mbnas_contracts.util.atomic` | atomic_write_json |  |
| api/model_services.py (core) | L452 | `mbnas_model_runtime.registry` | arch_spec |  |
| api/model_services.py (core) | L480 | `mbnas_contracts.util.paths` | check_server_path |  |
| api/model_services.py (core) | L510 | `mbnas_model_runtime.operators` | load_etri_mixed |  |
| api/routes_meta.py (core) | L39 | `mbnas_model_runtime.benchmark.capability` | device_targets |  |
| api/routes_meta.py (core) | L40 | `mbnas_model_runtime.operators` | probe_mixed_pool |  |
| orchestration/engine.py (core) | L982 | `mbnas_contracts.util.paths` | is_within |  |
| protocol/validator.py (core) | L501 | `mbnas_model_runtime.operators` | probe_mixed_pool |  |
| protocol/validator.py (core) | L678 | `mbnas_model_runtime.benchmark.capability` | declared_target_for, device_targets |  |
| registry/__init__.py (core) | L58 | `mbnas_model_runtime.adapters.ultralytics_adapter` | RtdetrAdapter, YoloDetectAdapter |  |
| registry/__init__.py (core) | L66 | `mbnas_model_runtime.adapters.rfdetr_adapter` | RfdetrAdapter |  |
| registry/__init__.py (core) | L69 | `mbnas_model_runtime.adapters.etri_hybrid.adapter` | EtriHybridAdapter |  |
| registry/__init__.py (core) | L77 | `mbnas_model_runtime.adapters.base` | ArchSpec |  |
| registry/__init__.py (core) | L204 | `mbnas_model_runtime.registry.specs` | ETRI_ID |  |
| registry/__init__.py (core) | L245 | `mbnas_model_runtime.benchmark.capability` | DECLARED_TARGETS, SUPPORTED_TARGETS, target_status |  |
| registry/__init__.py (core) | L324 | `mbnas_model_runtime.adapters.etri_hybrid` | ETRI_AUDITED_HEAD, NOTEBOOK_BLOB_SHA1, NOTEBOOK_CELLS |  |
| search/generation.py (core) | L84 | `mbnas_contracts.protocol_hash` | protocol_hash |  |
| search/handlers.py (core) | L49 | `mbnas_model_runtime.datasets.yaml_safe` | DatasetYamlError, load_dataset_yaml |  |
| search/handlers.py (core) | L116 | `mbnas_contracts.util.paths` | check_server_path |  |
| search/handlers.py (core) | L160 | `mbnas_contracts.util.atomic` | atomic_write_bytes |  |
| search/handlers.py (core) | L191 | `mbnas_contracts.protocol_hash` | protocol_hash |  |
| search/handlers.py (core) | L251 | `mbnas_contracts.jobs_protocol` | JobFailure, JobPaused |  |
| training/selection.py (core) | L96 | `mbnas_model_runtime.registry` | SPEC_BY_ID, arch_spec |  |
| training/selection.py (core) | L106 | `mbnas_model_runtime.genotype` | Genotype |  |
| training/selection.py (core) | L136 | `mbnas_model_runtime.operators` | yaml_uses_mixed_modules |  |
| training/selection.py (core) | L168 | `mbnas_model_runtime.datasets.yaml_safe` | load_dataset_yaml, DatasetYamlError |  |
| training/selection.py (core) | L253 | `mbnas_model_runtime.datasets.yaml_safe` | load_dataset_yaml |  |
| training/selection.py (core) | L271 | `mbnas_model_runtime.datasets.yaml_safe` | load_dataset_yaml |  |
| training/selection.py (core) | L272 | `mbnas_model_runtime.genotype` | Genotype |  |
| worker/child.py (core) | L59 | `mbnas_model_runtime.operators` | yaml_uses_mixed_modules |  |
| yolo_ga_runtime/mixed_search_space.py (core) | L60 | `mbnas_model_runtime.operators.mixed_blocks` | register_ultralytics_blocks |  |
| registry/probe.py (model_runtime) | L231 | `mbnas_contracts.util.time` | utcnow_iso |  |
| benchmark/handlers.py (runner) | L41 | `mbnas_contracts.util.paths` | check_server_path |  |
| benchmark/handlers.py (runner) | L50 | `mbnas_model_runtime.benchmark.subjects` | subjects |  |
| benchmark/handlers.py (runner) | L71 | `mbnas_model_runtime.benchmark.capability` | target_status |  |
| benchmark/handlers.py (runner) | L72 | `mbnas_model_runtime.benchmark.pipeline` | DEFAULT_RUNTIMES, PostprocessError, resolve_postprocess, run_benchmark |  |
| benchmark/handlers.py (runner) | L73 | `mbnas_model_runtime.benchmark.subjects` | SubjectError |  |
| benchmark/handlers.py (runner) | L74 | `mbnas_model_runtime.datasets.manifest` | build_manifest |  |
| evaluation/handlers.py (runner) | L102 | `mbnas_model_runtime.checkpoint` | checkpoint |  |
| training/context.py (runner) | L44 | `mbnas_contracts.protocol_hash` | protocol_hash |  |
| training/context.py (runner) | L69 | `mbnas_model_runtime.adapters.ultralytics_adapter` | prepare_ultralytics_env |  |
| training/context.py (runner) | L143 | `mbnas_model_runtime.registry` | SPEC_BY_ID, adapter_for, arch_spec |  |
| training/context.py (runner) | L143 | `mbnas_core.registry` | load_registry | i |
| training/context.py (runner) | L149 | `mbnas_model_runtime.adapters.ultralytics_adapter` | UltralyticsAdapter |  |
| training/selection.py (runner) | L447 | `mbnas_model_runtime.adapters.common` | structural_hash |  |
| training/selection.py (runner) | L448 | `mbnas_model_runtime.benchmark.subjects` | ultralytics_from_weights |  |
| training/selection.py (runner) | L449 | `mbnas_model_runtime.evaluation.evaluator` | evaluator |  |
| training/selection.py (runner) | L451 | `mbnas_model_runtime.evaluation.images` | render_fixed_samples |  |
| training/stage_trainer.py (runner) | L230 | `mbnas_contracts.util.atomic` | sha256_file |  |
| worker/child.py (runner) | L194 | `mbnas_model_runtime.evaluation.latency` | ensure_torchvision |  |
| archive/views.py (server) | L267 | `mbnas_contracts.util.time` | utcnow_iso |  |
| archive/views.py (server) | L824 | `mbnas_contracts.util.time` | utcnow_iso |  |


## 부록 B. `Settings` 필드의 소유 패키지 (config.py:45-157, 사용처 grep `settings.<field>`)

| 필드 | 현재 사용 모듈 | 새 소유 |
|---|---|---|
| `api_token` | api/app.py, api/doctor.py | server(사용자 bearer); core는 console/runner/owner 서비스 토큰 신설 |
| `cors_origins` | api/app.py, api/doctor.py | server |
| `host`, `port` | api/app.py, api/doctor.py | server(8710), core(8700) 각자 |
| `db_path` | db/core.py, worker/main.py, worker/child.py, api/doctor.py | core만(runner는 DB 없음) |
| `output_root`, `output_roots` | protocol/validator.py, training/context.py, training/handlers.py, api/model_services.py, training/selection.py, benchmark/handlers.py, search/handlers.py, api/routes_files.py, api/routes_meta.py, api/services.py, datasets/manifest.py(from_settings), worker/main.py | core(해석·검증·manifest 캐시)와 runner(run_dir allowlist) 각자 |
| `dataset_roots` | protocol/validator.py, training/validation.py, training/selection.py, benchmark/handlers.py, datasets/manifest.py, worker/main.py, api/doctor.py | core와 runner 각자 |
| `weights_dir` | training/context.py, training/selection.py, api/model_services.py, benchmark/handlers.py, worker/main.py, api/doctor.py | core(ArchSpec 해석)와 runner(실제 로드) 각자 |
| `worker_python` | worker/main.py, api/doctor.py | core(owner worker 자식)와 runner 각자 |
| `artifact_signing_key`, `signed_url_ttl_s` | api/views.py, api/candidate_views.py, api/routes_files.py, api/routes_meta.py, api/doctor.py | server |
| `lease_ttl_s`, `heartbeat_s` | worker/main.py, api/routes_runs.py, api/routes_meta.py, api/doctor.py | core(lease·stale 판정); runner는 heartbeat 간격만 |
| `max_replay_events` | api/routes_stream.py | core |
| `max_request_body_bytes` | api/app.py | server와 core 각자 |
| `etri_nas_root` | api/model_services.py, training/selection.py, worker/main.py(child env) | 제거(DD-A2) |
| `log_dir` | worker/main.py, api/doctor.py | 각자 |
| `min_free_disk_gb` | protocol/validator.py, training/validation.py | core |
| `archive_roots`, `archive_driver_dirs`, `archive_campaign_roots`, `archive_cache_ttl_s` | api/app.py, api/routes_meta.py | server |
| `full_state_retention` | orchestration/retention.py, worker/main.py | core |

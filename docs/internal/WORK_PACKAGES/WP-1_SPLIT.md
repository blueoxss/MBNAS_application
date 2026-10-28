# WP-1 SPLIT — 패키지 분할 계약 v1 (설계자 claude-fable-5-1, 2026-10-23 22:1x)

이 문서는 WP-1 구현 에이전트(WP-1A~H, V)의 **유일한 작업 지시**다. 상충 시 우선순위: 이 문서 > `docs/internal/DECISIONS.md`(DD-A1~A25) > `docs/internal/analysis/MODULE_MAP.md`(§3 split 계획·§8 접두사표·§7 스크립트) > 기타 분석 문서. 모든 경로는 `TARGET_ROOT=/home/mgjeong/Desktop/etri_hd/mbnas_application` 기준.

## 0. 목표·불변·금지

- 목표: `build/source_snapshot/`(HEAD 18c4815 + delta 20261023T220106 = 계약 1.4.2)의 코드를 5개 Python 패키지 + `mbnas_web`로 **재배치**하고, import·설정·ETRI 의존을 고쳐 **격리 venv에서 설치·import·테스트가 되는 상태**를 만든다. 런타임 구조(단일 호스트, DB 기반 worker)는 바꾸지 않는다(DD-A14). 원격 큐·gateway relay·투영은 WP-2~4.
- 알고리즘·연구 의미 불변: 검색 RNG·prior·repair·fitness·승급·예산·full-state 재개 로직의 **바이트 단위 변경은 import 경로·설정 접근·vendoring에 필요한 최소**만 허용. 기존 테스트의 assert 완화·기대값 교체·임의 skip 금지(허용 적응은 §9의 A1~A20뿐이며 전부 `docs/internal/evidence/WP1/TEST_ADAPTATIONS.md`에 사유와 함께 기록).
- 금지: `/home/mgjeong/Desktop/etri_hd/etri_nas` 읽기 외 접근 금지(쓰기·git 금지; 입력은 스냅샷만). GPU 사용 금지(`CUDA_VISIBLE_DEVICES=""`로 테스트). 포트 8000~8002·5173~5175 사용 금지(테스트는 임시 포트). `pip install`은 `--no-index --find-links $TARGET_ROOT/build/wheelhouse -c $TARGET_ROOT/constraints.txt`만. 패키지 소스(`*/src/**`)에 금지 문자열(`/root/detection`, `/data/mgjeong`, `147.46.114.253`, `pc2300`, `mgjeong`, `MBNAS_server`, `etri_nas_root`, `yolo_ga_runtime` import) 0건.
- 실행 인터프리터: `$TARGET_ROOT/runtime/venv/bin/python`(3.10.22, third-party 전부 설치됨; system-site-packages=false). `PIP_CONFIG_FILE=/dev/null`.

## 1. 입력

| 입력 | 위치 |
|---|---|
| 서버 소스 | `build/source_snapshot/application/MBNAS_server/src/mbnas_server/**`(139 .py + schema.sql + handoff json), `contracts/{openapi.json(1.4.2),history/}`, `config/*.json|yaml`, `deploy/*`, `scripts/*`, `tests/**`(130 .py) |
| 웹 | `mbnas_web/`(이미 배치됨; `node_modules` 설치됨) |
| ETRI | `build/source_snapshot/yolo_ga_runtime/{mixed_blocks.py, mixed_search_space.py, runtime.py, mixed_hooks.py}` (읽기 전용 입력) |
| 배치표 | `docs/internal/analysis/MODULE_MAP.json`(`target_module_path`, `split_plan`), `MODULE_MAP.md` §3/§7/§8 |
| 테스트 배치표 | `docs/internal/analysis/TEST_INVENTORY.json`(`proposed_home`, 적응 코드) |
| ETRI 명세 | `docs/internal/analysis/ETRI_RUNTIME_SCOPE.md` §3.3(17개 집합), §4(parse_model 결합), §5.1(vendoring 표), §6.3(/3 정의) |
| 결정 | `docs/internal/DECISIONS.md` DD-A10~A25 |

## 2. 패키지 레이아웃 (src layout, 배포명·import명)

| 디렉터리 | 배포명 | import | 의존(런타임) | 비고 |
|---|---|---|---|---|
| `mbnas_contracts/` | `mbnas-contracts` | `mbnas_contracts` | `pydantic==2.13.5`, `PyYAML==6.0.3` | 패키지 데이터 `openapi/openapi.json`, `openapi/HISTORY.json` |
| `mbnas_model_runtime/` | `mbnas-model-runtime` | `mbnas_model_runtime` | contracts, `torch==2.6.0`, `torchvision==0.21.0`, `ultralytics==8.4.173`, `numpy==2.2.6`, `pillow==12.3.0`, `psutil==7.2.2`, `nvidia-ml-py==13.615.71`, `PyYAML`; extras `rfdetr`(rfdetr[train,onnx]==1.11.2), `bench`(onnx, onnxruntime-gpu, onnxsim, pycocotools, scipy, opencv-python-headless), `trt`(tensorrt-cu12, wheelhouse 미포함) | 패키지 데이터: `registry/model_registry.example.json` 아님(core) |
| `mbnas_core/` | `mbnas-core` | `mbnas_core` | contracts, model_runtime, `fastapi==0.142.2`, `uvicorn[standard]==0.54.0`, `pydantic-settings==2.15.0`, `httpx==0.28.1`, `python-dotenv==1.2.4`, `jsonschema==4.26.0` | 패키지 데이터 `db/schema.sql`, `protocol/handoff_protocol_20261006.json`, `registry/model_registry.example.json` |
| `mbnas_runner/` | `mbnas-runner` | `mbnas_runner` | contracts, model_runtime, `pydantic-settings`, `httpx`, `psutil`, `nvidia-ml-py`, `python-dotenv` | |
| `mbnas_server/` | `mbnas-server` | `mbnas_server` | contracts, `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `httpx`, `python-dotenv`, `jsonschema` | gateway + archive |

각 `pyproject.toml`: setuptools build-system(`setuptools>=69,<81`, `wheel`), `requires-python = ">=3.10,<3.11"`, `version` = 루트 `VERSION`(동적 읽기 금지 → 리터럴 `0.1.0`), `[tool.setuptools.packages.find] where=["src"]`, 패키지 데이터 명시, `[project.scripts]`: core `mbnas-core-api = "mbnas_core.api.app:main"`, `mbnas-core-owner-worker = "mbnas_core.owner_worker.main:main"`, `mbnas-core-doctor = "mbnas_core.ops.doctor:main"`; server `mbnas-gateway = "mbnas_server.api.app:main"`; runner `mbnas-runner = "mbnas_runner.__main__:main"`(WP-1은 `--help`와 spec-file 실행만). 테스트 extras: 루트에서 `pytest==9.1.1`, `pytest-timeout==2.4.0` 설치(이미 venv에 있음).

루트: `pyproject.toml`(빌드 없음; `[tool.pytest.ini_options]` §9), `constraints.txt`(= `build/wheelhouse/requirements.closure.txt` 복사), `tests/{owner,public,_shared}`, `scripts/{owner,runner,public,dev}`.

## 3. 재배치 규칙

1. **모듈 경로**는 MODULE_MAP.json의 `target_module_path`를 따른다(§8.1 접두사표). 예외(설계자 override): (a) `api/routes_files.py`·`api/views.py`(artifact_view 포함)·`api/candidate_views.py`·`artifacts/registry.py`는 WP-1에서 **core에 통째로**(`mbnas_core.console.routes_files`, `.views`, `.candidate_views`, `mbnas_core.artifacts.registry`) — 서명 URL 분리는 WP-5. (b) `api/app.py`는 두 파일: `mbnas_core/api/app.py`(= 오늘의 앱: console 라우터 6종 `/api/v1` + bearer(CoreSettings.api_token) + CORS + request-id + body limit + log redaction; archive 미마운트) 와 `mbnas_server/api/app.py`(gateway 골격: bearer(GatewaySettings.api_token) + CORS + request-id + body limit + log redaction + `archive_router` + `GET /api/v1/health` 최소 응답 `{status, version, gateway: true}` — relay 라우터는 WP-3). (c) `worker/main.py` 전체 → `mbnas_core/owner_worker/main.py`(DB 루프 유지), `worker/child.py` → `mbnas_core/owner_worker/child.py`(DB `load_context`·`native_params`·`search_arch`·`latest_live_full_state` 유지) + 신규 얇은 `mbnas_runner/child.py`(§7). (d) `worker/contract.py` → `mbnas_contracts/jobs_protocol.py`(타입) + `mbnas_runner/contract.py` + `mbnas_core/owner_worker/contract.py`(§7). (e) `search/etri_bridge.py` → §6. (f) `config.py` → §5.
2. **import 재작성**: 모든 상대 import를 절대 import로 바꾸고 옛 dotted 경로를 새 경로로 매핑한다(모듈 단위 표 + §4 심볼표). 함수 내부 lazy import·문자열 참조(§8.2 표: DEFAULT_HANDLERS, register_hook, `-m` argv, registry/specs import_class, block_catalog 경로 문자열, uvicorn factory, services registry 탐색, logger 이름)도 재작성. 재작성 스크립트는 `scripts/dev/rewrite_imports.py`로 보존(재현 가능).
3. **docstring·주석의 옛 경로 언급**은 수정 의무 없음(단 금지 문자열은 제거).
4. 모든 복사·분할은 `docs/internal/provenance/PLACEMENT_MANIFEST.jsonl`에 기록: `{source_relpath, source_sha256, target_relpath, target_sha256, kind: moved|split|new|reference_copy|deleted, owner_wp, notes}`. 스냅샷 밖에서 가져오는 바이트는 없다.

## 4. 심볼 이동표 (MODULE_MAP §3 요약 + 결정 반영; 이름은 고정)

| 원본 심볼 | 새 위치 |
|---|---|
| `worker/contract.py`: `JobFailure, JobPaused, Emitter, Handler, STOP_ENV, PAUSED_REASON, PAUSE_REASONS, HANDLERS_ENV` | `mbnas_contracts.jobs_protocol` |
| `worker/contract.py`: `DEFAULT_HANDLERS, resolve_handler, handler_override, _load` | `mbnas_runner.contract`(5 kinds: BASELINE_TRAIN/CANDIDATE_TRAIN/FINAL_RETRAIN→`mbnas_runner.training.handlers:run_training_job`, EVALUATE→`mbnas_runner.evaluation.handlers:run_evaluation_job`, BENCHMARK→`mbnas_runner.benchmark.handlers:run_benchmark_job`) 및 `mbnas_core.owner_worker.contract`(위 5 + PROPOSE_GENERATION→`mbnas_core.search.handlers:propose_generation`) |
| `protocol/validator.py`: `protocol_hash`, `RESOLUTION_HASH_EXCLUDED` | `mbnas_contracts.protocol_hash` (validator는 여기서 import) |
| `protocol/validator.py:DEFAULT_POSTPROCESS`, `benchmark/capability.py:DEFAULT_POSTPROCESS`(중복) | `mbnas_contracts.constants.DEFAULT_POSTPROCESS` 하나 |
| `protocol/presets.py`: `DEFAULT_BASELINE14, BASELINE_FAMILIES` | `mbnas_contracts.constants` |
| `search/structure.py`: `SCHEMA, BACKEND, REPRESENTATION, CHECK_NAMES, EVIDENCE_LEVELS, CHECK_EVIDENCE` | `mbnas_contracts.structure_doc` |
| `api/contract.py`: `contract, component, prune, CONTRACT_PATH` | `mbnas_contracts.openapi`(`importlib.resources`로 `openapi.json` 읽기) |
| `api/contract.py`: `validator, contract_errors`(jsonschema) | `mbnas_server.api.contract_check` (owner 테스트도 여기서 import; server 패키지는 owner venv에 설치됨) |
| `api/errors.py`: `ApiError, pydantic_errors, _HTTP_CODES` | `mbnas_contracts.errors`; `install(app), error_response` → `mbnas_core.api.errors`와 `mbnas_server.api.errors`(동일 코드 2벌) |
| `api/limits.py: BodyLimitMiddleware`, `api/auth.py: RequestIdMiddleware` | `mbnas_contracts.asgi` |
| `api/auth.py: bearer_ok, require_bearer` | `mbnas_core.api.auth` 와 `mbnas_server.api.auth`(2벌; WP-2에서 core는 principals로 교체) |
| `api/deps.py: contract_json` | `mbnas_contracts.openapi.contract_json`은 두지 않는다(fastapi 의존) → `mbnas_core.console.deps.contract_json`과 `mbnas_server.api.deps.contract_json`(2벌, `prune`은 contracts) |
| `provenance/source_hash.py` | `mbnas_contracts.util.source_hash`(`compute_source_tree_hash(roots: list[Path], labels)`); `default_roots()`/MBNAS_server 경로 상수 삭제. core `console.services.source()`는 설치된 `mbnas_core`, `mbnas_contracts`, `mbnas_model_runtime` 패키지 디렉터리를 roots로 사용 |
| `provenance/env_lock.py` | `mbnas_model_runtime.provenance.env_lock` (호스트 고정 경로 상수 제거) |
| `registry/__init__.py: adapter_for, arch_spec, SPEC_BY_ID 재수출`, `registry/specs.py`, `registry/probe.py` | `mbnas_model_runtime.registry`(`__init__`에 adapter_for/arch_spec, `specs`, `probe`) |
| `registry/__init__.py`: 나머지(build_entry/build_entries/load_registry/registry_lookup/scan_evidence/scan_trt_evidence/latest_probe_path/roots_skipped/device_capability/source_identity 등) | `mbnas_core.registry`(`from mbnas_model_runtime.registry import SPEC_BY_ID, adapter_for, arch_spec` 재수출로 기존 호출자 호환) |
| `search/audit.py` | `mbnas_model_runtime.genotype_audit` |
| `training/selection.py: run_official_evaluation` | `mbnas_runner.evaluation.official_evaluation`(예외는 `JobFailure`) |
| `evaluation/handlers.py: fitness_and_gate, _fitness_rung` | `mbnas_core.protocol.fitness_gate`(runner 코드에서 호출 제거, DD-A15) |
| `training/model_artifacts.py` | `mbnas_core.orchestration.model_artifacts` |
| `training/validation.py` | `mbnas_core.protocol.training_validation` |
| `training/selection.py`(나머지) | `mbnas_core.training_runs.selection` |
| `training/checkpoint.py` | `mbnas_model_runtime.checkpoint` |
| `training/dataset_view.py` | `mbnas_model_runtime.datasets.yolo_view` |
| `config/model_registry.example.json` | `mbnas_core/registry/model_registry.example.json`(패키지 데이터) + `config/examples/model_registry.example.json` 사본 |

## 5. 설정 분할 (DD-A13)

공통: `mbnas_contracts.util.home.mbnas_home() -> Path` = `os.environ.get("MBNAS_HOME")` 또는 `Path.cwd()/"runtime"`(절대경로화). 모든 기본 경로는 이 아래. `Settings.load(dotenv_path=None)`은 `MBNAS_ENV_FILE` 대신 각각 `MBNAS_CORE_ENV_FILE`/`MBNAS_GW_ENV_FILE`/`MBNAS_RUNNER_ENV_FILE`. 검증 규칙(절대경로·allow root·CORS exact origin·빈 비밀 거부)은 원본 그대로.

| 클래스(모듈) | env 접두 | 필드(기본값) |
|---|---|---|
| `CoreSettings` (`mbnas_core.config`) | `MBNAS_CORE_` | `api_token`(None; WP-1 core 앱 bearer), `artifact_signing_key`(None), `cors_origins`([]), `db_path`($HOME/core/db/mbnas.sqlite3), `output_root`($HOME/core/runs), `output_roots`([output_root]), `dataset_roots`([$HOME/data]), `weights_dir`($HOME/weights), `model_yaml_roots`([$HOME/uploads] — 검증 시 output_roots와 합집합 사용), `worker_python`(sys.executable), `host`(127.0.0.1), `port`(8700), `signed_url_ttl_s`(600), `lease_ttl_s`(60), `heartbeat_s`(10), `max_replay_events`(5000), `max_request_body_bytes`(1 MiB), `log_dir`($HOME/core/logs), `min_free_disk_gb`(5.0), `full_state_retention`(keep_all), `preset_data_root`(None), `preset_output_root`(None). **삭제**: `etri_nas_root`, `archive_*`. `SERVER_ROOT` 상수 삭제. |
| `GatewaySettings` (`mbnas_server.config`) | `MBNAS_GW_` | `api_token`, `cors_origins`, `host`(127.0.0.1), `port`(8710), `core_url`(http://127.0.0.1:8700), `core_token`(None), `artifact_signing_key`, `signed_url_ttl_s`, `artifact_store`($HOME/gateway/artifacts), `max_request_body_bytes`, `archive_roots`([]), `archive_driver_dirs`([]), `archive_campaign_roots`([]), `archive_cache_ttl_s`(5.0), `log_dir`($HOME/gateway/logs) |
| `RunnerSettings` (`mbnas_runner.config`) | `MBNAS_RUNNER_` | `core_url`(http://127.0.0.1:8700), `token`(None), `runner_id`(None → `f"runner-{socket.gethostname()}"`), `work_root`($HOME/runner/runs), `output_roots`([work_root]), `dataset_roots`([$HOME/data]), `weights_dir`($HOME/weights), `worker_python`(sys.executable), `heartbeat_s`(10), `kill_grace_s`(10), `gpu_index`(0), `log_dir`($HOME/runner/logs), `allow_pickle_resume`(False) |

핸들러(`mbnas_runner.training.handlers` 등)가 쓰던 `Settings.load()`는 `RunnerSettings.load()`로; `check_run_dir`은 `settings.output_roots`, `DatasetService.from_settings(settings)`는 `output_root`(runner는 `work_root`를 `output_root` 프로퍼티로 노출)·`dataset_roots`를 duck-typing으로 읽는다. owner worker(core)가 자식에게 넘기는 env(`child_settings_env`): `MBNAS_RUNNER_WORK_ROOT=<core.output_root>`, `MBNAS_RUNNER_OUTPUT_ROOTS`, `MBNAS_RUNNER_DATASET_ROOTS`, `MBNAS_RUNNER_WEIGHTS_DIR`, `MBNAS_RUNNER_LOG_DIR`, `MBNAS_RUNNER_ALLOW_PICKLE_RESUME=1`(owner), `MBNAS_CORE_DB_PATH`(core child 로더용), `MBNAS_CORE_OUTPUT_ROOT`; 비밀(`MBNAS_CORE_API_TOKEN`, `MBNAS_CORE_ARTIFACT_SIGNING_KEY`, `*_ENV_FILE`)은 제거(`SECRET_ENV` 확장). rfdetr 어댑터의 `torch.load(weights_only=False)`는 `RunnerSettings.allow_pickle_resume`가 False면 `ResumeBlocked("PICKLE_RESUME_DISABLED")`.

## 6. ETRI vendoring 구현 명세 (DD-A12)

1. `mbnas_model_runtime/operators/etri_mixed_blocks.py`: `yolo_ga_runtime/mixed_blocks.py` 1-314행 **전체 바이트 보존**(클래스·audit 함수·`IMPLEMENTATION_VERSION`), 단 `register_ultralytics_blocks`는 `exec`를 쓰지 않는 구현으로 교체(아래 2). 모듈 docstring 끝에 `VENDORED_FROM = "etri_nas/yolo_ga_runtime/mixed_blocks.py sha256 4233dd324e755440d1bc223241fe2e0c95ca37f71e4c3aac8513778491496e70 (HEAD 18c4815)"` 상수 추가.
2. `mbnas_model_runtime/operators/parse_model_8_4_173.py`: 설치된 `ultralytics==8.4.173`의 `nn/tasks.py` `parse_model`을 복제하되 `base_modules`에 `NASPatchMerge, NASWindowTransformer, NASGlobalTransformer`, `repeat_modules`에 `NASWindowTransformer, NASGlobalTransformer`를 추가. 자유 변수는 `from ultralytics.nn import tasks as _tasks`를 통해 해석(`globals()[m]` → `_resolve(m)`: 자체 3클래스 → `getattr(_tasks, m)`; 나머지 이름은 `_tasks.<name>`). 파일 머리에 AGPL-3.0 출처 표기(ultralytics 8.4.173 `ultralytics/nn/tasks.py:parse_model`)와 "modified: +3 base, +2 repeat" 주석. **설치 게이트**: `register_ultralytics_blocks()`는 먼저 `hashlib.sha256(inspect.getsource(_tasks.parse_model).encode()).hexdigest()`를 상수 `EXPECTED_PARSE_MODEL_SHA256`(WP-1C가 설치본에서 실측해 기록; 예상 `7da6bb14…`)과 대조하고 불일치면 `OperatorsUnavailable("ultralytics parse_model source mismatch")`로 실패(묵시 fallback 금지). 통과 시 `_tasks.parse_model = patched`, `setattr(_tasks/_modules, name, cls)`, `modules.__all__` 확장, 속성 `_nas_mixed_installed/_nas_mixed_original/_nas_mixed_capabilities`(키: `installed, already_installed, implementation_version, ultralytics_version, custom_modules, repeat_modules, parser_source_sha256(원본), replacement_source_sha256(복제본 파일), native_modules, safe_globals…`) 설정. idempotent. **fallback 조항**: 정적 복제본이 (a) parity 테스트 실패 또는 (b) 자유 변수 해석 불가로 구현이 막히면, 원본의 AST 패치+`compile`/`exec` 방식을 유지하되 소스 sha 게이트·parity 테스트·`PARSE_MODEL_EXTENSION_MODE="ast_patch_exec"` 기록을 적용하고 evidence에 사유를 남긴다.
3. `mbnas_model_runtime/operators/__init__.py`: `MIXED_MODULES`, `OperatorsUnavailable`, `register_ultralytics_blocks`(재수출), `ensure_registered() -> dict`(= 등록 + capabilities 반환; `register_hook` 대상), `yaml_uses_mixed_modules`, `probe() -> dict`(`importlib.util.find_spec("ultralytics")` + 자체 모듈 import 가능 여부; ETRI 코드 미실행), `operator_files_sha256() -> dict`(설치된 `etri_mixed_blocks.py`, `parse_model_8_4_173.py` 바이트 sha256).
4. `mbnas_core/search/mixed_pool/runtime_constants.py`: `REPEAT_MODULES = frozenset({...17개...})`(ETRI_RUNTIME_SCOPE §5.2 리터럴, 주석에 출처). `seed_variants.py`: `mixed_search_space.py` L12-17·25-62·154-203 바이트 보존(import만 `from mbnas_model_runtime.operators import register_ultralytics_blocks`, `from .runtime_constants import REPEAT_MODULES`); `mixed_enabled`·`mutate_operators`·`_legal_choices`·`_input_shape` 미이식. `bridge.py`: `EtriUnavailable`, `SearchSpaceUnavailable`, `EtriMixed`(필드 `root=None`, `head=None`, `files`(3 vendored 파일 sha256: `etri_mixed_blocks.py`, `seed_variants.py`, `runtime_constants.py` — `Path(mod.__file__).read_bytes()`; `__file__` 없으면 `SearchSpaceUnavailable`), `capabilities`, `make_seed_variants`, `audit_config`, `audit_model`, `provenance()`= `{"root": None, "git_head": None, "files_sha256": …, "vendor_origin": {<원본 8파일 상대경로: sha256>, "etri_nas_head": "18c4815…", "space_version_before": "mbnas-mixed-space/2"}, "registration": caps, "env": {"gate": None}, "functions": [...]}`), `load_etri_mixed(root=None)`(root 인자는 무시·경고 없이 수용 → 호출자 시그니처 유지), `probe_mixed_pool()`, `ensure_mixed_registered()`(= operators.ensure_registered). `sys.path`·`.git`·`ETRI_NAS_MIXED` 제거.
5. `mbnas_core/search/space.py`: `SPACE_VERSION="mbnas-mixed-space/3"`, `SPACE_HASH_DEFINITION` 문자열은 ETRI_RUNTIME_SCOPE §6.3 그대로; `_add_family`의 origin = `{"kind": "vendored_mixed_seed_variant", "function": "mbnas_core.search.mixed_pool.seed_variants.make_mixed_seed_variants", "base": src, "mixed_pool_files_sha256": dict(sorted(self.etri.files.items()))}`; `describe()["etri"]` 키 유지. 그 외 바이트 불변.
6. `protocol/validator.py`: probe → `bridge.probe_mixed_pool()`; `search_space_resolved = {"variant": ..., "etri_root": None}`. `console/routes_meta.py`: `capabilities.search_space = {variant, root: None, reason, checked: "vendored operators importable + ultralytics installed", fallback_policy}`.
7. `register_hook`: child/selection 문자열 → `"mbnas_model_runtime.operators:ensure_registered"`. `adapters/base.py:resolve_register_hook` 허용목록 = `{"mbnas_model_runtime.operators:ensure_registered", "mbnas_server.search.etri_bridge:ensure_mixed_registered"(→ 동일 대상으로 매핑)}`; 그 외 문자열은 거부(기존 동작).
8. `block_catalog.ETRI_MIXED_SOURCE = "mbnas_model_runtime/operators/etri_mixed_blocks.py (vendored from yolo_ga_runtime/mixed_blocks.py sha256 4233dd32…)"`, `_E_MODEL`/`ETRI_HYBRID_SOURCE` 경로 문자열 → `mbnas_model_runtime.adapters.etri_hybrid.model` / `mbnas_model_runtime/adapters/etri_hybrid/model.py`.
9. 참조 사본: `docs/internal/provenance/vendored_reference/yolo_ga_runtime/{mixed_blocks.py, mixed_search_space.py, mixed_hooks.py, runtime.py}` + `README.md`(sha256 표, 미이식 사유). owner 전용(공개 ZIP 제외 대상).
10. `docs/internal/provenance/MIXED_SPACE_HASH_MAP.json`: WP-1V가 격리 venv에서 `SearchSpace.create(nc=1, imgsz=640)`의 `space_hash/prior_hash`와 3파일 sha256, 원본 8파일 sha256을 기록.
11. parity 테스트(`tests/owner/search/test_operators_parity.py`): 서브프로세스 A는 스냅샷 원본 `mixed_blocks.py`를 파일 경로로 import(`importlib.util.spec_from_file_location`, 패키지 `__init__` 미실행)해 `register_ultralytics_blocks()` 후 yolo11n native/cnn_backbone/transformer_backbone 3 YAML을 `DetectionModel`로 CPU 빌드; 서브프로세스 B는 vendored `operators`로 동일 수행; 비교: 모듈 클래스명 시퀀스·파라미터 수·`structural_hash`·64px forward 출력 shape 전부 동일. 추가로 `tests/public/model_runtime/test_parse_model_gate.py`: 설치본 parse_model sha == EXPECTED, 패치 집합 크기 37/17(정적).

## 7. 과도기 실행 모델 (DD-A14)

- `mbnas_core.owner_worker.main.Worker`: 원본 `worker/main.py` 로직 유지(`acquire_lease`, reclaim, `_settle_job`, retention, publish). `WorkerConfig`에 `generation_only: bool|None` 분리(R6)하되 기본 동작(`--cpu` → generation_only=True)은 유지. 자식 argv: `[worker_python, "-m", "mbnas_core.owner_worker.child", "--job-id", id, "--db-path", db]`.
- `mbnas_core.owner_worker.child`: `load_context`(DB)·`native_params`·`search_arch`·`latest_live_full_state` 유지. **baseline arch 주입**(DD-A16): `native_params`에서 `arch is None and 후보 arm == Baseline`이면 `mbnas_core.registry.load_registry(settings)`로 상태 확인(AVAILABLE 아니면 `JobFailure("BASELINE_UNAVAILABLE", …)`, 기존 `training/context.resolve_model`의 메시지 재사용) 후 `arch_spec(adapter_id, weights_dir).as_dict()`를 `params["arch"]`로 넣는다. 핸들러 해석은 `mbnas_core.owner_worker.contract.resolve_handler`(6 kinds; `HANDLERS_ENV` 스텁 override 유지). `run_official_evaluation` 분기 → `mbnas_runner.evaluation.official_evaluation`.
- `mbnas_runner.child`: `run_job(spec, emit)`(원본 run_job 본문), `main(argv)`: `--spec-file <json>`(JobSpec JSON: `job_id, run_id, kind, protocol_hash, candidate_id, rung, cumulative_target, fixed_horizon, arm, search_seed, training_seed, attempt, params`)을 읽어 실행; DB 접근 없음. WP-1에서는 테스트 1개(`tests/public/runner/test_child_spec_file.py`: 스텁 핸들러 모듈로 SUCCEEDED JSONL 출력)만.
- `mbnas_runner.training.context.resolve_model(params, settings)`: `params.arch`가 필수(없으면 `JobInputError("ARCH_REQUIRED", …)`); `adapter_for`는 `mbnas_model_runtime.registry`.
- import 검사기 과도기 허용: `mbnas_core.owner_worker.{child,contract} → mbnas_runner.*`(동적 문자열 포함). 만료: WP-2.

## 8. 기타 코드 변경(필수)

| 항목 | 변경 |
|---|---|
| fitness 제거(DD-A15) | `mbnas_runner.evaluation.handlers`: `fitness_and_gate/_fitness_rung` 삭제, L29-30 import 삭제, `run_evaluation_job`의 `payload.update(fitness…)` 삭제, metric emit에서 `fitness` 키 제거; `mbnas_runner.training.handlers._evaluate`: 동일 제거(`gate=None, fitness_rung` 세트 삭제). 두 함수는 `mbnas_core.protocol.fitness_gate`로(테스트 `test_fitness_gate_integration`, `test_protocol_promotion`은 owner에서 이 모듈 import) |
| presets 경로(DD-A19/A21) | `mbnas_core.protocol.presets`: `PILOT_DATA_ROOT/DEFAULT_OUTPUT_ROOT/PILOT_OUTPUT_ROOT` 상수 → 함수 `preset_paths(settings=None)`: settings의 `preset_data_root/preset_output_root`가 있으면 그 아래 경로, 없으면 `/ABSOLUTE/PATH/TO/MBNAS_data/pilot_visdrone_v1/...`, `/ABSOLUTE/PATH/TO/MBNAS_runs/...` placeholder. `handoff_protocol_20261006.json`은 바이트 불변(패키지 데이터)이되 `default_protocol()`이 console.paths를 placeholder로 치환해 반환(원문 파일은 그대로) |
| 캠페인 배선(DD-A23) | `mbnas_server.api.app`: `ArchiveStore(settings.archive_roots, settings.archive_driver_dirs, settings.archive_cache_ttl_s, campaign_roots=settings.archive_campaign_roots)`(scanner 시그니처 확인 후 전달) |
| services registry | `mbnas_core.console.services.load_registry`: `importlib.import_module("mbnas_server.registry")` 탐색 삭제 → `from mbnas_core import registry; registry.load_registry(settings.output_root)`; `EXAMPLE_REGISTRY` → 패키지 데이터 |
| 호스트 경로 | `benchmark/capability.py`·`adapters/rfdetr_adapter.py:DEFAULT_RF_HOME`·`registry/__init__.py:DEFAULT_OUTPUT_ROOT, ETRI_NOTEBOOK`·`provenance/env_lock.py`·`console_stack` 등 금지 문자열 전부 제거(설정값 또는 `mbnas_home()` 기반). `registry/__init__.py:ETRI_NOTEBOOK`는 `adapters/etri_hybrid`의 `NOTEBOOK_BLOB_SHA1` 상수만 남기고 경로 참조 삭제(존재 검사 제거) |
| 버전 문자열 | core 앱 `FastAPI(version="1.4.2")`; contracts `openapi.json` 1.4.2(delta 스냅샷) |
| logger 이름 | `mbnas_core.*`, `mbnas_server.*`, `mbnas_runner.*` |
| schema/핸드오프 데이터 | `mbnas_core/db/schema.sql`, `mbnas_core/protocol/handoff_protocol_20261006.json` 패키지 데이터(`importlib.resources`) |

## 9. 테스트 재배치 (DD-A22)

- 루트 `pyproject.toml` `[tool.pytest.ini_options]`: `testpaths=["tests/owner","tests/public"]`, `addopts="-ra --import-mode=importlib -p no:cacheprovider"`, `timeout=600`, markers: `gpu, slow, host_data, live, legacy_records, owner_worker, runner_protocol`. 기본 실행 명령(게이트): `pytest tests/owner tests/public -m "not gpu and not live and not legacy_records and not host_data"`.
- 디렉터리: `tests/owner/{api,orchestration,qa,search,worker,unit,training,registry,evaluation,adapters}`(원본 하위 디렉터리 유지), `tests/public/{contracts,model_runtime,runner,server,infra}`, `tests/_shared/{conftest_common.py, harness.py, stub_handlers.py, qa_handlers.py, datasets.py(make_dataset), gpu_guard.py, fixtures/}`. `proposed_home`(TEST_INVENTORY.json) 적용: `owner`→tests/owner, `public`·`public(host_data)`→tests/public/<model_runtime|runner|contracts>, `public(server)`→tests/public/server, `both(infra)`→tests/_shared 또는 양쪽 conftest, `owner(legacy_records)`→tests/owner/legacy(마커 `legacy_records`, `test_evaluation_summary_record.py`·`test_orchestration_records_set2.py`는 삭제), `owner(live)`→tests/owner/live(마커 `live`).
- conftest: `clean_env`는 `MBNAS_*` 전부 제거(단 `MBNAS_TEST_*` 보존); `MBNAS_TEST_ROOT` 기본 `$TARGET_ROOT/runtime/test_root`(`tmp` fallback 없이 생성), `MBNAS_TEST_WEIGHTS_DIR` 기본 `$MBNAS_TEST_ROOT/weights`, `MBNAS_TEST_RUNS_ROOT` 기본 `$MBNAS_TEST_ROOT/runs`, `PYTEST_GPU_LOCK` 기본 `$MBNAS_TEST_ROOT/locks/gpu.lock`; `MBNAS_TEST_ETRI_ROOT`·`MBNAS_ETRI_NAS_ROOT`·`MBNAS_TEST_ASSUME_ULTRALYTICS` 삭제; `settings` fixture는 `CoreSettings`(owner) / `RunnerSettings`·`GatewaySettings`(public) 각각; `host_data` 마커: 실제 데이터(PILOT 서브셋·probe/bench evidence·`/data/mgjeong` run) 필요 테스트는 환경변수 `MBNAS_TEST_HOST_DATA=1`이 없으면 skip.
- 하네스(`tests/_shared/harness.py`): `InlineWorker`는 `mbnas_core.owner_worker.main.Worker`·`owner_worker.child.load_context/run_job` 사용; `api_settings` fixture는 `CoreSettings`; `client`는 `mbnas_core.api.app.create_app`; archive 테스트는 `mbnas_server.api.app.create_app(GatewaySettings)`.
- 적응 허용 코드 A1~A20(TEST_INVENTORY §4) 외의 변경 금지. A3(ETRI 해시)는 §6 정의로 기대값 갱신; A4는 DD-A13 기본값으로; A15의 3 테스트는 owner worker 경로 유지(PROPOSE_GENERATION을 owner worker가 실행 — WP-1에서는 동일 프로세스이므로 변경 최소); A20은 `mbnas_core.protocol.fitness_gate` import. `test_space_hash_content.py`의 "다른 루트 거부"는 삭제, "내용 변조→해시 변화"는 vendored 모듈 사본 변조로 재작성. `test_etri_bridge_permissions.py`는 삭제하고 "bridge 예외가 500이 아닌 진단"만 `test_mixed_pool_bridge.py`로 승계. `test_repair_evidence_seed.py`는 **무변경 통과 게이트**.
- 신규 테스트: `tests/public/infra/test_import_closure.py`(§13 규칙), `tests/public/infra/test_no_host_facts.py`(금지 문자열), `tests/public/model_runtime/test_parse_model_gate.py`, `tests/owner/search/test_operators_parity.py`, `tests/public/runner/test_child_spec_file.py`, `tests/owner/unit/test_settings_split.py`(3 Settings 기본값·접두사·MBNAS_HOME).

## 10. 웹 최소 변경(WP-1H)

`mbnas_web/scripts/gen-types.mjs` 기본 계약 경로 → `../mbnas_contracts/src/mbnas_contracts/openapi/openapi.json`; `tests/contract.test.ts`·`tests/archive_contract.test.ts`·`tests/preset_parity.test.ts`의 상대경로 → 새 위치(preset parity는 `../mbnas_core/src/mbnas_core/protocol/handoff_protocol_20261006.json` 또는 `config/examples/protocol.pilot_smoke.example.json`); `vite.config.ts` dev proxy 기본 `http://127.0.0.1:8710`; README의 경로 예시는 WP-7. 검증: `npm run check:types`(types.gen.ts 바이트 동일), `npm run typecheck`, `npm test`(vitest) 통과 — DD-A8 변경은 WP-6.

## 11. 스크립트 재배치(WP-1A)

MODULE_MAP §7 표대로: `scripts/owner/{probe_registry.py, search_dry_run.py, smoke_end_to_end.py}`, `scripts/runner/{bench_host_gpu.py, download_weights.py, resume_equivalence.py}`(presets import → `--protocol-json` 인자), `scripts/public/make_pilot_subset.py`, `scripts/dev/{dev_stack.py, source_tree_hash.py, rewrite_imports.py, check_import_closure.py}`, `tests/public/tools/{api_bearer_only.py, smoke_api_only.py}`; `setup_venv.sh`·`doctor.py`·`run_tests_host.sh`·`console_stack.sh`는 복사하지 않는다(WP-7 대체). 각 스크립트는 `python -m py_compile` 통과 + import가 소유 패키지 경계를 지켜야 한다.

## 12. provenance

`PLACEMENT_MANIFEST.jsonl`(§3.4) + `docs/internal/evidence/WP1/`: `install.log`, `import_closure.txt`, `host_facts.txt`, `pytest_owner_gate.txt`, `pytest_public.txt`, `pytest_skips.txt`(skip 사유 집계), `parity.txt`, `web_checks.txt`, `TEST_ADAPTATIONS.md`, `SUMMARY.md`(명령·cwd·인터프리터·시각·exit code·소요시간·잔여 결함).

## 13. 수용조건(WP-1V가 실행·기록, 설계자가 판정)

1. `runtime/venv`에 5패키지 editable 설치(`pip install --no-index --find-links build/wheelhouse -c constraints.txt --no-deps -e <pkg>` ×5) exit 0; `python -c "import mbnas_contracts, mbnas_model_runtime, mbnas_core, mbnas_runner, mbnas_server"` exit 0; 콘솔 스크립트 `mbnas-core-api --help`, `mbnas-gateway --help`, `mbnas-runner --help`, `mbnas-core-doctor --help` exit 0.
2. `scripts/dev/check_import_closure.py` 위반 0건(과도기 허용 2건 제외, 목록 출력). 금지 문자열 0건.
3. `pytest tests/public -m "not gpu and not live"` 0 failed/0 error(skip 사유 기록).
4. `pytest tests/owner -m "not gpu and not live and not legacy_records and not host_data"` → 목표 0 failed/0 error. 미달 시 fix 라운드(최대 3회) 후 잔여를 파일·사유와 함께 보고(은폐 금지). 이 결과가 **T1 기준선**(재배치 직후)이다.
5. `test_repair_evidence_seed.py` 무변경 통과; parity 테스트 통과; `MIXED_SPACE_HASH_MAP.json` 기록.
6. 웹 `check:types`/`typecheck`/`vitest` 통과.
7. 원본 etri_nas 미변경(스냅샷 manifest 대비 live sha 비교는 설계자가 수행).

## 14. 하위 작업·소유 파일·모델

| ID | 범위(소유 디렉터리) | 모델 |
|---|---|---|
| WP-1A scaffold(선행, 단독) | 5 패키지 디렉터리 생성·pyproject·`__init__`·스냅샷 복사·§3.2 import 재작성·§11 스크립트·`constraints.txt`·루트 pyproject·`scripts/dev/{rewrite_imports,check_import_closure}.py`·editable 설치·PLACEMENT_MANIFEST 초판. 심볼 이동(§4)과 §5~§8의 의미 변경은 **하지 않는다**(단, 새 모듈 자리에 `# TODO WP-1x` 빈 파일을 만들지 말 것 — 없는 모듈은 import 오류로 남겨 둔다) | opus |
| WP-1B contracts | `mbnas_contracts/**` (§4의 contracts 항목 전부 + home.py) | opus |
| WP-1C model_runtime | `mbnas_model_runtime/**` (§6.1-3, registry 분할, genotype_audit, block_catalog 문자열, env_lock, checkpoint/datasets/evaluation/benchmark 모듈 import 정리, adapters register_hook 허용목록, rfdetr pickle 플래그) | opus |
| WP-1D core | `mbnas_core/**` (§5 CoreSettings, §6.4-6 mixed_pool/space/validator/routes_meta, §7 owner_worker, §8 fitness_gate/presets/services/registry/model_artifacts/training_runs/console/api/ops) | opus |
| WP-1E runner | `mbnas_runner/**` (§5 RunnerSettings, §7 child/contract, §8 fitness 제거, context.resolve_model, handlers/stage_trainer/recipe import 정리, gpu/gpu_guard, `__main__`) | sonnet |
| WP-1F server | `mbnas_server/**` (§5 GatewaySettings, api/app·auth·errors·deps·contract_check, archive 패키지 import 정리·캠페인 배선) | sonnet |
| WP-1G tests | `tests/**`, `docs/internal/evidence/WP1/TEST_ADAPTATIONS.md` (§9) | opus |
| WP-1H web | `mbnas_web/{scripts/gen-types.mjs, tests/contract.test.ts, tests/archive_contract.test.ts, tests/preset_parity.test.ts, vite.config.ts}` (§10) | sonnet |
| WP-1V verify+fix | §13 실행·evidence 기록; 실패는 소유 패키지별 fixer에게 전달(최대 3라운드); 설계자 판정용 SUMMARY | opus(검증) / opus·sonnet(fixer) |

병렬 단계(B~H)에서 다른 소유 디렉터리의 파일을 수정해야 하면 수정하지 말고 SUMMARY의 `cross_package_requests`에 "파일·심볼·요청 내용"을 적는다(WP-1V가 조정).

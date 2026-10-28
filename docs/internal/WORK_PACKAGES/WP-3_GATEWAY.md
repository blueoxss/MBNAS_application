# WP-3 GATEWAY·콘솔 보안 계약 v1 (설계자 claude-fable-5-1, 2026-10-23 22:4x; WP-2 수용 후 착수)

우선순위: 이 문서 > DECISIONS(DD-A3, A13, A19~A23) > SECURITY_BASELINE.md(R1, R5, R7, R11, R13) > WEB_PRIVATE_INVENTORY.md §7(화면↔엔드포인트) > DTO_ALLOWLIST(§투영은 WP-4).

## 0. 목표
브라우저가 보는 `/api/v1` 계약을 `mbnas_server`(gateway)가 제공하고, gateway는 core `/core/v1/console/*`에 서비스 토큰으로 중계한다. core 콘솔 라우트는 `/core/v1/console`로 이동(공개 DTO 투영은 WP-4에서 추가되지만 WP-3에서 투영 훅 자리 `project_public(component, payload, scope)`를 만들어 pass-through로 둔다). 고객 호스트에서의 데이터 manifest 계산(gateway) → core validate 전달. 구조 검사 sandbox, YAML allowlist, rate limit, 업로드 엔드포인트.

## 1. core 측
1. `mbnas_core.api.app`: 콘솔 라우터를 `/core/v1/console`(scope server 또는 admin)로 마운트; `/api/v1` 직접 노출은 설정 `MBNAS_CORE_EXPOSE_LEGACY_API=false` 기본(owner 로컬 디버그용만 true). SSE: `/core/v1/console/runs/{id}/events`(동일 SSE 포맷).
2. 투영 훅: `mbnas_core.console.projection.project_public(component: str, payload: dict, principal_scope: str) -> dict` — WP-3에서는 admin이면 원본, server면 `prune(component)` 수준(기존 동작). WP-4가 allowlist로 교체.
3. **manifest provider**: `POST /core/v1/console/protocols/validate` 본문에 선택 필드 `dataset_manifests`(gateway 계산: 각 split의 `{split_hash, counts, class_mapping_hash, names, sample_ids_sha256, manifest_json_artifact_id?}`)와 `path_checks`(gateway 호스트에서 수행한 allow-root/존재/쓰기 검사 결과)를 받아 validator가 파일 대신 사용(`ValidationContext.manifest_provider`, WP-2 §1.6). 미제공 시 core 로컬 검사(owner all-in-one). 응답 `resolution.paths.*.realpath`는 WP-4에서 제거 예정이므로 WP-3에서는 gateway가 중계 시 `realpath`를 `requested`로 덮는다(임시 scrub).
4. **구조 검사 sandbox**(`mbnas_core.structure.sandbox` + `check_worker`): A3 §10.2 설계 그대로(argv `python -I -m mbnas_core.structure.check_worker`, stdin JSON, rlimit CPU/AS/NOFILE/FSIZE/CORE, setsid, PDEATHSIG, wall timeout, env 최소화 + `ULTRALYTICS_SAFE_LOAD=1 YOLO_AUTOINSTALL=false YOLO_OFFLINE=1 CUDA_VISIBLE_DEVICES=""`, `unshare -rn` 가용 시 사용·기록, 전역 세마포어 1, 결과 ≤1 MiB, 실패 코드 매핑 STRUCTURE_CHECK_TIMEOUT/STRUCTURE_RUNTIME_UNAVAILABLE/STRUCTURE_CHECK_RESOURCE_LIMIT). `model_services.run_structure_check/check_registry_model/check_user_model`과 owner worker의 `SearchSpace.inspect`가 **같은 sandbox**를 쓴다(PROPOSE_GENERATION child 내부에서는 프로세스가 이미 격리된 자식이므로 in-process 허용 — 설정 `sandbox_in_proposer=false` 기본).
5. **YAML allowlist·인자 스키마**(R5): `mbnas_model_runtime.genotype.validate_declared(yaml)` 신설 — MODULE_ALLOWLIST(ultralytics 8.4.173 base/repeat/head 집합 + ETRI 3종 + `nn.Upsample`, `nn.Identity`; 생성은 `parse_model_8_4_173`의 집합에서 유도) 밖 모듈 → `BLOCK_UNSUPPORTED`; 인자 스칼라 타입·리스트 depth≤2·정수≤65536·채널≤4096·repeats≤64·행≤256·nc≤10000·alias≤64·원문 ≤1 MiB; `activation` 가드를 Genotype 생성 시점으로; `input_size` 상한 `CoreSettings.structure_check_max_input=1280`. `check_structure` 진입 전 호출. 음성 테스트(SECURITY §4 1~6).
6. 사용자 YAML 허용 루트 = `model_yaml_roots ∪ output_roots`(etri 루트 제거 확인).
7. principals: gateway용 server scope 토큰 발급 CLI(`mbnas-core principal create --kind server`), owner 로컬 토큰.

## 2. gateway 측 (`mbnas_server`)
1. `core_client.py`(httpx, 서비스 토큰, TLS verify, 타임아웃, 오류 relay: core `Error{code,…}` → 동일 코드로 재전달 + `request_id` 체인(`X-Request-ID` 전달), 네트워크 실패 → 503 `CORE_UNAVAILABLE`(명시 실패; fallback 없음)).
2. `/api/v1` relay 라우터: WEB_PRIVATE_INVENTORY §7의 전 엔드포인트(health, presets, registry, validate, runs CRUD/snapshot/events/pause/resume/cancel/export/final-retrain, training-runs, candidates detail/structure-check, registry structure routes, structure/block-catalog, artifacts(WP-5 전까지 core relay), archive(로컬)). `GET /health`: gateway 상태 + core health 요약(core 불가 시 `core: unreachable`, HTTP 200에 status degraded).
3. SSE relay: gateway가 core SSE를 구독해 그대로 스트리밍(Last-Event-ID 전달, heartbeat 유지, core 끊김 시 `snapshot.required`가 아니라 연결 종료 + 재시도 헤더).
4. validate 전처리: 요청 protocol의 `console.paths.*`를 gateway 호스트 allowlist(`GatewaySettings.dataset_roots/output_roots`)로 검사하고 `mbnas_model_runtime.datasets.manifest.DatasetService`로 manifest를 계산해 `dataset_manifests`로 동봉(계산 결과는 gateway 캐시 `$MBNAS_HOME/gateway/manifests`). core 응답을 그대로 반환. 같은 호스트 owner 프로필에서는 gateway가 계산해도 동일.
5. 업로드(F5a): `POST /api/v1/uploads/model-config`(multipart 또는 raw body ≤1 MiB, `.yaml/.yml`), 원문 바이트를 `artifact_store/uploads/<sha256>.yaml`에 저장(원문 보존), 응답 `{upload_id, source_sha256, bytes, server_path}`; 이후 `POST /structure-check`·`POST /training-runs(source.kind=user)`는 `model_path` 대신 `upload_id`를 받아 gateway가 로컬 경로로 치환(core는 경로를 받지 않고 **gateway가 YAML 텍스트+sha를 본문에 실어 전달** — core 콘솔 `structure-check`/`training-runs`에 `model_yaml_text`/`source_sha256` 필드 추가, 계약 1.5.0 WP-4).
6. rate limit(R13): 토큰 버킷(기본 10 rps/burst 50), 고비용 라우트(validate, structure-check*, training-runs, export) 동시 1·분당 N(설정), SSE 동시 4/토큰; 초과 429 `RATE_LIMITED`; 민감 조회(candidate detail, lineage, generations) gateway 감사 로그(토큰 id 해시·라우트·대상·시각) 파일 `$MBNAS_HOME/gateway/logs/audit.jsonl`.
7. 정적 웹(DD-A30): `GatewaySettings.web_dist`(기본 `<ROOT>/mbnas_web/dist`)가 있으면 `/`에 SPA 정적 서빙(index.html fallback, `/api` 제외, no-store 아님·immutable asset 캐시 헤더), 없으면 `/`는 404+안내 JSON. 웹 번들의 API origin은 same-origin(`/api/v1`).
8. 오류: 공개 오류 `{code, location, message(안전), request_id}`; `location` enum은 contracts.
8. 테스트 하네스 (가): `tests/_shared/harness.py`에 `combined_app()` — core 앱(TestClient/ASGITransport)과 gateway 앱을 같은 프로세스에서 결합(gateway의 core_client가 ASGI transport로 core 앱 호출). 기존 owner API 테스트는 `client` fixture가 gateway 앱을 가리키도록 전환(계약 `/api/v1` 보존).

## 3. 수용조건
- owner 게이트(`tests/owner`) 통과 수가 WP-1 기준선 이상(gateway 경유), gateway 전용 검사(auth/CORS/limits/redaction/rate limit/업로드) 공개 테스트 통과.
- 음성 테스트: allowlist 밖 모듈(`check_requirements`, `torch_load`, `nn.Linear` 거대 차원) → 빌드 전 FAIL·부작용 없음(소켓 차단 fixture); input_size 초과 422; alias 65 거부; 행 257·채널 4097 거부; sandbox RLIMIT 초과 시 API RSS 불변·UNVERIFIED 코드; 동시 검사 세마포어.
- core 종료 상태에서 gateway: health degraded·모든 중계 503 `CORE_UNAVAILABLE`, 자동 OFF/로컬 fallback 없음(테스트).
- 웹 e2e(mock) 186 전부 + 실제 2프로세스(core+gateway) 수동 하네스 1회(validate→run(OFF)→snapshot→다운로드) 로그.

## 4. 소유
WP-3A core(console 이동·sandbox·validate_declared·manifest provider; opus) / WP-3B gateway(relay·SSE·업로드·rate limit·core_client; opus) / WP-3C 테스트·하네스(opus) / WP-3V verify(opus).

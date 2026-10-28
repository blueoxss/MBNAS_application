# INTERFACES — 인터페이스 명세 요약

①·③의 연산 표는 `mbnas_contracts`에 포함된 계약 파일(OpenAPI 3.1)에서 생성한 것이다. ②·④는 별도 계약 파일이 없어 소스 코드의 라우터 정의를 기준으로 요약했다. 이 문서는 요약이며 권위 있는 정의는 계약 파일이다.

## 1. 인터페이스 목록

| ID | 이름 | 제공 → 사용 | 경로 | 인증 | 계약 근거 | 공개 범위 |
|---|---|---|---|---|---|---|
| ① | 공개 사용자 API | gateway(SS2) → 브라우저(SS1)·사용자 도구 | `/api/v1/*` | 브라우저 Bearer 토큰(`MBNAS_GW_API_TOKEN`). artifact 다운로드는 Bearer 또는 짧은 수명의 서명 URL | `mbnas_contracts/openapi/openapi.json` | 공개 |
| ② | 콘솔 중계 API | core(SS6) → gateway(SS2) | `/core/v1/console/*` | gateway 서비스 토큰(principal kind `server`) | ①과 같은 경로·DTO + 중계 전용 요청 필드(§4) | 경로·요청 형식은 공개 gateway 코드로 드러남. 구현은 비공개 |
| ③ | runner 원격 작업 프로토콜 | core(SS6) → runner(SS3) | `/core/v1/runner/*` | runner 토큰(principal kind `runner`, `runner_id`에 결속) + 작업별 job ticket | `mbnas_contracts/openapi/openapi_runner.json` | 프로토콜 공개, 구현 비공개 |
| ④ | owner 관리 API | core(SS6) → owner 관리자 | `/core/v1/owner/*` | admin 토큰(principal kind `admin`) | 계약 파일 없음(코드 기준) | 비공개 |
| ⑤ | 정적 웹 | gateway(SS2) → 브라우저 | `/`(SPA fallback, `/api` 제외) | 없음(정적 파일) | — | 공개 |
| 부1 | 구성 YAML 업로드 | gateway(SS2) → 브라우저 | `POST /api/v1/uploads/model-config` | 브라우저 Bearer | 계약 1.5.0(`UploadResponse`). gateway가 직접 처리(core로 중계하지 않음) | 공개 |
| 부2 | 명령행(CLI) | 각 패키지 | `mbnas-gateway`, `mbnas-gateway-doctor`, `mbnas-runner` / owner: `mbnas-core-api`, `mbnas-core-owner-worker`, `mbnas-core-doctor`, `mbnas-core-principal` | — | 각 명령의 `--help` | 공개(owner 명령은 owner 배포본에만 설치) |
| 부3 | 프로세스 내 Python 호출 | model runtime(SS4) → runner·gateway·core | Python import | — | 패키지 공개 API | 공개 |

## 2. 공통 규칙(①–④)

- **오류 본문**: `{code, message, request_id, errors?}` 형식이며 `code`는 안정적인 대문자 코드다(예: `AUTH_REQUIRED`, `CORE_UNAVAILABLE`, `LEASE_LOST`). 오류에는 발생 위치 `location`(`gateway|core|runner|store`)이 붙는다(계약 1.5.0 `Error.location`). 공개 오류에는 예외 문자열·호스트 경로를 싣지 않는다.
- **요청 추적**: `X-Request-ID`를 받아 그대로 돌려주며, gateway는 core 호출에 같은 값을 전달한다. gateway와 core의 접근 로그는 같은 `request_id`로 한 요청을 잇는다.
- **본문 한도**: 공개 API 기본 1 MiB, 구성 YAML 업로드 1 MiB, runner 경로 32 MiB.
- **전송 보안**: loopback이 아닌 주소로 평문 `http://`를 쓰는 core 연결은 runner(시작 전)와 gateway(core 연결 시)가 거부한다(`CORE_URL_INSECURE`; gateway는 명시 설정으로만 예외 허용). TLS 검증은 끄지 않으며 사설 CA는 설정 파일로 지정한다.
- **요청률 제한(gateway)**: 토큰별 요청률·고비용 경로 동시 실행 수·SSE 연결 수를 제한하고 초과 시 `429 RATE_LIMITED`와 `Retry-After`를 돌려준다(기본값은 `docs/public/OPERATIONS.md`).
- **core 부재 시**: gateway는 대체 동작 없이 `503 CORE_UNAVAILABLE`로 명시 실패하고, `/api/v1/health`는 core를 `unreachable`로 표시한다.

## 3. ① 공개 사용자 API `/api/v1`

<!-- BEGIN GENERATED:api-v1 -->
계약 문서: **MBNAS Server API** 버전 `1.5.0` · 경로 38개 · 연산 39개 · 인증: `Authorization: Bearer <브라우저 토큰>`(artifact 다운로드는 Bearer 또는 서명 URL)

| # | 메서드 | 경로 | 기능 | 요청 본문 | 성공 응답 | 오류 응답(HTTP) |
|---:|---|---|---|---|---|---|
| 1 | GET | `/api/v1/health` | 서비스 상태·버전·허용 경로·capability 조회(gateway 상태 + core 요약) | — | 200 `Health` | 401, 403, 409, 422, 500 |
| 2 | POST | `/api/v1/protocols/validate` | 실험 프로토콜 검증·해석(resolved protocol, protocol_hash, validation_id) | `ValidateRequest` | 200 `ValidateResponse` | 401, 403, 409, 422, 500 |
| 3 | GET | `/api/v1/runs` | run 목록(최신순) | — | 200 `RunList` | 401, 403, 404, 500 |
| 4 | POST | `/api/v1/runs` | 검증된 프로토콜로 탐색 run 생성(Idempotency-Key) | `RunRequest` · 헤더 `Idempotency-Key` | 200 `RunCreated` | 401, 403, 409, 422, 500 |
| 5 | GET | `/api/v1/runs/{run_id}/snapshot` | run 스냅샷(후보·세대·작업자·비용) | — | 200 `Snapshot` | 401, 403, 409, 422, 500 |
| 6 | GET | `/api/v1/runs/{run_id}/events` | run 이벤트 스트림(SSE, Last-Event-ID/after_seq로 재개) | — · 헤더 `Last-Event-ID` | 200 SSE 스트림 | 500 |
| 7 | POST | `/api/v1/runs/{run_id}/pause` | 일시정지 요청(안전 경계에서 full-state 저장 후 PAUSED) | JSON | 200 `Run` | 401, 403, 409, 422, 500 |
| 8 | POST | `/api/v1/runs/{run_id}/resume` | 재개 요청(full-state 검증 후 재개) | JSON | 200 `Run` | 401, 403, 409, 422, 500 |
| 9 | POST | `/api/v1/runs/{run_id}/cancel` | 취소 요청 | JSON | 200 `Run` | 401, 403, 409, 422, 500 |
| 10 | GET | `/api/v1/runs/{run_id}/artifacts/{artifact_id}` | artifact 바이트 다운로드(Bearer 또는 서명 URL, sha256 대조) | — | 200 | 401, 403, 404, 409, 410, 500 |
| 11 | GET | `/api/v1/runs/{run_id}/export` | run 내보내기 ZIP | — | 200 `application/zip` | 500 |
| 12 | GET | `/api/v1/protocols/presets` | 서버 preset(DEFAULT_BASELINE14 설계 예시, PILOT_SMOKE) | — | 200 `PresetList` | 401, 403, 404, 500 |
| 13 | GET | `/api/v1/registry` | baseline 모델 registry와 capability 증거 | — | 200 `RegistryList` | 401, 403, 404, 500 |
| 14 | GET | `/api/v1/runs/{run_id}` | run 객체(resolved protocol, 비용) | — | 200 `Run` | 401, 403, 404, 500 |
| 15 | GET | `/api/v1/runs/{run_id}/candidates/{candidate_id}` | 후보 상세(계보·블록·평가·checkpoint·비용·게이트) | — | 200 `CandidateDetail` | 401, 403, 404, 500 |
| 16 | POST | `/api/v1/runs/{run_id}/artifacts/{artifact_id}/refresh` | 만료된 artifact 참조의 서명 URL·메타데이터 재발급 | — | 200 `Artifact` | 401, 403, 404, 500 |
| 17 | POST | `/api/v1/runs/{run_id}/final-retrain` | 완료된 탐색 run의 최종 재학습(FINAL_RETRAIN) child run 생성 | `FinalRetrainRequest` · 헤더 `Idempotency-Key` | 200 `RunCreated` | 401, 403, 404, 409, 422, 500 |
| 18 | GET | `/api/v1/archive/runs` | 아카이브 run 목록 | — | 200 `ArchiveRunList` | 401, 403, 503, 500 |
| 19 | GET | `/api/v1/archive/runs/{run_id}` | 아카이브 run 개요 | — | 200 `ArchiveRun` | 401, 403, 404, 422, 503, 500 |
| 20 | GET | `/api/v1/archive/runs/{run_id}/baselines` | baseline과 탐색 기준점 | — | 200 `ArchiveBaselineList` | 401, 403, 404, 422, 503, 500 |
| 21 | GET | `/api/v1/archive/runs/{run_id}/generations` | 세대별 진행·특성 | — | 200 `ArchiveGenerationList` | 401, 403, 404, 422, 503, 500 |
| 22 | GET | `/api/v1/archive/runs/{run_id}/generations/{g}` | 세대 상세 | — | 200 `ArchiveGenerationDetail` | 401, 403, 404, 422, 503, 500 |
| 23 | GET | `/api/v1/archive/runs/{run_id}/candidates` | 후보 평가 목록 | — | 200 `ArchiveCandidateList` | 401, 403, 404, 422, 503, 500 |
| 24 | GET | `/api/v1/archive/runs/{run_id}/candidates/{cid}` | 후보 상세 | — | 200 `ArchiveCandidateDetail` | 401, 403, 404, 422, 503, 500 |
| 25 | GET | `/api/v1/archive/runs/{run_id}/pareto` | Pareto archive | — | 200 `ArchivePareto` | 401, 403, 404, 422, 503, 500 |
| 26 | GET | `/api/v1/archive/runs/{run_id}/fidelity` | fidelity(T1/T2 순위 상관) 산출물 | — | 200 `ArchiveFidelity` | 401, 403, 404, 422, 503, 500 |
| 27 | GET | `/api/v1/archive/runs/{run_id}/confirm` | 3-seed 확인 단계 | — | 200 `ArchiveConfirm` | 401, 403, 404, 422, 503, 500 |
| 28 | GET | `/api/v1/archive/runs/{run_id}/files/{kind}` | 등록된 산출 파일 | — | 200 `image/png`, `text/markdown`, `text/plain` | 401, 403, 404, 413, 422, 503, 500 |
| 29 | GET | `/api/v1/archive/compare` | run 비교(조건 포함) | — | 200 `ArchiveCompare` | 401, 403, 404, 422, 503, 500 |
| 30 | GET | `/api/v1/archive/live` | 진행 중 파이프라인 상태 | — | 200 `ArchiveLive` | 401, 403, 503, 500 |
| 31 | POST | `/api/v1/runs/{run_id}/candidates/{candidate_id}/structure-check` | 후보 구조 검사 재실행(CPU, 학습·성능 평가 없음) | — | 200 `StructureReport` | — |
| 32 | POST | `/api/v1/training-runs` | baseline·사용자 구성·후보를 선택한 별도 학습 run 생성 | `TrainingRequest` | 200 `TrainingRunCreated` | 401, 403, 404, 409, 422, 500 |
| 33 | GET | `/api/v1/registry/{model_id}/structure` | registry 모델의 기록된 구조 증거(빌드 없음) | — | 200 JSON | 401, 403, 404, 500 |
| 34 | POST | `/api/v1/registry/{model_id}/structure-check` | registry 모델 구조 관측(CPU, 결과 저장) | JSON | 200 JSON | 401, 403, 404, 422, 500 |
| 35 | POST | `/api/v1/structure-check` | 사용자 detector YAML 구조 검사(결과 저장 안 함) | JSON | 200 `StructureReport` | 401, 403, 404, 409, 422, 500, 507 |
| 36 | GET | `/api/v1/structure/block-catalog` | 블록 설계 출처 표(정적, 모델 빌드 없음) | — | 200 `BlockCatalogTable` | 401, 403 |
| 37 | GET | `/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions` | 후보 구성의 개정 이력(원본·선택본·실행본·생성본) | — | 200 `ModelConfigRevisionList` | 401, 403, 404, 500 |
| 38 | GET | `/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions/{revision_id}/yaml` | 개정 하나의 저장 바이트(다운로드·선택본↔실행본 diff용) | — | 200 `application/x-yaml` | 401, 403, 404, 409, 500 |
| 39 | POST | `/api/v1/uploads/model-config` | 모델 구성 YAML 업로드(gateway 저장소, 내용 주소; gateway가 직접 처리) | `application/yaml`, `application/x-yaml`, `application/octet-stream`, `multipart/form-data` · 헤더 `X-Filename` | 200 `UploadResponse` | 401, 403, 413, 422, 500, 507 |

출처: `mbnas_contracts/src/mbnas_contracts/openapi/openapi.json` (sha256 `70e0cffaab8fafd9c7f230af9169dbbe8812b3eccf7ad7838ca3f46fa9eebc2a`)
<!-- END GENERATED:api-v1 -->

gateway가 직접 처리하는 경로: 업로드(`POST /api/v1/uploads/model-config`, 원문 바이트를 고객 측 저장소에 sha256 이름으로 보존)와 아카이브(`/api/v1/archive/*`), artifact 바이트 제공(core의 기록으로 id·sha256을 확인한 뒤 고객 측 저장소에서 전송. 분리 배치에서 core 호스트에 남는 생성 구조 YAML은 고객 측 저장소에 없으므로 core에서 받아 sha256을 다시 대조한 뒤 전달). 나머지는 core 콘솔 API로 중계한다. 계약의 `servers`는 같은 origin의 상대 루트(`/`)이며 gateway 기본 주소는 `127.0.0.1:8710`이다.

## 4. ② 콘솔 중계 API `/core/v1/console`

- **경로 집합**: ①의 경로 중 아카이브(`/archive/*`)와 업로드를 제외한 경로를 core가 `/core/v1/console` 접두로 같은 의미·같은 DTO로 제공한다(health, presets, registry, protocols/validate, runs 생성·목록·조회·snapshot·events(SSE)·pause·resume·cancel·export·final-retrain, candidates 상세·structure-check, training-runs, registry 구조, structure-check, block-catalog, artifacts 조회·refresh). 아카이브는 고객 호스트의 파일을 읽으므로 gateway가 직접 처리한다.
- **중계 전용 요청 필드**(gateway가 고객 호스트에서 계산해 덧붙임):
  - `POST …/protocols/validate`: `path_checks`(경로 허용·존재·쓰기 검사 결과), `dataset_yaml`(클래스 이름·수, split 경로), `dataset_manifests`(형식 `mbnas.gateway-manifests/1`: split별 hash·개수·클래스 매핑 hash·표본 id hash·중복·split 간 중복).
  - `POST …/structure-check`, `POST …/training-runs`(사용자 구성): 업로드 id나 서버 경로 대신 `model_yaml_text`와 `source_sha256`. core는 고객 호스트 경로를 받지 않는다.
- **응답 투영**: core는 콘솔 응답·SSE 이벤트·내보내기를 계약 1.5.0의 공개 DTO로 투영한다(`additionalProperties:false`, owner 전용 필드 제거, opaque 작업자·장치 id, 상대경로). admin 토큰으로 콘솔 경로를 불러도 투영본이며, 원본은 ④에서만 본다.
- **legacy 노출**: core는 설정 `MBNAS_CORE_EXPOSE_LEGACY_API=true`일 때만 같은 라우트를 `/api/v1`로도 연다(기본 false, owner 로컬 디버그용).

## 5. ③ runner 원격 작업 프로토콜 `/core/v1/runner`

<!-- BEGIN GENERATED:runner-v1 -->
계약 문서: **MBNAS Runner Protocol** 버전 `1.1.0` · 경로 11개 · 연산 11개 · 인증: `Authorization: Bearer <runner 토큰>`, 작업 범위 요청은 job ticket·attempt 동반

| # | 메서드 | 경로 | 기능 | 요청 본문 | 성공 응답 | 오류 응답(HTTP) |
|---:|---|---|---|---|---|---|
| 1 | POST | `/core/v1/runner/register` | runner 등록·갱신과 호환성 협상(계약·envelope 버전, 주기, 허용 작업 종류) | `RegisterRequest` | 200 `RegisterResponse` | 401, 403, 409 |
| 2 | POST | `/core/v1/runner/claim` | 다음 작업 lease와 JobEnvelope 수령 | `ClaimRequest` | 200 `ClaimResponse`, 204 | 400, 401, 403, 429 |
| 3 | POST | `/core/v1/runner/jobs/{job_id}/start` | attempt 시작(과금 시작점) | `StartRequest` | 200 `StartResponse` | 401, 403, 409 |
| 4 | POST | `/core/v1/runner/jobs/{job_id}/heartbeat` | lease 연장과 제어 플래그(pause·cancel) 수신 | `HeartbeatRequest` | 200 `HeartbeatResponse` | 401, 403, 409 |
| 5 | POST | `/core/v1/runner/jobs/{job_id}/progress` | 진행 보고(배치) | `ProgressBatch` | 200 `ProgressResponse` | 401, 403, 409, 422 |
| 6 | POST | `/core/v1/runner/jobs/{job_id}/checkpoints` | 검증된 checkpoint 등록 | `CheckpointReport` | 200 `CheckpointResponse` | 401, 403, 409, 422 |
| 7 | POST | `/core/v1/runner/jobs/{job_id}/artifacts` | 평가 artifact 등록(배치) | `ArtifactReport` | 200 `ArtifactResponse` | 401, 403, 409, 422 |
| 8 | POST | `/core/v1/runner/jobs/{job_id}/result` | 작업 결과 commit(멱등) | `ResultCommit` | 200 `ResultResponse`, 202 `ErrorBody` | 401, 403, 409, 500 |
| 9 | POST | `/core/v1/runner/jobs/{job_id}/ack` | 결과 응답 수신 확인 | `AckRequest` | 204 | 401, 403, 404 |
| 10 | POST | `/core/v1/runner/deregister` | 정상 종료 등록 해제 | `DeregisterRequest` | 204 | 401, 403 |
| 11 | POST | `/core/v1/runner/checkpoints/{checkpoint_id}/pruned` | 보존 정책 삭제 지시의 처리 결과 보고(core가 checkpoint를 PRUNED로 표시) | `PruneReport` | 200 `PruneAck` | 401, 403, 404, 409 |

출처: `mbnas_contracts/src/mbnas_contracts/openapi/openapi_runner.json` (sha256 `0f3dac4db29e36485f04941028f296f9fdaf6d00fca44fb304d410474084ecd7`)
<!-- END GENERATED:runner-v1 -->

정상 흐름: `register` → `claim`(JobEnvelope 수령) → GPU 대기 후 `start`(과금 시작) → 주기적 `heartbeat`(lease 연장, pause·cancel 제어 플래그, 보존 정책 삭제 지시) → `progress` → `checkpoints`/`artifacts` 등록 → `result`(멱등 commit) → `ack`. 종료 시 `deregister`.

- **허용 작업 종류**: BASELINE_TRAIN, CANDIDATE_TRAIN, FINAL_RETRAIN, EVALUATE, BENCHMARK. 후보 생성(PROPOSE_GENERATION)과 구조 검사는 runner에 보내지 않고 owner 호스트에서만 실행한다.
- **fencing**: 모든 쓰기 요청은 `(job_id, runner_id, attempt)`로 검사한다. lease를 잃은 요청은 `409 LEASE_LOST`.
- **멱등 결과**: 같은 `(job_id, attempt)`의 같은 본문은 저장된 응답을 재생하고, 다른 본문은 `409 RESULT_CONFLICT`, 처리 중이면 `202 PROCESSING`.
- **runner가 계산하지 않는 것**: 적합도(fitness)·승급 게이트. runner는 raw 측정값만 보고하고 core가 판정한다.
- **claim과 장치(GPU·CPU 혼합 fleet)**: CPU로 등록한 runner(`device = cpu`)는 결과에 CUDA 측정이 필요한 작업(평가 ON 탐색·최종 재학습 run의 작업, 평가하는 명시 학습, BENCHMARK)을, 같은 작업 종류를 받는 CPU가 아닌 runner가 `ONLINE`일 때 받지 않는다. 그 작업은 임대되지 않고(시도 횟수 유지) `QUEUED`로 남으며, CPU runner는 다른 작업을 받거나 `204`(작업 없음)를 받는다. CPU runner만 있으면 CPU runner가 그 작업을 받는다. 이 배정 규칙은 응답 형식·오류 코드에 영향을 주지 않는다.
- **평가하지 못한 결과**: CPU runner의 평가 단계는 지연시간을 재지 않고 결과에 `NOT_EVALUATED`(사유 `EVALUATION_REQUIRES_CUDA`)를 선언하며 작업은 성공한다. core는 그 단계의 행을 지표 없이 `SEARCH_COMPLETE` + `performance_status = NOT_EVALUATED`로 기록한다(가짜 지표·승급 없음, checkpoint 유지).
- **JobEnvelope 내용**: 사용자가 작성한 resolved protocol, 작업 명세, 후보의 공개 행. 탐색 정책 내부 상태(prior·repair·부모 풀·예산 추정)는 넣지 않는다. runner 경로의 응답에도 owner 전용 정보(정책 파라미터·계보·감사 정보)는 들어가지 않는다.
- **보존 정책 삭제 왕복(1.1.0)**: heartbeat 응답의 `control.prune`이 삭제 지시(checkpoint id, run 기준 상대경로, sha256, 크기, 정책, 사유)를 같은 lease의 같은 작업·시도에 한 번 전달한다. runner는 자기 작업 디렉터리의 그 run 아래 일반 파일만 sha256·크기가 지시와 같을 때 지우고(심볼릭 링크·저장소 안은 지우지 않음), `POST /core/v1/runner/checkpoints/{checkpoint_id}/pruned`(runner 토큰, job ticket 없음)로 보고한다. 같은 보고는 저장된 응답을 재생(`replayed`)하고, 맞지 않는 보고는 `404 PRUNE_REQUEST_NOT_FOUND`·`409 PRUNE_REPORT_CONFLICT`·`403`·`422`로 거부한다. 후보의 최신 full-state checkpoint와 평가 행이 참조하는 checkpoint는 지시 대상이 아니며, gateway 저장소의 사본은 남는다.
- **runner가 보내는 경로**: checkpoint 등록의 `uri`와 결과의 출력 경로에는 runner 호스트의 절대경로가 들어갈 수 있다. core는 이를 DB에 owner 전용 값으로 저장하고 공개 응답에는 투영하지 않는다.

## 6. ④ owner 관리 API `/core/v1/owner`

| 메서드 | 경로 | 기능 |
|---|---|---|
| GET | `/core/v1/owner/runners` | 등록된 runner 목록 |
| GET | `/core/v1/owner/principals` | 자격 주체 목록(토큰 값은 반환하지 않음) |
| GET | `/core/v1/owner/runs/{run_id}/candidates/{candidate_id}/audit` | 후보의 원본 감사 정보(투영 전) |
| GET | `/core/v1/owner/registry` | registry 원본(건너뛴 registry 루트 진단 포함) |
| GET | `/core/v1/owner/runs/{run_id}/export` | owner 프로필 내보내기(투영 전) |

인증은 admin scope 검사뿐이며 core는 요청한 주소를 검사하지 않는다. owner 호스트 안에서만 쓰고, core를 원격 gateway·runner에 열 때는 앞단 proxy가 `/core/v1/owner/`를 차단한다(`deploy/public/nginx.example.conf` 끝의 owner 호스트용 예시: 콘솔·runner 경로만 전달).

토큰 발급·회전·폐기는 HTTP가 아니라 owner 호스트의 CLI로 한다: `mbnas-core-principal create --kind {server,runner,admin} [--runner-id …] [--ttl-days …]`, `rotate <principal_id> [--grace-s …]`, `revoke <principal_id>`, `list`. 토큰은 발급 시 한 번만 출력되고 core에는 hash만 저장된다.

## 7. 버전과 호환

| 대상 | 현재 버전 | 비고 |
|---|---|---|
| 공개 API 계약 | 1.5.0 | 1.4.3 대비 공개 DTO 허용목록·opaque id·업로드·구성 개정 이력·`training_status`·`Error.location` |
| runner 프로토콜 | 1.1.0 (`envelope_version` 1) | 1.0.0에 보존 정책 삭제 지시(`control.prune`)와 삭제 보고 경로를 더함. `register` 응답에서 버전 협상, 비호환 시 `409 VERSION_MISMATCH` |
| 콘솔·owner API | 계약 파일 없음 | ①과 같은 DTO를 사용(콘솔) |

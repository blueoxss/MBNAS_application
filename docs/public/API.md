# API — 공개 사용자 API 요약 (Public API summary)

권위 있는 정의는 계약 파일이다: 공개 API `mbnas_contracts/openapi/openapi.json`(현재 **1.5.0**, sha256 `70e0cffa…`), runner 프로토콜 `mbnas_contracts/openapi/openapi_runner.json`(1.1.0: 1.0.0에 보존 정책 삭제 지시 `control.prune`와 삭제 보고 경로 `POST /core/v1/runner/checkpoints/{checkpoint_id}/pruned`를 더함, `COMPATIBILITY.md` §2). 모든 연산의 표는 계약 파일에서 생성한 등록 문서(registration 배포본의 `docs/registration/INTERFACES.md`)에도 있다. 1.4.3 대비 바뀐 점은 §8에 있다.

## 1. 기본 규칙 (Basics)

| 항목 | 내용 |
|---|---|
| 기본 주소 | `http://127.0.0.1:8710/api/v1`(gateway 기본값). 외부 노출은 TLS reverse proxy 뒤에서 `https://` |
| 인증 | 모든 `/api/v1` 경로: `Authorization: Bearer <MBNAS_GW_API_TOKEN>`. 없으면 `401 AUTH_REQUIRED`, 틀리면 `403 ACCESS_DENIED`. artifact 다운로드는 Bearer 또는 짧은 수명의 서명 URL |
| 요청 추적 | `X-Request-ID`(선택). 응답과 오류 본문의 `request_id`로 돌아오며 gateway가 core 호출에 전달한다 |
| 오류 본문 | `{"code": "...", "message": "...", "errors": [...], "request_id": "...", "location": "..."}` — `code`는 안정적인 대문자 코드. gateway가 만든 오류에는 `location`(예: `gateway`, `core`, `store`)이 붙는다 |
| 본문 한도 | 1 MiB(초과 시 `413`), 구성 YAML 업로드 1 MiB |
| 요청률 | 토큰별 10 rps(burst 50), 고비용 경로(검증·구조 검사·학습 생성·내보내기) 동시 1개·분당 30회, SSE 4개. 초과 시 `429 RATE_LIMITED` + `Retry-After`(기본값, gateway 설정으로 변경) |
| core 부재 | `503 CORE_UNAVAILABLE`(+ `Retry-After`). 대체 동작 없음. `GET /health`는 200으로 응답하되 `status: degraded`, `core: unreachable` |
| 경로 위생 | 경로에 `.`·`..` 세그먼트(퍼센트 인코딩 포함)가 있으면 라우팅 전에 `404 NOT_FOUND`(`location: gateway`). 경로 인자는 한 세그먼트로 인코딩되어 core에 전달된다 |

## 2. 연산 그룹 (Operation groups)

| 그룹 | 경로 | 요점 |
|---|---|---|
| 상태 | `GET /health`, `GET /registry`, `GET /protocols/presets` | gateway 상태 + core 요약, baseline registry와 capability, 서버 preset. health의 `allowlists`는 gateway 자신의 허용 경로(비어 있을 수 있음)와 아카이브 루트만이며 core 호스트의 경로는 나오지 않는다 |
| 프로토콜 | `POST /protocols/validate` | 본문 `{protocol}`. 응답 `valid`, `errors`, `protocol_hash`, `resolved`, `resolved_diff`, `validation_id`. gateway가 고객 호스트에서 경로·데이터셋을 검사해 core로 보낸다 |
| 탐색 run | `POST /runs`, `GET /runs`, `GET /runs/{run_id}`, `GET /runs/{run_id}/snapshot` | 생성 본문 `{protocol, protocol_hash, idempotency_key, validation_id?}` + 헤더 `Idempotency-Key`. 검증 뒤 프로토콜이 바뀌었으면 `409 STALE_VALIDATION` |
| 실행 제어 | `POST /runs/{run_id}/pause`, `…/resume`, `…/cancel`, `…/final-retrain` | 일시정지는 안전 경계 도달 후 `PAUSED`. 반복 요청은 no-op |
| 이벤트 | `GET /runs/{run_id}/events` | SSE. 이벤트 id = `seq`. 재연결 시 `Last-Event-ID` 헤더 또는 `after_seq` 쿼리 |
| 후보 | `GET /runs/{run_id}/candidates/{candidate_id}`, `POST …/structure-check` | 계보·블록·평가·checkpoint·비용·게이트. 구조 재검사는 CPU, 학습·평가 없음. 학습했지만 평가하지 못한 행(CPU runner)은 `status: SEARCH_COMPLETE`, `performance_status: NOT_EVALUATED`, `reason_code`(예: `EVALUATION_REQUIRES_CUDA`), 지표·`fitness`·`gate` null로 나온다(`OPERATIONS.md` §14) |
| 별도 학습 | `POST /training-runs` | 본문 `{source, protocol, mode?, validation_enabled?, checkpoint?, idempotency_key}`. `mode`: scratch·pretrained·evaluate_only·resume |
| 구조 | `GET /registry/{model_id}/structure`, `POST /registry/{model_id}/structure-check`, `POST /structure-check`, `GET /structure/block-catalog` | 사용자 YAML 검사 결과는 저장하지 않는다 |
| 산출물 | `GET /runs/{run_id}/artifacts/{artifact_id}`, `POST …/refresh`, `GET /runs/{run_id}/export` | id로만 조회(파일 경로를 받지 않음). checkpoint도 같은 경로에서 id로 받는다. 응답의 artifact URL은 상대경로(`/api/v1/...`). gateway가 고객 측 저장소에서 sha256을 대조해 제공(`X-Artifact-Sha256`). core가 core 호스트에 만든 생성 구조 YAML(분리 배치의 평가 없는 생성)은 gateway 저장소에 없으므로 gateway가 core에서 받아 sha256을 다시 대조해 전달한다(`X-Artifact-Verification: core`, core 연결 필요). 만료된 서명 URL은 `410` |
| 구성 개정 | `GET /runs/{run_id}/candidates/{candidate_id}/config-revisions`, `GET …/config-revisions/{revision_id}/yaml` | 후보 구성의 개정 이력(원본·선택본·실행본·생성본; `ModelConfigRevision`)과 각 개정의 저장 바이트(`application/x-yaml`). 선택본↔실행본 diff와 다운로드에 쓴다 |
| 아카이브 | `GET /archive/*`(13종) | 고객 호스트의 연구 run 결과 읽기 전용. 루트 미설정 시 `503 ARCHIVE_ROOT_UNAVAILABLE` |
| 업로드 | `POST /uploads/model-config` | 응답 `UploadResponse` — §4 |

## 3. 예: 연결 확인과 run 생성

아래 예는 `curl`과 `jq`를 쓴다.

```bash
export MBNAS_URL=http://127.0.0.1:8710/api/v1
export MBNAS_TOKEN='<콘솔 토큰>'          # 설치 시 생성된 MBNAS_GW_API_TOKEN 값. 화면·로그에 남기지 않는다
AUTH=(-H "Authorization: Bearer ${MBNAS_TOKEN}")

curl -sS "${AUTH[@]}" "$MBNAS_URL/health"                 # status, core 연결 상태
curl -sS "${AUTH[@]}" "$MBNAS_URL/protocols/presets" > presets.json

# protocol.json = 편집한 프로토콜(예: config/examples/protocol.pilot_smoke.example.json의 /ABSOLUTE/PATH/TO/... 를 실제 경로로)
jq '{protocol: .}' protocol.json > validate.json
curl -sS "${AUTH[@]}" -H 'Content-Type: application/json' -d @validate.json "$MBNAS_URL/protocols/validate" > validated.json

# validated.json의 protocol_hash·validation_id로 run 생성 (Idempotency-Key = 본문 idempotency_key)
KEY=$(python3 -c 'import uuid; print(uuid.uuid4())')
jq --arg k "$KEY" --slurpfile p protocol.json \
   '{protocol: $p[0], protocol_hash: .protocol_hash, validation_id: .validation_id, idempotency_key: $k}' \
   validated.json > run.json
curl -sS "${AUTH[@]}" -H 'Content-Type: application/json' -H "Idempotency-Key: $KEY" -d @run.json "$MBNAS_URL/runs"

curl -sS -N "${AUTH[@]}" "$MBNAS_URL/runs/<run_id>/events"   # SSE: 끊기면 -H "Last-Event-ID: <seq>" 로 이어서
```

위 예는 계약의 요청 형식을 보여 준다.

## 4. 구성 YAML 업로드와 구조 검사 (Upload and structure check)

```bash
curl -sS "${AUTH[@]}" -F "file=@my_detector.yaml" "$MBNAS_URL/uploads/model-config"
# -> {"upload_id": "upl-<64 hex>", "source_sha256": "sha256:<64 hex>", "bytes": N}

curl -sS "${AUTH[@]}" -H 'Content-Type: application/json' \
     -d '{"upload_id": "upl-<64 hex>", "input_size": 640}' "$MBNAS_URL/structure-check"
```

- 업로드: `.yaml`/`.yml`, UTF-8, 1 MiB 이하. 원문 바이트는 고객 측 저장소에 sha256 이름으로 보존되고(같은 내용 재업로드는 no-op), 서버 경로는 응답하지 않는다. 오류: `UPLOAD_TYPE_UNSUPPORTED`(422), `PAYLOAD_TOO_LARGE`(413), `UPLOAD_EMPTY`, `UPLOAD_NOT_UTF8`, `UPLOAD_INVALID`(422), 이후 요청의 `UPLOAD_ID_INVALID`(422)·`UPLOAD_NOT_FOUND`(404)·`UPLOAD_CORRUPT`(409), `STORE_UNAVAILABLE`(507).
- `POST /structure-check`와 `POST /training-runs`(`source.kind: "user"`)는 `upload_id` 또는 `model_path`(gateway 호스트의 허용 루트 안 경로)를 받는다. gateway는 core에 경로 대신 YAML 원문과 sha256을 보낸다. `model_path`의 거부 코드: 경로 검사 코드(`PATH_*`), `.yaml`/`.yml`이 아니거나 1 MiB를 넘거나 UTF-8이 아니면 `422 MODEL_CONFIG_INVALID`. 본문 형식 문제는 gateway가 따로 검사하지 않고 core의 `VALIDATION_FAILED`를 그대로 전달한다. 계약 1.5.0은 요청 필드 `upload_id`·`model_yaml_text`·`source_sha256`·`model_name`을 정의한다(`model_yaml_text`·`source_sha256`은 gateway가 core로 보낼 때 채운다).
- 구조 검사 결과 상태는 `PASS`, `FAIL`, `UNVERIFIED`이며 사용자 YAML은 빌드 전에 모듈 허용목록·인자 한도를 먼저 검사한다(`SECURITY_MODEL.md` §5).

## 5. 이벤트 스트림 (SSE)

- 전달은 최소 1회(at-least-once)이며 이벤트 `id`는 run 안의 증가 순번(`seq`)이다. 클라이언트는 `seq`로 중복을 제거한다.
- 재연결: 마지막으로 받은 `seq`를 `Last-Event-ID` 헤더(또는 `after_seq` 쿼리)로 보낸다. 재생 범위를 넘으면 스냅샷을 다시 받으라는 이벤트가 온다.
- gateway는 core 스트림을 중계하며, core 스트림이 끊기면 브라우저 연결을 종료한다(재연결 필요). 일정 시간 조용하면 heartbeat를 보낸다.

## 6. 멱등성 (Idempotency)

`POST /runs`, `POST /training-runs`는 `idempotency_key`(본문)와 `Idempotency-Key`(헤더, `/runs`)로 같은 요청을 한 번만 처리한다. 같은 키에 다른 본문을 보내면 `409 IDEMPOTENCY_CONFLICT`.

## 7. 주요 오류 코드 (Common error codes)

자주 만나는 코드와 대처는 `TROUBLESHOOTING.md`에 있다. HTTP 상태별 의미: 401·403 인증, 404 대상 없음, 409 상태·hash·멱등 충돌, 410 서명 URL 만료, 413 본문 초과, 422 의미 검증 실패, 429 요청률 초과, 502 core 응답 이상·서비스 토큰 거부, 503 core 연결 불가, 507 저장소 쓰기 불가.

## 8. 계약 1.5.0 — 1.4.3 대비 변경

웹 콘솔의 타입은 이 계약에서 생성된다.

**공개 응답 투영(projection).** 투영 대상 component는 `additionalProperties: false`이며, 계약에 없는 필드는 공개 응답·SSE 이벤트·내보내기에서 빠진다(저장된 원본 행은 바뀌지 않는다).

- 제거된 owner 전용 필드: `Generation.group_key`, `Worker.pid`·`lease_expires_at`·`child_pid`, `Block.old_name`·`before`·`after`·`repair_reason`·`params.slot_kind`(블록 출처의 `repair` 값은 접힘), 계보의 교차·변이·수리·prior·선택 기록과 탐색 공간 hash, `raw_metrics`의 허용 밖 키, `Run.budget.policy`, `resolved_protocol`의 `etri_root`, registry 진단 경로 등.
- 작업자·장치 id: 공개 응답의 core 호스트 작업자와 장치는 DB마다 다른 salt로 만든 opaque id(`wrk-…`, `dev-…`)로 나온다. runner 행의 `id`·`runner_id`는 runner 토큰에 묶인 runner id 그대로이며, 기본 runner id는 설치 시 무작위로 만든 `runner-<16진수 8자리>`(`runtime/runner/runner_id`에 보관)라 호스트 이름 같은 호스트 정보를 담지 않는다. `MBNAS_RUNNER_RUNNER_ID`로 직접 정한 id도 그대로 공개되므로 거기에 호스트 정보를 넣지 않는다. `Worker.runner_id`·`device_id`·`runtime_manifest_status`(`verified|mismatch|unknown`)가 추가되고 `device_uuid`는 deprecated다.
- 경로: 응답의 경로는 run 기준 상대경로이고 호스트 절대경로는 나오지 않는다. `resolution.paths.*`는 `{requested}`만 담는다. registry 증거는 `파일명@sha256`. artifact URL은 상대 `/api/v1/...`.
- checkpoint: `CandidateDetail.checkpoints[]`는 경로 대신 id(+ `artifact_id`)로 참조하고 artifact 경로로 받는다.
- admin 토큰으로 콘솔 경로를 불러도 투영본이 나온다. 원본 감사 정보는 owner 호스트의 `/core/v1/owner/*`에서만 본다.

**추가.**

- `training_status`(`UNTRAINED|TRAINED`): `CandidateEvaluation`·`CandidateDetail`(선택), `GeneratedCandidate`(필수, 항상 `UNTRAINED`). 웹은 평가 없는 생성 후보를 "UNTRAINED · UNEVALUATED"로 표시한다.
- 업로드: `POST /uploads/model-config` → `UploadResponse{upload_id, source_sha256, bytes}`.
- 구성 개정 이력: `ModelConfigRevision`·`ModelConfigRevisionList`와 §2의 두 조회 경로.
- 요청 필드: `POST /structure-check`와 `TrainingRequest.source`의 `upload_id`·`model_yaml_text`·`source_sha256`·`model_name`.
- 오류: `Error.location`(`gateway|core|runner|store`)과 `Error.core_request_id`. 업로드·gateway 중계·요청률·저장 바이트 오류 코드가 `x-status-vocabulary`에 등록되었다.
- `servers`는 같은 origin의 상대 루트(`/`)다.

**호환성 영향.** 1.4.3의 제거 필드에 의존하던 클라이언트는 영향을 받는다. 또 이 판에서 검증 hash의 정의가 바뀌었으므로(`resolution.paths`가 hash에서 제외됨) 1.4.x 시절의 DB는 쓸 수 없다(`COMPATIBILITY.md` §5).

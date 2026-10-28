# TROUBLESHOOTING — 문제 해결 (Troubleshooting)

먼저 `bash scripts/doctor.sh --profile <owner|public>`를 실행해 PASS/WARN/FAIL 목록을 본다(비밀 값은 출력하지 않는다). 오류 응답의 `code`와 `request_id`가 원인 추적의 기준이다.

## 1. 설치 (Installation)

| 증상 | 원인 | 조치 |
|---|---|---|
| venv 생성 실패, Python 버전 오류 | Python 3.10이 아님 | `bash scripts/install.sh --profile <p> --python /path/to/python3.10` |
| pip가 엉뚱한 index를 찾음 | 호스트의 `pip.conf` | `PIP_CONFIG_FILE=/dev/null`로 실행 |
| 온라인 설치가 내려받기에서 멈춤 | 네트워크 필요(PyPI, `download.pytorch.org`) | 네트워크를 열거나 wheel 묶음으로 `--offline --wheelhouse <dir>` |
| `./wheels/...whl`을 찾지 못함 | requirements 파일의 프로젝트 wheel 경로는 **현재 작업 디렉터리 기준** | 압축을 푼 최상위 디렉터리(requirements 파일과 `wheels/`가 있는 곳)에서 `pip install -r ...` 실행 |
| install.sh가 기존 venv를 거부 | 개발용 editable 설치가 들어 있는 venv | 다른 위치에 설치하거나 그 venv를 직접 정리(스크립트는 지우지 않는다) |
| `OperatorsUnavailable` / 맞춤 연산자 등록 실패 | ultralytics 버전이 8.4.173이 아님(파서 소스 hash 불일치) | 고정 버전으로 재설치(`COMPATIBILITY.md` §3) |
| CPU 전용 호스트에서 CUDA wheel 설치 | 기본 requirements는 cu124 빌드 | `--requirements requirements-cpu.txt`(GPU 없는 호스트용, 평가 완료 판정 없음) |

## 2. 기동·정지 (Start and stop)

| 증상 | 원인 | 조치 |
|---|---|---|
| `start.sh` exit 1 "already running" | 같은 역할이 이미 실행 중 | `bash scripts/stop.sh --profile <p> --role <role>` 후 다시 시작 |
| 포트 사용 중 | 8700/8710/8720을 다른 프로세스가 사용 | 환경 변수로 포트 변경(예: `MBNAS_GW_PORT`, `MBNAS_CORE_PORT`, `MBNAS_WEB_PORT`) — `start.sh --help` 참조 |
| gateway가 바로 종료 | `MBNAS_GW_API_TOKEN` 없음 또는 빈 값 | `config/<profile>/gateway.env` 확인(빈 값 `KEY=`는 거부됨. 주석 처리하거나 값을 채운다) |
| runner가 시작 전에 거부(exit 2), 또는 gateway가 core 연결 시 거부 `CORE_URL_INSECURE` | loopback이 아닌 core에 `http://` | `https://` URL과 필요 시 CA 파일(`MBNAS_RUNNER_CA_FILE`, `MBNAS_GW_CORE_CA_FILE`). gateway만 `MBNAS_GW_CORE_ALLOW_INSECURE_HTTP=true`로 예외 허용 가능(권장하지 않음) |
| runner `CORE_URL_INVALID` | URL 형식 오류 | `MBNAS_RUNNER_CORE_URL=https://host[:port]` |
| public 프로필에서 runner가 뜨지 않음 | core URL이 응답하지 않음(start.sh가 먼저 확인) | core 운영자에게 주소·방화벽 확인 |
| `stop.sh`가 아무것도 멈추지 않음 | pid 파일이 이 트리의 프로세스를 가리키지 않음(재사용된 pid 등) | 정상 동작. 스크립트는 자기 pid 파일의 프로세스만 멈추며 `pkill`을 쓰지 않는다 |
| `stop.sh`가 어떤 pid에 `not signalled`를 출력 | SIGKILL 직전 확인에서 그 pid의 시작 시각이 처음 모았을 때와 다름(그사이 다른 프로세스가 같은 pid를 받음) | 정상 동작. 그 pid에는 신호를 보내지 않았다 |
| `doctor.sh`의 `env:<역할>`이 FAIL(`PYTHONPATH` 또는 `PYTHONHOME`) | 실행 중인 역할 프로세스의 환경에 Python 경로 변수가 있다(`start.sh`를 거치지 않고 띄웠거나 이 변수를 지우기 전의 `start.sh`로 띄움). 그 역할이 `runtime/venv` 밖의 패키지를 불러올 수 있다 | `bash scripts/stop.sh --profile <p> --role <role>` 뒤 `start.sh`로 다시 시작(지금의 `start.sh`는 이 변수들과 `OLDPWD`를 지운다). WARN은 그 프로세스의 환경을 읽을 수 없다는 뜻이다 |
| 브라우저에서 `/`가 `404 WEB_DIST_MISSING` | `mbnas_web/dist`가 없음 | 배포본의 `dist/` 포함 여부 확인 또는 `install.sh --web`(Node 필요) |

## 3. 연결·인증 (Connection and authentication)

| 코드 | HTTP | 뜻 | 조치 |
|---|---|---|---|
| `AUTH_REQUIRED` | 401 | Bearer 토큰 없음 | 콘솔 토큰(`MBNAS_GW_API_TOKEN`)을 입력 |
| `ACCESS_DENIED` | 403 | 토큰 불일치 | 토큰 확인(앞뒤 공백 포함 여부) |
| `CORE_UNAVAILABLE` | 503 | gateway가 core에 연결하지 못함(대체 동작 없음) | core 기동·주소·방화벽·TLS 확인. `GET /api/v1/health`의 `core` 항목 |
| `CORE_AUTH_FAILED` | 502 | core가 gateway의 서비스 토큰을 거부(브라우저 토큰 문제가 아님) | `MBNAS_GW_CORE_TOKEN` 확인. 만료·회전·폐기 여부를 core 운영자에게 문의 |
| `CORE_BAD_RESPONSE` | 502 | core가 예상하지 못한 응답 | core 로그와 `request_id`로 추적 |
| `RATE_LIMITED` | 429 | 요청률·고비용 경로·SSE 연결 수 초과 | `Retry-After`만큼 기다린다. 한도는 `MBNAS_GW_RATE_LIMIT_*`, `MBNAS_GW_EXPENSIVE_*`, `MBNAS_GW_SSE_MAX_PER_TOKEN` |
| `PAYLOAD_TOO_LARGE` | 413 | 본문 1 MiB 초과 | 본문을 줄인다 |

## 4. 프로토콜·run (Protocols and runs)

| 코드 | HTTP | 뜻 | 조치 |
|---|---|---|---|
| `VALIDATION_FAILED` | 422 | 프로토콜 의미 검증 실패 | `errors[]`의 `field`·`code`를 보고 수정 |
| `STALE_VALIDATION` | 409 | 검증 뒤 프로토콜·데이터가 바뀌어 hash가 달라짐 | 다시 검증한 뒤 새 `protocol_hash`로 생성. 1.4.x 시절 DB의 run을 재개할 때도 hash 정의가 달라 맞지 않는다(`COMPATIBILITY.md` §5) |
| `IDEMPOTENCY_CONFLICT` | 409 | 같은 멱등 키에 다른 본문 | 새 키를 쓰거나 원래 본문으로 재전송 |
| `BASELINE_UNAVAILABLE` | 422 | 고른 baseline의 registry 상태가 AVAILABLE이 아님 | `GET /api/v1/registry`의 상태·사유 확인(registry 증거는 core 운영자 관리) |
| `CLASS_MAPPING_MISMATCH` | — (작업 실패 사유·검증 문제) | 공식 가중치 평가 전용인데 checkpoint의 클래스 매핑이 데이터셋과 다름, 또는 split별 manifest의 클래스 매핑이 데이터셋 YAML과 다름 | 데이터셋 클래스 매핑 확인, 또는 사전학습 가중치로 추가 학습(pretrained)으로 변경 |
| `RESUME_REQUIRES_PAUSED_RUN` | 409 | 재개 대상 run이 일시정지 상태가 아님 | run 상태 확인 |
| `EXTERNAL_CHECKPOINT_IMPORT_UNSUPPORTED` | 409 | 외부 checkpoint를 새 run에 붙이는 재개 | 지원하지 않음(`COMPATIBILITY.md` §6) |
| `FINAL_RETRAIN_NOT_APPLICABLE` | 409 | 평가 없는 생성 run에 최종 재학습 요청 | 평가를 수행한 탐색 run에만 가능 |
| `EVALUATION_REQUIRES_CUDA` | — (검증 오류 `errors[].code`) | 검증용 장치 목록이 CPU뿐인데(또는 `console.benchmark.device`가 `cpu`) 평가 ON 탐색을 요청함. 지연시간은 CUDA에서만 잰다. 후보 행의 `reason_code`로 나오면 뜻이 다르다(§7) | GPU runner를 등록하거나(GPU runner가 다른 호스트면 `MBNAS_CORE_DEVICE_SOURCE=runners`), 평가 OFF 생성(`console.search.evaluation_enabled=false`) 또는 명시 학습을 쓴다 |
| `DEVICE_UNAVAILABLE` | — (검증 오류 `errors[].code`) | 검증용 장치 목록이 비어 있음. 기본 `MBNAS_CORE_DEVICE_SOURCE=local`에서는 core 호스트에 GPU가 없고 등록된 CPU runner도 없을 때(`local`은 GPU 없는 core 호스트에서 등록된 CPU runner의 `cpu` 항목으로 대신하지만 GPU runner는 보지 않는다), `runners`에서는 등록된 runner가 없을 때 | runner를 실행해 둔 채(`mbnas-runner run`; 정지했거나 응답이 끊긴 runner는 목록에서 빠진다) 다시 검증한다(장치 목록은 최대 30초 캐시). GPU runner가 다른 호스트에 있으면 owner 쪽에서 `MBNAS_CORE_DEVICE_SOURCE=runners`(`OPERATIONS.md` §14) |
| `WORKER_IO_ERROR` / `WORKER_JOB_ERROR` | — (owner worker 작업 실패 사유) | 후보 생성 작업 중 core 호스트의 파일 오류 / 그 밖의 예외. worker는 계속 돌고 그 작업만 실패 | core 쪽 작업 로그(`<MBNAS_CORE_OUTPUT_ROOT>/<run>/logs` 또는 run 디렉터리의 `logs`) 확인 |
| `RESUME_BLOCKED` | — (작업 실패 사유) | full-state가 없거나 손상·불일치 | 가중치만으로 대체하지 않는다. checkpoint 상태(`PRESENT/PRUNED/MISSING`) 확인 |

## 5. 구조 검사 (Structure checks)

| 코드 | 뜻 | 조치 |
|---|---|---|
| `BLOCK_UNSUPPORTED` | 허용목록 밖 모듈 | 허용 모듈만 사용(`SECURITY_MODEL.md` §5) |
| `ARG_SCHEMA_INVALID` | 인자 형식·크기 한도 위반(행·repeats·채널·정수·`nc`·alias·크기 등) | 한도 안으로 수정 |
| `ACTIVATION_REFUSED` | 허용되지 않은 activation 식 | 지원되는 생성자(SiLU, ReLU 등)로 변경 |
| `MODEL_CONFIG_INVALID` | YAML로 해석되지 않음. `model_path`로 준 파일이 `.yaml`/`.yml`이 아니거나 1 MiB를 넘거나 UTF-8이 아님 | 문법·파일 확인(업로드는 `UPLOAD_*` 코드, §6) |
| `INPUT_SIZE_INVALID` | `input_size`가 32의 배수가 아니거나 상한 초과 | 32의 배수, 상한 이하 |
| `STRUCTURE_INVALID` | 구조 검사 FAIL 상태로 학습 요청 | 구조를 고친 뒤 다시 검사 |
| `STRUCTURE_CHECK_BUSY` (429) | 다른 구조 검사가 실행 중(동시 1개) | 잠시 후 재시도 |
| `STRUCTURE_CHECK_TIMEOUT` | 시간 제한 초과 → `UNVERIFIED` | 모델 크기·입력 크기 축소 |
| `STRUCTURE_CHECK_RESOURCE_LIMIT` | CPU·메모리 한도 초과 → `UNVERIFIED` | 모델 크기·입력 크기 축소 |
| `STRUCTURE_RUNTIME_UNAVAILABLE` | 구조 런타임을 실행할 수 없음 → `UNVERIFIED`(PASS로 바뀌지 않음) | core 운영자에게 런타임 상태 확인 요청 |
| `REGISTER_HOOK_REFUSED` | 허용되지 않은 `register_hook` 문자열 | `COMPATIBILITY.md` §4의 두 문자열만 사용 |

## 6. 구성 업로드 (Uploads)

| 코드 | HTTP | 뜻 |
|---|---|---|
| `UPLOAD_TYPE_UNSUPPORTED` | 422 | 파일 이름이 `.yaml`/`.yml`이 아님(원문 본문 업로드는 `X-Filename` 헤더 또는 `?filename=` 필요) |
| `UPLOAD_EMPTY`, `UPLOAD_NOT_UTF8`, `UPLOAD_INVALID` | 422 | 빈 파일, UTF-8 아님, multipart 해석 실패 |
| `UPLOAD_ID_INVALID` / `UPLOAD_NOT_FOUND` | 422 / 404 | `upl-<64 hex>` 형식이 아니거나 없는 업로드 |
| `UPLOAD_CORRUPT` | 409 | 저장된 파일이 sha256과 맞지 않음 — 다시 업로드 |
| `STORE_UNAVAILABLE` | 507 | gateway 저장소(`MBNAS_GW_ARTIFACT_STORE`)에 쓰거나 읽을 수 없음 — 권한·디스크 확인 |

## 7. runner (Remote runner)

| 코드 | HTTP | 뜻 | 조치 |
|---|---|---|---|
| `VERSION_MISMATCH` | 409 | runner와 core의 프로토콜·envelope 버전 비호환 | 같은 배포 버전으로 맞춘다 |
| `RUNNER_MISMATCH` | 403 | 토큰에 묶인 runner id와 요청의 runner id가 다름. 기본 runner id는 무작위 `runner-<8 hex>`(`runtime/runner/runner_id`)라서, 예전 기본값 `runner-<hostname>`으로 발급된 토큰을 쓰면 이 오류가 난다 | `MBNAS_RUNNER_RUNNER_ID`를 토큰 발급 때의 `--runner-id`로 설정하거나, 생성된 id(설치 때 출력, `runtime/runner/runner_id`)로 토큰을 다시 발급받는다 |
| `TICKET_INVALID` | 403 | job ticket 없음·만료·다른 lease용 | runner가 작업을 다시 claim한다(자동) |
| `LEASE_LOST` | 409 | lease가 만료·회수됨 | runner는 자식 작업을 멈춘다. core가 다음 시도로 다시 배정 |
| `RESULT_CONFLICT` | 409 | 같은 시도에 다른 결과 본문 | journal 손상 여부 확인 |
| `KIND_NOT_ALLOWED` | 400 | 허용되지 않은 작업 종류 요청 | `MBNAS_RUNNER_KINDS`를 허용 집합 안으로 |
| `RUNNER_BUSY` | 429 | runner 하나가 작업 둘을 동시에 요청 | runner 하나 = 장치 하나 = 작업 하나 |
| 후보가 `SEARCH_COMPLETE`인데 지표가 없음(`performance_status=NOT_EVALUATED`, `reason_code=EVALUATION_REQUIRES_CUDA`, `latency_source=pending`) | — | CPU runner가 학습만 하고 평가하지 못했다(지연시간은 CUDA에서만 잰다). checkpoint는 남고, 지표·적합도는 null이며 승급하지 않는다. 웹 표시는 "학습 완료 · 미평가" | 평가가 필요하면 GPU runner로 실행한다. GPU·CPU 혼합 fleet에서는 GPU runner가 `ONLINE`일 때만 CPU runner가 이런 작업을 건너뛴다(`OPERATIONS.md` §14) |
| CPU runner가 대기 중인 작업을 가져가지 않음(claim이 계속 `204`) | 204 | 그 작업은 CUDA 측정이 필요하고(평가 ON 탐색·최종 재학습, 평가하는 명시 학습, BENCHMARK), 같은 작업 종류를 받는 GPU runner가 `ONLINE`이다. 작업은 시도 횟수를 쓰지 않고 `QUEUED`로 GPU runner를 기다린다 | 정상 동작. 오래 기다리면 GPU runner의 상태와 `MBNAS_RUNNER_KINDS`를 확인한다 |

## 8. 산출물 다운로드 (Artifact downloads)

| 코드 | HTTP | 뜻 | 조치 |
|---|---|---|---|
| `ARTIFACT_NOT_READY` | 404 | runner가 등록한 산출물이 아직 gateway 저장소로 전송되지 않음 | runner의 로컬 전송 설정(`MBNAS_RUNNER_ARTIFACT_TRANSFER=local`, `MBNAS_RUNNER_ARTIFACT_STORE_DIR` = gateway 저장소, 같은 호스트) 확인 |
| `MISSING` | 404 | 이 run에 없는 id, 또는 core 쪽 파일(분리 배치의 생성 구조 YAML)이 core 호스트에서 사라짐 | id 확인. core 쪽 파일이면 `<MBNAS_CORE_OUTPUT_ROOT>/<run id>/architectures/`를 백업에서 복원(`OPERATIONS.md` §8·§14) |
| `ACCESS_DENIED` | 403 | 서명 URL의 서명이 맞지 않음(토큰 문제는 §3), 또는 core 쪽 생성 구조 YAML이 심볼릭 링크이거나 허용 위치 밖, 또는 `MBNAS_CORE_OUTPUT_ROOT`가 `MBNAS_CORE_OUTPUT_ROOTS` 밖 | 서명 URL을 다시 받는다. core 쪽 파일이면 링크를 실제 파일로 되돌리거나 `MBNAS_CORE_OUTPUT_ROOTS`에 `MBNAS_CORE_OUTPUT_ROOT`를 넣는다(`OPERATIONS.md` §14) |
| `LOAD_FAILED` | 409 | 저장된 바이트가 기록된 sha256과 다름(gateway 저장소 또는 core 쪽 파일; core 쪽 파일은 1 MiB 초과도 포함) | 파일이 바뀌었거나 손상됨. 원본을 복원한다 |
| `EXPIRED` | 410 | 서명 URL 만료 | `POST …/refresh`로 새 URL을 받는다(웹은 한 번 자동으로 요청한다) |
| `CORE_UNAVAILABLE` | 503 | core에 연결하지 못함. 모든 다운로드가 core의 기록을 먼저 확인하고, core 쪽 생성 구조 YAML은 바이트도 core에서 받는다 | core 연결 복구 |
| (오류 아님) checkpoint 행이 보존 정책상 지울 대상인데 `PRESENT`로 남음 | — | 그 runner가 작업 lease를 갖고 있지 않아 삭제 지시(heartbeat `control.prune`)를 아직 받지 못했거나, 저장소 전송이 끝나지 않은 파일이라 runner가 삭제를 미뤘다(`PRUNE_TRANSFER_PENDING`) | 정상 대기. runner가 다음 작업을 받으면 처리된다. `PRUNED`가 된 checkpoint도 gateway 저장소 사본으로 내려받을 수 있다(`OPERATIONS.md` §8) |

## 9. 아카이브 (Archive)

`503 ARCHIVE_ROOT_UNAVAILABLE`: 아카이브 루트(`MBNAS_GW_ARCHIVE_ROOTS`)가 설정되지 않았거나 읽을 수 없다. 아카이브는 선택 기능이며 기본으로 꺼져 있다.

## 10. 로그와 지원 요청 (Logs and support)

- 프로세스 로그: `runtime/logs/<이름>.log`(start.sh가 기록), 설치 로그: `runtime/logs/install-<시각>.log`.
- gateway 감사 로그: `MBNAS_GW_AUDIT_LOG`(기본 `runtime/gateway/logs/audit.jsonl`).
- runner 작업 디렉터리: `MBNAS_RUNNER_WORK_ROOT`(journal, checkpoint, 자식 로그).
- 요청 하나를 두 호스트에서 추적하기: gateway와 core의 접근 로그는 요청마다 한 줄(`method path status request_id duration_ms client`)이고 같은 `request_id`를 쓴다. 오류 본문의 `request_id`로 `grep "request_id=<id>" runtime/logs/gateway.log`(고객 호스트)와 core 로그(owner 호스트)를 찾는다(`OPERATIONS.md` §13).
- 지원을 요청할 때는 `request_id`, `bash scripts/doctor.sh --profile <p> --json -` 출력, 관련 로그 구간을 함께 보낸다. env 파일이나 토큰 값은 보내지 않는다.

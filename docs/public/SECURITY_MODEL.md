# SECURITY_MODEL — 보안 모델 (Security model)

이 문서는 배포 구조가 무엇을 보호하고 무엇을 보호하지 않는지 설명한다.

## 1. 신뢰 영역 (Trust zones)

| 영역 | 실행되는 것 | 누가 통제하나 |
|---|---|---|
| 사용자 브라우저 | 웹 콘솔 번들 | 사용자 |
| 고객 호스트 | gateway(`mbnas_server`), runner(`mbnas_runner`), model runtime, 공통 계약, 고객 측 artifact 저장소, 데이터셋·가중치 | 고객(호스트 root 포함) |
| Owner 호스트 | core(`mbnas_core`): 탐색 정책·구조 게이트 규칙·판정·권위 상태(SQLite)·자격 정보, owner worker | owner(프로그램 제공자) |

core와 공개 구성요소는 HTTP 인터페이스로만 통신한다: 브라우저 → gateway `/api/v1`, gateway → core `/core/v1/console`, runner → core `/core/v1/runner`, owner 관리자 → core `/core/v1/owner`(요약: `API.md`).

## 2. 보호 대상과 공개 대상 (What is protected, what is public)

| 구분 | 내용 | 위치 |
|---|---|---|
| 보호(비공개) | 구조 게이트 규칙과 수리 규칙, 탐색 prior·난수 흐름 키·GA 연산자·부모 선택, 적합도·승급·예산 추정·종료 규칙, 권위 상태(run·후보·평가·작업·비용), 자격 정보(서비스·runner·admin 토큰 hash, ticket 키) | core 프로세스와 core DB |
| 공개(비밀 아님) | gateway·runner·model runtime·맞춤 연산자 코드, 웹 번들, 공개 API·runner 프로토콜 계약 | 공개 배포본 |
| 공개(사용자 소유) | 승인된 모델의 gene·구성 YAML·가중치, 측정값, 구조 검사 결과, 판정 결과 코드, 생성 통계 | 고객 호스트와 공개 응답 |

## 3. 자격 증명 (Credentials)

| 자격 | 가진 쪽 | 용도 | 보관·수명 |
|---|---|---|---|
| 브라우저 토큰 `MBNAS_GW_API_TOKEN` | 사용자 | `/api/v1` 전 경로의 Bearer 인증(상수 시간 비교) | 설치 시 생성, gateway env 파일(0600). 웹은 메모리에만 보관(URL·브라우저 저장소에 저장하지 않음) |
| gateway 서비스 토큰 `MBNAS_GW_CORE_TOKEN` | gateway | core 콘솔 API 호출(principal kind `server`) | core 운영자가 발급, core에는 hash만 저장, 만료·회전 지원. 브라우저에 보내지 않음 |
| runner 토큰 `MBNAS_RUNNER_TOKEN` | runner | runner 프로토콜(principal kind `runner`, 특정 `runner_id`에 결속) | core 운영자가 발급, hash 저장, 만료·회전·폐기 |
| admin 토큰 | owner 관리자 | owner 관리 API `/core/v1/owner/*`(admin scope 필요. 콘솔 경로도 부를 수 있으나 응답은 공개 투영본) | core 운영자가 발급(`mbnas-core-principal create --kind admin`, 기본 수명 1일), core에는 hash만 저장. owner 호스트에서만 쓴다. core는 요청한 주소를 검사하지 않으므로 core를 원격에 열 때는 앞단 proxy가 `/core/v1/owner/`를 차단해야 한다(§4) |
| job ticket | runner(작업 동안) | 작업별 쓰기 요청 결속(작업·시도·단계·runner·만료를 HMAC으로 서명) | claim 응답으로 발급, 새 시도에서 이전 ticket 무효 |
| 다운로드 서명 URL | 브라우저 | artifact 다운로드(짧은 수명) | gateway 서명키(`MBNAS_GW_ARTIFACT_SIGNING_KEY`), 기본 600초 |

core의 키(DB 경로, ticket 키, admin 토큰)는 공개 배포본과 runner·gateway 프로세스 환경에 들어가지 않는다. runner가 학습 작업마다 띄우는 자식 프로세스는 토큰·서명 키 변수와 `MBNAS_*_ENV_FILE` 변수를 물려받지 않는다. 비밀 값은 설치 시 생성되고 화면·로그에 출력하지 않으며, 예시 파일에는 placeholder만 있다.

`scripts/start.sh`는 서비스를 띄우기 전에 호출한 셸의 `PYTHONPATH`·`PYTHONHOME`·`PYTHONSTARTUP`·`PYTHONUSERBASE`·`OLDPWD`를 지우고 `PYTHONNOUSERSITE=1`을 설정한다. 그래서 호출자 환경이 설치된 패키지 대신 다른 코드를 불러오게 하거나 호출자의 작업 디렉터리가 서비스 환경에 남지 않는다. `scripts/doctor.sh`의 `env:<역할>` 검사는 실행 중인 역할 프로세스에 `PYTHONPATH`·`PYTHONHOME`이 있으면 FAIL로 알린다(값은 출력하지 않는다).

## 4. 전송 (Transport)

- runner는 loopback이 아닌 core에 평문 `http://`로 연결하지 않는다(시작 전 `CORE_URL_INSECURE`로 거부). TLS 검증은 끌 수 없고, 사설 CA는 `MBNAS_RUNNER_CA_FILE`로 지정한다.
- gateway도 loopback 밖의 평문 `http://` core URL을 거부한다(core 연결 클라이언트를 열 때 `CORE_URL_INSECURE`). `MBNAS_GW_CORE_ALLOW_INSECURE_HTTP=true`를 명시할 때만 예외로 허용되며, 이때 서비스 토큰이 평문으로 오가므로 권장하지 않는다. core 인증서는 `MBNAS_GW_CORE_TLS_VERIFY`(기본 true)와 `MBNAS_GW_CORE_CA_FILE`로 검증한다.
- 브라우저와 gateway 사이의 TLS는 앞단 reverse proxy(예: nginx)가 맡는다. gateway 기본 수신 주소는 `127.0.0.1`.
- mTLS, 하드웨어 신뢰 실행 환경(TEE), 코드 난독화는 제공하지 않는다.
- core를 다른 호스트의 gateway·runner에 열 때, core 앞 TLS reverse proxy는 `/core/v1/console/`과 `/core/v1/runner/`만 넘기고 `/core/v1/owner/`는 차단한다. core는 owner 경로에서 admin scope만 검사하고 요청한 주소는 검사하지 않기 때문이다. 예시는 `deploy/public/nginx.example.conf` 끝의 owner 호스트용 블록(nginx 1.26용)이다: owner 경로는 `../`·`%2e%2e`·`//` 형태까지 403, 콘솔·runner 경로만 core로 전달, 나머지는 404.

## 5. 입력 경계 (Input hardening)

**사용자 모델 YAML** — 빌드 전에 다음을 검사하고, 어기면 모델을 만들지 않고 거부한다(`BLOCK_UNSUPPORTED`, `ARG_SCHEMA_INVALID`, `ACTIVATION_REFUSED`, `MODEL_CONFIG_INVALID`, `INPUT_SIZE_INVALID`).

| 한도 | 값 |
|---|---|
| 원문 크기 | 1 MiB |
| YAML alias 수 | 64 |
| 행(layer) 수 | 256 |
| repeats | 64 |
| 정수 인자 | 65,536 이하 |
| 채널 수 | 4,096 이하 |
| 클래스 수 `nc` | 1–10,000 |
| 인자 리스트 중첩 깊이 | 2 |
| 문자열 인자 길이 | 64 |
| scale 항목 수 | 16 |
| 입력 크기 `input_size` | 32의 배수, 32 이상, 기본 상한 1280 |

- 모듈은 허용목록(ultralytics 8.4.173 파서가 아는 모듈 + 맞춤 연산자 3종 + `nn.Upsample`, `nn.Identity`)만 쓸 수 있고, torchvision 모델을 만드는 `TorchVision` 모듈은 거부한다. activation 식은 안전한 생성자 몇 가지만 허용한다.
- 빌드를 포함하는 구조 검사는 core 호스트에서 별도 프로세스로 실행한다: 자원 한도(CPU 시간·메모리·파일 수·파일 크기), 시간 제한, 최소 환경 변수(토큰·키 없음, ultralytics 안전 로드·자동 설치 금지·오프라인), 가능하면 네트워크 차단(`unshare -rn`), 동시 실행 1개. 실제로 적용된 격리 수준이 결과에 기록된다. 자원 한도나 시간 제한을 넘은 검사는 `UNVERIFIED`로 끝나며 API 프로세스에는 영향을 주지 않는다.
- 데이터셋 YAML은 안전 파서로 읽고 크기·alias를 제한한다. 업로드는 `.yaml`/`.yml`, UTF-8, 1 MiB까지.

**요청 본문** — 공개 API 1 MiB(초과 시 본문을 읽기 전에 413), runner 경로 32 MiB.

## 6. 응답·로그 노출 (Data exposure)

- **공개 응답 투영**: 계약 1.5.0의 허용목록 투영을 적용한다. 공개 계약에 정의된 필드만 공개 응답·SSE 이벤트·내보내기에 남고, 작업자·장치는 DB별 salt의 opaque id(runner 행은 설치 시 무작위로 만든 runner id `runner-<16진수 8자리>`; 운영자가 직접 정한 runner id는 그대로 공개된다), 경로는 run 기준 상대경로, artifact URL은 상대 `/api/v1/...`로 나온다(`API.md` §8). admin 토큰으로 콘솔 경로를 불러도 투영본이며 원본은 owner 경로에서만 본다. gateway의 `GET /api/v1/health`는 gateway 자신의 허용 경로와 아카이브 루트만 보여 주며 core의 허용 경로는 중계하지 않는다.
- **오류 본문**: 안정 코드·위치·request id 중심이며, 예외 문자열·호스트 경로를 싣지 않도록 설계되었다.
- **로그**: 공통 마스킹 필터가 토큰 형태 값을 가린다. gateway는 민감 조회(후보 상세·계보·세대)를 감사 로그(JSON lines)에 토큰 hash·경로·대상·시각으로 남긴다.
- **다운로드 무결성**: gateway는 64 MiB(`MBNAS_GW_ARTIFACT_VERIFY_FIRST_MAX_BYTES`) 이하 객체를 응답 전에 sha256 대조하고(불일치 `409 LOAD_FAILED`), 더 큰 객체는 전송하면서 대조하므로 변조는 응답 도중 본문 중단으로만 드러난다. 받은 쪽은 `X-Artifact-Sha256`과 대조해야 한다. 웹은 HTTPS나 localhost에서만 내려받은 파일의 sha256을 계산하며, 평문 LAN 접속에서는 "대조 못 함"으로 표시한다. 분리 배치에서 core 호스트에 남는 생성 구조 YAML은 core가 직접 제공한다: core는 run의 core 쪽 디렉터리 안 `architectures/<후보 id>.yaml`이고 심볼릭 링크가 아닌 일반 파일이며 1 MiB 이하이고 바이트가 후보에 기록된 YAML과 같을 때만 등록하고, 내줄 때 같은 조건과 등록 sha256을 다시 확인한다(어긋나면 `403 ACCESS_DENIED` 또는 `409 LOAD_FAILED`). 제공할 때는 core의 `MBNAS_CORE_OUTPUT_ROOT/<run id>` 밖을 보지 않는다. gateway는 받은 바이트의 sha256을 다시 대조한다.
- **브라우저 입력 위조 방지**: 브라우저가 요청 본문에 넣은 고객 호스트 측 검사 결과(`path_checks` 등)는 gateway가 모든 경로에서 지우고 직접 다시 측정한다.
- **웹 번들**: source map을 만들지 않으며, 적합도 공식·설계 handoff 원문·원본 호스트 문자열은 번들에 들어 있지 않다(`npm run check:bundle`이 금지 문자열을 검사한다).

## 7. 남용 방지 (Abuse resistance)

gateway는 토큰별로 요청률(기본 10 rps, burst 50), 고비용 경로(검증·구조 검사·학습 생성·내보내기) 동시 1개·분당 30회, SSE 연결 4개를 제한하고 초과 시 `429 RATE_LIMITED`와 `Retry-After`를 돌려준다(값은 `MBNAS_GW_*`로 조정).

## 8. 위협 모델과 신뢰 한계 (Threat model and trust limits)

고려한 공격자: (가) 고객 호스트의 root·관리자, (나) 호스트 사이 네트워크 공격자, (다) 유효한 토큰을 가진 악의적 사용자, (라) 악성 모델 YAML 작성자, (마) 공개 배포본을 입수한 제3자.

신뢰 한계 — 이 배포가 **보장하지 않는 것**:

1. **공개 구성요소는 비밀이 아니다.** gateway·runner·model runtime·맞춤 연산자 코드, 웹 번들, 승인 모델의 gene·YAML·가중치, 측정 결과는 고객 호스트에서 실행·저장되므로 그 호스트의 root는 모두 읽고 바꿀 수 있다. 이것들의 기밀성은 주장하지 않는다.
2. **고객 측 측정값의 진실성은 증명되지 않는다.** runner가 보고하는 지표·지연시간·장치 정보·실행 환경 정보는 "보고값"이다. core는 형식·fencing·digest를 검사하고 적합도·승급을 직접 계산하지만, 고객 호스트가 조작한 측정값 자체를 진짜라고 증명할 수는 없다. 보고된 실행 환경 정보는 core의 기준과 대조해 `verified | mismatch | unknown` 라벨로 공개한다(대조일 뿐 증명은 아니다). 따라서 고객 측에서 만든 결과의 신뢰도는 그 고객 호스트의 신뢰도를 넘지 않는다.
3. **정책 비공개는 관리 경계를 전제로 한다.** 탐색 정책·구조 게이트 규칙·판정·core DB·관리 키의 보호는 core 호스트가 고객과 다른 관리 주체의 신뢰 영역(다른 호스트, 다른 관리자)에 있을 때만 성립한다. 같은 호스트에서 프로세스만 나눈 배치(owner 일체형 등)는 관리 경계가 아니다.
4. **반복 관찰에 의한 추론은 막지 못한다.** 공개 응답에는 후보 구조, 계보의 공개 부분, 판정 결과 코드, 생성 통계가 나온다. 많은 run을 반복 관찰하면 정책의 경향 일부를 추정할 수 있다. 요청률 제한과 감사 로그는 이를 늦추고 기록할 뿐이다.
5. **seed는 비밀이 아니다.** 탐색·학습 seed는 사용자가 입력하고 응답에 표시되는 공개 값이다. 정책 보호는 seed를 숨기는 데 의존하지 않으며, 같은 seed와 프로토콜로 결과를 재현할 수 있는 것은 의도된 성질이다.
6. **고객 정보 일부는 core로 간다.** core 운영자는 사용자 프로토콜(경로 문자열 포함), gateway가 보내는 데이터셋 요약(split hash, 표본 수, 클래스 이름, 표본 id hash, 경로 검사 결과와 실제 경로), 사용자 구성 YAML 원문, 학습 진행 지표·측정값, runner 장치 정보, runner가 보고하는 checkpoint·결과 파일의 runner 호스트 절대경로(core DB에만 저장되는 owner 전용 값이며 공개 응답에는 투영되지 않는다)를 받는다. 이미지·라벨 파일과 가중치·checkpoint 바이트는 고객 호스트(runner 작업 디렉터리와 gateway 저장소)에 남는다. 고객은 이 정보 흐름을 전제로 core 운영자를 신뢰해야 한다.
7. **판정의 정당성은 공개 결과로만 확인된다.** core가 run의 권위 상태와 판정을 가지므로, 고객은 판정 로직을 직접 볼 수 없다. 확인 수단은 공개된 측정값·판정 코드·계약뿐이다.
8. **core 운영자 자신은 막지 않는다.** owner 호스트의 관리자는 core의 모든 상태와 고객이 보낸 정보를 볼 수 있다.

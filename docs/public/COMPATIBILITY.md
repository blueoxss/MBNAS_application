# COMPATIBILITY — 호환성 (Compatibility)

문서 기준일: 2026-10-28. 이 배포본(패키지 0.1.0)은 원본 단일 패키지(`mbnas_server`)를 5개 패키지로 나눈 것이다. 아래는 원본·과거 기록과의 호환 규칙과 지원 플랫폼이다.

## 1. 지원 플랫폼 (Platform)

| 항목 | 지원 | 비고 |
|---|---|---|
| OS | Linux x86_64 | 검증 플랫폼 |
| Python | 3.10만 | 모든 패키지 `requires-python >=3.10,<3.11` |
| PyTorch | 2.6.0(cu124 빌드), torchvision 0.21.0 | CPU 빌드(`requirements-cpu.txt`)는 CPU smoke용이며 검증 플랫폼이 아니다 |
| GPU | NVIDIA, CUDA 12.4를 지원하는 드라이버 | GPU 경로 검증 완료(2026-10-27): 기준 모델 학습·공식 평가·탐색 run·최종 재학습·일시정지 후 full-state 재개·취소·lease 만료 후 재할당·gateway 다운로드·장치 기록이 GPU에서 통과했다. 한계: TensorRT는 검증 환경에 없어 미검증, 벤치마크는 owner 스크립트로만 실행, 0번이 아닌 GPU에서는 기준 모델 학습과 평가만 확인, 검증 뒤의 소폭 변경은 CPU 시험으로만 확인 |
| ultralytics | 8.4.173 고정 | §3 |
| 웹 | 빌드된 `mbnas_web/dist`를 gateway가 제공 | Node.js는 웹을 다시 빌드할 때만 필요 |

## 2. 계약 버전 (Contract versions)

| 계약 | 버전 | 위치 |
|---|---|---|
| 공개 API(`/api/v1`) | 1.5.0 | 패키지 데이터 `mbnas_contracts/openapi/openapi.json`, 이력 `docs/public/contracts/history/`(1.0.0–1.5.0 계약 사본과 버전 간 migrate 스크립트, `HISTORY.json`) |
| runner 프로토콜(`/core/v1/runner`) | 1.1.0, `envelope_version` 1 | `mbnas_contracts/openapi/openapi_runner.json` |

- 계약 이력은 바이트 단위로 재생 가능하게 보존한다(이전 버전 + migrate 스크립트 → 다음 버전). 1.2.2 이후의 migrate 스크립트는 입력 파일 경로를 인자로 받아 재생된다. 1.0.0→1.2.1 세 스크립트는 원래부터 고정 경로를 읽으므로 다른 호스트에서 재생할 수 없다.
- 일부 migrate 스크립트의 기본·고정 경로 문자열은 원본 호스트의 절대경로였으므로 placeholder(`/ABSOLUTE/PATH/TO/...`)로 바꿨다. 그 밖의 바이트는 바꾸지 않았고 재생 결과는 같다.
- **1.5.0(현재)**: 공개 component에 `additionalProperties: false`, owner 전용 필드 제거(생성 그룹 내부 키, 작업자 pid, 블록 수리 상세, 계보의 탐색 내부 기록, 절대경로 필드), opaque 작업자·장치 id(runner 행은 설치 시 무작위로 만든 runner id `runner-<16진수 8자리>`), 상대 artifact URL, id 기반 checkpoint 참조, 추가 필드(`training_status: UNTRAINED|TRAINED`, 업로드 응답, 구성 개정 이력과 조회 경로, 오류 `location`). 1.4.3의 제거 필드에 의존하던 클라이언트는 영향을 받는다. 자세한 내용은 `API.md` §8.
- **runner 프로토콜 1.1.0**(2026-10-28, 1.0.0에 추가): heartbeat 응답의 `control.prune`이 삭제 지시 목록(`PruneOrder`: checkpoint id, run 기준 상대경로, sha256, 크기, 정책, 사유)이 되었고(같은 lease의 같은 작업·시도에는 한 번만 전달), runner가 삭제 결과를 알리는 E11 `POST /core/v1/runner/checkpoints/{checkpoint_id}/pruned`(`PruneReport`)가 생겼다. 같은 보고를 다시 보내면 저장된 응답을 그대로 돌려주고, 맞지 않는 보고는 `404`·`403`·`409`·`422`로 거부한다. 공개 API 계약 1.5.0은 바뀌지 않았다(`openapi.json` sha256 `70e0cffa…` 그대로).
- runner와 core의 버전 협상은 `register` 응답으로 하며, 맞지 않으면 `409 VERSION_MISMATCH`.

## 3. ultralytics 연산자 확장 (Operator extension)

- 맞춤 연산자 3종(`NASPatchMerge`, `NASWindowTransformer`, `NASGlobalTransformer`)은 `mbnas_model_runtime.operators`에 있다. ultralytics 파서 확장은 `exec` 없이, 설치된 ultralytics 8.4.173의 `parse_model` 소스 sha256을 고정값과 대조한 뒤 정적 복제본을 설치하는 방식이다.
- 설치된 ultralytics가 다른 버전이면(소스 hash 불일치) 확장은 **명시적으로 실패**한다(`OperatorsUnavailable`). 조용한 대체 동작은 없다. ultralytics 버전을 올리려면 정적 복제본과 그 검증 시험을 함께 갱신해야 한다.

## 4. 구성 등록 훅 문자열 (register_hook shim)

작업 명세의 `register_hook`(모델 빌드 전에 실행할 연산자 등록 함수)은 허용목록의 정확한 문자열 두 개만 받는다.

| 문자열 | 실행 대상 | 비고 |
|---|---|---|
| `mbnas_model_runtime.operators:ensure_registered` | 같은 함수 | 현재 문자열 |
| `mbnas_server.search.etri_bridge:ensure_mixed_registered` | `mbnas_model_runtime.operators:ensure_registered` | 분할 전 run에 저장된 옛 문자열. import하지 않고 새 대상에 명시적으로 대응시킨다 |

그 밖의 문자열은 `REGISTER_HOOK_REFUSED`로 거부한다. 옛 문자열 대응(위 표 둘째 행)은 공개 배포본 호환을 위한 임시 대응이며, 계약 1.6에서 유지 여부를 다시 검토한다(그때 없앨 수 있다).

## 5. 탐색 공간 정체성 hash (Search-space identity)

- 탐색 공간의 정체성 hash(`space_hash`, `prior_hash`)는 정의 `mbnas-mixed-space/3`을 쓴다: 공간 기술의 정규 sha256과, 실제 실행되는 vendored 맞춤 연산자·시드 변형 모듈 파일의 설치 바이트 sha256을 포함한다.
- 원본 프로그램은 `mbnas-mixed-space/2`(외부 연구 코드 checkout의 실행 모듈 hash)를 썼다. **/2로 기록된 원본 run과 /3으로 기록된 이 배포본의 run은 hash가 같을 수 없으므로 hash로 직접 비교하지 않는다.** 두 정의 사이의 대응 기록은 owner 쪽 provenance에 있다.
- 새 배포는 `runtime/` 아래 새 DB로 시작한다. 원본 프로그램 DB의 이관 도구는 없다. 0.1.0의 core DB 스키마 버전은 6이다(보존 정책의 삭제 요청을 담는 `prune_requests` 테이블을 더한 가산 변경).
- **1.4.x 시절 DB 비호환**: 0.1.0(계약 1.5.0)은 검증 hash에서 `resolution.paths`를 제외하고 실제 경로·`etri_root`를 기록하지 않는다. 그래서 같은 preset을 검증한 hash가 이전과 다르고, 1.5.0 이전에 만든 DB의 run은 재개·runner 검사에서 hash가 맞지 않는다. 0.1.0은 마이그레이션을 제공하지 않으므로 그런 DB는 쓰지 않는다(새 DB로 시작).

## 6. 과거 체크포인트와 재개 (Checkpoints and resume)

- 원본 연구 코드의 모듈 경로(`yolo_ga_runtime.mixed_blocks`)로 pickle된 ultralytics `.pt`는 클래스 위치가 바뀌어 **읽을 수 없다(미지원)**.
- full-state 재개는 같은 run의 저장 프로토콜과 최신 full-state checkpoint를 이어 가는 방식이다. 외부 checkpoint를 새 run에 붙여 full-state로 이어 가는 기능은 없다.
- RF-DETR native 재개는 `torch.load(weights_only=False)`가 필요해 공개 runner에서 기본으로 꺼져 있다(`MBNAS_RUNNER_ALLOW_PICKLE_RESUME=false`; 켜도 digest는 대조). 꺼진 상태에서는 RF-DETR full-state 재개가 불가능하다. owner 프로필은 켠다.
- 보존 정책의 원격 삭제 왕복(prune)은 0.1.0에 들어 있다(runner 프로토콜 1.1.0, `OPERATIONS.md`의 보존 정책 절). 다른 runner로 checkpoint를 옮겨 재개하는 기능(runner 사이 전송·작업 배정 시 checkpoint 위치 고려)은 0.1.0 범위가 아니다.

## 7. 실행 모델 변화 (Runtime changes from the original)

| 원본 | 이 배포본 |
|---|---|
| API 프로세스 하나가 DB·탐색·학습 작업자를 모두 가짐 | core(정책·상태) / gateway(사용자 API) / runner(학습·평가) / owner worker(후보 생성) |
| 작업자가 DB를 직접 읽음 | runner는 HTTP로 작업을 임대받고 JobEnvelope만 받음(DB 접근 없음) |
| 학습·평가 핸들러가 적합도·게이트까지 계산 | runner는 측정값만 보고, core가 계산 |
| 단일 Bearer 토큰 | 역할별 자격(브라우저·gateway·runner·admin) + 작업별 ticket |
| 웹을 Vite 서버로 제공 | gateway가 빌드된 `dist/`를 정적 제공(Vite preview는 개발용, 포트 8720) |
| 기본 포트 8000 / 5173 | core 8700 / gateway 8710 / 웹 preview 8720 |
| 설정 접두사 `MBNAS_*` 하나 | `MBNAS_CORE_*` / `MBNAS_GW_*` / `MBNAS_RUNNER_*`, 공통 루트 `MBNAS_HOME` |
| CPU 작업자는 생성 전용 | CPU runner도 학습·checkpoint를 실행(평가 완료 판정은 CUDA 필요) |

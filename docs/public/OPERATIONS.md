# OPERATIONS — 운영 (Operations)

각 스크립트와 명령의 옵션 전체는 `--help`로 볼 수 있다.

## 1. 프로세스와 포트 (Processes and ports)

| 역할 | 명령(콘솔 스크립트) | 기본 주소 | 프로필 | 비고 |
|---|---|---|---|---|
| core API | `mbnas-core-api --env-file <core.env>` | 127.0.0.1:8700 | owner | `/core/v1/console`, `/core/v1/runner`, `/core/v1/owner` |
| owner worker | `mbnas-core-owner-worker --owner --env-file <core.env>` | — | owner | 후보 생성(PROPOSE_GENERATION) 전용, GPU 불필요 |
| gateway | `mbnas-gateway --env-file <gateway.env>` | 127.0.0.1:8710 | owner·public | `/api/v1` + 웹(`/`) + 아카이브 |
| runner | `mbnas-runner run --env-file <runner.env>` | — | owner·public | 학습·평가·벤치마크. GPU 하나에 runner 하나 |
| 웹 preview | Vite preview | 127.0.0.1:8720 | 개발용 | Node.js와 `mbnas_web/node_modules` 필요. 운영에는 gateway가 웹을 제공 |

진단: `mbnas-core-doctor [--strict]`(owner), `mbnas-gateway-doctor`, `mbnas-runner register`(core 연결·버전 협상 확인).

명령행 옵션(각 명령의 `--help` 기준):

| 명령 | 옵션 |
|---|---|
| `mbnas-gateway` | `--env-file <file>`(없으면 환경 변수 또는 `MBNAS_GW_ENV_FILE`), `--host`, `--port` |
| `mbnas-gateway-doctor` | `--env-file <file>` — 설정·core 연결·저장소·웹 dist 점검 |
| `mbnas-runner run` | `--env-file <file>`, `--once`(작업 하나만 claim하고 종료), `--max-jobs <n>`(n개 뒤 종료) |
| `mbnas-runner register` | `--env-file <file>` — core에 한 번 등록하고 협상 결과 출력 |
| `mbnas-runner --spec-file <JobSpec.json>` | core 없이 JobSpec 파일의 작업 하나를 실행(진행은 JSONL로 stdout, 마지막 줄이 결과; exit 0 = 성공, 3 = 그 외) |
| `mbnas-core-api`(owner) | `--env-file <file>`(없으면 `MBNAS_CORE_ENV_FILE`), `--host`, `--port` |
| `mbnas-core-doctor`(owner) | `--env-file <file>`, `--strict`(시작을 막는 문제가 있으면 exit 1). 출력은 JSON, 비밀 없음 |
| `mbnas-core-owner-worker`(owner) | `--owner`(기본: 후보 생성 작업 전부) 또는 `--cpu`(평가 없는 생성 작업만), `--once`, `--max-jobs <n>`, `--poll-s <s>`, `--kill-grace-s <s>`, `--worker-id <id>`, `--env-file <file>`(없으면 `MBNAS_CORE_ENV_FILE`). 운영에 쓰지 않는 옵션: `--kinds <a,b>`(stub 핸들러 전용), `--gpu-index <i>`(`--kinds`와 함께), `--gpu-clear-timeout-s`(현재 사용 안 함) |
| `mbnas-core-principal`(owner) | `--env-file <file>` + `create`·`rotate`·`revoke`·`list` — §4 |

`mbnas-core-api`와 `mbnas-core-owner-worker`는 `--env-file`로 받은 경로를 자기 프로세스의 `MBNAS_CORE_ENV_FILE`로 설정한다. 그래서 그 프로세스 안의 모든 core 설정 읽기(owner worker가 core 쪽 run 디렉터리를 찾는 것 포함, §14)가 같은 파일을 쓴다. 파일 안의 값은 환경 변수로 내보내지 않으며, owner worker의 작업 자식 프로세스는 이 변수를 물려받지 않는다. core API와 owner worker에는 같은 core 설정 파일을 준다.

## 2. 디렉터리 (Runtime layout)

`MBNAS_HOME`(스크립트는 `<ROOT>/runtime`) 아래 기본 위치:

| 경로 | 내용 | 역할 |
|---|---|---|
| `venv/` | 설치 환경 | 공통 |
| `pids/`, `logs/` | start.sh의 pid 파일, 프로세스 로그(`<이름>.log`), 설치 로그 | 공통 |
| `core/db/mbnas.sqlite3` | core 권위 상태(run·후보·평가·작업·비용·자격 hash) | owner |
| `core/runs/`, `core/logs/` | core 쪽 run 디렉터리, core 로그 | owner |
| `gateway/artifacts/` | 고객 측 artifact 저장소(업로드 원문 `uploads/<sha256>.yaml` 등) | 고객 |
| `gateway/manifests/` | 데이터셋 manifest 캐시 | 고객 |
| `gateway/logs/audit.jsonl` | 민감 조회 감사 로그 | 고객 |
| `runner/runs/`, `runner/logs/` | runner 작업 디렉터리(journal, checkpoint, 자식 로그), runner 로그 | 고객 |
| `data/`, `weights/`, `uploads/`, `locks/` | 기본 데이터 허용 루트, 가중치, 업로드, GPU 잠금 | 공통 |

설정 파일은 `config/<profile>/<role>.env`(0600)에 있다.

## 3. 기동·정지 (Start and stop)

운영 스크립트는 Linux 전용이며 bash 4 이상이 필요하다. 어느 작업 디렉터리에서 불러도 스크립트 위치로 ROOT를 찾는다.

```bash
bash scripts/start.sh --profile <owner|public> [--role core|gateway|runner|web|all] [--env-dir <dir>] [--wait-s <seconds>]
bash scripts/stop.sh  --profile <owner|public> [--role core|gateway|runner|web|all] [--timeout-s <seconds>]
bash scripts/doctor.sh --profile <owner|public> [--strict] [--json <file>|-]
```

- `start.sh`: 역할마다 별도 세션(`setsid nohup`)으로 띄우고 작업 디렉터리는 ROOT, `MBNAS_HOME=<ROOT>/runtime`. pid는 `runtime/pids/<이름>.pid`, 로그는 `runtime/logs/<이름>.log`(이어 쓰기). 이미 실행 중인 역할은 거부한다. `--role all`(기본)은 owner: core·owner worker·gateway·runner, public: gateway·runner이며, gateway가 웹 `dist`를 제공하므로 `web`(개발용 Vite preview, 포트 `MBNAS_WEB_PORT` 기본 8720, Node.js와 `mbnas_web/node_modules` 필요)은 포함하지 않는다. `--role core`는 core API와 owner worker를 함께 띄운다. public 프로필은 core를 띄우지 않는다(`--role core` 거부). 준비 판정: core = HTTP 포트 응답, gateway = 콘솔 토큰으로 `GET /api/v1/health` 응답(core 연결 여부 표시), owner worker·runner = 몇 초 뒤에도 살아 있음. runner를 띄우기 전에 core URL이 응답해야 한다. `--wait-s` 기본 60초.
- `start.sh`의 환경 정리: 역할을 띄우기 전에 호출한 셸의 `PYTHONPATH`·`PYTHONHOME`·`PYTHONSTARTUP`·`PYTHONUSERBASE`·`OLDPWD`를 지우고 `PYTHONNOUSERSITE=1`을 설정한다. 호출자 환경이 설치된 패키지 대신 다른 코드를 불러오게 하거나 호출자의 작업 디렉터리가 서비스 환경에 남지 않게 하려는 것이다.
- `stop.sh`: 이 트리의 pid 파일에 있는 프로세스만 멈춘다. 그 pid가 여전히 이 트리의 해당 프로그램일 때만 신호를 보내고(재사용된 pid는 무시하고 pid 파일만 정리), `pkill`/`killall`을 쓰지 않는다. SIGTERM 후 `--timeout-s`(기본 30초) 뒤 SIGKILL. SIGKILL 직전에 대상(역할 프로세스와 그 자손)의 시작 시각을 처음 모을 때의 값과 다시 대조하고, 그사이 다른 프로세스가 같은 pid를 받았으면 신호를 보내지 않는다(`not signalled`로 출력). 멈추는 순서: web, runner, gateway, owner worker, core.
- `doctor.sh`: 인터프리터·패키지 위치·원본 호스트 경로 문자열 부재·콘솔 스크립트·설정 파일(권한 0600, 필수 비밀 설정 여부)·포트·pid·GPU(읽기 전용 조회)·core 연결·디스크·웹 `dist` 점검. 실행 중인 Python 역할(core·owner worker·gateway·runner)마다 `env:<역할>` 검사가 그 프로세스 환경(`/proc/<pid>/environ`, 키 이름만 읽음)에 `PYTHONPATH`나 `PYTHONHOME`이 있으면 FAIL(`--strict`면 종료 코드 1), 환경을 읽을 수 없으면 WARN으로 알린다. 비밀 값은 출력하지 않는다. public 프로필에서 원격 core가 응답하지 않으면 FAIL.
- 종료 코드(공통): 0 성공, 1 거부·실패, 2 사용법 오류.

포트 변경 예(환경 변수가 env 파일보다 우선):

```bash
MBNAS_CORE_PORT=18700 MBNAS_GW_PORT=18710 MBNAS_GW_CORE_URL=http://127.0.0.1:18700 \
MBNAS_RUNNER_CORE_URL=http://127.0.0.1:18700 bash scripts/start.sh --profile owner
```

core·owner worker·gateway는 `CUDA_VISIBLE_DEVICES=''`(CPU)로 실행되고, runner는 호출한 쪽의 `CUDA_VISIBLE_DEVICES`를 물려받는다.

## 4. 자격 관리 (Credentials; owner 호스트)

```bash
mbnas-core-principal --env-file config/owner/core.env create --kind server                       # gateway 서비스 토큰
mbnas-core-principal --env-file config/owner/core.env create --kind runner --runner-id <id>      # runner 토큰
mbnas-core-principal --env-file config/owner/core.env create --kind admin                        # owner 관리 토큰
mbnas-core-principal --env-file config/owner/core.env list
mbnas-core-principal --env-file config/owner/core.env rotate <principal_id> [--grace-s <s>] [--ttl-days <d>]
mbnas-core-principal --env-file config/owner/core.env revoke <principal_id>
```

- 토큰은 발급할 때 한 번만 출력되고 core에는 hash만 저장된다. 기본 유효기간: runner 90일, server 365일, admin 1일(`--ttl-days 0` = 만료 없음).
- `rotate`는 새 토큰을 발급하고 이전 토큰을 유예 시간 뒤 만료시킨다. 고객 쪽 env 파일(`MBNAS_GW_CORE_TOKEN`, `MBNAS_RUNNER_TOKEN`)을 바꾼 뒤 해당 역할을 재시작한다.
- `list`는 토큰 값을 보여 주지 않는다.
- admin 토큰은 owner 관리 API(`/core/v1/owner/*`)와 콘솔 경로에 쓴다. owner 호스트 안에서만 쓰고(예: `http://127.0.0.1:8700`), core를 원격에 열 때는 §5처럼 proxy에서 owner 경로를 막는다.

## 5. 원격 배치 (Remote deployment)

- 고객 호스트의 gateway·runner는 owner 호스트의 core에 연결한다. loopback이 아니면 core 앞에 TLS reverse proxy를 두고 `https://` URL을 쓴다. proxy는 `/core/v1/console/`(gateway)과 `/core/v1/runner/`(runner)만 넘기고 `/core/v1/owner/`는 차단한다. core는 owner 경로에서 admin scope만 검사하고 요청한 주소는 보지 않으므로, 차단하지 않으면 admin 토큰을 가진 쪽이 원격에서 owner 관리 API에 닿을 수 있다. 예시는 `deploy/public/nginx.example.conf` 끝의 owner 호스트용 블록(주석 처리됨, nginx 1.26용)이다: owner 경로는 `../`·`%2e%2e`·`//` 형태까지 403, 그 밖의 경로는 404. runner와 gateway 모두 loopback이 아닌 평문 `http://` core URL을 거부한다(`CORE_URL_INSECURE`: runner는 시작 전, gateway는 core 연결 클라이언트를 열 때). gateway만 `MBNAS_GW_CORE_ALLOW_INSECURE_HTTP=true`를 명시하면 예외로 허용하지만, 서비스 토큰이 평문으로 오가므로 권장하지 않는다. `mbnas-gateway-doctor`도 이 상태를 보고한다.
- 사설 CA: `MBNAS_GW_CORE_CA_FILE`, `MBNAS_RUNNER_CA_FILE`.
- 데이터 경로: gateway가 검증 시 고객 호스트의 경로를 검사하고 데이터셋 manifest를 계산해 core로 보낸다(`MBNAS_GW_DATASET_ROOTS`, `MBNAS_GW_OUTPUT_ROOTS` 필수). runner 호스트의 실제 경로가 프로토콜의 경로와 다르면 `MBNAS_RUNNER_DATASET_MOUNTS`로 대응시킨다.
- 출력 경로: gateway에 데이터 허용 루트(`MBNAS_GW_DATASET_ROOTS`)가 있으면 gateway가 고객 호스트에서 `console.paths`를 검사해 그 결과를 core로 보내므로, 프로토콜의 `console.paths.output_root`는 gateway 쪽 `MBNAS_GW_OUTPUT_ROOTS` 안에 있으면 된다(core 쪽 `MBNAS_CORE_OUTPUT_ROOTS` 밖의 고객 측 경로여도 된다). core 호스트에서 그 경로를 쓸 수 없으면 core는 run 문서 사본(`protocol/*.json`)을 건너뛰고(DB에 남고, runner가 고객 호스트의 run 디렉터리에 사본을 쓴다) owner worker의 파일을 core 쪽 `<MBNAS_CORE_OUTPUT_ROOT>/<run id>/`에 쓴다(§14). gateway에 데이터 허용 루트가 없으면(owner 일체형) core가 자기 호스트에서 `MBNAS_CORE_DATASET_ROOTS`·`MBNAS_CORE_OUTPUT_ROOTS`로 검사한다.
- 브라우저 노출: gateway는 기본으로 127.0.0.1에서만 듣는다. 외부 접근은 TLS reverse proxy로 연다. 웹은 같은 origin(`/`)에서 제공되므로 CORS 설정이 필요 없다(별도 호스팅 시에만 `MBNAS_GW_CORS_ORIGINS`).
- 버전: core·gateway·runner는 같은 배포 버전을 쓴다. runner 프로토콜이 맞지 않으면 `409 VERSION_MISMATCH`.

## 6. GPU와 runner (GPU runners)

- runner 하나 = 장치 하나 = 동시 작업 하나. GPU가 여럿이면 GPU마다 runner id·토큰·env 파일을 따로 두고 `MBNAS_RUNNER_GPU_INDEX`를 다르게 한다.
- 작업 시작 전에 해당 GPU에 다른 프로세스가 없을 때까지 기다린다(`MBNAS_RUNNER_GPU_CLEAR_TIMEOUT_S`). 대기 시간은 과금되지 않고, 과금은 `start` 시점부터다.
- CPU runner: runner 프로세스 환경에 `CUDA_VISIBLE_DEVICES=`(빈 값)을 두면 장치를 `cpu`로 등록하고 학습·checkpoint·구조 작업을 CPU에서 한다. 평가 지연시간은 측정하지 않으므로 평가 완료 판정이 나지 않는다: 평가가 필요한 작업을 맡으면 학습과 checkpoint는 남기고 성능 상태 `NOT_EVALUATED`(사유 `EVALUATION_REQUIRES_CUDA`)로 끝난다. GPU runner와 함께 쓰면 CUDA가 필요한 작업은 GPU runner에 남긴다(§14).
- 허용 작업 종류는 `MBNAS_RUNNER_KINDS`로 좁힐 수 있다(BASELINE_TRAIN, CANDIDATE_TRAIN, FINAL_RETRAIN, EVALUATE, BENCHMARK의 부분집합).
- GPU 번호: runner의 작업 자식 프로세스는 `CUDA_DEVICE_ORDER=PCI_BUS_ID`로 실행되어 CUDA 번호와 `nvidia-smi`(NVML) 번호가 같다. 운영자가 직접 실행하는 GPU 스크립트(§12 등)에도 같은 값을 설정한다.
- 산출물 전송: runner → gateway 저장소는 같은 호스트의 로컬 전송(`MBNAS_RUNNER_ARTIFACT_TRANSFER=local`, `MBNAS_RUNNER_ARTIFACT_STORE_DIR`=gateway 저장소, `MBNAS_RUNNER_ARTIFACT_STORE_MODE`)만 지원한다. 네트워크 업로드(`upload`)는 0.1.0에 구현되지 않았다.
- 벤치마크(BENCHMARK)는 콘솔 작업 큐로 만들어지지 않는다. §12의 owner 스크립트를 쓴다.

## 7. 요청률 제한과 감사 (Rate limits and audit; gateway)

| 설정 | 기본값 | 뜻 |
|---|---|---|
| `MBNAS_GW_RATE_LIMIT_ENABLED` | true | 제한 사용 |
| `MBNAS_GW_RATE_LIMIT_RPS` / `_BURST` | 10 / 50 | 토큰별 초당 요청과 burst |
| `MBNAS_GW_EXPENSIVE_CONCURRENCY` / `_PER_MINUTE` | 1 / 30 | 고비용 경로(검증·구조 검사·학습 생성·내보내기)의 동시 실행과 분당 시작 수 |
| `MBNAS_GW_SSE_MAX_PER_TOKEN` | 4 | 토큰별 SSE 연결 수 |
| `MBNAS_GW_AUDIT_LOG` | `runtime/gateway/logs/audit.jsonl` | 민감 조회 감사 로그 |

## 8. 백업과 보존 (Backup and retention)

- owner 호스트: `runtime/core/db/`(권위 상태 전체)와 `config/owner/`(비밀)을 정기 백업한다. SQLite 파일은 core를 멈춘 뒤 복사하거나 SQLite의 온라인 백업 기능을 쓴다. 분리 배치에서는 `MBNAS_CORE_OUTPUT_ROOT`(기본 `runtime/core/runs/`)도 백업한다. 고객 측 `output_root`를 쓰는 run의 생성 구조 YAML 파일(다운로드되는 원본)과 생성 journal은 여기에만 있다(§14).
- 고객 호스트: `runtime/gateway/artifacts/`(업로드 원문·산출물), `runtime/runner/runs/`(checkpoint), 데이터셋.
- full-state checkpoint 보존 정책: `MBNAS_CORE_FULL_STATE_RETENTION`(`keep_all` | `rung_end_only` | `last_rung_end_only`). 원격 runner에서 실행한 run에도 같은 정책을 적용한다(core 설정 `remote_retention_policy`는 쓰이지 않는다).
  - 왕복: core가 지울 checkpoint마다 삭제 요청을 만들고, 그 runner의 heartbeat 응답 `control.prune`으로 지시(checkpoint id, run 기준 상대경로, sha256, 크기)를 보낸다(같은 lease의 같은 작업·시도에 한 번). runner는 지운 뒤 `POST /core/v1/runner/checkpoints/{checkpoint_id}/pruned`로 보고하고 core는 그 행을 `PRUNED`로 바꾼다. 같은 보고를 다시 보내면 저장된 응답을 그대로 돌려준다.
  - 지우지 않는 것: 후보의 가장 최근 살아 있는 full-state checkpoint(재개 기점)와 평가 행이 참조하는 checkpoint. `CANCELLED`·`RESUME_BLOCKED`·`PAUSED`·`ELIGIBLE_BUDGET_EXHAUSTED` 후보와 취소된 run도 대상에서 빠진다.
  - runner 쪽 조건: `<MBNAS_RUNNER_WORK_ROOT>/runs/<run id>` 안의 일반 파일만, sha256과 크기가 지시와 같을 때만 지운다(심볼릭 링크를 따라가지 않고 artifact 저장소 안은 지우지 않는다). 파일이 이미 없으면 `already_absent`로 보고하고, 조건이 맞지 않으면 거부한다. 저장소로 보내는 전송이 끝나지 않은 파일은 미룬다(`PRUNE_TRANSFER_PENDING`). 보고는 보내기 전에 journal에 적으므로 core가 내려가 있었어도 나중에 다시 보낸다.
  - gateway 저장소의 사본은 0.1.0에서 항상 남는다. 그래서 `PRUNED`가 된 checkpoint도 id로 내려받을 수 있다. 저장소 디스크는 운영자가 관리한다.
  - 한계: 삭제 지시는 그 runner가 작업 lease를 가진 동안의 heartbeat에만 실린다. 다음 작업을 받지 않는 runner의 요청은 대기로 남고 checkpoint 행은 `PRESENT`로 남는다. 삭제 요청 목록을 보는 API는 없다(owner DB의 `prune_requests` 표에만 있다). 다른 runner로 checkpoint를 옮기는 기능은 없다. runner 쪽 checkpoint·결과 파일의 절대경로는 core DB에 owner 전용 값으로 저장되고 공개 응답에는 나오지 않는다.

## 9. 업그레이드 (Upgrade)

1. 모든 역할을 멈춘다(`stop.sh`).
2. 새 배포본을 새 디렉터리에 풀고 설치한다. 기존 `config/<profile>/`와 `runtime/`(특히 owner의 core DB)을 옮기기 전에 백업한다.
3. core → gateway → runner 순으로 시작하고 `doctor.sh`로 확인한다.

DB 스키마 변경은 core가 시작할 때 추가 방식으로 적용한다. 0.1.0의 스키마 버전은 6이다(삭제 요청 표 `prune_requests` 추가). 원본 프로그램의 DB를 옮겨 오는 도구는 제공하지 않는다(`COMPATIBILITY.md` §5).

## 10. 검증 스크립트 (`scripts/verify.sh`)

```bash
bash scripts/verify.sh --profile <owner|public> --suite <suite>[,<suite>...] [--json <file>|-] [--allow-gpu]
```

| suite | 내용 |
|---|---|
| `integration` | owner: `scripts/dev/wp3_three_process_smoke.py` — 실제 core·owner worker·runner·gateway 프로세스를 임시 포트로 띄워 gateway `/api/v1`로 구동(CPU, 약 40초). public: 살아 있는 core가 필요(`MBNAS_CORE_URL`이 없으면 환경 미충족) — gateway doctor로 core 확인, runner 등록 확인, 이 트리의 gateway가 떠 있으면 `/api/v1/health` |
| `relocation` | 설치된 mbnas 패키지의 모든 모듈(하위 모듈 포함, import하지 않고 위치만 찾음)이 ROOT 안에서 로드되는지, 원본 호스트 경로 문자열 부재, 외부 작업 디렉터리·공백 포함 경로에서 doctor 재호출. `relocation.json`에 모듈별 로드 위치(`module_origins`: ROOT·OUTSIDE·MISSING), 패키지별 모듈 수, `sys.path`, venv의 site 디렉터리와 `.pth` 파일의 줄, 호출한 셸에 있던 Python 경로 변수의 이름(값은 기록하지 않음)을 남기고, 모듈 표를 `relocation_origins.tsv`로도 쓴다 |
| `package` | 압축을 푼 배포본(`FILE_MANIFEST.jsonl` 있음)에서 매니페스트의 모든 항목(크기·sha256)을 대조한다. 누출 검사 스캐너는 배포본에 들어 있지 않으므로 `MBNAS_CANARY_SCAN`으로 외부 스캐너를 지정하면 그 검사도 하고, 지정하지 않으면 매니페스트만 검사했다는 사유를 결과에 적는다. 매니페스트가 없는 트리에서는 NOT_APPLICABLE |
| `gpu` | `nvidia-smi` 읽기 전용 조회로 유휴 GPU를 찾고, `--allow-gpu`를 준 경우에만 그 GPU에서 GPU 시험을 실행한다. 이 시험은 시험 묶음에 들어 있으므로 배포본에서는 NOT_APPLICABLE |
| `all` | `unit`·`integration`·`relocation`·`package`·`gpu` 전부. 해당 없는 suite는 NOT_APPLICABLE로 따로 표시된다 |

배포본에는 시험 묶음이 들어 있지 않으므로 `unit`(시험 묶음 실행)은 NOT_APPLICABLE(사유 `test suites are not shipped in this distribution`)로 보고되며 통과·실패 어느 쪽에도 세지 않는다.

`--suite`는 쉼표로 여러 개를 줄 수 있다. 환경 변수: `MBNAS_CORE_URL`(public integration의 core 주소), `MBNAS_CANARY_SCAN`(package의 외부 스캐너 경로). suite마다 결과는 `PASS`, `FAIL`, `NOT_APPLICABLE`(이 트리에 해당 없음), 또는 이 호스트에 필요한 것(venv, core 주소, GPU 등)이 없어 실행하지 못했다는 환경 미충족 상태 중 하나다(상태 이름은 `--help`에 있다). 로그와 결과 파일은 `runtime/logs/verify/<UTC 시각>-<profile>/`에 남는다. 종료 코드: 0 모두 PASS 또는 NOT_APPLICABLE, 1 FAIL 있음, 2 사용법 오류, 3 FAIL 없이 환경 미충족 suite 있음.

## 11. 컨테이너 운영 (Docker)

설치 명령은 `INSTALL.md` §1에 있다. 이 절은 compose 파일(`deploy/public/docker-compose.yml`, `deploy/owner/docker-compose.yml`)이 읽는 변수와 고정 설정이다.

| 변수 | 기본값 | 쓰는 곳 | 뜻 |
|---|---|---|---|
| `MBNAS_UID`, `MBNAS_GID` | 10001 | public·owner | 컨테이너 실행 uid/gid. `runtime/` 쓰기, `data/` 읽기 권한이 있어야 한다(보통 `$(id -u)`, `$(id -g)`) |
| `MBNAS_PUBLIC_IMAGE` | `mbnas-public:0.1.0` | public | 이미지 태그. CPU 호스트는 `mbnas-public-cpu:0.1.0` |
| `MBNAS_PUBLIC_DOCKERFILE` | `deploy/public/Dockerfile` | public | 빌드할 Dockerfile. CPU 호스트는 `deploy/public/Dockerfile.cpu` |
| `MBNAS_NETWORK_MODE` | `bridge` | public | `host`면 호스트 네트워크(같은 호스트의 loopback core에 붙는 시험용) |
| `MBNAS_GW_BIND_HOST` | `0.0.0.0` | public | 컨테이너 안 gateway 수신 주소. host 네트워크에서는 `127.0.0.1` |
| `MBNAS_GW_LISTEN_PORT` | 8710 | public | gateway 수신 포트. host 네트워크에서는 호스트에서 바로 쓰는 포트 |
| `MBNAS_GW_PUBLISH` | `127.0.0.1:8710` | public·owner(all-in-one) | 호스트에 공개할 `주소:포트`. bridge에서만 의미가 있고 host 네트워크에서는 무시된다. 외부 공개는 TLS proxy 뒤에서만 |
| `MBNAS_CORE_PUBLISH` | `127.0.0.1:8700` | owner | core를 공개할 `주소:포트`. 원격 runner·gateway가 쓰려면 TLS proxy 뒤에서 공개 |
| `MBNAS_OWNER_IMAGE` | `mbnas-owner:0.1.0` | owner | owner 이미지 태그(비공개 core 포함: 외부 레지스트리에 올리지 않는다) |
| `MBNAS_RUNNER_GPU` | 0 | public·owner(`gpu` 프로필) | GPU runner가 예약할 GPU 번호. GPU를 더 쓰려면 runner 서비스를 복사해 다른 번호·runner id·토큰·env 파일을 준다 |
| `MBNAS_RUNNER_SHM_SIZE` | GPU runner `8gb`, CPU runner `2gb` | public·owner | runner 컨테이너 공유 메모리(PyTorch 데이터 로더) |

compose가 env 파일보다 우선해 고정하는 값(컨테이너 경로):

- 공통: `MBNAS_HOME=/srv/mbnas/runtime`(호스트 `runtime/`), 데이터셋 `/srv/mbnas/data`(호스트 `data/`, 읽기 전용).
- gateway: 웹 `MBNAS_GW_WEB_DIST=/opt/mbnas/web/dist`, `MBNAS_GW_DATASET_ROOTS=/srv/mbnas/data`, 저장소 `MBNAS_GW_ARTIFACT_STORE=/srv/mbnas/runtime/gateway/artifacts`. 상태 확인(healthcheck)은 `GET /api/v1/health`가 200 또는 401로 답하는지만 본다.
- runner: `MBNAS_RUNNER_DATASET_ROOTS=/srv/mbnas/data`, 산출물 로컬 전송(`MBNAS_RUNNER_ARTIFACT_TRANSFER=local`, 같은 `runtime/` 볼륨의 gateway 저장소, 같은 uid). 정지 유예 90초(실행 중 작업을 멈추고 등록 해제).
- owner: core는 `MBNAS_CORE_DEVICE_SOURCE=runners`(컨테이너 안에 NVML이 없으므로 등록된 runner의 장치 정보를 씀). all-in-one runner는 core 컨테이너의 네트워크를 공유해 `http://127.0.0.1:8700`으로 붙고, owner 프로필이므로 RF-DETR native 재개(pickle)를 켠다.

이미지와 역할:

- 이미지는 uid/gid 10001(`USER`)로 실행되며 진입점의 첫 인자가 역할이다. public 이미지: `gateway`, `runner`(그 밖에 `register`, `doctor`, `version`). owner 이미지: `core`, `owner-worker`, `principal`, `core-doctor`, `gateway`, `runner`, `register`, `version`.
- `config/examples/owner-all-in-one.env.example`(core 비밀 포함)은 프로세스마다 `--env-file`로만 읽힌다. compose `env_file:`, systemd `EnvironmentFile=`, 셸 `source`로 gateway·runner 프로세스에 넣지 않는다. compose는 역할마다 별도 env 파일(`core.env`, `gateway.env`, `runner.env`)을 쓴다.

## 12. 호스트 GPU 벤치마크 (`scripts/runner/bench_host_gpu.py`, owner 전용)

0.1.0은 콘솔 작업 큐로 BENCHMARK 작업을 만들지 않는다. 벤치마크는 owner 호스트에서 이 스크립트로 실행한다(공식 가중치 → ONNX → ONNX Runtime(CUDA) → TensorRT, scope별 지연시간과 정확도 차이; BENCHMARK 핸들러를 이 프로세스에서 실행). 이 스크립트는 공개 배포본에 들어 있지 않다.

```bash
CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES=0 runtime/venv/bin/python scripts/runner/bench_host_gpu.py \
    --data-root /ABSOLUTE/PATH/TO/pilot_subset --weights yolo11n.pt
```

| 옵션 | 환경 변수 | 뜻 |
|---|---|---|
| `--data-root` | `MBNAS_BENCH_DATA_ROOT` | (벤치마크 실행 시 필수) PILOT 배치의 데이터셋 디렉터리(`data.yaml` + `--val-dir`). 이 실행의 유일한 데이터 허용 루트 |
| `--val-dir` | — | 벤치마크 split 이미지 디렉터리(`--data-root` 기준, 기본 `images/search_val`) |
| `--weights-dir` | `MBNAS_RUNNER_WEIGHTS_DIR` | 가중치 디렉터리(기본 `$MBNAS_HOME/weights`, RF-DETR는 그 아래 `rfdetr/`) |
| `--weights`, `--rfdetr` | — | 측정할 ultralytics 체크포인트 파일 이름(기본 `yolo11n.pt`)과 RF-DETR 체크포인트 이름 |
| `--runs-root` | `MBNAS_BENCH_RUNS_ROOT` | 출력 허용 루트(기본 `$MBNAS_HOME/runner/bench_host_gpu`). 임시 파일도 이 아래(`/tmp` 미사용) |
| `--postprocess CONF IOU MAX_DET`, `--imgsz`, `--warmup`, `--repeats`, `--runtimes` | — | 측정 조건(기본 후처리 0.25 0.7 300) |
| `--evidence-dir`, `--doc`, `--write-doc`, `--render-latest`, `--render-from` | — | 결과 디렉터리(기본 `<runs-root>/evidence`)와 보고 문서 생성. `--render-*`는 GPU 작업 없이 문서만 다시 만든다 |

- GPU가 필요하다(`CUDA_VISIBLE_DEVICES`로 하나를 고른다). 다른 작업과 GPU를 나눠 쓰는 호스트에서는 `flock`으로 GPU 잠금(`$MBNAS_HOME/locks/gpu.lock`)을 잡고 실행한다.
- TensorRT는 설치 묶음에 없다. `tensorrt`를 import할 수 없으면 TensorRT 엔진은 BLOCKED로 남고 결과의 `tensorrt.status`에도 환경 미충족 상태가 기록되며, 다른 값으로 채우지 않는다.
- 이 스크립트는 네트워크 접근을 끄고(`YOLO_OFFLINE=1`, `HF_HUB_OFFLINE=1`) 미리 받아 둔 가중치만 쓴다.
- 보고서의 target 라벨은 보이는 장치 기준 `host-gpu-0`으로 고정되지만, 기록된 장치 uuid가 실제 GPU를 가리킨다. TensorRT를 쓸 수 없을 때 작업 결과의 TensorRT `runtime_status`는 `FAILED`, 상위 `tensorrt.status`는 환경 미충족 상태로 표기가 다르다(원인은 같다).

## 13. 로그와 요청 추적 (Access logs and request ids)

core와 gateway는 HTTP 요청 하나마다 접근 로그 한 줄을 남긴다(logger `mbnas_core.access` / `mbnas_server.access`, 각 프로세스 로그 = start.sh 기준 `runtime/logs/<이름>.log`, 컨테이너는 컨테이너 로그).

```text
<UTC 시각> INFO mbnas_server.access method=POST path=/api/v1/runs status=200 request_id=<id> duration_ms=812.4 client=127.0.0.1:53122
<UTC 시각> INFO mbnas_core.access method=POST path=/core/v1/console/runs status=200 request_id=<id> duration_ms=790.1 client=127.0.0.1:53140
```

- 필드: `method`, `path`(쿼리의 `token`·`access_token`·`sig`·`exp` 값은 `***`로 가림), `status`, `request_id`, `duration_ms`, `client`. 요청 하나는 항상 한 줄이다(제어 문자·공백은 escape).
- 요청 추적: 브라우저나 도구가 보낸 `X-Request-ID`(없으면 gateway가 만든 값)가 gateway 줄의 `request_id`가 되고, gateway는 같은 값을 core 호출에 `X-Request-ID`로 넘긴다. 그래서 사용자 요청 하나의 gateway 줄과 core 줄은 같은 `request_id`를 가진다. 오류 응답 본문의 `request_id`로 두 호스트의 로그를 `grep "request_id=<id>"`로 찾는다. core 오류 본문의 id가 다르면 `core_request_id`로 함께 온다.
- 두 프로세스 모두 uvicorn 자체 접근 로그 대신 이 줄을 쓴다.

## 14. 분리 배치의 core 쪽 파일, 장치 목록, CPU runner (Split deployment notes)

**owner worker 파일 위치.** 후보 생성 작업의 제어 파일(`control/<job>.stop`)은 항상 core 호스트의 `<MBNAS_CORE_OUTPUT_ROOT>/<run id>/`(기본 `runtime/core/runs/<run id>/`)에 둔다. 프로토콜의 `console.paths.output_root`가 고객 호스트 경로라 core 호스트에서 만들거나 쓸 수 없으면, 작업별 오류 로그(`logs/`), 생성 구조 YAML(`architectures/<후보 id>.yaml`)과 생성 journal(`architectures/generation-<seed>.json`)도 같은 core 쪽 디렉터리에 쓴다. owner 일체형처럼 그 경로를 core 호스트에서 쓸 수 있으면 원래 run 디렉터리를 그대로 쓴다. 생성 작업을 재시도·재개할 때 journal은 run 디렉터리, 그 프로세스 설정의 core 쪽 디렉터리, 이미 저장된 후보의 core 쪽 디렉터리 순으로 찾는다. 그러므로 core API와 owner worker는 같은 core 설정으로 띄운다(§1 `--env-file`).

**core 쪽 생성 구조 YAML의 다운로드.** core 쪽 디렉터리에 쓴 생성 구조 YAML도 그 후보의 `architecture` artifact로 등록된다. 조건은 파일이 정확히 `<MBNAS_CORE_OUTPUT_ROOT>/<run id>/architectures/<후보 id>.yaml`이고, 심볼릭 링크가 아닌 일반 파일이며, 1 MiB 이하이고, 내용이 그 후보에 기록된 구조 YAML과 바이트 단위로 같은 것이다. 조건에 맞지 않는 파일은 등록하지 않는다.

- 제공: core가 같은 조건을 다시 확인한 뒤 바이트를 내주고, gateway는 그 sha256을 다시 대조해 전달한다(`X-Artifact-Verification: core`). 거부: 경로가 `architectures/<이름>.yaml`이 아니거나 심볼릭 링크이면 `403 ACCESS_DENIED`, 1 MiB를 넘거나 등록 때의 sha256과 다르면 `409 LOAD_FAILED`, 파일이 없으면 `404 MISSING`(이 두 경우 그 artifact의 상태도 `LOAD_FAILED`/`MISSING`으로 바뀐다).
- 이 파일은 gateway 저장소에 복사되지 않는다. 다운로드와 내보내기 ZIP은 core에서 바이트를 받아 오므로 core가 연결되어 있어야 하며(아니면 `503 CORE_UNAVAILABLE`), 원본은 core 호스트에만 있다(§8 백업).
- `MBNAS_CORE_OUTPUT_ROOTS`를 직접 정할 때는 `MBNAS_CORE_OUTPUT_ROOT`가 그 안에 있어야 한다(밖이면 이 파일들의 다운로드가 `403 ACCESS_DENIED`). 기본값은 `[MBNAS_CORE_OUTPUT_ROOT]`다.

**owner worker 작업 실패 코드.** 작업 하나를 처리하다 오류가 나면 worker는 멈추지 않고 그 작업만 실패로 기록한다: `WORKER_IO_ERROR`(core 호스트의 파일·디렉터리 오류, 예: 만들 수 없는 경로), `WORKER_JOB_ERROR`(그 밖의 예외). 원인은 core 쪽 작업 로그에서 찾는다.

**검증용 장치 목록(`MBNAS_CORE_DEVICE_SOURCE`).** 프로토콜 검증은 장치 목록으로 `console.benchmark.device`를 확인하고, 평가 ON 탐색이 가능한지 판단한다.

| 값 | 장치 목록 |
|---|---|
| `local`(기본) | core 호스트의 NVML(GPU) 목록. 그 목록이 비어 있으면(GPU 없는 core 호스트) 등록된 CPU runner들의 `cpu` 한 항목으로 대신한다. GPU runner의 장치는 `local`에 합치지 않는다 |
| `runners` | 등록된 runner의 장치. GPU runner는 장치 uuid별 한 항목, CPU 전용 runner들은 `cpu` 한 항목으로 묶인다 |

- `console.benchmark.device`에는 장치 id·uuid·이름, 그 장치를 쓰는 runner id, 또는 `cpu`를 줄 수 있다.
- "등록된 runner"는 상태가 `REGISTERED` 또는 `ONLINE`인 runner다. 정지한(`OFFLINE`) runner와 응답이 끊긴(`LOST`) runner는 빠지므로, 검증 시점에 runner가 실행 중(`mbnas-runner run`)이어야 한다. 장치 목록은 core API 프로세스에서 최대 30초 동안 캐시되므로, runner를 처음 띄운 직후의 검증에는 잠시 반영되지 않을 수 있다.
- 장치 목록이 CPU뿐이면(또는 `console.benchmark.device`가 `cpu`이면) 평가 ON 탐색은 `EVALUATION_REQUIRES_CUDA`로 거부된다. 명시 학습과 평가 OFF 생성은 허용된다.
- GPU가 없는 owner 호스트(분리 배치에서 흔함)에서 `local`이면 CPU runner가 실행 중일 때 그 `cpu` 항목으로 학습 검증이 통과하고 평가 ON 탐색은 `EVALUATION_REQUIRES_CUDA`로 거부된다. 실행 중인 CPU runner가 없으면 목록이 비어 학습·탐색 검증이 `DEVICE_UNAVAILABLE`이다. GPU runner가 다른 호스트에 있으면 그 GPU를 검증에 쓰도록 `MBNAS_CORE_DEVICE_SOURCE=runners`로 둔다(`local`은 GPU runner를 보지 않는다).

**CPU runner의 claim 규칙(GPU·CPU 혼합 fleet).** CPU로 등록한 runner(`device = cpu`)는 claim할 때 결과에 CUDA 측정이 필요한 작업을 다음 경우에만 건너뛴다: 그 작업 종류를 받는 GPU runner(CPU가 아닌 runner)가 `ONLINE`일 때. CUDA가 필요한 작업은 평가 ON 탐색·최종 재학습 run의 모든 작업, BENCHMARK, 평가를 하는 명시 학습(`validation_enabled`가 false가 아니거나 `mode`가 `evaluate_only`)이다. 건너뛴 작업은 임대되지 않고 시도 횟수도 쓰지 않은 채 `QUEUED`로 GPU runner를 기다리며, CPU runner는 그 뒤의 다른 작업을 받거나 받을 것이 없으면 `204`(작업 없음)를 받는다. GPU runner는 규칙 없이 종전대로 받는다.

- CPU runner만 있는 fleet은 종전대로 받는다. 이런 fleet에서는 평가 ON 탐색이 보통 검증 단계에서 거부되지만, GPU가 있는 장치 목록으로 검증된 run(예: GPU 있는 core 호스트의 `local`)이면 CPU runner가 모든 후보를 학습하고 모두 `NOT_EVALUATED`로 끝난다. 순위·승급이 없고 GA는 부모로 쓸 평가 행이 없어 계산이 낭비되므로, 평가 ON 탐색은 GPU runner가 있을 때 돌린다.
- GPU runner가 멈추거나(`deregister` → `OFFLINE`) 응답이 끊기면(core의 `MBNAS_CORE_HEARTBEAT_S`의 3배 + 5초 뒤 `LOST`) 더는 `ONLINE`이 아니므로, 남은 CUDA 필요 작업을 CPU runner가 가져가 `NOT_EVALUATED`로 끝날 수 있다. 평가 ON 탐색 중 GPU runner를 내릴 때는 CPU runner도 멈추거나 `MBNAS_RUNNER_KINDS`로 받을 작업 종류를 좁힌다.

**CPU에서 평가하지 못한 결과(`NOT_EVALUATED`).** CPU runner가 평가 단계(학습 작업의 평가, EVALUATE 작업)에 이르면 지연시간을 잴 수 없으므로 아무것도 측정하지 않고, 작업은 성공(`SUCCEEDED`)하며 결과는 "평가 안 됨"을 선언한다. core는 그 단계의 행을 다음과 같이 기록한다.

- 행: 상태 `SEARCH_COMPLETE`, `performance_status = NOT_EVALUATED`, `reason_code = EVALUATION_REQUIRES_CUDA`(유효한 사유 코드가 없으면 `NOT_EVALUATED`). `ap`·`recall`·`latency_ms`·`size_mb`·`fitness`·`gate`는 null(가짜 지표를 만들지 않음), `latency_source`·`evaluation_hash`는 `pending`, `split_hash`는 null. 학습한 checkpoint(`checkpoint_path`·sha256)와 실행 장치(`raw_metrics.device_used`)는 남는다. 이미 측정된 행(EVALUATED)은 바꾸지 않는다.
- 명시 학습 run: validation을 끈 명시 학습과 같은 모양(`training_mode`·`source_kind` 유지)으로 끝나고 run은 `COMPLETED`다.
- 탐색 arm(GA·IndependentRandom·Baseline·Final): 그 후보는 평가되지 않은 채로 남는다. 승급·다음 단계가 없고, 세대의 평가 수에 세지 않으며, GA의 부모가 되지 않는다.
- 학습된 구조 hash가 후보와 다르면 종전처럼 표시한다: 명시 학습 행은 `reason_code = ARCHITECTURE_HASH_MISMATCH`, 다른 arm의 행은 `EVAL_FAILED`.
- 웹은 명시 학습의 이런 행과, 사유가 `EVALUATION_REQUIRES_CUDA`인 탐색 arm 행을 "학습 완료 · 미평가"로 표시한다(서버가 `training_status = UNTRAINED`라고 보고한 탐색 arm 행은 제외). 지표 값은 null이라 비어 보인다.

**health의 허용 경로.** gateway의 `GET /api/v1/health`는 gateway 자신의 허용 경로(`MBNAS_GW_DATASET_ROOTS`·`OUTPUT_ROOTS`, 비어 있을 수 있음)와 아카이브 루트만 보여 준다. core 호스트의 허용 경로는 중계하지 않는다.

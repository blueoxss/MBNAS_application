# INSTALL — 설치 (Installation)

설치 방법은 ① Docker compose, ② `pip install -r requirements-<profile>.txt`, ③ (선택) `scripts/install.sh`(②의 래퍼, 오프라인 설치 지원) 순으로 권장한다.

## 0. 시작 전에 (Before you start)

**프로필을 고른다.**

| 프로필 | 설치되는 것 | 설치 위치 |
|---|---|---|
| `public` | gateway(`mbnas-server`), runner(`mbnas-runner`), model runtime, 공통 계약, 빌드된 웹 | 고객 호스트. **원격 core가 반드시 필요** |
| `owner` | `public` 전부 + core(`mbnas-core`: core API, owner worker) | owner 호스트(owner 배포본에만 core가 들어 있다) |

**public 설치 전에 core 운영자에게 받을 것**

- core 주소(loopback이 아니면 `https://`), 사설 CA라면 CA 파일(PEM)
- gateway 서비스 토큰: core 운영자가 `mbnas-core-principal create --kind server`로 발급
- runner 토큰과 runner id: `mbnas-core-principal create --kind runner --runner-id <id>`로 발급(GPU 하나에 runner 하나)

**요구 사항**

- Linux x86_64, Python 3.10(방법 ②·③), 학습용 NVIDIA GPU와 CUDA 12.4를 지원하는 드라이버
- 운영 스크립트(`scripts/*.sh`)는 Linux 전용이며 bash 4 이상이 필요하다
- Docker 사용 시 Docker Compose, GPU 컨테이너에는 NVIDIA Container Toolkit
- 온라인 설치(방법 ②·③, 이미지 빌드)는 `pypi.org`, `files.pythonhosted.org`, `download.pytorch.org`, `download-r2.pytorch.org`, `pypi.nvidia.com`에 접속한다(방화벽·프록시 허용 필요)
- 기본 포트: core 8700, gateway 8710, 웹 preview 8720(개발용)
- Node.js는 웹을 다시 빌드할 때만 필요하다(gateway가 `mbnas_web/dist`를 제공)
- 사전학습 가중치(Ultralytics 공식 가중치, RF-DETR·DINOv2 등)와 데이터셋은 배포본에 들어 있지 않으며(예제는 합성 데이터), 각 배포처의 이용 조건을 따른다

배포 ZIP을 푼 최상위 디렉터리(`mbnas_application/`)를 아래에서 **ROOT**라 부른다. 모든 명령은 ROOT에서 실행한다.

## 1. 방법 ① Docker compose

공개 배포 정의는 `deploy/public/`(gateway + runner, 웹은 gateway가 정적 제공), owner 배포 정의는 `deploy/owner/`(core + owner worker, 선택적으로 gateway·runner)에 있다. 이미지 빌드 컨텍스트는 ZIP을 푼 ROOT다.

| 이미지(기본 태그) | Dockerfile | 기반 이미지 | 크기(대략) | 비고 |
|---|---|---|---|---|
| `mbnas-public:0.1.0` | `deploy/public/Dockerfile` | `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` | 10.6 GB | GPU runner + gateway. 역할 인자 `gateway` 또는 `runner` |
| `mbnas-public-cpu:0.1.0` | `deploy/public/Dockerfile.cpu` | `python:3.10-slim` | 3 GB | CPU 전용(GPU 없는 호스트) |
| `mbnas-owner:0.1.0` | `deploy/owner/Dockerfile` | `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` | 10.6 GB | **비공개 core 포함 — owner 호스트 로컬에서만 빌드·보관하고 외부 레지스트리에 올리지 않는다** |

- 이미지는 uid/gid 10001로 실행된다(compose는 `MBNAS_UID`·`MBNAS_GID`로 바꿀 수 있다). `runtime/`은 그 uid가 쓸 수 있고 `data/`는 읽을 수 있어야 한다.
- 이미지 빌드도 온라인 설치와 같은 서버에 접속한다(로컬 wheel 묶음으로 빌드하는 방법은 각 Dockerfile 머리말 참고).
- compose 변수 전체(이미지·네트워크·포트 공개·GPU·공유 메모리)는 `OPERATIONS.md` §11에 있다.

**준비(공통)**

```bash
cd <ROOT>
mkdir -p runtime data                       # runtime/: 모든 상태(쓰기), data/: 데이터셋(컨테이너에 읽기 전용으로 마운트)
export MBNAS_UID=$(id -u) MBNAS_GID=$(id -g)   # runtime/·data/ 소유자(기본 10001)
```

역할별 env 파일을 만든다. 방법 ③의 `install.sh`가 만들어 주며, 직접 만들 때는 `config/examples/<role>.env.example`을 `config/<profile>/<role>.env`로 복사(권한 0600)하고 값을 채운다. 비밀 값 생성: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.

**public(고객 호스트): gateway + GPU runner**

```bash
docker compose -f deploy/public/docker-compose.yml build
docker compose -f deploy/public/docker-compose.yml up -d            # gateway(127.0.0.1:8710) + runner(GPU 0)
docker compose -f deploy/public/docker-compose.yml logs -f gateway
docker compose -f deploy/public/docker-compose.yml down
```

- 사용하는 파일: `config/public/gateway.env`, `config/public/runner.env`. compose가 gateway 데이터 루트를 `/srv/mbnas/data`로 정하므로 `gateway.env`에 `MBNAS_GW_OUTPUT_ROOTS`를 반드시 설정한다(비어 있으면 모든 `output_root`가 거부된다).
- 컨테이너 안에서 `MBNAS_HOME=/srv/mbnas/runtime`(= `runtime/`), 데이터셋은 `/srv/mbnas/data`(= `data/`, 읽기 전용)다. **프로토콜의 데이터 경로는 컨테이너 경로로 적는다**(예: `/srv/mbnas/data/<dataset>/data.yaml`).
- gateway는 기본으로 `127.0.0.1:8710`에만 공개된다(`MBNAS_GW_PUBLISH`). 외부 노출은 TLS reverse proxy(`deploy/public/nginx.example.conf`)를 앞에 둔다.
- GPU runner에는 NVIDIA Container Toolkit이 필요하다. 사용할 GPU는 `MBNAS_RUNNER_GPU`(기본 0). GPU를 더 쓰려면 runner 서비스를 복사하고 runner id·토큰·env 파일을 따로 둔다.
- GPU가 없는 호스트(CPU runner):

  ```bash
  export MBNAS_PUBLIC_IMAGE=mbnas-public-cpu:0.1.0 MBNAS_PUBLIC_DOCKERFILE=deploy/public/Dockerfile.cpu
  docker compose -f deploy/public/docker-compose.yml --profile cpu up -d gateway runner-cpu
  ```

- 같은 호스트의 loopback core에 연결하는 경우(시험용): `export MBNAS_NETWORK_MODE=host MBNAS_GW_BIND_HOST=127.0.0.1`(필요하면 `MBNAS_GW_LISTEN_PORT=<빈 포트>`), env 파일의 core URL은 `http://127.0.0.1:8700`. host 네트워크에서는 포트 공개(`MBNAS_GW_PUBLISH`)가 무시되고 gateway가 호스트의 `MBNAS_GW_LISTEN_PORT`(기본 8710)에서 바로 듣는다.
- 이미지 진입점 역할: `gateway`, `runner`, `register`(core에 한 번 등록하고 협상 결과 출력), `doctor`, `version`.

**owner(owner 호스트): core + owner worker** — owner 배포본에만 있다.

```bash
docker compose -f deploy/owner/docker-compose.yml build
docker compose -f deploy/owner/docker-compose.yml up -d                                   # core(8700) + owner worker
docker compose -f deploy/owner/docker-compose.yml run --rm core principal create --kind server
docker compose -f deploy/owner/docker-compose.yml run --rm core principal create --kind runner --runner-id <id>
docker compose -f deploy/owner/docker-compose.yml --profile all-in-one --profile gpu up -d   # + gateway + GPU runner
docker compose -f deploy/owner/docker-compose.yml --profile all-in-one --profile cpu up -d   # + gateway + CPU runner
```

- 사용하는 파일: `config/owner/core.env`(core·owner worker), `config/owner/gateway.env`(all-in-one), `config/owner/runner.env`(gpu/cpu).
- 발급된 토큰은 한 번만 출력된다. gateway·runner env 파일에 넣거나 고객에게 전달한다.
- owner 이미지 역할 인자: `core`, `owner-worker`, `principal`, `core-doctor`, `gateway`, `runner`, `register`, `version`. core 공개 주소는 `MBNAS_CORE_PUBLISH`(기본 `127.0.0.1:8700`).
- `config/examples/owner-all-in-one.env.example`을 compose `env_file`로 쓰지 않는다(core 비밀이 gateway·runner 컨테이너 환경에 들어간다).
- core를 127.0.0.1 밖으로 공개할 때는 TLS reverse proxy를 앞에 둔다(원격 runner는 https만 허용). proxy는 `/core/v1/console/`과 `/core/v1/runner/`만 넘기고 owner 관리 API `/core/v1/owner/`는 차단한다(core는 admin 토큰만 검사하고 요청 주소는 보지 않는다). 예시: `deploy/public/nginx.example.conf` 끝의 owner 호스트용 블록, 설명은 `OPERATIONS.md` §5.

## 2. 방법 ② pip requirements (표준 경로)

```bash
cd <ROOT>                                   # requirements 파일과 wheels/ 가 있는 디렉터리
python3.10 -m venv .venv
.venv/bin/python -m pip install -r requirements-public.txt      # owner 호스트: requirements-owner.txt
```

- requirements 파일 끝의 프로젝트 wheel(`./wheels/mbnas_*-0.1.0-py3-none-any.whl`)은 **현재 작업 디렉터리 기준 경로**다. 반드시 ROOT에서 실행한다.
- 서드파티 버전은 `constraints.txt`와 같은 값으로 고정되어 있고, PyTorch는 cu124 index에서 받는다.
- GPU가 없는 호스트: `requirements-cpu.txt`(CPU 빌드 torch). CPU runner는 평가 완료 판정을 하지 않는다. 이 파일은 `download.pytorch.org/whl/cpu`의 +cpu 빌드를 쓰며 `triton==3.2.0`을 고정한다(의존성 폐포 보정). cu124 wheel 묶음에는 +cpu 빌드가 없으므로 **그 묶음으로는 오프라인 설치할 수 없다**.
- 호스트의 `pip.conf`가 방해하면 `PIP_CONFIG_FILE=/dev/null`을 붙인다.
- 오프라인: wheel 묶음이 있으면 `pip install --no-index --find-links <wheelhouse> -r requirements-public.txt`.
- 알려진 한계: requirements 파일은 버전을 정확히 고정하지만 hash 고정(`--require-hashes`)은 하지 않는다. 내려받는 파일의 무결성은 index 서버(TLS)에 의존한다.

설치 뒤에는 §4의 설정 파일을 만들고 §5처럼 실행한다. 방법 ②는 설정 파일과 `runtime/` 디렉터리를 자동으로 만들지 않는다(방법 ③은 만든다).

## 3. 방법 ③ (선택) `scripts/install.sh`

방법 ②의 얇은 래퍼다. `runtime/venv`를 만들고 requirements를 설치한 뒤 `runtime/` 디렉터리와 역할별 설정 파일을 만든다.

```bash
bash scripts/install.sh --profile public
bash scripts/install.sh --profile public --requirements requirements-cpu.txt     # CPU 전용 호스트
bash scripts/install.sh --profile public --offline --wheelhouse <dir>             # 네트워크 없이(wheel 묶음 필요)
bash scripts/install.sh --profile owner                                           # owner 호스트
```

| 옵션 | 뜻 |
|---|---|
| `--profile owner\|public` | (필수) 설치 프로필 |
| `--python <interp>` | venv를 만들 Python 3.10 인터프리터(기본: PATH의 `python3.10`) |
| `--offline` | `pip install --no-index --find-links <wheelhouse>`로 네트워크 없이 설치(`requirements-cpu.txt`는 +cpu 빌드를 담은 wheel 묶음이 있어야 한다) |
| `--wheelhouse <dir>` | `--offline`에서 쓸 wheel 디렉터리(기본: `<ROOT>/wheelhouse`, 없으면 `<ROOT>/build/wheelhouse`). `--offline`과 함께만 |
| `--requirements <file>` | `requirements-<profile>.txt` 대신 쓸 파일(예: `requirements-cpu.txt`) |
| `--web` | `mbnas_web`에서 `npm ci`(+ `dist`가 없으면 `npm run build`). Node.js 필요. 배포본의 `dist`를 그대로 쓸 때는 불필요 |

스크립트가 하는 일(요약, 자세한 내용은 `bash scripts/install.sh --help`):

1. 패키지 메타데이터 확인(public·registration 배포본은 `--profile owner`로 설치할 수 없다)
2. `runtime/venv` 생성(시스템 site-packages 미사용). 개발용 editable 설치가 든 기존 venv는 거부하고 지우지 않는다
3. `pip install -r <requirements>`(로그: `runtime/logs/install-<UTC 시각>.log`), 프로필 패키지가 ROOT 안에서 import되는지 확인
4. `runtime/{pids,logs,data,weights,uploads,locks}` 생성
5. `config/<profile>/<role>.env`를 `config/examples/<role>.env.example`에서 복사(기존 파일은 덮어쓰지 않음). 비밀 값은 비었거나 placeholder일 때만 `secrets.token_urlsafe(48)`로 생성하며 출력하지 않는다. 파일 0600, 디렉터리 0700
   - public 프로필: core가 발급하는 `MBNAS_GW_CORE_TOKEN`, `MBNAS_RUNNER_TOKEN`은 주석 처리된 placeholder로 남는다 → 운영자에게 받은 값을 넣는다
   - owner 프로필: 로컬 core DB에 server·runner principal을 만들어 두 토큰을 채운다
6. 선택 웹 단계(`--web`)와 환경 요약 출력

종료 코드: 0 설치 완료, 1 단계 실패(메시지와 설치 로그 확인), 2 사용법 오류.

## 4. 설정 (Configuration)

역할마다 env 파일이 하나다. 모든 키와 설명은 `config/examples/*.env.example`에 있다(값은 placeholder만).

| 역할 | 파일(설치 후) | 반드시 확인할 키 |
|---|---|---|
| gateway | `config/<profile>/gateway.env` | `MBNAS_GW_API_TOKEN`(콘솔 토큰, 생성됨), `MBNAS_GW_CORE_URL`, `MBNAS_GW_CORE_TOKEN`(core 발급), `MBNAS_GW_CORE_CA_FILE`(사설 CA), `MBNAS_GW_DATASET_ROOTS`·`MBNAS_GW_OUTPUT_ROOTS`(public 배포에서 필수: 이 호스트의 데이터·출력 허용 루트) |
| runner | `config/<profile>/runner.env` | `MBNAS_RUNNER_CORE_URL`, `MBNAS_RUNNER_TOKEN` 또는 `MBNAS_RUNNER_TOKEN_FILE`, `MBNAS_RUNNER_RUNNER_ID`(토큰 발급 때의 id와 같게. 비워 두면 무작위 `runner-<8 hex>`가 처음 쓸 때 만들어져 `runtime/runner/runner_id`에 보관되고, install.sh가 그 id를 출력한다 — 호스트 이름은 쓰지 않는다. 예전 기본값 `runner-<hostname>`으로 발급된 토큰은 `RUNNER_MISMATCH`가 되므로 id를 명시하거나 토큰을 다시 받는다), `MBNAS_RUNNER_DATASET_ROOTS`, `MBNAS_RUNNER_GPU_INDEX`, 필요 시 `MBNAS_RUNNER_DATASET_MOUNTS`·`MBNAS_RUNNER_CA_FILE` |
| core(owner) | `config/owner/core.env` | `MBNAS_CORE_API_TOKEN`·`MBNAS_CORE_ARTIFACT_SIGNING_KEY`·`MBNAS_CORE_TICKET_KEY`(생성됨), `MBNAS_CORE_DATASET_ROOTS` 등 |

- 공통 루트 `MBNAS_HOME`은 **프로세스 환경에서만** 읽는다(env 파일로는 설정되지 않음). `scripts/start.sh`는 `<ROOT>/runtime`으로 설정한다. 방법 ②로 직접 실행할 때는 `export MBNAS_HOME=<ROOT>/runtime`.
- 우선순위: 프로세스 환경 변수 > env 파일 > 기본값. 비밀 키에 빈 값(`KEY=`)을 쓰면 거부되므로, 쓰지 않을 키는 주석으로 둔다.
- 경로 설정은 절대경로여야 한다. 목록은 `a,b` 또는 JSON 배열.
- **분리 배치의 `output_root`**: gateway에 데이터 허용 루트(`MBNAS_GW_DATASET_ROOTS`)가 있으면 gateway가 고객 호스트에서 `console.paths`를 검사하고, run·학습·최종 재학습 생성 때도 다시 측정해 core에 보낸다. 따라서 사용자가 입력하는 `output_root`는 gateway 호스트의 `MBNAS_GW_OUTPUT_ROOTS` 안에 있으면 되고, core 호스트에 없는 경로여도 된다. 그런 run에서 core가 쓰는 파일(owner worker의 제어 파일·작업 로그·생성 구조 YAML·생성 journal)은 core 호스트의 `<MBNAS_CORE_OUTPUT_ROOT>/<run id>/`에 남고, 생성 구조 YAML은 core가 gateway를 거쳐 제공한다(`OPERATIONS.md` §5·§14). gateway에 데이터 허용 루트가 없는 owner 일체형은 core가 자기 `MBNAS_CORE_DATASET_ROOTS`·`MBNAS_CORE_OUTPUT_ROOTS`로 검사한다.
- **분리 배치의 학습 프로토콜**: core와 gateway가 다른 호스트일 때 학습 프로토콜의 `console.paths`에는 `train_root`·`search_val_root`를 명시한다. `dataset_yaml`만 주면 gateway가 측정하지 않은 split 경로를 core가 자기 호스트에서 찾다가 실패한다.
- 콘솔 토큰 확인: `grep MBNAS_GW_API_TOKEN config/<profile>/gateway.env`(화면에 비밀이 표시되므로 주의).
- owner 일체형(한 호스트에 core·gateway·runner)은 `config/examples/owner-all-in-one.env.example`을 참고한다. 이 파일은 **dotenv로만**(`--env-file`) 읽어야 한다. systemd `EnvironmentFile=`나 compose `env_file:`로 쓰면 core 비밀이 gateway·runner 프로세스 환경에 들어간다.

## 5. 실행과 확인 (Run and check)

방법 ③으로 설치한 경우:

```bash
bash scripts/doctor.sh --profile public            # 점검(--strict: FAIL이 있으면 exit 1, --json <file>|-: JSON 보고)
bash scripts/start.sh  --profile public            # gateway + runner (public은 core를 띄우지 않는다)
# 브라우저: http://127.0.0.1:8710/  →  서버 연결 화면에 콘솔 토큰 입력
bash scripts/verify.sh --profile public --suite relocation   # 설치 위치 점검(OPERATIONS.md §10)
bash scripts/stop.sh   --profile public
```

- `start.sh --role`: `core`(owner 전용) = core API(`mbnas-core-api`, 8700) + owner worker, `gateway`(8710, 웹 `dist`도 제공), `runner`, `web`(개발용 Vite preview, 127.0.0.1:`MBNAS_WEB_PORT`, 기본 8720, Node.js와 `mbnas_web/node_modules` 필요), `all`(기본: owner = core·owner worker·gateway·runner, public = gateway·runner). gateway가 웹을 제공하므로 `all`에는 `web`이 들어가지 않는다.
- 이미 설정된 환경 변수가 env 파일보다 우선한다(예: `MBNAS_GW_PORT`, `MBNAS_CORE_PORT`). core·owner worker·gateway는 `CUDA_VISIBLE_DEVICES`를 빈 값으로 두고(CPU) 실행되며, runner는 호출한 쪽의 `CUDA_VISIBLE_DEVICES`를 물려받는다.

방법 ②로 설치한 경우(같은 일을 직접):

```bash
export MBNAS_HOME="$PWD/runtime"
.venv/bin/mbnas-gateway-doctor --env-file config/public/gateway.env
.venv/bin/mbnas-gateway --env-file config/public/gateway.env            # 기본 127.0.0.1:8710
.venv/bin/mbnas-runner register --env-file config/public/runner.env     # core 연결·협상 확인
.venv/bin/mbnas-runner run --env-file config/public/runner.env          # 작업 수행 데몬
```

owner 호스트에서는 core API(`mbnas-core-api --env-file ...`, 기본 8700)와 owner worker(`mbnas-core-owner-worker --owner --env-file ...`)를 먼저 띄운다. 기동·정지·포트 변경은 `OPERATIONS.md`에 있다.

## 6. 제거 (Uninstall)

1. `bash scripts/stop.sh --profile <profile>`로 이 트리의 프로세스를 멈춘다.
2. `runtime/`을 지우면 venv·로그·데이터·산출물이 사라진다. owner 호스트의 `runtime/core/db/`는 run 기록 전체이므로 먼저 백업한다.
3. `config/<profile>/`에는 비밀 값이 있으므로 안전하게 폐기한다. 발급받은 토큰은 core 운영자에게 폐기(`mbnas-core-principal revoke <principal_id>`)를 요청한다.

## 7. 라이선스 (Licenses)

- 설치되는 Python 제3자 패키지의 라이선스 목록은 `THIRD_PARTY_NOTICES.md`, 웹 번들(`mbnas_web/dist`)에 들어가는 npm 패키지의 목록은 `THIRD_PARTY_NOTICES_WEB.md`에 있다.
- **ultralytics 8.4.173**(및 `ultralytics-thop`)은 AGPL-3.0이다. 모델 런타임이 실행 시 import하며, `mbnas_model_runtime/operators/parse_model_8_4_173.py`는 ultralytics `parse_model`의 수정 사본이다.

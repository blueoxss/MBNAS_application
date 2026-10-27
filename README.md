# MBNAS Research Console

**MBNAS 모듈형 신경망 구조 탐색·실험 통합관리 프로그램** — 객체 검출 모델의 신경망 구조 탐색(NAS) 실험을 설계·실행·관리하는 웹 콘솔과 서버. 실험 프로토콜 검증, 후보 구조 생성과 CPU 구조 검사, 학습·평가 작업의 원격 실행·일시정지·재개, 후보 구조 상세(gene 구성도·블록 표·구성 YAML)와 비교, 산출물 관리를 제공한다.

> 상태(2026-10-27): 개발 중. 패키지 분할과 원격 작업 큐는 이 저장소에서 CPU로 시험·실행 확인되었고, gateway 중계·공개 응답 투영·고객 측 artifact 저장소·웹 정리·설치/패키징은 작업 중이다. 독립 설치(owner·public·재배치), GPU 경로, 누출 검사는 **검증 예정**이다. 기능별 상태: [`docs/public/FEATURES.md`](docs/public/FEATURES.md).

## 구성 (Components)

| 패키지 | 배포 이름 | 역할 | 공개 여부 |
|---|---|---|---|
| `mbnas_contracts` | `mbnas-contracts` | DTO·OpenAPI 계약·작업 프로토콜·공통 유틸 | 공개 |
| `mbnas_model_runtime` | `mbnas-model-runtime` | 모델 어댑터(Ultralytics·RF-DETR·ETRI hybrid), 맞춤 연산자, 구조 관측, 데이터셋, 평가·벤치마크 | 공개 |
| `mbnas_runner` | `mbnas-runner` | 고객 GPU 호스트의 학습·평가·벤치마크 실행기(core에서 작업 임대) | 공개 |
| `mbnas_server` | `mbnas-server` | 사용자 대면 `/api/v1` gateway(포트 8710), 웹 정적 제공, 읽기 전용 아카이브 | 공개 |
| `mbnas_web` | — | React 웹 콘솔(빌드 산출물 `dist/`를 gateway가 제공) | 공개 |
| `mbnas_core` | `mbnas-core` | 탐색 정책·구조 게이트·판정·권위 상태·자격(포트 8700) | **비공개(owner 전용)** |

공개 구성요소는 고객 장비에서 실행되므로 비밀이 아니다. 탐색 정책과 판정은 owner 호스트의 core에만 있으며, 공개 배포본은 **원격 core 연결이 반드시 필요**하다. 신뢰 영역과 인터페이스: [`docs/public/SECURITY_MODEL.md`](docs/public/SECURITY_MODEL.md), [`docs/public/API.md`](docs/public/API.md). 구조도(System·Subsystem·Block·Module)와 인터페이스 연산 표는 registration 배포본의 `docs/registration/`에 있다.

## 배포 프로필 (Profiles)

| 프로필 | 내용 | 설치 위치 |
|---|---|---|
| `public` | gateway + runner + model runtime + 계약 + 웹 | 고객 호스트(원격 core 필요) |
| `owner` | `public` + core(API, owner worker) | owner 호스트 |
| `registration` | `public` + 등록 문서(`docs/registration/`) | 등록 제출용 |

## 요구 사항 (Requirements)

- Linux x86_64, Python **3.10**, 학습·평가용 NVIDIA GPU(CUDA 12.4 빌드 PyTorch 2.6.0). CPU 전용 runner는 학습·checkpoint·구조 생성까지(평가 완료 판정 없음).
- 고정 버전: ultralytics 8.4.173, torch 2.6.0(cu124), torchvision 0.21.0 — 전체 핀은 `constraints.txt`.
- Docker 설치 시 Docker Compose(+ GPU 컨테이너용 NVIDIA Container Toolkit).
- 온라인 설치는 PyPI와 `download.pytorch.org`에 접속한다. Node.js는 웹을 다시 빌드할 때만 필요하다.
- 기본 포트: core 8700, gateway 8710, 웹 preview 8720(개발용).

## 설치 (Installation)

자세한 내용과 설정 키: [`docs/public/INSTALL.md`](docs/public/INSTALL.md). 아래 명령은 배포 ZIP을 푼 최상위 디렉터리(ROOT)에서 실행한다.

**① Docker compose** — 공개 배포 정의 `deploy/public/`(gateway + runner), owner 배포 정의 `deploy/owner/`(core + owner worker).

```bash
mkdir -p runtime data && export MBNAS_UID=$(id -u) MBNAS_GID=$(id -g)
# config/public/gateway.env, config/public/runner.env 준비(config/examples/*.env.example 복사, 0600)
docker compose -f deploy/public/docker-compose.yml build
docker compose -f deploy/public/docker-compose.yml up -d        # gateway(127.0.0.1:8710) + GPU runner
```

컨테이너 안의 데이터 경로는 `/srv/mbnas/data`(= `data/`)다. CPU 전용 호스트, owner compose, 토큰 발급은 [`docs/public/INSTALL.md`](docs/public/INSTALL.md) §1.

**② pip requirements (표준 경로)**

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -r requirements-public.txt     # owner 호스트: requirements-owner.txt, CPU 전용: requirements-cpu.txt
```

requirements 파일 끝의 프로젝트 wheel(`./wheels/...`)은 현재 작업 디렉터리 기준이므로 반드시 ROOT에서 실행한다. 이어서 역할별 설정 파일(`config/examples/*.env.example` 참고)을 만들고 `mbnas-gateway --env-file …`, `mbnas-runner run --env-file …`으로 실행한다.

**③ (선택) `scripts/install.sh`** — ②의 래퍼. venv·`runtime/` 디렉터리·역할별 설정 파일(비밀 값 자동 생성, 0600)을 만든다.

```bash
bash scripts/install.sh --profile public                                   # 온라인
bash scripts/install.sh --profile public --offline --wheelhouse <dir>      # 오프라인(wheel 묶음 필요)
bash scripts/install.sh --profile public --requirements requirements-cpu.txt
```

public 설치에는 core 운영자가 발급한 gateway 서비스 토큰(`MBNAS_GW_CORE_TOKEN`)과 runner 토큰(`MBNAS_RUNNER_TOKEN`), core 주소가 필요하다.

## 실행 (Run)

```bash
bash scripts/doctor.sh --profile public       # 점검: PASS / WARN / FAIL (비밀 값은 출력하지 않음)
bash scripts/start.sh  --profile public       # gateway + runner (owner 프로필: core + owner worker + gateway + runner)
# 브라우저에서 http://127.0.0.1:8710/ 을 열고 서버 연결 화면에 콘솔 토큰(MBNAS_GW_API_TOKEN)을 입력
bash scripts/stop.sh   --profile public       # 이 트리의 pid 파일에 있는 프로세스만 정지
bash scripts/verify.sh --profile public --suite relocation     # 검증 묶음: unit, integration, relocation, package, gpu, all
```

옵션 전체는 각 스크립트의 `--help`와 [`docs/public/OPERATIONS.md`](docs/public/OPERATIONS.md)에 있다.

## 문서 (Documentation)

| 문서 | 내용 |
|---|---|
| [`docs/public/INSTALL.md`](docs/public/INSTALL.md) | 설치 방법 ①②③, 설정 파일 |
| [`docs/public/OPERATIONS.md`](docs/public/OPERATIONS.md) | 프로세스·포트·디렉터리, 기동·정지, 자격 관리, 원격 배치, 백업 |
| [`docs/public/FEATURES.md`](docs/public/FEATURES.md) | 기능과 구현·검증 상태, 지원 한계 |
| [`docs/public/API.md`](docs/public/API.md) | 공개 API 요약(계약 1.4.3), 업로드, SSE, 1.5.0 예정 변경 |
| [`docs/public/SECURITY_MODEL.md`](docs/public/SECURITY_MODEL.md) | 신뢰 영역, 자격, 입력 경계, 위협 모델과 신뢰 한계 |
| [`docs/public/COMPATIBILITY.md`](docs/public/COMPATIBILITY.md) | 지원 플랫폼, 계약 버전, 연산자 확장, hook 문자열, 탐색 공간 hash, 과거 checkpoint |
| [`docs/public/TROUBLESHOOTING.md`](docs/public/TROUBLESHOOTING.md) | 오류 코드별 원인과 조치 |
| [`docs/public/contracts/history/`](docs/public/contracts/history/) | 공개 API 계약 이력(1.0.0–1.4.3)과 migrate 스크립트 |
| `docs/registration/` | 등록 문서(개요, 구조도, 인터페이스 표, 파일 목록, 핵심 기여, 범위표, 확인 목록, 권리 검토) — registration 배포본에만 포함 |

## 보안 요약 (Security summary)

- 역할별 자격: 브라우저 토큰, gateway 서비스 토큰, runner 토큰(runner id에 결속), admin 토큰, 작업별 서명 ticket. core에는 토큰 hash만 저장된다.
- loopback 밖의 core 연결은 `https://`를 쓴다(runner는 평문을 거부).
- 고객 호스트의 root는 공개 구성요소와 그 결과를 모두 읽고 바꿀 수 있으며, 고객 측 측정값의 진실성은 증명되지 않는다. 정책 비공개는 core가 별도 관리 영역에 있을 때만 성립한다. 자세한 신뢰 한계: [`docs/public/SECURITY_MODEL.md`](docs/public/SECURITY_MODEL.md) §8.

## 라이선스 상태 (License status)

자체 코드의 라이선스는 **정해지지 않았다**. 의존 구성요소 중 ultralytics(AGPL-3.0)를 포함한 제3자 라이선스 목록은 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)(자동 수집, 법적 검토 아님)에 있고, 권리 검토 질문 목록은 registration 배포본의 `docs/registration/RIGHTS_REVIEW.md`에 있다. 검토가 끝나기 전에는 공개·제출하지 않는다.

## 버전 (Version)

설치 패키지 5종과 웹 패키지는 `0.1.0`이다. 공개 API 계약은 1.4.3, runner 프로토콜은 1.0.0이다.

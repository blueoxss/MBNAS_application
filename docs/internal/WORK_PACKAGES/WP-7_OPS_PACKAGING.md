# WP-7 운영 스크립트·배포·문서·ZIP 계약 v1 (설계자 claude-fable-5-1, 2026-10-23 22:5x; WP-1 수용 직후 scripts 초판 착수 가능, ZIP은 WP-6까지 수용 후)

우선순위: 이 문서 > 지침 §10~§13 > DD-A7, A13, A24, A25 > SECURITY_BASELINE §5(canary).

## 1. scripts/ (bash, `#!/usr/bin/env bash`, LF, `set -u -o pipefail`, 자기 위치 기준 ROOT 탐색 `ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"`, 모든 경로 인용, argv 배열만, 사용자 입력 셸 결합 금지, 기준 호출은 `bash scripts/x.sh`)
| 스크립트 | CLI | 동작 |
|---|---|---|
| `install.sh` | `--profile owner\|public [--python <interp>] [--wheelhouse <dir>] [--offline]` | `runtime/venv` 생성(`<python> -m venv`, system-site false, `PYTHONNOUSERSITE=1`), wheelhouse에서 `--no-index` 설치(없고 `--offline`이면 실패; 없고 온라인이면 `constraints.txt` 핀으로 PyPI+pytorch cu124 index 설치 — 네트워크 의존 명시), 프로필 패키지 설치(public: contracts/model_runtime/runner/server; owner: +core) **빌드된 wheel**(`build/wheels/<pkg>-0.1.0-py3-none-any.whl`; ZIP에는 wheel 동봉 또는 sdist→wheel 빌드) ; web: `npm ci --cache $ROOT/build/npm-cache`(ZIP에 `dist/` 동봉 시 선택) ; `runtime/` 디렉터리 생성; `BUILD_INFO.json` 검증; 환경 요약 출력 |
| `doctor.sh` | `--profile owner\|public [--strict]` | 인터프리터·버전·패키지 origin(`__file__`이 ROOT 아래인지, 원본 경로 문자열 0건)·설정 파일·포트 가용·GPU(nvidia-smi 있으면)·core 연결(public: `core_url` health)·디스크·`mbnas-core-doctor`/gateway doctor 호출; `--strict`는 차단 결함 시 exit 1 |
| `start.sh` | `--profile owner\|public [--role core\|gateway\|runner\|web\|all] [--env-dir <dir>]` | 역할별 프로세스를 `setsid nohup`로 기동, pid/log는 `runtime/pids/<role>.pid`, `runtime/logs/<role>.log`; env 파일은 `config/<profile>/*.env`(install이 examples에서 복사, 비밀은 install 시 `secrets.token_urlsafe`로 생성해 0600 저장; 출력 금지); 이미 떠 있으면 거부; public은 core 기동 안 함(원격 core 필요 메시지) |
| `stop.sh` | `--profile … [--role …]` | 자기 pid 파일의 프로세스만 SIGTERM→대기→SIGKILL; `pkill/killall` 금지; pid 재사용 검사(cmdline에 ROOT 포함 확인) |
| `verify.sh` | `--profile owner\|public --suite unit\|integration\|relocation\|package\|gpu\|all [--json <out>]` | unit=pytest(public 또는 owner+public 게이트), integration=live 스택(핵심 API 흐름; public은 `MBNAS_CORE_URL` 필요→없으면 BLOCKED_BY_ENV), relocation=모듈 origin·금지 경로·외부 cwd·공백 경로 재호출 검사, package=ZIP 검사(T8 canary scan + manifest 검증), gpu=유휴 GPU 확인 후 제한 예산 학습(없으면 BLOCKED_BY_ENV); owner 전용 시험은 public에서 NOT_APPLICABLE; 결과 JSON `{suite, status: PASS\|FAIL\|NOT_APPLICABLE\|BLOCKED_BY_ENV, counts, evidence}` |
| `package.sh` | `--profile owner\|public\|registration [--platform <auto>] [--reproducible]` | allowlist(`build/allowlists/<profile>.txt`, glob) → `build/staging/<profile>/mbnas_application/` 복사(ZIP 최상위 `mbnas_application/`) → wheel 빌드(`python -m build --wheel` 각 패키지, offline) → web `dist/` → `PACKAGE_PROFILE.json`·`BUILD_INFO.json`·`FILE_MANIFEST.jsonl`(상대경로·크기·sha256·역할·출처·라이선스; 자기 자신 제외) → 검사(절대경로·`..`·symlink·중복 entry·대소문자 충돌·금지 파일 패턴·private 패키지 부재) → `dist/<profile>/mbnas_application-<version>-<profile>-runtime-<platform>.zip`(owner는 `-owner-private-`) + `<zip>.sha256`; `--reproducible`은 mtime/순서/권한 고정 후 2회 빌드 hash 비교 결과를 BUILD_INFO에 기록(일치할 때만 주장) |

## 1b. 표준 설치 경로(DD-A31, 사용자 지시 — install.sh보다 우선하는 공식 경로)
- `requirements-public.txt`, `requirements-owner.txt`: 핀 고정(constraints.txt 값), 상단에 `--extra-index-url https://download.pytorch.org/whl/cu124`, 끝에 프로젝트 wheel 상대경로(`./wheels/mbnas_contracts-0.1.0-py3-none-any.whl` …; public은 core 제외). `requirements-cpu.txt` 변형(torch CPU index)도 제공하되 "검증 플랫폼은 cu124"로 표기.
- `deploy/public/Dockerfile`(nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 + python3.10 + requirements-public + wheels + mbnas_web/dist; ENTRYPOINT는 역할 인자 gateway|runner), `deploy/public/docker-compose.yml`(gateway 8710 → 정적 web, runner GPU reservation, env 파일 `config/public/*.env`, 볼륨 runtime/·data/), `deploy/public/Dockerfile.cpu`(python:3.10-slim, CPU smoke용). `deploy/owner/Dockerfile`+compose(core 8700 + owner worker). 이미지 빌드는 ZIP 압축 해제본을 build context로(private 파일 없음 검사 포함).
- README 설치 절은 ① Docker compose ② pip requirements ③ (선택) install.sh/offline 순.
- 수용: 로컬 docker로 public 이미지 빌드·기동 → 이 머신 core에 연결(T5) → CPU smoke 통과; owner compose로 T4; 이미지 크기·빌드 시간 기록.

## 2. config/examples, deploy, 문서
- `config/examples/{core.env.example, gateway.env.example, runner.env.example, owner-all-in-one.env.example}`(값은 placeholder; 키 설명 주석), `protocol.pilot_smoke.example.json`, `model_registry.example.json`, `server.example.yaml` 갱신.
- `deploy/public/{mbnas-gateway.service.example, mbnas-runner.service.example, nginx.example.conf}`, `deploy/owner/{mbnas-core.service.example, mbnas-core-owner-worker.service.example}`; 역할별 env 파일 분리(R3).
- `docs/public/{INSTALL.md, FEATURES.md, OPERATIONS.md, TROUBLESHOOTING.md, SECURITY_MODEL.md(trust limits 포함), COMPATIBILITY.md(hook shim, /3 hash, 과거 .pt), API.md(1.5.0 요약), contracts/history/}`; `docs/registration/{PROGRAM_OVERVIEW.md, SYSTEM_STRUCTURE.md(System/Subsystem/Block/Module 표 + SVG 도식(직접 그린 편집 가능 파일: mermaid 소스 + 렌더), INTERFACES.md, FILE_LIST.md(ZIP manifest 기반 자동 생성), KEY_CONTRIBUTIONS.md(기여 1·2 강조, private 규칙 비기재), SUBMISSION_SCOPE_TABLE.md(owner/public/registration 범위표), REVIEW_CHECKLIST.md(확인 필요 필드: 개발일·창작자·권리자·기관 양식), RIGHTS_REVIEW.md}`; 루트 `README.md`, `BUILD_INFO.json`(version, build_id, git 없이 생성: 소스 트리 sha, 시각, 플랫폼, 도구 버전), `PACKAGE_PROFILE.json`(프로필별 포함/제외 모듈, Python/Node/OS/CUDA 요구, core 연결 필요 여부, 설정 키, 데이터·가중치 요구, 설치 네트워크 의존, offline_install 지원 여부, 시험 platform·build id).
- `examples/`: 합성 4이미지 데이터셋 생성 스크립트+산출(작은 PNG, 라벨), `yolo11n` 기반 사용자 YAML 예시(정상 1·고의 오류 1), CPU smoke protocol JSON(generation-only, 64px).

## 3. canary(T8) 도구
`scripts/dev/canary_plant.py`·`scripts/dev/canary_scan.py`(SECURITY §5 설계), 매니페스트 `docs/internal/canary/CANARY_MANIFEST.json`(owner 전용). `verify.sh --suite package`가 scan 호출.

## 4. 수용조건
- README 명령과 스크립트 옵션 일치(문서에 없는 옵션 0, 구현 안 된 옵션 0).
- 공백 포함 새 경로에 ZIP 해제 → `bash scripts/install.sh --profile public` → doctor → start(web+gateway+runner) → verify --suite unit,relocation → stop, 외부 cwd에서 재호출 OK(T6).
- `package.sh` 3 프로필 ZIP + .sha256, 검사 0 위반, manifest 자기 순환 없음, public/registration에 private 패키지·owner 테스트·internal 문서·canary 0건.
- offline: wheelhouse 동봉 public ZIP(platform wheel 포함 변형 `-public-runtime-linux_x86_64-offline`)은 선택 산출이며 `unshare -rn` 설치 성공 시에만 `offline_install: true`.

## 5. 소유
WP-7A scripts(opus) / WP-7B config·deploy·examples(sonnet) / WP-7C docs public·registration(opus) / WP-7D package.sh·allowlist·manifest·canary(opus) / WP-7V verify(T6/T8, opus).

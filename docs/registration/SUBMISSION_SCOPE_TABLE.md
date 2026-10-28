# SUBMISSION_SCOPE_TABLE — 배포·제출 범위표 (owner · public · registration)

## 1. 배포 프로필

| 프로필 | 용도 | core 연결 |
|---|---|---|
| owner-private | owner 호스트 전체 설치(core 포함). 외부 배포 금지 | 자체 core |
| public-runtime | 고객 호스트 설치(gateway·runner·웹) | **원격 core 필수** |
| registration-runtime | 등록 제출용: public-runtime + 등록 문서 | 원격 core 필수 |

ZIP 이름 형식은 `mbnas_application-<version>-<profile>-runtime-<platform>.zip`이고 owner 프로필은 이름에 `owner-private`를 표기한다. ZIP의 최상위 디렉터리는 `mbnas_application/`이며, 각 ZIP 옆에 `.sha256` 파일이 있고 ZIP 안에는 `BUILD_INFO.json`·`PACKAGE_PROFILE.json`·`FILE_MANIFEST.jsonl`·`PACKAGE_CONTENTS.md`가 들어 있다.

## 2. 구성 요소별 범위

표기: ● 그 경로의 파일이 모두 포함 · △ 일부(비고 참조) · ○ 없음. 빌드 부산물(`__pycache__`, `*.egg-info`, `node_modules`)은 어느 ZIP에도 없다. 각 ZIP에 실제로 들어 있는 파일의 최종 목록은 그 ZIP의 `PACKAGE_CONTENTS.md`(사람이 읽는 표)와 `FILE_MANIFEST.jsonl`이다.

| 구성 요소 | 경로 | owner-private | public-runtime | registration-runtime | 등록 문서 기재 수준 | 비고 |
|---|---|:-:|:-:|:-:|---|---|
| 공통 계약 | `mbnas_contracts/`(pyproject + src) | ● | ● | ● | 전체 | OpenAPI 계약 파일 포함(공개 API 1.5.0, runner 프로토콜 1.1.0) |
| 모델 런타임 | `mbnas_model_runtime/`(pyproject + src) | ● | ● | ● | 전체 | ETRI 연산자(vendored)·ETRI hybrid 어댑터와 ultralytics `parse_model` 수정본 포함. 출처·라이선스는 `THIRD_PARTY_NOTICES.md` |
| 실행기(runner) | `mbnas_runner/`(pyproject + src) | ● | ● | ● | 전체 | |
| 게이트웨이 | `mbnas_server/`(pyproject + src) | ● | ● | ● | 전체 | |
| 웹 빌드 산출물 | `mbnas_web/dist/` | ● | ● | ● | 파일 목록 | gateway가 정적 제공. source map 없음 |
| 웹 소스 | `mbnas_web/{src,scripts}/`, `README.md`, `index.html`, `package.json`, `package-lock.json`, `vite.config.ts`, `tsconfig.json` | ● | ● | ● | 구조표(Module 열) | `scripts/`는 계약 타입 생성(`gen-types.mjs`)·번들 검사(`check-bundle.mjs`) 스크립트 |
| 웹 시험 | `mbnas_web/{tests,e2e}/`, `playwright*.config.ts` | ○ | ○ | ○ | — | 저장소에만 둔다 |
| 코어 | `mbnas_core/`(pyproject + src) | ● | ○ | ○ | Block·하위 패키지 이름·외부 관찰 동작만 | 소스·정책 데이터·DB 스키마는 owner 전용 |
| 프로젝트 wheel | `wheels/*.whl`(패키징 때 생성) | ● 5종 | ● 4종 | ● 4종 | 파일 목록 | 공개 ZIP에는 core wheel 없음 |
| 시험 코드 | `tests/`(전체) | ○ | ○ | ○ | — | 저장소에만 둔다 |
| 사용자 문서 | `docs/public/*.md` | ● | ● | ● | 전체 | 설치·기능·운영·문제 해결·보안 모델·호환성·API |
| 계약 변경 이력 | `docs/public/contracts/` | ○ | ○ | ○ | — | 버전별 계약 사본과 변환 스크립트. 저장소에만 둔다 |
| 등록 문서 | `docs/registration/`의 `PROGRAM_OVERVIEW.md`·`SYSTEM_STRUCTURE.md`·`INTERFACES.md`·`KEY_CONTRIBUTIONS.md`·`SUBMISSION_SCOPE_TABLE.md`·`FILE_LIST.md`와 `diagrams/` | ○ | ○ | ● | 전체 | `FILE_LIST.md`는 registration ZIP의 매니페스트에서 생성 |
| 내부 기록 | 설계·작업 기록, 등록 전 검토 기록(권리·라이선스 검토 포함) | ○ | ○ | ○ | — | 저장소에만 둔다 |
| 공개 배포 정의 | `deploy/public/` | ● | ● | ● | 전체 | Docker·compose·service·nginx 예시(owner 호스트에서 core 앞에 두는 proxy 예시 포함) |
| owner 배포 정의 | `deploy/owner/` | ● | ○ | ○ | — | |
| 설정 예시 | `config/examples/` | ● | △ | △ | 전체(공개분) | public·registration은 `core.env.example`, `owner-all-in-one.env.example` 제외 |
| 예제 | `examples/` | ● | ● | ● | 전체 | 합성 데이터와 예시 YAML |
| 운영 스크립트 | `scripts/{install,doctor,start,stop,verify}.sh` | ● | ● | ● | 사용법 | |
| 공개 보조 도구 | `scripts/public/`(pilot 부분집합 생성) | ● | ● | ● | 파일 목록 | `--source`·`--out` 필수 인자, 호스트 기본 경로 없음 |
| owner 운영 도구 | `scripts/owner/`의 `probe_registry.py`·`search_dry_run.py`·`smoke_end_to_end.py`, `scripts/runner/` | ● | ○ | ○ | — | registry probe, 후보 생성 CPU dry run, PILOT_SMOKE 종단 실행, 가중치 내려받기, 호스트 GPU 벤치마크, full-state 재개 동등성 비교 |
| 개발·패키징 도구 | `scripts/package.sh`, `scripts/dev/`, `scripts/owner/`의 나머지 | △ | ○ | ○ | — | owner는 `verify.sh --suite integration`이 쓰는 다중 프로세스 구동 스크립트 하나만 포함. 패키저·검사·문서 생성 도구는 저장소에만 둔다 |
| 루트 메타 | `README.md`, `VERSION`, `THIRD_PARTY_NOTICES.md`, `THIRD_PARTY_NOTICES_WEB.md`, `constraints.txt`, `.dockerignore` | ● | ● | ● | 전체 | `THIRD_PARTY_NOTICES_WEB.md`는 웹 번들의 npm 패키지 목록 |
| 라이선스 파일 | `LICENSE` | ○ | ○ | ○ | — | 넣지 않는다. 제3자 구성요소의 고지는 `THIRD_PARTY_NOTICES.md`·`THIRD_PARTY_NOTICES_WEB.md` |
| 설치 요구 파일 | `requirements-public.txt`, `requirements-cpu.txt` / `requirements-owner.txt` | ● / ● | ● / ○ | ● / ○ | 사용법 | |
| 워크스페이스 설정 | `pyproject.toml`(루트) | ○ | ○ | ○ | — | 저장소의 시험 실행 설정 |
| 빌드 메타 | `BUILD_INFO.json`, `PACKAGE_PROFILE.json`, `FILE_MANIFEST.jsonl`, `PACKAGE_CONTENTS.md` | ● | ● | ● | `FILE_LIST.md`로 요약 | 패키징 때 생성. `PACKAGE_CONTENTS.md`는 매니페스트를 사람이 읽는 표로 만든 것(경로·크기·sha256 앞부분·역할·라이선스, 역할별 합계) |
| 원본 사본·실행 상태 | `build/`, `runtime/`, `dist/`, `mbnas_web/runtime/` | ○ | ○ | ○ | — | 배포하지 않음 |
| 모델 가중치·DB·키·로그 파일 | `*.pt`, `*.onnx`, `*.sqlite3`, `*.pem`, `*.key`, `*.log` 등 | ○ | ○ | ○ | — | 모든 ZIP에서 거부 |

## 3. 어떤 공개 산출물에도 넣지 않는 것

- core 소스·core wheel·core를 import하는 파일·정책 데이터(prior·repair·handoff 원문)·core DB·owner 감사 자료
- 관리 키·서비스 토큰·서명키 등 실제 비밀 값(설치 시 생성, 예시 파일에는 예시 값만)
- 소유자 호스트 사실(내부 절대경로, 호스트 이름, 내부 IP)
- 시험 코드와 시험용 데이터·표식, 내부 기록(owner 배포본에도 넣지 않는다)

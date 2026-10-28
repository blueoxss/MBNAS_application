# SUBMISSION_SCOPE_TABLE — 배포·제출 범위표 (owner · public · registration)

문서 상태: 초안 · 2026-10-27 작성 · 2026-10-28 실측 갱신. 이 표는 설계 결정(패키지 분할·공개 경계)에 따른 범위를 실제 패키징 allowlist의 선택 결과로 맞춘 것이다. 실제 ZIP에 무엇이 들어가는지는 패키징 allowlist(`build/allowlists/<profile>.txt`)와 각 ZIP의 `FILE_MANIFEST.jsonl`(사람이 읽는 표: `PACKAGE_CONTENTS.md`)이 최종 기준이며, 이 표와 다르면 allowlist를 따른 뒤 이 표를 고친다. 등록 대상 저작물의 범위(공개 부분만인지, core를 포함한 전체인지)는 사용자가 정한다(`REVIEW_CHECKLIST.md` 4번).

## 1. 배포 프로필

| 프로필 | 용도 | core 연결 |
|---|---|---|
| owner-private | owner 호스트 전체 설치(core 포함). 외부 배포 금지 | 자체 core |
| public-runtime | 고객 호스트 설치(gateway·runner·웹) | **원격 core 필수** |
| registration-runtime | 등록 제출용: public-runtime + 등록 문서 | 원격 core 필수 |

ZIP 이름은 패키저(`scripts/package.sh`)가 정한다. 계약상 형식은 `mbnas_application-<version>-<profile>-runtime-<platform>.zip`이고 owner 프로필은 이름에 `owner-private`를 표기한다. ZIP의 최상위 디렉터리는 `mbnas_application/`이며, 각 ZIP 옆에 `.sha256` 파일이, ZIP 안에 `BUILD_INFO.json`·`PACKAGE_PROFILE.json`·`FILE_MANIFEST.jsonl`·`PACKAGE_CONTENTS.md`가 만들어진다(패키징 단계).

## 2. 구성 요소별 범위

기준(실측, 2026-10-28 10:11 KST): 그 시점의 `build/allowlists/{public,registration,owner}.txt`를 패키저(`scripts/dev/package_build.py`)의 파일 선택 함수로 그대로 적용한 결과(빌드 없이 선택만 함: 선택 파일 owner 2614 · public 468 · registration 477개, 빌드 때 생성되는 파일 제외)와 같은 날 임시 빌드의 ZIP 멤버 목록(owner 2459 · public 476 · registration 486개, 09:28 트리)이다. 근거 설계 결정: DD-A41·DD-A43·DD-A50·DD-A59·DD-A65. 표기: ● 그 경로의 파일이 모두 포함 · △ 일부(비고 참조) · ○ 없음. 파일 수 열은 owner / public / registration 순이며, 빌드 부산물(`__pycache__`, `*.egg-info`, `node_modules`)은 세지 않았다.

| 구성 요소 | 경로 | owner-private | public-runtime | registration-runtime | 파일 수 o / p / r | 등록 문서 기재 수준 | 비고 |
|---|---|:-:|:-:|:-:|---|---|---|
| 공통 계약 | `mbnas_contracts/`(pyproject + src) | ● | ● | ● | 28 / 28 / 28 | 전체 | OpenAPI 계약 파일 포함(공개 API 1.5.0, runner 프로토콜 1.1.0) |
| 모델 런타임 | `mbnas_model_runtime/` | ● | ● | ● | 50 / 50 / 50 | 전체 | ETRI 연산자 포함 — 권리 확인(`RIGHTS_REVIEW.md` 2) |
| 실행기(runner) | `mbnas_runner/` | ● | ● | ● | 24 / 24 / 24 | 전체 | |
| 게이트웨이 | `mbnas_server/` | ● | ● | ● | 31 / 31 / 31 | 전체 | |
| 웹 빌드 산출물 | `mbnas_web/dist/` | ● | ● | ● | 3 / 3 / 3 | 파일 목록 | gateway가 정적 제공. source map 없음 |
| 웹 소스 | `mbnas_web/{src,scripts,tests,e2e}/`, `README.md`, `index.html`, `package.json`, `package-lock.json`, `vite*.config.ts`, `tsconfig.json`, `playwright*.config.ts` | ● | △ | △ | 151 / 147 / 147 | 구조표(Module 열) | DD-A41로 포함. public·registration 제외 4개: 적합도 수식을 담은 `tests/fitness_oracle.ts`와 이를 쓰는 `tests/fixtures_oracle.ts`·`tests/fixtures_oracle.test.ts`·`e2e/weights.spec.ts`(DD-A43, allowlist `[deny-importers]`). `node_modules/`는 어디에도 없음 |
| 코어 | `mbnas_core/` | ● | ○ | ○ | 93 / 0 / 0 | Block·하위 패키지 이름·외부 관찰 동작만 | 소스·정책 데이터·DB 스키마는 owner 전용 |
| 프로젝트 wheel | `wheels/*.whl`(패키징 때 생성) | ● 5종 | ● 4종 | ● 4종 | (빌드 때 생성) | 파일 목록 | 공개 ZIP에는 core wheel 없음. 빌드가 wheel마다 내용을 소스와 sha256으로 대조 |
| 공개 시험 | `tests/public/` | ● | △ | △ | 85 / 79 / 79 | 파일 목록 | DD-A41로 포함(`verify.sh --suite unit`용). public·registration 제외 6개: `infra/test_no_host_facts.py`(호스트 사실 문자열을 시험 값으로 가짐), `packaging/**` 5개(owner 패키저 시험). `contracts/test_contract_history.py`·`tools/smoke_api_only.py`는 호스트 사실을 지운 뒤 포함된다 |
| 공용 시험 도구 | `tests/_shared/` | ● | △ | △ | 12 / 6 / 6 | 파일 목록 | public·registration은 core를 쓰지 않는 6개(`conftest_common`, `datasets`, `fake_core`, `gpu_guard`, `stub_core`, `stub_handlers`)만. `harness`, `qa_handlers`, `combined_fixtures`, `operators_parity`, `remote_handlers`, `runner_sim`은 owner 전용 |
| owner 시험 | `tests/owner/` | ● | ○ | ○ | 149 / 0 / 0 | — | |
| 공개 문서 | `docs/public/` | ● | ● | ● | 43 / 43 / 43 | 전체 | 계약 이력과 migrate 스크립트 포함 |
| 등록 문서 | `docs/registration/` | ○ | ○ | ● | 0 / 0 / 9 + 1 | 전체 | `FILE_LIST.md`는 소스의 자리표시를 쓰지 않고 registration ZIP의 매니페스트로 다시 생성(+1) |
| 내부 문서 | `docs/internal/` | △ | ○ | ○ | 1853 / 0 / 0 | — | owner도 누출 검사 manifest(`docs/internal/canary/`)와 원본 스냅샷 provenance 매니페스트 4개는 제외(전체 1858개 중; 증거가 늘면 이 수도 늘어난다). owner ZIP에는 빌드가 만든 wheel 목록 `docs/internal/evidence/package_build/owner_wheels.txt`가 더해진다 |
| 공개 배포 정의 | `deploy/public/` | ● | ● | ● | 7 / 7 / 7 | 전체 | Docker·compose·service·nginx 예시(owner 호스트에서 core 앞에 두는 proxy 예시 포함) |
| owner 배포 정의 | `deploy/owner/` | ● | ○ | ○ | 5 / 0 / 0 | — | |
| 설정 예시 | `config/examples/` | ● | △ | △ | 7 / 5 / 5 | 전체(공개분) | public·registration은 `core.env.example`, `owner-all-in-one.env.example` 제외 |
| 예제 | `examples/` | ● | ● | ● | 29 / 29 / 29 | 전체 | 합성 데이터와 예시 YAML |
| 운영 스크립트 | `scripts/{install,doctor,start,stop,verify}.sh` | ● | ● | ● | 5 / 5 / 5 | 사용법 | |
| 공개 보조 도구 | `scripts/public/`(pilot 부분집합 생성) | ● | ● | ● | 1 / 1 / 1 | 파일 목록 | DD-A59: 호스트 기본 경로를 없애고(`--source`·`--out` 필수) 공개 시험이 쓰는 도구로 동봉 |
| 기타 스크립트 | `scripts/package.sh`, `scripts/{dev,owner,runner}/` | ● | ○ | ○ | 26 / 0 / 0 | — | 패키저·누출 검사 도구·등록 문서 생성기·npm 고지 생성기(`gen_npm_notices.py`)·호스트 GPU 벤치마크, owner 전용 |
| 루트 메타 | `README.md`, `VERSION`, `LICENSE`, `THIRD_PARTY_NOTICES.md`, `THIRD_PARTY_NOTICES_WEB.md`, `constraints.txt`, `.dockerignore` | ● | ● | ● | 7 / 7 / 7 | 전체 | `LICENSE`는 라이선스 결정 전의 자리표시(권리 유보, 라이선스 부여 없음, DD-A65), `THIRD_PARTY_NOTICES_WEB.md`는 웹 번들의 npm 패키지 목록. 두 파일이 없으면 빌드가 실패한다 |
| 권리 상태 메모 | `LICENSE_STATUS.md` | ● | ○ | ○ | 1 / 0 / 0 | — | 미공개 연구 원고 관련 문구 포함 → owner 전용(DD-A41). 공개본의 라이선스 안내는 `LICENSE`, `THIRD_PARTY_NOTICES.md`, `THIRD_PARTY_NOTICES_WEB.md`와 `docs/public/INSTALL.md`의 라이선스 절 |
| 설치 요구 파일 | `requirements-public.txt`, `requirements-cpu.txt` / `requirements-owner.txt` | ● / ● | ● / ○ | ● / ○ | 2+1 / 2 / 2 | 사용법 | |
| 워크스페이스 설정 | `pyproject.toml`(루트) | ● | ● | ● | 1 / 1 / 1 | — | pytest 설정(공개 시험 실행에 필요). 이전 판 표는 public·registration ○였으나 실제 allowlist는 포함한다 |
| 빌드 메타 | `BUILD_INFO.json`, `PACKAGE_PROFILE.json`, `FILE_MANIFEST.jsonl`, `PACKAGE_CONTENTS.md` | ● | ● | ● | (빌드 때 생성) | `FILE_LIST.md`로 요약 | 패키징 때 생성. `PACKAGE_CONTENTS.md`는 매니페스트를 사람이 읽는 표로 만든 것(경로·크기·sha256 앞부분·역할·라이선스, 역할별 합계) |
| 원본 사본·실행 상태 | `build/`, `runtime/`, `dist/`, `mbnas_web/runtime/` | ○ | ○ | ○ | 0 / 0 / 0 | — | 배포하지 않음 |
| 모델 가중치·DB·키·로그 파일 | `*.pt`, `*.onnx`, `*.sqlite3`, `*.pem`, `*.key`, `*.log` 등 | ○ | ○ | ○ | — | — | 모든 ZIP에서 거부(owner의 `docs/internal` 증거 로그만 예외) |

**남은 확인 사항**

1. core를 import하는 파일은 패키저의 비공개 패키지 검사에서 위반으로 거부되므로 public·registration ZIP에 들어갈 수 없다(DD-A43: core를 import하는 시험은 번들 제외 유지).
2. 표의 최종 기준은 각 ZIP의 `FILE_MANIFEST.jsonl`이다. allowlist가 바뀌면 이 표를 다시 대조한다.

## 3. 어떤 공개 산출물에도 넣지 않는 것

- core 소스·core wheel·정책 데이터(prior·repair·handoff 원문)·core DB·owner 감사 자료
- 관리 키·서비스 토큰·서명키 등 실제 비밀 값(설치 시 생성, 예시 파일에는 placeholder만)
- 소유자 호스트 사실(내부 절대경로, 호스트 이름, 내부 IP)
- 누출 검사용 표식과 그 manifest

이 조건은 누출 검사(T8)로 확인했다(2026-10-28, PASS): 최종 배포 ZIP 3종의 멤버·내용 검사(표식·비밀 값·호스트 사실 0, 공개·등록 ZIP에 core 없음)와 최종 배포본의 응답·로그·프로세스 환경 검사. 그 뒤 바뀐 파일은 배포 ZIP을 다시 빌드할 때 같은 검사를 다시 거친다.

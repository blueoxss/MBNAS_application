# 다음 과제 운영 계획 — 등록·배포 소스 정리 및 별도 저장소 이관 (설계자 초안 v1, 2026-10-28 09:4x)

원문: `docs/internal/NEXT_TASK_MIGRATION_PROMPT.md` (사용자 10-28 03:4x). 시작 조건: 현재 과제의 P-final-2·재채점·최종 보고가 끝난 뒤(TASK_STATE 참조). 이 문서는 설계자(최상위 모델)의 계획이며, 구현·수집은 하위 모델 에이전트가 수행한다.

## 1. 범위 한 문장
mbnas_application(로컬 트리 = SOURCE_REPO `blueoxss/MBNAS_application`의 실제 소스)을 읽기 전용으로 전수 조사해, 별도 WORK_ROOT 사본에서 주석·문체·죽은 코드만 동작 보존 범위로 정리하고, 프로필별 allowlist staging·등록용 프로그램 목록(XLS)·통계·검증·이관 계획을 만든 뒤, **사용자 승인 후에만** 확정된 이름의 private 저장소에 최초 push 한다.

## 2. 경로 (겹침 금지)
| 이름 | 경로 | 성격 |
|---|---|---|
| SOURCE_ROOT | `/home/mgjeong/Desktop/etri_hd/mbnas_application` | 원본(현재 과제의 최종 트리). 읽기 전용. git 조작 금지 |
| MIGRATION_ROOT | `/home/mgjeong/Desktop/etri_hd/mbnas_migration_ver1/` | 이 과제의 모든 산출물 (원본 밖) |
| BASELINE | `MIGRATION_ROOT/baseline/` | 원본 전수 SHA-256 manifest + git HEAD/dirty/untracked 기록(사본 아님, 해시만) |
| WORK_ROOT | `MIGRATION_ROOT/work/` | 편집 사본(allowlist 후보 전체 복사 후 주석·코드 정리) |
| STAGING_ROOT | `MIGRATION_ROOT/staging/{owner,public,registration}/` | 프로필별 allowlist 결과 |
| TARGET_WORK_ROOT | `MIGRATION_ROOT/target/` | 이관 파일만, 독립 `git init`(원본 .git 미복사) |
| DELIVERABLES | `MIGRATION_ROOT/deliverables/` | 14절 산출물(CSV/MD/JSON/XLS/ZIP/SHA256SUMS) |
| TOOLS_VENV | `MIGRATION_ROOT/.tools_venv/` | xlwt/xlrd/openpyxl 등 과제용 도구(앱 venv 오염 금지) |

규칙: 원본 내부에 아무것도 만들지 않는다(현재 과제처럼 `docs/internal`에 기록하던 관행은 **이 과제에서는 중단**; 로그·결정도 MIGRATION_ROOT/review/에 둔다). 단, 설계자 TASK_STATE/메모리의 포인터 갱신은 예외.

## 3. 사전 사실(10-28 09:4x 설계자 확인; 과제 시작 시 재확인)
- `gh` CLI 없음. GH_TOKEN/GITHUB_TOKEN 환경변수 없음 → 저장소 **생성**은 이 세션에서 불가. 승인 단계에서 사용자가 (a) 직접 빈 private 저장소를 만들거나 (b) 토큰을 제공해야 한다. push 자격증명은 git credential helper 유무로 판단(값 출력 금지).
- 원격 조회는 `git ls-remote`(GIT_TERMINAL_PROMPT=0)와 비인증 REST(`api.github.com/repos/...` HTTP 코드)로만. private 저장소는 404로 보이므로 '없음'과 '접근 불가'를 구분해 보고한다. **10-28 09:4x 조회 결과:** `MBNAS_application` main = c9a2940852ee669a28ca46709147cf80acd0863b(README만, 변동 없음; HTTP 200); `MBNAS_application_ver1`·`MBNAS_application_ver1.0` 모두 ls-remote 'Repository not found' + HTTP 404 → 존재하지 않거나 접근 불가(둘 다 신규 생성 대상 후보). git credential helper = `store`(`~/.git-credentials` 존재, 값 미열람) → 사용자가 빈 저장소를 만들면 push 자체는 가능할 수 있음(실행 전 재확인).
- XLS 원본 양식("002 프로그램 목록 - 프로파일.xls")은 로컬에서 미확보(Desktop/Downloads/Documents 검색 0건) → 동일 7열 CSV/XLSX + 실제 BIFF XLS(xlwt)로 생성하고 '원본 양식 미적용'을 명시. 양식 확보 시 재적용.
- 앱 venv에 xlwt/openpyxl 없음 → TOOLS_VENV에 설치(PIP_CONFIG_FILE=/dev/null).
- 사용자 로컬 git: 커밋 3e5bbecb9가 runtime/·build/·dist/owner·docs/internal 추적(DD-A67, 미push). 기준선 기록에 그대로 적는다(정리하지 않는다).

## 4. 파도(에이전트; 소유 파일 분리; 모두 CPU, GPU 금지)
| 파도 | 담당(모델) | 입력 | 산출 |
|---|---|---|---|
| M0 기준선 | opus | SOURCE_ROOT, 원격 | `baseline/SOURCE_MANIFEST.json`(전수 path/bytes/sha256/type/encoding/newline/physical_lines), git HEAD/branch/dirty/untracked 기록, 원격 조회 결과, **FILE_INVENTORY.csv 초안**(type/origin/role 열) |
| M1 의존성 | opus | M0 inventory, 코드 | dependency_evidence 열, `PROFILE_ALLOWLISTS/{owner,public,registration}.txt`(현재 build/allowlists 실측 + 호출 근거), `EXCLUSION_RULES.csv`, `EXCLUSION_CANDIDATES.csv` |
| M2 공개 경계·기여 대응 | opus | 코드 | 6 패키지 실행 주체·의존 방향표, 기여①②→구현 파일/심볼→제출 파일 대응표, 코어 포함 범위 '확인 필요' 항목 |
| M3 주석·문체·코드 정리 | opus ×2(패키지 분할: core+contracts+runner / server+model_runtime+web+scripts) | WORK_ROOT | `COMMENT_AND_CODE_CHANGES.md`, 동작 보존 검증(lint/typecheck/unit/integration/smoke 각 명령·종료코드) |
| M4 출처·권리 | opus | 코드·NOTICES | `LICENSE_AND_PROVENANCE.md`(자체/외부/수정/생성; 원 URL·버전; AI 생성 이력 내부 보존) |
| M5 목록·통계·XLS | opus | 확정 registration 집합 | `REGISTRATION_SOURCE_FILES.csv`, `METRICS.json`, `프로그램목록_프로파일_MBNAS.xls`(BIFF, SUM 수식 마지막 행까지, 재열기 검증) + CSV/XLSX, 창작자·완료일은 '확인 필요' |
| M6 staging·대상·ZIP | opus | WORK_ROOT, allowlists | STAGING_ROOT, `TARGET_ALLOWLIST.csv`, TARGET_WORK_ROOT(독립 git init, 비밀·원본 .git 없음), 독립 설치·smoke, ZIP + `SHA256SUMS.txt`(Linux 생성 명시, 190MB 분할 규칙 기록), `MIGRATION_PLAN.md` 초안 |
| E 평가 | opus ×12(관점별 독립) + 설계자 | 산출물 | `review/CRITERIA/Cxx/EVAL_n.md`, 설계자 adjudication → `REVIEW_LOG.md` |
| R 재정비 | 설계자 | 라운드 결과 | 기준 재정의(가중치·앵커) → 다음 라운드 |

순서: M0 → (M1 ∥ M2 ∥ M4) → M3(두 에이전트) → (M5 ∥ M6) → E(12) → 수정 → E 재채점 … 총 5라운드 한도. 이전 라운드 최종본이 다음 입력.

## 5. 기준셋 초안 (각 4×25; 통과 = 총점 >90 및 Blocker/Critical 0 및 필수 검증 통과) — 라운드마다 설계자가 재정의
| 셋 | 관점 | 기준 a | b | c | d |
|---|---|---|---|---|---|
| C01 | 접근/버전/식별 | SOURCE_REPO 원격 읽기 전용 조회 기록(branch/commit/tree truncated) | 로컬 HEAD/dirty/untracked + 전수 SHA 기준선 | 대상명 ver1/ver1.0 존재·권한 조회와 모호성 보고 | 버전 1.0 vs 프로그램 0.1.0 정합 판단·문서 일치 |
| C02 | 요구/프로필/범위 | 3 프로필 정의 실측 근거 | TARGET_ALLOWLIST 단일 프로필/명시 구획, 코어 자동 포함 없음 | MIGRATION_PLAN 범위·승인 어휘 정확 | 등록명/표시명/영문명 일관 |
| C03 | 의존성 | Python import/entry point/package_data/resources 추적 | 웹 package.json/lock/vite/dist 대조 | 스크립트가 읽는 tests/fixture/requirements/wheels 유지 | 독립 설치(원본 PYTHONPATH/editable 없음) 실측 |
| C04 | 공개 경계 | 6 패키지 실행 주체·의존 방향 코드 근거 | 기여→구현→제출 대응표 | public에 core/비밀 없음 스캔 | 코어 포함 범위를 '확인 필요'로 분리 |
| C05 | 제외 정책 | 일반 규칙/실제 후보 분리 | 일괄 삭제 금지 항목 개별 판정 | staging allowlist 결과·원본 보존 | 복구 위치·유지 예외 기록 |
| C06 | 출처/권리 | 자체/외부/수정/생성 구분 | 라이선스/NOTICE 보존 | AI 생성·인간 검토 이력 내부 보존 | 과대 주장 없음(Ultralytics 등) |
| C07 | 주석 문체 | 문제 유형 탐지 커버리지 | 수정표(파일·행·유형·변경·영향) | 유지 규칙 준수 | TODO/FIXME 정직 처리 |
| C08 | 동작 보존 | 공개 API/CLI/설정/오류코드/DB 불변 | NAS 의미(seed/평가 OFF/승급/예산/pause·resume) 불변 | shebang/noqa/pragma/런타임 docstring 영향 확인 | 주석-only vs 동작 변경 분리 + 실제 테스트 실행 결과 |
| C09 | 목록/통계 | 물리행 규칙(strict decode + splitlines) | 바이너리/판독 실패 분리 | 4 지표 분리 + 외부/생성/자체 구분 | 창작자·완료일 자동 대입 금지 |
| C10 | XLS 서식 | 원본 양식 확보 여부 기록 | 7열·SUM 마지막 행까지(회귀 검증) | 숫자/날짜 형식·렌더링 잘림 확인 | 실제 BIFF 생성·재열기 또는 제한 명시 |
| C11 | 패키징/Windows | manifest vs staging vs 해제본 대조 | 경로 탈출/symlink/대소문자/예약어/길이/LFS | owner 실행·public 원격 core 실행 분리 검증 | Windows ZIP 상태 정직 표기 |
| C12 | 재현/이관/보존 | SOURCE_MANIFEST/VERIFY_RESULTS 재현 가능 | TARGET_WORK_ROOT 독립·비밀/원본 .git 없음 | 원본 HEAD/dirty/hash 불변 확인 | 완료 어휘 정확(원격 후 검증은 push 후에만) |

평가자 다양성: 관점당 1명(opus)이 독립 채점하고, Blocker/Critical 판정은 설계자가 반박 가능성을 검토해 확정한다(사용자 지시: "서로 다른 관점의 복수 평가 모델"). 모델 라우팅은 `review/MODEL_ROUTING.md`에 기록(최상위 모델 'fable5.1 ultra'는 평가 모델로 노출되지 않으므로 설계자=현재 세션 모델, 평가·수집=opus).

## 6. 승인 지점(사용자)
1. 저장소명(`MBNAS_application_ver1` vs `_ver1.0`)과 private 설정, 포함 프로필(기본 registration 단일 프로필 제안; owner 코어 미포함) — B절 보고서로 한 번에 요청.
2. 생성 수단: gh 없음 → 사용자가 빈 private 저장소 생성(자동 README/라이선스 초기화 끄기) 또는 토큰 제공.
3. 창작자·창작완료일 확인값, 라이선스/권리자(현재 LICENSE 자리표시) — XLS·README에 '확인 필요'로 남기고 요청.
4. Windows ZIP: 이 호스트는 Linux → Linux 생성본으로 제공하고 Windows 재압축은 미수행으로 표기.

## 7. 완료 기준(체크리스트)
- [ ] 14절 산출물 전부 DELIVERABLES에 존재(없는 자료는 BLOCKED/NOT_RUN 표기, 가짜 행 없음)
- [ ] C01~C12 각 >90, Blocker/Critical 0 (C12 원격 후 검증 항목은 push 전까지 '승인 대기'로 분리 표기)
- [ ] 원본 불변: 기준선 SHA 전수 재대조 0 차이, git HEAD/dirty 동일
- [ ] TARGET_WORK_ROOT 독립 설치·smoke PASS, 비밀 스캔 0, 원본 .git 미포함
- [ ] 상태 어휘: LOCAL_PREPARED → READY_FOR_APPROVAL(보고서 제출) → (승인 후) PUSHED_PENDING_VERIFICATION → MIGRATED_VERIFIED

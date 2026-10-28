# FINAL_REPORT — MBNAS Research Console 0.1.0 (지침 §15 최종 보고) — 초안 2026-10-28 12:2x KST (설계자)

> 상태: **초안**. `[확정 대기]` 표시는 P1(owner 재빌드·T6)·P2(T4/T5 재실행)·재채점(C10/C11/C12)·추록 결과로 채운다. 이 문서는 owner 전용 내부 기록이며 public/registration ZIP에 포함되지 않는다.

## 1. 상태 판정 (각각 별도; §15)

| 상태 | 판정 | 근거 |
|---|---|---|
| IMPLEMENTATION_COMPLETE | **충족(DD-A38 범위; 1.5.1 이월 목록 명시)** | 수직 경로 전부 구현·회귀 통과(트리 `1b4bd1a4…`: public 1190/0·owner 1601/0은 직전 트리 `f00987be…` 기준, 최종 트리 재실행 결과 [확정 대기]). 이월: RELEASE_1_5_1_CARRYOVER.md 11항목 |
| SOURCE_INDEPENDENCE_VALIDATED | **충족** | T6 rerun2(bwrap: 원본 트리·etri_nas·/home 불가시, loopback) public/registration PASS; import closure 0; module_origins 전부 ROOT; T2 결정 동등 10/10; provenance 603/603·placement 221/221 |
| OWNER_BUNDLE_VALIDATED | [확정 대기: P1 T6 owner + P2 T4 on the DD-A73 owner ZIP] | 이전 ZIP `79cce02a…` T4 FINAL PASS(bwrap 전체 통합). |
| PUBLIC_ZIP_VALIDATED | **충족** | public ZIP `bc0500bd…`만 설치한 docker 이미지(`a97cb3dd…`) + host의 실제 원격 owner core 연동: 20개 점검 중 19 PASS(T5 rerun2), 1건은 core 결함(DD-A73, 수정) → 해당 단계 재실행 [확정 대기] |
| REGISTRATION_ZIP_VALIDATED | **충족** | registration ZIP `dea510df…` bwrap 설치·실행·verify PASS, 범위 대조(FILE_LIST check OK, package 485/485, SUBMISSION_SCOPE_TABLE 실측). 기관 등록 승인과 별개 |
| GPU_PATH_VALIDATED | **충족(한계 명시)** | T7: 승인 창(DD-A35) 트리 `9dafe4d0…` 62/62 PASS(실 GPU 학습·평가·재개·취소·SIGKILL 재시도·다운로드·benchmark). 한계: TensorRT BLOCKED_BY_ENV, 컨테이너 GPU job 미검증, 이후 변경은 CPU 검증 |
| ADMIN_BOUNDARY_VALIDATED | **BLOCKED_BY_ENV — 미충족** | 별도 관리자 host 없음(같은 host 프로세스·계정 분리만 검증). 조건 4개: VALIDATION_REPORT ADMIN 행 |
| OFFLINE_INSTALL_VALIDATED | **충족(public `requirements-public.txt` + cu124 wheelhouse 경로)** | `unshare -rn`에서 install.sh/pip --no-index/pip check exit 0. `requirements-cpu.txt`는 오프라인 불가(온라인/Docker), registration 오프라인 설치 미실행 |
| RELEASE_CANDIDATE_READY | [확정 대기] | 12 기준셋 전부 >90·Blocker/Critical 0 + owner 검증 완료 시 |
| HIGH_MODEL_REVIEW_PENDING | **해당** | `fable5.1 ultra` 식별자 미노출(MODEL_ROUTING.md) |
| RELEASE_REVIEW_PENDING | **해당** | 라이선스·권리자 미정(LICENSE 자리표시), 논문 코드 제공 약속, AGPL 범위 검토 |
| BLOCKED_BY_ENV | **해당 항목** | TensorRT, 별도 관리자 host, 컨테이너 GPU job |

## 2. 실제 TARGET_ROOT·버전·모델
- TARGET_ROOT `/home/mgjeong/Desktop/etri_hd/mbnas_application`, VERSION 0.1.0, 표시명 MBNAS Research Console / MBNAS 모듈형 신경망 구조 탐색·실험 통합관리 프로그램.
- 공개 계약 1.5.0(`openapi.json` sha256 `70e0cffa…`), runner 프로토콜 1.1.0(`openapi_runner.json` `0f3dac4d…`), DB 스키마 v6, 최종 소스 트리 해시(5 패키지 221 파일) `sha256:1b4bd1a4a45196e3c558d62507f109301973eddc180df5968ed7014a72e6708c`, public 4패키지 `9859adb2…`.
- 원본: `etri_nas` HEAD 18c4815(work/mbnas-integration-20261021) 스냅샷 603 파일(`build/source_snapshot`, provenance manifest).
- 모델 라우팅(MODEL_ROUTING.md): 설계·판정 = 세션 모델 `claude-fable-5-1`('ultra' 미노출 → HIGH_MODEL_REVIEW_PENDING); 구현·수집 = `claude-opus-5-5`(일부 `claude-sonnet-5-5`); 평가자 = opus 12명(관점별) + 설계자 adjudication.

## 3. 보존 / 신규 / 미완료
- **보존(T2 동등성 10/10, 0 MISMATCH)**: GA·IndependentRandom·prior 탐색 의미, 구조 gate(학습 전 구조 정합성 검사), source rung·동률 규칙, baseline/final 재학습, seed/RNG, 평가 OFF 의미(무작위 생성→구조 검사→유효 후보 저장, 미평가 지표 null).
- **신규**: core(`/core/v1`)·gateway(`/api/v1`)·runner 분리와 lease fencing·멱등 commit, 계약 1.5.0 투영(불투명 id·상대경로), artifact store·checkpoint id 다운로드·export zip, 보존 정책 prune 왕복(runner 프로토콜 1.1.0), CPU fleet(NOT_EVALUATED 의미), 설정 개정(original/selected/executed/generated), 웹(블록 표·개정·diff·비교·ON/OFF·baseline), 패키징 3 프로필(allowlist·manifest·canary·재현 빌드), 운영 스크립트(install/doctor/start/stop/verify/package).
- **미완료·이월**: RELEASE_1_5_1_CARRYOVER.md(교차 runner 전송·affinity, 유휴 runner prune 전달, prune_requests 조회 route, verify.sh base_env scrub, 테스트 이식 3건, 웹 툴팁 2건, PRUNED→PRESENT 상태 우선순위, TensorRT, 별도 host).

## 4. 실행 가능한 명령 (docs/public/INSTALL.md·OPERATIONS.md)
```bash
# 설치 (표준: pip requirements; 또는 Docker compose; 또는 래퍼 install.sh)
cd <ROOT> && python3.10 -m venv .venv && .venv/bin/python -m pip install -r requirements-public.txt   # owner: requirements-owner.txt
bash scripts/install.sh --profile public [--offline --wheelhouse <dir>] [--requirements requirements-cpu.txt]
docker compose -f deploy/public/docker-compose.yml build && docker compose -f deploy/public/docker-compose.yml up -d
# 점검·기동·검증·정지
bash scripts/doctor.sh --profile public --strict
bash scripts/start.sh  --profile public            # gateway 8710 + runner (owner: --profile owner → core 8700 + owner worker + gateway + runner)
bash scripts/verify.sh --profile public --suite unit|integration|relocation|package|gpu
bash scripts/stop.sh   --profile public
# 복구: 일시정지 run 재개(최근 full-state checkpoint) — POST /api/v1/runs/{id}/resume (웹 Resume 버튼); runner SIGKILL 후 lease 만료 → attempt+1 자동
# owner 전용: 자격 발급 mbnas-core-principal create --kind server|runner, 패키징 bash scripts/package.sh --profile public|registration|owner
```

## 5. 원본 의존성 차단 근거
- 입력은 `build/source_snapshot`(603 파일, 파일별 provenance·sha)만; 원본 트리 쓰기 0(git index/HEAD 불변 확인 기록).
- `scripts/dev/check_import_closure.py` 0 위반(1 문서화 예외); public ZIP에 `mbnas_core` 없음(이미지 내 ModuleNotFoundError·find_spec None·/proc maps 0건).
- T6 rerun2: 공백 포함 새 경로, 원본 트리·etri_nas·/home 불가시(bwrap), PYTHONPATH 미설정, editable 0, `module_origins` 전부 ROOT.
- start.sh가 호출자 PYTHON*/OLDPWD를 제거(C09 D1), doctor `env:<role>` 검사.

## 6. ZIP 3종 (절대경로·SHA-256·파일 범위·core 연결 조건)
| 프로필 | 절대경로 | sha256 | 바이트/멤버 | 범위 | core 연결 |
|---|---|---|---|---|---|
| public | `/home/mgjeong/Desktop/etri_hd/mbnas_application/dist/public/mbnas_application-0.1.0-public-runtime-linux_x86_64.zip` | `bc0500bdfa43ddf5fb74bde4cf51a967e06f45bd2b901ba26da0794c1f0853d3` | 2,716,913 / 476 | gateway·runner·contracts·model_runtime·web dist·tests/public·docs/public·wheels 4 | **원격 core 필요**(`MBNAS_GW_CORE_URL`+서비스 토큰; core 없으면 503 CORE_UNAVAILABLE, 로컬 fallback 없음) |
| registration | `/home/mgjeong/Desktop/etri_hd/mbnas_application/dist/registration/mbnas_application-0.1.0-registration-runtime-linux_x86_64.zip` | `dea510dfb54dd85da556ac38423f747a0a7d1afbff7398c501127bd0f8b92f6e` | 2,792,584 / 486 | public + docs/registration | 동일(원격 core 필요) |
| owner | `/home/mgjeong/Desktop/etri_hd/mbnas_application/dist/owner/mbnas_application-0.1.0-owner-private-runtime-linux_x86_64.zip` | [확정 대기: DD-A73 재빌드본] | [확정 대기] | public + mbnas_core + owner worker + tests/owner + docs/internal + wheels 5 | 자체 core(all-in-one) |
- 외부 `.sha256` 파일 동봉, FILE_MANIFEST.jsonl(상대경로/bytes/sha256/role/origin/licence), PACKAGE_PROFILE.json/BUILD_INFO.json(소스 트리 해시), PACKAGE_CONTENTS.md, LICENSE(자리표시), THIRD_PARTY_NOTICES(.md/_WEB.md). 재현 빌드(2회 바이트 동일). 캔ary·호스트 사실 누출: public/registration 0건.

## 7. 12 기준셋 회차/점수/수정/시험 근거 (REVIEW_LOG.jsonl, evidence/CRITERIA/)
| 셋 | 1차 | 2차 | 3차 | 최종 | 주요 수정 |
|---|---|---|---|---|---|
| C01 사실·기준선 | 100 | 100 | — | PASS | provenance 게이트 |
| C02 위협·비밀 | 85 | 100 | — | PASS(추록 [확정 대기]) | RunnerSettings/canary 테스트, 문구, ADMIN 행, nginx owner 차단 |
| C03 NAS 의미 | 95 | 95 | — | PASS | T2 행, NOT_EVALUATED 제외 테스트 |
| C04 분산 상태 | 95 | 95 | 100 | PASS | DD-A64 reclaim, prune 상태 기계 |
| C05 입력·인증 | 100 | 100 | — | PASS | 리댁션 테스트 |
| C06 복구·재현 | 100 | 100 | — | PASS | docstring 정정 |
| C07 UI·기능 | 95 | 100 | — | PASS(추록 [확정 대기]) | 블록 표 링크, 툴팁, 실스택 V4B_FINAL2 |
| C08 원격 산출물 | 80 | 95 | — | PASS(추록 [확정 대기]) | prune 왕복(T3-34), CRLF 해시 |
| C09 설치 폐쇄성 | 90 | 100 | — | PASS(추록 [확정 대기]) | start.sh scrub, verify origins, wheel gate |
| C10 ZIP 배포 | 90 | [확정 대기] | | | PACKAGE_CONTENTS, licence 열, 재빌드 |
| C11 실구동·등록 | 75 | [확정 대기] | | | 보고서 행, LICENSE, npm notices, DD-A73 |
| C12 운영·최종회귀 | 70 | [확정 대기] | | | 트리 해시 .meta, MODEL_ROUTING, ops 스크립트 |

## 8. 남은 최소 다음 행동
1. **사용자(git)**: 로컬 커밋 `3e5bbecb9`는 runtime/·build/·dist/owner·docs/internal을 추적 → **push 금지**, `docs/internal/GIT_HYGIENE_URGENT.md` 절차로 재커밋(DD-A67).
2. **사용자(권리)**: 자체 코드 라이선스·권리자 결정 → LICENSE 자리표시 교체; 논문 각주 코드 제공 약속 처리(RIGHTS_REVIEW Q7-3).
3. **환경**: 별도 관리자 host가 생기면 T5/T8을 고객 root에서 재실행(ADMIN_BOUNDARY); 승인된 GPU 창에서 TensorRT·컨테이너 GPU job.
4. **정리(승인 필요)**: `runtime/test_root/*` 스크래치 트리(~60 GB)와 docker 이미지 `mbnas-public-cpu:{t5,final,g2t7,final2}` 삭제.
5. **다음 과제**: 등록·배포 소스 정리 및 별도 저장소 이관(NEXT_TASK_MIGRATION_PROMPT.md; 운영 계획 MIGRATION_TASK_OPERATIONS_PLAN.md) — 저장소명(ver1/ver1.0)·private·포함 프로필 승인과 빈 저장소 생성(이 호스트에 gh 없음)이 필요.

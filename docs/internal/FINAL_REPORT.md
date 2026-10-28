# FINAL_REPORT — MBNAS Research Console 0.1.0 (지침 §15 최종 보고) — 초안 2026-10-28 11:5x KST (설계자)

> 상태: **초안**. `[확정 대기]` 표시는 P1(owner 재빌드·T6)·P2(T4/T5 재실행)·재채점(C10/C11/C12)·추록 결과로 채운다. 이 문서는 owner 전용 내부 기록이며 public/registration ZIP에 포함되지 않는다.

## 1. 상태 판정 (각각 별도; §15)

| 상태 | 판정 | 근거 |
|---|---|---|
| IMPLEMENTATION_COMPLETE | **충족(DD-A38 범위; 1.5.1 이월 목록 명시)** | 수직 경로 전부 구현·회귀 통과(릴리스 후보 트리 `8fc93f2a…` gates_r4: public 1190/0, owner 1686/0; 이력 gates_r3/r2/P-final-2 모두 0 실패). 이월: RELEASE_1_5_1_CARRYOVER.md 11항목 |
| SOURCE_INDEPENDENCE_VALIDATED | **충족** | T6 rerun2(bwrap: 원본 트리·etri_nas·/home 불가시, loopback) public/registration PASS; import closure 0; module_origins 전부 ROOT; T2 결정 동등 10/10; provenance 603/603·placement 221/221 |
| OWNER_BUNDLE_VALIDATED | **충족** | 코드 최종 owner ZIP `9f03c484…`: T6 attempt4 PASS(unit 2829/0, 통합 17/17, relocation 8/8, package 3692/3692, cwd `/` 재호출), P2 T4 attempt_dd_a75 PASS(ETRI 후보 200→COMPLETED, 공개 응답 경로 조각 0, verify 통합 17/17, DB v6), v5→v6 이관 PASS(이전 ZIP). 한계: 공백 없는 긴 설치 경로 entry_points 오탐(1.5.1 #13) |
| PUBLIC_ZIP_VALIDATED | **충족** | public ZIP `bc0500bd…`만 설치한 docker 이미지(`a97cb3dd…`) + host owner core: T5 attempt_dd_a73 14/14, attempt_dd_a75 8/8 + coredown PASS; T8 runtime 0 비밀/0 canary; raw_metrics 절대경로 노출은 DD-A75 F1/F1b로 0건 확인 |
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
- 공개 계약 1.5.0(`openapi.json` sha256 `70e0cffa…`), runner 프로토콜 1.1.0(`openapi_runner.json` `0f3dac4d…`), DB 스키마 v6, 최종 소스 트리 해시(5 패키지 221 파일) `sha256:8fc93f2a596891174c2884e1697fd6003377b713102c0b21b01eb45d3f6f7312`(계보: `f00987be…` P-final-2 → `1b4bd1a4…` DD-A73 → `26e02259…` DD-A75 F1 → `8fc93f2a…` DD-A75 F1b; 각 단계 재게이트), public 4패키지 `9859adb2…`.
- 원본: `etri_nas` HEAD 18c4815(work/mbnas-integration-20261021) 스냅샷 603 파일(`build/source_snapshot`, provenance manifest).
- 모델 라우팅(MODEL_ROUTING.md): 설계·판정 = 세션 모델 `claude-fable-5-1`('ultra' 미노출 → HIGH_MODEL_REVIEW_PENDING); 구현·수집 = `claude-opus-5-5`(일부 `claude-sonnet-5-5`); 평가자 = opus 12명(관점별) + 설계자 adjudication.

## 3. 보존 / 신규 / 미완료
- **보존(T2 동등성 10/10, 0 MISMATCH)**: GA·IndependentRandom·prior 탐색 의미, 구조 gate(학습 전 구조 정합성 검사), source rung·동률 규칙, baseline/final 재학습, seed/RNG, 평가 OFF 의미(무작위 생성→구조 검사→유효 후보 저장, 미평가 지표 null).
- **신규**: core(`/core/v1`)·gateway(`/api/v1`)·runner 분리와 lease fencing·멱등 commit, 계약 1.5.0 투영(불투명 id·상대경로), artifact store·checkpoint id 다운로드·export zip, 보존 정책 prune 왕복(runner 프로토콜 1.1.0), CPU fleet(NOT_EVALUATED 의미), 설정 개정(original/selected/executed/generated), 웹(블록 표·개정·diff·비교·ON/OFF·baseline), 패키징 3 프로필(allowlist·manifest·canary·재현 빌드), 운영 스크립트(install/doctor/start/stop/verify/package).
- **미완료·이월**: RELEASE_1_5_1_CARRYOVER.md 13항목(교차 runner 전송·affinity, 유휴 runner prune 전달, prune_requests 조회 route, verify.sh base_env scrub, 테스트 이식 3건, 웹 툴팁 2건, PRUNED→PRESENT 상태 우선순위, TensorRT, 별도 host, canary_scan 줄 번호 선형화(DD-A74), doctor/verify entry_points의 비인용 pip 트램펄린 파싱(DD-A75 F3)).
- **알려진 한계(0.1.0)**: 공백 없는 긴 설치 경로(pip이 `/bin/sh` 트램펄린을 따옴표 없이 쓰는 shebang 한계 초과 경로)에서 `doctor`/`verify relocation`의 entry_points 검사가 'runs None' 오탐을 낸다(진입점은 정상 동작; P2 T4 sandbox relocation 4/7, 짧은 경로에서는 8/8) → 설치 경로를 짧게 두거나 1.5.1까지 해당 검사 결과를 수동 확인. `requirements-cpu.txt`는 동봉 cu124 wheelhouse로 오프라인 설치 불가(온라인/Docker 경로, INSTALL.md 명시). `verify --suite integration`은 CPU 전용 fleet에서 DD-A75 F2 수정본부터 통과(수정 전 owner ZIP `53e6a1f0…`은 14/16).

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
| owner | `/home/mgjeong/Desktop/etri_hd/mbnas_application/dist/owner/mbnas_application-0.1.0-owner-private-runtime-linux_x86_64.zip` | `9f03c484bbc8c0a2227cc325658892e3efe286d08cca9a1978383158075c2f2a` (DD-A75 재빌드본, 릴리스 후보 트리 `8fc93f2a…`; 최종 docs-only 재빌드 시 갱신) | 85,188,530 / 3693 | public + mbnas_core + owner worker + tests/owner + docs/internal + wheels 5 | 자체 core(all-in-one) |
- 외부 `.sha256` 파일 동봉, FILE_MANIFEST.jsonl(상대경로/bytes/sha256/role/origin/licence), PACKAGE_PROFILE.json/BUILD_INFO.json(소스 트리 해시), PACKAGE_CONTENTS.md, LICENSE(자리표시), THIRD_PARTY_NOTICES(.md/_WEB.md). 재현 빌드(2회 바이트 동일).
- **후속(DD-A76, 사용자 15:2x 지시, peer 세션 수행)**: 위 3종은 DD-A75 코드 검증 세트다. 사용자 지시에 따라 '테스트·내부 기록·주석·자리표시 LICENSE를 뺀 깔끔한 프로그램만' 담는 CLEAN1 ZIP 3종이 별도 세션(`etri-hd-d8`, CLEAN_DISTRIBUTION_SPEC.md)에서 재빌드·재게이트 중이며, 완료 시 이 표를 대체한다(이 세션의 DD-A70 docs-only 재빌드는 취소, DD-A77). 캔ary·호스트 사실 누출: public/registration 0건; owner 0 canary(owner 전용 파일의 legacy 호스트 사실 38건은 DD-A72로 수용, 1.5.1).

## 7. 12 기준셋 회차/점수/수정/시험 근거 (REVIEW_LOG.jsonl, evidence/CRITERIA/)
| 셋 | 1차 | 2차 | 3차 | 최종 | 주요 수정 |
|---|---|---|---|---|---|
| C01 사실·기준선 | 100 | 100 | — | PASS | provenance 게이트 |
| C02 위협·비밀 | 85 | 100 → **95**(추록: F1 발견 D9 → F1b 수정 확인; D9b 자유 텍스트 공백 경로 꼬리·SECURITY_MODEL.md:96 무단서 → 1.5.1 #14 또는 CLEAN1 재빌드 시 수정) | — | PASS(95) | RunnerSettings/canary 테스트, 문구, ADMIN 행, nginx owner 차단 |
| C03 NAS 의미 | 95 | 95 | — | PASS | T2 행, NOT_EVALUATED 제외 테스트 |
| C04 분산 상태 | 95 | 95 | 100 | PASS | DD-A64 reclaim, prune 상태 기계 |
| C05 입력·인증 | 100 | 100 | — | PASS | 리댁션 테스트 |
| C06 복구·재현 | 100 | 100 | — | PASS | docstring 정정 |
| C07 UI·기능 | 95 | 100 | — | PASS(`9f03c484…` 확인: web dist 3파일 동일, raw_metrics `<path>` UI 정상, 100 유지) | 블록 표 링크, 툴팁, 실스택 V4B_FINAL2 |
| C08 원격 산출물 | 80 | 95 → 90(추록) → **95**(`9f03c484…` 확인: host_path 25 복귀, ZIP 코드로 시험 357건 PASS) | — | PASS(Minor L1–L4 이월, O1 관찰) | prune 왕복(T3-34), CRLF 해시, 다운로드 sha 전수 대조 |
| C09 설치 폐쇄성 | 90 | 100 | — | PASS(`9f03c484…` 확인: wheel gate 5/5, core wheel 변경 멤버 projection.py뿐, T6 attempt4·P2 bwrap 격리 설치, 100) | start.sh scrub, verify origins, wheel gate |
| C10 ZIP 배포 | 90 | 100 | — | PASS(D1–D7 FIXED; D8 Minor: T8 owner 스캔 덤프 동봉·canary_scan 선형화 → DD-A74/1.5.1) | PACKAGE_CONTENTS, licence 열, 독립 재빌드 바이트 동일, 매니페스트 전수 대조 |
| C11 실구동·등록 | 75 | [확정 대기] | | | 보고서 행, LICENSE, npm notices, DD-A73 |
| C12 운영·최종회귀 | 70 | [확정 대기] | | | 트리 해시 .meta, MODEL_ROUTING, ops 스크립트 |

## 8. 남은 최소 다음 행동
1. **사용자(git)**: 로컬 커밋 `3e5bbecb9`와 그 뒤의 `8722e3062`(11:58)는 runtime/(146,689 파일, env 파일 5개)·build/canary·dist/owner·docs/internal을 추적 → **push 금지**, `docs/internal/GIT_HYGIENE_URGENT.md` 절차로 재커밋(DD-A67; `.gitignore`는 이미 추적된 파일을 제외하지 못함).
2. **사용자(권리)**: 자체 코드 라이선스·권리자 결정 → LICENSE 자리표시 교체; 논문 각주 코드 제공 약속 처리(RIGHTS_REVIEW Q7-3).
3. **환경**: 별도 관리자 host가 생기면 T5/T8을 고객 root에서 재실행(ADMIN_BOUNDARY); 승인된 GPU 창에서 TensorRT·컨테이너 GPU job.
4. **정리(승인 필요)**: `runtime/test_root/t6/*` T6 스크래치 트리(venv 포함 122 GB, 15:45 실측; P2 T5 증거가 `final/mbnas owner final/…`를 참조), 남은 응답 덤프(`t4t5` 1.2 GB, 세션 스크래치 `p2a75`·`p2dd73` 약 1.3 GB), docker 이미지 `mbnas-public-cpu:{t5,final,g2t7,final2}` + untagged `6700da22…` 삭제. **이미 삭제된 것(보고)**: 15:3x에 P2가 설계자의 '추출 트리를 지워라' 지시를 넓게 해석해 `runtime/test_root/t4t5/{t4,t4f,final/t4,final2/ownercur}`·public 추출본·`zipdiff`·`p2dd73` 추출 트리 12개(~42 GB)를 삭제함 — 증거·ZIP 사본·응답 덤프는 보존, DD-A73 이관 시험에 쓴 v5 DB 사본은 함께 사라짐(결과는 `T4/rerun2/attempt_dd_a73/migration_v5_to_v6_check.txt`에 남음); 설계자가 F2 에이전트의 세션 스크래치 설치 트리 21 GB도 삭제함(프로젝트 밖).
5. **배포본 정리(DD-A76, 진행 중, peer 세션)**: CLEAN1 ZIP 3종의 재게이트 결과(게이트·canary·T8·T6×3·T4/T5)와 기준셋 재확인(특히 C09 package-data 실행 증거 대체, C10 재현 빌드, C02/C08의 SECURITY_MODEL.md:96 단서)을 이 보고서의 §6·§7에 반영한 뒤 RELEASE_CANDIDATE_READY 재판정.
6. **다음 과제**: 등록·배포 소스 정리 및 별도 저장소 이관(NEXT_TASK_MIGRATION_PROMPT.md; 운영 계획 MIGRATION_TASK_OPERATIONS_PLAN.md) — 저장소가 CLEAN1로 안정된 뒤 착수(기준선 변동 중 착수 시 산출물이 낡음); 저장소명(ver1/ver1.0)·private·포함 프로필 승인과 빈 저장소 생성(이 호스트에 gh 없음)이 필요.

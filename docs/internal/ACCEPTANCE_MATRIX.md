# ACCEPTANCE_MATRIX (지침 §15 상태별 판정) — 갱신 2026-10-28 15:50 KST (설계자; P-final-2 gates_r2·DD-A73 owner ZIP 반영, P2 T4/T5·재채점 C10–C12 결과는 확정 후 갱신)

각 상태는 별도로 판정한다. 근거는 VALIDATION_REPORT.md 행과 REVIEW_LOG.jsonl, DECISIONS.md의 DD 번호다. 'ZIP'은 P-final-2 산출물(public `bc0500bd…`, registration `dea510df…`, owner `9f03c484…` = DD-A75 재빌드본, 릴리스 후보 트리 `8fc93f2a…`; 이전 owner `53e6a1f0…`(DD-A73)·`867cc577…`·`a43a8a2e…`는 대체됨)을 가리킨다.

| 상태 | 판정 | 근거 / 미충족 조건 |
|---|---|---|
| IMPLEMENTATION_COMPLETE | **충족(범위 DD-A38 기준; 1.5.1 이월 목록 있음)** | 요청 범위의 수직 경로 전부 구현: core/gateway/runner 분리, 계약 1.5.0 투영, artifact store·다운로드, 재개·보존(prune 왕복 DD-A66 포함), CPU fleet, 웹(블록 표·개정·diff·비교), 패키징 3종. 최종 회귀 PASS(릴리스 후보 트리 `8fc93f2a…` gates_r4: public 1190/0, owner 1686/0; 이력 gates_r3 `26e02259…` 1190/0·1663/0, gates_r2 `1b4bd1a4…` 1190/0·1604/0, `f00987be…` 1190/0·1601/0). 미구현은 RELEASE_1_5_1_CARRYOVER.md에 명시(교차 runner 전송·affinity, 유휴 runner prune 전달, 조회 route 등). |
| SOURCE_INDEPENDENCE_VALIDATED | **충족** | 원본 접근 불가 환경(bwrap: 프로젝트 트리·etri_nas·/home 불가시)에서 public/registration ZIP 설치·실행·verify PASS(T6 rerun2); import closure 0 위반; `module_origins` 전부 ROOT, `caller_env_python_vars` []; T2 결정 동등 10/10; provenance(SOURCE_SNAPSHOT/PLACEMENT 221/221). owner `9f03c484…` T6 rerun2 attempt4 PASS(unit 2829/0, relocation 215 rows ROOT, cwd `/` 재호출 0; attempt3 on `53e6a1f0…` 동일) + C09 평가자의 bwrap 격리 설치 probe(/home·프로젝트 트리·etri_nas·/data·네트워크·GPU 불가시, 오프라인 설치 0, relocation 8/8) PASS. |
| OWNER_BUNDLE_VALIDATED | **충족** | 코드 최종 owner ZIP `9f03c484…`(트리 `8fc93f2a…`, 2회 빌드 동일, FILE_MANIFEST 3692/0 불일치, wheel gate 5/5): P1 T6 owner attempt4(새 추출본, 공백 경로, 외부 cwd) PASS — install/start/doctor 32 PASS/unit 2829/0/integration 17/17/relocation 8/8(215 rows ROOT)/package 3692/3692/stop·cwd `/` 재호출; P2 T4 attempt_dd_a75(bwrap, GPU 노드 없음, 공백·유니코드 경로) PASS — ETRI 후보 사전검사 없이 200→COMPLETED, 공개 응답 경로 조각 0, verify 통합 17/17·relocation 8/8, DB v6; 이전 ZIP에서 v5→v6 이관 PASS·C09 bwrap 격리 설치 PASS. 한계: 공백 없는 긴 설치 경로의 doctor/verify entry_points 오탐(F3, 1.5.1 #13). 최종 docs-only 재빌드(DD-A70) 후 sha 갱신. |
| PUBLIC_ZIP_VALIDATED | **충족** | public ZIP `bc0500bd…`만 설치한 docker 이미지(`a97cb3dd…`) + host owner core: T5 attempt_dd_a73 14/14 + coredown, attempt_dd_a75 8/8 + coredown(ETRI 후보 사전검사 없이 200→COMPLETED·실행 개정 다운로드, prune 왕복, 503 CORE_UNAVAILABLE 무 fallback, 컨테이너에 mbnas_core 없음); T8 runtime 0 비밀/0 canary; attempt_dd_a73에서 발견된 `raw_metrics` runner 절대경로 노출은 core projection 수정(DD-A75 F1/F1b)으로 공백·유니코드 경로까지 0건 확인. |
| REGISTRATION_ZIP_VALIDATED | **충족** | registration ZIP `dea510df…` bwrap 설치·실행·verify PASS(T6 rerun2), 범위 대조(SUBMISSION_SCOPE_TABLE 실측, FILE_LIST check OK, 485/485 package check). 기관 등록 승인과는 별개. |
| GPU_PATH_VALIDATED | **충족(한계 명시)** | 승인 창(DD-A35)에서 트리 `9dafe4d0…` 기준 62/62 단계 PASS(T7: 실제 GPU 학습·평가·재개·취소·SIGKILL 재시도·다운로드·benchmark). 한계: TensorRT BLOCKED_BY_ENV, BENCHMARK는 owner 스크립트 경로, 컨테이너 GPU job 미검증; 이후 변경(38 수정/1 추가/0 삭제)은 CPU로만 검증. |
| ADMIN_BOUNDARY_VALIDATED | **BLOCKED_BY_ENV — 미충족** | 별도 관리자 신뢰영역 host 없음. 같은 host의 프로세스·계정 분리만 검증(기능 검증). 필요 조건 4개: VALIDATION_REPORT ADMIN 행. |
| OFFLINE_INSTALL_VALIDATED | **충족(public `requirements-public.txt` + cu124 wheelhouse 경로에 한정)** | `unshare -rn` 네트워크 차단에서 install.sh·pip --no-index·pip check 모두 exit 0(P-final-2). `requirements-cpu.txt`는 wheelhouse에 없어 오프라인 불가(온라인/Docker 경로); registration ZIP의 오프라인 설치는 미실행. |
| RELEASE_CANDIDATE_READY | **P2 T4/T5·재채점(C08 추록/C10/C11/C12)·C07/C09 owner 추록 완료 시 판정** | 현재(DD-A75 세트 `9f03c484…` 기준): C01 100, C02 95(확인), C03 95, C04 100, C05 100, C06 100, C07 100(확인), C08 95(확인), C09 100(확인), C10 100(53e6a1f0… 기준; 9f03c484…/CLEAN1 재확인 필요); C11/C12 재채점 진행 중. 누출 검사: public/registration 0건, owner 0 canary(legacy 38건은 DD-A72 수용). |
| HIGH_MODEL_REVIEW_PENDING | **해당** | 지침의 `fable5.1 ultra` 식별자가 환경에 노출되지 않음(MODEL_ROUTING.md); 설계·판정은 claude-fable-5-1 세션 모델이 수행. |
| RELEASE_REVIEW_PENDING | **해당** | 자체 코드 라이선스·권리자 미정(LICENSE 자리표시, DD-A65), 논문 각주의 코드 제공 약속(RIGHTS_REVIEW Q7-3), Ultralytics AGPL 범위 검토(DD-A9). 실제 release 권한과 별개. |
| BLOCKED_BY_ENV | **해당 항목** | TensorRT 경로, 별도 관리자 host, 컨테이너 GPU job, Windows ZIP(해당 시). |

사용자 조치: DD-A67 git 위생(커밋 3e5bbecb9 push 금지·재커밋), 라이선스/권리자 결정, 관리자 host 제공 여부, 정리 트리 삭제 승인.

---
## 이력
### 2026-10-23 초기 판정(이력)

| 상태 | 판정 | 근거/미충족 |
|---|---|---|
| IMPLEMENTATION_COMPLETE | 미충족 | 구현 시작 전 |
| SOURCE_INDEPENDENCE_VALIDATED | 미충족 | — |
| OWNER_BUNDLE_VALIDATED | 미충족 | — |
| PUBLIC_ZIP_VALIDATED | 미충족 | — |
| REGISTRATION_ZIP_VALIDATED | 미충족 | — |
| GPU_PATH_VALIDATED | BLOCKED_BY_ENV | GPU 2장 day 캠페인 점유, 사용자 승인 창 필요 |
| ADMIN_BOUNDARY_VALIDATED | 미충족 | 별도 관리자 host 없음(동일 host 프로세스 분리는 기능 검증일 뿐) |
| OFFLINE_INSTALL_VALIDATED | 미충족 | wheelhouse 준비 중; 네트워크 차단 설치 미시험 |
| RELEASE_CANDIDATE_READY | 미충족 | — |
| HIGH_MODEL_REVIEW_PENDING | 해당 | `ultra` 모델 식별자 미확인(MODEL_ROUTING.md) |
| RELEASE_REVIEW_PENDING | 해당 | 라이선스(AGPL)·권리자·논문 공개 약속 검토(DD-A9) |


## 갱신 2026-10-27 15:3x
- T2 core 동등성: 10/10 비불일치(REVIEW_LOG T2 equivalence-1) → SOURCE_INDEPENDENCE_VALIDATED의 '결정 동등' 근거 확보(독립 설치 근거는 WP-8 T4/T5/T6 대기).
- GPU_PATH: DD-A35로 GPU 확보(10-27 14:55), G2 트랙 실행 중 → 판정 대기.
- 10-28 04:4x GPU_PATH_VALIDATED: **충족**(TREE_HASH 9dafe4d0… 기준, REVIEW_LOG T7 gpu-rerun-final; 한계: TensorRT BLOCKED_BY_ENV, BENCHMARK owner 스크립트 경로, 컨테이너 GPU job 미검증).

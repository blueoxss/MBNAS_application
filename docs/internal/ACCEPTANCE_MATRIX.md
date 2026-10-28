# ACCEPTANCE_MATRIX (지침 §15 상태별 판정) — 갱신 2026-10-28 11:3x KST (설계자; P-final-2 반영, owner 재빌드·P2 결과는 확정 후 갱신)

각 상태는 별도로 판정한다. 근거는 VALIDATION_REPORT.md 행과 REVIEW_LOG.jsonl, DECISIONS.md의 DD 번호다. 'ZIP'은 P-final-2 산출물(public `bc0500bd…`, registration `dea510df…`, owner는 DD-A72 재빌드본)을 가리킨다.

| 상태 | 판정 | 근거 / 미충족 조건 |
|---|---|---|
| IMPLEMENTATION_COMPLETE | **충족(범위 DD-A38 기준; 1.5.1 이월 목록 있음)** | 요청 범위의 수직 경로 전부 구현: core/gateway/runner 분리, 계약 1.5.0 투영, artifact store·다운로드, 재개·보존(prune 왕복 DD-A66 포함), CPU fleet, 웹(블록 표·개정·diff·비교), 패키징 3종. 최종 회귀 PASS(public 1190/0, owner 1601/0, 트리 `f00987be…`). 미구현은 RELEASE_1_5_1_CARRYOVER.md에 명시(교차 runner 전송·affinity, 유휴 runner prune 전달, 조회 route 등). |
| SOURCE_INDEPENDENCE_VALIDATED | **충족** | 원본 접근 불가 환경(bwrap: 프로젝트 트리·etri_nas·/home 불가시)에서 public/registration ZIP 설치·실행·verify PASS(T6 rerun2); import closure 0 위반; `module_origins` 전부 ROOT, `caller_env_python_vars` []; T2 결정 동등 10/10; provenance(SOURCE_SNAPSHOT/PLACEMENT 221/221). owner는 재빌드본 T6 결과로 확정. |
| OWNER_BUNDLE_VALIDATED | **재빌드 후 확정(이전 ZIP 기준 PASS)** | 이전 최종 owner ZIP `79cce02a…`에서 T4 FINAL PASS(bwrap 전체 통합 실행). DD-A72 재빌드본에 대해 P1 T6 owner rerun2 + P2 T4 rerun2 결과로 확정한다. |
| PUBLIC_ZIP_VALIDATED | **충족(T5 rerun2로 재확인 중)** | public ZIP만 설치한 docker 이미지 + 실제 원격(host) owner core 연동 실행 PASS(T5 FINAL, 이전 ZIP); P-final-2 ZIP `bc0500bd…`에 대해 T6 rerun2 PASS·T8 runtime 0건; P2 T5 rerun2(generated 개정 학습 단계 포함) 진행 중. |
| REGISTRATION_ZIP_VALIDATED | **충족** | registration ZIP `dea510df…` bwrap 설치·실행·verify PASS(T6 rerun2), 범위 대조(SUBMISSION_SCOPE_TABLE 실측, FILE_LIST check OK, 485/485 package check). 기관 등록 승인과는 별개. |
| GPU_PATH_VALIDATED | **충족(한계 명시)** | 승인 창(DD-A35)에서 트리 `9dafe4d0…` 기준 62/62 단계 PASS(T7: 실제 GPU 학습·평가·재개·취소·SIGKILL 재시도·다운로드·benchmark). 한계: TensorRT BLOCKED_BY_ENV, BENCHMARK는 owner 스크립트 경로, 컨테이너 GPU job 미검증; 이후 변경(38 수정/1 추가/0 삭제)은 CPU로만 검증. |
| ADMIN_BOUNDARY_VALIDATED | **BLOCKED_BY_ENV — 미충족** | 별도 관리자 신뢰영역 host 없음. 같은 host의 프로세스·계정 분리만 검증(기능 검증). 필요 조건 4개: VALIDATION_REPORT ADMIN 행. |
| OFFLINE_INSTALL_VALIDATED | **충족(public `requirements-public.txt` + cu124 wheelhouse 경로에 한정)** | `unshare -rn` 네트워크 차단에서 install.sh·pip --no-index·pip check 모두 exit 0(P-final-2). `requirements-cpu.txt`는 wheelhouse에 없어 오프라인 불가(온라인/Docker 경로); registration ZIP의 오프라인 설치는 미실행. |
| RELEASE_CANDIDATE_READY | **owner 재빌드·P2·재채점(C08/C10/C11/C12) 완료 시 판정** | 현재: C01 100, C02 100, C03 95, C04 100, C05 100, C06 100, C07 100, C09 100(ZIP 추록 대기) PASS; C08/C10/C11/C12 재채점 진행·대기. 누출 검사 0건(public/registration). |
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

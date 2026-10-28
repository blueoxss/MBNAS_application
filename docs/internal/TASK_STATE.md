# TASK_STATE — MBNAS Research Console (mbnas_application)

갱신: 2026-10-26 16:2x KST (세션 8da2e29b). 이 파일은 세션 복구용 요약이다. 상세는 IMPLEMENTATION_PLAN.md / DECISIONS.md / WORK_PACKAGES/.

## 확정 경로
- SOURCE_ROOT = `/home/mgjeong/Desktop/etri_hd/etri_nas` (읽기 전용 참조; 다른 Claude 세션 pid 2543338이 Phase G 작업으로 동시 수정 중 — 절대 쓰지 않는다)
- TARGET_ROOT = `/home/mgjeong/Desktop/etri_hd/mbnas_application` (모든 구현·문서·검증·패키징은 여기서)
- 입력 스냅샷 = `build/source_snapshot/` (603 파일 = 첫 복사 597 + delta 2회(재복사 45, 신규 6), 10.4 MB, allowlist 선별 복사). 파일별 provenance: `docs/internal/provenance/SOURCE_SNAPSHOT_MANIFEST.jsonl`, 요약 `SOURCE_SNAPSHOT_SUMMARY.json`
  (HEAD 18c4815 / branch work/mbnas-integration-20261021 / origin/main cc5f3e9 / tracked_clean 416 · tracked_modified 93 · untracked 94; 첫 복사 값 425/84/88은 SUMMARY `initial_copy`; 10-28 라이브 대조 603/603 일치, evidence/PROVENANCE/live_compare_*).
- 표시명: MBNAS Research Console / MBNAS 모듈형 신경망 구조 탐색·실험 통합관리 프로그램. VERSION 0.1.0 (기존 mbnas-server 0.1.0 계승).

## 환경 사실 (2026-10-23 20:55 확인)
- GPU 2×RTX A6000 모두 day 캠페인(S1 random/ga seed 42, pid 2037797/2038034) 점유 중(96%/89%) → 콘솔 작업 GPU 사용 금지(DD-23 계승). T7은 BLOCKED_BY_ENV, 사용자 승인 창 필요.
- 포트 사용 중: 8000/8001/8002(원본 API), 5173/5174(원본 web). 본 프로젝트는 8700(core)·8710(server)·8720(web preview) 사용.
- Python: 앱용 3.10.22 = `/home/mgjeong/miniconda3/envs/mbnas310/bin/python3.10`(플랫폼 인터프리터). 원본 .venv(torch 2.6.0+cu124, ultralytics 8.4.173, rfdetr 1.11.2)는 참조만. sg26(3.9)·sg는 연구 환경, 건드리지 않음.
- Node 24.20 / npm 11.19 (nvm; node 22.23.2도 있음). docker 26.1.3 사용 가능(python:3.10-slim, node:22 이미지 로컬). bwrap/unshare -rn 네트워크 차단 동작 확인.
- pip 네트워크 OK(~12.6 MB/s). 호스트 pip.conf에 깨진 ngc index → 항상 PIP_CONFIG_FILE=/dev/null.
- iterative-feedback-evaluator 스킬: 설치되어 있지 않음(~/.claude, 프로젝트 .claude 검색 0건) → §14 명시 계약으로 수행, 스킬 실행 주장 금지.

## 진행 상태
- [x] 기준선 조사(호출관계·환경·동시 작업·라이선스) — 이 세션 20:5x~21:1x
- [x] TARGET_ROOT 골격 + 스냅샷 복사 + provenance
- [x] wheelhouse 139 wheel(3.5 GB) + `runtime/venv`(py3.10.22, torch 2.6.0+cu124, ultralytics 8.4.173, 137 pkgs) 설치 완료; `npm ci` 완료
- [x] WP-0 분석 워크플로(wf_6464dd36-408, 8 에이전트 fable 상속, 38.8분, 3.6M 토큰) → `docs/internal/analysis/*` 13파일; 설계자 검토 후 DECISIONS DD-A10~A25, FUNCTION_AND_SECRET_MATRIX v1, IMPLEMENTATION_PLAN v1, WORK_PACKAGES/WP-1_SPLIT.md 작성
- [x] delta 스냅샷(20261023T220106): 라이브가 계약 1.4.2 적용 → 11파일 재복사(provenance 기록)
- [x] WP-1 분할 수용(10-26 20:0x): public 564/0, owner 게이트 1116/0, import 검사 0 위반, parity 17, vitest 488 → T1 기준선 기록(REVIEW_LOG). 이력: wf_f6ce4bcc-08c: 10-23 22:12 기동 → A/B/C/E/F/H 완료, D(core)/G(tests)/V(verify)는 세션 한도로 실패 → 10-26 16:1x 교차 요청 판정(DD-A26~A29, WORK_PACKAGES/WP-1_RESOLUTIONS.md) 후 resumeFromRunId로 재개(캐시 6 + 실행 3 + fix 루프)
- [ ] 원격 큐/lease(WP-2) · gateway(WP-3) · projection/public ID(WP-4) · artifact relay/재개(WP-5) · 웹 정리(WP-6) · scripts/ZIP(WP-7) · 검증 T2–T8 · 기준셋 12개 루프
- 재개 방법: 이 파일 → IMPLEMENTATION_PLAN.md → WORK_PACKAGES/ 최신 → `docs/internal/REVIEW_LOG.jsonl` 마지막 줄.

- [x] SYNC-1 delta 3(46파일, 계약 1.4.3·gate 수정·DD-44/45 웹) 병합 완료
- [x] WP-2 원격 큐 수용(10-26 22:1x, wf_993989af-195): closure 0 위반, public 692/0, owner 1158/0, T3 40/40, 2프로세스 smoke PASS(실 CPU 학습 COMPLETED, EVALUATED는 stub만)
- [~] WP-3 gateway: 10-26 22:10 세션 종료로 워크플로 사망(빌드 미완) → 10-27 11:0x RESUME_STATE.md 작성 후 같은 run id로 재개(wf_4b13dd70-0b8)
- [ ] DD-A35: WP-3 수용 즉시 캠페인 STOP+TERM으로 GPU 확보 → WP-G2 GPU 트랙(WORK_PACKAGES/WP-G2_GPU_TRACK.md) → 종료 후 캠페인 --resume 재개

- 10-27 12:2x: 사용자 지시 '07:00 전 완성' → OPERATIONS_PLAN_20261027.md(파도 W1/W2/W2b/V3/G/W4/V4/P/C), DD-A38 범위 축소, DD-A39 store 레이아웃. 진행 중 에이전트: W1 3개, W2 5개, W2b 3개.
- 10-27 15:0x: WP-3 수용(REVIEW_LOG). 캠페인 STOP+TERM 완료(DD-A35 실행 기록) → G2 GPU 트랙 + W4(4B/4C/5B/6B) 기동. T2 동등성 진행 중. WP-7A/B/C/D·6A·5A·5C·4A 완료.
- 10-28 03:1x: 세션이 10-27 18:4x~10-28 03:00 사이 종료(약 8h 공백). 완료: 4A/4B/5A/5B/5C/6A/6B/7A/7B/7C/7D/T2/G2(PARTIAL→수정 완료). 4C는 거의 완료(게이트 1500/0, public 1070/0, smoke 11/11·17/17) → 재개해 마무리(T2 해시 규칙·5B 테스트 순서). 03:1x 기동: G2 재실행(GPU), V4-B 웹 실서버 흐름, 7C 최종 문서, P1(ZIP 3종·T6·T8·offline), P2(T4·T5). 다음: 설계자 기준셋 채점 → VALIDATION_REPORT·FINAL_REPORT → 캠페인 재개.
- 10-28 03:4x 사용자 지시: 멈추지 말고 계속; 계획·평가는 최상위 모델(fable), 구현·실행은 하위 모델. **현재 과제 완료 후 다음 과제** = docs/internal/NEXT_TASK_MIGRATION_PROMPT.md(등록·배포 소스 정리 + 별도 저장소 MBNAS_application_ver1.0 이관, C01~C12 × 5회 재정비 루프; 원격 생성·push는 사용자 승인 후에만).
- 10-28 03:3x 발견: mbnas_application이 사용자에 의해 git 저장소가 됨(origin https://github.com/blueoxss/MBNAS_application.git, 커밋 c9a294085 'first upload', 3e5bbecb9 'update'; 작업트리 dirty 107항목). 우리는 git add/commit/push 무조작(사용자 행위). 다음 과제의 SOURCE_REPO가 바로 이 저장소.
- 10-28 03:29: GPU 트랙 전 항목 PASS(REVIEW_LOG T7 gpu-rerun-final, GPU_PATH_VALIDATED 충족) → 캠페인 재개 완료(DD-A35 (2)). 이후 GPU 사용 금지.

## 10-28 09:5x 현재 상태 (세션 8da2e29b, 컨텍스트 압축 후)
- 기준셋 1차 채점 완료: PASS C01 100·C03 95·C04 95(iter3 대기)·C05 100·C06 100·C07 95 / FAIL C02 85·C08 80·C09 90·C10 90·C11 75·C12 70.
- 실행 중 에이전트: **라운드 4** ab1733db72159da66(문서·보고서 행·테스트 이식·웹·ops·패키저 보강, 하위 트랙 4개; 추가 지시: fix-5 문서 항목 + ops 테스트 import 2건) / **fix 5 후속** aae197ca93a25340d(DD-A68: 교차 패키지 패치 1·2·3·4·6 적용 + 재실행 + openapi_runner·INTERFACES 재생성).
- fix 5 완료(DD-A68): prune 왕복 T3-34 구현, runner 프로토콜 1.1.0, DB v6, D1 CRLF 해시. 증거 docs/internal/evidence/WP7/FIX5_T3_34/.
- 다음 순서: (1) 두 에이전트 보고 → DD/REVIEW_LOG 기록 → (2) **P-final-2**(P1 에이전트 a7094476016728bc7 재개; 브리프 scratchpad/wp3/pfinal2.txt): 최종 회귀 게이트(tree hash .meta) → closure/placement/t2_compare → ZIP 3종 재빌드(재현성) → canary → T6 3종 → T8 §2 → offline → (3) P2(af844a30755a49368) T4/T5 최종 ZIP + kind=generated 학습 단계 → (4) 재채점 C02/C04(iter3)/C08/C09/C10/C11/C12(+C03/C07 spot) 기존 평가자 재개(rescore.txt 갱신) → (5) ACCEPTANCE_MATRIX/VALIDATION_REPORT/FINAL_REPORT/MODEL_ROUTING/TASK_STATE/메모리 → (6) 다음 과제(NEXT_TASK_MIGRATION_PROMPT.md; 운영 계획 docs/internal/MIGRATION_TASK_OPERATIONS_PLAN.md).
- 사용자 조치 대기: DD-A67 git 위생(push 금지·재커밋), 라이선스/권리자, ADMIN_BOUNDARY 호스트, 대상 서버 사실, 정리 트리 삭제 승인(runtime/test_root/* ~60GB, docker 이미지 mbnas-public-cpu:{t5,final,g2t7}).
- 금지 유지: GPU 사용 금지(캠페인 재개됨), etri_nas 쓰기 금지, git 조작 금지, 원격 생성/push 금지(승인 전).
- 10-28 10:2x 라운드 4 완료(구현 claude-opus-5-5 + fork 4개: 웹·시험·운영 스크립트·패키저; 판정 대기): FIX_ROUND_1 "최종 문서/주석 잔여" 전 항목 + 조정자 추가분(C11·DD-A65 라이선스/npm 고지/범위표/FILE_LIST, C09 start.sh 환경 정리·doctor env·verify relocation·wheel 패키지 데이터 검사, C10 PACKAGE_CONTENTS·라이선스 열·registration 결정성·검사 표, DD-A66 prune 왕복 문서화). 현재 소스 트리 `01c7d785…`(4패키지 `9859adb2…`, 최종 ZIP의 `5a7bc592…`/`83af6d5f…`와 다름 → ZIP 3종 모두 재빌드 필요). 같은 트리 재실행: public ops 65 · runner 141 · infra+contracts 268 · packaging 63, owner projection 261 · orchestration 48 · runner_protocol+artifacts 97 · T2 비교 10/10(불일치 0)+pytest 32, closure 0, placement 221/221, check_docs PASS, gen --check 0; 웹 vitest 600 · mock e2e 210 · 새 dist `index-7f__3l0I.js`(`df0d6b2d…`). 근거: `VALIDATION_REPORT.md` "Round 4" 절, `evidence/WP7/ROUND4/`. 다음: P-final-2(ZIP 3종 재빌드·T8 ZIP 스캔·T6·T4/T5 재실행·전체 게이트에 tree hash .meta·실스택 V4를 새 dist로) → C02·C04·C07~C12 재채점 → C12 회귀 기록.

## 10-28 11:3x 상태 (P-final-2 반영)
- 재채점 2차: C02 100·C04 100(iter3)·C07 100·C09 100(추록 대기) PASS → PASS 8셋(C01/C02/C03/C04/C05/C06/C07/C09). 남은 재채점: C08(진행 중, afa7fd0acff86a18b), C10/C11/C12(owner 재빌드 후 기동; 노트 scratchpad/wp3/rescore2_sets.txt).
- P-final-2(P1 a7094476016728bc7): 트리 f00987be…, 게이트 전부 PASS(public 1190/0, owner 1601/0), ZIP 3종 재현 빌드. **public bc0500bd…·registration dea510df… 최종**; owner a43a8a2e…는 T6 unit 1건 실패(라운드 4 테스트의 번들 skip 누락 → 설계자 수정 DD-A72) → owner 재빌드·T6 owner 재실행 진행 중. SUMMARY: docs/internal/evidence/WP7/PFINAL2/SUMMARY.md(설계자 작성).
- P2(af844a30755a49368): T5 rerun2 진행 중(public bc0500bd + host owner core a43a8a2e 설치본; generated 개정 학습·prune 관찰·T8 runtime). T4는 재빌드 owner sha 전달 후.
- 설계자 완료: ACCEPTANCE_MATRIX 갱신(11:3x), VALIDATION_REPORT P-final-2 행 8개 삽입(이전 P1 final 행은 history 표기), RELEASE_1_5_1_CARRYOVER.md, DD-A68~A72, MODEL_ROUTING 재채점 행, reference/ORIGINAL_DIRECTIVE_20261023.md, FINAL_REPORT 뼈대(scratchpad/final_report_skeleton.md).
- 남은 순서: owner 재빌드 보고 → P2 T4 → C10/C11/C12 재채점 + C02/C07/C09 추록 → FINAL_REPORT.md·MODEL_ROUTING·TASK_STATE·메모리 → 문서만 반영한 owner ZIP 최종 재빌드(FILE_MANIFEST diff로 코드 동일성 증명) → 다음 과제(MIGRATION_TASK_OPERATIONS_PLAN.md; 브리프 scratchpad/mig/*.txt).

## 10-28 12:3x 상태 (fix 6 / DD-A73 반영)
- **DD-A73(Major) 수정·수락**: 생성/탐색 후보의 학습 선택 구조 검사가 ETRI 블록 백엔드를 적재하지 않던 결함(T5 rerun2에서 발견). `training_runs/selection.py` 패치(설계자) + 회귀 테스트 3건·스위트 7묶음·실제 프로세스 확인(fix-5 에이전트). **최종 트리 해시 `1b4bd1a4a45196e3c558d62507f109301973eddc180df5968ed7014a72e6708c`**. public `bc0500bd…`·registration `dea510df…`는 core 미포함이라 최종 유지.
- 진행 중: P1(a7094476016728bc7) 최종 트리 정적 게이트 + public/owner 전체 게이트 + owner ZIP 재빌드 ×2 + canary/T8 + T6 owner(evidence WP7/PFINAL2/gates_r2/). P2(af844a30755a49368)는 owner sha 대기(T4 + T5 generated ETRI 후보 단계 + T8 runtime). 추록: C07 완료(public/registration dist 일치), C02·C09 진행 중.
- 설계자 완료: FINAL_REPORT.md 초안([확정 대기] 표시), VALIDATION_REPORT T5 rerun2 행, RELEASE_1_5_1_CARRYOVER #11, REVIEW_LOG(fix-6, T5, C07 추록).
- 남은 순서: P1 보고 → SUMMARY 추록·P2에 sha 전달 → P2 보고 → C10/C11/C12 재채점(rescore2_sets.txt) + C02/C07/C09 owner 추록 → FINAL_REPORT [확정 대기] 채움·ACCEPTANCE_MATRIX·MODEL_ROUTING·메모리 → 문서만 반영한 owner ZIP 최종 재빌드(manifest diff) → 다음 과제.

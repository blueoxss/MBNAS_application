# MODEL_ROUTING — 요청 모델 vs 실제 모델

| 항목 | 사실 | 확인 근거 |
|---|---|---|
| 요청(지침 §4) | `fable5.1 ultra`를 분류·분석·계획·설계 결정·검증 판단·평가·최종 승인 담당으로 배정 | 지침 원문 |
| 실행 세션 모델 | `claude-fable-5-1` (Claude Fable 5.1). 시스템 프롬프트가 모델 ID를 명시. `ultra`라는 모델 ID·옵션·표시명은 이 환경에 노출되지 않음 → 사용자 별칭으로 간주. 세션 effort 값은 질의 API가 없어 확인 못 함(이전 세션 기록은 `max`). | 시스템 프롬프트, 이전 MODEL_ASSIGNMENT.md |
| 모드 | ultracode ON(시스템 알림) → Workflow 다중 에이전트 오케스트레이션 사용. "ultra" 요청과 ultracode 모드를 동일시하지 않는다(모드 사실만 기록). | 시스템 알림 |
| 하위 모델 | Workflow/Agent `model` 지정 가능값: sonnet(claude-sonnet-5-5), opus(claude-opus-5-5), haiku(claude-haiku-5-5), fable. 구현·리팩터링·스크립트·문서 정리: 구조 민감 WP는 opus, 기계적 WP는 sonnet. 알고리즘·연구 조건·신뢰경계 변경 권한 없음. | 도구 스키마 |
| 검증·채점 | 모든 통과/점수 판정은 fable(세션 모델 또는 fork)이 실제 파일·명령 출력·exit code·API 응답을 읽고 내린다. 하위 모델 보고만으로 통과시키지 않는다. | — |
| 상태 | 요청 모델 식별자 `ultra` 미확인 → `HIGH_MODEL_REVIEW_PENDING`(기록). 가역적 구현·조사·증거 수집은 계속. | — |

기록 규칙: 각 WP·기준셋 라운드의 (requested_model, resolved_model_id, mode, role)을 REVIEW_LOG.jsonl에 적는다. 분석 워크플로(2026-10-23 21:1x)의 에이전트는 model 미지정(세션 모델 상속 = claude-fable-5-1).

## 실제 사용 기록 (2026-10-28 라운드 4에서 전 라운드로 갱신, C12 EVAL_1 D9)

규칙: 설계·판정은 세션 모델 `claude-fable-5-1`, 구현·실행·평가 에이전트는 `claude-opus-5-5`(별칭 opus)이며, REVIEW_LOG가 sonnet이라고 적은
작업만 `claude-sonnet-5-5`다. TARGET_ROOT에는 하네스가 남긴 에이전트별 모델 기록(workflow journal)이 없다. 그래서 "확인 근거"가 REVIEW_LOG
행이나 설계자 기록이면 정적 근거이고, 평가 라운드는 평가 파일 머리의 자기 보고(`evaluator: claude-opus-5-5`)와 교차 확인된다.
`ultra`는 요청 별칭으로만 나오며 resolved 열에는 쓰지 않는다. REVIEW_LOG 행 번호는 1부터 센 줄 번호다.

| 단계 | 시각(KST) | requested_model(지침) | resolved_model_id | 모드 | 역할 | 확인 근거 |
|---|---|---|---|---|---|---|
| WP-0 분석(wf_6464dd36-408, 8 에이전트) | 10-23 21:1x–22:0x | fable5.1 ultra | claude-fable-5-1(세션 모델 상속, `model` 미지정) | Workflow, ultracode ON | 분류·분석(설계자의 보조 분석) | REVIEW_LOG 1; Workflow usage(agents 8, tokens 3,604,293, 38.8 min) |
| WP-0 결정·계획 | 10-23 22:0x | fable5.1 ultra | claude-fable-5-1 | 세션 | 설계 결정 DD-A10~A25, 계약 작성 | 본 세션, DECISIONS |
| WP-1 분할 구현(wf_f6ce4bcc-08c, 15 에이전트) | 10-23 22:12 – 10-26 19:3x | 하위 모델 | claude-opus-5-5(A, B, C, D, G, V1~V4), claude-sonnet-5-5(E, F, H); 판정 claude-fable-5-1 | Workflow `model` 지정 | 구현·재배치·시험·검증 실행 | REVIEW_LOG 2, journal.jsonl(세션 당시) |
| WP-2 원격 큐(wf_993989af-195, 6 에이전트) | 10-26 20:xx–21:35 | 하위 모델 | claude-opus-5-5(sync, 2A, 2C, 2D, 2V), claude-sonnet-5-5(2B); 판정 claude-fable-5-1 | Workflow | 구현·검증 | REVIEW_LOG 3 |
| WP-3 gateway(3A core, 3B gateway, 3C 시험 + 3B 수정) | 10-27 11:0x–14:50 | 하위 모델 | claude-opus-5-5 ×3 + 수정 1; 판정 claude-fable-5-1 | Agent tool | 구현 | REVIEW_LOG 4 |
| W1/W2 병렬 작업 WP-4A, 5A, 5C, 6A, 7A, 7B, 7C, 7D | 10-27 12:2x–17:xx | 하위 모델 | claude-opus-5-5(sonnet 지정 기록 없음); 판정 claude-fable-5-1 | Agent tool | 계약 1.5.0 초안·artifact·웹·운영 스크립트·문서·패키저 | 설계자 기록: OPERATIONS_PLAN_20261027.md, DECISIONS DD-A40~A47, evidence `WP4/4A`, `WP5/5A`, `WP5/5C`, `WP6/6A`, `WP7/7A`~`7D` (REVIEW_LOG 행 없음) |
| T2 동등성 1차 | 10-27 15:0x | 하위 모델 | claude-opus-5-5; 검토 claude-fable-5-1 | Agent tool | T2 실행 | REVIEW_LOG 5 |
| W4: 4B core, 4C 시험, 5B 연결, 6B 웹 + 수정 에이전트 | 10-27 14:0x – 10-28 03:37 | 하위 모델 | claude-opus-5-5 ×4 + 수정 에이전트(opus); 검토 claude-fable-5-1 | Agent tool | 투영·계약 1.5.0 승격·연결·웹 | REVIEW_LOG 9 |
| G2 GPU 트랙(1차, G2FIX, 재실행) | 10-27 15:xx – 10-28 03:29 | 하위 모델 | claude-opus-5-5(G2 에이전트); 검토 claude-fable-5-1 | Agent tool, 실제 GPU(DD-A35 창) | T7 실행 | REVIEW_LOG 6, evidence `T7/`, `WP7/G2FIX` |
| V4-B 웹 실서버, P1 1차 패스 | 10-28 02:xx–03:2x | 하위 모델 | claude-opus-5-5 | Agent tool | 브라우저 실스택 시험, ZIP 3종·T6·T8 1차 | 설계자 기록: DD-A55, DD-A59, evidence `WP6/V4B`, `WP7/7V` (REVIEW_LOG 행 없음) |
| 기준셋 평가 1회차: C04, C03, C06, C01, C05 | 10-28 03:33–03:49 | 평가: 최상위 모델(설계자) / 수집: opus | 평가자 claude-opus-5-5(평가 파일 자기 보고), 판정 claude-fable-5-1 | Agent tool(독립 평가자) | 채점(평가자) / 판정(설계자) | REVIEW_LOG 7, 8, 10, 11, 13; `CRITERIA/*/EVAL_1.md` |
| T4/T5 1차(P2) | 10-28 03:0x–03:49 | 하위 모델 | claude-opus-5-5; 검토 claude-fable-5-1 | Agent tool, bwrap / docker | 격리 실행 | REVIEW_LOG 12 |
| 수정 1a(DD-A61), 1b, 1c, 1d | 10-28 04:0x–05:3x | 하위 모델 | claude-opus-5-5; 검토 claude-fable-5-1 | Agent tool | runner id·docstring·health 누출·리댁션 등 | REVIEW_LOG 14(1b), 15(1d); 1a·1c는 DECISIONS DD-A61과 evidence `WP7/FIX1`, `WP7/FIX1C`, `WP7/C05_FIX1D` |
| 수정 2(DD-A62), 2b(DD-A63) | 10-28 05:3x–07:0x | 하위 모델 | claude-opus-5-5; 검토 claude-fable-5-1 | Agent tool | CPU runner·NOT_EVALUATED·owner worker 경로 | 설계자 기록: DECISIONS DD-A62/A63, evidence `WP7/FIX2`, `WP7/FIX2B` (REVIEW_LOG 행 없음) |
| P-final(P1 최종 빌드) | 10-28 06:4x–07:43 | 하위 모델 | claude-opus-5-5; 검토 claude-fable-5-1 | Agent tool | 최종 게이트·ZIP 3종·T6·T8 | REVIEW_LOG 16, evidence `WP7/7V_FINAL` |
| 기준셋 평가 2회차: C01, C04, C06, C05, C03 | 10-28 07:43–08:03 | 평가: 최상위 모델(설계자) / 수집: opus | 평가자 claude-opus-5-5, 판정 claude-fable-5-1 | Agent tool(독립 평가자) | 재채점 / 판정 | REVIEW_LOG 17~21; `CRITERIA/*/EVAL_2.md` |
| T4/T5 최종 재실행(P2) | 10-28 07:4x–08:08 | 하위 모델 | claude-opus-5-5; 검토 claude-fable-5-1 | Agent tool, bwrap / docker | 최종 ZIP 격리 실행 | REVIEW_LOG 22 |
| 기준셋 평가 1회차: C02, C07, C12, C11, C09, C08, C10 | 10-28 07:45–08:35 | 평가: 최상위 모델(설계자) / 수집: opus | 평가자 claude-opus-5-5, 판정 claude-fable-5-1 | Agent tool(독립 평가자) | 채점 / 판정 | REVIEW_LOG 23~25, 27~30; `CRITERIA/*/EVAL_1.md` |
| 수정 3(DD-A64, claim 회수) | 10-28 08:05–08:28 | 하위 모델 | claude-opus-5-5; 검토 claude-fable-5-1 | Agent tool | core claim 경로 | REVIEW_LOG 26, evidence `WP7/FIX3_DD_A64` |
| 수정 5(DD-A66 prune 왕복)와 교차 적용(DD-A68) | 10-28 08:49–09:50 | 하위 모델 | claude-opus-5-5; 검토 claude-fable-5-1 | Agent tool | T3-34 구현·runner 프로토콜 1.1.0 | REVIEW_LOG 31, 32, evidence `WP7/FIX5_T3_34` |
| 라운드 4 문서·주석·시험 패스(이 기록의 작성자) + fork 4개(웹, 시험, 운영 스크립트, 패키저) | 10-28 08:3x–10:xx | 하위 모델 | claude-opus-5-5(이 에이전트의 시스템 프롬프트에 적힌 실행 모델 id. fork는 항상 부모와 같은 모델로 실행된다) | Agent tool + fork | FIX_ROUND_1 최종 문서 잔여, C09/C10/C11 추가 항목 | evidence `WP7/ROUND4/`, `WP6/ROUND4_WEB/`, `WP6/V4B_FINAL/`; 판정은 이후 설계자(claude-fable-5-1) |
| 기준셋 재채점 2회차: C04(iter3), C07, C02, C09 (+ 설계자 DD-A68~A71, 1.5.1 이월 목록) | 10-28 10:0x–10:40 | 평가: 최상위 모델(설계자) / 수집: opus | 평가자 claude-opus-5-5(기존 평가자 재개, 독립 재실행·probe), 판정 claude-fable-5-1 | Agent tool(SendMessage 재개) | C04 100, C07 100, C02 100, C09 100(ZIP 추록 대기) | REVIEW_LOG C04 iter3 / C07 iter2 / C02 iter2 / C09 iter2, evidence CRITERIA/*/EVAL_{2,3}.md, WP6/V4B_FINAL2 |

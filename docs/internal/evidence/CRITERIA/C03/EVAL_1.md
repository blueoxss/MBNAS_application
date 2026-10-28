# C03 EVAL_1 — NAS 의미 (구조 gate · GA/Random prior · source rung · baseline/final)

- 기준셋: C03 v1.0 (`docs/internal/criteria/C03.json`), WP-8 §2 루프의 독립 평가 1회차. 점수는 평가자 의견이며 판정은 설계자가 한다.
- 평가자: claude-opus-5-5 (Opus 5.5), 독립 평가자.
- 시각: 2026-10-28 03:17–03:33 KST (호스트 시계).
- Tree hash (`runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`; 지시된 `| tail -3`은 JSON 닫는 괄호만 출력하므로 같은 출력의 sha256 줄을 인용):
  - 시작 03:17: `sha256:634a083bb910a28d1aa183c9a6a2cee66da7c1fc937f01f8aa85d1bb4c4a301d` (221 files)
  - 종료 03:29 이후: `sha256:76ff726a1fd34353c085913f33d0bbee8ce646ed7beeb41f68000d19e3559fc0` (221 files). 평가 중 다른 에이전트가 DD-A55(a) 수정으로 `mbnas_core/orchestration/retention.py`(03:26:55)와 `mbnas_core/console/routes_runs.py`(03:27:03)를 고쳤다. 변경 내용은 candidate detail의 checkpoint `artifact_id` 표시뿐이며 C03 결정 코드는 건드리지 않는다(`git diff` 열람).
  - core3(contracts+model_runtime+core, t2_compare 알고리즘): r1–r6와 T2 재실행 동안 `aee93aa5…`, 수정 뒤 `04ee95d1…`.
- 실행 조건: 모든 명령은 TARGET_ROOT를 cwd로 하고 `CUDA_VISIBLE_DEVICES=""`, `-B`, `-p no:cacheprovider`로 실행했다. 테스트는 `MBNAS_TEST_ROOT=<scratch>/test_root`로 돌려 TARGET_ROOT/runtime에 쓰지 않았다(기존 `runtime/test_root/{weights,data}`는 비어 있어 충실도 손실 없음). GPU는 쓰지 않았다. 원시 로그·junit·T2 산출물은 세션 scratchpad `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c03/`에 있다(휘발성). 핵심 수치는 아래에 옮겨 적었다. CPU 사용 합계는 약 9.7분.

## 결론

**합계 95/100, Blocker 0 · Critical 0 → PASS(>90).** 결함은 Minor 5건이다(D1~D5). 감점은 structure_gate의 D1 하나뿐이다. 필수 점검인 "test_repair_evidence_seed 무변경 통과"가 문자 그대로 충족되지 않았는데, 테스트 자체는 통과하고 의미는 T2-4 EXACT_MATCH로 별도 확인된다. 평가-OFF run이 UNTRAINED/UNEVALUATED(지표 null, fitness 없음)로 기록된다는 점은 단위 spy, 실 CPU API, 계약 스키마, 원본 대비 T2-10, 실서버 V4-11의 다섯 층에서 실행 근거로 확인했다.

## 재실행 기록

공통 명령: `CUDA_VISIBLE_DEVICES="" MBNAS_TEST_ROOT=<scratch>/test_root runtime/venv/bin/python -B -m pytest -p no:cacheprovider -q -rfEsx -m "not gpu and not live and not legacy_records and not host_data" <paths>`

| run | 대상 | exit | 결과 | wall | tree |
|---|---|---|---|---|---|
| r1 | `tests/owner/search/test_structure_{adversarial,cpu,observation,runtime_cpu}.py`, `test_repair_evidence_seed.py`, `test_generation_only.py` | 0 | 67 passed (14+7+23+3+1+19) | 45 s | 634a083b |
| r2 | `tests/owner/search/test_proposers.py`, `tests/owner/qa/test_search_fairness_engine.py`, `tests/owner/t2` | 0 | 47 passed, 7 skipped (proposers 12, fairness 10, comparator 22, T2 빠른 3; skip 7은 `MBNAS_TEST_T2=1` 전용 무거운 시나리오이며 아래 T2 스크립트 재실행이 같은 시나리오를 덮는다) | 138 s | 634a083b |
| r3 | `tests/owner/unit/test_protocol_promotion.py`, `test_protocol_fitness.py`, `tests/owner/orchestration/test_semantics.py` | 0 | 65 passed (26+23+16) | 21 s | 634a083b |
| r4 | `tests/owner/api/test_final_retrain.py`, `tests/owner/orchestration/test_final_selection.py` | 0 | 14 passed (10+4) | 24 s | 634a083b |
| r5 | `tests/owner/api/test_generation_real_cpu.py`, `test_off_run_guards.py`, `tests/owner/worker/test_generation_crash.py`, `tests/owner/api/test_generation_multi_seed.py`, `test_generation_freshness.py`, `tests/owner/projection/test_contract_150.py` | 0 | 179 passed (1+11+1+7+2+157) | 57 s | 634a083b |
| r6 | `tests/owner/search` 나머지 7파일, `tests/owner/orchestration/{test_engine_flow,test_open_generation_progress,test_retention}.py`, `tests/owner/qa/test_engine_negatives.py` | 0 | 99 passed | 134 s | 634a083b |
| r7 | `tests/owner/unit/test_schemas_contract.py -k "generated or evaluation_status or training_status or status_vocabulary"` | 0 | 9 passed, 184 deselected | 1 s | 76ff726a |
| r8 | r3·r4 파일 + `tests/owner/orchestration/test_retention.py` (동시 수정 뒤 재결속) | 0 | 90 passed | 69 s | 76ff726a (실행 전후 동일) |
| T2 | `runtime/venv/bin/python -B scripts/owner/t2_compare.py --out-dir <scratch>/t2 --work-root <scratch>/t2work` | 0 | 10/10 시나리오, mismatches 0 (EXACT_MATCH T2-4/5/9), snapshot guard 변경 없음(603 files, `1475baa5…`), core3 `aee93aa5` 전후 동일 | 91 s | core3 aee93aa5 |
| closure | `runtime/venv/bin/python -B scripts/dev/check_import_closure.py` | 0 | ok true, violations 0, unresolved 0, forbidden_strings 0 | — | 634a083b |
| probe | `<scratch>/probe_generated_candidate.py` (평가자 작성, scratchpad 전용) | 0 | 8/8 true (아래 OFF 절) | — | 634a083b |

디렉터리 단위로 `tests/owner/search/*`는 147/147, `tests/owner/orchestration/*`는 33/33 통과했다. 실패 0건.

## 기준별 채점

### 1. structure_gate — 20/25 (기준점 20: "사소한 누락(문서 1건·라벨) 외 충족")

근거:
- 구조 규칙(shape/merge/head): `tests/owner/search/test_structure_*` 47 passed (r1). T2-1 재실행에서 20/20 결정(status와 진단 코드)이 원본 monolith와 같다. FAIL 항목은 SHAPE_MISMATCH+MERGE_INPUT_MISMATCH, HEAD_INPUT_INVALID, HEAD_CLASS_MISMATCH, BLOCK_UNSUPPORTED 각 2건이고 나머지 12건은 PASS다. 허용 차이는 module_path 12건과 timestamp 18건뿐이다.
- repair: `test_repair_evidence_seed.py` 1 passed (r1). T2-4(seed 116, GA·IR 모두 row 9 C3TR→SPPF)가 원본 대비 EXACT_MATCH다.
- UNVERIFIED 의미: `test_structure_adversarial.py:73`, `test_structure_cpu.py:100,110,125`, `test_structure_observation.py:314-320`, `test_generation_only.py:129,137`, `test_generation_recovery_independent.py:113-147` 모두 통과(r1, r6).
- 금지 호출 spy: `test_generation_only.py:42-61`은 OFF에서 `_schedule_stage/_queue_evaluation/commit_evaluation/fitness/evaluate_gate/_parent_pool/_plan_baseline` 호출 시 실패하도록 막고, PROPOSE_GENERATION만 생기며 evaluations 0건임을 확인한다. `test_structure_cpu.py:92-110`(GET detail은 추론 금지)과 `test_generation_only.py:193-236`(두 번째 CPU 빌드 금지)도 통과.
- 분리(정적): `mbnas_core/search/structure.py:20-30`은 stdlib, `mbnas_contracts.structure_doc`, `mbnas_model_runtime`만 import한다. promotion/fitness/training_validation/admission은 `check_structure`를 참조하지 않는다(grep 0건). 원본 대비 import 외 차이는 12줄이고 전부 상수 이동이다. 이동된 상수 6개(SCHEMA, BACKEND, REPRESENTATION, CHECK_NAMES, EVIDENCE_LEVELS, CHECK_EVIDENCE)는 값이 동일하다(실행 비교 6/6 True).

빈틈(D1): `test_repair_evidence_seed.py`가 무변경이 아니다. 10-27 16:17에 63-66행이 바뀌었다. 원래의 `jsonschema.validate(blk, block_schema)`(저장 행 그대로 검증)가 `project_public("Block", fold_block(...))` 검증과 추가 assert 2개로 대체됐다. 계약 1.5.0에서 Block이 닫힌 스키마가 되면서 원래 줄이 실패했기 때문이다(`docs/internal/evidence/WP5/5B/owner_gate.txt:12769,23546`). seed 116 단언(이벤트 row 9/violation 10/C2PSA, C3TR→SPPF, position 9, before/after, lineage repair_events 없음)은 모두 남아 있다. 그러나 적응 기록이 없다: `docs/internal/evidence/WP1/TEST_ADAPTATIONS.md:247-248`에는 A6만 있고 WP4/4C README와 DECISIONS에도 해당 항목이 없다.

### 2. ga_random_prior — 25/25

근거:
- `test_proposers.py` 12 passed, `qa/test_search_fairness_engine.py` 10 passed (r2).
- T2 대조 기록: 최종 core 트리 기록은 `docs/internal/evidence/WP4/4C/t2/T2_REPORT.md`와 `t2_compare.log`(10-28 03:05, core3 `aee93aa5`, mismatch 0)이고 `pytest_t2.txt`(MBNAS_TEST_T2=1, 32 passed)가 있다. 평가자 재실행(위 T2 행)도 같은 core 트리에서 시나리오별 허용 건수(30/78/136/0/0/12/1396/970/0/261)까지 일치했다.
- gen0 공통 frozen prior: T2-3 `gen0_shared_with_random_arm` = {101, 202, 303: true}, ref와 new 모두.
- RNG 호출 순서: T2-2(Random gen0 seed 101/202/303) mismatch 0. T2-3 seed별 `rng_after` digest가 ref·new 동일. T2-9 seed stream 100행 EXACT_MATCH.
- elite·tie-break·동일 family 교차: T2-3 부모 pool은 family가 섞여 있다(yolo26n 5, yolo11n 3). fitness 동률(c000 = c003 = 0.61)에서 양쪽 모두 elite c003을 골랐다. fitness None 행은 제외됐고, 교차 부모는 같은 family(yolo11n×yolo11n, `mbnas_core/search/ga.py:126`)였다. 음성 사례 4종(L1 행, 미커밋, 중복, 유효 fitness 없음)은 양쪽 모두 ParentPoolError.
- Random의 부모 fitness 독립: `test_proposers.py:54,66`, `test_search_fairness_engine.py:22,33,39`, `test_semantics.py::test_random_proposals_never_receive_fitness_and_ga_uses_l0_pool` 통과.
- 정적: `search/ga.py`, `search/random.py`, `search/streams.py`는 원본 대비 import 외 차이 0줄.
- 비교기 엄격성: DD-A53 규칙(`scripts/owner/t2_compare.py:156-174`)은 문서 전체에서 1:1인 hash 매핑만 허용하고 T2-7/8/10에 한정된다. comparator 테스트 22 passed.

빈틈(감점 없음): 혼합 family pool·clone 강등(`ga.py:126,142`)을 직접 확인하는 owner 단위 테스트가 없다(D4). 정식 T2 디렉터리는 갱신되지 않았다(D2).

### 3. source_rung — 25/25

근거:
- `unit/test_protocol_promotion.py` 26, `unit/test_protocol_fitness.py` 23, `orchestration/test_semantics.py` 16 passed (r3). 최신 트리에서 재결속(r8)도 통과.
- runner의 fitness import 0건: `grep -rnE "^\s*(from|import)\s+mbnas_core|fitness_gate|evaluate_gate|commit_evaluation|_fitness_rung|fitness_and_gate" mbnas_runner/src --include=*.py`는 exit 1(0줄). "fitness" 문자열은 docstring 3곳에만 있다(`mbnas_runner/evaluation/handlers.py:5`, `evaluation/official_evaluation.py:4`, `training/handlers.py:20`). import closure exit 0, violations 0.
- 불변(정적): `protocol/fitness.py`, `protocol/promotion.py`, `scheduling/admission.py`, `scheduling/planner.py`, `scheduling/costs.py`는 원본 대비 import 외 차이 0줄. `engine.commit_evaluation`은 SOURCE stage 가중치로 fitness를 계산하고(`engine.py:566`) gate는 GA/IR에만 적용한다(`engine.py:577`). engine의 원본 대비 차이는 runner protocol 배선(WP-2 R3/R4), DD-A32 `with_device_used`(raw_metrics만 건드림), F5d revision 기록뿐이다.
- 동등성(재실행): T2-5 fitness/gate EXACT_MATCH(fitness 72, gate 144, fitness_and_gate 144건). T2-6 admission 15결정과 settle 12전이는 timestamp만 다르다. T2-7 엔진 흐름(job 34개, GA/IR 각 L0에서 PROMOTION_ELIGIBLE 4·REJECTED_BY_GATE 4, L1에서 SEARCH_RUNG_FINAL 4) mismatch 0.
- GPU(열람): `docs/internal/evidence/T7/dbviews/rerun_full_b_run-6b46da7fd4a3.json`(tree 9dafe4d0, 18:21Z)에서 run COMPLETED, PROPOSE_GENERATION 8과 CANDIDATE_TRAIN 32 SUCCEEDED, EVALUATED 32행(latency measured 32), core가 계산한 fitness 0.397–0.700. `raw_metrics.handler_fitness`는 0/32로 runner가 fitness를 보내지 않았다. L0 16×PROMOTION_ELIGIBLE, L1 16×SEARCH_RUNG_FINAL. `t7_rerun_full_summary_20261027T181305Z.json`에서 b PASS.

빈틈(감점 없음): DD-A15 런타임 단언(`tests/owner/training/test_job_gpu.py:90`, `test_final_job_gpu.py:69`)은 gpu+host_data 표지라 이 트리에서 실행 근거가 없다(확인 못 함). 간접 근거는 T7의 handler_fitness 0/36이다.

### 4. baseline_final — 25/25

근거:
- `api/test_final_retrain.py` 10, `orchestration/test_final_selection.py` 4 passed (r4). 최신 트리 r8도 통과.
- baseline 별도 예산·비게이트: `engine.py:14-15`, `engine.py:577`(gate는 SEARCH_ARMS만), `engine.py:700-705`(Baseline은 gate 없이 진행). `test_semantics.py:114-133`(baseline_s 상한 없으면 거부), `:196`(Baseline 원장 billed_arm None). `qa/test_engine_negatives.py:198-311`(원장의 billed/physical·baseline/final 분리, Baseline overshoot) 18 passed (r6). T2-7 재실행에서 Baseline L0/L1 gate None, Baseline 원장 billed_arm None 2건이 ref·new 동일.
- final retrain split·초기화·비교군: `test_final_retrain.py:99`(eval_split final_eval, arm Final), `:109`(rung FINAL, comparison_group final, split_hash = final manifest), `:110`(gate None, fitness 표시), `:270-291`(search는 train/search_val만 읽고 final_eval은 child에서만), `:294-307`(FINAL_SPLIT_LEAK). `test_final_selection.py:46-47,76-80`(동률 순서 AP→latency→candidate_id), `:92`(init scratch, pinned recipe). `final_retrain.py`의 원본 대비 차이는 F5d revision과 DD-A46 `check_fresh` 훅을 더한 것뿐이고 선택 로직은 같다. T2-8(최종 선택과 child run) mismatch 0.
- GPU(열람): `rerun_full_h_run-9f31f93d3de4.json`(tree 9dafe4d0)에서 FINAL_RETRAIN child run(parent = b run) COMPLETED, FINAL_RETRAIN 4 SUCCEEDED, Final 4행 comparison_group final, gate None, fitness 0.399–0.400. summary에서 h PASS.

빈틈(감점 없음): "baseline 평가의 gate는 None"을 명시하는 CPU 단위 단언이 없다. 현재는 T2-7 엄격 비교로만 확인된다(D5).

## 평가-OFF run의 UNTRAINED/UNEVALUATED 기록 확인 (요청 항목)

| 층 | 근거 | 결과 |
|---|---|---|
| 단위 spy | `tests/owner/search/test_generation_only.py:42-61` | 학습·평가·fitness·승급·baseline 계획 미호출, job은 PROPOSE_GENERATION만, evaluations 0 (r1 통과) |
| 실 CPU API | `tests/owner/api/test_generation_real_cpu.py:43-47,71-75` | 실제 owner worker로 실행. snapshot `candidates == []`, 생성 후보 2개 NOT_EVALUATED에 ap/recall/latency_ms/fitness null. 저장 audit.performance NOT_EVALUATED와 ap null, evaluations 0, job은 PROPOSE_GENERATION만, Snapshot 계약 유효 (r5 통과) |
| 계약 | `mbnas_contracts/schemas/records.py:428-451`, `tests/owner/unit/test_schemas_contract.py:364-376` | `evaluation_status: Literal['NOT_EVALUATED']`, 지표·fitness 타입 None, training_status 필수 (r7 9 passed). probe: 지표·fitness가 null이 아니거나 EVALUATED이면 5/5 거부, null 허용, `projection._generated`는 입력이 TRAINED여도 UNTRAINED로 강제(`projection.py:360`) |
| 원본 대비 | T2-10 재실행 | ref·new 모두 evaluations 0, PROPOSE_GENERATION 2, 후보 4개 모두 performance {ap, fitness, latency_ms, recall: null, NOT_EVALUATED}, mismatch 0 |
| 실서버 | `docs/internal/evidence/WP6/V4B/README.md:23`, `e2e_steps.jsonl:33` | GeneratedTable과 Lineage에 "UNTRAINED · UNEVALUATED" 6/6 (기능 PASS, Playwright 실패 사유는 soft axe뿐). 평가자가 `runtime/test_root/web_real_stack/20261027T181429Z/core/db/mbnas.sqlite3`를 읽기 전용(`mode=ro`)으로 조회: run-22622a76a4a4는 evaluation_enabled false, seeds [101,202,303] × candidate_count 2 = 6 후보, evaluations 0, PROPOSE_GENERATION 3, 6개 모두 지표 null·NOT_EVALUATED, structure PASS. V4B README:34의 "확인 못 함"(6행의 원인)이 여기서 풀린다: candidate_count는 seed별 개수다 |

빈틈: 실제 OFF snapshot에서 `training_status == "UNTRAINED"` 값 자체를 단언하는 Python 테스트는 없다. 계약 enum은 TRAINED도 허용한다(D3).

## 결함

| ID | 심각도 | 소유 | 파일 | 내용 | 수정안 |
|---|---|---|---|---|---|
| D1 | Minor | WP-4C(테스트) / 설계자 승인 | `tests/owner/search/test_repair_evidence_seed.py:63-66` | "무변경 통과" 게이트 테스트가 1.5.0 투영 때문에 수정됐는데 적응 기록·DD 승인이 없다(의미 단언은 유지, T2-4 EXACT) | TEST_ADAPTATIONS에 행을 추가하고 DECISIONS에 근거(DD-A19 D1, DD-A42, DD-A53 a)를 기록한다. 원래 줄은 `docs/public/contracts/history`의 1.4.3 Block 스키마로 저장 행 검증을 남겨 문자 그대로 보존하고, 투영 검증은 추가 단언으로 둔다 |
| D2 | Minor | T2/V4(P 단계) | `docs/internal/evidence/T2/T2_REPORT.md` | 정식 T2 증거는 W4 이전 트리(`a76f7687`, 10-27 14:55) 기준이다. 최종 core 결과는 `WP4/4C/t2`(`aee93aa5`)에만 있고 core는 03:26-03:27에 다시 바뀌었다(`04ee95d1`) | 마지막 core 수정 뒤 `scripts/owner/t2_compare.py`를 재실행해 `evidence/T2`를 갱신하거나 권위 실행·트리 hash를 가리키는 안내를 둔다(DD-A48 c). VALIDATION_REPORT에서 인용 |
| D3 | Minor | WP-4B/4C 테스트 | `tests/owner/api/test_generation_real_cpu.py:46-47` | 생성 후보 training_status 값 단언 누락 | `assert all(c["training_status"] == "UNTRAINED" for c in generated)`를 추가하고 detail에도 같은 단언 |
| D4 | Minor | search 테스트 | `tests/owner/search/test_proposers.py` | `ga.py:126,142`(동일 family 짝, 짝 없으면 clone 강등 note)의 직접 단위 테스트가 없다(T2-3이 간접 확인) | 혼합 family pool에서 교차 부모 family 동일, 단독 family면 "degraded to clone" note를 단언하는 테스트 추가 |
| D5 | Minor | orchestration 테스트 | `tests/owner/orchestration/test_engine_flow.py` | baseline 평가 gate None·비승급을 명시하는 단언이 없다(T2-7만 확인) | Baseline 평가 행은 `gate is None`, 후보 상태는 EVALUATED(PROMOTION_* 아님)를 단언 |

실패(failed) 항목은 없다. 아래 "확인 못 함"은 실패가 아니라 미검증이다.

## 합계

structure_gate 20 + ga_random_prior 25 + source_rung 25 + baseline_final 25 = **95/100 (>90)**, Blocker 0, Critical 0 → **PASS**. D2–D5는 감점하지 않았다. 각 기준의 필수 점검이 현재 트리에서 실행 근거를 갖기 때문이다. D1만 필수 점검 문구("무변경")를 직접 어기므로 20으로 매겼다.

## 확인 못 함

- DD-A15 런타임 단언(runner 결과에 fitness/gate 키 없음)을 GPU 테스트로 실행하지 못했다(GPU 사용 금지). 간접 근거는 T7 rerun_full b/h의 handler_fitness 0/36.
- T7 GPU 증거는 동결 사본 tree `9dafe4d0`(10-27 18:02Z rsync)에 묶여 있고 현재 트리(`634a083b` → `76ff726a`)와 다르다. full 재실행은 평가 도중 끝났다(summary 03:27, RERUN_README 03:28 갱신). 나는 b/h 행과 dbview만 열람했다.
- 03:26-03:27 동시 수정 뒤에는 r3·r4·retention만 재실행했다(r8, 90 passed). r1/r2/r5/r6과 T2는 `634a083b` / core3 `aee93aa5`에 묶여 있다. 수정 파일이 구조·탐색·T2 결정 경로 밖에 있다는 판단은 정적 판단이며 재실행으로 확인하지 않았다.
- `MBNAS_TEST_T2=1` 무거운 pytest 7건은 직접 돌리지 않았다. 같은 시나리오를 스크립트 재실행이 덮는다.
- 평가자 원시 로그는 scratchpad(휘발성)에만 있다. TARGET_ROOT에 영속하는 실행 산출물은 `WP4/4C/t2`, `T7/dbviews/rerun_full_*`, `WP6/V4B`와 이 보고서다.
- (범위 밖 관찰) DECISIONS DD-A55와 TASK_STATE의 마지막 항목에 "10-28 04:2x/04:3x" 시각이 적혀 있는데, 이 항목들이 나타났을 때 호스트 시계는 03:2x였다. 기록 시각이 어긋나 있다.
- (범위 밖 관찰) `scripts/dev/__pycache__/package_build.cpython-310.pyc`가 03:17:43에 새로 생겼다. 같은 시각 다른 에이전트가 `-B` 없이 `runtime/venv/bin/python -m pytest -q -p no:cacheprovider tests/public/packaging ...`를 돌리고 있었다(ps 확인). 이 평가의 명령은 모두 `-B`로 실행했고 `package_build`를 참조하지 않는다(grep exit 1). DD-A29 기준으로 P 단계 ZIP 전에 정리해야 한다.

# C03 EVAL_2 — NAS 의미 재채점 (수정 라운드 이후)

- 기준셋: C03 v1.0 (`docs/internal/criteria/C03.json`). EVAL_1(`EVAL_1.md`, REVIEW_LOG C03 iter1 = 95) 이후 재채점이다. 기준점(25/20/15/0)과 규칙은 EVAL_1과 같다. 점수는 평가자 의견이며 판정은 설계자가 한다.
- 평가자: claude-opus-5-5 (Opus 5.5), 독립 평가자.
- 시각: 2026-10-28 07:43–08:01 KST.
- Tree hash (`runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`의 sha256 줄; `| tail -3`은 닫는 괄호만 출력):
  - 시작 07:43과 종료 07:59가 같다: `sha256:5a7bc5920ac3c1fe630a45c8ea0dd4661cb754661a90da5c09441fb7237c4223` (221 files). 평가 중 패키지·tests·scripts 파일 변경 0, 새 `.pyc` 0.
  - core3(contracts+model_runtime+core, t2_compare 알고리즘): `sha256:27d6934b22a3219c59c82a62b00d468462e5fad3cd9f0c024034e3e5ccac2ee5`. `WP7/7V_FINAL/t2`가 기록한 값과 같다.
- 실행 조건: EVAL_1과 같다. cwd는 TARGET_ROOT, `CUDA_VISIBLE_DEVICES=""`, `-B`, `-p no:cacheprovider`, `MBNAS_TEST_ROOT=<scratch>/test_root`. GPU·/data는 쓰지 않았고 프로젝트에는 이 보고서만 썼다. 원시 로그·junit·T2 산출물은 `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c03e2/`(휘발성)에 있다. 다른 평가자들이 동시에 테스트를 돌려 load가 60–100(48코어)이었으므로 wall 시간이 EVAL_1보다 길다.

## 결론

**합계 100/100, Blocker 0 · Critical 0 → PASS(>90).** EVAL_1 결함 D1·D3·D4·D5는 해결됐다(D1은 DD-A57 승인 이탈). D2는 실질적으로 해결됐고 표기 잔여(D2')만 남았다. 새로 Minor 2건(D6 시험 공백, D7 기록 줄 번호 표류)을 기록했는데, 둘 다 필수 점검의 실행 근거를 건드리지 않아 감점하지 않았다. DD-A63(a)의 NOT_EVALUATED 저장 의미는 C03의 "가짜 지표 없음 / OFF run UNTRAINED·UNEVALUATED" 요구를 어기지 않는다(아래 전용 절과 테스트 인용). 현재 트리에서 525 passed, 7 skipped, 0 failed이고 T2는 10/10 mismatch 0이다.

## 재실행 기록 (현재 트리 5a7bc592)

공통 명령: `CUDA_VISIBLE_DEVICES="" MBNAS_TEST_ROOT=<scratch>/test_root runtime/venv/bin/python -B -m pytest -p no:cacheprovider -q -rfEsx -m "not gpu and not live and not legacy_records and not host_data" <paths>`

| run | 대상 | exit | 결과 | wall |
|---|---|---|---|---|
| e1 | `tests/owner/search/test_structure_{adversarial,cpu,observation,runtime_cpu}.py`, `test_repair_evidence_seed.py`, `test_generation_only.py` | 0 | 67 passed (14+7+23+3+1+19) | 229 s |
| e2 | `tests/owner/search/test_proposers.py`, **`test_ga_same_family_crossover.py`(신규)**, `tests/owner/qa/test_search_fairness_engine.py`, `tests/owner/t2` | 0 | 49 passed, 7 skipped (12+2+10, comparator 22, T2 빠른 3; skip 7은 `MBNAS_TEST_T2=1` 전용 무거운 시나리오) | 525 s |
| e3 | `tests/owner/unit/test_protocol_promotion.py`, `test_protocol_fitness.py`, `tests/owner/orchestration/test_semantics.py`, **`test_dd_a62_not_evaluated_search.py`** | 0 | 67 passed (26+23+16+2) | 25 s |
| e4 | `tests/owner/api/test_final_retrain.py`, `tests/owner/orchestration/test_final_selection.py`, **`test_semantics_baseline_ungated.py`(신규)** | 0 | 15 passed (10+4+1) | 26 s |
| e5 | `tests/owner/api/test_generation_real_cpu.py`, **`test_generation_real_cpu_training_status.py`(신규)**, `test_off_run_guards.py`, `tests/owner/worker/test_generation_crash.py`, `tests/owner/runner_protocol/test_dd_a62_cpu_claim_and_commit.py`, `test_dd_a60_cpu_training_validation.py`, `test_dd_a60_cpu_fleet.py`, `tests/public/runner/test_dd_a60_cpu_evaluation.py`, `tests/owner/unit/test_schemas_contract.py` | 0 | 224 passed (1+1+11+1+7+1+4+5+193) | 364 s |
| e6 | `tests/owner/search` 나머지 8파일(`test_generation_recovery_independent_journal.py` 포함), `tests/owner/orchestration/{test_engine_flow,test_open_generation_progress,test_retention}.py`, `tests/owner/qa/test_engine_negatives.py` | 0 | 103 passed | 530 s |
| T2 | `runtime/venv/bin/python -B scripts/owner/t2_compare.py --out-dir <scratch>/t2 --work-root <scratch>/t2work` | 0 | 10/10 mismatch 0 (EXACT_MATCH T2-4/5/9). snapshot guard 불변(603 files, `1475baa5…`), core3 `27d6934b` 전후 동일, 격리 위반 0. 시나리오별 verdict와 allowed 수(30/78/136/0/0/12/1396/970/0/261)가 `7V_FINAL/t2/T2_SUMMARY.json`과 같다 | 301 s |
| closure | `runtime/venv/bin/python -B scripts/dev/check_import_closure.py` | 0 | ok true, violations 0, unresolved 0, forbidden_strings 0, excepted 1(base.py legacy hook, DD-A26/DD-A58) | — |
| grep | `grep -rnE "^\s*(from\|import)\s+mbnas_core\|fitness_gate\|evaluate_gate\|commit_evaluation\|_fitness_rung\|fitness_and_gate" mbnas_runner/src --include=*.py` | 1 (0줄) | runner에 core·fitness·gate import 없음. "fitness" 문자열은 docstring 5줄뿐(`evaluation/handlers.py:5,21,92`, `evaluation/official_evaluation.py:4`, `training/handlers.py:20`) | — |
| probe | `<scratch>/../c03/probe_generated_candidate.py` (EVAL_1 평가자 probe, scratchpad 전용) | 0 | 8/8 true: `GeneratedCandidate`는 지표·fitness 값과 EVALUATED를 거부, null 허용, `projection._generated`는 UNTRAINED로 강제 | — |

디렉터리 단위로 `tests/owner/search/*`는 153/153(e1 67 + e2 14 + e6 72), `tests/owner/orchestration/*`는 36/36(e3 18 + e4 5 + e6 13) 통과했다. 합계 525 passed, 7 skipped, 0 failed.

## EVAL_1 결함의 처리 상태

| ID | EVAL_1 내용 | 상태 | 근거 |
|---|---|---|---|
| D1 | "무변경" 게이트 테스트 `test_repair_evidence_seed.py:63-66` 수정에 기록·승인 없음 | **해결(승인된 이탈)** | `docs/internal/DECISIONS.md:70` DD-A57 사후 승인. `docs/internal/evidence/WP4/TEST_ADAPTATIONS.md` §A(7-17행)에 변경 줄·사유·불변 단언이 기록됐다. 테스트 파일 sha256 `ef19e539…`는 EVAL_1 때와 같다. e1에서 통과 |
| D2 | 정식 T2 증거가 W4 이전 트리 기준 | **실질 해결, 표기 잔여(D2')** | 최종 트리 재실행 `docs/internal/evidence/WP7/7V_FINAL/t2_compare.txt`(exit 0, 77 s, 10/10, mismatch 0)와 `7V_FINAL/t2/T2_SUMMARY.json`(core3 `27d6934b`, 현재와 같음), `7V_FINAL/pytest_t2.txt`(MBNAS_TEST_T2=1, 32 passed), `docs/internal/VALIDATION_REPORT.md:23` 회귀 행에서 인용. 평가자 재실행도 같은 결과(위 T2 행). 잔여 표기는 아래 D2' |
| D3 | 실제 OFF snapshot에서 `training_status == "UNTRAINED"` 값 단언 없음 | **해결** | `tests/owner/api/test_generation_real_cpu_training_status.py:54-56,62`: 생성 후보 `["UNTRAINED","UNTRAINED"]`, 응답 행에 `"TRAINED"` 없음, detail마다 UNTRAINED와 `evaluations == []`, job은 PROPOSE_GENERATION만, evaluations 0. e5에서 통과 |
| D4 | 동일 family 교차 직접 단위 테스트 없음 | **해결** | `tests/owner/search/test_ga_same_family_crossover.py:33-52`: 혼합 pool, seed 6개, 교차 부모 family 동일, parent-B 후보도 같은 family뿐, 두 family 모두 parent A로 등장. `:55-78`: 단독 family면 clone 강등 note와 A 구조 유지, 다른 family는 교차, 두 분기 모두 실행됐음을 단언(무의미한 통과 방지). e2에서 통과 |
| D5 | baseline gate None 명시 단언 없음 | **해결** | `tests/owner/orchestration/test_semantics_baseline_ungated.py:41-60`: 모든 탐색 후보를 거부하는 gate(`score_ge=0.99`)를 대조군으로 둔다(GA 전부 REJECTED_BY_GATE). baseline은 두 stage 모두 평가되고 각 행 `gate is None`이며 fitness가 있다. 후보는 EVALUATED로 끝나고 gate 상태가 아니다. Baseline 원장 billed_arm None. snapshot에서는 GA 행만 gate를 가진다. e4에서 통과 |

## DD-A63 (a) NOT_EVALUATED 의미와 C03 요구의 정합성

**판정: 위반 아님.** DD-A62(a)/DD-A63(a)는 runner의 job 장치가 CUDA가 아니어서 평가하지 못한 학습 결과를 위한 새 저장 형태다. `SEARCH_COMPLETE` + `performance_status=NOT_EVALUATED` + 사유 `EVALUATION_REQUIRES_CUDA`로 남는다. 지표·fitness·gate는 null, latency·evaluation_hash는 pending, checkpoint는 유지되며 승급·GA 부모·`candidate.evaluated`는 없다. C03의 "가짜 지표 없음"과 "OFF run은 UNTRAINED/UNEVALUATED" 요구를 어기지 않는다. 다섯 층의 근거:

| 층 | 근거 (file:line) | 확인 내용 | 재실행 |
|---|---|---|---|
| runner payload | `mbnas_runner/src/mbnas_runner/evaluation/handlers.py:86-111`, `tests/public/runner/test_dd_a60_cpu_evaluation.py:61-71` | ap/ap_50/recall/latency_ms/size_mb/evaluation_hash/split_hash/device_uuid 모두 None, latency pending, `raw_metrics`는 상태·사유·detail·job_device뿐(지표 값 없음), checkpoint 참조 유지, CUDA API 미호출 | e5 5 passed |
| core 커밋(탐색 arm) | `mbnas_core/src/mbnas_core/orchestration/engine.py:576-585`(분기), `:655-741`(`commit_not_evaluated`); `tests/owner/orchestration/test_dd_a62_not_evaluated_search.py:69-136` | GA 행: ap/recall/latency_ms/size_mb/fitness 5개 None(85), gate None, latency/evaluation_hash pending(86), split_hash None, immutable 아님(87), checkpoint 유지(88), `raw_metrics.reported` 없음(92). 승급 없음: rung L0만(81), 승급 job 없음(109). GA 부모 불가: gen 1 `parent_pool == []`(113). 세대 통계에서 evaluated 0·failed = planned, representative None(117-118). `candidate.evaluated` 없음(121). replay guard: NOT_EVALUATED payload가 기존 immutable 측정을 바꾸지 못함(125-134). 같은 run의 IR arm은 측정되고 L1로 승급, baseline도 EVALUATED(94-97) | e3 2 passed |
| 기존 경로 불변 guard | `test_dd_a62_not_evaluated_search.py:139-159` | NOT_EVALUATED를 선언하지 않은 채 지표만 빠진 payload는 종전처럼 EVAL_FAILED METRIC_MISSING, performance_status None | e3 |
| 명시 학습·claim | `tests/owner/runner_protocol/test_dd_a62_cpu_claim_and_commit.py:119-175`(명시 학습 COMPLETED, 지표 5개 None, gate None, checkpoint 유지, candidate.evaluated 없음, 공개 투영 계약 유효·fitness None·latency pending), `:199-266`(fleet-aware claim) | 가짜 지표 없음. GPU runner claim은 불변(`test_gpu_runner_claims_are_unchanged`) | e5 7 passed |
| 검증 단계 | `tests/owner/runner_protocol/test_dd_a60_cpu_fleet.py:119-124` | CPU 전용 fleet의 평가 ON 탐색은 검증에서 EVALUATION_REQUIRES_CUDA로 거부. 학습과 OFF 생성은 허용 | e5 4 passed |

추가 확인:
- GA 부모·종료·승급 로직은 원본과 같다. 원본 snapshot과 AST로 비교한 결과 `_parent_pool`(`engine.py:336-353`, `status == "EVALUATED"`만), `_advance`, `_maybe_next_generation`, `_refresh_generations`, `_schedule_stage`, `_candidate_failed`, `_evaluation_failed`, `_split_violation`은 동일하다. `settle_run`은 lazy import 경로 한 줄만 다르다. NOT_EVALUATED 행은 `status == "SEARCH_COMPLETE"`이므로 부모 pool에 들어갈 수 없다.
- finalist 제외: `mbnas_core/src/mbnas_core/orchestration/final_retrain.py:92-94`는 immutable이고 `EVALUATED`이며 마지막 rung에서 latency가 measured인 행만 받는다. NOT_EVALUATED 행은 세 조건 모두에 걸려 제외된다. 다만 이를 고정하는 테스트는 없다(D6, 정적 근거).
- 공개 노출: 공개 계약 `CandidateEvaluation.raw_metrics`는 닫힌 객체이고 `reported`가 허용목록에 없다. 행 수준 `performance_status`는 "NOT_EVALUATED on rows that measured nothing"으로 문서화돼 있다.
- OFF 의미에는 영향이 없다. 평가 OFF run에는 학습 job이 없어 `commit_not_evaluated`에 도달하지 않는다. OFF 테스트(e1 `test_generation_only`, e5 `test_generation_real_cpu`·`test_generation_real_cpu_training_status`·`test_off_run_guards`)와 T2-10 원본 대조(evaluations 0, 후보 4개 지표 null·NOT_EVALUATED, mismatch 0)가 현재 트리에서 통과한다.
- 표시 어휘: 학습했으나 평가하지 못한 행은 "UNTRAINED · UNEVALUATED"가 아니라 "학습 완료 · 미평가"(training_status TRAINED + NOT_EVALUATED)로 보인다. 사실과 맞는 표기다. 근거는 `mbnas_web/tests/wp6a_badges_workers.test.tsx:99`와 실행 기록 `docs/internal/evidence/WP7/PREPACK/web_gates.txt`(vitest 593/593, 06:36; 구 `evaluation.ts`로 바꾼 음성 대조에서 새 2건 실패). vitest는 프로젝트 안 `node_modules/.vite`에 쓰므로 평가자가 직접 돌리지 않았다.
- C03 source_rung의 "commit_evaluation 불변"과의 관계: 측정 경로는 불변이다. T2-5 EXACT_MATCH와 T2-7 mismatch 0이 현재 core3에서 나왔고(7V_FINAL과 평가자 재실행), guard 테스트가 기존 EVAL_FAILED 경로가 그대로임을 보인다. 새 분기는 원본이 받지 못한 입력(CPU runner 평가; DD-A60(4)에 따르면 원본은 AssertionError로 job 실패)만 처리한다. 설계자 결정 DD-A62/A63으로 승인된 확장이다.

## 기준별 채점

### 1. structure_gate — 25/25 (기준점 25: 증거 파일·명령·exit code 모두 존재, 재실행 확인)
- 필수 점검: `test_structure_*` 47 passed. `test_repair_evidence_seed` 통과. 이 테스트의 1.5.0 적응은 DD-A57로 승인되고 `WP4/TEST_ADAPTATIONS.md` §A에 기록됐다(EVAL_1의 감점 사유 해소). 금지 호출 spy `test_generation_only.py:42-61`과 `test_structure_cpu.py:92-110` 통과(e1).
- 원본 대비: T2-1은 ALLOWED_DIFF 30(module_path 12 + timestamp 18), mismatch 0. T2-4(seed 116 repair)는 EXACT_MATCH(평가자 재실행).
- UNVERIFIED 의미: EVAL_1에서 인용한 단언 파일들이 모두 e1·e6에서 통과.
- 정적: `search/structure.py`의 원본 대비 import 외 차이는 12줄로 EVAL_1과 같다(상수 이동뿐). `mbnas_contracts.structure_doc` 상수 6개는 원본과 값이 같다(재실행 6/6 True). 구조 gate 모듈은 promotion/fitness/validator를 import하지 않는다.

### 2. ga_random_prior — 25/25
- 필수 점검: `test_proposers.py` 12, `qa/test_search_fairness_engine.py` 10 passed, 신규 `test_ga_same_family_crossover.py` 2 passed(e2).
- T2 대조 기록: `7V_FINAL/t2`(현재 core3)와 평가자 재실행 모두 mismatch 0. T2-3 ref·new 모두 `gen0_shared_with_random_arm` {101,202,303: true}, elite c003(동률 0.61 tie-break), 교차 부모 yolo11n×yolo11n(혼합 family pool), `rng_after` digest `c4aba00f…`/`57b44e73…`/`8dfa9d13…`가 양쪽 같다. T2-2 mismatch 0, T2-9 EXACT_MATCH.
- 정적: `search/ga.py`, `random.py`, `streams.py`는 원본 대비 import 외 차이 0줄.

### 3. source_rung — 25/25
- 필수 점검: `unit/test_protocol_promotion.py` 26, `orchestration/test_semantics.py` 16 passed(+fitness 23, DD-A62 탐색 2; e3). runner의 fitness import 0건(grep exit 1), closure 0 위반.
- 불변: `protocol/fitness.py`, `protocol/promotion.py`, `scheduling/admission.py`, `scheduling/planner.py`, `scheduling/costs.py`는 원본 대비 import 외 차이 0줄. T2-5 EXACT_MATCH, T2-6과 T2-7은 mismatch 0(현재 core3). `commit_evaluation`에 생긴 NOT_EVALUATED 분기(`engine.py:576-585`)는 DD-A62(a)/DD-A63(a)로 승인된 확장이다. 측정 경로가 그대로임은 위 T2와 guard 테스트로 확인했다. 승급·종료 함수(`_advance`, `_maybe_next_generation`, `_refresh_generations`, `_parent_pool`)는 원본과 AST 동일.
- GPU 근거는 EVAL_1과 같고(T7 rerun_full b, tree 9dafe4d0) DD-A56(c)에 따라 이월됐다. 재실행하지 않았다.

### 4. baseline_final — 25/25
- 필수 점검: `api/test_final_retrain.py` 10, `orchestration/test_final_selection.py` 4 passed, 신규 `test_semantics_baseline_ungated.py` 1 passed(e4).
- 원본 대비: T2-7 ref·new 모두 Baseline 행 gate None(L0·L1)과 billed_arm None 2건. T2-8(최종 선택·child run) mismatch 0.
- `final_retrain.py`의 원본 대비 차이는 EVAL_1과 같다(19줄; F5d revision과 DD-A46 훅 추가뿐). finalist 필터(`:92-94`)는 NOT_EVALUATED 행을 제외한다(D6: 테스트 고정 없음, 감점 없음).

## 결함 (EVAL_2 기준 열린 항목)

| ID | 심각도 | 소유 | 파일 | 내용 | 수정안 |
|---|---|---|---|---|---|
| D2' | Minor (EVAL_1 D2의 잔여) | WP-8 / 설계자 | `docs/internal/VALIDATION_REPORT.md:10`, `docs/internal/evidence/T2/` | T2 행이 "확인 못 함 (row owner: WP-8)"이고 증거 칸이 갱신되지 않은 `T2/`(10-27 14:55, W4 이전 트리)를 가리킨다. 최종 결과(`WP7/7V_FINAL/t2`, core3 `27d6934b`, 10/10 mismatch 0)는 같은 문서 23행 회귀 행에만 있다. 또 `scripts/owner/t2_compare.py:753,761`이 보고서 머리에 `산출: docs/internal/evidence/T2/…`와 `<OUT> = docs/internal/evidence/T2`를 고정 문구로 써서 `7V_FINAL/t2/T2_REPORT.md` 머리가 실제 출력 위치(`--out-dir`)와 다르다(표기만 틀림) | T2 행을 PASS로 채우고 `WP7/7V_FINAL/t2_compare.txt`, `7V_FINAL/t2/T2_SUMMARY.json`, `7V_FINAL/pytest_t2.txt`를 인용한다(이 재채점의 재실행도 일치). `evidence/T2/`에는 최종 실행을 가리키는 README를 둔다. 보고서 머리는 `--out-dir` 값을 쓰도록 고친다 |
| D6 | Minor (신규, 시험 공백) | orchestration/final 테스트 | `tests/owner/orchestration/test_dd_a62_not_evaluated_search.py` 또는 `tests/owner/api/test_final_retrain.py` | NOT_EVALUATED(EVALUATION_REQUIRES_CUDA) 탐색 행이 final-retrain finalist가 되지 않음을 고정하는 테스트가 없다. 현재는 `final_retrain.py:92-94` 필터(immutable·EVALUATED·measured)라는 정적 근거뿐이다 | 1-rung 프로토콜에서 GA arm을 NOT_EVALUATED로 만든 뒤 `POST final-retrain`을 보내 GA arm finalist 0(전부 NOT_EVALUATED면 409 NO_FINALIST)을 단언한다 |
| D7 | Minor (기록 표류) | WP-7 FIX1 기록 | `docs/internal/evidence/WP4/TEST_ADAPTATIONS.md` §B (C03-D5 행) | "Line trace: engine.py 577-578 and 700-704 executed"는 06:07 engine 변경(DD-A62) 이전 줄 번호다. 현재 gate는 `engine.py:599-600`, Baseline 분기는 `:834-838`이다 | 함수 이름(`commit_evaluation` gate 분기, `_advance` Baseline 분기)으로 고치거나 줄 번호를 현재 트리에 맞춘다 |

D1, D3, D4, D5는 해결됐다(위 표). 실패(failed) 항목은 없다.

## 합계

structure_gate 25 + ga_random_prior 25 + source_rung 25 + baseline_final 25 = **100/100 (>90)**, Blocker 0, Critical 0 → **PASS**. EVAL_1 대비 +5다(D1 승인·기록). 열린 D2'·D6·D7은 감점하지 않았다. 각 기준의 필수 증거(파일·명령·exit code)가 현재 트리에서 실행으로 모두 확인됐고, 이 세 항목은 그 증거 자체가 아니라 전역 보고서 표기, 추가 시험, 기록 줄 번호에 관한 것이기 때문이다. 설계자가 D2'(T2 표기)를 "라벨 누락"으로 보면 ga_random_prior의 기준점 20 적용을 검토할 수 있다. 그 경우 합계는 95로 여전히 PASS다.

## 확인 못 함

- GPU 경로: DD-A56(c)에 따라 GPU 증거는 TREE_HASH `9dafe4d0` 기준이다. 그 뒤 변경(DD-A57~A63 포함)은 CPU 검증뿐이고, GPU 사용 금지와 캠페인 재개 때문에 재실행하지 않았다.
- 배포 ZIP과 평가 트리의 결속: 7V_FINAL은 5패키지 tree hash를 기록하지 않는다(T2의 core3 `27d6934b`만 기록하며 현재와 같다). 평가 트리 `5a7bc592`가 최종 ZIP 내용과 같은지는 확인하지 않았다.
- 웹 "학습 완료 · 미평가" 라벨(DD-A63 a)은 `WP7/PREPACK/web_gates.txt`의 실행 기록만 인용했다. vitest가 프로젝트 안 `node_modules/.vite`에 쓰므로 직접 돌리지 않았다.
- `MBNAS_TEST_T2=1` 무거운 pytest 7건은 직접 돌리지 않았다. `7V_FINAL/pytest_t2.txt` 32 passed가 있고, 같은 시나리오를 평가자 스크립트 재실행이 덮는다.
- D6(NOT_EVALUATED 행의 finalist 제외)는 정적 근거뿐이다.
- 평가자 원시 로그는 scratchpad(휘발성)에만 있다.

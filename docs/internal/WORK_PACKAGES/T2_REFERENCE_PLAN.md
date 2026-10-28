# T2 core 동등성 대조 계획 v1 (설계자, 2026-10-23 23:0x) — owner 전용

목적: 분할·vendoring 후의 `mbnas_core`가 원본(스냅샷 18c4815+delta)과 **같은 결정**을 내리는지 고정 입력으로 대조한다. reference 구현은 owner 환경에서만 실행한다(공개 ZIP에 포함 금지).

## 1. reference 실행 방식
- `tests/owner/t2/reference_runner.py`: 서브프로세스에서 `sys.path=[<snapshot>/application/MBNAS_server/src, <snapshot>]`로 **원본 mbnas_server**를 import(ETRI pool은 `MBNAS_ETRI_NAS_ROOT=<snapshot root>`; 스냅샷 안의 `yolo_ga_runtime`을 읽기 전용 import, `sys.dont_write_bytecode=True`, `PYTHONDONTWRITEBYTECODE=1`로 스냅샷에 pyc 생성 금지). 같은 venv(third-party 동일). 출력은 canonical JSON(stdout).
- 대상(new) 실행: `mbnas_core` import로 같은 시나리오 실행 → canonical JSON.
- 비교: 정확 일치(JSON 동등) 또는 시나리오별 명시 허용 차이(아래 "허용 차이" 열; 예: space_hash/prior_hash는 /2→/3로 달라지므로 `search_space.version`만 비교하고 hash는 기록만).

## 2. 시나리오 (고정 입력)
| ID | 시나리오 | 입력 | 비교 대상 | 허용 차이 |
|---|---|---|---|---|
| T2-1 구조 gate | 10개 YAML(yolo11n/yolo26n native, 변형 2종, 사용자 오류 YAML: Concat 불일치·head stride 역전·미지 모듈·nc 불일치·거대 행) × input 64/160 | `check_structure` 문서(status, checks, diagnostics code/position, blocks 필드(effective c/h/w/stride/params), edges) | `schema`/`observation.environment`의 모듈 경로 문자열·parser_source_sha256·catalog.source_path |
| T2-2 Random gen0 | nc=1, imgsz=64, families 기본, seeds {101,202,303}, population 4 | `random.propose_generation` 결과 genotype yaml·architecture_hash·lineage(operation, prior_draw) | lineage.search_space.{space_hash,prior_hash}(기록만), origin kind 문자열 |
| T2-3 GA gen0/gen1 | 위 + 합성 parent pool(가짜 L0 fitness/latency 8행, 고정) crossover_rate 0.5 mutation_rate 0.15 | `ga.propose_generation`: child별 operation·parents·cut_index·mutation events·elite·genotype | 동일 |
| T2-4 repair seed 116 | `test_repair_evidence_seed` 입력 | repair events(row 9 C3TR→SPPF 등) | 없음 |
| T2-5 fitness/gate | 고정 평가 24행(유효/무효/경계값) × rung cfg 3종 × hard constraints | `fitness()`·`evaluate_gate()` 결과 전부 | 없음 |
| T2-6 admission·종료 | 합성 DB(sqlite tmp): cap/billed/reserved 조합 12 | `admission.admit` 결정·remaining; `settle_run` 전이(active/open) | 없음 |
| T2-7 engine 흐름 | InlineWorker 하네스로 PILOT 유사 합성 protocol(2 rung, 2 seeds, population 2, stub 핸들러 결정적 metric) | 전체 evaluations 행(fitness, gate, candidate_status 순서), generations 카운트, cost_ledger | job_id/시각/worker_id/경로(정규화 후 비교) |
| T2-8 final 선택 | T2-7 결과 + final-retrain 요청 | finalists(rule, source), child run plan | 동일 |
| T2-9 streams | search_rng/training_seed 고정 입력 100건 | 값 | 없음 |
| T2-10 generation-only | OFF protocol seeds 2, candidate_count 2, max_attempts 6 | journal 순서·후보 yaml·duplicate 판정·generation_results | worker_id |

## 3. 산출
`docs/internal/evidence/T2/{scenario}.{ref,new}.json` + `T2_REPORT.md`(일치/허용 차이/불일치 표). 불일치는 결함으로 REVIEW_LOG에 기록하고 WP에 배정.

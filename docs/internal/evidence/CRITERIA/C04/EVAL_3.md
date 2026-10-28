# C04 분산 상태 — 독립 재평가 EVAL_3

- set: **C04** (`docs/internal/criteria/C04.json` v1.0). anchor는 EVAL_1·EVAL_2와 같다(25/20/15/0).
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자 — 채점만 하며 결정은 설계자
- time: 2026-10-28T10:25+09:00 (2026-10-28T01:25Z)
- tree hash: `sha256:f00987be7994203fc16f52140bd98b3f89e1b548b6a346255066727a36dcc40c` (mbnas-source-tree/2, 221 files).
  `runtime/venv/bin/python -B scripts/dev/source_tree_hash.py` exit 0. 01:16:22Z(평가 시작)와 01:24:11Z(`--digest-only`, 모든 재실행 후)의 값이 같고, 그사이 tests/·소스 아래에서 바뀐 `.py` 파일도 0개다.
  이전 트리: EVAL_1 `634a083b…`, EVAL_2 `5a7bc592…`.
- dist/ 상태: 평가 시점에 `docs/internal/evidence/WP7/PFINAL2/SUMMARY.md`가 없었다. dist/의 ZIP은 `5a7bc592…` 기준이라 오래된 것이다. C04의 네 기준은 배포 ZIP에 의존하지 않으므로 소스 트리 기준으로 채점했고, ZIP으로 확인한 것은 없다.
- 환경: CPU 전용. `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`. `--basetemp`, `MBNAS_TEST_ROOT`, probe 소스와 로그는 모두 세션 scratchpad에 두었다. GPU, /data, etri_nas, 원본 snapshot은 건드리지 않았다. 프로젝트에 쓴 파일은 이 파일 하나다.

## 결론

총점은 **100/100**(25+25+25+25)이고 Blocker 0, Critical 0, Major 0이므로 **PASS**다(기준: 90 초과, Blocker/Critical 0). 결함은 Minor 3건만 남았다.

- EVAL_2 결함은 모두 해결됐다.
  - **N1(Major)**: DD-A64. claim이 lease 직전에 같은 `now`로 reclaim한다. 프로젝트 테스트와 평가자 probe 재실행으로 확인했다.
  - N2(Minor), N3(Minor): 문서에 반영됐다. 다만 수치 하나가 틀렸고, 이는 M3로 남겼다.
- fix 5의 prune 왕복 분산 상태(DD-A66/A68)를 네 기준으로 평가했다.
  - 프로젝트 테스트(T3-34 6건, 공개 runner 단위 테스트)가 통과했다.
  - 프로젝트 테스트가 다루지 않는 실패·취소 경로 6가지는 평가자 probe P5–P10으로 실행 확인했다.
  - 모두 기준을 충족한다. 남은 Minor 1건(M1)은 그 경로들의 회귀 테스트 부재다.

## 재실행 요약 (cwd=TARGET_ROOT, `runtime/venv/bin/python -B -m pytest … -p no:cacheprovider`)

| # | 대상 | exit | 결과 | 시간 |
|---|---|---|---|---|
| R1 | `tests/owner/runner_protocol` 전체. 마커 필터는 걸지 않았다(gpu/live 마커 없음을 확인함). `test_t3_34_prune_roundtrip.py`와 `test_dd_a64_claim_reclaims_first.py` 포함 | 0 | **82 passed**, 0 failed/skipped | 161 s (01:16:38Z–01:19:19Z) |
| R2 | `tests/owner/orchestration` 전체 | 0 | **48 passed** | 61 s |
| R3 | `tests/public/runner/test_prune_control.py`, `test_generation_recovery_independent.py`, `test_generation_recovery_independent_journal.py`, `test_generation_only.py`, `worker/test_lease.py`, `qa/test_immutable.py`, `api/test_state_machine.py`, `tests/public/contracts/test_runner_protocol.py`, `tests/public/runner/test_runner_daemon_unit.py` | 0 | **206 passed** | 73 s |
| R4 | `tests/owner/unit/test_db.py`, `test_db_migration.py`(schema v6), `tests/owner/orchestration/test_retention_remote_policy.py` | 0 | 27 passed | 16 s |
| Q1 | EVAL_2 probe 재실행(`c04e2_probe/`): N1 경쟁(GPU·CPU 경로), 늦은 결과, CPU claim 동시성, NOT_EVALUATED 재생·충돌·롤백 | 0 | 6 passed. N1이 해결됐음을 관측(부록 C) | 11 s |
| Q2 | 새 probe `c04e3_probe/test_probe_prune_state.py` P5–P10 | 0 | 6 passed(부록 C) | 9 s |

CPU 합계는 약 340 s다.

- 로그(프로젝트 밖): `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c04e3_{rp,orch,named,db,oldprobes,prune_probes}.txt`(+ `.meta`).
- 고정 사본 대비 diff: `c04e3_tree_diff.json`.

열람한 트리 내 증거:

- `docs/internal/evidence/WP7/FIX5_T3_34/COMMANDS.txt`(F1–F6, 새 테스트 33 passed, 반복 ×3, D1 음성 대조).
- `FIX5_T3_34/apply/prune_roundtrip_smoke_after_apply.txt`: 실프로세스 core+owner+runner+gateway, `result=PASS`, STEP PASS 15 / FAIL 0, 39.2 s, 00:42Z.
- `docs/internal/VALIDATION_REPORT.md:14`(T3 행), `:21`(T7 행).
- `docs/internal/DECISIONS.md` DD-A64(:80–81), DD-A66(:83), DD-A68(:85).

## EVAL_2 결함 추적

| ID | EVAL_2 내용 | 상태 | 근거 |
|---|---|---|---|
| N1 (Major) | claim이 만료 lease를 sweeper보다 먼저 잡으면 lost 행 0개 | **해결** | 아래 참조 |
| N2 (Minor) | VALIDATION_REPORT T3/T7 행이 "확인 못 함" | **해결** | 아래 참조 |
| N3 (Minor) | DD-A56(c)의 이후 변경 목록이 불완전 | **해결** | 아래 참조 |

N1 근거:

- 코드: `mbnas_core/src/mbnas_core/runner_api/routes.py:345`. claim의 매 반복이 `sweeper.reclaim(conn, settings, now)`를 lease와 **같은 `now`**로 먼저 호출한다. GPU 경로(`repo.acquire_lease`)와 DD-A63 CPU 경로에 똑같이 적용된다.
- 프로젝트 테스트(R1, 모두 PASSED), 파일은 `tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py`:
  - `:85` claim/sweeper 순서 ×2: 같은 회계.
  - `:111` 혼합/CPU 전용 fleet ×2.
  - `:156` 늦은 결과가 1행을 연장.
  - `:185` 시도 소진 시 FAILED.
  - `:211` 자기 만료 lease를 재전달하지 않음.
  - `:232` 정상 경로 불변.
  - `:257` claim 4개 + sweeper 동시 → 1회만 과금.
- 평가자 probe 재실행(Q1):
  - claim이 sweep보다 먼저 와도 ledger가 `[(1, failed, 3.0, LEASE_EXPIRED)]`이고 job.error.reason_code는 LEASE_EXPIRED다.
  - 이후 sweep은 `reclaimed []`, attempt 2는 `retry`로 과금된다(`[(1,failed,3.0),(2,retry,0.171)]`).
  - CPU 경로도 같다(`[(1, failed, 3.0, LEASE_EXPIRED)]`).
  - 늦은 결과는 같은 1행을 20.05 s로 연장한다(LEASE_LOST).

N2 근거:

- `VALIDATION_REPORT.md:14`의 T3 행: PASS. 근거 경로와 T3-34/35 위치가 적혀 있다.
- `:21`의 T7 행: `9dafe4d0…`에서 PASS, 이후 변경은 CPU로만 검증했다고 명시한다.
- 잔여는 M3다. "1 removed"는 프로젝트 해시 규칙으로 0이 맞다.

N3 근거:

- DD-A64 본문에 "DD-A56(c) 보충"이 있다(`DECISIONS.md:80`).
- VALIDATION_REPORT T7 행에 변경 경로 요약이 있다.

GPU 이월 재확인(DD-A56 c): `c04e3_tree_diff.json`(고정 사본 `9dafe4d0…` 대 현재) 기준으로 변경 38개, 추가 1개(`mbnas_core/owner_worker/paths.py`), 삭제 0개다.

- C04 경로 중 이번에 새로 바뀐 것:
  - `runner_api/sweeper.py`: docstring만.
  - `mbnas_runner/daemon.py`: 추가만 있다. prune 함수, `_last_beat`(+15), `apply_control`(+2).
  - `journal.py`: `PruneJournal` 추가.
  - `routes.py`: claim reclaim과 E4·E11 추가.
- `_last_beat`(`daemon.py:673-688`)는 결과 commit 직전 heartbeat 1회로 prune 지시만 수집한다. action 플래그는 적용하지 않고, lease 상실이나 core 부재 시 commit이 결정한다.
- 따라서 lease, commit, 취소 판정은 바뀌지 않았다. GPU d/e 이월 결론은 유지되지만, 현재 트리에서 GPU로 재실행한 것은 아니다(확인 못 함 2).

## fix 5: prune 왕복 분산 상태 평가 (조정자 요청)

### 상태 모델

- 테이블: `mbnas_core/src/mbnas_core/db/schema.sql:311-341`.
  - `prune_requests(id PK, checkpoint_id UNIQUE, state CHECK IN ('PENDING','DELIVERED','DONE','CANCELLED'), deliveries, delivered_job_id, delivered_attempt, cancel_reason, report_json, …)`.
  - 상태 이름은 PENDING/DELIVERED/DONE/CANCELLED이다. checkpoint 행의 PRUNED는 `checkpoints.state`에 기록된다.
- id: `prq-` + sha256("prune\0" + checkpoint id)[:24] (`retention.py:214-216`).
- 이 결정적 id와 `checkpoint_id UNIQUE` 때문에 재계획해도 둘째 요청은 생기지 않는다. `_open_request`(`:279-300`)는 열린 요청이나 DONE 요청이 있으면 None을 돌려주고, CANCELLED 요청만 다시 무장한다.

### 기준 1 idempotent_atomic 관점

**E11 멱등성** — `routes.py:664-683`, `retention.py:395-447`.
- 한 tx 안에서 처리한다.
- DONE 요청의 중복 보고에는 변경 없이 `replayed: true`로 답한다(`:415-416`).
- 거부 코드:
  - 404 PRUNE_REQUEST_NOT_FOUND(`:406`, `:419`).
  - 403 RUNNER_MISMATCH(`:409`. 토큰↔body는 `routes.py:668`의 `check_runner`).
  - 409 PRUNE_REPORT_CONFLICT(`:412`).
  - 422(pydantic).
- 실행 근거: `test_t3_34_prune_roundtrip.py:75`(재전송 `replayed is True`), `:162`(거부 6건 + 422, 행 불변, 정직한 보고는 이후 성공). R1 PASSED.

**E11 원자성** — 평가자 probe **P8**(실행).
- `record_prune_report`가 checkpoint PRUNED, location PRUNED, 요청 DONE을 쓴 **뒤** 예외를 내게 했다.
- checkpoint PRESENT, location PRESENT, 요청 DELIVERED, report_json NULL로 전부 롤백됐다.
- 재시도는 200, `replayed: false`, 1회 적용이다.

**계획(planning) 격리**
- 코드: `engine.request_prunes`(`engine.py:500-516`)가 E6(`checkpoint_registered`, `:519`)와 E8(`job_finished`, `:476`, `:497`) 호출자의 tx 안 SAVEPOINT에서 실행된다. 실패는 `PRUNE_PLANNING_FAILED`로 기록만 한다.
- 평가자 probe **P5**(실행): 부분 쓰기 뒤 예외를 내게 했다.
  - E6 3회 모두 2xx이고, checkpoint는 모두 PRESENT로 부분 쓰기가 SAVEPOINT로 롤백됐다. 요청 0건, 실패 로그 3건.
  - E8 commit은 정상이다(run COMPLETED, 실패 로그 4번째).
  - 따라서 result commit 단일 tx가 prune 계획 실패에 오염되지 않는다.

**전달 격리**
- 코드: `_prune_orders`(`routes.py:405-414`)가 heartbeat tx 안 SAVEPOINT에서 실행된다. 실패 시 지시 없이 응답하고 lease 갱신은 유지된다.
- 평가자 probe **P6**(실행): DELIVERED로 갱신한 뒤 예외를 내게 했다.
  - heartbeat 200, `control.prune` null, `lease_expires_at` 전진.
  - 요청은 PENDING·deliveries 0 그대로였다(롤백).
  - 복구 후 2건이 전달됐다.

**runner 쪽** — `daemon.py:739-783`.
- outbox 규칙: 보고를 journal에 먼저 기록하고 보낸다(`:766`, `PruneJournal.record_report`는 `journal.py:300`이며 첫 기록이 이긴다).
- 재전달된 지시는 같은 보고를 다시 보내고 다시 지우지 않는다.
- core 부재 시 `unreported` 상태로 두고 유휴 루프가 재전송한다(`journal.py:323`, `daemon.py:804-812`).
- 실행 근거: `tests/public/runner/test_prune_control.py:168`, `:184`, `:204`(R3 PASSED). fake core 단위 테스트라 통합 증거는 위 T3-34와 smoke다.

### 기준 3 lease_fencing 관점

**lease당 최대 1회 전달**
- 코드: `retention.deliver_prune_orders`(`:345-392`)의 SELECT 조건 `NOT (delivered_job_id IS ? AND delivered_attempt IS ?)`(`:356`).
- 실행 근거: T3-34 `:75`(같은 lease의 둘째 heartbeat에는 `[]`), `:223`(보고되지 않은 지시는 다음 lease에서 정확히 1회, deliveries 2).

**전달은 attempt fence 뒤에만**
- 코드: heartbeat가 `repo.renew_lease(..., attempt=body.attempt)`(`routes.py:422`) 실패 시 tx 이전에 409로 끝난다. 전달은 tx 안의 `:431`이다.
- 평가자 probe **P9**(실행): 회수 뒤 stale attempt 1의 heartbeat는 409 LEASE_LOST이고 요청은 PENDING·deliveries 0 그대로였다. 같은 runner의 attempt 2 heartbeat가 2건을 전달했다(`delivered_attempt` 2).

**전달 직전 재검사**
- 코드: `:362-385`. checkpoint 부재/PRUNED, 보유 runner 변경, 보호(LATEST_FULL_STATE·EVALUATION_REFERENCED), candidate의 keep-everything 상태, 취소된 run을 각각 검사해 CANCELLED + `cancel_reason`으로 바꾼다.
- 실행 근거: T3-34 `:223`(LATEST_FULL_STATE·ALREADY_PRUNED), `:304`(CANDIDATE_PAUSED → 같은 id로 재무장), 평가자 probe **P7**(RUN_CANCEL_REQUESTED, 아래).

**E11에 ticket이 필요 없는 이유와 대신 거는 조건**
- 보고가 lease 종료 뒤에 올 수 있으므로 ticket을 요구하지 않는다.
- 대신 runner 토큰과 body.runner_id가 일치해야 하고(`routes.py:668`), 요청의 runner_id와도 일치해야 한다(`retention.py:409`). 다른 runner는 403이다(T3-34 `:162`).

**관찰(결함 아님)** — 평가자 probe **P10**.
- 전달 뒤 runner가 이미 지운 요청이 나중에 CANCELLED(LATEST_FULL_STATE)가 된 경우, E11은 200 PRUNED로 기록하고 로그에 `after_cancel`을 남긴다(`retention.py:443-446`).
- 이는 runner 디스크의 사실을 기록하는 것이다.
- 보호 규칙은 계획 시점과 전달 시점에 검사되고, 실행 시점에는 검사되지 않는다. 그 사이 창은 정상 흐름(policy가 최신·rung-end를 보존)에서는 열리지 않는 것으로 보이지만 측정하지는 않았다(확인 못 함 4).

### 기준 4 budget_cancel 관점

- prune은 과금 행을 만들지 않는다(probe 전 구간에서 ledger 변화 없음).
- 취소된 run은 계획하지 않는다(`NO_PRUNE_RUN_STATUSES`, `retention.py:65`, `:315-317`). 이미 열린 요청은 전달 시점에 취소된다.
- 평가자 probe **P7**(실행):
  - run CANCEL_REQUESTED에서 heartbeat는 `control.action == "cancel"`, `prune` null이다.
  - 두 요청 모두 CANCELLED(`RUN_CANCEL_REQUESTED`), 파일은 보존된다.
  - runner의 결과 commit 뒤 run은 CANCELLED이고 요청은 재무장되지 않는다.
- DD-A64로 claim과 sweeper 중 누가 먼저든 회계가 같아졌다. 늦은 결과는 1행 연장이다(Q1, `test_dd_a64…:156`).

### 기준 2 revision_rng 관점

- prune 대상은 `object_kind='checkpoint' AND store='runner_local'`인 학습 full state뿐이다(`retention.py:225-231`). 생성 journal, RNG 상태, architecture YAML은 대상이 아니다.
- T3-37과 journal 테스트는 R1·R3에서 PASSED다.

## 기준별 채점

### 1. idempotent_atomic — 25/25 (anchor 25)

- 필수 검사 T3-10/11/12/22(`test_t3_commit.py:34`, `:50`, `:70`, `:254`)는 R1에서 PASSED다. 테스트 파일은 EVAL_1/2와 같은 sha다.
- E8 단일 tx와 digest 재계산은 바뀌지 않았다(`routes.py` docstring `:11-17`, `runner_protocol.py` digest 검증).
- prune 확장(E11 멱등·원자, 계획·전달 SAVEPOINT 격리)은 위와 같이 실행으로 확인했다(T3-34 + P5/P6/P8).
- DD-A63(a) NOT_EVALUATED 재생·충돌·롤백도 회귀 없이 통과했다(Q1).
- 잔여(감점 없음): `_Retry` 동시 최초 commit 분기는 여전히 테스트가 없다(확인 못 함 3).

### 2. revision_rng — 25/25 (anchor 25)

- T3-37 `test_t3_owner_generation.py:42`, `test_generation_recovery_independent.py` 7/7, `test_generation_recovery_independent_journal.py` 4/4(D1)이 R1·R3에서 PASSED다.
- `qa/test_immutable.py:72`(commit_evaluation 재생 보호)도 PASSED다.
- prune과는 상호작용이 없다.

### 3. lease_fencing — 25/25 (anchor 25; EVAL_2 20 → 25)

- 필수 검사 T3-13~18(`test_t3_commit.py:94`, `:115`, `:134`, `:173`, `:191`, `test_t3_control.py:148`)은 R1에서 PASSED다.
- EVAL_2 감점 사유 N1은 해결됐다. 코드는 `routes.py:345`, 테스트는 `test_dd_a64…` 9건, 평가자 Q1이 근거다.
- prune 전달은 attempt fence 뒤에 lease당 1회만 일어나고, 전달 직전 재검사와 E11 runner 바인딩이 있다(T3-34 + P9).
- 잔여 M2(Minor, 문서화됨): 자기 만료 RUNNING lease에 대해 claim이 429를 주는 문제다. 아래 결함 표 참조.

### 4. budget_cancel — 25/25 (anchor 25)

- 필수 검사 T3-08 `test_t3_control.py:100`, T3-09 `:129`, T3-14, T3-16과 `record_lost_attempt` 의미 테스트 `worker/test_lease.py:161`, `:205`가 R1·R3에서 PASSED다.
- `api/test_state_machine.py:77`도 PASSED다.
- prune과 취소의 상호작용(P7), 과금 없음, claim·sweeper 순서와 무관한 회계(DD-A64)를 위와 같이 확인했다.

## 결함 (EVAL_3 기준)

| ID | 심각도 | 소유 패키지/파일 | 내용 | 구체적 수정 |
|---|---|---|---|---|
| M1 | Minor | `tests/owner/runner_protocol/`(fix 5 소유: `test_t3_34_prune_roundtrip.py` 옆) | 계획 실패 격리(`PRUNE_PLANNING_FAILED`, `engine.py:500-516`), 전달 실패 격리(`routes.py:405-414`), E11 예외 롤백, 취소된 run의 열린 요청 취소(`RUN_CANCEL_REQUESTED`)를 실행하는 프로젝트 테스트가 0건이다(grep). 동작은 평가자 probe P5/P6/P7/P8로 확인했지만 회귀 보호가 없다. | `c04e3_probe/test_probe_prune_state.py`의 P5–P8을 그대로 옮긴다. N1 때 부록 C를 옮긴 것과 같은 방식이고, 프로젝트 fixture만 쓴다. |
| M2 | Minor (DD-A64 기록의 1.5.1 잔여) | `mbnas_core/src/mbnas_core/runner_api/routes.py:337-338` 대 `:345` | RUNNER_BUSY 검사(자기 RUNNING job 존재 → 429)가 reclaim보다 먼저 실행된다. 그래서 자기 RUNNING lease가 만료된 채 재시작한 runner는 다음 sweep(≤ heartbeat_s 10 s)까지 claim하지 못한다. 과금은 정확하고 가용성만 지연된다. | RUNNER_BUSY 검사를 반복 안 reclaim 뒤로 옮기거나, 검사에 `lease_expires_at >= now` 조건을 추가한다. |
| M3 | Minor | `docs/internal/VALIDATION_REPORT.md:21`(T7 행) | "38 files modified, 1 added, 1 removed"라고 적었지만 프로젝트 해시 규칙(`_entries`)으로 고정 사본 `9dafe4d0…`과 현재 `f00987be…`를 비교하면 변경 38, 추가 1, **삭제 0**이다(220→221 files, `c04e3_tree_diff.json`). | 숫자를 "0 removed"로 고치거나 비교 기준을 명시한다. |

Blocker 0, Critical 0, Major 0, Minor 3.

## 총점·판정

25 + 25 + 25 + 25 = **100/100**으로 90을 넘고 Blocker/Critical이 0이므로 **PASS**다.
필수 시험(T3-08~18, T3-22, T3-37, `test_generation_recovery_independent`, `record_lost_attempt` 의미 테스트)과 조정자가 지정한 시험(`tests/owner/orchestration`, `tests/owner/runner_protocol`(T3-34 포함), `tests/public/runner/test_prune_control.py`)은 현재 트리 `f00987be…`에서 다시 실행해 전부 통과했다.

## 확인 못 함 (unverified — failed 아님)

1. 새 배포 ZIP. PFINAL2/SUMMARY.md가 아직 없고 dist/는 `5a7bc592…` 기준이다. C04는 소스 트리로 채점했다.
2. 현재 트리의 GPU 동작. GPU 증거는 `9dafe4d0…` 기준 이월(DD-A56 c)이다. GPU 사용 금지로 재실행하지 않았다.
3. `_Retry` 동시 최초 result commit 분기(`routes.py`의 `raise _Retry` / `except _Retry`). 테스트가 없다.
4. P10 관찰(전달 뒤 보호 상태로 바뀐 요청의 늦은 보고)의 실제 발생 가능성. 정상 흐름에서는 창이 열리지 않는 것으로 판단했지만 측정하지 않았다.
5. 실제 다중 프로세스 HTTP 동시성. 동시성 테스트와 probe는 TestClient 스레드 기반이다. 실프로세스 증거는 FIX5 smoke(직렬 흐름)뿐이다.
6. FIX5 smoke(00:42Z)가 돈 트리 해시. smoke 파일에 소스 트리 해시가 기록되어 있지 않다.

## 부록 A — T3 id ↔ 테스트 (C04 범위 + T3-34, R1, 현재 트리)

| T3 | C04 기준 | 테스트 | 결과 |
|---|---|---|---|
| T3-08 | budget_cancel | test_t3_control.py:100 | PASSED |
| T3-09 | budget_cancel | test_t3_control.py:129 | PASSED |
| T3-10 | idempotent_atomic | test_t3_commit.py:34 | PASSED |
| T3-11 | idempotent_atomic | test_t3_commit.py:50 | PASSED |
| T3-12 | idempotent_atomic | test_t3_commit.py:70 | PASSED |
| T3-13 | lease_fencing | test_t3_commit.py:94 | PASSED |
| T3-14 | lease_fencing, budget_cancel | test_t3_commit.py:115 | PASSED |
| T3-15 | lease_fencing | test_t3_control.py:148 | PASSED |
| T3-16 | lease_fencing, budget_cancel | test_t3_commit.py:134 | PASSED |
| T3-17 | lease_fencing | test_t3_commit.py:173 | PASSED |
| T3-18 | lease_fencing | test_t3_commit.py:191 | PASSED |
| T3-22 | idempotent_atomic | test_t3_commit.py:254 | PASSED |
| T3-37 | revision_rng | test_t3_owner_generation.py:42 | PASSED |
| T3-34 (C08 필수, C04 분산 상태 관점) | idempotent_atomic, lease_fencing | test_t3_34_prune_roundtrip.py:75, :162, :198, :223, :268, :304 | PASSED ×6 |

누락 0.

## 부록 C — 평가자 probe 관측 출력 (기계적으로 복사)

```
--- Q1 (EVAL_2 probes re-run on f00987be)
CLAIM_BEFORE_SWEEP ('job-82169248da0d4d16', 2)
JOB_AFTER_CLAIM {'status': 'LEASED', 'attempt': 2, 'lease_owner': 'R2', 'error': {'attempt': 1, 'lease_expires_at': '2026-10-28T01:21:57.489040Z', 'lease_owner': 'R1', 'reason_code': 'LEASE_EXPIRED', 'reclaimed_at': '2026-10-28T01:21:58.519559Z'}}
LEDGER_AFTER_CLAIM [(1, 'failed', 3.0, 'LEASE_EXPIRED')]
SWEEP {'reclaimed': [], 'workers_pruned': 0, 'runners_lost': 0, 'processing_cleared': 0}
LEDGER_AFTER_SWEEP [(1, 'failed', 3.0, 'LEASE_EXPIRED')]
ATTEMPT2 200 {'outcome': 'SUCCEEDED', 'job_status': 'SUCCEEDED', 'run_status': 'COMPLETED', 'response_digest': 'sha256:813b0be714f585d412043bc6a70a49a1c1be2c71ebbf4d484628edb916307f3c'}
LEDGER_FINAL [(1, 'failed', 3.0, 'LEASE_EXPIRED'), (2, 'retry', 0.171, None)]
LATE_RESULT 409 LEASE_LOST LEDGER [(1, 'failed', 20.05, 'LEASE_LOST')]
CPU_PATH_CALLS 2 SECOND 2
SWEEP {'reclaimed': [], 'workers_pruned': 0, 'runners_lost': 0, 'processing_cleared': 0} LEDGER [(1, 'failed', 3.0, 'LEASE_EXPIRED')]
TOTAL 8 LEASED 8 DISTINCT 8 CPU_PATH_CALLS 12
ATTEMPTS [1] STATUSES ['LEASED']
CONFLICT 409 RESULT_CONFLICT SEQ_DELTA 1
BROKEN 500 COMMIT_FAILED
6 passed, 2 warnings in 10.55s
--- Q2 (prune probes P5-P10)
P5_E6 states {1: 'PRESENT', 2: 'PRESENT', 3: 'PRESENT'} requests {} failed_logs 3
P5_E8 run COMPLETED job SUCCEEDED failed_logs 4
P6 hb 200 prune None renewed True requests [('PENDING', 0, None), ('PENDING', 0, None)]
P7 control cancel None requests [('CANCELLED', 0, 'RUN_CANCEL_REQUESTED'), ('CANCELLED', 0, 'RUN_CANCEL_REQUESTED')] files_kept [True, True]
P7 after commit run CANCELLED requests [('CANCELLED', 0, 'RUN_CANCEL_REQUESTED'), ('CANCELLED', 0, 'RUN_CANCEL_REQUESTED')]
P8 outcome raised synthetic E11 failure after the writes checkpoint PRESENT location ['PRESENT'] request DELIVERED None
P8 retry 200 {'checkpoint_id': 'ck-sim-65044b3a-1-0', 'request_id': 'prq-30db919f05c5fe676f8176c0', 'state': 'PRUNED', 'replayed': False}
P9 stale hb 409 LEASE_LOST requests [('PENDING', 0, None), ('PENDING', 0, None)]
P9 attempt2 orders 2 [(True, 2), (True, 2)]
P10 200 {'checkpoint_id': 'ck-sim-ad5540c5-1-0', 'request_id': 'prq-3ed1dc96af5fb4e19366e0e5', 'state': 'PRUNED', 'replayed': False} checkpoint PRUNED after_cancel ['LATEST_FULL_STATE']
6 passed, 2 warnings in 8.51s
```

## 부록 B — 재실행 출력 발췌 (scratchpad 로그에서 기계적으로 복사)

### c04e3_rp

```
start 2026-10-28T01:16:38Z cwd=/home/mgjeong/Desktop/etri_hd/mbnas_application
exit=0 dur=161s end=2026-10-28T01:19:19Z
tests/owner/runner_protocol/test_artifact_transfer.py::test_local_transfer_stores_every_registered_file_with_meta PASSED
tests/owner/runner_protocol/test_artifact_transfer.py::test_transfer_failure_never_fails_the_job_and_a_later_retry_completes PASSED
tests/owner/runner_protocol/test_dd_a60_cpu_fleet.py::test_cpu_runners_form_one_cpu_device_entry_instead_of_being_left_out PASSED
tests/owner/runner_protocol/test_dd_a60_cpu_fleet.py::test_cpu_only_fleet_refuses_evaluation_on_and_validates_training_and_generation PASSED
tests/owner/runner_protocol/test_dd_a60_cpu_fleet.py::test_mixed_fleet_keeps_the_gpu_and_refuses_only_the_cpu_benchmark_device PASSED
tests/owner/runner_protocol/test_dd_a60_cpu_fleet.py::test_empty_inventory_is_not_a_cpu_fleet PASSED
tests/owner/runner_protocol/test_dd_a60_cpu_training_validation.py::test_cpu_training_with_validation_succeeds_not_evaluated_and_keeps_the_checkpoint PASSED
tests/owner/runner_protocol/test_dd_a62_cpu_claim_and_commit.py::test_explicit_training_with_validation_on_a_cpu_fleet_completes_not_evaluated PASSED
tests/owner/runner_protocol/test_dd_a62_cpu_claim_and_commit.py::test_requires_cuda_and_needs_cuda_rules PASSED
tests/owner/runner_protocol/test_dd_a62_cpu_claim_and_commit.py::test_cpu_runner_leaves_evaluation_on_jobs_to_the_gpu_runner_and_leases_what_it_can_run PASSED
tests/owner/runner_protocol/test_dd_a62_cpu_claim_and_commit.py::test_explicit_training_that_evaluates_goes_to_the_gpu_runner_while_one_is_online PASSED
tests/owner/runner_protocol/test_dd_a62_cpu_claim_and_commit.py::test_cpu_only_fleet_still_runs_explicit_training_with_validation PASSED
tests/owner/runner_protocol/test_dd_a62_cpu_claim_and_commit.py::test_gpu_runner_claims_are_unchanged PASSED
tests/owner/runner_protocol/test_dd_a62_cpu_claim_and_commit.py::test_cpu_only_fleet_cpu_runner_still_takes_an_evaluation_on_job PASSED
tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py::test_claim_before_or_after_the_sweep_bills_the_dead_attempt_the_same[claim] PASSED
tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py::test_claim_before_or_after_the_sweep_bills_the_dead_attempt_the_same[sweeper] PASSED
tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py::test_cpu_claim_path_reclaims_before_leasing[mixed] PASSED
tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py::test_cpu_claim_path_reclaims_before_leasing[cpu_only] PASSED
tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py::test_late_result_of_the_claim_reclaimed_attempt_extends_its_one_lost_row PASSED
tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py::test_claim_reclaim_fails_the_job_when_no_attempt_is_left PASSED
tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py::test_claim_does_not_redeliver_its_own_expired_lease PASSED
tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py::test_normal_claim_path_is_unchanged PASSED
tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py::test_concurrent_claims_and_sweeper_bill_the_dead_attempt_once PASSED
tests/owner/runner_protocol/test_device_used.py::test_search_run_records_raw_metrics_device_used_cpu PASSED
tests/owner/runner_protocol/test_device_used.py::test_explicit_training_run_records_raw_metrics_device_used_cpu PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_unit[as reported by the runner-verified] PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_unit[one hash changed-mismatch] PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_unit[one file missing-mismatch] PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_unit[extra file-mismatch] PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_unit[nothing reported-unknown] PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_is_unknown_when_the_core_cannot_hash_its_own_operators PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_register_labels_the_reported_operator_manifest PASSED
tests/owner/runner_protocol/test_t3_34_prune_roundtrip.py::test_prune_request_round_trip_marks_checkpoint_pruned_only_after_runner_report PASSED
tests/owner/runner_protocol/test_t3_34_prune_roundtrip.py::test_pruned_report_refusals_never_change_the_row PASSED
tests/owner/runner_protocol/test_t3_34_prune_roundtrip.py::test_already_absent_payload_is_reported_and_recorded_as_such PASSED
tests/owner/runner_protocol/test_t3_34_prune_roundtrip.py::test_orders_are_rechecked_at_delivery_and_redelivered_only_in_a_later_lease PASSED
tests/owner/runner_protocol/test_t3_34_prune_roundtrip.py::test_real_daemon_prunes_on_its_own_and_keeps_the_store_copies PASSED
tests/owner/runner_protocol/test_t3_34_prune_roundtrip.py::test_keep_everything_candidate_cancels_open_orders_and_replanning_rearms_them PASSED
tests/owner/runner_protocol/test_t3_auth.py::test_runner_token_bound_to_other_runner_id_is_403 PASSED
tests/owner/runner_protocol/test_t3_auth.py::test_expired_or_revoked_token_is_401 PASSED
tests/owner/runner_protocol/test_t3_auth.py::test_server_scope_token_cannot_call_runner_routes PASSED
tests/owner/runner_protocol/test_t3_auth.py::test_rotated_token_overlaps_for_grace_then_old_is_rejected PASSED
tests/owner/runner_protocol/test_t3_auth.py::test_runner_refuses_plain_http_to_non_loopback_core PASSED
tests/owner/runner_protocol/test_t3_auth.py::test_runner_routes_have_their_own_32mib_body_limit PASSED
tests/owner/runner_protocol/test_t3_body_limit.py::test_result_body_over_limit_is_413_and_runner_externalises_per_sample PASSED
tests/owner/runner_protocol/test_t3_claim.py::test_two_runners_never_claim_the_same_job PASSED
tests/owner/runner_protocol/test_t3_claim.py::test_claim_rejects_kind_outside_allowlist PASSED
tests/owner/runner_protocol/test_t3_claim.py::test_claim_retry_returns_same_leased_job_without_new_attempt PASSED
tests/owner/runner_protocol/test_t3_claim.py::test_envelope_native_params_equal_child_load_context PASSED
tests/owner/runner_protocol/test_t3_claim.py::test_lease_without_start_is_not_billed_after_sweep PASSED
tests/owner/runner_protocol/test_t3_claim.py::test_owner_worker_leases_propose_generation_and_runner_never_sees_it PASSED
tests/owner/runner_protocol/test_t3_claim.py::test_owner_mode_wait_gpu_clear_never_calls_nvml PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_result_replay_same_digest_returns_stored_response_without_side_effects PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_result_with_different_digest_is_409_conflict PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_result_processing_row_yields_202_with_retry_after_and_sweeper_clears_orphan PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_sweeper_reclaims_expired_lease_and_bills_lost_window PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_sweeper_fails_job_and_candidate_when_attempts_exhausted PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_stale_succeeded_result_is_discarded_and_lost_row_extended_once PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_lost_lease_without_reclaim_row_appends_one_lost_row PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_stale_attempt_of_same_runner_cannot_renew_touch_or_finish PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_core_restart_between_claim_and_result_preserves_lease_and_commit PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_commit_exception_rolls_back_and_leaves_no_idempotent_row PASSED
tests/owner/runner_protocol/test_t3_commit.py::test_runner_without_heartbeat_is_reported_lost PASSED
tests/owner/runner_protocol/test_t3_control.py::test_heartbeat_returns_pause_then_null_after_withdrawal PASSED
tests/owner/runner_protocol/test_t3_control.py::test_pause_at_epoch_boundary_then_resume_over_http PASSED
tests/owner/runner_protocol/test_t3_control.py::test_cancel_control_terminates_child_and_commits_cancelled PASSED
tests/owner/runner_protocol/test_t3_control.py::test_succeeded_result_after_cancel_request_is_recorded_cancelled PASSED
tests/owner/runner_protocol/test_t3_control.py::test_heartbeat_after_reclaim_is_409_lease_lost PASSED
tests/owner/runner_protocol/test_t3_control.py::test_runner_shutdown_requeues_without_consuming_retry PASSED
tests/owner/runner_protocol/test_t3_files.py::test_runner_recomputes_checkpoint_sha_and_rejects_tampered_file PASSED
tests/owner/runner_protocol/test_t3_files.py::test_core_rejects_checkpoint_without_digest_wrong_kind_or_stale_attempt PASSED
tests/owner/runner_protocol/test_t3_files.py::test_result_artifacts_resolve_to_preregistered_rows_without_hashing PASSED
tests/owner/runner_protocol/test_t3_files.py::test_execution_configuration_report_updates_candidate_without_core_file_access PASSED
tests/owner/runner_protocol/test_t3_files.py::test_progress_batch_touches_job_and_emits_one_candidate_updated_per_batch PASSED
tests/owner/runner_protocol/test_t3_owner_generation.py::test_sigkill_mid_generation_recovers_saved_candidates_once PASSED
tests/owner/runner_protocol/test_t3_restart.py::test_runner_restart_without_result_reports_child_exit_no_result_and_resumes PASSED
tests/owner/runner_protocol/test_t3_restart.py::test_runner_journal_replays_result_then_ack[after] PASSED
tests/owner/runner_protocol/test_t3_restart.py::test_runner_journal_replays_result_then_ack[before] PASSED
tests/owner/runner_protocol/test_t3_tickets.py::test_unit_authentic_ticket_verifies_only_for_its_own_lease_and_before_exp PASSED
tests/owner/runner_protocol/test_t3_tickets.py::test_unit_forged_tampered_and_garbage_tickets_never_decode PASSED
tests/owner/runner_protocol/test_t3_tickets.py::test_unit_issue_refuses_the_field_separator PASSED
tests/owner/runner_protocol/test_t3_tickets.py::test_bad_tickets_are_403_on_start_and_heartbeat_without_any_write PASSED
================== 82 passed, 1 warning in 158.87s (0:02:38) ===================
```

### c04e3_orch

```
start 2026-10-28T01:19:19Z cwd=/home/mgjeong/Desktop/etri_hd/mbnas_application
exit=0 dur=61s end=2026-10-28T01:20:20Z
tests/owner/orchestration/test_dd_a62_not_evaluated_search.py::test_ga_not_evaluated_candidates_record_the_reason_and_are_never_promoted PASSED
tests/owner/orchestration/test_dd_a62_not_evaluated_search.py::test_a_payload_without_metrics_that_does_not_say_not_evaluated_still_fails PASSED
tests/owner/orchestration/test_engine_flow.py::test_full_run_completes_with_rungs_generations_and_costs PASSED
tests/owner/orchestration/test_final_retrain_excludes_not_evaluated.py::test_not_evaluated_rows_are_never_finalists_but_measured_rows_are PASSED
tests/owner/orchestration/test_final_retrain_excludes_not_evaluated.py::test_only_not_evaluated_search_rows_is_no_finalist PASSED
tests/owner/orchestration/test_final_selection.py::test_rule_ties_exclusions_and_supersession PASSED
tests/owner/orchestration/test_final_selection.py::test_top_k_orders_ties_by_latency_then_candidate_id PASSED
tests/owner/orchestration/test_final_selection.py::test_no_finalist_and_recipe_errors PASSED
tests/owner/orchestration/test_final_selection.py::test_final_training_seed_follows_the_seed_policy PASSED
tests/owner/orchestration/test_open_generation_progress.py::test_open_generations_are_pushed_as_generation_updated PASSED
tests/owner/orchestration/test_retention.py::test_last_rung_end_only_keeps_the_last_rung_end_state PASSED
tests/owner/orchestration/test_retention.py::test_rejected_candidate_keeps_its_last_reached_rung_end PASSED
tests/owner/orchestration/test_retention.py::test_rung_end_only_and_keep_all PASSED
tests/owner/orchestration/test_retention.py::test_server_default_applies_when_the_protocol_has_no_retention PASSED
tests/owner/orchestration/test_retention.py::test_missing_vs_pruned_and_trainer_reconciliation PASSED
tests/owner/orchestration/test_retention.py::test_cancelled_candidates_keep_everything PASSED
tests/owner/orchestration/test_retention.py::test_cli_applies_a_policy_after_the_run PASSED
tests/owner/orchestration/test_retention.py::test_plan_deletes_rfdetr_native_companions_and_never_leaves_the_run_dir PASSED
tests/owner/orchestration/test_retention.py::test_final_complete_candidates_follow_the_policy PASSED
tests/owner/orchestration/test_retention.py::test_resume_check_refuses_a_pruned_full_state PASSED
tests/owner/orchestration/test_retention.py::test_last_rung_end_only_never_removes_the_resume_source_of_a_live_candidate PASSED
tests/owner/orchestration/test_retention_remote_policy.py::test_policies_apply_to_remote_runs_with_both_protections[last_rung_end_only-expected0] PASSED
tests/owner/orchestration/test_retention_remote_policy.py::test_policies_apply_to_remote_runs_with_both_protections[rung_end_only-expected1] PASSED
tests/owner/orchestration/test_retention_remote_policy.py::test_policies_apply_to_remote_runs_with_both_protections[keep_all-expected2] PASSED
tests/owner/orchestration/test_retention_remote_policy.py::test_without_the_evaluation_reference_the_policy_requests_epoch_one_too PASSED
tests/owner/orchestration/test_retention_remote_policy.py::test_keep_everything_candidates_and_cancelled_runs_get_no_requests[CANCELLED] PASSED
tests/owner/orchestration/test_retention_remote_policy.py::test_keep_everything_candidates_and_cancelled_runs_get_no_requests[ELIGIBLE_BUDGET_EXHAUSTED] PASSED
tests/owner/orchestration/test_retention_remote_policy.py::test_keep_everything_candidates_and_cancelled_runs_get_no_requests[PAUSED] PASSED
tests/owner/orchestration/test_retention_remote_policy.py::test_keep_everything_candidates_and_cancelled_runs_get_no_requests[RESUME_BLOCKED] PASSED
tests/owner/orchestration/test_retention_remote_policy.py::test_apply_for_run_on_a_remote_run_requests_and_deletes_nothing_itself PASSED
tests/owner/orchestration/test_retention_remote_policy.py::test_evaluation_reference_matching PASSED
tests/owner/orchestration/test_semantics.py::test_gate_rejection_stops_promotion_and_closes_next_rung PASSED
tests/owner/orchestration/test_semantics.py::test_budget_exhaustion_is_eligible_not_admitted PASSED
tests/owner/orchestration/test_semantics.py::test_budget_overshoot_is_recorded_in_costs_and_logged_once PASSED
tests/owner/orchestration/test_semantics.py::test_no_overshoot_key_is_positive_when_billed_stays_below_the_cap PASSED
tests/owner/orchestration/test_semantics.py::test_admission_refuses_work_whose_cap_is_missing[GA-search_per_arm_seed_s] PASSED
tests/owner/orchestration/test_semantics.py::test_admission_refuses_work_whose_cap_is_missing[IndependentRandom-search_per_arm_seed_s] PASSED
tests/owner/orchestration/test_semantics.py::test_admission_refuses_work_whose_cap_is_missing[Baseline-baseline_s] PASSED
tests/owner/orchestration/test_semantics.py::test_admission_refuses_work_whose_cap_is_missing[Final-final_s] PASSED
tests/owner/orchestration/test_semantics.py::test_missing_search_cap_makes_candidates_eligible_budget_exhausted PASSED
tests/owner/orchestration/test_semantics.py::test_random_proposals_never_receive_fitness_and_ga_uses_l0_pool PASSED
tests/owner/orchestration/test_semantics.py::test_failed_training_counts_as_failed_and_bills_arm PASSED
tests/owner/orchestration/test_semantics.py::test_evaluated_rows_are_immutable_and_replay_is_idempotent PASSED
tests/owner/orchestration/test_semantics.py::test_progress_and_failures_never_touch_committed_rows PASSED
tests/owner/orchestration/test_semantics.py::test_representative_rule PASSED
tests/owner/orchestration/test_semantics.py::test_runs_are_deterministic_in_structure PASSED
tests/owner/orchestration/test_semantics.py::test_per_kind_checkpoint_hash_is_normalised PASSED
tests/owner/orchestration/test_semantics_baseline_ungated.py::test_baseline_rows_carry_no_gate_and_advance_through_every_stage_under_a_rejecting_gate PASSED
======================== 48 passed, 1 warning in 59.68s ========================
```

### c04e3_named

```
start 2026-10-28T01:20:20Z cwd=/home/mgjeong/Desktop/etri_hd/mbnas_application
exit=0 dur=73s end=2026-10-28T01:21:33Z
tests/public/runner/test_prune_control.py::test_deletes_only_the_named_payload_and_its_native_companion PASSED
tests/public/runner/test_prune_control.py::test_already_absent_and_other_bytes_are_reported_and_nothing_is_deleted PASSED
tests/public/runner/test_prune_control.py::test_invalid_orders_are_refused_before_any_file_access[over0-PRUNE_ORDER_INVALID] PASSED
tests/public/runner/test_prune_control.py::test_invalid_orders_are_refused_before_any_file_access[over1-PRUNE_ORDER_INVALID] PASSED
tests/public/runner/test_prune_control.py::test_invalid_orders_are_refused_before_any_file_access[over2-PRUNE_ORDER_INVALID] PASSED
tests/public/runner/test_prune_control.py::test_invalid_orders_are_refused_before_any_file_access[over3-PRUNE_ORDER_INVALID] PASSED
tests/public/runner/test_prune_control.py::test_invalid_orders_are_refused_before_any_file_access[over4-PRUNE_ORDER_INVALID] PASSED
tests/public/runner/test_prune_control.py::test_invalid_orders_are_refused_before_any_file_access[over5-PRUNE_ORDER_INVALID] PASSED
tests/public/runner/test_prune_control.py::test_invalid_orders_are_refused_before_any_file_access[over6-PRUNE_ORDER_INVALID] PASSED
tests/public/runner/test_prune_control.py::test_symlinks_and_locations_outside_the_work_root_are_refused PASSED
tests/public/runner/test_prune_control.py::test_daemon_prune_is_idempotent_by_request_id PASSED
tests/public/runner/test_prune_control.py::test_unanswered_reports_are_resent_unchanged_and_refusals_never_reported PASSED
tests/public/runner/test_prune_control.py::test_submit_prunes_queues_each_request_once_and_the_worker_drains PASSED
tests/public/runner/test_prune_control.py::test_prune_is_deferred_while_the_store_transfer_of_the_file_is_owed PASSED
tests/owner/search/test_generation_recovery_independent.py::test_saved_candidate_survives_notification_crash_without_resampling PASSED
tests/owner/search/test_generation_recovery_independent.py::test_completed_retry_replays_without_instantiating_runtime PASSED
tests/owner/search/test_generation_recovery_independent.py::test_duplicate_cap_returns_durable_partial_candidates PASSED
tests/owner/search/test_generation_recovery_independent.py::test_failed_or_unverified_checks_never_accept_candidate[FAIL] PASSED
tests/owner/search/test_generation_recovery_independent.py::test_failed_or_unverified_checks_never_accept_candidate[UNVERIFIED] PASSED
tests/owner/search/test_generation_recovery_independent.py::test_pause_before_next_draw_preserves_completed_candidate PASSED
tests/owner/search/test_generation_recovery_independent.py::test_unavailable_runtime_is_explicit_failure_with_no_passes PASSED
tests/owner/search/test_generation_recovery_independent_journal.py::test_journal_of_another_configuration_is_refused_without_side_effects[other_protocol_hash] PASSED
tests/owner/search/test_generation_recovery_independent_journal.py::test_journal_of_another_configuration_is_refused_without_side_effects[other_run_id] PASSED
tests/owner/search/test_generation_recovery_independent_journal.py::test_journal_of_another_configuration_is_refused_without_side_effects[corrupt_journal] PASSED
tests/owner/search/test_generation_recovery_independent_journal.py::test_concurrent_attempt_holding_the_journal_lock_is_busy_and_retryable PASSED
tests/owner/worker/test_lease.py::test_two_workers_never_lease_the_same_job PASSED
tests/owner/worker/test_lease.py::test_expired_lease_is_reclaimed_and_lost_work_billed PASSED
tests/owner/worker/test_lease.py::test_attempts_exhausted_fails_job_and_candidate PASSED
tests/owner/worker/test_lease.py::test_leased_but_never_started_attempt_is_not_billed PASSED
tests/owner/worker/test_lease.py::test_idle_worker_heartbeat_keeps_health_live PASSED
tests/owner/worker/test_lease.py::test_lost_attempt_billed_to_last_progress_before_first_heartbeat PASSED
tests/owner/worker/test_lease.py::test_killed_worker_is_reported_lost_not_busy PASSED
tests/owner/worker/test_lease.py::test_lost_lease_after_reclaim_by_other_worker_bills_the_attempt_once PASSED
tests/owner/worker/test_lease.py::test_lost_lease_without_reclaim_row_appends_one_lost_row PASSED
tests/owner/worker/test_lease.py::test_snapshot_lists_only_live_recently_lost_or_attached_workers PASSED
tests/owner/qa/test_immutable.py::test_candidate_updated_after_evaluated_never_alters_the_row PASSED
tests/owner/qa/test_immutable.py::test_altered_replay_of_a_result_does_not_change_the_committed_row PASSED
tests/owner/qa/test_immutable.py::test_sql_cannot_rewrite_delete_or_unfreeze_an_evaluated_row PASSED
tests/owner/qa/test_immutable.py::test_superseding_revision_keeps_the_old_row_everywhere PASSED
tests/owner/qa/test_immutable.py::test_a_superseding_revision_must_not_be_applied_twice_or_to_a_provisional_row PASSED
tests/owner/api/test_state_machine.py::test_pause_resume_without_active_jobs PASSED
tests/owner/api/test_state_machine.py::test_pause_waits_for_safe_boundary PASSED
tests/owner/api/test_state_machine.py::test_resume_withdraws_pending_pause PASSED
tests/owner/api/test_state_machine.py::test_cancel_tracks_active_jobs_until_worker_confirms PASSED
tests/owner/api/test_state_machine.py::test_cancel_without_active_jobs_is_immediate PASSED
tests/owner/api/test_state_machine.py::test_completed_run_rejects_transitions PASSED
tests/owner/api/test_state_machine.py::test_resume_blocked_when_dataset_changed PASSED
tests/owner/api/test_state_machine.py::test_resume_blocks_candidate_with_corrupt_full_state PASSED
tests/owner/api/test_state_machine.py::test_transition_unknown_run_is_404 PASSED
tests/public/runner/test_runner_daemon_unit.py::test_lease_lost_heartbeat_stops_child_and_discards_result PASSED
tests/public/runner/test_runner_daemon_unit.py::test_lease_expiry_watchdog_kills_child_without_renewal PASSED
================== 206 passed, 1 warning in 72.04s (0:01:12) ===================
```

### c04e3_db

```
27 passed, 1 warning in 16.02s
```

### tree hash

```
start 2026-10-28T01:16:22Z: "sha256":"sha256:f00987be7994203fc16f52140bd98b3f89e1b548b6a346255066727a36dcc40c"
end   2026-10-28T01:24:11Z (--digest-only): sha256:f00987be7994203fc16f52140bd98b3f89e1b548b6a346255066727a36dcc40c
frozen G2 copy (TREE 9dafe4d0) vs current, project _entries rule:
frozen sha256:9dafe4d021ab572058c306e596b686d3f19a1203cab7504c4c2662973fb670ea 220
current sha256:f00987be7994203fc16f52140bd98b3f89e1b548b6a346255066727a36dcc40c 221
added 1 (mbnas_core/owner_worker/paths.py), removed 0, changed 38
```

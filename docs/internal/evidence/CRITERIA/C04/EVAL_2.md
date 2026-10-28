# C04 분산 상태 — 독립 재평가 EVAL_2

- set: **C04** (`docs/internal/criteria/C04.json` v1.0). EVAL_1과 같은 anchor(25/20/15/0)를 그대로 쓴다.
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자 — 채점만 하며 결정은 설계자
- time: 2026-10-28T07:56+09:00 (2026-10-27T22:56Z)
- tree hash: `sha256:5a7bc5920ac3c1fe630a45c8ea0dd4661cb754661a90da5c09441fb7237c4223` (mbnas-source-tree/2, 221 files).
  `runtime/venv/bin/python -B scripts/dev/source_tree_hash.py` exit 0. 22:43:36Z(평가 시작)와 22:55:43Z(`--digest-only`, 모든 재실행 후)의 값이 같고, 그사이 tests/ 아래에서 바뀐 파일도 없다.
  EVAL_1 시점 트리는 `634a083b…`였다.
- 환경: CPU 전용. `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`. pytest `--basetemp`, `MBNAS_TEST_ROOT`, probe 파일은 모두 세션 scratchpad에 두었다. GPU, /data, etri_nas, snapshot은 건드리지 않았다. 프로젝트에 쓴 파일은 이 파일 하나다.

## 결론

총점은 **95/100**(idempotent_atomic 25, revision_rng 25, lease_fencing 20, budget_cancel 25)이고 Blocker 0, Critical 0이므로 **PASS**다(기준: 90 초과, Blocker/Critical 0).

EVAL_1 결함:

- D1(Minor)과 D3(Minor)은 고쳐졌다.
- D2(Minor)는 `9dafe4d0…` GPU 재실행과 DD-A56(c) 이월 수용으로 닫혔다.

새 결함 1건(**Major N1**):

- runner claim(E2)이 만료된 lease를 sweeper보다 먼저 다시 잡으면, 죽은 attempt의 작업 시간이 과금되지 않는다(lost 행 0개).
- GPU 경로와 DD-A63 CPU 경로 모두 실행으로 재현했다(부록 C).
- C04 lease_fencing의 "sweeper 회수·과금 1행"과 상충하므로 이 기준을 20점으로 내렸다.

## 재실행 요약 (cwd=TARGET_ROOT, `runtime/venv/bin/python -B -m pytest … -p no:cacheprovider`)

| # | 대상 | exit | 결과 | 시간 |
|---|---|---|---|---|
| E1 | `tests/owner/runner_protocol` 전체. 마커 필터는 걸지 않았다(gpu/live 마커 없음을 확인함) | 0 | **67 passed**, 0 failed/skipped | 145 s (22:43:56Z–22:46:21Z) |
| E2 | `test_generation_recovery_independent.py`, `test_generation_recovery_independent_journal.py`, `test_generation_only.py`, `worker/test_lease.py`, `qa/test_immutable.py`, `api/test_state_machine.py`, `orchestration/test_dd_a62_not_evaluated_search.py`, `test_semantics.py::test_evaluated_rows_are_immutable_and_replay_is_idempotent`, 공개 contract digest/fence 2건, 공개 runner daemon 단위 3건 | 0 | **62 passed** | 51 s |
| P1 | probe `test_probe_claim_race.py`(N1, GPU 경로) | 0 | 재현(부록 C) | 4 s |
| P2 | probe `test_probe_cpu_claim_race.py`: CPU runner 12대가 job 8개를 동시에 claim(DD-A63 d 경로) | 0 ×3 | 3/3 통과: 이중 lease 0, attempt 1 | 5–7 s ×3 |
| P3 | probe `test_probe_not_evaluated_idem.py`: DD-A63(a) NOT_EVALUATED commit의 재생·충돌·롤백 | 0 | 2 passed | 47 s |
| P4 | probe `test_probe_claim_race2.py`: N1 늦은 결과 경우 + CPU 경로 경우 | 0 | 2 passed(관측 출력은 부록 C) | 13 s |

이번 라운드 CPU 합계는 약 290 s다.

- 로그(프로젝트 밖): `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c04e2_rp.txt`, `c04e2_named.txt`, `c04e2_probe.txt`, `c04e2_probe2_{1,2,3}.txt`, `c04e2_probe3.txt`, `c04e2_probe4.txt`(각각 `.meta` 포함).
- probe 소스: 같은 폴더의 `c04e2_probe/`.
- 고정 사본 대비 트리 diff: `c04e2_tree_diff.py`, `c04e2_tree_diff.json`.

열람한 트리 내 증거:

- `docs/internal/evidence/WP7/7V_FINAL/gate_owner.{meta,txt}`: exit=0, 1569 passed / 0 failed, 21:48–22:03Z. 소스 트리 해시가 기록되어 있지 않다(확인 못 함 1).
- `docs/internal/evidence/T7/RERUN_README.md:37-56`(두 번째 표), `steps_rerun.jsonl`, `dbviews/rerun_full_{d,e}_*.json`.

## EVAL_1 결함 추적

| ID | EVAL_1 내용 | 상태 | 근거 |
|---|---|---|---|
| D1 (Minor) | journal identity 거부(GENERATION_STATE_INVALID)와 flock 경합(GENERATION_BUSY) 분기를 실행하는 테스트 0건 | **해결** | 아래 참조 |
| D2 (Minor) | GPU d/e 증거가 164292c6…에만 있음 | **해결(이월 수용)** | 아래 참조 |
| D3 (Minor) | 설계 §7.2의 PROCESSING 서술이 구현과 다름 | **해결** | 아래 참조 |

D1 근거:

- `tests/owner/search/test_generation_recovery_independent_journal.py:71`: other_protocol_hash / other_run_id / corrupt_journal 3건 모두 `GENERATION_STATE_INVALID`, `retryable False`, emit 0, journal 바이트 불변.
- 같은 파일 `:101`: lock을 쥔 동안 `GENERATION_BUSY`, `retryable True`, journal 미생성. lock을 풀면 같은 요청이 성공한다(대조군).
- E2에서 4건 모두 PASSED. 검증 대상 코드 `mbnas_core/src/mbnas_core/search/generation.py:104-125`는 `9dafe4d0…` 이후 바뀌지 않았다.
- DD-A56(a)가 "policy revision" 해석을 확정했다.

D2 근거:

- `T7/RERUN_README.md:37-56`의 두 번째 표: TREE_HASH `9dafe4d0…`에서 a/b/h/c/d/e/f가 모두 PASS다.
- 직접 열람한 내용:
  - `steps_rerun.jsonl`의 `e-sigkill-runner`: runner_exit -9, child_pids_alive_after [], tree_hash 9dafe4d0….
  - `e-lease-expired-requeued`: 58.14 s, LEASE_EXPIRED, runner LOST.
  - `e-reclaimed-attempt-plus-1`: commits a1=409 DONE, a2=200 DONE.
  - `e-replay-idempotent`: a1 → 409 LEASE_LOST, a2 → 저장 응답 200.
  - `dbviews/rerun_full_e_train-968136cffe10.json`: cost_ledger = failed(a1) 82.37 s **1행** + retry(a2) 57.53 s + profile(a2) 3.24 s. idem 409/200 DONE. job SUCCEEDED attempt 2.
  - `dbviews/rerun_full_d_train-e85df4da9852.json`: failed 1행, job CANCELLED/RUN_CANCELLED, run CANCELLED/CANCELLED_BY_USER.
- DD-A56(c)가 이월을 수용했다.
- 이월의 타당성은 직접 대조했다. 고정 사본 `runtime/test_root/g2_tree2/…/site-packages`를 프로젝트의 `_entries` 규칙으로 해시하면 `9dafe4d0…`이 정확히 재현된다. 현재 트리와 비교하면 변경 25개, 추가 1개다.
- d/e가 지나는 코드는 그대로다:
  - 바뀌지 않음: fence.py, sweeper.py, settle.py, scheduling/costs.py, scheduling/jobs.py, db/repo.py, mbnas_runner/daemon.py, journal.py.
  - routes.py: E8 본문은 docstring만 바뀌었다. 바뀐 claim 코드는 CPU runner 전용이고, GPU runner는 여전히 `repo.acquire_lease`로 lease한다.
- 잔여 사항은 N3(문서 완전성)이다.

D3 근거:

- `mbnas_core/src/mbnas_core/runner_api/routes.py:11-17` docstring이 "한 트랜잭션(SAVEPOINT)이라 PROCESSING 행을 커밋하지 않으며, 202와 60 s 정리는 외부/구버전 writer 대비 방어 경로"로 정정됐다.
- DD-A61(a)가 이를 기록하고, analysis 문서는 동결하기로 결정했다.

## 새 항목 평가 (조정자 요청)

### DD-A63 (d) fleet-aware claim 규칙 — lease_fencing 관점

규칙: `routes.py:211-299`(`cpu_runner`, `requires_cuda`, `needs_cuda`, `_cuda_runner_kinds`, `_lease_for_cpu_runner`)와 claim 분기 `:318`, `:328`.

통과한 부분:

- **건너뛴 job은 lease되지 않는다.** attempt 0, lease_owner None이 유지된다.
  - `tests/owner/runner_protocol/test_dd_a62_cpu_claim_and_commit.py:199`, `:218`(`_queued_untouched`), E1에서 PASSED.
- **lease 컬럼과 조건이 `repo.acquire_lease`와 같다.**
  - 컬럼: status LEASED, lease_owner, lease_expires_at, heartbeat_at, attempt+1, updated_at.
  - 조건: QUEUED 또는 만료, attempt<max, run 상태.
  - 대조 위치: `routes.py:271-291` vs `db/repo.py:382-411`.
  - 따라서 이후 fence(lease_owner+attempt, `fence.py:20-39`)가 똑같이 적용된다.
- **조건부 단일 문장이다.** `UPDATE … RETURNING`(autocommit, `db/core.py:4`, `:167`)이라 동시 claim이 겹쳐도 한쪽만 이긴다.
  - P2 probe: GPU runner가 EVALUATE만 제공하는 혼합 fleet에서 CPU runner 12대가 job 8개를 동시에 claim했다.
  - 3회 모두 8건이 각각 정확히 1번씩 lease됐고, attempt {1}, LEASED였다. 12개 claim 모두 CPU 경로를 거쳤다(monkeypatch로 계수).
- **GPU 경로는 바뀌지 않았다.** `:236` PASSED, diff로도 확인.
- **CPU 전용 fleet은 종전과 같다.** `:229`, `:249` PASSED.

결함: 이 경로도 만료 lease(`routes.py:275`, `:289`의 `lease_expires_at < ?`)를 sweeper의 과금 회수 없이 잡는다. **N1**에 해당하며 P4에서 재현했다.

### DD-A63 (a) commit_not_evaluated — idempotent/atomic 관점

- `engine.commit_evaluation`(`engine.py:572-584`)이 `commit_not_evaluated`(`:655-…`)로 분기한다. 그러므로 runner 경로에서는 E8 단일 tx(`routes.py:563-603`) 안에서 실행된다.
- immutable EVALUATED 행은 대체되지 않는다(`engine.py:583`, `:683`).
- NOT_EVALUATED 행 자체는 mutable(immutable=0)이다. DD-A63(a) 설계대로이고 `test_dd_a62…:149`가 단언한다. E8 재생은 `idempotent_responses`에서 끝나고 엔진까지 가지 않는다.
- 기존 테스트, E1·E2에서 PASSED:
  - `test_dd_a62_cpu_claim_and_commit.py:119`(명시 학습 COMPLETED, 지표 null, checkpoint 유지, candidate.evaluated 없음, 공개 Snapshot 계약 위반 0).
  - `tests/owner/orchestration/test_dd_a62_not_evaluated_search.py:69`, `:139`.
- P3 probe(평가자, 실행):
  - (1) 같은 digest 재생: 저장된 응답과 동일한 200, job/evals/cands/costs/idem/last_seq 불변.
  - (1') 다른 digest: 409 RESULT_CONFLICT, 상태 불변(+ error 로그 1건, last_seq +1).
  - (2) `commit_not_evaluated`가 행·candidate·rung·로그를 쓴 **뒤** 예외: 500 COMMIT_FAILED, 상태가 커밋 직전과 **완전히 같음**(job RUNNING, 행 TRAINING, candidate TRAINING, ledger 0, idem 0, last_seq 6=6). 재시도 200 → run COMPLETED, SEARCH_COMPLETE 1행, ledger [(1, train)].
- 판정: 멱등·원자성 요건을 충족한다. 과금은 학습 1행뿐이고 환급·재청구는 없다.

## 기준별 채점

### 1. idempotent_atomic — 25/25 (anchor 25)

필수 검사 T3-10/11/12/22는 E1에서 PASSED다. 테스트 파일은 EVAL_1과 같은 sha이고 줄도 같다.

| T3 | 테스트 |
|---|---|
| T3-10 | `test_t3_commit.py:34` |
| T3-11 | `test_t3_commit.py:50` |
| T3-12 | `test_t3_commit.py:70` |
| T3-22 | `test_t3_commit.py:254` |

코드:

- E8 단일 tx: `routes.py:563-603`. INSERT PROCESSING `:567`, fence `:574`, LEASE_LOST 분기 `:578-587`, settle `:590-603`.
- 롤백과 500: `:610`.
- digest 재계산: `mbnas_contracts/src/mbnas_contracts/runner_protocol.py:402-410`. E2의 `test_digest_and_hash_formats`가 PASSED다.
- D3 정정: docstring `:11-17`.

보조 증거: DD-A63(a) commit의 재생·충돌·롤백(P3), T3-20, T3-21, 공개 COMMIT_FAILED 재전송 단위 테스트(모두 PASSED).

잔여(감점 없음): 동시 최초 commit 분기 `_Retry`(`routes.py:565`, `:604`)는 여전히 테스트가 없다(확인 못 함 3).

### 2. revision_rng — 25/25 (anchor 25; EVAL_1 20 → 25)

필수 검사는 모두 E1·E2에서 PASSED다.

- T3-37: `test_t3_owner_generation.py:42`.
- `test_generation_recovery_independent.py` 7/7.

EVAL_1 감점 사유(D1)는 해결됐다. D1 테스트 4건이 PASSED이고, DD-A56(a)가 "매 원자 저장에 protocol_hash 포함" 해석과 "분기 테스트 추가 후 25점"을 명시했다.

보조 증거, 모두 PASSED:

- `qa/test_immutable.py:72`: commit_evaluation 재생 보호. `engine.py:583`.
- `test_semantics.py:204`.
- `test_generation_only.py::test_cancel_recovers_journal_candidate_saved_before_notification`.

### 3. lease_fencing — 20/25 (anchor 20; EVAL_1 25 → 20)

필수 검사 T3-13~18은 E1에서 PASSED다.

| T3 | 테스트 |
|---|---|
| T3-13 | `test_t3_commit.py:94` |
| T3-14 | `test_t3_commit.py:115` |
| T3-15 | `test_t3_control.py:148` |
| T3-16 | `test_t3_commit.py:134` |
| T3-17 | `test_t3_commit.py:173` |
| T3-18 | `test_t3_commit.py:191` |

그 밖의 근거:

- fence와 SQL attempt 조건은 바뀌지 않았다(`fence.py:20-39`, `repo.py:415-416`, `jobs.py:68-92`, `settle.py:66-92`).
- DD-A63(d) 경로의 attempt·동시성은 위 평가대로 통과다.
- GPU 실측 d/e는 `9dafe4d0…`에서 PASS이고, 이월이 타당하다(D2).

**감점 사유 N1(Major).** "sweeper 회수·과금 1행"이 sweeper가 먼저 도는 경로(T3-13)에서만 성립한다. 실행 재현(P1, P4):

1. runner R1이 시작한 뒤 죽는다(마지막 heartbeat t0+3 s, lease 만료).
2. sweeper가 다음으로 돌기 **전에** R2가 claim한다.
3. `repo.acquire_lease`(`repo.py:407`)가 만료된 RUNNING 행을 바로 attempt 2로 lease한다.
4. 결과:
   - ledger `[]`, job.error None(LEASE_EXPIRED 기록 없음).
   - 다음 sweep: `reclaimed []`.
   - attempt 2 성공 후 ledger `[(2, 'train', 0.158)]`. 죽은 attempt의 작업 시간은 영영 과금되지 않고, 다음 attempt는 `retry`가 아니라 `train`이 된다.
5. DD-A63 CPU 경로(`_lease_for_cpu_runner`)에서도 똑같다(P4: CPU_PATH_CALLS 2, SECOND attempt 2, sweep `reclaimed []`, ledger `[]`).
6. 죽었던 R1이 나중에 결과를 보내면 `record_lost_attempt`가 1행을 남긴다(P4: 409 LEASE_LOST, ledger `[(1, failed, 20.0, LEASE_LOST)]`). 따라서 누락은 **보고하지 못한 attempt**(호스트 사망, journal 소실)에 한정된다.

창의 크기는 기본값 기준으로 sweeper 주기 10 s(`config.py:94`, `sweeper.py:2`) 대 runner poll 2 s(`mbnas_runner/config.py:186`)다. owner worker가 idle일 때는 tick(1 s)마다 reclaim하므로 창이 줄지만 사라지지는 않는다.

owner worker 자신은 이 불변식을 코드에 적어 지키고 있다: `owner_worker/main.py:315-316` "reclaim right before leasing: acquire_lease also takes expired leases, which must be billed by reclaim first". E2 claim(`routes.py:310-335`)만 이를 따르지 않는다. 설계 §6.1(claim = `acquire_lease` 단일문)과 D4(sweeper 단일 회수)가 이 경우를 다루지 않았고, 이를 수용한 DD도 없다(grep).

attempt fencing, stale 409, 이중 실행 방지는 온전하므로 15가 아니라 20이다.

### 4. budget_cancel — 25/25 (anchor 25)

필수 검사, 모두 E1·E2에서 PASSED:

- T3-08 `test_t3_control.py:100`, T3-09 `:129`, T3-14, T3-16.
- `record_lost_attempt` 의미 테스트: `worker/test_lease.py:161`, `:205`, T3-17.
- `api/test_state_machine.py:77`.

DD-A56(b)가 "동기화 미완료 run 미완료" = job 상태 동기화(취소 후 runner 결과를 받아야 CANCELLED)로 해석을 확정했다. artifact 전송 실패는 job 실패가 아니다(DD-A38/A39).

DD-A63(a)의 NOT_EVALUATED commit은 학습 1행만 과금하고 환급·재청구가 없다(P3 ledger `[(1, 'train')]`).

N1과의 관계:

- "timeout 불명 시 자동 환급/재청구 없음"(설계 F10: 회수 뒤 늦은 결과)은 N1 경로에서도 지켜진다. 늦은 결과가 오면 정확히 1행이 과금된다(P4).
- N1의 누락은 결과가 끝내 오지 않는 경우의 회수 과금 문제다. 그래서 lease_fencing에서 한 번만 감점했다.
- 설계자가 이를 "자동 환급"으로도 본다면 이 기준도 20점이 되고 총점은 90이 되어 FAIL이다(90 초과 필요). 판단은 설계자 몫이다.

## 결함 (EVAL_2 기준; D1·D2·D3는 해결)

| ID | 심각도 | 소유 패키지/파일 | 내용 | 구체적 수정 |
|---|---|---|---|---|
| N1 | **Major** | `mbnas_core/src/mbnas_core/runner_api/routes.py:310-335`(E2 claim), `:260-299`(`_lease_for_cpu_runner`, `:275`, `:289`), `mbnas_core/src/mbnas_core/db/repo.py:382-411`(`acquire_lease`가 만료 lease 허용) | 만료된 lease를 claim이 sweeper보다 먼저 잡으면 죽은 attempt의 GPU 작업 시간이 과금되지 않는다(lost 행 0, LEASE_EXPIRED 감사 누락, 다음 attempt `train` 분류). runner가 2대 이상이면 기본 타이밍(sweep 10 s, poll 2 s)에서 현실적으로 일어난다. GPU 경로와 CPU 경로 모두 재현(P1, P4). | E2에서 lease 전에 `sweeper.reclaim(conn, settings, now)`를 호출한다. owner `tick()`의 `main.py:315-317`과 같은 순서이고, BEGIN IMMEDIATE로 직렬화된다. 또는 runner claim은 `status='QUEUED'`만 lease하고 만료 행은 sweeper에 맡긴다. 회귀 테스트(부록 C의 P1 소스를 그대로 쓸 수 있음)로 GPU·CPU 두 경로에서 "만료 후 sweep 전 claim → a1 failed/lost 정확히 1행 ≈ heartbeat_at−started_at, error LEASE_EXPIRED, a2는 retry"를 단언한다. |
| N2 | Minor | `docs/internal/VALIDATION_REPORT.md:11`(T3 행), `:18`(T7 행) | T3 행이 "확인 못 함"이다. runner_protocol 67/67과 7V_FINAL owner gate 1569/0이 있는데도 그렇다. T7 행도 "확인 못 함"이다. `T7/RERUN_README.md` 두 표가 `9dafe4d0…`에서 PASS를 기록했고 DD-A54/A56(c)가 이월을 수용했는데도 그렇다. | T3 행: 현재 트리 해시와 함께 runner_protocol 결과, T3-34/35 위치를 적는다. T7 행: "PASS on 9dafe4d0, 이월(DD-A56 c)"과 그 뒤 바뀐 경로 목록을 적는다. |
| N3 | Minor | `docs/internal/DECISIONS.md` DD-A56(c) | 이후 변경을 "checkpoint artifact_id·웹·테스트"로 적었지만, 고정 사본 diff로는 변경 25개 + 추가 1개다. 여기에는 `runner_api/routes.py`(claim), `orchestration/engine.py`(NOT_EVALUATED), `owner_worker/main.py`, `mbnas_runner/{client,config,evaluation/*}`가 들어 있다. d/e GPU 경로가 바뀌지 않아 이월 결론은 유지되지만 기록이 불완전하다. | 최종 보고의 트리 해시 차이 문단에 경로 목록(또는 이 평가의 diff 명령)을 덧붙인다. 7V_FINAL README에 최종 소스 트리 해시를 기록한다. |

Blocker 0, Critical 0, Major 1, Minor 2.

## 총점·판정

25 + 25 + 20 + 25 = **95/100**으로 90을 넘고 Blocker/Critical이 0이므로 **PASS**다.
필수 시험(T3-08~18, T3-22, T3-37, `test_generation_recovery_independent`, `record_lost_attempt` 의미 테스트)은 현재 트리 `5a7bc592…`에서 다시 실행해 전부 통과했다.
N1(Major)은 합격 조건을 깨지는 않지만 수정을 권고한다.

## 확인 못 함 (unverified — failed 아님)

1. 7V_FINAL owner gate(1569/0, 21:48–22:03Z)가 `5a7bc592…`에서 돌았는지. README에 소스 트리 해시가 없다.
2. 현재 트리의 GPU 동작. GPU 증거는 `9dafe4d0…` 기준 이월이고, GPU 사용 금지로 재실행하지 않았다.
3. 동시 최초 result commit 분기 `_Retry`(`routes.py:565`, `:604`). 테스트가 없다.
4. P2 동시성 probe는 TestClient 인프로세스 스레드를 쓴다(T3-01과 같은 한계). 실제 다중 프로세스 HTTP 동시성은 실행하지 않았다.
5. N1의 실제 발생 빈도. 기본값(TTL 60 s, sweep 10 s, poll 2 s, owner tick 1 s)으로 추정했을 뿐 측정하지 않았다.

## 부록 A — T3 id ↔ 테스트 (C04 범위, E1, 현재 트리)

| T3 | C04 기준 | 테스트 | 결과 |
|---|---|---|---|
| T3-08 | budget_cancel | test_t3_control.py:100 `test_cancel_control_terminates_child_and_commits_cancelled` | PASSED |
| T3-09 | budget_cancel | test_t3_control.py:129 `test_succeeded_result_after_cancel_request_is_recorded_cancelled` | PASSED |
| T3-10 | idempotent_atomic | test_t3_commit.py:34 `test_result_replay_same_digest_returns_stored_response_without_side_effects` | PASSED |
| T3-11 | idempotent_atomic | test_t3_commit.py:50 `test_result_with_different_digest_is_409_conflict` | PASSED |
| T3-12 | idempotent_atomic | test_t3_commit.py:70 `test_result_processing_row_yields_202_with_retry_after_and_sweeper_clears_orphan` | PASSED |
| T3-13 | lease_fencing | test_t3_commit.py:94 `test_sweeper_reclaims_expired_lease_and_bills_lost_window` | PASSED |
| T3-14 | lease_fencing, budget_cancel | test_t3_commit.py:115 `test_sweeper_fails_job_and_candidate_when_attempts_exhausted` | PASSED |
| T3-15 | lease_fencing | test_t3_control.py:148 `test_heartbeat_after_reclaim_is_409_lease_lost` | PASSED |
| T3-16 | lease_fencing, budget_cancel | test_t3_commit.py:134 `test_stale_succeeded_result_is_discarded_and_lost_row_extended_once` | PASSED |
| T3-17 | lease_fencing | test_t3_commit.py:173 `test_lost_lease_without_reclaim_row_appends_one_lost_row` | PASSED |
| T3-18 | lease_fencing | test_t3_commit.py:191 `test_stale_attempt_of_same_runner_cannot_renew_touch_or_finish` | PASSED |
| T3-22 | idempotent_atomic | test_t3_commit.py:254 `test_commit_exception_rolls_back_and_leaves_no_idempotent_row` | PASSED |
| T3-37 | revision_rng | test_t3_owner_generation.py:42 `test_sigkill_mid_generation_recovers_saved_candidates_once` | PASSED |

누락 0.

## 부록 C — N1 재현 probe (평가자 작성, scratchpad; 회귀 테스트로 옮길 수 있음)

`c04e2_probe/conftest.py`는 `tests/owner/conftest.py`와 `tests/owner/runner_protocol/conftest.py`의 fixture를 경로로 불러온다. 프로젝트 파일은 수정하지 않는다.

```python
def test_claim_of_expired_lease_before_sweep(core, runner_sim, runner_sim2, base_proto, sweeper, db):
    run_id, _ = harness.validate_and_run(core, base_proto())
    runner_sim.register()
    first = runner_sim.claim()
    st = runner_sim.start(first)
    with tx(db):  # runner 1 died: last heartbeat 3 s after start, lease ran out a moment ago, no sweep yet
        db.execute("UPDATE jobs SET started_at = ?, heartbeat_at = ?, lease_expires_at = ? WHERE id = ?",
                   (iso_after(utcnow_iso(), -20), iso_after(utcnow_iso(), -17), iso_after(utcnow_iso(), -1),
                    first.job_id))
    runner_sim2.register()
    second = runner_sim2.claim()  # an idle runner polls before the sweeper
    swept = sweeper()
    # observed: second = attempt 2; ledger []; job error None; swept["reclaimed"] == [];
    # after attempt 2 succeeds: ledger [(2, 'train', 0.158)]  -> the dead attempt's 3 s are never billed
```

관측 출력:

```
CLAIM_BEFORE_SWEEP ('job-ac6730e18071473a', 2)
JOB_AFTER_CLAIM {'status': 'LEASED', 'attempt': 2, 'lease_owner': 'R2', 'error': None}
LEDGER_AFTER_CLAIM []
SWEEP {'reclaimed': [], 'workers_pruned': 0, 'runners_lost': 0, 'processing_cleared': 0}
LEDGER_AFTER_SWEEP []
ATTEMPT2 200 {'outcome': 'SUCCEEDED', 'job_status': 'SUCCEEDED', 'run_status': 'COMPLETED', 'response_digest': 'sha256:813b0be714f585d412043bc6a70a49a1c1be2c71ebbf4d484628edb916307f3c'}
LEDGER_FINAL [(2, 'train', 0.158, None)]
RUN_COSTS {'actual_gpu_seconds': 0.157965, 'baseline_gpu_seconds': 0.157965, 'overshoot_gpu_seconds': 0.0, 'overshoot_gpu_seconds.Baseline': 0.0}
1 passed, 1 warning in 4.14s
--- P4 (late result after preemption; CPU path)
LATE_RESULT 409 LEASE_LOST LEDGER [(1, 'failed', 20.0, 'LEASE_LOST')]
CPU_PATH_CALLS 2 SECOND 2
SWEEP {'reclaimed': [], 'workers_pruned': 0, 'runners_lost': 0, 'processing_cleared': 0} LEDGER []
2 passed, 1 warning in 13.06s
--- P2 (CPU path concurrency, 3 runs)
TOTAL 8 LEASED 8 DISTINCT 8 CPU_PATH_CALLS 12
ATTEMPTS [1] STATUSES ['LEASED']
1 passed, 1 warning in 5.17s
TOTAL 8 LEASED 8 DISTINCT 8 CPU_PATH_CALLS 12
ATTEMPTS [1] STATUSES ['LEASED']
1 passed, 1 warning in 7.41s
TOTAL 8 LEASED 8 DISTINCT 8 CPU_PATH_CALLS 12
ATTEMPTS [1] STATUSES ['LEASED']
1 passed, 1 warning in 6.53s
--- P3 (NOT_EVALUATED replay/conflict/rollback)
CONFLICT 409 RESULT_CONFLICT SEQ_DELTA 1
BROKEN 500 COMMIT_FAILED
2 passed, 2 warnings in 46.67s
```

## 부록 B — 재실행 출력 발췌 (scratchpad 로그에서 기계적으로 복사)

### c04e2_rp

```
start 2026-10-27T22:43:56Z cwd=/home/mgjeong/Desktop/etri_hd/mbnas_application
exit=0 dur=145s end=2026-10-27T22:46:21Z
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
tests/owner/runner_protocol/test_device_used.py::test_search_run_records_raw_metrics_device_used_cpu PASSED
tests/owner/runner_protocol/test_device_used.py::test_explicit_training_run_records_raw_metrics_device_used_cpu PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_unit[as reported by the runner-verified] PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_unit[one hash changed-mismatch] PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_unit[one file missing-mismatch] PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_unit[extra file-mismatch] PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_unit[nothing reported-unknown] PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_runtime_label_is_unknown_when_the_core_cannot_hash_its_own_operators PASSED
tests/owner/runner_protocol/test_runtime_manifest_status.py::test_register_labels_the_reported_operator_manifest PASSED
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
================== 67 passed, 1 warning in 142.48s (0:02:22) ===================
```

### c04e2_named

```
start 2026-10-27T22:50:27Z
exit=0 dur=51s end=2026-10-27T22:51:18Z
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
tests/owner/search/test_generation_only.py::test_off_validation_without_dataset_gpu_and_fixed_point PASSED
tests/owner/search/test_generation_only.py::test_new_schema_defaults_evaluation_on PASSED
tests/owner/search/test_generation_only.py::test_off_controls_invalid[nc-0] PASSED
tests/owner/search/test_generation_only.py::test_off_controls_invalid[candidate_count-0] PASSED
tests/owner/search/test_generation_only.py::test_off_controls_invalid[max_attempts-1] PASSED
tests/owner/search/test_generation_only.py::test_off_controls_invalid[allow_duplicates-yes] PASSED
tests/owner/search/test_generation_only.py::test_off_controls_invalid[seeds-value4] PASSED
tests/owner/search/test_generation_only.py::test_off_controls_invalid[input_size-33] PASSED
tests/owner/search/test_generation_only.py::test_plan_is_only_generation_and_forbidden_calls_never_occur PASSED
tests/owner/search/test_generation_only.py::test_generation_progress_persists_once_and_only_real_artifact PASSED
tests/owner/search/test_generation_only.py::test_cancel_recovers_journal_candidate_saved_before_notification PASSED
tests/owner/search/test_generation_only.py::test_unavailable_structure_runtime_never_returns_internal_source PASSED
tests/owner/search/test_generation_only.py::test_off_validation_resolves_per_seed_scope_and_contract_structure_status PASSED
tests/owner/search/test_generation_only.py::test_on_path_candidate_reuses_proposal_build_report_when_input_size_matches PASSED
tests/owner/search/test_generation_only.py::test_on_path_candidate_rebuilds_only_when_input_size_differs PASSED
tests/owner/search/test_generation_only.py::test_on_path_invalid_candidate_records_structure_failed_with_diagnostics PASSED
tests/owner/search/test_generation_only.py::test_generation_only_checks_each_draw_once_and_records_cpu_execution PASSED
tests/owner/search/test_generation_only.py::test_generation_worker_id_is_null_without_a_worker PASSED
tests/owner/search/test_generation_only.py::test_duplicates_are_suppressed_per_seed_only PASSED
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
tests/owner/orchestration/test_dd_a62_not_evaluated_search.py::test_ga_not_evaluated_candidates_record_the_reason_and_are_never_promoted PASSED
tests/owner/orchestration/test_dd_a62_not_evaluated_search.py::test_a_payload_without_metrics_that_does_not_say_not_evaluated_still_fails PASSED
tests/owner/orchestration/test_semantics.py::test_evaluated_rows_are_immutable_and_replay_is_idempotent PASSED
tests/public/contracts/test_runner_protocol.py::test_digest_and_hash_formats PASSED
tests/public/contracts/test_runner_protocol.py::test_fence_fields_are_required_on_job_writes PASSED
tests/public/runner/test_runner_daemon_unit.py::test_lease_lost_heartbeat_stops_child_and_discards_result PASSED
tests/public/runner/test_runner_daemon_unit.py::test_lease_expiry_watchdog_kills_child_without_renewal PASSED
tests/public/runner/test_runner_daemon_unit.py::test_commit_failed_is_retried_with_the_same_digest PASSED
======================== 62 passed, 1 warning in 49.14s ========================
```

### tree hash

```
start 2026-10-27T22:43:36Z: "sha256":"sha256:5a7bc5920ac3c1fe630a45c8ea0dd4661cb754661a90da5c09441fb7237c4223"
end   2026-10-27T22:55:43Z (--digest-only): sha256:5a7bc5920ac3c1fe630a45c8ea0dd4661cb754661a90da5c09441fb7237c4223
frozen G2 copy (TREE 9dafe4d0) vs current, project _entries rule:
frozen sha256:9dafe4d021ab572058c306e596b686d3f19a1203cab7504c4c2662973fb670ea 220
current sha256:5a7bc5920ac3c1fe630a45c8ea0dd4661cb754661a90da5c09441fb7237c4223 221
```

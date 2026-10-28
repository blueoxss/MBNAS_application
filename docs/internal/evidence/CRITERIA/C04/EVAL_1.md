# C04 분산 상태 — 독립 평가 EVAL_1

- set: **C04** (`docs/internal/criteria/C04.json` v1.0)
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자 — 채점만 하며 결정은 설계자
- time: 2026-10-28T03:29+09:00 (2026-10-27T18:29Z)
- tree hash: `sha256:634a083bb910a28d1aa183c9a6a2cee66da7c1fc937f01f8aa85d1bb4c4a301d` (mbnas-source-tree/2, 221 files).
  `runtime/venv/bin/python -B scripts/dev/source_tree_hash.py` exit 0. 평가 시작 시점(≈18:17Z)과 테스트 재실행 후(18:25:29Z, `--digest-only`)의 값이 같다.
  지시된 `| tail -3`은 JSON 닫는 괄호만 출력하므로 `sha256` 필드를 인용했다.
- 환경: CPU 전용. `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`. pytest `--basetemp`와 `MBNAS_TEST_ROOT`는 세션 scratchpad를 가리켜 프로젝트 트리에 쓰지 않았다. GPU, /data, etri_nas, build/source_snapshot은 건드리지 않았다. 프로젝트에 쓴 파일은 이 파일 하나다.

## 결론

총점은 **95/100**(25+20+25+25)이고 Blocker 0, Critical 0이므로 **PASS**다(기준: 90 초과, Blocker/Critical 0).
C04가 지정한 T3 id 13개(T3-08~18, T3-22, T3-37)는 모두 현재 트리에서 통과한 테스트에 1:1로 대응하며, 누락은 0건이다(부록 A).

## 재실행 요약

모두 cwd=TARGET_ROOT에서 `runtime/venv/bin/python -B -m pytest <대상> -p no:cacheprovider -v -rfEs --basetemp=<scratch>`로 실행했다.

| # | 대상 | exit | 결과 | 시간 |
|---|---|---|---|---|
| R1 | `tests/owner/runner_protocol` 전체. 마커 필터는 걸지 않았다(디렉터리 안에 gpu/live 마커가 없음을 확인함) | 0 | 44 passed, 0 failed, 0 skipped | 119 s (18:18:08Z–18:20:07Z) |
| R2 | `tests/owner/search/test_generation_recovery_independent.py`, `tests/owner/search/test_generation_only.py`, `tests/owner/worker/test_lease.py`, `tests/owner/qa/test_immutable.py` | 0 | 41 passed | 32 s |
| R3 | `tests/owner/api/test_state_machine.py` | 0 | 9 passed | 13 s |
| R4 | `test_semantics.py::test_evaluated_rows_are_immutable_and_replay_is_idempotent`, `::test_runs_are_deterministic_in_structure`, `test_proposers.py::test_same_seed_reproducible_and_seed_changes_sequence` | 0 | 3 passed | 44 s |
| R5 | `tests/public/contracts/test_runner_protocol.py::test_digest_and_hash_formats`, `::test_fence_fields_are_required_on_job_writes` | 0 | 2 passed | 1 s |
| R6 | `tests/public/runner/test_runner_daemon_unit.py` 5건: lease_lost 시 child 중지, 만료 watchdog, COMMIT_FAILED 뒤 같은 digest 재전송, 재시작 재생 폐기, journal digest 일치 | 0 | 5 passed | 16 s |

CPU 합계는 약 225 s다(15분 미만).
로그(프로젝트 밖): `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c04_rp.txt`, `.../c04_search.txt`, `.../c04_sm.txt`, `.../c04_det.txt`, `.../c04_pub.txt`, `.../c04_pubrun.txt`(각각 `.meta` 포함).
트리 안에 증거를 남기기 위해, C04 관련 테스트의 PASSED 줄을 부록 B에 그대로 옮겼다.

열람·대조한 트리 내 기존 증거(모두 실행된 명령이 만든 파일):

- `docs/internal/evidence/WP2/2V/pytest_runner_protocol.txt` + `.meta`: exit=0, 40 passed(WP-2 수용 시점의 트리).
- `docs/internal/evidence/WP7/G2FIX/logs/round2_owner_runner_protocol.txt`: 44 passed(10-27 16:56).
- `docs/internal/evidence/WP4/4C/owner_gate_run4.txt` + `.meta`: exit=0, 1510 passed / 8 skipped / 47 deselected / 0 failed, 18:12:41Z–18:28:30Z.
  다른 에이전트가 같은 시간대에 실행한 것이고 내가 기동하지 않았다. runner_protocol, search, worker, qa, api 테스트가 들어 있다.
- `docs/internal/evidence/T7` item e/d(GPU, TREE_HASH 164292c6…):
  - `steps.jsonl`의 `e-sigkill-runner`: runner_exit -9, child_pids_alive_after [].
  - LEASE_EXPIRED 재큐까지 59.17 s.
  - idempotent_responses: attempt 1은 409 DONE, attempt 2는 200 DONE.
  - `dbviews/e_train-fe074a484661.json`의 cost_ledger: attempt 1의 failed 행 1개(83.38 s), attempt 2의 retry 45.61 s와 profile 3.22 s.

## 기준별 채점

### 1. idempotent_atomic — 25/25 (anchor 25)

필수 검사 T3-10/11/12/22는 R1에서 4건 모두 PASSED다.

- T3-10 → `tests/owner/runner_protocol/test_t3_commit.py:34` `test_result_replay_same_digest_returns_stored_response_without_side_effects`
  - 응답 본문이 같다.
  - job/costs/evals/cands 스냅샷과 `last_seq`가 변하지 않는다.
  - train 행 1개, EVALUATED 행 1개.
- T3-11 → `:50` `test_result_with_different_digest_is_409_conflict`
  - 409 RESULT_CONFLICT, 스냅샷 불변, error 로그 정확히 1건.
- T3-12 → `:70` `test_result_processing_row_yields_202_with_retry_after_and_sweeper_clears_orphan`
  - 202와 `Retry-After: 2`를 돌려준다.
  - 60 s 미만인 행은 남기고, 60 s를 넘은 행은 sweeper가 지운다. 그다음 재시도는 200.
- T3-22 → `:254` `test_commit_exception_rolls_back_and_leaves_no_idempotent_row`
  - 500 COMMIT_FAILED, idempotent 행 0, job은 RUNNING 유지, ledger 0.
  - 재시도는 1회 성공한다.

코드 열람(`mbnas_core/src/mbnas_core/runner_api/routes.py:437-510`):

- 키는 `(job_id, attempt)`다(:411-413, :460-462).
- PROCESSING insert → fence → `settle_job` → `_store` DONE이 하나의 `with tx(conn)` 블록(:456-496) 안에서 일어난다.
- 중첩 tx는 SAVEPOINT가 되므로(`mbnas_core/src/mbnas_core/db/core.py:183-202`) COMMIT은 바깥에서 한 번뿐이다.
- 재생/충돌/PROCESSING 판정은 :416-427, 예외 시 롤백과 500은 :501-503이다.
- 서버가 digest를 재계산한다(`mbnas_contracts/src/mbnas_contracts/runner_protocol.py:402-410`). R5의 `test_digest_and_hash_formats`가 PASSED다.

보조 증거:

- T3-20 `test_t3_restart.py:133`: journal 재생 → commit 1회, acked. [after]/[before] 2건 PASSED.
- T3-21 `test_t3_commit.py:229` PASSED.
- R6: COMMIT_FAILED 뒤 같은 digest로 재전송한다. PASSED.
- T7 e(GPU): 명시적 재생에서 저장된 409가 재생되고, 200 응답이 동일하다.

갭:

- (a) E8 경로로는 PROCESSING 행이 커밋될 수 없다. 같은 tx 안에서 DONE으로 바뀌기 때문이다. 그래서 T3-12는 SQL로 행을 직접 넣어 방어 경로를 검증한다(`test_t3_commit.py:77-80`). 설계 §7.2의 "중간 crash 시 남는 상태"라는 서술은 구현과 다르다. 동작 문제는 아니다(D3).
- (b) 같은 attempt에 대한 동시 최초 commit 경쟁 분기 `_Retry`(routes.py:457-458, :497-498)에는 테스트가 없다(확인 못 함 2).

### 2. revision_rng — 20/25 (anchor 20: 사소한 누락 외 충족)

필수 검사 T3-37과 `tests/owner/search/test_generation_recovery_independent`는 R1·R2에서 PASSED다.

- T3-37 → `test_t3_owner_generation.py:42` `test_sigkill_mid_generation_recovers_saved_candidates_once`
  - 실제 CPU 생성 백엔드를 쓰고, 기본 owner 모드 worker를 SIGKILL한다.
  - `tests/owner/worker/test_generation_crash.py`의 원래 단언을 그대로 호출하고, `--cpu`가 없음을 추가로 단언한다.
- `test_generation_recovery_independent.py` 7/7 PASSED:
  - :64 crash 뒤 재샘플링이 없고, 결과 hash가 무중단 실행과 같다.
  - :90 완료된 재시도는 런타임을 만들지 않고 재생한다.
  - :100 중복 상한.
  - :114 FAIL/UNVERIFIED 후보는 받아들이지 않는다(×2).
  - :123 pause 시 완료된 후보를 보존한다.
  - :139 런타임이 없으면 명시적으로 실패한다.

코드 열람:

- `mbnas_core/src/mbnas_core/search/generation.py:104-125`: flock, journal identity(protocol_hash+run_id), `rng.setstate`, 그리고 `save()`가 candidates+rng_state+attempts를 `atomic_write_bytes`로 원자 기록한다.
- 같은 파일 :188-189: 알리기 전에 먼저 저장한다(save-before-notify).
- commit_evaluation 재생 보호: `mbnas_core/src/mbnas_core/orchestration/engine.py:561-563`.

보조 증거(R2/R4), 모두 PASSED:

- `tests/owner/qa/test_immutable.py:72`: 변경된 재생이 커밋된 행을 바꾸지 않는다.
- `tests/owner/orchestration/test_semantics.py:204`: 재생이 멱등이다.
- `tests/owner/search/test_proposers.py:82`: 같은 seed로 재현된다.
- `test_generation_only.py::test_cancel_recovers_journal_candidate_saved_before_notification`.

감점 사유: 관측 항목의 "policy revision"에 실행 근거가 없다.

- journal이 다른 configuration(protocol_hash/run_id)에 묶여 있으면 거부하는 분기(generation.py:113-118 `GENERATION_STATE_INVALID`)를 실행하는 테스트가 `tests/` 전체에 0건이다(grep).
- 동시 attempt를 막는 분기(:107-109 `GENERATION_BUSY`)도 마찬가지다.
- 따라서 정적 근거뿐이다(D1).

"policy revision"을 정의한 문서는 C04.json 말고는 없다. 평가자 해석은 "run에 고정된 resolved protocol(proposal_policy 포함)의 hash가 journal 상태와 함께 원자 기록되고 검증되는 것"이다. 설계자가 "매 save의 원자 쓰기에 protocol_hash가 포함되어 실행되므로 충족"이라고 본다면 이 항목은 25점이다.

### 3. lease_fencing — 25/25 (anchor 25)

필수 검사 T3-13~18은 R1에서 6건 모두 PASSED다.

- T3-13 → `test_t3_commit.py:94`: QUEUED, LEASE_EXPIRED, ledger 1행(failed/lost 3.0 s), 다른 runner가 attempt 2를 잡는다.
- T3-14 → `:115`: FAILED LEASE_EXPIRED_MAX_ATTEMPTS, candidate TRAIN_FAILED, failed 3행.
- T3-15 → `test_t3_control.py:148`: 409 LEASE_LOST, daemon heartbeat 루프가 5 s 안에 lease_lost를 기록한다.
- T3-16 → `test_t3_commit.py:134`
  - 409, 기존 행 1개를 전체 구간으로 연장(extended_by), evaluation 없음.
  - 재생해도 행이 늘지 않는다.
  - 다음 attempt는 retry로 과금된다.
- T3-17 → `:173`: lost 행 1개.
- T3-18 → `:191`
  - attempt 1의 start/heartbeat/progress/checkpoint/artifacts/result가 모두 409다.
  - attempt-1 ticket으로 attempt 2에 접근하면 403 TICKET_INVALID다.
  - attempt 2 행은 오염되지 않는다.

코드:

- fence: `mbnas_core/src/mbnas_core/runner_api/fence.py:20-39`(lease_owner==runner_id ∧ attempt ∧ status).
- SQL 수준 `AND attempt = ?`: `mbnas_core/src/mbnas_core/db/repo.py:415-416`(finish_job :454-470 포함), `mbnas_core/src/mbnas_core/scheduling/jobs.py:68-92`.
- 라우트가 attempt를 넘긴다: routes.py:202-203, :253, :280, :315, `runner_api/settle.py:66-92`.
- sweeper 회수·과금: `mbnas_core/src/mbnas_core/runner_api/sweeper.py:39-60`. RUNNING만 과금하고 LEASED-only는 과금하지 않는다(T3-05 `test_t3_claim.py:188` PASSED).

보조 증거:

- R6: lease_lost 시 child 중지, 만료 watchdog PASSED. fake core 단위 테스트라 WP-2 §4에 따라 통합 증거로는 세지 않는다.
- T7 e: GPU 실측(구 트리).

갭: T7 GPU 증거는 TREE_HASH 164292c6…에만 있고 그 뒤 트리에서는 다시 실행되지 않았다(D2). 필수 검사가 CPU T3이므로 점수에는 영향이 없다.

### 4. budget_cancel — 25/25 (anchor 25)

필수 검사 T3-08/09/14/16은 PASSED다. costs.record_lost_attempt 의미 테스트도 PASSED다.

- `tests/owner/worker/test_lease.py:161` `test_lost_lease_after_reclaim_by_other_worker_bills_the_attempt_once`: 기존 failed 행을 전체 구간으로 연장하고 id를 유지한다. 두 번째 finalize도 축소·중복이 없다.
- `:205` `test_lost_lease_without_reclaim_row_appends_one_lost_row`.
- runner 경로의 T3-16/T3-17.

시험별 대응:

- T3-08 → `test_t3_control.py:100`. 실제 runner daemon과 child subprocess로 실행한다.
  - cancel 직후 CANCEL_REQUESTED + active_jobs(:115).
  - runner가 commit한 뒤 job CANCELLED, ledger [failed/RUN_CANCELLED], run CANCELLED, child 종료.
- T3-09 → `:129`: cancel 뒤 들어온 SUCCEEDED result에 200 outcome CANCELLED, discarded_result.status SUCCEEDED, EVALUATED 없음.

관측 항목 대응:

- **timeout 불명 시 자동 환급/재청구 없음**(설계 F10): T3-16(행 1개, 재생해도 추가 없음) + T3-10(재생 시 ledger 불변).
- **취소 경쟁 결정적**(§7.4): routes.py:483이 commit 시점의 run 상태 또는 cancel_observed로 판정한다. T3-09가 이를 검증한다.
- **동기화 미완료 run 미완료**(§7.4(ii)): engine.py:873-878에서 CANCEL_REQUESTED→CANCELLED 전이는 active==0일 때만 일어난다. T3-08(:115)과 `tests/owner/api/test_state_machine.py:77`이 PASSED다.
- 코드: `mbnas_core/src/mbnas_core/scheduling/costs.py:119-144`.

갭/해석: "동기화"를 artifact 전송으로 읽으면 결론이 달라진다.

- `test_artifact_transfer.py:114-127`은 전송이 실패해도 job SUCCEEDED를 단언한다.
- settle_run(engine.py:879-890)은 전송 상태를 보지 않는다. 이는 정적 확인이고, 그 테스트는 run 상태를 단언하지 않는다.
- DD-A38/A39가 이 동작을 채택했으므로 결함으로 채점하지 않고 "확인 못 함" 목록에 둔다.

## 결함

| ID | 심각도 | 소유 패키지/파일 | 내용 | 구체적 수정 |
|---|---|---|---|---|
| D1 | Minor | `mbnas_core/src/mbnas_core/search/generation.py:107-118`, `tests/owner/search/test_generation_recovery_independent.py`(core search) | journal identity(protocol_hash/run_id) 불일치 → GENERATION_STATE_INVALID, flock 경합 → GENERATION_BUSY 분기를 실행하는 테스트가 0건이다. C04의 "policy revision"은 정적 근거뿐이다. | 같은 파일에 2개를 추가한다. (1) `spec()`로 journal을 만든 뒤 protocol을 바꾼 spec(다른 protocol_hash)과 다른 run_id로 `generate` → `JobFailure.reason_code=="GENERATION_STATE_INVALID"`, emit 0회, journal 바이트 불변을 단언한다. (2) 테스트가 `generation-<seed>.lock`에 `fcntl.flock(LOCK_EX)`를 잡은 상태에서 `generate` → `GENERATION_BUSY`, `retryable is True`를 단언한다. |
| D2 | Minor | `docs/internal/evidence/T7`(WP-G2 트랙, `t7_gpu_path.py`) | SIGKILL→lease 만료→attempt+1·stale 409(item e)와 cancel(item d)의 GPU 증거가 TREE_HASH 164292c6…에만 있다. 9dafe4d0…과 현재 634a083b…에서는 다시 실행하지 않았다(`RERUN_README.md:35-36, 46-48`에 스스로 적혀 있음). | 다음 GPU 창에서 `t7_gpu_path.py --items d,e`를 최종 트리에 다시 실행한다. 또는 이월 수용을 DECISIONS에 명시한다. |
| D3 | Minor | `docs/internal/analysis/REMOTE_PROTOCOL_DESIGN.md` §7.2(PROCESSING 행), `routes.py:11-13` docstring | 설계는 "2-단계 쓰기 중간 crash 시 PROCESSING이 남는다"고 하지만, 구현은 단일 tx(routes.py:456-496, SAVEPOINT 중첩)라 E8 경로로는 남지 않는다. 202와 sweeper 정리는 방어 경로다. | §7.2와 docstring을 "단일 tx 구현에서는 외부/구버전 writer가 남긴 행에만 해당하는 방어 경로"로 고친다. |

Blocker 0, Critical 0, Major 0, Minor 3.

## 총점·판정

25 + 20 + 25 + 25 = **95/100**으로 90을 넘고 Blocker/Critical이 0이므로 **PASS**다.
필수 시험(T3-08~18, T3-22, T3-37, test_generation_recovery_independent, record_lost_attempt 의미 테스트)은 현재 트리에서 다시 실행해 전부 통과했다.

## 확인 못 함 (unverified — failed 아님)

1. T7 GPU item d/e가 현재 트리(634a083b…)에서도 재현되는지. GPU 사용이 금지되어 실행하지 않았고, 구 트리 증거만 열람했다.
2. 같은 (job, attempt)에 대한 동시 최초 result commit 경쟁(`_Retry`, routes.py:457-458, :497-498). 테스트가 없어 실행 근거가 없다. 다중 프로세스 HTTP 동시성은 TestClient 인프로세스 테스트로 간접 확인했을 뿐이다.
3. "policy revision"과 "동기화 미완료"의 정확한 의미. C04.json 말고는 정의 문서가 없어서, 위 해석은 평가자 판단이다. 설계자 확인이 필요하다.
4. artifact 전송이 끝나지 않은 run이 COMPLETED가 되는지. engine.py:879-890을 정적으로 읽었을 뿐 run 상태를 실행으로 확인하지 않았다.
5. 다른 에이전트가 실행한 owner_gate_run4의 전 구간 동안 트리가 같았는지. 내 해시 확인은 ≈18:17Z와 18:25Z 두 시점뿐이다.
6. `tests/public/runner` 단위 테스트(R6)는 fake core 기반이라 WP-2 §4에 따라 통합 증거로 세지 않았다.

## 부록 A — T3 id ↔ 테스트 대응 (C04 범위, R1 2026-10-27T18:18Z, 현재 트리)

| T3 | C04 기준 | 테스트 (tests/owner/runner_protocol/…) | 결과 |
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

테스트 이름은 설계 §14(`docs/internal/analysis/REMOTE_PROTOCOL_DESIGN.md:562-604`)의 이름과 일치한다. 누락 0.

## 부록 B — 재실행 출력 발췌 (scratchpad 로그에서 기계적으로 복사)

### c04_rp

```
start 2026-10-27T18:18:08Z cwd=/home/mgjeong/Desktop/etri_hd/mbnas_application
exit=0 dur=119s end=2026-10-27T18:20:07Z
tests/owner/runner_protocol/test_artifact_transfer.py::test_local_transfer_stores_every_registered_file_with_meta PASSED
tests/owner/runner_protocol/test_artifact_transfer.py::test_transfer_failure_never_fails_the_job_and_a_later_retry_completes PASSED
tests/owner/runner_protocol/test_device_used.py::test_search_run_records_raw_metrics_device_used_cpu PASSED
tests/owner/runner_protocol/test_device_used.py::test_explicit_training_run_records_raw_metrics_device_used_cpu PASSED
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
================== 44 passed, 1 warning in 118.00s (0:01:58) ===================
```

### c04_search

```
start 2026-10-27T18:20:28Z cwd=/home/mgjeong/Desktop/etri_hd/mbnas_application
exit=0 dur=32s end=2026-10-27T18:21:00Z
tests/owner/search/test_generation_recovery_independent.py::test_saved_candidate_survives_notification_crash_without_resampling PASSED
tests/owner/search/test_generation_recovery_independent.py::test_completed_retry_replays_without_instantiating_runtime PASSED
tests/owner/search/test_generation_recovery_independent.py::test_duplicate_cap_returns_durable_partial_candidates PASSED
tests/owner/search/test_generation_recovery_independent.py::test_failed_or_unverified_checks_never_accept_candidate[FAIL] PASSED
tests/owner/search/test_generation_recovery_independent.py::test_failed_or_unverified_checks_never_accept_candidate[UNVERIFIED] PASSED
tests/owner/search/test_generation_recovery_independent.py::test_pause_before_next_draw_preserves_completed_candidate PASSED
tests/owner/search/test_generation_recovery_independent.py::test_unavailable_runtime_is_explicit_failure_with_no_passes PASSED
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
======================== 41 passed, 1 warning in 31.27s ========================
```

### c04_sm

```
exit=0 dur=13s
tests/owner/api/test_state_machine.py::test_pause_resume_without_active_jobs PASSED
tests/owner/api/test_state_machine.py::test_pause_waits_for_safe_boundary PASSED
tests/owner/api/test_state_machine.py::test_resume_withdraws_pending_pause PASSED
tests/owner/api/test_state_machine.py::test_cancel_tracks_active_jobs_until_worker_confirms PASSED
tests/owner/api/test_state_machine.py::test_cancel_without_active_jobs_is_immediate PASSED
tests/owner/api/test_state_machine.py::test_completed_run_rejects_transitions PASSED
tests/owner/api/test_state_machine.py::test_resume_blocked_when_dataset_changed PASSED
tests/owner/api/test_state_machine.py::test_resume_blocks_candidate_with_corrupt_full_state PASSED
tests/owner/api/test_state_machine.py::test_transition_unknown_run_is_404 PASSED
======================== 9 passed, 1 warning in 12.93s =========================
```

### c04_det

```
exit=0 dur=44s
tests/owner/orchestration/test_semantics.py::test_evaluated_rows_are_immutable_and_replay_is_idempotent PASSED
tests/owner/orchestration/test_semantics.py::test_runs_are_deterministic_in_structure PASSED
tests/owner/search/test_proposers.py::test_same_seed_reproducible_and_seed_changes_sequence PASSED
======================== 3 passed, 1 warning in 42.15s =========================
```

### c04_pub

```
exit=0 dur=1s
tests/public/contracts/test_runner_protocol.py::test_digest_and_hash_formats PASSED
tests/public/contracts/test_runner_protocol.py::test_fence_fields_are_required_on_job_writes PASSED
============================== 2 passed in 0.41s ===============================
```

### c04_pubrun

```
exit=0 dur=16s
tests/public/runner/test_runner_daemon_unit.py::test_lease_lost_heartbeat_stops_child_and_discards_result PASSED
tests/public/runner/test_runner_daemon_unit.py::test_lease_expiry_watchdog_kills_child_without_renewal PASSED
tests/public/runner/test_runner_daemon_unit.py::test_commit_failed_is_retried_with_the_same_digest PASSED
tests/public/runner/test_runner_daemon_unit.py::test_restart_replay_claimed_without_start_is_discarded_when_the_lease_is_gone PASSED
tests/public/runner/test_runner_daemon_unit.py::test_journal_digest_matches_the_contract_digest_and_stages_advance PASSED
============================== 5 passed in 14.67s ==============================
```

### tree hash

```
before: "sha256":"sha256:634a083bb910a28d1aa183c9a6a2cee66da7c1fc937f01f8aa85d1bb4c4a301d"
after (2026-10-27T18:25:29Z, --digest-only): sha256:634a083bb910a28d1aa183c9a6a2cee66da7c1fc937f01f8aa85d1bb4c4a301d
```

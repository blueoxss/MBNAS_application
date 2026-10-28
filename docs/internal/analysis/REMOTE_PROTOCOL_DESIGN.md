# A3 — 원격 제어면 프로토콜 설계 (`/core/v1/runner/*`)

작성: WP-0 분석 A3 (2026-10-23). 입력: `build/source_snapshot/application/MBNAS_server/src/mbnas_server/` (2026-10-23 21:07 KST 스냅샷). 모든 인용은 `application/MBNAS_server/src/mbnas_server/<file>:<line>` 기준이며, 아래에서는 `<file>:<line>`로 줄여 쓴다. 검증하지 못한 항목은 "확인 못 함"으로 표시했다.

## 0. 결론 요약

1. 오늘의 worker는 **DB를 제어면으로 쓰는 로컬 큐**다: `repo.acquire_lease`(단일 `UPDATE … RETURNING`, repo.py:382-411) → `jobs.start_attempt`(jobs.py:68-73) → heartbeat 스레드의 `repo.renew_lease`(main.py:330-345) → child JSONL을 `_handle_line`이 소비(main.py:389-416) → `_finalize` 1-트랜잭션 정산(main.py:454-472, `_settle_job` 495-537). 이 코드는 모두 "owner_id + 상태"로 fencing된 짧은 SQL이라 **HTTP 엔드포인트 하나 = 기존 함수 1~3개 호출 + 1개 짧은 tx**로 그대로 재사용할 수 있다.
2. 설계 핵심 변경 4가지: (a) worker 프로세스를 `runner`(고객 호스트, HTTP 클라이언트+로컬 journal)와 `core`(엔드포인트, 단일 SQLite 쓰기자)로 분리, (b) `child.load_context`가 읽던 DB 입력(job·run·candidate·checkpoint 행)을 **JobEnvelope**로 직렬화하고 `native_params`/`search_arch`는 **무변경** 재사용, (c) stop 파일은 runner가 heartbeat 응답의 `control` 플래그를 보고 로컬에 materialize(child/contract 무변경), (d) 만료 lease 회수(`Worker.reclaim`, main.py:254-275)를 core 백그라운드 sweeper로 이동.
3. fencing: 모든 쓰기 엔드포인트는 `(job_id, runner_id, attempt)`로 fencing한다. 현행 `renew_lease`/`touch_job`/`finish_job`/`requeue`/`start_attempt`는 `lease_owner`만 검사하고 `attempt`를 검사하지 않는다(repo.py:414-455, jobs.py:68-85). 같은 runner_id가 같은 job을 재-lease(attempt+1)한 뒤 이전 attempt의 지연 요청이 도달하면 오염되므로 **`attempt` 조건 추가가 필요**(§13 "수정" 목록).
4. 멱등 result commit: key=(job_id, attempt), 본문 digest 동일 → 저장 응답 재생, 상이 → 409 `RESULT_CONFLICT`, 처리 중 → 202 + `Retry-After`, lease 상실 → 409 `LEASE_LOST`(오늘의 `_finalize` "lease of job lost; result discarded" 분기 main.py:461-467 + `costs.record_lost_attempt` costs.py:119-144와 동일 의미).
5. 취소 경쟁: 오늘 worker는 `CANCEL_REQUESTED`를 관측했으면 child 결과를 **버리고** `CANCELLED/RUN_CANCELLED`로 대체한다(main.py:373-377, 498-500). 원격에서는 core가 commit 시점의 run 상태로 같은 판정을 내린다 → **result는 거부하지 않고 CANCELLED로 수용**(runner가 journal을 닫을 수 있어야 함). 근거와 대안은 §7.4.
6. 재배치가 필요한 core-로컬 파일 접근 6곳을 찾았다: DD-8 sha 재계산(main.py:418-444), `register_file` 해싱(registry.py:125-163, engine.py:484-503), `record_model_configuration`/`record_native_images`(model_artifacts.py:28-115), `retention._delete`(retention.py:99-109), `state._verify_full_state`(state.py:102-119), `resolve_file`(registry.py:166-190). 각각의 재배치 위치는 §6/§9/§15.
7. DD 충돌·보완 플래그 5건(§15): 학습·평가 핸들러의 `protocol.fitness/promotion` import(DD-A5 위반 후보), PROPOSE_GENERATION이 오늘 GPU worker에서도 lease됨(DD-A4와 불일치), `MIXED_HOOK` 문자열 경로(DD-A2), `params.admission` 노출(DD-A1(a)), protocol 내부 `console.paths` 절대경로(hash 포함)와 runner-로컬 데이터 경로.

---

## 1. 현행 코드 사실 (근거표)

### 1.1 worker 수명주기 (main.py)

| 단계 | 함수 | 핵심 동작 | 근거 |
|---|---|---|---|
| tick | `Worker.tick` | `wait_gpu_clear()` → `self.reclaim()` → `repo.acquire_lease(conn, id, now, lease_ttl_s, kinds, generation_only)` → `execute(job)` | main.py:241-252 |
| reclaim | `Worker.reclaim` | 1 tx: `jobs.reclaim` → RUNNING이었던 것만 `costs.record_attempt(outcome="lost", finished_at=heartbeat_at)` → FAILED면 `engine.job_finished(LEASE_EXPIRED_MAX_ATTEMPTS)`, 아니면 `engine.job_requeued`+`engine.settle_run` → `costs.refresh_run_costs`; tx 밖에서 FAILED 건 `apply_retention` | main.py:254-275, jobs.py:88-94, repo.py:458-475 |
| start | `Worker.execute` | stop 파일 삭제 → `wait_gpu_clear(job)`(미과금) → 1 tx: `jobs.start_attempt`(실패 시 return) → `engine.job_started` → `publish("busy", event_run)` → child spawn → heartbeat 스레드 → `_monitor` → `_finalize` | main.py:300-328 |
| spawn | `Worker._spawn` | env에서 `MBNAS_API_TOKEN`/`MBNAS_ARTIFACT_SIGNING_KEY`/`MBNAS_ENV_FILE` 제거, `child_settings_env`(DB_PATH·OUTPUT_ROOT(S)·DATASET_ROOTS·WEIGHTS_DIR·ETRI_NAS_ROOT·LOG_DIR·WORKER_PYTHON) 주입, `CUDA_VISIBLE_DEVICES`, `MBNAS_STOP_REQUEST_FILE`, `MBNAS_WORKER_ID`; argv `[worker_python, -m, mbnas_server.worker.child, --job-id, id, --db-path, …]`, PDEATHSIG, GPU-lock fd 상속 | main.py:61, 68-74, 287-298 |
| heartbeat | `Worker._heartbeat` | 별도 연결; `heartbeat_s`마다 `repo.renew_lease` 실패 → `live.lease_lost=True` 후 종료; 성공 시 `lease_expires_at` 갱신 + `publish("busy")`(workers 행 upsert, 이벤트 없음) | main.py:330-345 |
| monitor | `Worker._monitor` | 0.2 s 폴링; `lease_lost` → `proc.terminate()`+grace 후 kill; 0.5 s마다 run.status 조회: `CANCEL_REQUESTED`/`stopping` → stop 파일 `{"action":"cancel"}` + terminate; `PAUSE_REQUESTED` → stop 파일 `{"action":"pause"}`; 그 외 상태면 stop 파일 삭제(철회); 1 s 스로틀로 `_progress_event` | main.py:347-387, 62 |
| line | `Worker._handle_line` | `progress`: `generation_summary`면 1 tx `engine.generation_progress`+`touch_job`, 아니면 live.progress 병합; `checkpoint` → `_checkpoint`; `result` → `JobResult` 파싱; `log`/`metric`은 무시(stderr 로그 파일에만 남음) | main.py:389-416 |
| checkpoint | `Worker._checkpoint` | run_dir 내부·실존·크기 일치·kind 허용·candidate 있음 확인 → **파일 sha256 재계산**(DD-8) → 불일치/부재 시 `engine.log(CHECKPOINT_REPORT_REJECTED)`; 통과 시 `repo.insert_checkpoint(path=realpath, sha256=actual)` | main.py:418-444 |
| progress | `Worker._progress_event` | 1 tx: `repo.touch_job` → `engine.job_progress(job, prog)` → `publish("busy", event_run)` | main.py:446-452 |
| finalize | `Worker._finalize` | 1 tx: 현재 행의 `lease_owner==self.id AND status IN (LEASED,RUNNING)`이 아니면 `costs.record_lost_attempt(current|{"attempt": job["attempt"]}, started_at=live.started_at, …)` + 경고 로그("result discarded"); 소유 중이면 `_settle_job`; 공통 `costs.refresh_run_costs` + `publish("idle")`; tx 밖 `apply_retention` | main.py:454-472 |
| settle | `Worker._settle_job` | `cancelled` → 결과를 `CANCELLED/RUN_CANCELLED`로 **대체**; result None → `WORKER_SHUTDOWN`/`CHILD_EXIT_NO_RESULT`(retryable); SUCCEEDED → `finish_job`+`record_attempt(succeeded, inline_timings)`+`job_finished`; BLOCKED → failed 과금+`finish_job(FAILED)`; CANCELLED∧PAUSE_REASONS → `jobs.requeue(pause=True)`+`record_attempt(paused)`+`job_requeued(paused=True)`+`settle_run`; FAILED∧retryable∧(stopping ∨ attempt<max) → `requeue(pause=stopping)`+`job_requeued`+`settle_run`; 나머지 → `finish_job(status)`+`job_finished` | main.py:495-537, 59 |

### 1.2 lease/큐 SQL (repo.py, jobs.py)

| 함수 | fencing 조건 | 비고 |
|---|---|---|
| `acquire_lease(conn, worker_id, now, ttl, kinds, generation_only, runnable_run_statuses)` | `status='QUEUED' OR (status IN (LEASED,RUNNING) AND lease_expires_at < now)`, `attempt < max_attempts`, run.status ∈ RUNNABLE | `attempt = attempt + 1` → attempt가 lease epoch. 단일 문장(autocommit 가능) repo.py:382-411 |
| `renew_lease(conn, job_id, worker_id, now, ttl)` | `lease_owner=? AND status IN (LEASED,RUNNING)` | attempt 미검사 repo.py:414-420 |
| `touch_job(conn, job_id, worker_id, now)` | `lease_owner=? AND status='RUNNING' AND heartbeat_at < now` | lease 연장 없이 생존 증거만 repo.py:423-431 |
| `mark_job_running` | `lease_owner=? AND status='LEASED'`, `started_at=COALESCE(started_at, now)` | worker는 이것 대신 `jobs.start_attempt`(started_at 무조건 갱신, finished_at NULL) 사용 repo.py:434-440, jobs.py:68-73, main.py:306 |
| `finish_job(conn, job_id, worker_id, status, result, error)` | `lease_owner=? AND status IN (LEASED,RUNNING)`; `_require_tx` | repo.py:443-455 |
| `reclaim_expired_leases(conn, now)` | `status IN (LEASED,RUNNING) AND lease_expires_at < now` → QUEUED 또는 FAILED(`attempt>=max_attempts`), error_json에 `lease_owner/attempt/lease_expires_at/reclaimed_at` | repo.py:458-475 |
| `jobs.requeue(conn, job_id, worker_id, error, pause)` | `lease_owner=? AND status IN (LEASED,RUNNING)`; `max_attempts += 1 if pause` | jobs.py:76-85 |
| `cancel_queued_jobs`/`jobs.cancel_job` | QUEUED만 | repo.py:478-483, jobs.py:112-118 |
| `upsert_worker`/`prune_workers`/`list_workers_for_run` | workers 행 = liveness(`worker_stale` = 3·heartbeat_s+5 s) | repo.py:487-528, views.py:39-48, routes_meta.py:21-23 |

### 1.3 child가 DB에서 읽는 것 (child.py)

`load_context(db_path, job_id, stop_path, native=True)` (child.py:113-148)와 `native_params` (71-99), `search_arch` (55-68), `latest_live_full_state` (102-110):

| # | 읽기 | 사용처 | 근거 |
|---|---|---|---|
| D1 | `repo.get_job` → `id, kind, run_id, candidate_id, attempt, spec{params…}` | spec 전체가 `JobSpec`의 뼈대; `spec.params.candidate{baseline_id, init, …}`, `params.stage.epochs_added`, `params.eval_split` | child.py:119, 128, 146; native_params 75, 93-94 |
| D2 | `repo.get_run` → `id, resolved_protocol` | `params.protocol`, `resolved_protocol`, `run_dir(run)`(= `console.paths.output_root/<run id>`), explicit-training 분기 | child.py:122-131; registry.py:53-66; native_params 76-85 |
| D3 | `repo.get_candidate` → `id, arm, family, architecture_yaml, audit.proposal.genotype{yaml, family}` | `arch`(ArchSpec dict, `register_hook=MIXED_HOOK` when mixed) / Baseline이면 None | child.py:127; search_arch 61-68; MIXED_HOOK 52 |
| D4 | `repo.latest_checkpoint(cid, "training_full")` (PRUNED 포함) | generic `resume_from` | child.py:136-140 |
| D5 | `latest_live_full_state` = `list_checkpoints(kind=training_full)`∖PRUNED 중 최대 (epoch, created_at, id) | native `resume_from`(= `live_resume_from`) | child.py:102-110, 143 |
| D6 | `repo.latest_checkpoint(cid, "eval_ema")` → `path` | EVALUATE의 `eval_ckpt`(없으면 `EVAL_CHECKPOINT_MISSING`) | child.py:144-145, 89-92 |
| D7 | 경로 파생: `run_dir`, `resolved_protocol_path`(`run_dir/protocol/resolved.json`), `work_dir`(`run_dir/candidates/<safe cid>` 또는 `run_dir/jobs/<job>`), PROPOSE_GENERATION의 `architecture_dir` | child.py:130-135 |
| D8 | env `MBNAS_STOP_REQUEST_FILE` → `stop_request_path`; `Emitter.stop_requested()`가 파일 존재로 판단 | child.py:184; contract.py:53, 133-144 |
| D9 | Settings(런타임 호스트): `output_roots`(`check_run_dir`), `dataset_roots`(`DatasetService.from_settings`), `weights_dir`, `output_root`(ultralytics cfg, manifests 캐시, registry probe), `etri_nas_root` | training/context.py:59-73, 112-157; datasets/manifest.py:299-304; registry/__init__.py:423-435 |

핸들러가 요구하는 native 파라미터: `TrainJobParams`(run_dir, adapter_id, resolved_protocol, arch, init, device, stop_request_path, evaluate, resume_from, stage_epochs_added; training/handlers.py:51-92), `EvalJobParams`(+ eval_ckpt, eval_split; evaluation/handlers.py:42-61), BENCHMARK = `spec.params` + run_dir(child.py:99).

### 1.4 결과 소비 경로 (engine.py)

`job_finished` (engine.py:430-465): TRAIN_KINDS면 `record_model_configuration`/`record_native_images`(**core 파일시스템 읽기**, model_artifacts.py:34-63, 77-115) → explicit training이면 `training.selection.on_training_result` → PROPOSE_GENERATION 분기 → SUCCEEDED면 `evaluation` 유무로 `_queue_evaluation`/`commit_evaluation`(529-582; `_register_artifacts`(484-503) → `register_file`이 **core 디스크에서 해싱**) → 실패면 `_candidate_failed`(690-701) → `settle_run`(820-852). `settle_run`은 `PAUSE_REQUESTED∧active==0 → PAUSED`, `CANCEL_REQUESTED∧active==0 → cancel_queued_jobs + _cancel_pending_candidates + CANCELLED`, `open==0 → COMPLETED/FAILED`.

---

## 2. 설계 원칙

- P1 **재사용**: repo/jobs/costs/engine 함수를 호출 순서까지 그대로 보존한다. 변경은 (i) fencing에 `attempt` 추가, (ii) 파일시스템 접근을 "등록된 digest/위치" 조회로 치환, (iii) 새 테이블 5개.
- P2 **엔드포인트 = 1 짧은 tx**: `db.core.tx`(BEGIN IMMEDIATE, core.py:133-153) 안에서는 DB 호출만. 네트워크·모델 구축·파일 해싱 금지. 엔벨로프 구성은 lease 문장 **뒤** 읽기 tx.
- P3 **fencing**: 쓰기 요청은 `(job_id, runner_id, attempt)`; `attempt`는 `acquire_lease`가 증가시킨 `jobs.attempt`(= lease epoch, DD-A4).
- P4 **core가 유일한 상태 판정자**: 취소·일시정지·lease 상실·예산 판정은 core가 DB 상태로 내린다. runner는 "관측 보고"만 한다(`cancel_observed`, `started_at` 등).
- P5 **runner는 멱등 재전송자**: 모든 쓰기 요청은 로컬 journal에 먼저 기록 후 전송, 응답을 저장, ack 후 정리. 결과 본문은 한 번 쓰면 불변(digest 고정).
- P6 **DD-A5**: 엔벨로프에는 사용자 resolved protocol 전체 + job spec + candidate 공개 행만. prior/repair/parent pool/admission/search_state는 넣지 않는다.
- P7 **private 계산 제거**: runner는 fitness/gate를 계산하지 않는다(현행 핸들러의 `fitness_and_gate` 호출은 runner 패키지에서 제거; §15 F1).

---

## 3. 엔드포인트

공통: base `https://<core>/core/v1/runner`, `Authorization: Bearer <runner token>`(§11), `X-Request-ID` 에코(auth.py:39-56 재사용), 본문 JSON, 오류 본문은 기존 `Error{code, message, request_id, errors}`(errors.py 재사용). 모든 쓰기 본문은 `runner_id`, 그리고 job 범위 요청은 `attempt`를 포함한다. 토큰에 묶인 `runner_id`와 본문 `runner_id`가 다르면 403 `RUNNER_MISMATCH`.

| # | 메서드/경로 | 목적 | tx 내용(재사용 함수) | 응답 | 오류 |
|---|---|---|---|---|---|
| E1 | `POST /register` | runner 등록·갱신, 호환성 협상 | tx: `runners` upsert(신규), `repo.upsert_worker(status="idle", device…)`(workers 행 = /health liveness) | `{runner_id, core_contract, envelope_version, lease_ttl_s, heartbeat_s, progress_interval_s, max_batch_messages, max_body_bytes, allowed_kinds, artifact_store}` | 409 `VERSION_MISMATCH`(runtime/schema 비호환), 403 |
| E2 | `POST /claim` | 다음 job lease + JobEnvelope | (a) 재전달 검사: `SELECT … FROM jobs WHERE lease_owner=:r AND status='LEASED'` → 있으면 그 envelope 반환(응답 유실 복구, §8 F7). (b) `repo.acquire_lease(conn, runner_id, now, lease_ttl_s, kinds=ALLOWED ∩ requested)`(단일 문장). (c) 읽기 tx(BEGIN)로 run/candidate/checkpoints 조회 → 엔벨로프 구성(§4). | 200 JobEnvelope / 204 없음 | 400 `KIND_NOT_ALLOWED`(DD-A4 밖 kind 요청), 429 `RUNNER_BUSY`(이미 RUNNING 보유, 동시성 1) |
| E3 | `POST /jobs/{job_id}/start` | attempt 시작(과금 시작점) | tx: fence(`lease_owner=:r AND attempt=:a AND status='LEASED'`) → `jobs.start_attempt`(attempt 조건 추가) → `repo.get_job` → `engine.job_started(conn, job, runner_id)` → `publish busy`(`repo.upsert_worker` + `worker.updated`) | `{started_at, lease_expires_at}` | 409 `LEASE_LOST` |
| E4 | `POST /jobs/{job_id}/heartbeat` | lease 연장 + 제어 플래그 | 단일 문장 `repo.renew_lease(+attempt)` → 실패면 409; 성공 시 tx: `repo.upsert_worker(busy, row=device sample+progress)`; 읽기: `run.status` | `{lease_expires_at, control:{action: null|"pause"|"cancel", run_status, stop_requested: bool, prune: [checkpoint_id…]?}}` | 409 `LEASE_LOST` → runner는 child terminate(main.py:367-369와 동일) |
| E5 | `POST /jobs/{job_id}/progress` | 진행 보고(배치) | tx: fence → `repo.touch_job(+attempt)` → `engine.job_progress(conn, job, merged_progress)` → `publish busy` + `worker.updated`. `generation_summary` 페이로드는 거부(owner worker 전용) | `{accepted_seq}` | 409 `LEASE_LOST`, 422 |
| E6 | `POST /jobs/{job_id}/checkpoints` | checkpoint 행 등록 | tx: fence → `repo.insert_checkpoint(path=<runner path>, sha256=<runner-verified>, …)` → `artifact_locations` insert(`object_kind='checkpoint'`) | `{checkpoint_id, location_id, state}` | 409, 422 `CHECKPOINT_REPORT_REJECTED` |
| E7 | `POST /jobs/{job_id}/artifacts` | 평가 artifact 등록(batch) | tx: fence → `repo.insert_artifact(kind, relpath, sha256, bytes, mime, sample_id, split_hash, state=…, meta)` + `artifact_locations` | `[{relpath, artifact_id, state}]` | 409, 422 |
| E8 | `POST /jobs/{job_id}/result` | 결과 commit(멱등) | §7 참조. tx: `idempotent_responses` 조회/삽입 → fence → `_settle_job` 등가 로직(`finish_job`/`jobs.requeue` + `costs.record_attempt` + `engine.job_finished`/`job_requeued`+`settle_run`) 또는 lost 분기(`costs.record_lost_attempt` + `engine.log`) → `costs.refresh_run_costs` → `publish idle` → 응답 저장 | 200 `{outcome, job_status, run_status, response_digest}` | 202 `PROCESSING`, 409 `RESULT_CONFLICT`/`LEASE_LOST`, 500 `COMMIT_FAILED` |
| E9 | `POST /jobs/{job_id}/ack` | 응답 수신 확인 | tx: `idempotent_responses.acked_at` 설정 | 204 | 404 |
| E10 | `POST /deregister` | 정상 종료 | tx: `repo.upsert_worker(status="offline")`, `runners.status='OFFLINE'` | 204 | |

보조(읽기 전용, server scope 포함): `GET /runners`(owner), `GET /jobs/{job_id}`(runner 자신 것만: 재시작 후 상태 확인).

`ALLOWED_KINDS` = `scheduling.jobs.GPU_KINDS`(jobs.py:26-27) = {BASELINE_TRAIN, CANDIDATE_TRAIN, FINAL_RETRAIN, EVALUATE, BENCHMARK} = DD-A4 집합. 서버 측에서 `kinds` 교집합을 강제하고, 요청 집합이 허용 밖 kind를 포함하면 400으로 거부한다(조용히 필터링하지 않음).

`start`를 claim과 분리한 이유: 오늘 lease 후 `wait_gpu_clear(job)`(main.py:304)까지의 대기는 과금되지 않는다("waiting for the device is not billed GPU time"). runner가 device 대기 뒤 `start`를 호출하면 같은 의미가 보존된다. 대안(claim에 즉시 start)은 §16 D2.

---

## 4. JobEnvelope

### 4.1 스키마 (JSON, `envelope_version: 1`)

```json
{
  "envelope_version": 1,
  "job": {
    "job_id": "job-…", "attempt": 2, "kind": "CANDIDATE_TRAIN",
    "lease_expires_at": "2026-…Z", "leased_at": "…Z",
    "spec": { "...JobSpec (schemas/jobs.py:42-74) 전체: job_id, run_id, kind, protocol_hash, candidate_id, rung, cumulative_target, fixed_horizon, arm, search_seed, training_seed, params(§4.3 필터 적용)" },
    "max_attempts": 4, "priority": 100
  },
  "run": {
    "run_id": "run-…", "kind": "SEARCH", "protocol_hash": "sha256:…",
    "resolved_protocol": { "...사용자 resolved protocol 전체 (DD-A5)" },
    "status_at_claim": "RUNNING", "execution_mode": "PILOT"
  },
  "candidate": {
    "candidate_id": "run-….ga0.g1.c003", "name": "…", "arm": "GA", "family": "YOLO26",
    "generation": 1, "search_seed": 0, "training_seed": 123,
    "architecture_hash": "sha256:…", "architecture_yaml": "…", "architecture_path": null,
    "genotype": { "yaml": {...}, "family": "yolo26" },
    "candidate_status": "PROMOTION_ADMITTED", "current_rung": "L1"
  },
  "resume": {
    "recorded_latest": { "id": "ck-…", "path": "<runner path>", "sha256": "…", "epoch": 2, "rung": "L0", "hashes": {...}, "state": "PRESENT" },
    "live_latest": { "...non-PRUNED 최신 training_full 또는 null" },
    "eval_ema": { "id": "ck-…", "path": "<runner path>", "sha256": "…", "epoch": 2 },
    "location": { "runner_id": "runner-A", "store": "runner_local", "uri": null }
  },
  "data_refs": {
    "dataset_yaml": { "protocol_path": "/srv/data/x.yaml", "names": { "0": "person", "...": "" } },
    "train":      { "protocol_root": "/srv/data/train",   "manifest_hash": "sha256:…" },
    "search_val": { "protocol_root": "/srv/data/val",     "manifest_hash": "sha256:…" },
    "final_eval": { "protocol_root": "/srv/data/test",    "manifest_hash": "sha256:…", "allowed": false }
  },
  "versions": {
    "core_contract": "1.4.1", "progress_version": 1, "checkpoint_format": "mbnas.full_state.v1",
    "required_runtime": { "mbnas_model_runtime": ">=0.1,<0.2", "mbnas_contracts": ">=0.1,<0.2" }
  },
  "fence": { "runner_id": "runner-A", "attempt": 2, "claim_id": "clm-…", "issued_at": "…Z" },
  "control": { "lease_ttl_s": 60, "heartbeat_s": 10, "progress_interval_s": 1.0, "kill_grace_s": 10 }
}
```

### 4.2 D1~D9 → 엔벨로프 매핑

| 입력 | 엔벨로프 필드 | runner 측 처리 |
|---|---|---|
| D1 job 행 | `job.spec`(JobSpec 그대로), `job.attempt` | `JobSpec.model_validate({**spec, "attempt": attempt, "params": native})` (child.py:146 동일) |
| D2 run 행 | `run.resolved_protocol`, `run.run_id` | `native_params(job, run, cand, generic, eval_ckpt)`에 `run={"resolved_protocol": …}`로 전달(무변경) |
| D2 run_dir | 없음 — runner가 `<work_root>/runs/<run_id>` 계산(§9) | `generic["run_dir"]`, `resolved_protocol_path`(runner가 `protocol/resolved.json`을 엔벨로프에서 기록) |
| D3 candidate 행 | `candidate.*`; `audit.proposal.genotype`만 `candidate.genotype`으로 추출(감사의 나머지 private 필드는 미전송) | `search_arch({"id", "arm", "family", "architecture_yaml", "audit": {"proposal": {"genotype": …}}})` 어댑터로 기존 함수 호출(무변경) |
| D4 | `resume.recorded_latest`(PRUNED 포함: 핸들러가 거부해야 하므로 state 포함) | generic `resume_from` |
| D5 | `resume.live_latest` | native `resume_from`(= `live_resume_from`) |
| D6 | `resume.eval_ema.path` | `eval_ckpt`; 없으면 child와 같은 `EVAL_CHECKPOINT_MISSING` |
| D7 architecture_dir | 미포함(PROPOSE_GENERATION은 owner worker 전용) | — |
| D8 stop 파일 | `control` 플래그(heartbeat 응답) | runner가 `<work_root>/runs/<run_id>/control/<job_id>.stop`에 `{"action": …, "requested_at": …}` 기록/삭제 → child/contract 무변경 |
| D9 Settings | 엔벨로프 아님: runner 설정(`work_root`, `output_roots=[work_root]`, `dataset_mounts`, `weights_dir`, `etri_root`→vendored) | `check_run_dir(run_dir, settings)`가 runner allowlist로 검사 |

### 4.3 `spec.params` 필터

`_schedule_stage`가 넣는 params(engine.py:363-369): `candidate`(= `_candidate_params`, 341-348), `evaluate_inline`, `admission`(= `admission.admit(...).as_dict()`), `stage{id, epoch_target_total, epochs_added, fitness_weights}`, Baseline은 `baseline` 항목, Final은 `eval_split`, `final`. EVALUATE는 `candidate, training_job_id, training_outputs, stage, eval_split`(468-478).
- 전송: `candidate`, `evaluate_inline`, `stage.id/epoch_target_total/epochs_added`, `baseline`, `eval_split`, `final`, `training_job_id`.
- 제거: `admission`(예산 판정 내부; 내용은 **확인 못 함** — admission.py 미열람, DD-A1(a)에 따라 기본 제거), `stage.fitness_weights`(사용자 protocol의 `rungs[].fitness_weights`와 동일 값이므로 정보 손실 없음; runner는 fitness를 계산하지 않음), `training_outputs`(이전 결과 전체 — EVALUATE 핸들러는 쓰지 않음: `EvalJobParams`에 해당 키 없음, evaluation/handlers.py:42-61).
- `native_params`가 읽는 키는 `candidate.baseline_id/init`, `stage.epochs_added`, `eval_split`, `live_resume_from`, `run_dir`, `stop_request_path`뿐(child.py:75-96)이므로 위 필터로 깨지지 않는다.

### 4.4 ID 노출

엔벨로프는 **내부 id**(`run_id`, `candidate_id`, `job_id`)를 그대로 쓴다. 이유: (i) `candidate_dir(run_dir, spec.candidate_id)`(training/handlers.py:95-99, 135)·`trainer.run_until(spec.candidate_id, …)`·binding 파일·`arch.model_id=cand["id"]`(child.py:67)이 candidate id를 경로와 해시 입력에 쓴다; `canonical_sha256(arch)`(stage_trainer.py:88)에는 `model_id`가 포함되므로 id 치환은 `hashes.architecture`를 바꿀 수 있고 DD-10 교차검증(engine.py:569-570, 595-616)과 충돌할 위험이 있다(`candidates.architecture_hash`의 유도 원천은 **확인 못 함**). (ii) id 형식 `<run>.<ga0>.g<gen>.c<idx>`(engine.py:786)은 UI가 이미 보여주는 arm/세대/순번 정보 외에 private 정보를 담지 않는다. 공개 opaque id는 gateway 투영(WP-4) 전용으로 `public_ids` 테이블에 둔다(§12). → §16 D1.

---

## 5. 시퀀스 (텍스트)

### 5.1 정상 경로

```
runner R                         core C (SQLite 단일 쓰기자)
  |-- POST /register ------------>| tx: runners upsert, upsert_worker(idle)
  |<-- 200 {config} --------------|
  |   [GPU clear 대기: gpu.wait_clear, runner-로컬, 미과금]
  |-- POST /claim {kinds} ------->| acquire_lease(R, now, ttl, kinds∩ALLOWED)   (autocommit 단일문)
  |                               | read tx: get_run, get_candidate, latest_checkpoint×2, list_checkpoints
  |<-- 200 JobEnvelope ----------| journal: jobs/<job>/a<attempt>/envelope.json (fsync)
  |   [runner가 run_dir/protocol/resolved.json, control/ 디렉터리 준비]
  |-- POST /start --------------->| tx: start_attempt(+attempt) → job_started → upsert_worker(busy)+worker.updated
  |<-- 200 {started_at} ---------| journal: started.json
  |   spawn child (argv 리스트, env 위생 main.py:288-292와 동일, MBNAS_DB_PATH 없음, --envelope <path>)
  |-- POST /heartbeat (10 s) ---->| renew_lease(+attempt) ; upsert_worker(busy, sample)
  |<-- 200 {lease_expires_at, control:{action:null}}
  |-- POST /progress (≤1/s 배치)->| tx: touch_job → job_progress → worker.updated
  |   child: checkpoint line → runner가 파일 sha256 재계산(DD-8 위치 재배치, §6.3)
  |-- POST /checkpoints -------->| tx: insert_checkpoint(path=runner path, sha=runner-verified) + artifact_locations
  |<-- 200 {checkpoint_id}
  |   child: result line → runner가 evaluation.artifacts[*]를 해싱·등록
  |-- POST /artifacts (batch) --->| tx: insert_artifact×n + artifact_locations
  |<-- 200 [{relpath, artifact_id}]
  |   journal: result.json (불변, digest 계산)
  |-- POST /result {digest, result, started_at, finished_at, cancel_observed} -->
  |                               | tx: idempotent_responses(job,attempt) 확인 → fence → _settle_job 등가 →
  |                               |     refresh_run_costs → upsert_worker(idle)+worker.updated → 응답 저장
  |<-- 200 {outcome:"SUCCEEDED", response_digest}
  |-- POST /ack ----------------->| tx: acked_at
  |<-- 204                        | runner: journal 항목 정리(결과·엔벨로프는 보존 기간 후 삭제)
```

### 5.2 일시정지 (PAUSE_REQUESTED → PAUSED)

```
user → gateway → core console: state.request_pause (PAUSE_REQUESTED, settle_run)   state.py:58-67
R -- heartbeat -->  C: run.status == PAUSE_REQUESTED → control.action="pause"
R: control/<job>.stop ← {"action":"pause"}  (main.py:378-379와 동일 파일)
child: Emitter.stop_requested() → 다음 epoch 경계에서 full-state 저장 → result CANCELLED/PAUSED_AT_EPOCH
R -- checkpoints(해당 epoch) --> C
R -- result --> C: CANCELLED ∧ reason ∈ PAUSE_REASONS → jobs.requeue(pause=True, max_attempts+1)
                 → record_attempt(paused) → job_requeued(paused=True) → settle_run → PAUSED(active==0)
(철회) user resume while PAUSE_REQUESTED → RUNNING (state.py:162-164) → 다음 heartbeat control.action=null →
R: stop 파일 삭제 (main.py:380-381과 동일)
```

### 5.3 취소

```
user cancel → state.request_cancel: cancel_queued_jobs + CANCEL_REQUESTED          state.py:70-86
R -- heartbeat --> C: control.action="cancel"
R: stop 파일 {"action":"cancel"} 기록 + child SIGTERM, kill_grace_s 후 SIGKILL         (main.py:373-377, 382-383)
R -- result {cancel_observed:true, result:<child 결과 또는 null>} --> C
C: run.status ∈ {CANCEL_REQUESTED, CANCELLED} → JobResult(CANCELLED, RUN_CANCELLED)로 대체(main.py:498-500 등가)
   → record_attempt(cancelled) → finish_job(CANCELLED) → job_finished → _candidate_failed(CANCELLED) → settle_run → CANCELLED
```

### 5.4 lease 상실 (sweeper 회수 후 늦은 결과)

```
R(attempt 1) 네트워크 단절 … lease_expires_at 경과
C sweeper (heartbeat_s 주기): tx: jobs.reclaim → record_attempt(lost, finished_at=heartbeat_at) → job_requeued/settle_run → refresh_run_costs   (main.py:254-275 이동)
R'(attempt 2) claim → 같은 job, resume.location.runner_id = R  (checkpoint affinity, §6.4)
R(복구) -- heartbeat(attempt 1) --> C: renew_lease rowcount 0 → 409 LEASE_LOST → R: child terminate
R -- result(attempt 1) --> C: fence 실패 → costs.record_lost_attempt(job|{"attempt":1}, started_at=<start 응답의 started_at>, finished_at)
   → 기존 failed 행을 실제 종료 시각까지 연장(두 번째 행 없음, 환불 없음) → engine.log("lease lost; result discarded") → 409 LEASE_LOST
R: journal 항목을 'discarded'로 닫고 ack 불필요
```

### 5.5 runner 재시작

```
R 재시작 → journal 스캔:
  envelope.json ∧ ¬started.json   → start 호출 시도(lease 유효하면 계속) / 409면 폐기
  started.json ∧ ¬result.json     → child는 죽었다(PDEATHSIG) → result FAILED/CHILD_EXIT_NO_RESULT(retryable) 전송 (main.py:502-506 등가)
  result.json ∧ ¬response.json    → result 재전송(같은 digest → 200 재생 또는 409 LEASE_LOST)
  response.json ∧ ¬acked          → ack 재전송
```

### 5.6 core 재시작

core 상태는 전부 SQLite에 있다. 진행 중 요청은 연결 오류 → runner 재시도(모든 쓰기는 멱등 또는 fence로 안전). `claim` 응답 유실은 E2(a)의 재전달로 복구(LEASED인 채 `lease_owner=R`인 행을 다시 돌려준다). sweeper는 프로세스 시작 시 1회 즉시 실행.

---

## 6. 트랜잭션 경계와 runner 로컬 영속성

### 6.1 엔드포인트별 tx 내용 (재사용 함수 명시)

| 엔드포인트 | tx 전(읽기/검증) | tx 안(쓰기) | tx 후 |
|---|---|---|---|
| register | 버전 비교 | `runners` upsert; `repo.upsert_worker` | — |
| claim | kinds 교집합 | `repo.acquire_lease`(단일문, autocommit) | 읽기 tx로 엔벨로프 구성 → 응답 |
| start | — | fence SELECT; `jobs.start_attempt(+attempt)`; `engine.job_started`; `repo.upsert_worker`; `emit(worker.updated)` | `bus.notify(run_id)` |
| heartbeat | — | `repo.renew_lease(+attempt)`(단일문); tx: `repo.upsert_worker` | — |
| progress | 배치 병합(`_numeric_training_progress` main.py:597-614 재사용, 서버에서도 재검증) | fence; `repo.touch_job(+attempt)`; `engine.job_progress`; `repo.upsert_worker`; `emit(worker.updated)` | `bus.notify` |
| checkpoints | 스키마 검증(kind ∈ 4종, bytes≥0, sha 64 hex) | fence; `repo.insert_checkpoint`; `artifact_locations` insert | — |
| artifacts | relpath 규칙(`insert_artifact`의 `..`/절대경로 거부 repo.py:573-574 재사용) | fence; `repo.insert_artifact`/`update_artifact`; `artifact_locations` | — |
| result | 본문 크기·스키마(`JobResult`) 검증; digest 계산 | §7의 단일 tx | `bus.notify`; 응답 |
| ack | — | `idempotent_responses` 갱신 | — |
| sweeper(배경) | — | `Worker.reclaim` 본문 그대로(main.py:257-271) + `repo.prune_workers`(224-228) + `runners.status='LOST'` 갱신 | `apply_retention`은 §9.3에 따라 재배치 |

tx 밖으로 빠지는 것: 모델 구축(없음), 파일 해싱(runner로 이동), retention 파일 삭제(runner로 이동), SSE notify.

### 6.2 runner journal (디스크, fsync, 원자적 교체 = `util.atomic` 재사용)

```
<work_root>/journal/<job_id>/a<attempt>/
  envelope.json      claim 응답 원문 (불변)
  started.json       {started_at, lease_expires_at}
  progress.cursor    마지막 accepted_seq
  checkpoints/<n>.json   {report, verified_sha256, response | null}
  artifacts/<n>.json     {batch, response | null}
  result.json        JobResult + started_at + finished_at + cancel_observed + payload_digest (불변)
  response.json      core 200 본문
  acked              빈 파일
  discarded          lease lost 시 사유
```
규칙: 각 단계 파일은 "요청 보내기 전에" 기록한다(outbox). 재시작 처리 순서는 §5.5. `result.json`이 존재하면 본문을 절대 다시 만들지 않는다(digest 고정 → 409 RESULT_CONFLICT 방지).

### 6.3 DD-8 sha 재계산의 재배치

오늘: worker 프로세스(child와 다른 프로세스)가 디스크 바이트를 스트리밍 해싱하여 child 보고값과 대조(main.py:426-434). 원격에서 바이트를 가진 호스트는 runner다. 따라서:
1. **runner 데몬**(child가 아닌 감독 프로세스)이 `_checkpoint`의 검사(run_dir 내부·실존·크기·kind) + `sha256_file` 재계산을 그대로 수행하고(`util.atomic.sha256_file`, `util.paths.is_within` 재사용), 불일치면 core에 **등록하지 않고** `progress` 배치의 `log` 메시지(level error, `CHECKPOINT_REPORT_REJECTED`)로 보고한다. 신뢰경계는 오늘과 동일(감독 프로세스 vs child).
2. **core**는 digest 없는 등록, 64-hex가 아닌 digest, `bytes<0`, 허용 밖 kind를 422로 거부하고(검증 가능한 형식 검사), 행에 `artifact_locations.verified_by='runner:<id>'`를 남긴다.
3. **바이트가 server 측 store로 전송될 때**(DD-A6: 같은 호스트 `runtime/artifacts` 또는 업로드) 수신자가 스트리밍 재계산하여 등록 digest와 대조, 불일치면 `state='LOAD_FAILED'`. 다운로드는 `sha256` 대조 후 제공(DD-A6). 재개 시에는 핸들러가 sidecar·파일을 다시 검증한다(checkpoint.py:213-221, stage_trainer.py:136-160).
결론: "등록 시점 검증(runner)" + "전송 시점 검증(store/server)" + "사용 시점 검증(핸들러)"의 3중 검증. core 자체는 바이트를 보지 않는다.

### 6.4 checkpoint affinity

`resume.location.runner_id`가 claim한 runner와 다르면 runner는 (DD-A6) store에서 digest 검증 후 가져와 `run_dir/candidates/<cid>/checkpoints/` 아래 같은 파일명으로 복원해야 `StageTrainer`의 `expected_resume.path` realpath 비교(stage_trainer.py:140)가 통과한다 → 경로 변환 규칙: 등록 시 `relpath`(run_dir 기준)를 함께 저장하고 복원은 `<my run_dir>/<relpath>`. 선호 배정은 §16 D5(`acquire_lease` ORDER BY 확장 여부).

---

## 7. 멱등성 (result commit)

### 7.1 키와 digest

- key = `(job_id, attempt)`; `attempt`는 엔벨로프의 값.
- `payload_digest` = `util.canon.canonical_sha256`(canon.py:53-55)로 계산한 `{result, started_at, finished_at, cancel_observed, artifact_ids}`의 digest. 서버가 재계산하여 본문 `payload_digest`와 다르면 422.

### 7.2 판정표

| 상태 | 응답 | 근거/동작 |
|---|---|---|
| `idempotent_responses(job,attempt)` 없음 | 신규 처리(7.3) | |
| 있음 ∧ `state='DONE'` ∧ digest 동일 | 200 저장 응답 재생(DB 무변경) | `commit_evaluation`도 자체 재생 보호가 있으나(engine.py:534-536) 과금 행은 없음 → 응답 테이블이 1차 방어 |
| 있음 ∧ digest 상이 | 409 `RESULT_CONFLICT` | 같은 attempt의 두 번째 다른 결과는 버그/변조 → 기록(`engine.log` error) |
| 있음 ∧ `state='PROCESSING'` | 202 `PROCESSING`, `Retry-After: 2` | 7.3의 2-단계 쓰기에서 중간 crash 시 남는 상태; sweeper가 `processing_since > 60 s`면 행 삭제(tx가 롤백되었으므로 재처리 안전) |
| fence 실패(`lease_owner≠R ∨ attempt≠a ∨ status∉{LEASED,RUNNING}`) | 409 `LEASE_LOST` | tx 안에서 `costs.record_lost_attempt(current|{"attempt": a}, started_at=<본문 started_at>, finished_at=<본문>, reason_code="LEASE_LOST", device_uuid)` + `engine.log("lease of … lost; result discarded")` + `costs.refresh_run_costs`(main.py:461-467, 470 등가); 응답도 저장(재전송 시 같은 409 재생) |

`started_at`을 본문에서 받는 이유: 회수 후 다른 runner가 `start_attempt`로 `jobs.started_at`을 덮어쓰므로(jobs.py:71), 오늘 worker가 `live.started_at`을 쓰는 것(main.py:459 주석)과 같은 값을 runner가 `start` 응답에서 보관해 돌려줘야 한다. core는 그 값이 해당 attempt의 lease 구간 안에 있는지(`leased_at ≤ started_at ≤ now`) 범위 검사만 한다.

### 7.3 신규 처리 tx

단일 `tx(conn)`:
1. `INSERT idempotent_responses(job_id, attempt, payload_digest, state='PROCESSING', processing_since)`.
2. fence SELECT(`repo.get_job`); 실패면 7.2의 LEASE_LOST 분기.
3. `cancelled := run.status ∈ {CANCEL_REQUESTED, CANCELLED}` 또는 본문 `cancel_observed` (§7.4). `stopping := 본문 runner_shutting_down`(오늘 `self.stopping`의 `WORKER_SHUTDOWN`/pause 재큐 의미, main.py:502, 531-532).
4. `_settle_job`(main.py:495-537)을 `core.runner.settle_job(conn, job, result, exit_code, cancelled, stopping, started, finished, device_uuid)`로 **이식**(본문 동일; `self.id`→runner_id, `self.device["uuid"]`→본문 device_uuid, `self.stopping`→stopping). 내부 호출: `repo.finish_job(+attempt)`, `jobs.requeue(+attempt)`, `costs.record_attempt`, `engine.job_finished`, `engine.job_requeued`, `engine.settle_run`, `_inline_timings`(main.py:540-548).
5. `costs.refresh_run_costs(conn, run_id)`; `repo.upsert_worker(idle)` + `worker.updated`.
6. 응답 본문 생성 → `UPDATE idempotent_responses SET state='DONE', response_json, response_digest`.
7. COMMIT. 예외 시 전체 롤백(1단계 행도 사라짐) → 500 `COMMIT_FAILED`(runner 백오프 재시도).

### 7.4 취소된 run의 result: 수용(CANCELLED)으로 결정

근거: (i) 오늘 `_settle_job`은 `cancelled`면 child의 결과 내용과 무관하게 `CANCELLED/RUN_CANCELLED`로 **대체**하고 `finish_job(CANCELLED)` → `job_finished` → `_candidate_failed`(status CANCELLED, engine.py:694-695) → `settle_run`이 `active==0`일 때 run을 CANCELLED로 닫는다(820-836). (ii) `settle_run`의 CANCELLED 전이는 "active==0"을 요구하므로 **runner의 result가 와야** run이 닫힌다 — result를 거부(4xx)하면 runner는 재시도하고 run은 CANCEL_REQUESTED에 머문다. (iii) 거부 시 runner journal을 닫을 방법이 없다. 따라서 200 `{outcome:"CANCELLED"}`로 수용한다.
오늘과의 차이: 오늘은 worker가 0.5 s 폴링으로 CANCEL_REQUESTED를 **보기 전에** child가 끝나면 SUCCEEDED가 그대로 커밋될 수 있다(경쟁에 따라 비결정). 원격에서는 core가 commit 시점 run 상태로 판정하므로 결정적이다. 선택지(§16 D3): (A) 권고 — core 판정(결정적); runner가 보낸 원 결과는 `jobs.error_json.discarded_result`에 보존(감사). (B) 오늘과 동일하게 `cancel_observed`만 기준(비결정 유지).

---

## 8. 실패 매트릭스

| # | 시나리오 | 오늘의 처리 | 원격 설계 | 관측 가능한 결과 |
|---|---|---|---|---|
| F1 | result 응답 유실(commit 후) | 해당 없음(로컬 tx) | runner 재전송 → 7.2 재생 | job 상태·ledger·evaluation 행 변화 없음, 응답 동일 |
| F2 | result 중복(다른 digest) | 해당 없음 | 409 RESULT_CONFLICT + error 로그 | DB 무변경 |
| F3 | runner 재시작(child 사망, 결과 없음) | `CHILD_EXIT_NO_RESULT` retryable → requeue(main.py:502-506, 531-535) | journal 재생: result FAILED/CHILD_EXIT_NO_RESULT(retryable, exit_code null) 전송 → 동일 분기 | job QUEUED(attempt 유지), ledger `failed/CHILD_EXIT_NO_RESULT`, candidate QUEUED; 다음 attempt는 등록된 checkpoints로 재개(epoch 재학습 없음: test_subprocess.py:99-100과 동일 단언) |
| F4 | runner 재시작(result.json 있음) | — | result 재전송 → 200 재생 또는 최초 commit | 1회 commit |
| F5 | core 재시작(요청 중) | — | 연결 오류 → 재시도; tx 원자성(core.py:147-153) | 반쯤 쓰인 상태 없음 |
| F6 | commit 실패(예외) | tx 롤백 → worker 예외 전파 | 500 COMMIT_FAILED, idempotent 행 없음 → 재시도 | 재시도 성공 시 1회 commit |
| F7 | claim 응답 유실 | — | 재-claim → E2(a) 재전달(동일 LEASED 행) | 같은 job_id/attempt 엔벨로프; TTL 만료로 인한 지연 없음 |
| F8 | stale lease 회수 | worker `reclaim`(모든 worker가 호출) | core sweeper(heartbeat_s 주기, 시작 시 1회) — 본문 동일 | QUEUED/FAILED + `failed/lost` 행(started_at→heartbeat_at: touch_job 근거 repo.py:426-427) |
| F9 | 회수 후 늦은 heartbeat | `renew_lease` False → child terminate(main.py:335-338, 367-369) | 409 LEASE_LOST → 동일 | child 종료, 이후 result는 F10 |
| F10 | 회수 후 늦은 result(실행 결과 불명·성공 포함) | lost 분기: `record_lost_attempt`가 기존 failed 행을 실제 종료 시각까지 **연장**, 결과 폐기(main.py:461-467; costs.py:119-144 "never shorter", "instead of appending a second row") | 7.2 LEASE_LOST | 자동 환불·재과금 없음; 행 1개, `meta.extended_by='owner_lost_lease_finalize'`; 성공한 결과라도 폐기(다음 attempt가 `retry` 행으로 과금되며 checkpoints는 재사용됨) |
| F11 | 취소 경쟁 | §7.4 | §7.4 | job CANCELLED, ledger `failed/RUN_CANCELLED`, run CANCELLED, `cancel.state='CANCELLED'`(test_subprocess.py:189-194 단언 재사용) |
| F12 | pause | stop 파일 → `PAUSED_AT_EPOCH` → `requeue(pause=True)`(max_attempts+1) → run PAUSED | heartbeat `control.action="pause"` → 동일 | job QUEUED, `error.reason_code=PAUSED_AT_EPOCH`, `max_attempts+1`, candidate PAUSED, run PAUSED, ledger `train/paused`(test_subprocess.py:150-169) |
| F13 | pause 철회 | run≠PAUSE_REQUESTED면 stop 파일 삭제(main.py:380-381) | `control.action=null` → runner 삭제 | child 계속, 결과 SUCCEEDED |
| F14 | runner 정상 종료(SIGTERM) 중 job | `stopping` → stop 파일(cancel) + terminate → `WORKER_SHUTDOWN` retryable → `requeue(pause=True)`(재시도 미소모) | runner가 `runner_shutting_down=true`로 result 전송 → 동일 분기 | job QUEUED, max_attempts+1, ledger `failed/WORKER_SHUTDOWN` |
| F15 | lease TTL 내 heartbeat 불가(네트워크 분할) | — | runner는 `lease_expires_at` 로컬 시계로 감시; 만료 전 갱신 실패가 지속되면 자체적으로 child 중지(선택: `kill_on_lease_expiry`, 기본 on) → F10 경로 | 분할 동안 GPU 낭비 상한 = lease_ttl_s |
| F16 | 같은 runner_id가 같은 job 재-lease(attempt+1) 뒤 이전 attempt 요청 도달 | 발생 불가(순차 1 job) | fence에 `attempt` 포함 → 409 | DB 무오염 (§13 수정 R1) |
| F17 | checkpoint 보고 digest 불일치 | 등록 거부 + error 로그 | runner에서 동일, core는 형식 검증 | `CHECKPOINT_REPORT_REJECTED` 이벤트, checkpoints 행 없음 |
| F18 | 허용 밖 kind claim | cpu/gpu 플래그로 분리 | 400 KIND_NOT_ALLOWED | PROPOSE_GENERATION은 owner worker만 lease |
| F19 | 토큰 만료/회수/다른 runner_id | — | 401/403 | 모든 쓰기 거부; runner는 재등록 요구 |
| F20 | result 본문 > `max_request_body_bytes` | 1 MiB 413(limits.py:16-61) | runner 라우트 한도 상향 또는 대용량 필드 artifact화(§16 D6) | 413 PAYLOAD_TOO_LARGE |

---

## 9. runner 작업 디렉터리 vs core run_dir

### 9.1 오늘

`artifacts.registry.run_dir(run)` = `resolved_protocol.console.paths.output_root / run_id`(registry.py:53-66). 이 값은 (a) child의 `run_dir`/`work_dir`(child.py:130-132), (b) worker의 stop/log 경로(main.py:278-285), (c) `register_file`/`resolve_file`의 기준(registry.py:147, 181-187), (d) retention 삭제 경계(retention.py:132), (e) owner의 `architectures/` journal(engine.py:984-985)에 공통으로 쓰인다. `console.paths.*`는 protocol_hash에 포함된다(RESOLUTION_HASH_EXCLUDED에 없음: validator.py:71, 172-182).

### 9.2 설계

| 의미 | core | runner |
|---|---|---|
| `run_dir` | `<core output_root>/<run_id>` 불변(owner worker, protocol/resolved.json, architectures/, logs/) | `<runner work_root>/runs/<run_id>`; 엔벨로프에는 **경로 없음**(runner가 계산), `check_run_dir`은 runner `output_roots=[work_root]`로 검사 |
| `candidates/<cid>/…` | 없음(파일은 runner에) | 핸들러가 그대로 생성(checkpoints/, eval/, native/, architecture.yaml) |
| `checkpoints.path` | runner 절대경로 저장 + `artifact_locations.runner_id/relpath` | sidecar와 함께 보존 |
| `artifacts.relpath` | run-상대경로(오늘과 동일 규칙, repo.py:573-574) | 같은 relpath |
| `protocol/resolved.json` | core가 기록 | runner가 엔벨로프에서 기록(`resolved_protocol_path`) |
| `logs/<job>.attempt<n>.stderr.log` | — | runner가 기록; 종료 시 artifact(kind other)로 등록(선택) |
| `control/<job>.stop` | — | runner가 heartbeat 플래그로 기록/삭제 |

엔벨로프의 `run_dir`은 따라서 "runner-로컬 실행 루트"를 뜻하며 core의 `run_dir`과 **다른 호스트의 다른 경로**다. 두 쪽을 잇는 키는 `(run_id, relpath)`다.

### 9.3 재배치 대상 (core가 파일을 만지는 곳)

| 함수 | 오늘 | 설계 |
|---|---|---|
| `engine._register_artifacts` → `register_file` (engine.py:484-503, registry.py:125-163) | 결과의 `evaluation.artifacts[*].path`를 core 디스크에서 해싱·등록 | runner가 E7로 선등록(sha256은 runner 데몬 재계산) → 결과 항목에 `artifact_id` 포함 → `_register_artifacts`는 `repo.get_artifact(run_id, artifact_id)`로 행을 찾아 view 구성(해싱 없음). 미등록 항목은 `problems`에 기록(오늘의 실패 처리 경로 재사용, 496-497) |
| `job_finished`의 `record_model_configuration`/`record_native_images` (engine.py:433-440) | `candidates/<cid>/architecture.yaml` 읽어 `architecture_yaml/path`·`audit.execution_configuration` 갱신, native 이미지 등록 | runner가 결과 `outputs.execution_configuration={yaml_text, yaml_hash, sha256, class_count, artifact_id}`와 native 이미지 artifact 등록(E7, 동일 meta 규칙 model_artifacts.py:105-112)을 보고 → core는 DB 갱신만(새 함수 `record_model_configuration_from_report`) |
| `retention.apply_for_candidate._delete` (retention.py:99-109) | core가 파일 unlink | `plan()`(74-86, 순수 함수)은 core가 계산 → heartbeat `control.prune=[checkpoint_id…]` 또는 전용 `GET /runner/prune-requests` → runner가 삭제 후 `POST /checkpoints/{id}/pruned {deleted, bytes}` → core `repo.mark_checkpoint_pruned`. `reconcile_trainer_prunes`(153-169)의 `os.path.exists`도 runner 보고(`checkpoint` 메시지에 `pruned_paths`)로 대체 |
| `state._verify_full_state` (state.py:102-119) | resume 시 파일 존재·크기·sha 검사 | 기록 수준 검사(행 존재·PRUNED·protocol hash)로 축소 + runner 측 검증은 핸들러가 수행(`ResumeBlocked` → `RESUME_BLOCKED`, engine.py:52, 699). 선택: 같은 호스트 store에 사본이 있으면 server가 검사 |
| `resolve_file` (registry.py:166-190) | `run_dir/relpath` 실파일 | `artifact_locations`로 store 경로 또는 relay(WP-5) |
| `_recover_generation`/generation journal (engine.py:973-993, generation.py:103-125) | core 로컬 | 변경 없음(owner worker) |

---

## 10. owner worker와 structure-check 격리

### 10.1 owner worker (PROPOSE_GENERATION)

변경 없음: `python -m mbnas_server.worker --cpu` 경로가 DB에 직접 `acquire_lease`한다(main.py:246-247). 단, 오늘 `cpu` 플래그는 `generation_only=True`만 lease하므로 평가가 있는 search run의 PROPOSE_GENERATION(GA/Random, `params.generation_only` 없음)은 **GPU worker가 child에서 실행**한다. DD-A4(PROPOSE_GENERATION은 owner worker 전용)를 지키려면 owner worker를 `WorkerConfig(cpu=True, kinds=["PROPOSE_GENERATION"])` + `generation_only=None`으로 기동해야 한다 → `tick()`의 `generation_only=True if self.cfg.cpu else None` 결합을 설정으로 분리(§13 수정 R6). GA parent pool(`_parent_pool`, engine.py:320-337)과 `previous_search_state`는 core DB에만 있으므로 private 경계가 유지된다. journal(`architectures/generation-<seed>.json`, flock)과 `_recover_generation`은 core 파일시스템에서 그대로 동작한다.

### 10.2 structure-check 격리

오늘: `POST /runs/{run}/candidates/{cid}/structure-check`(routes_runs.py:315-333)가 API 프로세스 안에서 `model_services.run_structure_check`(243-285) → `check_structure`(structure.py:336) → `inspect_genotype`(genotype.py:446; `import torch` 452, `torch.no_grad()` 482 하에 ultralytics `parse_model` 빌드 + CPU forward)를 실행한다. 임의 YAML로 메모리·시간을 소모하거나 프로세스를 죽일 수 있고, ETRI mixed 블록은 `load_etri_mixed`가 `sys.path`를 조작한다(etri_bridge.py:131-142; DD-A2로 vendoring 예정).

설계 `mbnas_core.structure.sandbox`:
```
run_check(request) -> dict
  request = {"yaml": <dict>, "input_size": int, "class_count": int|null, "mixed": bool, "schema": "mbnas-structure/…"}
  argv  = [sys.executable, "-I", "-m", "mbnas_core.structure.check_worker"]        # 셸 없음, -I(사용자 site/PYTHONPATH 무시)
  stdin = canonical JSON(request) ; stdout = JSON(result) ; stderr = 로그(크기 상한)
  env   = {"PATH", "LANG", "HOME": <빈 tmp>, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2",
           "CUDA_VISIBLE_DEVICES": "", "YOLO_CONFIG_DIR": <tmp>, "PYTHONDONTWRITEBYTECODE": "1"}   # MBNAS_* 비전달
  preexec_fn: setrlimit(RLIMIT_CPU, (cpu_s, cpu_s+5)), setrlimit(RLIMIT_AS, as_bytes), setrlimit(RLIMIT_NOFILE, (256,256)),
              setrlimit(RLIMIT_FSIZE, (64 MiB, …)), setrlimit(RLIMIT_CORE, (0,0)), os.setsid(), prctl(PDEATHSIG, SIGKILL)
  cwd   = 빈 tmp 디렉터리(읽기 전용 마운트가 가능하면 사용) ; 네트워크: `unshare -rn` 래퍼가 있으면 사용(없으면 결과에 "network_isolation": "none" 기록)
  timeout = wall_s → 초과 시 프로세스 그룹 SIGKILL ; 결과 JSON ≤ 1 MiB, pydantic 검증
  실패 매핑: timeout → status UNVERIFIED, code STRUCTURE_CHECK_TIMEOUT ; 비정상 종료 → STRUCTURE_RUNTIME_UNAVAILABLE ; 메모리 → STRUCTURE_CHECK_RESOURCE_LIMIT
  동시성: core 전역 세마포어(기본 1)
```
기본값 제안: `wall_s=120`, `cpu_s=100`, `as_bytes=16 GiB`. torch+ultralytics import의 가상메모리 요구치는 **확인 못 함**(CUDA 라이브러리 매핑 때문에 RLIMIT_AS를 낮게 잡으면 import가 실패할 수 있음) → 설치 검증 단계에서 측정해 설정값으로 둔다. `unshare -rn` 가용성은 개발 호스트에서만 확인됨(TASK_STATE.md), 고객 호스트는 **확인 못 함**. `run_structure_check`의 나머지(저장된 proof 비교, `_archive_structure`, `audit.structure` 갱신; routes_runs.py:297-312, 326-331)는 API 프로세스에 남고 "모델 구축" 부분만 서브프로세스로 간다. registry/user-model 검사(`check_registry_model`, `check_user_model` model_services.py:435-467, 492-523)도 같은 sandbox를 쓴다.

---

## 11. 인증·전송 보안

### 11.1 principal/토큰

- 테이블 `principals`(§12): `kind ∈ {server, runner, admin}`, `runner_id`(runner만, 필수), `token_prefix`(조회용 8자), `token_hash`(sha256(secret) — 32바이트 무작위 secret이므로 pepper 없이도 역산 불가; 저장은 hex), `scopes_json`, `expires_at`, `revoked_at`, `rotated_from`, `last_used_at`.
- 토큰 형식 `mbnas_<kind>_<prefix>_<secret>`; 발급은 owner CLI(`mbnas-core principal create --kind runner --runner-id R1 --ttl-days 90`)가 1회 출력, DB에는 hash만.
- 검증(`core.auth.require_principal(scope)`): 헤더 파싱은 auth.py:22-31 재사용 → prefix로 1행 조회 → `hmac.compare_digest(sha256(secret), token_hash)` → `revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now)` → scope 확인 → runner면 경로/본문 `runner_id == principal.runner_id`(불일치 403 `RUNNER_MISMATCH`) → `last_used_at` 갱신(분 단위 스로틀, 별도 짧은 tx).
- scope 매핑: `/core/v1/runner/*` → runner(또는 admin), `/core/v1/console/*` → server, `/core/v1/owner/*` → admin.
- 회전: `principal rotate <id>` → 새 행(`rotated_from`) 발급, 이전 행 `expires_at = now + grace(기본 15 min)`; runner는 401을 받으면 설정 파일의 새 토큰으로 재등록.
- 만료 기본: runner 90일, server 365일, admin 24시간. 실패 응답은 토큰 존재 여부를 구분하지 않음(401 `AUTH_REQUIRED` / 403 `ACCESS_DENIED`만, auth.py와 동일 코드).
- 로그: `install_log_redaction`(app.py:41) 재사용으로 Authorization/`token` 쿼리 마스킹.

### 11.2 TLS

- core 기본 바인드 `127.0.0.1`(config.py:58 계승; DD-A7 8700). 같은 호스트(server·runner 동거)에서는 loopback + 토큰.
- 다중 호스트: core는 TLS 종단(uvicorn `--ssl-certfile/--ssl-keyfile` 또는 리버스 프록시), runner 설정 `core_url`이 `https://`가 아니면 기동 거부(예외: `127.0.0.1`/UDS). runner는 `ca_file`(운영자 배포 CA 또는 핀) 로 서버 인증서 검증, 호스트명 검증 필수, TLS ≥ 1.2. 선택: mTLS(`client_cert`), 그 경우에도 bearer는 유지(인가는 토큰).
- 클럭: `lease_expires_at`은 core 시계 기준 ISO 문자열(util/time.py). runner는 응답의 `server_time`과 로컬 시계 차를 계산해 만료 감시(F15)에 보정한다.

---

## 12. 신규 DB 테이블 (DDL 초안, schema v4 → `db.core.migrate` 추가 컬럼/테이블 방식 유지)

```sql
CREATE TABLE IF NOT EXISTS runners (
  id TEXT PRIMARY KEY,                       -- runner_id (토큰에 바인딩)
  status TEXT NOT NULL CHECK (status IN ('REGISTERED','ONLINE','LOST','OFFLINE')),
  host TEXT, device TEXT, device_uuid TEXT, device_name TEXT,
  kinds_json TEXT NOT NULL DEFAULT '[]',     -- 요청 kinds ∩ ALLOWED_KINDS
  runtime_json TEXT NOT NULL DEFAULT '{}',   -- {mbnas_runner, mbnas_model_runtime, torch, cuda, python, envelope_version}
  data_mounts_json TEXT NOT NULL DEFAULT '{}', -- 표시용: {protocol_root: present(bool)} (경로 자체는 저장하지 않음)
  registered_at TEXT NOT NULL, last_seen_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
-- workers 테이블은 그대로 둔다(/health·snapshot liveness, repo.upsert_worker 재사용); workers.id = runner_id.

CREATE TABLE IF NOT EXISTS principals (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('server','runner','admin')),
  runner_id TEXT REFERENCES runners(id),
  token_prefix TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
  scopes_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, expires_at TEXT, revoked_at TEXT, rotated_from TEXT, last_used_at TEXT,
  CHECK (kind != 'runner' OR runner_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS principals_prefix ON principals(token_prefix);

CREATE TABLE IF NOT EXISTS idempotent_responses (
  job_id TEXT NOT NULL REFERENCES jobs(id),
  attempt INTEGER NOT NULL CHECK (attempt >= 1),
  runner_id TEXT NOT NULL,
  payload_digest TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PROCESSING','DONE')),
  status_code INTEGER, response_json TEXT, response_digest TEXT,
  processing_since TEXT NOT NULL, committed_at TEXT, acked_at TEXT,
  PRIMARY KEY (job_id, attempt)
);

CREATE TABLE IF NOT EXISTS artifact_locations (
  id TEXT PRIMARY KEY,
  object_kind TEXT NOT NULL CHECK (object_kind IN ('artifact','checkpoint')),
  object_id TEXT NOT NULL,                   -- artifacts.id 또는 checkpoints.id
  run_id TEXT NOT NULL REFERENCES runs(id),
  runner_id TEXT,                            -- 바이트를 가진 runner(없으면 store 전용)
  store TEXT NOT NULL CHECK (store IN ('runner_local','server_store','core_local')),
  relpath TEXT NOT NULL,                     -- run_dir 기준 상대경로(양쪽 공통 키)
  uri TEXT,                                  -- store 경로/URL(runner_local이면 runner 절대경로)
  sha256 TEXT NOT NULL, bytes INTEGER NOT NULL CHECK (bytes >= 0),
  verified_by TEXT NOT NULL,                 -- 'runner:<id>' | 'store' | 'server'
  verified_at TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('PRESENT','TRANSFERRING','PRUNED','MISSING','LOAD_FAILED')),
  job_id TEXT, attempt INTEGER,              -- 등록한 attempt(감사·fence)
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS artifact_locations_object ON artifact_locations(object_kind, object_id);
CREATE INDEX IF NOT EXISTS artifact_locations_run ON artifact_locations(run_id, relpath);

CREATE TABLE IF NOT EXISTS public_ids (
  public_id TEXT PRIMARY KEY,                -- 무작위 128-bit urlsafe
  kind TEXT NOT NULL CHECK (kind IN ('run','candidate','job','evaluation','artifact','checkpoint')),
  internal_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (kind, internal_id)
);
```
- `jobs`에 추가 컬럼 없음: `lease_owner`=runner_id, `attempt`=epoch으로 충분. `checkpoints.path`는 의미가 "등록 runner의 절대경로"로 바뀌며 `artifact_locations`가 권위.
- `events.type`에 새 타입 없음(`worker.updated`/`log` 재사용).

---

## 13. 재사용 목록

### 13.1 무변경 재사용
`repo.acquire_lease`, `repo.reclaim_expired_leases`, `repo.cancel_queued_jobs`, `repo.upsert_worker/get_worker/list_workers*/prune_workers`, `repo.append_event`, `repo.insert_checkpoint/list_checkpoints/latest_checkpoint/mark_checkpoint_pruned`, `repo.insert_artifact/update_artifact/get_artifact`, `repo.append_cost/find_cost/update_cost/aggregate_costs`; `jobs.ensure_job/job_key/reclaim/count_jobs/open_jobs/cancel_job/gpus_for/GPU_KINDS`; `costs.record_attempt/record_lost_attempt/refresh_run_costs/ledger_kind/job_arm`; `engine.job_started/job_progress/job_requeued/settle_run/generation_progress/_recover_generation/commit_evaluation(본체)/_candidate_failed/_queue_evaluation/log/set_run_status`; `state.request_pause/request_cancel/request_resume`; `events.bus.emit/EventBus`; `db.core.connect/tx/migrate`; `util.time/canon/atomic/paths/log_redact`; `schemas.jobs.JobSpec/JobResult/ProgressMessage`; `worker.contract` 전체(Emitter·stop 파일·핸들러 해석); `worker.child.native_params/search_arch/run_job/_protocol_stream/latest_live_full_state(입력을 행 리스트로 받는 형태로)`; `worker.main._numeric_training_progress/_inline_timings/_pump/_die_with_parent/child_settings_env(DB_PATH 제외)`; `worker.gpu/gpu_guard`; `views.worker_payload/worker_stale/run_payload`; `api.auth.RequestIdMiddleware`, `api.limits.BodyLimitMiddleware`, `api.errors`, `api.deps.db/contract_json`.

### 13.2 수정
| ID | 함수 | 수정 |
|---|---|---|
| R1 | `repo.renew_lease`, `repo.touch_job`, `repo.finish_job`, `repo.mark_job_running`, `jobs.start_attempt`, `jobs.requeue` | 선택 인자 `attempt: int | None`; 주어지면 `AND attempt = :attempt` 추가(기존 호출은 None으로 동작 동일) |
| R2 | `repo.acquire_lease` | 기존 시그니처 유지; (선택, D5) `prefer_owner` 정렬 추가 |
| R3 | `engine._register_artifacts` | 항목에 `artifact_id`가 있으면 `repo.get_artifact`로 조회(해싱 없음); 없으면 오늘 경로(core_local) 유지 → owner worker 호환 |
| R4 | `engine.job_finished` TRAIN_KINDS 분기(433-440) | `record_model_configuration/record_native_images` 호출을 "결과 보고 기반" 함수로 분기(runner 결과에 `execution_configuration`이 있으면 DB 갱신만) |
| R5 | `worker.main.Worker._settle_job` | `core.runner.settle_job(conn, job, result, exit_code, cancelled, stopping, started, finished, runner_id, device_uuid)`로 추출(본문 동일); `Worker._settle_job`은 이를 호출 |
| R6 | `worker.main.Worker.tick` | `generation_only` 필터를 `WorkerConfig.generation_only: bool | None`로 분리(owner worker가 PROPOSE_GENERATION 전부 lease) |
| R7 | `worker.child.load_context` | DB 읽기 대신 `--envelope <path>` 로더(`load_context_from_envelope`)를 추가; 기존 DB 로더는 owner worker용으로 유지 |
| R8 | `worker.child.MIXED_HOOK` 및 `search_arch`의 `yaml_uses_mixed_modules` import | `mbnas_model_runtime.operators:ensure_mixed_registered` 경로로 변경(DD-A2) |
| R9 | `training.context.split_manifests`, `load_dataset_yaml` 호출 | `data_refs` 재매핑 훅(`protocol_root → runner_root`; names는 엔벨로프 값 우선) — split_hash는 위치 독립(manifest.py:14-15, 244-245)이라 frozen hash 검사 유지 |
| R10 | `training.handlers._evaluate`, `evaluation.handlers.run_evaluation_job` | `fitness_and_gate` 호출 제거(또는 core 전용 플러그인으로 분리) — §15 F1 |
| R11 | `retention.apply_for_candidate` | `_delete`를 "prune 요청 생성"으로 치환, 실제 삭제는 runner 보고 후 `mark_checkpoint_pruned` |
| R12 | `state._verify_full_state` | 파일 검사 → 기록 검사 + store 검사(있을 때) |
| R13 | `artifacts.registry.resolve_file` | `artifact_locations` 경유(WP-5) |
| R14 | `db.core.SCHEMA_VERSION/migrate` | v4: 신규 테이블 생성(additive) |

### 13.3 신규
`mbnas_core.api.runner_routes`(E1~E10), `mbnas_core.runner.envelope`(빌더·pydantic 모델), `mbnas_core.runner.fence`(공통 fence SELECT), `mbnas_core.runner.sweeper`(reclaim+prune_workers+runners LOST+PROCESSING 고아 정리; 스레드, 주기 `heartbeat_s`), `mbnas_core.auth.principals`(발급·검증·회전 CLI), `mbnas_core.structure.sandbox/check_worker`, `mbnas_runner.daemon`(claim 루프, heartbeat 스레드, progress 배치, journal, child spawn — `Worker.execute/_monitor/_handle_line` 구조를 HTTP 호출로 치환한 이식), `mbnas_runner.client`(httpx 0.28.1 이미 의존성: pyproject.toml:24), `mbnas_runner.journal`, `mbnas_runner.settings`(`core_url, token_file, runner_id, work_root, dataset_mounts, weights_dir, ca_file, kinds, gpu_index`).

---

## 14. T3 테스트 매트릭스

기존 픽스처 재사용: `harness.make_inline_worker`(harness.py:98), `harness.drain`(133), `stub_handlers`(MBNAS_STUB_HANG_FILE/CRASH_ONCE/EPOCH_S), `api_settings`(lease_ttl_s=30, heartbeat_s=1). 신규 픽스처: `core_app`(runner 라우트 포함 FastAPI TestClient), `runner_sim`(HTTP로 claim/start/heartbeat/… 를 호출하는 인프로세스 시뮬레이터; child 대신 stub 핸들러를 직접 호출), `sweeper(now=…)`(시각 주입).

| # | 시나리오 | 테스트 이름 | 기대 관측치 |
|---|---|---|---|
| T3-01 | 두 runner 동시 claim | `test_two_runners_never_claim_the_same_job`(test_lease.py:21 이식) | 교집합 0, 전부 attempt 1 LEASED |
| T3-02 | 허용 밖 kind | `test_claim_rejects_kind_outside_allowlist` | 400 KIND_NOT_ALLOWED; PROPOSE_GENERATION은 owner worker `tick()`이 lease |
| T3-03 | claim 응답 유실 | `test_claim_retry_returns_same_leased_job_without_new_attempt` | 두 번째 claim = 같은 job_id/attempt, `jobs.attempt` 불변 |
| T3-04 | 엔벨로프 동치 | `test_envelope_native_params_equal_child_load_context` | 같은 DB 상태에서 `child.load_context(native=True).params` == `load_context_from_envelope(...).params`(run_dir·stop_request_path 치환 후) |
| T3-05 | start 전 과금 없음 | `test_lease_without_start_is_not_billed_after_sweep`(test_lease.py:86 이식) | ledger 비어 있음, job QUEUED |
| T3-06 | heartbeat 제어 플래그 | `test_heartbeat_returns_pause_then_null_after_withdrawal` | PAUSE_REQUESTED→`action=="pause"`, resume→`null`; stop 파일 생성/삭제 |
| T3-07 | pause 완주 | `test_pause_at_epoch_boundary_then_resume_over_http`(test_subprocess.py:141 이식) | job QUEUED/`PAUSED_AT_EPOCH`, `max_attempts==4`, run PAUSED, resume 후 attempt 2 SUCCEEDED, epochs 1..6 각 1회, ledger `[("train","paused"),("train","succeeded")]` |
| T3-08 | 취소 | `test_cancel_control_terminates_child_and_commits_cancelled`(test_subprocess.py:172 이식) | job CANCELLED, ledger `[("failed","RUN_CANCELLED")]`, run CANCELLED, `cancel.state=="CANCELLED"` |
| T3-09 | 취소 경쟁(D3 의존) | `test_succeeded_result_after_cancel_request_is_recorded_cancelled` | (A) job CANCELLED, evaluation EVALUATED 행 없음, `error.discarded_result.status=="SUCCEEDED"` |
| T3-10 | result 응답 유실 | `test_result_replay_same_digest_returns_stored_response_without_side_effects` | 두 응답 본문 동일, ledger 행 수·evaluation 수·`runs.last_seq` 불변 |
| T3-11 | result 충돌 | `test_result_with_different_digest_is_409_conflict` | 409, DB 무변경, error 이벤트 1건 |
| T3-12 | 처리 중 | `test_result_processing_row_yields_202_with_retry_after_and_sweeper_clears_orphan` | 202 + `Retry-After`; 60 s 후 sweeper가 PROCESSING 고아 삭제 → 재시도 200 |
| T3-13 | sweeper 회수·과금 | `test_sweeper_reclaims_expired_lease_and_bills_lost_window`(test_lease.py:51 이식) | QUEUED, `error.reason_code=="LEASE_EXPIRED"`, ledger 1행 `failed/lost` ≈ heartbeat_at−started_at |
| T3-14 | 시도 소진 | `test_sweeper_fails_job_and_candidate_when_attempts_exhausted`(test_lease.py:71 이식) | FAILED `LEASE_EXPIRED_MAX_ATTEMPTS`, candidate TRAIN_FAILED, failed 행 3 |
| T3-15 | 회수 후 heartbeat | `test_heartbeat_after_reclaim_is_409_lease_lost` | 409; runner_sim이 child 종료 플래그 기록 |
| T3-16 | 회수 후 늦은 result(성공) | `test_stale_succeeded_result_is_discarded_and_lost_row_extended_once`(test_lease.py:157 이식) | 409 LEASE_LOST; ledger failed 행 1개, `gpu_seconds`=전체 구간, `meta.extended_by=="owner_lost_lease_finalize"`; evaluation 없음; 다음 attempt 성공 시 `retry` 행 |
| T3-17 | 회수 없이 lease 상실(소유자 교체) | `test_lost_lease_without_reclaim_row_appends_one_lost_row`(test_lease.py:201 이식) | lost 행 1개 |
| T3-18 | 같은 runner 재-lease fence | `test_stale_attempt_of_same_runner_cannot_renew_touch_or_finish` | attempt 1의 heartbeat/progress/result 모두 409; attempt 2 행 무오염 |
| T3-19 | runner 재시작(결과 없음) | `test_runner_restart_without_result_reports_child_exit_no_result_and_resumes` (test_subprocess.py:87 이식) | attempt 1 `failed/CHILD_EXIT_NO_RESULT`, attempt 2 `retry`, training_full epochs `[1,2]` |
| T3-20 | runner 재시작(result.json 있음) | `test_runner_journal_replays_result_then_ack` | commit 1회, `idempotent_responses.acked_at` 설정 |
| T3-21 | core 재시작 | `test_core_restart_between_claim_and_result_preserves_lease_and_commit` | 새 app 인스턴스에서 heartbeat 200, result 200, 상태 동일 |
| T3-22 | commit 실패 | `test_commit_exception_rolls_back_and_leaves_no_idempotent_row` | 500 COMMIT_FAILED, `idempotent_responses` 없음, jobs 상태 RUNNING 유지, 재시도 성공 |
| T3-23 | checkpoint digest | `test_runner_recomputes_checkpoint_sha_and_rejects_tampered_file`(test_worker_integrity.py:46 이식) | 등록 없음 + `CHECKPOINT_REPORT_REJECTED` log(progress 배치 경유) |
| T3-24 | checkpoint 형식 | `test_core_rejects_checkpoint_without_digest_wrong_kind_or_stale_attempt` | 422/409, checkpoints 행 없음 |
| T3-25 | artifact 선등록→commit | `test_result_artifacts_resolve_to_preregistered_rows_without_hashing` | evaluation 행 `artifacts[*].id` == 등록 id; core가 `sha256_file`을 호출하지 않음(monkeypatch 카운트 0) |
| T3-26 | execution_configuration 보고 | `test_execution_configuration_report_updates_candidate_without_core_file_access` | `candidates.architecture_yaml`·`audit.execution_configuration` 갱신, core FS 접근 0 |
| T3-27 | progress 배치 | `test_progress_batch_touches_job_and_emits_one_candidate_updated_per_batch` | `jobs.heartbeat_at` 갱신, `candidate.updated` 1건/배치, `worker.updated` 1건 |
| T3-28 | 중단 종료 | `test_runner_shutdown_requeues_without_consuming_retry` | `failed/WORKER_SHUTDOWN`, QUEUED, `max_attempts+1` |
| T3-29 | runner liveness | `test_runner_without_heartbeat_is_reported_lost`(test_lease.py:139 이식) | `/health worker_count` 0, snapshot worker `status=="lost"`, `runners.status=='LOST'` |
| T3-30 | 인증 | `test_runner_token_bound_to_other_runner_id_is_403`, `test_expired_or_revoked_token_is_401`, `test_server_scope_token_cannot_call_runner_routes` | 401/403, DB 무변경 |
| T3-31 | 토큰 회전 | `test_rotated_token_overlaps_for_grace_then_old_is_rejected` | grace 내 둘 다 200, 이후 구 토큰 401 |
| T3-32 | TLS 강제 | `test_runner_refuses_plain_http_to_non_loopback_core` | 기동 실패 코드 `CORE_URL_INSECURE` |
| T3-33 | 본문 한도 | `test_result_body_over_limit_is_413_and_runner_externalises_per_sample`(D6 의존) | 413 → 재시도 본문 < 한도 |
| T3-34 | prune 왕복 | `test_prune_request_round_trip_marks_checkpoint_pruned_only_after_runner_report` | runner 보고 전 PRESENT, 후 PRUNED+`prune_json` |
| T3-35 | 구조검사 sandbox | `test_structure_check_subprocess_timeout_returns_unverified`, `test_structure_check_subprocess_env_has_no_secrets_and_no_gpu`, `test_structure_check_result_over_1mib_is_rejected` | UNVERIFIED/STRUCTURE_CHECK_TIMEOUT; env에 MBNAS_API_TOKEN 없음·`CUDA_VISIBLE_DEVICES==""`; 거부 |
| T3-36 | owner worker 경계 | `test_owner_worker_leases_propose_generation_and_runner_never_sees_it` | owner `tick()`이 lease, runner claim 204 |
| T3-37 | 세대 journal 복구 유지 | `test_sigkill_mid_generation_recovers_saved_candidates_once`(test_generation_crash.py:80 그대로) | 기존 단언 유지(owner 경로 무변경 증명) |

---

## 15. DD 충돌·보완 플래그 (증거 포함)

| ID | 발견 | 증거 | 영향/제안 |
|---|---|---|---|
| F1 | 학습·평가 핸들러가 private 모듈 `protocol.fitness`/`protocol.promotion`을 import하여 runner에서 fitness·gate를 계산 | training/handlers.py:189, 205; evaluation/handlers.py:29-30, 169-185, 230 | DD-A1(a)·DD-A5 위반 후보. core는 어차피 재계산하고 핸들러 값은 `raw_metrics.handler_fitness`로만 보관(engine.py:538-552, 546-548) → runner 패키지에서 제거(R10). `_fitness_rung`(188-194)은 순수 함수라 유지 가능 |
| F2 | PROPOSE_GENERATION(평가 있는 search)은 오늘 GPU worker가 lease·실행 | main.py:246-247(`generation_only=True if cpu else None`), 290-291(generation_only만 GPU 숨김) | DD-A4와 불일치 → R6 + owner worker 기동 옵션 `--kinds PROPOSE_GENERATION`. runner `claim`은 서버에서 차단 |
| F3 | `MIXED_HOOK="mbnas_server.search.etri_bridge:ensure_mixed_registered"` 문자열 경로와 `yaml_uses_mixed_modules` import가 runner 측 `search_arch`에 남음 | child.py:52, 59, 68; etri_bridge.py:112-142(sys.path 조작) | DD-A2 vendoring 후 `mbnas_model_runtime.operators`로 경로 변경(R8); 모듈 분류 담당(A1)에 전달 |
| F4 | `spec.params.admission`(예산 판정 상세)과 `stage.fitness_weights`가 job spec에 들어 있어 엔벨로프로 새어 나갈 수 있음 | engine.py:363-365 | §4.3 필터. `admission.as_dict()` 내용은 확인 못 함(admission.py 미열람) → 기본 제거 |
| F5 | protocol `console.paths.*`는 core 호스트 절대경로이며 protocol_hash에 포함 → runner가 protocol을 고쳐 쓸 수 없음 | validator.py:71, 99, 172-182; training/context.py:53-55(해시 불일치 → PROTOCOL_HASH_MISMATCH), 119-137(경로 사용) | `data_refs` + runner `dataset_mounts` 재매핑(R9). split_hash가 위치 독립이므로 frozen hash 검사는 유지됨(manifest.py:14-15, 244-245) |
| F6 | result commit 경로가 core 파일시스템을 읽음(모델 구성·native 이미지·artifact 해싱) | engine.py:433-440, 484-503; model_artifacts.py:34-63, 77-115; registry.py:147-152 | R3/R4; 미처리 시 원격 runner의 모든 학습 결과가 `MODEL_CONFIGURATION_UNAVAILABLE`·artifact problems로 기록됨 |
| F7 | retention·resume 검사가 core 파일을 전제 | retention.py:99-109, 132; state.py:102-119 | R11/R12; WP-5와 경계 합의 필요 |
| F8 | fencing에 attempt 부재 | repo.py:414-455; jobs.py:68-85 | R1(DD-A4의 "lease_epoch+runner_id" 명시 구현) |
| F9 | 결과 본문 크기: `evaluation` 페이로드에 `per_sample`·`coco_stats`·`latency` 전체가 들어가며 한도는 1 MiB | evaluation/handlers.py:133-158; config.py:65; limits.py:16 | 실제 크기는 확인 못 함. `evaluation.json` artifact에 동일 내용이 이미 저장됨(159-165) → D6 |
| F10 | `_handle_line`은 `log`/`metric`을 저장하지 않음(stderr 파일만) | main.py:396-416 | progress 배치에서 `log`/`metric`은 선택 전송(크기 상한), 기본은 runner 로그 파일 artifact화 |

---

## 16. 결정 필요 사항

| ID | 결정 | 권고 | 근거 |
|---|---|---|---|
| D1 | 엔벨로프의 식별자: 내부 id vs 공개 opaque id | **내부 id** 사용, public_ids는 gateway 투영 전용 | §4.4: candidate id가 경로·`arch.model_id`·해시 입력에 쓰임(child.py:67, stage_trainer.py:88, handlers.py:95-99); 치환 시 DD-10 교차검증 영향 확인 못 함 |
| D2 | `start` 엔드포인트 분리 vs claim 즉시 시작 | **분리** | GPU 대기 미과금 의미 보존(main.py:304); 비용은 호출 1회 |
| D3 | 취소된 run의 result 처리 | **(A)** core 상태 기준으로 CANCELLED 수용 + 원 결과를 `error_json.discarded_result`에 보존 | §7.4; (B)는 오늘의 비결정 경쟁을 그대로 둠 |
| D4 | 회수 책임: sweeper 단일 vs owner worker 병행 | sweeper 단일(owner worker의 `tick()` reclaim 호출은 무해하므로 당장 제거하지 않아도 됨) | `reclaim`은 BEGIN IMMEDIATE 직렬화로 중복 과금이 없음(core.py:147; repo.py:458-475) |
| D5 | checkpoint affinity를 `acquire_lease` 정렬에 반영할지 | 1차는 **미반영**(엔벨로프 `resume.location`으로 runner가 전송 판단), 2차에서 `prefer_owner` 정렬 검토 | `acquire_lease` 단일문 유지가 우선; 다중 runner 운영 전까지 효과 없음 |
| D6 | result 본문 한도 | runner 라우트만 **32 MiB** + `per_sample`/`latency.samples`는 `evaluation.json` artifact 참조로 대체 | F9 |
| D7 | retention 삭제의 원격화 시점 | WP-5에서 prune 왕복(R11) 구현, 그 전까지 원격 runner run은 `keep_all` 강제 | retention.py:99-109가 core 파일만 삭제 가능 |
| D8 | loopback 동거 배포에서 TLS 요구 여부 | loopback은 TLS 생략 허용, 비-loopback은 필수 | §11.2 |
| D9 | 구조검사 sandbox 자원 한도 기본값 | wall 120 s / cpu 100 s / AS 16 GiB / 동시성 1, 설치 검증에서 실측 후 조정 | §10.2; import 메모리 요구치 확인 못 함 |
| D10 | `log`/`metric` 진행 메시지 전송 여부 | 기본 미전송(오늘과 동일), 종료 시 stderr 로그 artifact 등록 | F10 |
| D11 | F1(runner 측 fitness 계산 제거)의 범위 | runner 패키지에서 제거; 핸들러 결과의 `fitness/gate` 키 부재를 core가 허용(이미 `raw_metrics.handler_fitness`만 선택적으로 사용) | engine.py:546-548 |

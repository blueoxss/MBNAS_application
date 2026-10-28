# WP-2 REMOTE QUEUE — 기존 queue/lease의 원격화 계약 v1 (설계자 claude-fable-5-1, 2026-10-23 22:3x; WP-1 수용 후 착수)

우선순위: 이 문서 > DECISIONS(DD-A18, A20, A14~A16, A25) > `docs/internal/analysis/REMOTE_PROTOCOL_DESIGN.md`(A3; 엔드포인트·엔벨로프·tx·실패 매트릭스·DDL·T3 매트릭스의 상세는 그 문서가 규범이며 여기 표기된 수정만 덮어쓴다) > SECURITY_BASELINE.md(R1~R4, R8).

## 0. 목표
runner(고객 호스트, 공개 패키지)가 DB 없이 core의 `/core/v1/runner/*`만으로 claim→start→heartbeat→progress→checkpoints/artifacts→result→ack를 수행하고, core는 기존 `repo/jobs/costs/engine` 함수를 짧은 tx 안에서 그대로 호출한다. owner worker는 PROPOSE_GENERATION 전용이 된다. WP-1의 과도기 허용(core→runner 동적 import)을 제거한다.

## 1. core 측 (`mbnas_core`)
1. **DB v4 (additive migrate)**: `runners`, `principals`, `idempotent_responses`, `artifact_locations` — A3 §12 DDL 그대로(단 `public_ids` 테이블은 만들지 않는다, DD-A19). `SCHEMA_VERSION="4"`.
2. **fencing(R1)**: `repo.renew_lease/touch_job/finish_job/mark_job_running`, `jobs.start_attempt/requeue`에 선택 인자 `attempt: int|None` 추가(주어지면 `AND attempt = :attempt`). 기존 호출(owner worker)은 None.
3. **`mbnas_core.runner_api`** 패키지: `routes.py`(E1 register, E2 claim[재전달 검사 포함, kinds ∩ GPU_KINDS 강제, 400 KIND_NOT_ALLOWED], E3 start, E4 heartbeat[control: action null|pause|cancel, run_status, prune 목록], E5 progress(배치; generation_summary 거부), E6 checkpoints, E7 artifacts(batch), E8 result(§7 멱등/fence/settle_job 이식/LEASE_LOST lost 분기/취소 수용 A), E9 ack, E10 deregister), `envelope.py`(A3 §4 스키마 v1; `owner_worker.child`의 `load_context/native_params/search_arch/latest_live_full_state`를 **이동**하여 DB 행→엔벨로프 빌더로 재사용; `spec.params` 필터 §4.3; baseline arch 주입(DD-A16); 내부 id 사용), `fence.py`, `settle.py`(`Worker._settle_job` 본문 이식: `settle_job(conn, job, result, exit_code, cancelled, stopping, started, finished, runner_id, device_uuid)`; owner worker도 이것을 호출), `sweeper.py`(스레드, 주기 heartbeat_s: `jobs.reclaim`+과금+requeue/finish, `prune_workers`, runners LOST, PROCESSING 고아 60 s 정리; core 앱 시작 시 1회 즉시), `auth.py`(principals: 발급·hash 검증·만료·회전·scope; `require_principal(scope)` dependency; runner 토큰은 runner_id 바인딩 403 RUNNER_MISMATCH), `tickets.py`(HMAC-SHA256 ticket = job_id|attempt|stage|runner_id|exp, 키 `CoreSettings.ticket_key`; claim 응답에 발급, 쓰기 엔드포인트 필수 검증).
4. **core 앱**: `mbnas_core.api.app`에 `/core/v1/runner`(scope runner) 마운트; `/core/v1/owner/{runners, principals}` 최소 GET(scope admin); `/api/v1` console 라우트는 WP-3까지 유지. 본문 한도: runner 라우트 32 MiB(별도 BodyLimit), 나머지 1 MiB.
5. **core가 파일을 만지는 6곳 재배치(A3 §9.3)**: `engine._register_artifacts`는 항목에 `artifact_id`가 있으면 `repo.get_artifact` 조회(해싱 없음), 없으면 기존 경로(owner 로컬 호환); `job_finished`의 `record_model_configuration/record_native_images`는 결과 `outputs.execution_configuration`·선등록 artifact 보고가 있으면 DB 갱신만(새 함수 `record_model_configuration_from_report`); `retention`은 원격 run에서 `keep_all` 강제(WP-5에서 prune 왕복); `state._verify_full_state`는 기록 검사 + (같은 호스트 store 사본이 있을 때만) 파일 검사; `resolve_file`은 WP-5.
6. **validator 입력 추상화**: `ValidationContext`에 `devices`(기존) 외 `manifest_provider`(선택) 추가: 주어지면 dataset manifest/counts/class mapping을 파일 대신 제공값에서 읽는다(WP-3 gateway가 고객 호스트에서 계산해 전달). 장치 inventory는 `runners` 테이블의 등록 장비에서 구성하는 `devices_from_runners(conn)`를 추가하되 기본은 기존 NVML 경로 유지(설정 `MBNAS_CORE_DEVICE_SOURCE=local|runners`).
7. **owner worker**: `WorkerConfig(kinds=["PROPOSE_GENERATION"], generation_only=None)`로 기동하는 `--owner` 모드 추가, 기본 `mbnas-core-owner-worker`는 이 모드. 학습 kind 핸들러 테이블·runner 동적 import 삭제(과도기 종료). `owner_worker.child`는 PROPOSE_GENERATION만.
8. **CoreSettings 추가**: `ticket_key`(SecretStr), `runner_body_limit_bytes`(32 MiB), `device_source`, `remote_retention_policy="keep_all"`.

## 2. runner 측 (`mbnas_runner`)
1. `client.py`(httpx; `verify`=CA 파일 또는 True; 비-loopback `http://` 거부 `CORE_URL_INSECURE`; 재시도·백오프; 모든 쓰기에 runner_id·attempt·ticket), `journal.py`(A3 §6.2 디렉터리·outbox 규칙; 재시작 재생 §5.5), `daemon.py`(claim 루프; `Worker.execute/_monitor/_handle_line/_heartbeat` 구조를 HTTP로 이식: start → child spawn(`-m mbnas_runner.child --spec-file <envelope-derived JobSpec json>`) → heartbeat 스레드(lease 연장, control 플래그 → stop 파일 기록/삭제 `<work_root>/runs/<run_id>/control/<job>.stop`) → progress 배치(≤1/s) → checkpoint 보고 시 데몬이 sha256 재계산·크기·run_dir 내부 검사 후 등록(DD-8 재배치) → artifact 선등록 → result(started_at·finished_at·cancel_observed·runner_shutting_down·payload_digest) → ack; lease 만료 감시(`kill_on_lease_expiry` 기본 on); runner 정상 종료 시 `WORKER_SHUTDOWN` 의미 보존), `__main__.py`(`mbnas-runner run|register|--help`).
2. `child.py`: `--spec-file`에 JobSpec(native params 포함)과 `run_dir`·`stop_request_path`; DB 없음. `resume_from`은 엔벨로프 `resume.live_latest`(runner 로컬 경로), `eval_ckpt`는 `resume.eval_ema.path`.
3. `training/context.py`: `data_refs` 재매핑 훅 — `RunnerSettings.dataset_mounts`(JSON `{protocol_root: local_root}`)로 `console.paths.*`를 로컬 경로로 치환해 `split_manifests`를 구성하고 frozen manifest hash를 검증(위치 독립). 미매핑·불일치는 `DATASET_UNAVAILABLE`/`DATASET_DRIFT` 실패(묵시 진행 금지).
4. `RunnerSettings` 추가: `token_file`, `ca_file`, `dataset_mounts`, `kinds`(기본 5종), `kill_on_lease_expiry`, `progress_interval_s`, `max_batch_messages`.
5. runtime manifest 보고(R6): register 본문에 `runtime={mbnas_runner, mbnas_model_runtime, mbnas_contracts 버전, torch, cuda, python, envelope_version, operator_files_sha256, parser_source_sha256}`; core는 저장하고 공개 DTO에 `verified|mismatch|unknown` 라벨(기준: core 호스트의 model_runtime operator 파일 sha와 일치).

## 3. 계약(contracts)
`mbnas_contracts/runner_protocol.py`: pydantic 모델 `JobEnvelope v1`, `ClaimRequest/Response`, `StartResponse`, `HeartbeatRequest/Response(control)`, `ProgressBatch`, `CheckpointReport`, `ArtifactReport`, `ResultCommit`, `ResultResponse`, 오류 코드 enum(`KIND_NOT_ALLOWED, LEASE_LOST, RESULT_CONFLICT, PROCESSING, COMMIT_FAILED, RUNNER_MISMATCH, VERSION_MISMATCH, TICKET_INVALID, CORE_URL_INSECURE`). `extra="forbid"`, 경로 필드 없음(R8). OpenAPI에는 `/core/v1/runner/*`를 **별도 문서** `openapi_runner.json`(contracts 패키지 데이터, 공개)로 기록(공개 `/api/v1` 계약과 분리; WP-4 1.5.0 bump와 함께 이력 등록).

## 4. 수용조건(T3 매트릭스, A3 §14)
T3-01~T3-37 중 WP-2 범위(T3-34 prune 왕복과 T3-35 sandbox 제외) 전부를 `tests/owner/runner_protocol/`(core 앱 + `runner_sim` 인프로세스 시뮬레이터 + stub 핸들러)와 `tests/public/runner/`(client/journal/daemon 단위: 가짜 core를 **stub HTTP 서버**로 두는 단위 테스트 — 통합 증거로 세지 않음)로 구현. 실제 2프로세스 통합: `scripts/dev/wp2_two_process_smoke.py` — core 앱(uvicorn, 임시 포트) + owner worker + runner 데몬을 실제 프로세스로 띄워 generation-only run(CPU)과 합성 4이미지 yolo11n 64px CPU 학습 1 epoch(`MBNAS_RUNNER_*` 설정, 데이터 mounts)을 완주하고 snapshot에 EVALUATED 행이 생기는지 확인(exit 0 + evidence/WP2/). import 검사기 과도기 허용 0건.

## 5. 소유 파일
| ID | 범위 | 모델 |
|---|---|---|
| WP-2A core 프로토콜 | `mbnas_core/{runner_api/**, db/schema.sql+core.py(v4), db/repo.py(attempt 인자), scheduling/jobs.py, orchestration/{engine,retention,state}.py(§1.5), protocol/validator.py(§1.6), config.py, api/app.py, owner_worker/**}` | opus |
| WP-2B contracts | `mbnas_contracts/{runner_protocol.py, openapi/openapi_runner.json}` | sonnet |
| WP-2C runner | `mbnas_runner/{client,journal,daemon,child,__main__,config}.py, training/context.py` | opus |
| WP-2D tests+smoke | `tests/owner/runner_protocol/**`, `tests/public/runner/**`, `scripts/dev/wp2_two_process_smoke.py`, evidence/WP2 | opus |
| WP-2V verify | §4 실행·판정 | opus |

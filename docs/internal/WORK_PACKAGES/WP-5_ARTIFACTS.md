# WP-5 ARTIFACT·재개·보존 계약 v1 (설계자 claude-fable-5-1, 2026-10-23 22:5x; WP-4와 병행 가능, WP-2 수용 후)

우선순위: 이 문서 > DD-A6, A18(D5/D7), A19(D10), A20(R8), A23 > REMOTE_PROTOCOL_DESIGN §6.3/§6.4/§9.3 > SECURITY_BASELINE R8/F7.

## 0. 목표
runner가 만든 바이트(checkpoint, 이미지, YAML, 평가 JSON)가 **고객 측 artifact store**에 들어가고, gateway가 digest 대조 후 제공하며, core는 id/digest/위치만 안다. 다른 runner로 재개 시 store에서 digest 검증 전송 후 재개. retention prune 왕복. export에 YAML 바이트.

## 1. artifact store (`mbnas_server.artifacts`)
- `store.py`: 콘텐츠 주소 저장 `$MBNAS_HOME/gateway/artifacts/<sha256[:2]>/<sha256>` + 메타 JSON(`run_id, relpath, kind, bytes, mime, registered_by(runner_id), job_id, attempt, received_at`), 원자적 쓰기(tmp+fsync+rename), 수신 시 스트리밍 sha256 재계산·불일치 `LOAD_FAILED`.
- 업로드 API(runner→gateway, runner 토큰+ticket): `PUT /api/v1/internal/artifacts/{sha256}`(본문 스트리밍, 최대 `GatewaySettings.artifact_max_bytes` 기본 4 GiB; 헤더 `X-Artifact-Bytes`, `X-Job-Ticket`), 멱등(동일 sha 존재 시 200). 같은 호스트 동거(기본 배포)에서는 runner가 `RunnerSettings.artifact_store_dir`(= gateway store 경로)로 **직접 하드링크/복사**하는 `local` 모드 제공(설정 `artifact_transfer=local|upload`).
- 다운로드: `GET /api/v1/runs/{run_id}/artifacts/{artifact_id}`(bearer 또는 서명 URL; gateway 서명키) → core에 artifact 레코드 조회(scope server) → store 바이트 스트리밍 + `X-Artifact-Sha256` + 재계산 대조(불일치 409 `LOAD_FAILED`). `refresh`(재서명)·`export`(core 생성 zip relay + public 프로파일 멤버에 store의 YAML 바이트 삽입은 core가 요청 시 gateway에서 가져오지 않는다 → **export zip은 gateway가 조립**: core `/core/v1/console/runs/{id}/export-manifest`(멤버 목록+core 생성 JSON 멤버 바이트) + store 바이트(yaml/이미지 선택 포함) → zip + `X-Bundle-Sha256`).
- 서명: `mbnas_server.artifacts.signing`(기존 sign/verify_signature/artifact_view 이식; 키는 GatewaySettings).

## 2. core 측
- `artifact_locations`(WP-2 테이블) 상태 전이: runner 등록 `PRESENT@runner_local` → store 수신 보고(gateway→core `POST /core/v1/console/artifacts/{id}/locations`) `PRESENT@server_store(verified_by=store)`; 다운로드 가능 조건 = server_store PRESENT.
- `engine._register_artifacts`/`_queue_evaluation`/`commit_evaluation`의 artifact 참조는 id 기반(WP-2 R3 완성); `artifact_view`는 gateway 책임이므로 core 응답은 `{id, kind, sha256, bytes, mime_type, state, sample_id, split_hash, metadata}`만(URL 없음) — WP-4 투영과 정합.
- resume 재배치(R12 완성): `state._verify_full_state`는 행+location 검사; 필요 시 gateway store 존재 확인 API 호출은 하지 않는다(core는 네트워크를 tx 안에서 기다리지 않음; location 행이 권위).
- retention 왕복(R11): `retention.plan()` 결과를 `prune_requests` 테이블(additive)에 기록 → heartbeat `control.prune`로 runner에 전달 → runner 삭제 후 `POST /core/v1/runner/checkpoints/{id}/pruned` → `mark_checkpoint_pruned`; gateway store 사본도 prune 대상이면 gateway에 `DELETE /api/v1/internal/artifacts/{sha256}`(runner 토큰) — 기본 정책은 store 사본 유지(설정).
- checkpoint affinity: claim 응답 `resume.location`이 다른 runner면 runner는 store에서 `GET /api/v1/internal/artifacts/{sha256}`로 받아 `<run_dir>/<relpath>`에 복원(sidecar 포함, digest 검증) 후 재개; 실패 시 `RESUME_BLOCKED CHECKPOINT_TRANSFER_FAILED`(scratch 금지). (lease 정렬 affinity는 미반영, DD-A18.)

## 3. runner 측
- 데몬: checkpoint 등록 직후 `artifact_transfer` 모드에 따라 store로 전송(local 하드링크 or 업로드), 완료 보고; 평가 artifact(이미지·evaluation.json·weights_fp16)도 동일; 실패는 재시도 큐(journal) — result commit은 전송 완료를 기다리지 않되 core가 `artifact_locations` 상태를 `TRANSFERRING`으로 두고 다운로드는 PRESENT 후에만.
- `RunnerSettings`: `artifact_transfer`, `artifact_store_dir`, `gateway_url`, `gateway_upload_token_file`.

## 4. 수용조건
- tests/owner: 등록→전송→다운로드 digest 일치(E2E in-process), 변조 바이트 409, 미전송 artifact NOT_READY, 다른 runner 재개 시 전송 후 `resumed_from` 경로 복원, prune 왕복(T3-34), export zip에 yaml 바이트+SHA256SUMS 일치.
- tests/public/server: store 원자성·sha 검증·크기 상한·경로 봉쇄(id만 수용).
- T4/T5에서 실제 브라우저 다운로드 로그(sha 헤더 일치).

## 5. 소유
WP-5A gateway store/signing/export(opus) / WP-5B core locations/retention/resume(opus) / WP-5C runner transfer(sonnet) / WP-5D tests(opus) / WP-5V verify.

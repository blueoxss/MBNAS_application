# WP-4 공개 투영·계약 1.5.0 계약 v1 (설계자 claude-fable-5-1, 2026-10-23 22:4x; WP-3 수용 후 착수)

우선순위: 이 문서 > DD-A17/A19/A20(D8)/A23 > DTO_ALLOWLIST_PROPOSAL.{md,json}(필드별 분류가 규범) > CONTRACTS_v2(스냅샷 docs) 이력 규칙.

## 0. 목표
공개 OpenAPI를 유일 권위로 삼아(`additionalProperties:false`) core 콘솔 응답·SSE·export를 allowlist 투영하고, owner 감사 DTO는 `/core/v1/owner/*`로 분리한다. 계약 1.4.2 → **1.5.0** 1회 bump(변경 기록·migrate 스크립트·types.gen.ts 재생·웹 contract 테스트).

## 1. 계약 1.5.0 변경 목록
1. 모든 공개 component에 `additionalProperties:false`(현재 open object인 `Health.capabilities` 등은 명시 키로 고정).
2. 제거: `Generation.group_key`, `Worker.pid/child_pid/lease_expires_at`, `Block.old_name/before/after/repair_reason/params.slot_kind`, lineage의 `crossover/mutation_events/repair_events/prior_draw/selection/parent_rows/elite/notes/space_hash/prior_hash`(→ `search_space.{version,variant}`만), `CandidateDetail.audit`는 `evidence` 서브셋 스키마로 재정의(DTO_ALLOWLIST §3.3 PUBLIC 키만), `raw_metrics`는 허용목록 키 스키마, `checkpoints[].path`→`artifact_ref`/`checkpoint_id`, `checkpoint_path/architecture_path`→ id 참조, Health `allowlists`는 고객 호스트 루트만, `capabilities.search_space.root` 제거, RegistryEntry evidence 경로→증거 id(파일명+sha256), `resolved_protocol`: `resolution.paths.*.realpath` 제거(+`RESOLUTION_HASH_EXCLUDED`에 `resolution.paths` 추가), `search_space_resolved.etri_root` 제거, `console.benchmark.device_resolved.uuid`→core 발급 `device_id`.
3. 추가: `GeneratedCandidate.training_status`("UNTRAINED"), `Candidate.training_status`, `Worker.runner_id/device_id`, `RuntimeManifestStatus`(verified|mismatch|unknown), `ModelConfigRevision`(revision_id, source_sha256, canonical_hash(yaml_hash), architecture_hash, kind(original|selected|executed|generated), created_at, parent_revision_id, artifact_id) + `GET /runs/{id}/candidates/{cid}/config-revisions`, `GET …/config-revisions/{rev}/yaml`(바이트), `UploadResponse`, 요청 필드 `upload_id`/`model_yaml_text`/`source_sha256`, `Error.location` enum, `Preset.protocol.console.paths` placeholder 규칙 설명.
4. SSE: 이벤트 payload는 같은 투영 함수 적용; `log` payload 키 허용목록 + message scrub.
5. export: public 프로파일(`GET /runs/{id}/export?profile=public` 기본) — 멤버별 투영(DTO_ALLOWLIST §3.9) + `architectures/<cid>_<revision>.yaml` 실제 바이트; owner 프로파일은 `/core/v1/owner/runs/{id}/export`.
6. `openapi_runner.json`(WP-2) 이력 등록.

## 2. 구현
- `mbnas_core.console.projection`: component별 allowlist(공개 OpenAPI에서 자동 생성한 스키마 트리로 "스키마에 있는 키만 복사"; 추가 scrub 규칙: 절대경로·PID·hostname 정규식, repair 접기, worker/device id 치환). 적용 지점: 모든 console 라우트 응답, SSE 이벤트 직렬화(`routes_stream`), export 멤버 생성. 저장 행 불변.
- `mbnas_core.console.owner_routes`: `/core/v1/owner/runs/{id}/candidates/{cid}/audit`(원본 audit/lineage/blocks), `/export?profile=owner`, runners/principals(WP-2 확장).
- `config_revisions` 테이블(additive v5)과 기록 지점: 업로드(original), 선택 시(selected: selection.create_training_run), 실행본 등록(executed: record_model_configuration_from_report), 생성 후보(generated: proposal). `GET …/yaml`은 저장 바이트 그대로(gateway는 artifact store 또는 core 저장소에서 relay — 원문 바이트는 **gateway store**에, canonical 텍스트는 core 행에 두고 revision 행이 둘의 sha를 모두 기록).
- contracts: `openapi.json` 1.5.0, `history/openapi-1.4.2.json` 보존, `migrate_1.4.2_to_1.5.0.py`(재생 byte-identical), `HISTORY.json`, pydantic 거울 `extra="forbid"`로 전환, `x-contract-changelog`.
- 웹: `npm run gen:types` 재생, `types.ts` 수기 보강 제거, 영향 컴포넌트(WP-6와 조율: Lineage 절대경로 표시→id, CandidateView hash 대조는 artifact sha 유지).

## 3. 수용조건
- `tests/owner/projection/`: DTO_ALLOWLIST JSON의 OWNER_ONLY 필드 969개 중 공개 응답(snapshot, detail, generations, events replay, export public, health, registry, validate)에 0건 등장(DB에 시드된 canary 값으로 확인), PUBLIC 필드는 보존.
- `tests/unit/test_contract_history`(재생 동일), `test_schemas_contract`(1:1 규칙) 통과; 웹 `check:types`·contract 테스트 통과.
- T8 canary 공개 응답 0 히트(OWNER_DTO 영역).

## 4. 소유
WP-4A contracts+migrate(opus) / WP-4B core projection+owner routes+revisions(opus) / WP-4C tests(opus) / WP-4D web types(sonnet) / WP-4V verify(opus).

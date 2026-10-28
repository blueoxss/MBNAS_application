# 계약 1.5.0 초안 — 1.4.3 대비 변경 보고 (WP-4A)

이 파일은 `check_draft_1_5_0.py --write`가 생성한다(표·수치는 실행 결과). 판정 근거: `check_draft_1_5_0.result.json`.

| 항목 | 값 |
|---|---|
| 상태 | DRAFT — live `openapi.json`은 1.4.3 그대로(교체 금지 지시) |
| 초안 | `mbnas_contracts/src/mbnas_contracts/openapi/drafts/openapi-1.5.0.draft.json` sha256 `70edb8ef0628f7e49bc96ac67000c520036b5e29d9e2b76439cc6e8f1d7f9d17` |
| 기준(live 1.4.3) | `mbnas_contracts/src/mbnas_contracts/openapi/openapi.json` sha256 `f3fe8ea672ee106d41c5c92723f7002136c7181892933aff7653a31c83f8c738` |
| 1.4.3 보존본 | `docs/public/contracts/history/openapi-1.4.3.json` (live와 byte 동일, HISTORY versions 마지막 항목) |
| 생성기 | `scripts/dev/migrate_openapi_1.4.3_to_1.5.0.py` (표준 라이브러리만; 1.4.3 스냅샷에서 실행 시 결과 sha256을 assert) |
| 재생 | `python -I -B scripts/dev/migrate_openapi_1.4.3_to_1.5.0.py <1.4.3 사본> [OUT]` → byte 동일 확인 |
| HISTORY | `openapi/HISTORY.json`의 `drafts[0]`(status `draft`); `versions`는 1.4.3에서 끝남(기존 이력 테스트 불변) |

## 1. "OWNER_ONLY 969개" 수치 정정

`DTO_ALLOWLIST_PROPOSAL.json`의 969는 **전체 항목 수**다: PUBLIC 817 / PUBLIC_MAPPED 104 / OWNER_ONLY 48 (`_meta` 의사 항목 5개 포함; md 3행과 일치). OWNER_ONLY는 48개 항목이고, 그중 4개는 스키마 경로가 아니다(`_meta` 범례 1, ExportBundle `(not exported) jobs…` 1, PublicIdentifiers 2). 나머지 44개 항목은 ` | `·`.x`·`{a,b}` 표기를 풀면 **58개 구체 경로**가 된다.

| 구분 | 경로 수 | 내용 |
|---|---|---|
| 1.4.3에서 선언된 스키마 키 | 17 | 투영 대상 10개 + Archive* 7개 |
| 1.4.3에서 스키마 키가 아님(열린 객체 아래 서버 추가 키) | 41 | raw_metrics.*, lineage.*, audit.*, evidence.*, Worker.child_pid, log payload path 등 |
| 스키마에 전혀 없음(의사 항목) | 4 | _meta / ExportBundle / PublicIdentifiers |

1.5.0 결과: 닫힌 부모로 배제 38, propertyNames 거부 13, 유지 7(Archive* — DD-A19가 아카이브의 mutation/driver 문서 유지를 결정), 여전히 허용 0. 검사기 위반 0건.

| component | member path | 1.4.3 | 1.5.0 |
|---|---|---|---|
| Block | `params.slot_kind` | OPEN | excluded: parent closed (additionalProperties false) |
| Block | `old_name` | DECLARED | removed from the schema; excluded: parent closed (additionalProperties false) |
| Block | `before` | DECLARED | removed from the schema; excluded: parent closed (additionalProperties false) |
| Block | `after` | DECLARED | removed from the schema; excluded: parent closed (additionalProperties false) |
| Block | `repair_reason` | DECLARED | removed from the schema; excluded: parent closed (additionalProperties false) |
| CandidateEvaluation | `raw_metrics.weights_only_path` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateEvaluation | `raw_metrics.eval_checkpoint_path` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateEvaluation | `raw_metrics.rung_result` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateEvaluation | `raw_metrics.artifact_problems` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateEvaluation | `raw_metrics.contract_error` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateEvaluation | `raw_metrics.row_error` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateEvaluation | `raw_metrics.reported` | OPEN | excluded: parent closed (additionalProperties false) |
| Generation | `group_key` | DECLARED | removed from the schema; excluded: parent closed (additionalProperties false) |
| Run | `budget.policy` | OPEN | excluded: propertyNames deny list (parent stays open) |
| Run | `resolved_protocol.console.search.search_space_resolved.etri_root` | DECLARED | removed from the schema; excluded: parent closed (additionalProperties false) |
| Worker | `pid` | DECLARED | removed from the schema; excluded: parent closed (additionalProperties false) |
| Worker | `child_pid` | OPEN | excluded: parent closed (additionalProperties false) |
| Worker | `lease_expires_at` | DECLARED | removed from the schema; excluded: parent closed (additionalProperties false) |
| Event | `payload[type=log].path` | OPEN | excluded: parent closed (additionalProperties false) |
| Health | `capabilities.search_space.root` | OPEN | excluded: parent closed (additionalProperties false) |
| Health | `versions.environment_lock.path` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `lineage.elite` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `lineage.parent_rows[]` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `lineage.selection` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `lineage.crossover` | DECLARED | removed from the schema; excluded: parent closed (additionalProperties false) |
| CandidateDetail | `lineage.mutation_events[]` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `lineage.repair_events[]` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `lineage.prior_draw` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `lineage.search_space.space_hash` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `lineage.search_space.prior_hash` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `lineage.notes[]` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `audit.config_estimate.rules` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `audit.config_estimate.selection_policy` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `audit.etri_audit_model` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `audit.orchestration.proposal_job_id` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `audit.orchestration.rungs.{}.admission.estimate_s` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `audit.orchestration.rungs.{}.admission.estimate_source` | OPEN | excluded: parent closed (additionalProperties false) |
| CandidateDetail | `audit.orchestration.rungs.{}.admission.reserved_s` | OPEN | excluded: parent closed (additionalProperties false) |
| RegistryEntry | `evidence.config_ref.path` | OPEN | excluded: propertyNames deny list (parent stays open) |
| RegistryEntry | `evidence.weights.path` | OPEN | excluded: propertyNames deny list (parent stays open) |
| RegistryEntry | `evidence.build.trace` | OPEN | excluded: propertyNames deny list (parent stays open) |
| RegistryEntry | `evidence.pretrained_load.loaded_from` | OPEN | excluded: propertyNames deny list (parent stays open) |
| RegistryEntry | `evidence.probe_file` | OPEN | excluded: propertyNames deny list (parent stays open) |
| RegistryEntry | `evidence.source.record` | OPEN | excluded: propertyNames deny list (parent stays open) |
| RegistryEntry | `evidence.source.notebook` | OPEN | excluded: propertyNames deny list (parent stays open) |
| RegistryEntry | `evidence.registry_roots_skipped[]` | OPEN | excluded: propertyNames deny list (parent stays open) |
| RegistryList | `registry_roots_skipped` | DECLARED | removed from the schema; excluded: parent closed (additionalProperties false) |
| StructureReport | `blocks[].before` | OPEN | excluded: propertyNames deny list (parent stays open) |
| StructureReport | `blocks[].after` | OPEN | excluded: propertyNames deny list (parent stays open) |
| StructureReport | `blocks[].repair_reason` | OPEN | excluded: propertyNames deny list (parent stays open) |
| StructureReport | `blocks[].old_name` | OPEN | excluded: propertyNames deny list (parent stays open) |
| ArchiveRun | `stop_rule` | DECLARED | kept (DD-A19 archive) |
| ArchiveRun | `transition` | DECLARED | kept (DD-A19 archive) |
| ArchiveRun | `orphan_check` | DECLARED | kept (DD-A19 archive) |
| ArchiveGeneration | `mutation` | DECLARED | kept (DD-A19 archive) |
| ArchiveLineage | `mutation` | DECLARED | kept (DD-A19 archive) |
| ArchiveLineageEntry | `mutation` | DECLARED | kept (DD-A19 archive) |
| ArchiveCandLineage | `mutation` | DECLARED | kept (DD-A19 archive) |

스키마 밖 OWNER_ONLY 항목: _meta :: `legend.OWNER_ONLY`; ExportBundle :: `(not exported) jobs.spec.params.parent_pool | previous_search_state | proposal_policy; jobs.result.outputs.space/rng_stream/ga/search_state`; PublicIdentifiers :: `group_key`; PublicIdentifiers :: `cost_ledger.id | meta.host_row_id | proposal_job_id | lease_owner`

## 2. 제거된 선언 멤버 (1.4.3에 선언, 1.5.0에 없음)

| component | member | allowlist 출처 | class | 근거 |
|---|---|---|---|---|
| Block | `after` | Block :: before \| after | OWNER_ONLY | DD-A19 D1 |
| Block | `before` | Block :: before \| after | OWNER_ONLY | DD-A19 D1 |
| Block | `old_name` | Block :: old_name | OWNER_ONLY | DD-A19 D1 |
| Block | `repair_reason` | Block :: repair_reason | OWNER_ONLY | DD-A19 D1 |
| CandidateDetail | `lineage.crossover` | CandidateDetail :: lineage.crossover | OWNER_ONLY | DD-A19 D2 |
| CandidateDetail | `lineage.mutations` | (contract-only name of the mutation trace; server emits mutation_events, OWNER_ONLY) | - | DD-A19 D2 public lineage subset |
| CandidateDetail | `lineage.repairs` | (contract-only name of the repair trace; server emits repair_events, OWNER_ONLY) | - | DD-A19 D2 public lineage subset |
| Generation | `group_key` | Generation :: group_key | OWNER_ONLY | DD-A19 D14 |
| Protocol | `console.search.search_space_resolved.etri_root` | Run :: resolved_protocol.console.search.search_space_resolved.etri_root | OWNER_ONLY | DD-A19 D3, WP-4A list |
| RegistryList | `registry_roots_skipped` | RegistryList :: registry_roots_skipped | OWNER_ONLY | DD-26 diagnostic |
| RegistryList | `registry_roots_skipped[].path` | RegistryList :: registry_roots_skipped | OWNER_ONLY | DD-26 |
| RegistryList | `registry_roots_skipped[].reason` | RegistryList :: registry_roots_skipped | OWNER_ONLY | DD-26 |
| Worker | `lease_expires_at` | Worker :: lease_expires_at | OWNER_ONLY | DD-A19 |
| Worker | `pid` | Worker :: pid | OWNER_ONLY | DD-A19 |

추가로 값 매핑(PUBLIC_MAPPED) 중 WP-4A 목록 항목은 스키마에서 배제했다(의도된 배제, 검사기 INTENDED_DROPS):

| component | path | 대체 |
|---|---|---|
| Run | `resolved_protocol.resolution.paths.{}.realpath` | resolved protocol without realpath (WP-4A list) |
| CandidateDetail | `checkpoints[].path` | path -> checkpoint id + artifact_id (WP-4A list) |
| CandidateDetail | `audit.execution_configuration.path` | path -> audit.execution_configuration.artifact_id |

## 3. 추가

경로: `GET /api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions`, `GET /api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions/{revision_id}/yaml`, `POST /api/v1/uploads/model-config`. 변경된 기존 경로: `POST /api/v1/structure-check`, `POST /api/v1/training-runs` (upload_id/model_yaml_text/source_sha256/model_name, 404/409/422/507 코드).

새 component: `ModelConfigRevision`, `ModelConfigRevisionList`, `RuntimeManifestStatus`, `UploadResponse`.

선언 멤버 경로 추가 372개(중첩 포함; component별: Block 6, CandidateDetail 132, CandidateEvaluation 47, Error 2, Event 39, GeneratedCandidate 10, Health 27, ModelConfigRevision 8, ModelConfigRevisionList 3, Preset 6, Protocol 9, RegistryEntry 5, Run 6, RunCreated 3, StructureReport 40, TrainingRequest 4, TrainingRunCreated 2, UploadResponse 3, ValidateResponse 8, Worker 12). 최상위 추가 멤버:

| component | member |
|---|---|
| CandidateDetail | `current_rung` |
| CandidateDetail | `generation` |
| CandidateDetail | `search_seed` |
| CandidateDetail | `training_seed` |
| CandidateDetail | `training_status` |
| CandidateEvaluation | `immutable` |
| CandidateEvaluation | `performance_status` |
| CandidateEvaluation | `source_kind` |
| CandidateEvaluation | `training_mode` |
| CandidateEvaluation | `training_status` |
| Error | `core_request_id` |
| Error | `location` |
| Event | `created_at` |
| GeneratedCandidate | `architecture_hash` |
| GeneratedCandidate | `architecture_path` |
| GeneratedCandidate | `attempt` |
| GeneratedCandidate | `blocks` |
| GeneratedCandidate | `generation` |
| GeneratedCandidate | `parents` |
| GeneratedCandidate | `search_seed` |
| GeneratedCandidate | `seed` |
| GeneratedCandidate | `training_seed` |
| GeneratedCandidate | `training_status` |
| Health | `core` |
| Health | `gateway` |
| Health | `request_id` |
| Health | `source_tree` |
| Health | `warnings` |
| ModelConfigRevision | `architecture_hash` |
| ModelConfigRevision | `artifact_id` |
| ModelConfigRevision | `canonical_hash` |
| ModelConfigRevision | `created_at` |
| ModelConfigRevision | `kind` |
| ModelConfigRevision | `parent_revision_id` |
| ModelConfigRevision | `revision_id` |
| ModelConfigRevision | `source_sha256` |
| ModelConfigRevisionList | `candidate_id` |
| ModelConfigRevisionList | `revisions` |
| ModelConfigRevisionList | `run_id` |
| Preset | `description` |
| Preset | `execution_mode` |
| Preset | `id` |
| Preset | `name` |
| Preset | `namespace` |
| Preset | `purpose` |
| Protocol | `resolution` |
| Run | `final_retrain_runs` |
| Run | `last_seq` |
| RunCreated | `created` |
| RunCreated | `kind` |
| RunCreated | `protocol_hash` |
| StructureReport | `model_id` |
| StructureReport | `sandbox` |
| UploadResponse | `bytes` |
| UploadResponse | `source_sha256` |
| UploadResponse | `upload_id` |
| Worker | `batch_seconds` |
| Worker | `device_id` |
| Worker | `epoch_seconds` |
| Worker | `images_per_second` |
| Worker | `loss` |
| Worker | `losses` |
| Worker | `native_metrics` |
| Worker | `runner_id` |
| Worker | `runtime_manifest_status` |
| Worker | `samples_per_second` |
| Worker | `throughput` |
| Worker | `train_loss` |

`Preset`의 추가 6개는 allOf → 평탄화(PresetSummary 멤버 + protocol; 의미 동일)이고, 나머지 추가는 닫힌 객체가 기존 PUBLIC 멤버를 잃지 않도록 선언한 것(서버가 이미 보내던 키)과 WP-4A 목록의 additive 항목이다.

## 4. additionalProperties:false

투영 대상 component(최상위 닫힘, `x-public-projection.projected_components`): `Artifact`, `Block`, `CandidateEvaluation`, `Generation`, `Run`, `Worker`, `Event`, `Snapshot`, `Health`, `ValidateResponse`, `RunCreated`, `Error`, `PresetSummary`, `Preset`, `PresetList`, `RunList`, `CandidateDetail`, `RegistryEntry`, `RegistryList`, `GeneratedCandidate`, `TrainingRunCreated`, `StructureReport`, `BlockCatalog`, `ModelConfigRevision`, `ModelConfigRevisionList`, `UploadResponse`.

중첩 닫힘: Block.params, CandidateEvaluation.raw_metrics, CandidateDetail.lineage/audit(하위 config_estimate, proposal, generation, orchestration(+rungs.*, admission, final, training_seed_policy), execution_configuration, structure_history[]), CandidateDetail.checkpoints[](prune, deleted[]), StructureReport.lineage/sandbox, Health.allowlists/capabilities.search_space/versions.environment_lock/source_tree/core/gateway, ValidateResponse.capabilities.search_space(+probe), Event log payload, Run.final_retrain_runs[], Protocol.console.search.search_space_resolved, Protocol.resolution(+paths.{}). propertyNames 거부 목록(열린 채 유지): RegistryEntry.evidence(+config_ref/weights/build/pretrained_load/source), StructureReport.blocks[], Run.budget, raw_metrics.externalised_detail. 투영 비대상(열린 채): Protocol(사용자 입력), Archive*(gateway 로컬 아카이브, DD-A19), 요청 스키마(이미 닫힘).

## 5. 검증 결과

| 검사 | 결과 |
|---|---|
| allowlist 매핑 불변식(OWNER_ONLY 미허용 / PUBLIC 미배제) | 위반 0 |
| 구조 자가검사(루트 멤버, $ref 해소, 2020-12 meta-schema, pattern, 경로 파라미터, operationId, 응답 description, 금지 문자열) | 문제 0 (schema 객체 2821개, $ref 280개); 1.4.3 기준선 문제 1: forbidden substring 'MBNAS_server' present |
| 행동 표본(닫힘/제거/추가가 실제로 검증기에서 작동) | 38/38 기대대로 |
| 서버 생성 web fixture(StructureReport 문서 12) | 1.4.3에서 유효한 문서는 1.5.0에서도 유효: True |
| audit fixture(ETRI mixed) | 1.5.0 오류 = OWNER_ONLY 키만(config_estimate.rules/selection_policy, etri_audit_model) |
| 재생(byte 동일) | True |

외부 검증(보고서 본문에 명령과 결과): Redocly openapi-core 1.34.20 lint(struct 등) 0 error/0 warning — 음성 대조(고의 손상본) 4 error 검출; openapi-typescript 7.13.0 생성 exit 0; 생성 타입 + 사용 프로브 tsc 5.9.3 --strict exit 0(제거 멤버는 @ts-expect-error로 부재 확인). openapi-spec-validator는 runtime/venv·wheelhouse에 없음 → 미사용(확인 못 함).


## 6. pydantic 거울(mbnas_contracts/schemas/*) 변경 목록 — WP-4B/4C 몫(이번에 수정하지 않음)

`tests/owner/unit/test_schemas_contract.py`는 component마다 모델 존재(`CONTRACT_MODELS`), 계약 required ⊆ 모델 required,
계약 최상위 properties ⊆ 모델 properties, enum 동일, additionalProperties 정책을 검사한다. 승격 시 필요한 변경:

| file:class | field / 설정 | 변경 |
|---|---|---|
| schemas/records.py:Block | `old_name`(required), `before`, `after`, `repair_reason` | 삭제; `params` 닫힌 모델(from, repeats, module, args, scope, effective); `model_config` extra=forbid |
| schemas/enums.py:BlockSourceKind | `REPAIR` | 삭제(계약 Block.source_kind enum과 동일 검사, test_schemas_contract 131행) |
| schemas/records.py:Generation | `group_key` | 삭제; extra=forbid |
| schemas/records.py:Worker | `pid`, `lease_expires_at` | 삭제 |
| schemas/records.py:Worker | `runner_id`, `device_id`, `runtime_manifest_status`, `loss`, `train_loss`, `images_per_second`, `samples_per_second`, `throughput`, `epoch_seconds`, `batch_seconds`, `native_metrics`, `losses` | 추가(선택); `device_uuid` deprecated; extra=forbid |
| schemas/enums.py (신규) | `RuntimeManifestStatus` | verified / mismatch / unknown StrEnum + `CONTRACT_MODELS` 등록(문자열 enum component라 `test_required_fields_exist_and_are_required`의 `js["properties"]` 가정을 비-object component에서 건너뛰게 조정 필요) |
| schemas/records.py:CandidateEvaluation | `training_status`, `immutable`, `source_kind`, `training_mode`, `performance_status` | 추가(선택); `raw_metrics` 허용목록 모델 또는 dict 유지(계약 검증으로 강제); extra=forbid |
| schemas/records.py:GeneratedCandidate | `training_status`(required, UNTRAINED\|TRAINED), `generation`, `parents`, `architecture_hash`, `search_seed`, `training_seed`, `seed`, `attempt`, `blocks`, `architecture_path` | 추가; extra=forbid |
| schemas/records.py:CandidateLineage | `crossover`, `mutations`, `repairs` | 삭제; `method`, `parents`, `parent_evaluation_ids`, `template_detail`, `search_space`, `source_*`, `rank`, `selection_rule`, `init`, `recipe`, `source_template`, `training_source`, `source_architecture_hash` 추가; extra=forbid |
| schemas/records.py:CandidateDetail | `training_status`, `current_rung`, `search_seed`, `training_seed`, `generation` | 추가(선택); `checkpoints` 항목 모델(path 없음, `artifact_id`), `audit` 공개 부분집합 모델 또는 dict; extra=forbid |
| schemas/records.py:Run | `last_seq`, `final_retrain_runs` | 추가; extra=forbid |
| schemas/records.py:Event | `created_at` | 추가; log payload 허용목록(선택: `_EVENT_PAYLOAD_MODELS[LOG]`); extra=forbid |
| schemas/records.py:StructureReport | `model_id`, `sandbox` | 추가; `lineage` 공개 lineage 모델; extra=forbid |
| schemas/records.py:Artifact, Snapshot, BlockCatalog | — | extra=forbid |
| schemas/api.py:Health | `warnings`, `source_tree`, `core`, `gateway`, `request_id` | 추가; extra=forbid; `Allowlists` extra=forbid |
| schemas/api.py:RegistryList | `registry_roots_skipped` | 삭제(owner 진단 → /core/v1/owner/*); extra=forbid |
| schemas/api.py:RegistryEntry, ValidateResponse, PresetSummary(→Preset), PresetList, RunList | — | extra=forbid |
| schemas/api.py:RunCreated | `protocol_hash`, `created`, `kind` | 추가; extra=forbid |
| schemas/api.py:Error | `location`(gateway\|core\|runner\|store = `errors.ErrorLocation`), `core_request_id` | 추가; extra=forbid |
| schemas/api.py:TrainingSource | `upload_id`, `model_yaml_text`, `source_sha256`, `model_name` | 추가 + user 규칙(정확히 하나: upload_id / model_yaml_text+source_sha256 / model_path; 새 필드는 user 전용) — core `training_runs.selection.TrainingSource`는 upload_id 외 3개를 이미 가짐 |
| schemas/api.py:TrainingRunCreated | `source` | 응답용 binding 모델(kind, run_id, candidate_id, baseline_id, model_path, source_sha256, model_name). 현재 core `POST /training-runs`는 `contract_json`을 거치지 않고 `req.source.binding_dump()`를 그대로 JSONResponse로 보내므로(거울 검증·`project_public` 모두 우회) 업로드 학습 응답의 source_sha256/model_name이 1.4.3 계약(닫힌 source)에는 위반이었다 — 1.5.0이 선언; WP-4B는 이 응답도 투영 경로로 보낼 것 |
| schemas/protocol.py:Protocol | `resolution` | 추가(계약 최상위 property 1:1 검사 대상) |
| schemas/protocol.py:BaselineSelection | `name`, `source` | 추가(선택) |
| schemas/api.py (신규) | `UploadResponse`, `ModelConfigRevision`, `ModelConfigRevisionList` | 닫힌 모델 + `CONTRACT_MODELS` 등록 |

## 7. 교차 패키지 요청

- WP-4B(core): `x-public-projection` 규칙대로 투영 함수(콘솔 응답·SSE·export 공통); validator가 새 검증부터 `search_space_resolved.etri_root`·`resolution.paths.*.realpath`를 쓰지 않게 하고 `mbnas_contracts/protocol_hash.py` `RESOLUTION_HASH_EXCLUDED`에 `resolution.paths` 추가(WP-4 §1.2; contracts 파일이지만 openapi/** 밖이라 미수정); 저장된 1.4.x resolved protocol의 null etri_root·realpath는 투영에서 제거(저장 행·기존 hash 불변); `training_status` 계산; runner_id/device_id 발급; `RegistryList.source`·`capabilities.registry_source` 라벨화; Block repair 접기; checkpoint `artifact_id`; config_revisions 테이블과 두 GET 경로; 사용자 모델 block 행(StructureReport 유래 키)은 닫힌 Block에서 module/from/args가 params 밖이라 빠짐 → params로 옮겨 투영할지 결정 필요.
- WP-3B/5B(gateway): `/runs/{id}/candidates/{cid}/config-revisions`·`.../{revision_id}/yaml` 중계 라우트(바이트 그대로, `X-Artifact-Sha256`); Health 병합 키(core, gateway, request_id)는 계약에 선언됨.
- WP-4C(tests): test_schemas_contract 갱신(additionalProperties 정책: Run·CandidateEvaluation·RegistryEntry 닫힘, BlockSourceKind, registry_roots_skipped 543-551행, 신규 4 component), test_contract_history에 1.5.0 이력·전용 검사 추가, canary 투영 테스트.
- WP-4D/6C(web): `npm run gen:types` 재생(생성 자체는 exit 0 확인), contract.test.ts fixture 갱신(Worker pid, Generation group_key, Block old_name 등 1.5.0에서 거부), CandidateView의 old_name/before/after/repair 열 제거.
- WP-7(scripts/dev/check_import_closure.py 소유자): 승격 후 `DATA_EXCEPTIONS`의 openapi.json `MBNAS_server` 예외 삭제(1.5.0 info.description에 없음, 초안 forbidden 0건 확인); 패키저는 `openapi/drafts/`를 공개 ZIP에 넣을지 결정(내용은 공개 계약 초안, host fact 0건).
- docs/public/contracts/history/HISTORY.json 소유자: 승격 시 패키지 HISTORY.json과 note 정정문·1.5.0 항목 동기화.

## 8. 하지 않은 것 / 확인 못 함

- live `openapi.json` 교체, pydantic 거울·core·web 수정, `docs/public/contracts/history/` 쓰기: 지시에 따라 하지 않음.
- WP-4 §1.5 export public 프로파일 파라미터, §1.6 openapi_runner.json 이력 등록, Health.devices·device_resolved uuid 치환 스키마화, CandidateEvaluation 경로 멤버의 id 필드 추가: DD-A38 최소형 범위 밖으로 보고 설명문 규칙만 둠.
- owner/public 테스트 스위트를 1.5.0 초안으로 돌린 영향 측정: 확인 못 함(계약 경로가 패키지 데이터로 고정되어 있어 교체 없이는 불가).
- openapi-spec-validator: runtime/venv·wheelhouse에 없음 → 미사용; 대신 Redocly lint(음성 대조 포함)와 2020-12 meta-schema 검사.
- `audit`/`raw_metrics`/log payload 허용목록은 1.4.1 기준 분석 + 현재 코드 grep으로 보강했으나 모든 생산 경로를 실행으로 확인하지는 않음(투영이 알려지지 않은 공개 키를 떨어뜨릴 수 있음).

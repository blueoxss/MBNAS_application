# DTO ALLOWLIST PROPOSAL (WP-0 / A2) — core 호스트 밖으로 나가도 되는 필드 분류

작성: 2026-10-23, 분석자 A2. 입력은 `build/source_snapshot` (HEAD 18c4815, 2026-10-23 21:07 KST 사본)만 읽었고 live `etri_nas`는 건드리지 않았다. 인용 표기 `S/<file>:<line>` = `application/MBNAS_server/src/mbnas_server/<file>:<line>`, `W/` = `application/MBNAS_web/src/`, `C` = `application/MBNAS_server/contracts/openapi.json`(1.4.1, components 96개 확인). 기계 판독본은 같은 폴더의 `DTO_ALLOWLIST_PROPOSAL.json`(969 항목: PUBLIC 817 / PUBLIC_MAPPED 104 / OWNER_ONLY 48; openapi 96 components 전부 + `ExportBundle`, `PublicIdentifiers`, `_meta` 의사 component).

## 0. 결론 먼저

1. 기존 `/api/v1` 응답은 **DB 행을 거의 그대로 내보낸다**. `api.contract.prune`는 null만 지우고 계약에 없는 추가 키는 통과시키므로(S/api/contract.py:94-101) `candidates.audit_json`·`lineage_json`·`raw_metrics`·`checkpoints` 행이 통째로 고객에게 도달한다(S/api/routes_runs.py:277-285). 투영(DD-A3)은 이 네 곳에서 가장 크다.
2. 지침 §8의 private 항목 중 **prior 테이블, parent pool, search_state, 해상된 private 정책(proposal_policy), RNG 스트림 키**는 이미 API 밖(DB `jobs.spec_json/result_json`)에만 있다(S/orchestration/engine.py:297-337, S/search/handlers.py:229-235; export `jobs.json`도 spec을 빼고 쓴다 S/export/bundle.py:112-116). 분할 후에도 owner DB에 남기면 된다. 반면 **repair/mutation/crossover trace, prior draw, selection record, space_hash/prior_hash**는 `CandidateDetail.lineage`와 `Block.old_name/before/after/repair_reason`, 그리고 `CandidateDetail.structure.lineage`(복사본, S/api/model_services.py:228)와 `export lineage.json`으로 **지금 그대로 나간다** → OWNER_ONLY.
3. 호스트 내부 노출 경로가 광범위하다: 절대경로(`checkpoint_path`, `architecture_path`, `checkpoints[].path`, `raw_metrics.*_path`, `Health.allowlists`, `capabilities.search_space.root`, registry `evidence.*.path`, preset `console.paths`, export manifest의 이미지 경로), 프로세스 식별자(`Worker.id = hostname:pid`, `Worker.pid/child_pid`), GPU UUID, SSE `log` 메시지의 경로/PID. 모두 PUBLIC_MAPPED(치환) 또는 OWNER_ONLY.
4. **resolved_protocol은 통째로 공개될 수밖에 없다**(ValidateResponse.resolved를 웹이 그대로 echo하여 `protocol_hash` 고정점을 검증, S/protocol/validator.py:172-182, S/api/routes_runs.py:99-108). 그 안의 서버 추가 필드 3개(`resolution.paths.*.realpath`, `console.search.search_space_resolved.etri_root`, `console.benchmark.device_resolved.uuid`)는 hash에 포함되어 있어 **계약 변경 없이는 제거할 수 없다**. 이것이 DD-A5와 충돌하는 유일한 지점이다(결정 D3).
5. 식별자: `run_id/job_id/validation_id/checkpoint id/train-/model-`는 uuid 유래로 이미 opaque, `artifact_id`는 sha256 유래. `candidate_id`·`evaluation_id`는 구조적이지만 구성 요소가 전부 공개 필드(arm/seed/generation/index/rung)라 매핑 이득이 없다. **매핑이 꼭 필요한 것은 `worker_id`(hostname:pid)와 GPU `device_uuid`뿐**이며 runner 등록 시 core가 발급하는 `runner_id`/`device_id`로 대체하면 매핑 테이블이 필요 없다(결정 D7/D11).

## 1. 분류 규칙과 적용 지점

| 분류 | 뜻 | 적용 |
|---|---|---|
| PUBLIC | 값 그대로 gateway/web/runner로 | 측정 결과, 결정 코드, 사용자 작성 protocol, gene/YAML, 구조 검사 결과, opaque id |
| PUBLIC_MAPPED | 전달하되 core 투영에서 변환 | 절대경로→artifact/checkpoint id, hostname:pid→runner_id, GPU UUID→device id, URL→gateway 경로, 자유 문자열 scrub(절대경로·PID 정규식; `search/structure.py:64-68`의 `<server-path>` 치환을 공통 유틸로 승격), grab-bag 객체→하위 키 허용목록 |
| OWNER_ONLY | `/core/v1/owner/*`에서만 | 정책 trace(repair/mutation/crossover/prior draw/selection), 정책 설명 문장(admission policy, audit rules), 내부 키(group_key, proposal_job_id, ledger id), 호스트 진단(registry_roots_skipped, traceback), 추정기 내부 |

투영은 **core의 console 응답 조립 단계**(DD-A3)와 **SSE 이벤트 payload**(동일 행이 `candidate.updated/evaluated`, `generation.*`, `run.updated`, `worker.updated`로 그대로 나감: S/orchestration/engine.py:242,578,728,732,256; S/worker/main.py:203) 양쪽에 같은 함수로 적용해야 한다. 저장 행은 바꾸지 않는다(불변 행 원칙, S/db/repo.py:249-259).

## 2. 컴포넌트별 요약

| component / 경로 | 생산 코드 | 핵심 판정 |
|---|---|---|
| `CandidateEvaluation` (snapshot.candidates, detail.evaluations, SSE candidate.*) | base_row S/orchestration/engine.py:205-233, commit S/…:529-582, view S/api/views.py:32-54 | 측정·fitness VALUE·gate·weights PUBLIC; `checkpoint_path`/`architecture_path` MAP; `raw_metrics` MAP(허용목록, §3.1); `blocks` Block 규칙 |
| `Block` | S/search/lineage.py:26-50 | name/position/params(행)/effective/source/parent_block_id/mutated PUBLIC; `old_name/before/after/repair_reason` OWNER_ONLY; `source_kind/operation`의 `repair` 값 접기; `params.slot_kind` OWNER_ONLY |
| `CandidateDetail.lineage` | S/search/lineage.py:53-68, view S/orchestration/views.py:71-88 | operation/method/parents/parent_evaluation_ids/template(id)/search_space.version·variant PUBLIC; crossover/mutation_events/repair_events/prior_draw/selection/parent_rows/elite/notes/space_hash/prior_hash OWNER_ONLY |
| `CandidateDetail.audit` | 조립 S/orchestration/engine.py:784-795, 290-292; S/search/generation.py:170-178; S/training/selection.py:349-354 | §3.3 표 |
| `CandidateDetail.checkpoints[]` | S/orchestration/retention.py:46-54 (행 전체) | `path`, `prune.deleted[].path` MAP/제거; 나머지 PUBLIC |
| `CandidateDetail.structure` / `StructureReport` | S/api/model_services.py:174-241, S/search/structure.py:180-191,455-457 | 검사 결과 PUBLIC; `lineage` 복사본·`configuration.path`·blocks 출처 키에 동일 규칙; `observation.parser_source_sha256`·`catalog.source_path`(site-packages 상대, S/search/block_catalog.py:330-359) PUBLIC(D16) |
| `Generation` | S/scheduling/planner.py:116-120 | 카운트·대표 id·규칙 문장 PUBLIC; `group_key` OWNER_ONLY(D14) |
| `Run` | S/orchestration/views.py:18-36 | `budget.policy` OWNER_ONLY; `resolved_protocol` §3.5; costs/finalists/cancel PUBLIC |
| `Worker` / SSE worker.updated | S/worker/main.py:176-191 | `id` MAP(runner_id); `pid/child_pid/lease_expires_at` OWNER_ONLY; `device_uuid` MAP(D7) |
| `Event[log]` | S/orchestration/engine.py:250-251 + 호출 27곳(§3.7) | 구조화 키 PUBLIC, `message/message_detail/checks` scrub, `path` 제거 |
| `Health` | S/api/routes_meta.py:55-70 | `allowlists`(D8), `devices`(D7), `capabilities.search_space.root` OWNER_ONLY, `versions.environment_lock.path` OWNER_ONLY, `registry_source`/`warnings` 라벨화 |
| `RegistryEntry` | S/registry/__init__.py:377-411 | 선언·상태 PUBLIC; 모든 `evidence`/`probe_file`/`weights.path`/`config_ref.path`/`source.record`/`source.notebook` 경로와 `build.trace`, `registry_roots_skipped` OWNER_ONLY 또는 증거 id로 치환 |
| `ValidateResponse` / `Error` | S/protocol/validator.py:148-152, S/api/errors.py:27-33 | 메시지에 realpath·allow roots 목록(S/util/paths.py:96-107) → scrub; `capabilities.search_space.etri_root` OWNER_ONLY; `capabilities.baselines.{id}.capabilities`는 RegistryEntry 규칙 |
| `Preset(.protocol)` | S/protocol/presets.py:38-46,62-66 | 본문 PUBLIC(DD-A8), `console.paths` 예시가 owner 호스트 경로 하드코딩 → 배포 설정값으로(D12) |
| `Archive*` (61개) | S/archive/views.py | 측정값 PUBLIC; `path/run_dir/best_ckpt/checkpoint path` MAP; `mutation`/`stop_rule/transition/orphan_check` OWNER_ONLY; 모듈 자체 공개 여부 D9 |
| Export zip | S/export/bundle.py:80-135 | §3.9 표 |

## 3. 상세

### 3.1 `CandidateEvaluation.raw_metrics` — grab-bag 분해

`commit_evaluation`은 평가 payload에서 `METRIC_KEYS`·`RAW_EXCLUDED`를 뺀 **나머지 전부**를 `raw_metrics`에 넣는다(S/orchestration/engine.py:506-508, 525-526, 546-548). 평가 payload 키는 S/evaluation/handlers.py:133-158과 S/training/handlers.py:208-216에서 나온다. 웹은 `raw_metrics`를 JSON 그대로 보여준다(W/components/CandidateView.tsx:342).

| raw_metrics 하위 키 | 분류 | 근거 |
|---|---|---|
| ap_50, counts, per_class_ap, per_class_recall, no_predictions, invalid_reason, coco_stats, evaluation_signature, latency_scope_base, postprocess_operating_point, postprocess, eval_split, size_dtype, size_definition, weights_only_sha256, eval_checkpoint_sha256, epochs, timings, evaluated_at, device_name, eval_batch, train_batch, global_step, samples_seen, epoch_target_total, checkpoint_sha256s, checkpoint_sha256_by_kind, non_finite_metrics, fitness_rung, handler_fitness, validation_enabled | PUBLIC | 측정값/정의 |
| weights_only_path, eval_checkpoint_path | OWNER_ONLY | runner 호스트 절대경로 (S/evaluation/handlers.py:146,149) |
| device_uuid | MAP | GPU UUID (S/evaluation/handlers.py:147) |
| latency (전체 레코드) | MAP | scopes 통계 공개, `manifest` 안 uuid/driver 치환 (S/evaluation/handlers.py:121-127) |
| resolved_recipe | MAP | adapter가 확정한 recipe; 사용자 recipe 유도값은 공개, adapter 내부 키는 제거 — 내용은 adapter별이라 전수 확인 못 함 |
| rung_result (명시 학습 validation OFF 행) | OWNER_ONLY | `RungResult.as_dict()` 전체: ckpt/eval/weights 절대경로, resumed_from, notes에 경로 (S/training/selection.py:419, S/adapters/base.py:127-152) |
| artifact_problems, contract_error, row_error, reported | OWNER_ONLY | 서버 내부 오류 문자열(항목 repr에 경로 가능) (S/orchestration/engine.py:489,559,576,631,641) |

권고: 투영에서 `raw_metrics`를 위 PUBLIC 키의 **허용목록 부분집합**으로 재조립한다(D4).

### 3.2 `Block`와 `lineage` — 어디까지가 "후보 provenance"이고 어디부터 "정책 trace"인가

- `blocks_for`는 행마다 `origin.kind`(parent_a/parent_b/random/mutation/repair), `before/after`, `reason`을 쓴다(S/search/lineage.py:31-49). `repair_reason`은 `illegal_reason`의 규칙 문장(예: `C2PSA requires c1 == c2`, `token budget exceeded`, S/search/space.py:176-199)으로 **private 구조 gate 규칙 본문**이다. `before/after/old_name`은 변이·수리 전후 행이라 trace다.
- `source/source_kind(parent_a|parent_b)/parent_block_id/mutated`는 "이 후보의 행 i가 어느 부모에서 왔는가"라는 후보별 사실이며, 웹 Lineage 화면의 핵심 표시다(W/screens/Lineage.tsx:2-3, 54; W/components/CandidateView.tsx:36). 다만 `source_kind=repair`·`operation=repair`가 남아 있으면 "몇 번째 행이 수리됐다"는 사실이 새므로 공개 투영에서 `repair`→부모 출처(reference_sources, S/search/space.py:447)로 접는다.
- `lineage.crossover.{cut_index,mask}`는 Block의 parent_a/parent_b 열과 정보가 같다(S/search/ga.py:131-140). 따라서 (a) Block 출처를 공개로 두면 lineage.crossover를 감춰도 one-point 절단점은 유도 가능하다. 그래도 연산자 명세(cut/mask/`rows`), 토너먼트 기록(`selection`, S/search/ga.py:101-108,124-129), `prior_draw`(템플릿·슬롯 draw 구조, S/search/space.py:475), `mutation_events`(choice grid `{op,repeats,width}` S/search/space.py:491-493), `repair_events`(S/search/space.py:445-446), `elite`(S/search/ga.py:168-169), `notes`(S/search/ga.py:143), `parent_rows`(부모 fitness/latency S/search/ga.py:110-113), `space_hash/prior_hash`(S/search/space.py:523-524)는 OWNER_ONLY로 둔다. 남는 공개 lineage: `operation, method, parents, parent_evaluation_ids, template(id), search_space.{version,variant}`, final/baseline/명시학습 계보 키(S/orchestration/final_retrain.py:252-257, S/training/selection.py:346-348).
- **같은 lineage 객체가 세 경로로 나간다**: `CandidateDetail.lineage`(view 변환 후), `CandidateDetail.structure.lineage`/`audit.structure.lineage`(원본 복사, S/api/model_services.py:228, S/search/structure.py:190), `export lineage.json`(S/export/bundle.py:104-107). 세 곳 모두 같은 투영 함수를 거쳐야 한다.
- `params.slot_kind`(down|feature|fixed, S/search/lineage.py:42)는 탐색 공간 슬롯 정의(prior 테이블의 축, S/search/space.py:223-235)를 행마다 드러내므로 OWNER_ONLY.

### 3.3 `CandidateDetail.audit.*`

`audit`은 `candidates.audit_json` 전체가 그대로 나간다(S/api/routes_runs.py:285). 키 출처: 제안 S/orchestration/engine.py:784-795(`proposal` extras + `orchestration`), `audit_for` S/search/lineage.py:71-83, 생성 전용 S/search/generation.py:170-178, baseline S/orchestration/engine.py:290-292, final S/orchestration/final_retrain.py:258-263, 명시 학습 S/training/selection.py:349-354, rung 상태 S/scheduling/planner.py:41-54, 실행 구성 S/training/model_artifacts.py:55-62, 구조 검사 S/api/routes_runs.py:297-312,327-328.

| 키 | 분류 | 사유 |
|---|---|---|
| origin, label, performance, params, flops, imgsz, architecture_hash_scheme, structure_signature_sha256, search_space_variant | PUBLIC | 라벨/측정/해시 스킴 |
| config_estimate.{backbone_family, *_operator_counts, *_attention_rows, detect_rows, head_preserved, label, scope, proof_of_model_family} | PUBLIC | YAML에서 센 통계(웹 AuditPanel, W/components/CandidateView.tsx:194-216) |
| config_estimate.rules, config_estimate.selection_policy | OWNER_ONLY | "ETRI mixed_search_space.audit_config (ported)", "single mixed pool; no family quotas" (S/search/audit.py:64-65) |
| built.* | PUBLIC | CPU 빌드 연산자 감사(S/search/audit.py:101-124) = 구조 검사 결과 |
| etri_audit_model | OWNER_ONLY(잠정) | ETRI `mixed_blocks.audit_model` 출력(S/search/space.py:336-337); 내용 구조 확인 못 함 → D15 |
| fallback_reason | MAP | 경로/예외문 포함(S/search/etri_bridge.py:296-304) → 코드화 |
| structure | MAP | StructureReport 규칙 |
| structure_history[] | PUBLIC | DD-7 이력(checked_at, yaml_hash, status, observation, archived_at) |
| proposal.genotype / yaml_hash / params / flops | PUBLIC | gene(=architecture_yaml), 해시, 카운트 (S/search/handlers.py:187-188) |
| generation.{index, seed, requested_method, effective_method, duplicate_scope, execution} | PUBLIC | DD-2/DD-6 기록 |
| generation.attempt | PUBLIC(D6) | 고객 run의 시도 수(웹 Monitor가 같은 통계를 run.budget에서 표시, W/screens/Monitor.tsx:263-265) |
| generation.worker_id | MAP | hostname:pid (S/search/generation.py:48-51) |
| orchestration.{baseline_id, comparison_group, init, requested/resolved_input_shape, index, invalid_reason} | PUBLIC | 실행 메타/INVALID 코드 |
| orchestration.proposal_job_id | OWNER_ONLY | 내부 연결 키 (S/orchestration/engine.py:792) |
| orchestration.rungs.{rung}.{state, evaluation_id, gate, reason_code, job_id, performance_status} | PUBLIC | 계획 상태·승급 코드 (S/orchestration/engine.py:375,479,579-580,706) |
| orchestration.rungs.{rung}.admission.{admitted, reason_code, arm, search_seed, cap_s, billed_s, remaining_s} | PUBLIC(D5) | 미승인 사유와 예산 소비(사용자 예산·측정값, S/scheduling/admission.py:52-66) |
| orchestration.rungs.{rung}.admission.{estimate_s, estimate_source, reserved_s} | OWNER_ONLY | 추정기 규칙(후보 측정률/finalist 출처/arm 평균, S/scheduling/admission.py:91-103) |
| orchestration.final.{source_*, rank, recipe} | PUBLIC | finalist 출처·pinned recipe |
| orchestration.final.training_seed_policy | MAP | requested/base/base_source 공개, `derivation`·`final_derivation`(RNG 유도식 S/search/streams.py:43-45, S/orchestration/final_retrain.py:156) 제거 |
| execution_configuration.{artifact_id, sha256, yaml_hash, class_count} / .path | PUBLIC / MAP | path는 artifact_id로 충분(S/training/model_artifacts.py:60) |
| training_source, source | PUBLIC | 사용자 선택 binding(model_path 포함; `architecture_hash_verified` DD-10) |

### 3.4 `Generation`, `CandidateDetail.checkpoints`, `cost`, `gate_history`

- Generation 행은 planner가 만든 dict 그대로(S/orchestration/views.py:66-68). `group_key`는 `arm|seed|gen|rung|group`(S/db/repo.py:303-305,319-320)로 공개 필드에서 유도되는 내부 키이며 웹 미사용(W 전체 grep 0건) → OWNER_ONLY/제거. `representative_rule` 문장은 계약 description에 이미 있고 표시용 tie-break라 PUBLIC.
- `checkpoints`는 `checkpoints` 테이블 행 전체에 `state`만 덧붙인다(S/orchestration/retention.py:46-54): `path`(runner 절대경로, S/worker/main.py:441-445), `prune.deleted[].path`(S/orchestration/retention.py:143-146,165-167) → artifact/checkpoint 핸들로 치환(DD-A6). `id`(ck-urandom), `hashes`, `sha256`, `bytes`, `epoch`, `kind`, `state` PUBLIC.
- `cost`(kind별 GPU-s, S/scheduling/costs.py:273-277)와 `gate_history`(S/api/routes_runs.py:282-283) PUBLIC.

### 3.5 `Run.resolved_protocol` — 서버가 추가하는 필드 목록

사용자 본문 외에 validator가 쓰는 키(모두 `protocol_hash`에 포함; 제외는 `console.label`, `resolution.validated_at`, `console.retention`뿐 S/protocol/validator.py:71):

| 서버 추가 필드 | 위치 | 분류 |
|---|---|---|
| status(RESOLVED_*), original_status | S/protocol/validator.py:889-893 | PUBLIC |
| resolution.{canonicalization, hash_excluded, validated_at, validator} | :894-896 | PUBLIC |
| resolution.paths.{key}.requested / .realpath | :897-898 (realpath는 :784) | PUBLIC / **MAP(D3)** — 검증 호스트의 심링크 해소 경로 |
| console.{execution_mode, preset, purpose, run_namespace} 기본값 | :282-289 | PUBLIC |
| rungs[].epochs_added | :346-348 | PUBLIC |
| training.{fixed_horizon_epochs, effective_batch, nbs} | :429-433 | PUBLIC |
| training.backend_resolved.ultralytics | :842-865 | PUBLIC (공개 라이브러리 규칙의 유도값) |
| console.search.crossover_rate(기본 0.5), allow_native_fallback | :555-562, :505-512 | PUBLIC (사용자 값/문서화된 기본) |
| console.search.search_space_resolved.{variant, etri_root} | :528-530 | PUBLIC / **OWNER_ONLY(D3)** — `/root/detection/ETRI_NAS` 등 owner 경로; DD-A2 vendoring 후 의미 없음 → pool 버전 라벨로 대체 |
| 생성 전용 console.search.* 기본값 | :965-1006 | PUBLIC |
| console.baselines[].resolved, console.baseline_override | :593-596, :653-665 | PUBLIC |
| console.benchmark.device_resolved.{id, uuid, name} | :686-690 | id/name PUBLIC, **uuid MAP(D3/D7)** |
| console.benchmark.postprocess, console.retention | :699-720, :722-739 | PUBLIC |
| dataset.*_hash, dataset.counts | :834-838 | PUBLIC |
| console.extra.training (명시 학습) | S/training/selection.py:216-231,308-310 | PUBLIC(arch.weights_path가 registry 가중치 디렉터리면 MAP) |

`console.search`의 `population/generations/mutation_rate/seeds/budget_gpu_hours`와 `proposal.GA.parent_selection_fidelity` 등은 사용자가 쓰는 본문이다(KNOWN_KEYS S/protocol/validator.py:89-119). "private computeFitness parameters that are NOT user-authored"에 해당하는 서버 상수는 `DEFAULT_CROSSOVER_RATE`(resolved에 기록됨, 문서화된 기본값), `TOURNAMENT_SIZE/elitism`(S/search/ga.py:27-28; resolved에 없음, `lineage.elite`·job result `ga` 기록에만), `PRIOR_NUM_DRAWS/DEPTH_CHOICES/WIDTH_CHOICES/TOKEN_BUDGET`(S/search/space.py:60-63; `describe()`에만 → DB jobs.result) 이다. 즉 API로 나가는 private 상수는 `elite` 플래그와 Block/lineage trace뿐이다.

### 3.6 `Health`, `RegistryEntry`, `ValidateResponse.capabilities`

- Health 본문(S/api/routes_meta.py:63-69): `allowlists`(dataset/output/archive 루트 절대경로 — 웹 Settings가 사용자 안내로 표시 W/screens/Settings.tsx:112-140), `capabilities.search_space.root`(ETRI 루트), `capabilities.registry_source`(entry point 이름 또는 예시 registry 파일 절대경로 S/api/services.py:74,79), `versions.environment_lock.path`(서버 소스 트리 경로 S/api/services.py:90), `devices[]`(NVML uuid/aliases/memory + worker_id S/api/routes_meta.py:60-61, S/provenance/env_lock.py:99-106), `warnings`(예외문). `source_tree`는 `{algorithm, sha256, files}`만 뽑아 절대경로 `roots`를 빼고 있다(S/api/routes_meta.py:66) — 좋은 선례.
- RegistryEntry(S/registry/__init__.py:377-411): 선언 capabilities와 상태 코드는 PUBLIC. 경로를 담는 키: `capabilities.{train,val,full_state_resume,export_onnx,export_trt,devices.*}.evidence`(S/registry/__init__.py:168,343,263,273,391), `evidence.probe_file`(:402), `evidence.config_ref.path`(S/registry/probe.py:61,70,78), `evidence.weights.path`(:101-115), `evidence.pretrained_load.loaded_from`(:180), `evidence.source.record`/`.notebook`(S/registry/__init__.py:300-301,328), `evidence.build.trace`(traceback S/registry/probe.py:40), `reason`에 덧붙는 skipped roots(:369-372), `evidence.registry_roots_skipped`(:405-407). 증거 경로는 "증거 id(파일명+sha256)"로 치환하면 검증 가능성을 유지한다.
- ValidateResponse.capabilities.baselines.{id}.capabilities는 registry capabilities 전체를 실어 보낸다(S/protocol/validator.py:596) → RegistryEntry 규칙을 재사용.

### 3.7 SSE `log` payload 인벤토리

`engine.log`는 `{message, level, **payload}`를 그대로 이벤트로 쓴다(S/orchestration/engine.py:250-251). 호출 지점과 키:

| 호출 | 키 | 위험 |
|---|---|---|
| run created S/api/routes_runs.py:170 | validation_id, request_id | 없음 |
| structure check S/api/routes_runs.py:330 | candidate_id, structure_status | 없음 |
| requeue/finished/failed S/orchestration/engine.py:426,459,463 | job_id, reason_code, attempt, paused | 없음 |
| 아키텍처 hash 불일치 :613-615 | candidate_id, reason_code, 두 hash | 없음 |
| 예산 소진/제안 실패/빈 제안 :743-744,753-754,771-772 | arm, search_seed, generation, reason_code, **message_detail**(handler message: RESUME_BLOCKED 등은 ckpt 디렉터리 절대경로 포함 S/training/stage_trainer.py:180-186) | scrub |
| 자식 run 상태 :850-851 | child_run_id, child_status, reason_code | 없음 |
| 생성 artifact 등록 실패 :960 | candidate_id, reason_code | 없음 |
| checkpoint 거부 S/worker/main.py:437-439 | job_id, **path**(절대), reported/computed sha | path 제거 |
| GPU 대기 노트 S/worker/main.py:160-171 | message에 PID 목록, gpu_wait_s | scrub |
| lease 상실 :466, retention :484-492 | job_id/candidate_id/policy/bytes_freed; retention 실패 message에 예외문 | scrub |
| overshoot S/scheduling/costs.py:182-186 | arm, seed, job_id, candidate_id, attempt, cap_s, billed_*, overshoot_s | 없음(측정) |
| pause/resume S/orchestration/state.py:65,163,170 | reason(사용자), **checks**(revalidation errors[:20]에 경로 메시지 가능, blocked/exhausted job 목록) | 코드/결과만 |
| final retrain S/orchestration/final_retrain.py:233-239,299 | parent_run_id, request_id, reason, finalists, recipe, selection_rule | 없음 |
| 명시 학습 생성 S/training/selection.py:363 | source(사용자 binding), mode, request_id | 없음 |

권고: `log` payload는 **키 허용목록 + message/message_detail 문자열 scrub**(절대경로 `/…`, `pid=\d+`, hostname) — D13.

### 3.8 `Preset`, `Error`, `ValidateResponse.errors`

- `presets()`는 handoff 설계 예시와 PILOT_SMOKE 본문에 `console.paths`를 `/root/detection/MBNAS_data/pilot_visdrone_v1/...`, `/root/detection/MBNAS_runs/...`로 박아 넣는다(S/protocol/presets.py:38-46,62-66,106-112). DD-A8은 웹이 이 응답을 템플릿으로 쓰게 하므로 예시 경로는 배포 설정(`config/examples`)에서 주입하거나 placeholder(`/ABSOLUTE/...` — 검증기가 PATH_PLACEHOLDER로 거부함 S/util/paths.py:19-20,48-49)로 바꿔야 한다. handoff 본문의 `proposal/allocation/fitness/continuation/comparison` 섹션은 모두 사용자 편집 가능한 policy 본문(KNOWN_KEYS)이라 PUBLIC으로 분류했다(D12에서 재확인).
- Error.errors와 ValidateResponse.errors/warnings의 `message`는 `check_server_path`가 `resolved path <real> is outside the allowed roots [<roots>]`처럼 realpath와 allow roots를 넣는다(S/util/paths.py:96-107); dataset 오류는 sample id 예시를 넣는다(S/protocol/validator.py:821-832). code는 PUBLIC, message는 scrub.

### 3.9 Export zip 구성원

| 멤버 | 분류 | 비고 |
|---|---|---|
| protocol/requested.json, protocol_hash.txt, manifests/hashes.json, README.txt, SHA256SUMS | PUBLIC | |
| protocol/resolved.json | MAP | §3.5 |
| manifests/<split_hash>.json | MAP | 캐시 manifest의 `samples[].image/.label`이 데이터 호스트 절대경로(S/datasets/manifest.py:50-58,237) → sample_id+sha256만 |
| evaluations.csv/.jsonl | MAP | 행 투영(checkpoint_path, job_id, raw_metrics) |
| lineage.json | MAP | lineage/blocks 투영(S/export/bundle.py:104-107) |
| generations.json | MAP | group_key 제거 |
| cost_ledger.csv | MAP | 측정 GPU-s 공개; ledger `id`, `meta.host_row_id`(S/scheduling/costs.py:110), `device_uuid` 제거 |
| checkpoints.json, artifacts.json | MAP | 행 원본(path / relpath+meta 전체) → 각 view 규칙 |
| jobs.json | PUBLIC | spec/params 미포함(S/export/bundle.py:112-116) |
| events.jsonl | MAP | log 투영 |
| provenance.json | MAP | `budget.policy` 제거; source_tree는 이미 3키만(S/api/routes_files.py:98) |
| environment.lock.json | MAP | `api_process.environment_lock.path`, `recorded_environment_lock.{host, python.executable, gpus[].uuid}` 제거(S/provenance/env_lock.py:113-118) |
| final_retrain*.json | MAP | results[].checkpoint_path, budget.policy |

비밀값(API token/signing key)은 어디에도 읽히지 않으며 테스트가 zip 바이트를 스캔한다(S/export/bundle.py:25-26,42; `tests/api/test_artifacts_export.py:124-151`, 151행에서 `TOKEN`/`SIGNING_KEY` 부재 단언). 단, 이 테스트는 비밀값만 검사하고 경로·PID·정책 문장은 검사하지 않는다 — WP-4의 T8 누출 canary는 §3.9의 MAP/OWNER_ONLY 항목을 대상으로 추가해야 한다. 공개 ZIP에는 "owner export"와 별도의 "public export" 프로파일을 두는 편이 단순하다(D10).

### 3.10 Archive

`ArchiveSource.run_dir`, `ArchiveRoot.path`, `ArchiveRunSummary/ArchiveRun.path`, `ArchiveDriver.path`, `ArchiveBaseline.best_ckpt`, `ArchiveCheckpoint.path`는 호스트 절대경로(S/archive/views.py:44,221,268,402,634-642; S/archive/live.py:97) → root 상대 id. `ArchiveGeneration.mutation`·`Archive*Lineage.mutation`(S/archive/views.py:153,447,645)은 ETRI GA의 mutation 기록, `ArchiveRun.stop_rule/transition/orphan_check`(:330-331)는 driver 내부 문서라 OWNER_ONLY. `ArchiveArchitecture.yaml_text/cfg`(:596-604)는 아카이브 후보의 gene이다 — 아카이브가 owner의 연구 run을 보여주는 용도라면 모듈 전체를 owner 전용으로 두는 쪽이 맞고, 고객 호스트의 run을 보는 용도라면 PUBLIC이다(D9). 측정값·카운트·verdict·곡선은 PUBLIC.

## 4. 식별자 목록과 opaque 매핑 경계

| id | 형식 / 생산 | 공개 응답·URL 등장 위치 | 분류 | 매핑 |
|---|---|---|---|---|
| run_id | `run-<uuid4 hex12>` S/api/routes_runs.py:154, S/orchestration/final_retrain.py:304; `train-<hex12>` S/training/selection.py:330 | 모든 URL(`/runs/{run_id}/...`), SSE `run_id`, Event, Artifact url | PUBLIC | 불필요(uuid) |
| candidate_id | `<run_id>:<GA|RAND>:s<seed>:g<gg>:c<iii>` S/search/streams.py:56-58; `<run_id>.base.<baseline_id>` S/orchestration/engine.py:285; `<child>.final.<ga|rnd><seed>.k<rank>` S/orchestration/final_retrain.py:250; `model-<hex12>` S/training/selection.py:330; fallback `<run_id>.<ga|rnd><seed>.g<g>.c<iii>` S/orchestration/engine.py:786 | URL `/candidates/{candidate_id}`, 행/Generation/Worker/Artifact metadata/SSE | PUBLIC | 구성 요소가 모두 공개 필드(arm, search_seed, generation, index, baseline id, rank). 매핑하면 SSE·웹 URL·`evaluation_id` 파생 규칙·export 전부에 양방향 테이블이 필요한데 얻는 비밀이 없음 → 유지 권고(D11) |
| evaluation_id | `<candidate_id>.<stage>.eval` S/orchestration/engine.py:195-196 | 행 id, Artifact.evaluation_id, Generation.representative_evaluation_id, finalists, gate_history | PUBLIC | 불필요 |
| job_id | `job-<uuid4 hex16>` S/scheduling/jobs.py:57 | CandidateEvaluation.job_id, Worker.job_id, log, audit.orchestration.rungs | PUBLIC | 불필요(runner 프로토콜에 어차피 노출) |
| worker_id | `<hostname>:<pid>` S/worker/main.py:121 | Worker.id, Health.devices[].worker_id, audit.generation.worker_id, SSE worker.updated | **MAP** | runner 등록 시 core 발급 `runner_id`(DD-A4/A6의 runner_id)를 `workers.id`로 저장 → 매핑 테이블 없이 해결 |
| artifact_id | `art-<sha256(run\0relpath\0eval)[:20]>` S/artifacts/registry.py:156 | Artifact.id/url, audit.execution_configuration.artifact_id | PUBLIC | 불필요(역산 불가) |
| validation_id | `val-<uuid4 hex16>` S/api/routes_runs.py:70 | ValidateResponse, RunRequest, log | PUBLIC | 불필요 |
| checkpoint id | `ck-<urandom 8B hex>` S/worker/main.py:442 | CandidateDetail.checkpoints[].id, Artifact.metadata.checkpoint_id | PUBLIC | 불필요 |
| device_uuid | NVML UUID S/provenance/env_lock.py:77 | Worker, Health.devices, device_resolved, raw_metrics, cost ledger | **MAP** | runner 등록 시 `device_id` 발급 또는 고객 장비이면 유지(D7) |
| group_key | `arm|seed|gen|rung|group` S/db/repo.py:303-305 | Generation | OWNER_ONLY | 제거 |
| 내부 행 id(cost_ledger.id, host_row_id, proposal_job_id, lease_owner) | S/db/repo.py:608-619, S/scheduling/costs.py:110, S/orchestration/engine.py:792 | export cost_ledger.csv, audit | OWNER_ONLY | 제거 |
| request_id | X-Request-ID 에코(S/api/app.py:6,68; 생성 규칙은 `api/auth.py` 미열람 — 확인 못 함) | Error, log | PUBLIC | 불필요 |

매핑 경계 제안: (1) 매핑 테이블을 두지 않는다. worker/device는 runner 프로토콜(WP-2) 입구에서 core가 발급한 id로만 저장하고, 기존 `hostname:pid`는 runner의 자기 보고 필드(owner DTO 전용)로 격하한다. (2) 경로→id 치환은 이미 존재하는 id(artifact_id, checkpoint id)를 쓴다: `checkpoint_path`는 같은 후보의 `checkpoints` 행(path 일치)으로, `architecture_path`는 kind=`architecture` artifact로 역참조. (3) SSE는 payload 투영만 바뀌고 `seq/type/run_id` 와이어 포맷은 그대로(S/api/routes_stream.py:29-32); 웹 URL(`/runs/{run_id}/candidates/{candidate_id}`, `/artifacts/{artifact_id}`)은 변경 없음. 설계자가 D11에서 candidate_id 매핑을 택하면 core DB에 `public_ids(kind, internal, public)` 테이블과 gateway 경로 파라미터 역매핑, `evaluation_id` 파생 규칙 재정의, export/SSE 동일 적용이 추가 작업이 된다.

## 5. 기존 결정(DD-A*)과의 충돌·주의

- **DD-A5 vs §3.5**: "resolved protocol 전체는 고객 입력"이지만 validator가 넣는 `etri_root`(owner 경로)·`realpath`·GPU `uuid`가 hash 안에 있다. 공개 ZIP에서 이 셋을 없애려면 새 contract 버전에서 (a) `etri_root`를 pool 버전 라벨로 대체하고(DD-A2 vendoring 후 자연스러움), (b) `resolution.paths`를 `RESOLUTION_HASH_EXCLUDED`에 넣거나 requested만 남기고, (c) `device_resolved.uuid`를 core 발급 device id로 바꿔야 한다. 모두 새 validation부터 hash가 달라지는 변경이며 기존 run의 hash는 재계산하지 않는다(불변).
- **DD-A8**: 웹이 `GET /protocols/presets`를 템플릿 원천으로 쓰면 §3.8의 owner 예시 경로가 고객 UI 기본값이 된다 → config 주입 필요.
- **DD-A6**: `Artifact.url`은 서버 `base_url`과 서명키로 만들어진다(S/artifacts/registry.py:203-212). gateway가 자기 키로 재서명/재작성하는 전제로 PUBLIC_MAPPED로 두었다.
- **웹 영향(WP-6)**: Lineage 화면은 "absolute architecture/checkpoint paths"를 보여준다고 명시한다(W/screens/Lineage.tsx:2-3,43). 경로를 id로 바꾸면 이 표시와 CandidateView의 hash 대조(W/components/CandidateView.tsx:305-312; artifact sha256 사용이라 유지 가능)를 조정해야 한다. `raw_metrics` JSON 덤프(W/components/CandidateView.tsx:342), Monitor의 generation_results 집계(W/screens/Monitor.tsx:256-265), Settings의 allowlists(W/screens/Settings.tsx:140)는 본 제안대로면 계속 동작한다.
- DD-7(structure_history), DD-26(registry_roots_skipped), DD-38(catalog) 과는 충돌 없음; DD-26 진단은 OWNER_ONLY로 보냈다.

## 6. 결정 필요 사항

| # | 결정 | 권고 |
|---|---|---|
| D1 | Block 출처 세분도: `source/source_kind(parent_a|parent_b)/parent_block_id/mutated` 공개 유지 vs `{inherited_from, changed}`로 축약; `repair` 값 접기; `old_name/before/after/repair_reason/slot_kind` 비공개 | 공개 유지 + repair 접기 + 4개 키 비공개 |
| D2 | `lineage` 공개 부분집합(`operation, method, parents, parent_evaluation_ids, template, search_space.version/variant` + final/baseline 키)과 `structure.lineage`·export 동일 적용 | 승인 |
| D3 | resolved_protocol 내 hash 포함 서버 필드 3개(realpath, etri_root, device uuid): 유지(PUBLIC) vs 새 contract에서 제거/치환 | 새 contract에서 치환(§5 첫 항목) |
| D4 | `raw_metrics`: 허용목록 재조립 vs 전부 제거 | 허용목록(§3.1) |
| D5 | admission 수치(cap/billed/remaining) 공개 범위 | 결정 코드+cap/billed/remaining 공개, estimate/reserved 비공개 |
| D6 | 생성 전용 통계(attempts/duplicates/failure_counts, audit.generation.attempt) | 공개(고객 run의 집계; 웹이 이미 표시) |
| D7 | worker_id/device_uuid: runner 등록 시 core 발급 id로 대체; Health.devices는 runner 보고 장비만 | 승인 |
| D8 | Health.allowlists: 고객 데이터 호스트 루트만 노출, core 내부 루트 제거 | 승인(배포 토폴로지 확정 후) |
| D9 | Archive 모듈을 공개 ZIP에 포함할지, 포함 시 `mutation`/driver 문서/`yaml_text` 처리 | 아카이브가 owner 연구 run 전용이면 owner 번들로 이동 |
| D10 | export: owner/public 두 프로파일 | 승인 |
| D11 | candidate_id/evaluation_id 매핑 여부 | 매핑하지 않음 |
| D12 | preset 예시 경로 주입 방식; handoff 본문(proposal/allocation 등) 공개 확인 | config 주입; 본문 공개 |
| D13 | SSE log 키 허용목록 + 문자열 scrub 채택 | 승인 |
| D14 | `Generation.group_key` 제거(계약에 선택 필드로 남음) | 제거 |
| D15 | `audit.etri_audit_model` 내용 검토 후 분류 | 검토 전까지 OWNER_ONLY |
| D16 | `observation.parser_source_sha256`, `catalog.source_path`(site-packages 상대) 공개 | 공개 |

## 7. 확인 못 함

- `CandidateEvaluation.source_kind_legend`의 생산 지점(서버 코드 grep 0건; 계약에만 존재).
- `audit.etri_audit_model`과 `raw_metrics.resolved_recipe`의 키 구성(ETRI 코드/adapter별 출력; 스냅샷의 adapter 본문은 전수 열람하지 않음).
- `X-Request-ID` 생성 규칙(`api/auth.py` 미열람).
- Archive `ArchiveRun.budget/preset/accuracy_floor`, `ArchivePareto.archive_entries`의 원문 키(driver JSON 내용 미확인).
- 어떤 호스트가 dataset 검증/manifest 계산을 수행하는지(원격 프로토콜 A3 범위) — `resolution.paths.realpath`와 `Health.allowlists`의 최종 분류가 이에 따라 달라진다.

## 8. 산출물

- `/home/mgjeong/Desktop/etri_hd/mbnas_application/docs/internal/analysis/DTO_ALLOWLIST_PROPOSAL.md` (이 문서)
- `/home/mgjeong/Desktop/etri_hd/mbnas_application/docs/internal/analysis/DTO_ALLOWLIST_PROPOSAL.json` (`{component: {field_path: {class, reason, source}}}`; 생성 스크립트는 스크래치패드 `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/a2/gen_allowlist.py`, openapi 96 components 전수 검사 통과)

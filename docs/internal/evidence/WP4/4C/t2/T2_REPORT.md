# T2 core 동등성 보고 (owner 전용)

생성: 2026-10-27T18:05:26+00:00 ~ 2026-10-27T18:06:37+00:00 (UTC), `scripts/owner/t2_compare.py`. 계획: `docs/internal/WORK_PACKAGES/T2_REFERENCE_PLAN.md`. 산출: `docs/internal/evidence/T2/{scenario}.{ref,new}.json`, 고정 입력 `inputs/`, 기계 판독 요약 `T2_SUMMARY.json`, 스냅샷 보호 `T2_snapshot_guard.json`.

- reference = 원본 monolith `mbnas_server` + `yolo_ga_runtime` (`build/source_snapshot`, 서브프로세스, sys.path=[snapshot/application/MBNAS_server/src, snapshot], 새 패키지 src 제거, MBNAS_ETRI_NAS_ROOT=snapshot).
- new = `mbnas_core` + `mbnas_model_runtime` + `mbnas_contracts` (같은 venv 서브프로세스).
- 스냅샷 트리(파일 603, 디렉터리 77, 링크 0) sha256 전 `1475baa52c82f0fc…` 후 `1475baa52c82f0fc…` → **변경 없음**.
- 명령의 토큰: `<SNAPSHOT>` = build/source_snapshot, `<OUT>` = docs/internal/evidence/T2, `<WORK>` = 시나리오별 작업 디렉터리(양쪽 동일 절대 경로, 측 사이에 삭제). 산출 JSON의 `<REPO>`/`<HOME>`/`<HOSTNAME>`은 호스트 경로·이름을 지운 토큰.
- 새 패키지 source tree(contracts+model_runtime+core) 전 `sha256:aee93aa527ab74a0438c80d392baa97a9990832075d1657370891101cf3a7454` 후 `sha256:aee93aa527ab74a0438c80d392baa97a9990832075d1657370891101cf3a7454` (실행 중 변경 없음).

환경: python 3.10.22, torch 2.6.0+cu124, ultralytics 8.4.173, CPU 전용(CUDA_VISIBLE_DEVICES="", pynvml 차단), PYTHONHASHSEED=0, PYTHONDONTWRITEBYTECODE=1.

## 요약

| 시나리오 | 판정 | 허용 차이 (규칙: 건수) | 불일치 | 고정 입력 sha256 | 소요 ref/new (s) |
|---|---|---|---|---|---|
| T2-1 구조 gate (check_structure, 10 YAML x input 64/160) | **ALLOWED_DIFF** | module_path:structural_hash: 4, module_path_string: 8, timestamp: 18 | 0 | `sha256:ffc3a53865eaa751…` (inputs/T2-1.input.json) | 4.28 / 4.242 |
| T2-2 Random gen0 (seeds 101/202/303, population 4) | **ALLOWED_DIFF** | key:module_path_string: 6, mixed_pool_provenance: 7, module_path:structural_hash: 11, module_path_string: 12, preset_paths: 2, search_space_hash: 27, search_space_version: 13 | 0 | t2_core.py 내 고정 (`de49c6fc9d4e…`) | 5.159 / 5.115 |
| T2-3 GA gen0/gen1 (8-row synthetic L0 pool, crossover 0.5, mutation 0.15) | **ALLOWED_DIFF** | key:module_path_string: 6, mixed_pool_provenance: 7, module_path:structural_hash: 35, module_path_string: 12, search_space_hash: 51, search_space_version: 25 | 0 | t2_core.py 내 고정 (`de49c6fc9d4e…`) | 6.065 / 6.085 |
| T2-4 repair seed 116 (PROPOSE_GENERATION handler) | **EXACT_MATCH** | - | 0 | t2_core.py 내 고정 (`de49c6fc9d4e…`) | 4.32 / 4.231 |
| T2-5 fitness / gate (24 rows x 3 rung cfg x 2 hard constraints) | **EXACT_MATCH** | - | 0 | `sha256:10af530ea211be1b…` (inputs/T2-5.input.json) | 0.334 / 0.286 |
| T2-6 admission (15 decisions) / settle_run (12 transitions) | **ALLOWED_DIFF** | timestamp: 12 | 0 | t2_core.py 내 고정 (`de49c6fc9d4e…`) | 0.408 / 0.476 |
| T2-7 engine flow (InlineWorker + stub handlers, 2 rungs, 2 seeds, population 2) | **ALLOWED_DIFF** | etri_root_resolution: 6, generated_id: 26, hash_excluded_extended: 1, own_resolution_hash: 1, protocol_hash_definition: 195, protocol_hash_embedding_file: 26, realpath_dropped: 5, timestamp: 899, wall_clock_duration: 210, worker_device_identity: 26, worker_telemetry: 1 | 0 | `sha256:09f555f1fe9c7e18…` (inputs/T2-7.input.json) | 2.299 / 2.45 |
| T2-8 final selection (T2-7 run + final-retrain requests; fixed-table rule) | **ALLOWED_DIFF** | generated_id: 22, protocol_hash_definition: 153, protocol_hash_embedding_file: 22, timestamp: 528, wall_clock_duration: 232, worker_device_identity: 11, worker_telemetry: 2 | 0 | `sha256:09f555f1fe9c7e18…` (inputs/T2-8.input.json) | 3.17 / 3.297 |
| T2-9 seed streams (100 fixed inputs) | **EXACT_MATCH** | - | 0 | t2_core.py 내 고정 (`de49c6fc9d4e…`) | 0.313 / 0.373 |
| T2-10 generation-only OFF run (seeds 2, candidate_count 2, max_attempts 6) | **ALLOWED_DIFF** | hash_excluded_extended: 1, key:module_path_string: 6, mixed_pool_provenance: 8, module_path:structural_hash: 21, module_path_string: 78, own_resolution_hash: 1, protocol_hash_definition: 16, realpath_dropped: 1, search_space_hash: 30, search_space_version: 14, timestamp: 82, wall_clock_duration: 2, worker_telemetry: 1 | 0 | `sha256:e8435833abd64292…` (inputs/T2-10.input.json) | 3.859 / 4.071 |

## 시나리오별 상세

### T2-1 — 구조 gate (check_structure, 10 YAML x input 64/160)

판정: **ALLOWED_DIFF**

- ref_run (exit 0, 4.9 s): `runtime/venv/bin/python -B -s tests/owner/t2/reference_runner.py --snapshot <SNAPSHOT> --scenario T2-1 --work <WORK> --out <OUT>/T2-1.ref.json --inputs <OUT>/inputs/T2-1.input.json`
- new_run (exit 0, 4.9 s): `runtime/venv/bin/python -B -s tests/owner/t2/new_runner.py --snapshot <SNAPSHOT> --scenario T2-1 --work <WORK> --out <OUT>/T2-1.new.json --inputs <OUT>/inputs/T2-1.input.json`
- 입력: 고정 입력 `sha256:ffc3a53865eaa751336eed61acb2adf2e79f371840885ddcae8e048260d1dbb2` (`docs/internal/evidence/WP4/4C/t2/inputs/T2-1.input.json`)
- 산출: `docs/internal/evidence/WP4/4C/t2/T2-1.ref.json` (sha256 `716999d09d68f8f1…`), `docs/internal/evidence/WP4/4C/t2/T2-1.new.json` (sha256 `5441c6c1b5031a7d…`)
- 격리 위반: ref 0건, new 0건; 구조 해시 기록 ref 7 / new 7

실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):

- err_concat_mismatch@160: FAIL ['SHAPE_MISMATCH', 'MERGE_INPUT_MISMATCH']
- err_concat_mismatch@64: FAIL ['SHAPE_MISMATCH', 'MERGE_INPUT_MISMATCH']
- err_head_stride_inverted@160: FAIL ['HEAD_INPUT_INVALID']
- err_head_stride_inverted@64: FAIL ['HEAD_INPUT_INVALID']
- err_huge_row@160: PASS []
- err_huge_row@64: PASS []
- err_nc_mismatch@160: FAIL ['HEAD_CLASS_MISMATCH']
- err_nc_mismatch@64: FAIL ['HEAD_CLASS_MISMATCH']
- err_unknown_module@160: FAIL ['BLOCK_UNSUPPORTED']
- err_unknown_module@64: FAIL ['BLOCK_UNSUPPORTED']
- yolo11n_native@160: PASS []
- yolo11n_native@64: PASS []
- yolo11n_variant_c2f@160: PASS []
- yolo11n_variant_c2f@64: PASS []
- yolo11n_variant_global@160: PASS []
- yolo11n_variant_global@64: PASS []
- yolo26n_native@160: PASS []
- yolo26n_native@64: PASS []
- yolo26n_variant_mixed@160: PASS []
- yolo26n_variant_mixed@64: PASS []

허용 차이 경로 (규칙 → 경로 패턴 × 건수):

- `module_path:structural_hash` (4): `docs.yolo11n_variant_global@160.architecture_hash` ×1; `docs.yolo11n_variant_global@64.architecture_hash` ×1; `docs.yolo26n_variant_mixed@160.architecture_hash` ×1; `docs.yolo26n_variant_mixed@64.architecture_hash` ×1
- `module_path_string` (8): `docs.yolo11n_variant_global@160.blocks.*.catalog.reference` ×2; `docs.yolo11n_variant_global@64.blocks.*.catalog.reference` ×2; `docs.yolo26n_variant_mixed@160.blocks.*.catalog.reference` ×2; `docs.yolo26n_variant_mixed@64.blocks.*.catalog.reference` ×2
- `timestamp` (18): `docs.err_concat_mismatch@160.observation.checked_at` ×1; `docs.err_concat_mismatch@64.observation.checked_at` ×1; `docs.err_head_stride_inverted@160.observation.checked_at` ×1; `docs.err_head_stride_inverted@64.observation.checked_at` ×1; `docs.err_huge_row@160.observation.checked_at` ×1; `docs.err_huge_row@64.observation.checked_at` ×1; `docs.err_unknown_module@160.observation.checked_at` ×1; `docs.err_unknown_module@64.observation.checked_at` ×1; `docs.yolo11n_native@160.observation.checked_at` ×1; `docs.yolo11n_native@64.observation.checked_at` ×1; `docs.yolo11n_variant_c2f@160.observation.checked_at` ×1; `docs.yolo11n_variant_c2f@64.observation.checked_at` ×1; `docs.yolo11n_variant_global@160.observation.checked_at` ×1; `docs.yolo11n_variant_global@64.observation.checked_at` ×1; `docs.yolo26n_native@160.observation.checked_at` ×1; `docs.yolo26n_native@64.observation.checked_at` ×1; `docs.yolo26n_variant_mixed@160.observation.checked_at` ×1; `docs.yolo26n_variant_mixed@64.observation.checked_at` ×1

참고(new 전용, DD-A20 콘솔 선검사 `validate_declared`; check_structure 비교 대상 아님): 빌드 전 거부되는 YAML = {"err_huge_row": ["ARG_SCHEMA_INVALID"], "err_unknown_module": ["BLOCK_UNSUPPORTED"]}

### T2-2 — Random gen0 (seeds 101/202/303, population 4)

판정: **ALLOWED_DIFF**

- ref_run (exit 0, 5.7 s): `runtime/venv/bin/python -B -s tests/owner/t2/reference_runner.py --snapshot <SNAPSHOT> --scenario T2-2 --work <WORK> --out <OUT>/T2-2.ref.json`
- new_run (exit 0, 5.7 s): `runtime/venv/bin/python -B -s tests/owner/t2/new_runner.py --snapshot <SNAPSHOT> --scenario T2-2 --work <WORK> --out <OUT>/T2-2.new.json`
- 입력: 고정 입력 t2_core.py 코드 내 고정
- 산출: `docs/internal/evidence/WP4/4C/t2/T2-2.ref.json` (sha256 `3fd94e58db38d4ff…`), `docs/internal/evidence/WP4/4C/t2/T2-2.new.json` (sha256 `de46c544183ec3d2…`)
- 격리 위반: ref 0건, new 0건; 구조 해시 기록 ref 16 / new 16

실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):

- templates: 6
- rejected_templates: 0
- seed 101: ["yolo26n:native draws=0 repairs=0", "yolo11n:native draws=3 repairs=0", "yolo26n:native draws=1 repairs=0", "yolo11n:cnn_backbone draws=1 repairs=0"]
- seed 202: ["yolo26n:transformer_backbone draws=3 repairs=0", "yolo26n:native draws=1 repairs=0", "yolo26n:cnn_backbone draws=2 repairs=0", "yolo11n:transformer_backbone draws=2 repairs=0"]
- seed 303: ["yolo11n:native draws=1 repairs=0", "yolo26n:cnn_backbone draws=1 repairs=0", "yolo11n:native draws=2 repairs=0", "yolo26n:transformer_backbone draws=0 repairs=0"]

허용 차이 경로 (규칙 → 경로 패턴 × 건수):

- `key:module_path_string` (6): `space.templates.*.source.<POOL_FILES_SHA256>` ×6
- `mixed_pool_provenance` (7): `space.templates.*.source.<POOL_FILES_SHA256>` ×6; `space.etri` ×1
- `module_path:structural_hash` (11): `proposals.202.*.report.architecture_hash` ×4; `proposals.303.*.report.architecture_hash` ×4; `proposals.101.*.report.architecture_hash` ×3
- `module_path_string` (12): `space.templates.*.source.function` ×6; `space.templates.*.source.kind` ×6
- `preset_paths` (2): `presets.default.console.paths` ×1; `presets.pilot_smoke.console.paths` ×1
- `search_space_hash` (27): `proposals.101.*.lineage.search_space.prior_hash` ×4; `proposals.101.*.lineage.search_space.space_hash` ×4; `proposals.202.*.lineage.search_space.prior_hash` ×4; `proposals.202.*.lineage.search_space.space_hash` ×4; `proposals.303.*.lineage.search_space.prior_hash` ×4; `proposals.303.*.lineage.search_space.space_hash` ×4; `space.prior_hash` ×1; `space.space_hash` ×1; `space.space_hash_definition` ×1
- `search_space_version` (13): `proposals.101.*.lineage.search_space.version` ×4; `proposals.202.*.lineage.search_space.version` ×4; `proposals.303.*.lineage.search_space.version` ×4; `space.version` ×1

### T2-3 — GA gen0/gen1 (8-row synthetic L0 pool, crossover 0.5, mutation 0.15)

판정: **ALLOWED_DIFF**

- ref_run (exit 0, 6.7 s): `runtime/venv/bin/python -B -s tests/owner/t2/reference_runner.py --snapshot <SNAPSHOT> --scenario T2-3 --work <WORK> --out <OUT>/T2-3.ref.json`
- new_run (exit 0, 6.7 s): `runtime/venv/bin/python -B -s tests/owner/t2/new_runner.py --snapshot <SNAPSHOT> --scenario T2-3 --work <WORK> --out <OUT>/T2-3.new.json --ref <OUT>/T2-3.ref.json`
- 입력: 고정 입력 t2_core.py 코드 내 고정; 2단계 입력(ref 산출) `sha256:a784edb71e9dec0bb8d3cceec4684b7566b08faea2aa637682c7eaee0a2a1aa4` = new가 소비한 입력 `sha256:a784edb71e9dec0bb8d3cceec4684b7566b08faea2aa637682c7eaee0a2a1aa4` → 일치
- 산출: `docs/internal/evidence/WP4/4C/t2/T2-3.ref.json` (sha256 `e331cf0588e2d6f3…`), `docs/internal/evidence/WP4/4C/t2/T2-3.new.json` (sha256 `b98336a216343da9…`)
- 격리 위반: ref 0건, new 0건; 구조 해시 기록 ref 19 / new 19

실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):

- gen0_shared_with_random_arm: {"101": true, "202": true, "303": true}
- negatives: {"duplicate": "ParentPoolError", "l1_row": "ParentPoolError", "no_valid_fitness": "ParentPoolError", "uncommitted": "ParentPoolError"}
- gen1 seed 101: {"elite": "t2-3:s101:c003", "children": ["clone(elite)", "clone", "clone", "clone"]}
- gen1 seed 202: {"elite": "t2-3:s101:c003", "children": ["clone(elite)", "mutation mut[10] C2f->C2f noop", "crossover cut=20", "mutation mut[2] NASWindowTransformer->C3TR"]}
- gen1 seed 303: {"elite": "t2-3:s101:c003", "children": ["clone(elite)", "mutation mut[7] Conv->NASPatchMerge", "crossover cut=2", "clone"]}

허용 차이 경로 (규칙 → 경로 패턴 × 건수):

- `key:module_path_string` (6): `space.templates.*.source.<POOL_FILES_SHA256>` ×6
- `mixed_pool_provenance` (7): `space.templates.*.source.<POOL_FILES_SHA256>` ×6; `space.etri` ×1
- `module_path:structural_hash` (35): `gen0.202.*.report.architecture_hash` ×4; `gen0.303.*.report.architecture_hash` ×4; `gen1.101.children.*.report.architecture_hash` ×4; `gen1.202.children.*.report.architecture_hash` ×4; `gen1.303.children.*.report.architecture_hash` ×4; `gen0.101.*.report.architecture_hash` ×3; `gen1.202.children.*.lineage.mutation_events.*.post_repair_architecture_hash` ×2; `gen1.202.children.*.lineage.mutation_events.*.pre_mutation_architecture_hash` ×2; `gen1.202.children.*.mutation_events.*.post_repair_architecture_hash` ×2; `gen1.202.children.*.mutation_events.*.pre_mutation_architecture_hash` ×2; `gen1.303.children.*.lineage.mutation_events.*.post_repair_architecture_hash` ×1; `gen1.303.children.*.lineage.mutation_events.*.pre_mutation_architecture_hash` ×1; `gen1.303.children.*.mutation_events.*.post_repair_architecture_hash` ×1; `gen1.303.children.*.mutation_events.*.pre_mutation_architecture_hash` ×1
- `module_path_string` (12): `space.templates.*.source.function` ×6; `space.templates.*.source.kind` ×6
- `search_space_hash` (51): `gen0.101.*.lineage.search_space.prior_hash` ×4; `gen0.101.*.lineage.search_space.space_hash` ×4; `gen0.202.*.lineage.search_space.prior_hash` ×4; `gen0.202.*.lineage.search_space.space_hash` ×4; `gen0.303.*.lineage.search_space.prior_hash` ×4; `gen0.303.*.lineage.search_space.space_hash` ×4; `gen1.101.children.*.lineage.search_space.prior_hash` ×4; `gen1.101.children.*.lineage.search_space.space_hash` ×4; `gen1.202.children.*.lineage.search_space.prior_hash` ×4; `gen1.202.children.*.lineage.search_space.space_hash` ×4; `gen1.303.children.*.lineage.search_space.prior_hash` ×4; `gen1.303.children.*.lineage.search_space.space_hash` ×4; `space.prior_hash` ×1; `space.space_hash` ×1; `space.space_hash_definition` ×1
- `search_space_version` (25): `gen0.101.*.lineage.search_space.version` ×4; `gen0.202.*.lineage.search_space.version` ×4; `gen0.303.*.lineage.search_space.version` ×4; `gen1.101.children.*.lineage.search_space.version` ×4; `gen1.202.children.*.lineage.search_space.version` ×4; `gen1.303.children.*.lineage.search_space.version` ×4; `space.version` ×1

### T2-4 — repair seed 116 (PROPOSE_GENERATION handler)

판정: **EXACT_MATCH**

- ref_run (exit 0, 4.9 s): `runtime/venv/bin/python -B -s tests/owner/t2/reference_runner.py --snapshot <SNAPSHOT> --scenario T2-4 --work <WORK> --out <OUT>/T2-4.ref.json`
- new_run (exit 0, 4.8 s): `runtime/venv/bin/python -B -s tests/owner/t2/new_runner.py --snapshot <SNAPSHOT> --scenario T2-4 --work <WORK> --out <OUT>/T2-4.new.json --ref <OUT>/T2-4.ref.json`
- 입력: 고정 입력 t2_core.py 코드 내 고정; 2단계 입력(ref 산출) `sha256:b7c22c1718d9c3996e9fc996fc89f496686e85b5005fba45c7d8a45dc58d6c6e` = new가 소비한 입력 `sha256:b7c22c1718d9c3996e9fc996fc89f496686e85b5005fba45c7d8a45dc58d6c6e` → 일치
- 산출: `docs/internal/evidence/WP4/4C/t2/T2-4.ref.json` (sha256 `43759e580d03d006…`), `docs/internal/evidence/WP4/4C/t2/T2-4.new.json` (sha256 `82212a839d1b4f37…`)
- 격리 위반: ref 0건, new 0건; 구조 해시 기록 ref 8 / new 8

실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):

- GA: {"status": "SUCCEEDED", "row9_c3tr_to_sppf": true, "repairs": [[], ["row 9 C3TR->SPPF (violation row 10)"]]}
- IndependentRandom: {"status": "SUCCEEDED", "row9_c3tr_to_sppf": true, "repairs": [[], ["row 9 C3TR->SPPF (violation row 10)"]]}

### T2-5 — fitness / gate (24 rows x 3 rung cfg x 2 hard constraints)

판정: **EXACT_MATCH**

- ref_run (exit 0, 0.5 s): `runtime/venv/bin/python -B -s tests/owner/t2/reference_runner.py --snapshot <SNAPSHOT> --scenario T2-5 --work <WORK> --out <OUT>/T2-5.ref.json --inputs <OUT>/inputs/T2-5.input.json`
- new_run (exit 0, 0.5 s): `runtime/venv/bin/python -B -s tests/owner/t2/new_runner.py --snapshot <SNAPSHOT> --scenario T2-5 --work <WORK> --out <OUT>/T2-5.new.json --inputs <OUT>/inputs/T2-5.input.json`
- 입력: 고정 입력 `sha256:10af530ea211be1b82db219d30e833f7d4cf2b90ca46e48de193ffc52946099b` (`docs/internal/evidence/WP4/4C/t2/inputs/T2-5.input.json`)
- 산출: `docs/internal/evidence/WP4/4C/t2/T2-5.ref.json` (sha256 `98a615bd158257f1…`), `docs/internal/evidence/WP4/4C/t2/T2-5.new.json` (sha256 `9b8b6c3439870cfa…`)
- 격리 위반: ref 0건, new 0건; 구조 해시 기록 ref 0 / new 0

실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):

- fitness_results: 72
- fitness_reasons: {"VALID": 36, "METRIC_MISSING:ap": 3, "METRIC_MISSING:recall": 3, "METRIC_MISSING:latency_ms": 3, "METRIC_MISSING:size_mb": 3, "METRIC_NOT_FINITE:ap": 9, "METRIC_NOT_FINITE:latency_ms": 3, "AP_OUT_OF_RANGE": 3, "RECALL_OUT_OF_RANGE": 3, "LATENCY_NONPOSITIVE": 3, "SIZE_NONPOSITIVE": 3}
- gate_results: 144
- gate_codes: {"PROMOTION_ELIGIBLE": 15, "SEARCH_RUNG_FINAL": 24, "REJECTED_BY_GATE": 33, "EVAL_INVALID": 72}
- fitness_and_gate: 144
- misc_errors: {"anchors_inverted": "ValueError", "anchors_zero": "ValueError", "gate_with_other_metrics": "ValueError", "gate_with_other_rung_weights": "ValueError", "unknown_rung": null, "weights_negative": "ValueError", "weights_sum_not_one": "ValueError", "weights_unknown_key": "ValueError"}

### T2-6 — admission (15 decisions) / settle_run (12 transitions)

판정: **ALLOWED_DIFF**

- ref_run (exit 0, 0.6 s): `runtime/venv/bin/python -B -s tests/owner/t2/reference_runner.py --snapshot <SNAPSHOT> --scenario T2-6 --work <WORK> --out <OUT>/T2-6.ref.json`
- new_run (exit 0, 0.7 s): `runtime/venv/bin/python -B -s tests/owner/t2/new_runner.py --snapshot <SNAPSHOT> --scenario T2-6 --work <WORK> --out <OUT>/T2-6.new.json`
- 입력: 고정 입력 t2_core.py 코드 내 고정
- 산출: `docs/internal/evidence/WP4/4C/t2/T2-6.ref.json` (sha256 `51ef0abee3af27bd…`), `docs/internal/evidence/WP4/4C/t2/T2-6.new.json` (sha256 `2a4f65de9b6cb73f…`)
- 격리 위반: ref 0건, new 0건; 구조 해시 기록 ref 0 / new 0

실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):

- admission: {"A10_final_source_rate": "True ADMITTED est=120.0 (finalist_source_measured_rate) remaining=1200.0", "A10b_final_unmeasured": "True ADMITTED est=None (unmeasured) remaining=1200.0", "A11_ir_isolated_from_ga": "True ADMITTED est=None (unmeasured) remaining=3600.0", "A12_no_caps_baseline": "False BUDGET_MISSING est=None (unmeasured) remaining=None", "A12_no_caps_final": "False BUDGET_MISSING est=None (unmeasured) remaining=None", "A12_no_caps_ga": "False BUDGET_MISSING est=None (unmeasured) remaining=None", "A1_own_rate": "True ADMITTED est=100.0 (candidate_measured_rate) remaining=3500.0", "A2_arm_seed_mean": "True ADMITTED est=120.0 (arm_seed_mean_measured_rate) remaining=3480.0", "A3_unmea
- settle: {"s1-open": "RUNNING None", "s10-gen-empty": "COMPLETED GENERATION_EMPTY", "s11-queued-nojobs": "COMPLETED None", "s12-child": "COMPLETED None", "s2-done": "COMPLETED None", "s3-failed": "FAILED NO_SUCCESSFUL_EVALUATION", "s4-pause-active": "PAUSE_REQUESTED None", "s5-pause-idle": "PAUSED None", "s6-cancel": "CANCELLED CANCELLED_BY_USER", "s7-gen-failed": "FAILED GENERATION_FAILED", "s8-gen-complete": "COMPLETED None", "s9-gen-partial": "COMPLETED GENERATION_PARTIAL"}

허용 차이 경로 (규칙 → 경로 패턴 × 건수):

- `timestamp` (12): `settle.s10-gen-empty.events.*.payload.updated_at` ×1; `settle.s11-queued-nojobs.events.*.payload.updated_at` ×1; `settle.s12-child.events.*.payload.updated_at` ×1; `settle.s2-done.events.*.payload.updated_at` ×1; `settle.s3-failed.events.*.payload.updated_at` ×1; `settle.s5-pause-idle.events.*.payload.updated_at` ×1; `settle.s6-cancel.cancel.completed_at` ×1; `settle.s6-cancel.events.*.payload.cancel.completed_at` ×1; `settle.s6-cancel.events.*.payload.updated_at` ×1; `settle.s7-gen-failed.events.*.payload.updated_at` ×1; `settle.s8-gen-complete.events.*.payload.updated_at` ×1; `settle.s9-gen-partial.events.*.payload.updated_at` ×1

### T2-7 — engine flow (InlineWorker + stub handlers, 2 rungs, 2 seeds, population 2)

판정: **ALLOWED_DIFF**

- ref_run (exit 0, 2.6 s): `runtime/venv/bin/python -B -s tests/owner/t2/reference_runner.py --snapshot <SNAPSHOT> --scenario T2-7 --work <WORK> --out <OUT>/T2-7.ref.json --inputs <OUT>/inputs/T2-7.input.json`
- new_run (exit 0, 2.8 s): `runtime/venv/bin/python -B -s tests/owner/t2/new_runner.py --snapshot <SNAPSHOT> --scenario T2-7 --work <WORK> --out <OUT>/T2-7.new.json --inputs <OUT>/inputs/T2-7.input.json --ref <OUT>/T2-7.ref.json`
- 입력: 고정 입력 `sha256:09f555f1fe9c7e185872f23599154242fb71cd7fd17d72bc5e2e9f9ac1b7892e` (`docs/internal/evidence/WP4/4C/t2/inputs/T2-7.input.json`); 2단계 입력(ref 산출) `sha256:ecdb2b6e3b9a98abf0bca1a86bbca7389450daaab2bbf362137eb7662ddc888b` = new가 소비한 입력 `sha256:ecdb2b6e3b9a98abf0bca1a86bbca7389450daaab2bbf362137eb7662ddc888b` → 일치
- 산출: `docs/internal/evidence/WP4/4C/t2/T2-7.ref.json` (sha256 `92d1fc1bc97d37b7…`), `docs/internal/evidence/WP4/4C/t2/T2-7.new.json` (sha256 `d73eca55b20e455d…`)
- 격리 위반: ref 0건, new 0건; 구조 해시 기록 ref 0 / new 0

실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):

- run: COMPLETED None
- jobs_executed: 34
- jobs_by_kind: {"BASELINE_TRAIN": 2, "PROPOSE_GENERATION": 8, "CANDIDATE_TRAIN": 24}
- evaluated_rows: 26
- gate_codes: {"None": 2, "PROMOTION_ELIGIBLE": 8, "REJECTED_BY_GATE": 8, "SEARCH_RUNG_FINAL": 8}
- candidate_status: {"EVALUATED": 1, "SEARCH_COMPLETE": 8, "REJECTED_BY_GATE": 8}
- generations: 16 rows, complete 16
- ledger_rows_by_kind: {"train": 26}
- events: 162

허용 차이 경로 (규칙 → 경로 패턴 × 건수):

- `etri_root_resolution` (6): `validation.capabilities.search_space.detail` ×1; `validation.capabilities.search_space.etri_root` ×1; `validation.capabilities.search_space.probe.checked` ×1; `validation.resolved.console.search.search_space_resolved.etri_root` ×1; `validation.resolved_diff.*.reason` ×1; `validation.resolved_diff.*.resolved.etri_root` ×1
- `generated_id` (26): `db.checkpoints.*.id` ×26
- `hash_excluded_extended` (1): `validation.resolved.resolution.hash_excluded` ×1
- `own_resolution_hash` (1): `validation.protocol_hash` ×1
- `protocol_hash_definition` (195): `db.events.*.payload.protocol_hash` ×107; `db.jobs.*.spec.protocol_hash` ×34; `db.checkpoints.*.hashes.protocol` ×26; `db.evaluations.*.row.protocol_hash` ×26; `db.run.protocol_hash` ×1; `protocol_hash` ×1
- `protocol_hash_embedding_file` (26): `db.checkpoints.*.sha256` ×26
- `realpath_dropped` (5): `validation.resolved.resolution.paths.dataset_yaml.realpath` ×1; `validation.resolved.resolution.paths.final_eval_root.realpath` ×1; `validation.resolved.resolution.paths.output_root.realpath` ×1; `validation.resolved.resolution.paths.search_val_root.realpath` ×1; `validation.resolved.resolution.paths.train_root.realpath` ×1
- `timestamp` (899): `db.events.*.created_at` ×162; `db.artifacts.*.created_at` ×104; `db.artifacts.*.updated_at` ×104; `db.jobs.*.created_at` ×34; `db.jobs.*.finished_at` ×34; `db.jobs.*.heartbeat_at` ×34; `db.jobs.*.result.finished_at` ×34; `db.jobs.*.result.started_at` ×34; `db.jobs.*.started_at` ×34; `db.jobs.*.updated_at` ×34; `db.checkpoints.*.created_at` ×26; `db.checkpoints.*.updated_at` ×26; `db.costs.*.created_at` ×26; `db.costs.*.finished_at` ×26; `db.costs.*.started_at` ×26; `db.costs.*.updated_at` ×26; `db.evaluations.*.created_at` ×26; `db.evaluations.*.updated_at` ×26; `db.candidates.*.created_at` ×17; `db.candidates.*.updated_at` ×17; `db.generations.*.created_at` ×16; `db.generations.*.updated_at` ×16; `db.checkpoints.*.pruned_at` ×9; `db.events.*.payload.created_at` ×3; `db.events.*.payload.updated_at` ×3 (+2 patterns, T2_SUMMARY.json)
- `wall_clock_duration` (210): `db.costs.*.gpu_seconds` ×26; `db.evaluations.*.row.cost.train` ×26; `db.events.*.payload.cost.train` ×26; `db.jobs.*.spec.params.admission.billed_s` ×17; `db.jobs.*.spec.params.admission.estimate_s` ×17; `db.jobs.*.spec.params.admission.remaining_s` ×17; `db.jobs.*.spec.params.admission.reserved_s` ×11; `db.candidates.*.audit.orchestration.rungs.L1.admission.billed_s` ×9; `db.candidates.*.audit.orchestration.rungs.L1.admission.estimate_s` ×9; `db.candidates.*.audit.orchestration.rungs.L1.admission.remaining_s` ×9; `db.candidates.*.audit.orchestration.rungs.L0.admission.billed_s` ×8; `db.candidates.*.audit.orchestration.rungs.L0.admission.estimate_s` ×8; `db.candidates.*.audit.orchestration.rungs.L0.admission.remaining_s` ×8; `db.candidates.*.audit.orchestration.rungs.L0.admission.reserved_s` ×7; `db.candidates.*.audit.orchestration.rungs.L1.admission.reserved_s` ×4; `db.events.*.payload.costs.actual_gpu_seconds` ×1; `db.events.*.payload.costs.baseline_gpu_seconds` ×1; `db.events.*.payload.costs.ga_billed_gpu_seconds` ×1; `db.events.*.payload.costs.random_billed_gpu_seconds` ×1; `db.run.costs.actual_gpu_seconds` ×1; `db.run.costs.baseline_gpu_seconds` ×1; `db.run.costs.ga_billed_gpu_seconds` ×1; `db.run.costs.random_billed_gpu_seconds` ×1
- `worker_device_identity` (26): `db.costs.*.device_uuid` ×26
- `worker_telemetry` (1): `db.worker_events` ×1

### T2-8 — final selection (T2-7 run + final-retrain requests; fixed-table rule)

판정: **ALLOWED_DIFF**

- ref_run (exit 0, 3.4 s): `runtime/venv/bin/python -B -s tests/owner/t2/reference_runner.py --snapshot <SNAPSHOT> --scenario T2-8 --work <WORK> --out <OUT>/T2-8.ref.json --inputs <OUT>/inputs/T2-8.input.json`
- new_run (exit 0, 3.6 s): `runtime/venv/bin/python -B -s tests/owner/t2/new_runner.py --snapshot <SNAPSHOT> --scenario T2-8 --work <WORK> --out <OUT>/T2-8.new.json --inputs <OUT>/inputs/T2-8.input.json --ref <OUT>/T2-8.ref.json`
- 입력: 고정 입력 `sha256:09f555f1fe9c7e185872f23599154242fb71cd7fd17d72bc5e2e9f9ac1b7892e` (`docs/internal/evidence/WP4/4C/t2/inputs/T2-8.input.json`); 2단계 입력(ref 산출) `sha256:5c336fdb33ed1fc60987152308692f43c32802cc63d2d2b552caf36134b1cf68` = new가 소비한 입력 `sha256:5c336fdb33ed1fc60987152308692f43c32802cc63d2d2b552caf36134b1cf68` → 일치
- 산출: `docs/internal/evidence/WP4/4C/t2/T2-8.ref.json` (sha256 `e5176616dba86d5c…`), `docs/internal/evidence/WP4/4C/t2/T2-8.new.json` (sha256 `400c15e0aaf3b1ab…`)
- 격리 위반: ref 0건, new 0건; 구조 해시 기록 ref 0 / new 0

실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):

- requests: {"budget_invalid": "BUDGET_INVALID", "k1_conflict": "IDEMPOTENCY_CONFLICT", "k1_default_topk": {"created": true, "finalists": ["GA s101 g0.c000 rank 1", "GA s202 g1.c000 rank 1", "IndependentRandom s101 g0.c000 rank 1", "IndependentRandom s202 g0.c001 rank 1"]}, "k1_replay": {"created": false, "finalists": ["GA s101 g0.c000 rank 1", "GA s202 g1.c000 rank 1", "IndependentRandom s101 g0.c000 rank 1", "IndependentRandom s202 g0.c001 rank 1"]}, "k2_top2": {"created": true, "finalists": ["GA s101 g0.c000 rank 1", "GA s101 g1.c001 rank 2", "GA s202 g1.c000 rank 1", "IndependentRandom s101 g0.c000 rank 1", "IndependentRandom s101 g1.c000 rank 2", "IndependentRandom s202 g0.c001 rank 1", "Independen
- child_runs: {"run-000000000023": "COMPLETED candidates {'FINAL_COMPLETE': 4}", "run-000000000028": "COMPLETED candidates {'FINAL_COMPLETE': 7}"}
- child_jobs_executed: 11
- fixed_table_top1: ["GA s101 ga-c2", "GA s202 ga-c5", "IndependentRandom s202 rnd-c9"]
- fixed_table_top3: ["GA s101 ga-c2 #1", "GA s101 ga-c3 #2", "GA s101 ga-c1 #3", "GA s202 ga-c5 #1", "IndependentRandom s202 rnd-c9 #1"]

허용 차이 경로 (규칙 → 경로 패턴 × 건수):

- `generated_id` (22): `children_after.<run#2>.checkpoints.*.id` ×14; `children_after.<run#1>.checkpoints.*.id` ×8
- `protocol_hash_definition` (153): `children_after.<run#2>.events.*.payload.protocol_hash` ×31; `children_after.<run#1>.events.*.payload.protocol_hash` ×19; `children_after.<run#2>.checkpoints.*.hashes.protocol` ×14; `parent_after.events.*.payload.protocol_hash` ×10; `selection_inputs.*.row.protocol_hash` ×9; `child_plans.<run#2>.events.*.payload.protocol_hash` ×8; `children_after.<run#1>.checkpoints.*.hashes.protocol` ×8; `child_plans.<run#2>.evaluations.*.row.protocol_hash` ×7; `child_plans.<run#2>.jobs.*.spec.protocol_hash` ×7; `children_after.<run#2>.evaluations.*.row.protocol_hash` ×7; `children_after.<run#2>.jobs.*.spec.protocol_hash` ×7; `child_plans.<run#1>.events.*.payload.protocol_hash` ×5; `child_plans.<run#1>.evaluations.*.row.protocol_hash` ×4; `child_plans.<run#1>.jobs.*.spec.protocol_hash` ×4; `children_after.<run#1>.evaluations.*.row.protocol_hash` ×4; `children_after.<run#1>.jobs.*.spec.protocol_hash` ×4; `child_plans.<run#1>.run.protocol_hash` ×1; `child_plans.<run#2>.run.protocol_hash` ×1; `children_after.<run#1>.run.protocol_hash` ×1; `children_after.<run#2>.run.protocol_hash` ×1; `parent_after.run.protocol_hash` ×1
- `protocol_hash_embedding_file` (22): `children_after.<run#2>.checkpoints.*.sha256` ×14; `children_after.<run#1>.checkpoints.*.sha256` ×8
- `timestamp` (528): `children_after.<run#2>.events.*.created_at` ×39; `children_after.<run#2>.artifacts.*.created_at` ×28; `children_after.<run#2>.artifacts.*.updated_at` ×28; `children_after.<run#1>.events.*.created_at` ×24; `children_after.<run#1>.artifacts.*.created_at` ×16; `children_after.<run#1>.artifacts.*.updated_at` ×16; `children_after.<run#2>.checkpoints.*.created_at` ×14; `children_after.<run#2>.checkpoints.*.updated_at` ×14; `parent_after.events.*.created_at` ×12; `parent_after.events.*.payload.created_at` ×10; `parent_after.events.*.payload.updated_at` ×10; `child_plans.<run#2>.events.*.created_at` ×9; `selection_inputs.*.created_at` ×9; `selection_inputs.*.updated_at` ×9; `children_after.<run#1>.checkpoints.*.created_at` ×8; `children_after.<run#1>.checkpoints.*.updated_at` ×8; `child_plans.<run#2>.candidates.*.created_at` ×7; `child_plans.<run#2>.candidates.*.updated_at` ×7; `child_plans.<run#2>.evaluations.*.created_at` ×7; `child_plans.<run#2>.evaluations.*.updated_at` ×7; `child_plans.<run#2>.jobs.*.created_at` ×7; `child_plans.<run#2>.jobs.*.updated_at` ×7; `children_after.<run#2>.candidates.*.created_at` ×7; `children_after.<run#2>.candidates.*.updated_at` ×7; `children_after.<run#2>.checkpoints.*.pruned_at` ×7 (+54 patterns, T2_SUMMARY.json)
- `wall_clock_duration` (232): `parent_after.events.*.payload.costs.actual_gpu_seconds` ×10; `parent_after.events.*.payload.costs.baseline_gpu_seconds` ×10; `parent_after.events.*.payload.costs.final_gpu_seconds` ×10; `parent_after.events.*.payload.costs.final_gpu_seconds_children` ×10; `parent_after.events.*.payload.costs.ga_billed_gpu_seconds` ×10; `parent_after.events.*.payload.costs.random_billed_gpu_seconds` ×10; `selection_inputs.*.row.cost.train` ×9; `child_plans.<run#2>.candidates.*.audit.orchestration.rungs.FINAL.admission.estimate_s` ×7; `child_plans.<run#2>.jobs.*.spec.params.admission.estimate_s` ×7; `children_after.<run#2>.candidates.*.audit.orchestration.rungs.FINAL.admission.estimate_s` ×7; `children_after.<run#2>.costs.*.gpu_seconds` ×7; `children_after.<run#2>.evaluations.*.row.cost.train` ×7; `children_after.<run#2>.events.*.payload.cost.train` ×7; `children_after.<run#2>.jobs.*.spec.params.admission.estimate_s` ×7; `child_plans.<run#2>.candidates.*.audit.orchestration.rungs.FINAL.admission.remaining_s` ×6; `child_plans.<run#2>.candidates.*.audit.orchestration.rungs.FINAL.admission.reserved_s` ×6; `child_plans.<run#2>.jobs.*.spec.params.admission.remaining_s` ×6; `child_plans.<run#2>.jobs.*.spec.params.admission.reserved_s` ×6; `children_after.<run#2>.candidates.*.audit.orchestration.rungs.FINAL.admission.remaining_s` ×6; `children_after.<run#2>.candidates.*.audit.orchestration.rungs.FINAL.admission.reserved_s` ×6; `children_after.<run#2>.jobs.*.spec.params.admission.remaining_s` ×6; `children_after.<run#2>.jobs.*.spec.params.admission.reserved_s` ×6; `child_plans.<run#1>.candidates.*.audit.orchestration.rungs.FINAL.admission.estimate_s` ×4; `child_plans.<run#1>.jobs.*.spec.params.admission.estimate_s` ×4; `children_after.<run#1>.candidates.*.audit.orchestration.rungs.FINAL.admission.estimate_s` ×4 (+26 patterns, T2_SUMMARY.json)
- `worker_device_identity` (11): `children_after.<run#2>.costs.*.device_uuid` ×7; `children_after.<run#1>.costs.*.device_uuid` ×4
- `worker_telemetry` (2): `children_after.<run#1>.worker_events` ×1; `children_after.<run#2>.worker_events` ×1

### T2-9 — seed streams (100 fixed inputs)

판정: **EXACT_MATCH**

- ref_run (exit 0, 0.5 s): `runtime/venv/bin/python -B -s tests/owner/t2/reference_runner.py --snapshot <SNAPSHOT> --scenario T2-9 --work <WORK> --out <OUT>/T2-9.ref.json`
- new_run (exit 0, 0.6 s): `runtime/venv/bin/python -B -s tests/owner/t2/new_runner.py --snapshot <SNAPSHOT> --scenario T2-9 --work <WORK> --out <OUT>/T2-9.new.json`
- 입력: 고정 입력 t2_core.py 코드 내 고정
- 산출: `docs/internal/evidence/WP4/4C/t2/T2-9.ref.json` (sha256 `5bf1d0c9c85fad2a…`), `docs/internal/evidence/WP4/4C/t2/T2-9.new.json` (sha256 `bd6f6c97161b7442…`)
- 격리 위반: ref 0건, new 0건; 구조 해시 기록 ref 0 / new 0

실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):

- rows: 100
- first_key: mbnas-search-stream/1\|seed=0\|gen=0\|prior

### T2-10 — generation-only OFF run (seeds 2, candidate_count 2, max_attempts 6)

판정: **ALLOWED_DIFF**

- ref_run (exit 0, 4.7 s): `runtime/venv/bin/python -B -s tests/owner/t2/reference_runner.py --snapshot <SNAPSHOT> --scenario T2-10 --work <WORK> --out <OUT>/T2-10.ref.json --inputs <OUT>/inputs/T2-10.input.json`
- new_run (exit 0, 4.9 s): `runtime/venv/bin/python -B -s tests/owner/t2/new_runner.py --snapshot <SNAPSHOT> --scenario T2-10 --work <WORK> --out <OUT>/T2-10.new.json --inputs <OUT>/inputs/T2-10.input.json --ref <OUT>/T2-10.ref.json`
- 입력: 고정 입력 `sha256:e8435833abd64292436894309dc1805f1438e9f0556b3e519d697355f9770630` (`docs/internal/evidence/WP4/4C/t2/inputs/T2-10.input.json`); 2단계 입력(ref 산출) `sha256:6bb9d552b47e1d924d1ad72109917d583a532eefec8dd76379da1700c23a027a` = new가 소비한 입력 `sha256:6bb9d552b47e1d924d1ad72109917d583a532eefec8dd76379da1700c23a027a` → 일치
- 산출: `docs/internal/evidence/WP4/4C/t2/T2-10.ref.json` (sha256 `06bae913138ccd23…`), `docs/internal/evidence/WP4/4C/t2/T2-10.new.json` (sha256 `117fcdd52f01f951…`)
- 격리 위반: ref 0건, new 0건; 구조 해시 기록 ref 5 / new 5

실행된 결정(ref 산출 기준; new 동일 여부는 위 판정):

- run: COMPLETED None
- jobs_executed: 2
- journals: {"generation-19.json": "attempts 3 duplicates 1 failures {} accepted 2", "generation-7.json": "attempts 2 duplicates 0 failures {} accepted 2"}
- yaml_files: 4
- candidates_in_db: 4
- generation_results: {"19": "COMPLETE generated 2/2", "7": "COMPLETE generated 2/2"}

허용 차이 경로 (규칙 → 경로 패턴 × 건수):

- `hash_excluded_extended` (1): `validation.resolved.resolution.hash_excluded` ×1
- `key:module_path_string` (6): `db.jobs.*.result.outputs.space.templates.*.source.<POOL_FILES_SHA256>` ×6
- `mixed_pool_provenance` (8): `db.jobs.*.result.outputs.space.templates.*.source.<POOL_FILES_SHA256>` ×6; `db.jobs.*.result.outputs.space.etri` ×2
- `module_path:structural_hash` (21): `db.artifacts.*.meta.architecture_hash` ×3; `db.candidates.*.architecture_hash` ×3; `db.candidates.*.audit.structure.architecture_hash` ×3; `db.jobs.*.result.outputs.candidates.*.row.architecture_hash` ×3; `db.jobs.*.result.outputs.candidates.*.row.audit.structure.architecture_hash` ×3; `journals.generation-19.json.candidates.*.row.architecture_hash` ×2; `journals.generation-19.json.candidates.*.row.audit.structure.architecture_hash` ×2; `journals.generation-7.json.candidates.*.row.architecture_hash` ×1; `journals.generation-7.json.candidates.*.row.audit.structure.architecture_hash` ×1
- `module_path_string` (78): `db.candidates.*.audit.structure.blocks.*.catalog.reference` ×22; `db.jobs.*.result.outputs.candidates.*.row.audit.structure.blocks.*.catalog.reference` ×22; `journals.generation-19.json.candidates.*.row.audit.structure.blocks.*.catalog.reference` ×21; `db.jobs.*.result.outputs.space.templates.*.source.function` ×6; `db.jobs.*.result.outputs.space.templates.*.source.kind` ×6; `journals.generation-7.json.candidates.*.row.audit.structure.blocks.*.catalog.reference` ×1
- `own_resolution_hash` (1): `validation.protocol_hash` ×1
- `protocol_hash_definition` (16): `db.events.*.payload.protocol_hash` ×10; `db.jobs.*.spec.protocol_hash` ×2; `db.run.protocol_hash` ×1; `journals.generation-19.json.protocol_hash` ×1; `journals.generation-7.json.protocol_hash` ×1; `protocol_hash` ×1
- `realpath_dropped` (1): `validation.resolved.resolution.paths.output_root.realpath` ×1
- `search_space_hash` (30): `db.candidates.*.lineage.search_space.prior_hash` ×4; `db.candidates.*.lineage.search_space.space_hash` ×4; `db.jobs.*.result.outputs.candidates.*.row.lineage.search_space.prior_hash` ×4; `db.jobs.*.result.outputs.candidates.*.row.lineage.search_space.space_hash` ×4; `db.jobs.*.result.outputs.space.prior_hash` ×2; `db.jobs.*.result.outputs.space.space_hash` ×2; `db.jobs.*.result.outputs.space.space_hash_definition` ×2; `journals.generation-19.json.candidates.*.row.lineage.search_space.prior_hash` ×2; `journals.generation-19.json.candidates.*.row.lineage.search_space.space_hash` ×2; `journals.generation-7.json.candidates.*.row.lineage.search_space.prior_hash` ×2; `journals.generation-7.json.candidates.*.row.lineage.search_space.space_hash` ×2
- `search_space_version` (14): `db.candidates.*.lineage.search_space.version` ×4; `db.jobs.*.result.outputs.candidates.*.row.lineage.search_space.version` ×4; `db.jobs.*.result.outputs.space.version` ×2; `journals.generation-19.json.candidates.*.row.lineage.search_space.version` ×2; `journals.generation-7.json.candidates.*.row.lineage.search_space.version` ×2
- `timestamp` (82): `db.events.*.created_at` ×18; `db.events.*.payload.created_at` ×10; `db.events.*.payload.updated_at` ×10; `db.artifacts.*.created_at` ×4; `db.artifacts.*.updated_at` ×4; `db.candidates.*.audit.structure.observation.checked_at` ×4; `db.candidates.*.created_at` ×4; `db.candidates.*.updated_at` ×4; `db.jobs.*.result.outputs.candidates.*.row.audit.structure.observation.checked_at` ×4; `db.jobs.*.created_at` ×2; `db.jobs.*.finished_at` ×2; `db.jobs.*.heartbeat_at` ×2; `db.jobs.*.result.finished_at` ×2; `db.jobs.*.result.started_at` ×2; `db.jobs.*.started_at` ×2; `db.jobs.*.updated_at` ×2; `journals.generation-19.json.candidates.*.row.audit.structure.observation.checked_at` ×2; `journals.generation-7.json.candidates.*.row.audit.structure.observation.checked_at` ×2; `db.run.created_at` ×1; `db.run.updated_at` ×1
- `wall_clock_duration` (2): `db.jobs.*.result.outputs.cpu_seconds` ×2
- `worker_telemetry` (1): `db.worker_events` ×1

## 허용 차이 규칙 정의

| 규칙 | 종류 | 경로 패턴 | 근거 |
|---|---|---|---|
| `timestamp` | timestamp | `**` | task brief (known allowed differences): timestamps; T2_REFERENCE_PLAN.md section 2 (allowed-difference column) T2-7 '시각' (scope: all) |
| `parser_source_sha256` | record | `**.parser_source_sha256` | task brief (known allowed differences); T2_REFERENCE_PLAN.md section 2 (allowed-difference column) T2-1 (scope: all) |
| `catalog.source_path` | record | `**.catalog.source_path` | task brief (known allowed differences); T2_REFERENCE_PLAN.md section 2 (allowed-difference column) T2-1 (scope: all) |
| `search_space_version` | expect | `**.version` | task brief (known allowed differences): search_space /2 -> /3 (compare version only); DD-A12 hash definition mbnas-mixed-space/3 (scope: all) |
| `search_space_hash` | record | `**.space_hash`, `**.prior_hash`, `**.space_hash_definition` | task brief (known allowed differences): record hashes only; T2_REFERENCE_PLAN.md section 2 (allowed-difference column) T2-2 lineage.search_space.{space_hash,prior_hash} (scope: all) |
| `mixed_pool_provenance` | record | `**.etri`, `**.source.<POOL_FILES_SHA256>` | DD-A12: the /2 identity hashed the executed ETRI yolo_ga_runtime files, /3 hashes the three vendored files (provenance of the search-space describe(); T2_REFERENCE_PLAN.md section 2 (allowed-difference column) T2-2 'origin kind 문자열') (scope: all) |
| `preset_paths` | record | `presets.*.console.paths` | task brief (known allowed differences): paths; DD-A28 preset_paths() placeholders (scope: T2-2) |
| `wall_clock_duration` | timing | `**.gpu_seconds`, `**.costs.*`, `**.cost.*`, `**.admission.billed_s`, `**.admission.reserved_s`, `**.admission.remaining_s`, `**.admission.estimate_s`, `**.meta.attempt_gpu_seconds`, `**.meta.inline_profile_gpu_seconds`, `**.cpu_seconds`, `**.timings.*` | T2_REFERENCE_PLAN.md section 2 (allowed-difference column) T2-7 '시각' (GPU-seconds / CPU-seconds measured from the wall clock of each attempt; the decision fields admitted/reason_code/estimate_source/cap_s stay strict) (scope: T2-7, T2-8, T2-10) |
| `worker_telemetry` | record | `**.worker_events` | T2_REFERENCE_PLAN.md section 2 (allowed-difference column) T2-7/T2-10 worker_id; worker.updated rows carry worker id, pid and device samples (DD-A27: the owner worker no longer samples NVML) (scope: T2-7, T2-8, T2-10) |
| `worker_device_identity` | record | `**.costs.*.device_uuid` | T2_REFERENCE_PLAN.md section 2 (allowed-difference column) T2-7/T2-10 worker_id: the ledger row stores the executing worker's device identity (original: NVML uuid, None without NVML; new owner worker: synthetic 'gpu-index-N', DD-A27; DD-A19 runner-issued ids); stored only, never read by a decision (scope: T2-7, T2-8, T2-10) |
| `etri_root_resolution` | record | `**.search_space_resolved.etri_root`, `validation.capabilities.search_space.etri_root`, `validation.capabilities.search_space.detail`, `validation.capabilities.search_space.probe.checked`, `validation.resolved_diff.*.reason (when field == 'console.search.search_space_resolved', 1 level(s) up)`, `validation.resolved_diff.*.resolved.etri_root (when field == 'console.search.search_space_resolved', 2 level(s) up)` | DD-A12: search_space_resolved.etri_root is null for the vendored pool; probe/detail text names the pool (scope: T2-7, T2-8, T2-10) |
| `own_resolution_hash` | record | `validation.protocol_hash` | derived: hash of each side's own resolution, which differs only by the etri_root rule above (the engine itself runs on the reference resolution on both sides and its protocol_hash is compared strictly) (scope: T2-7, T2-8, T2-10) |
| `protocol_hash_definition` | phash | `protocol_hash`, `**.protocol_hash`, `**.hashes.protocol` | DD-A53 (c): RESOLUTION_HASH_EXCLUDED += resolution.paths and no realpath/etri_root recorded (contract 1.5.0) -> protocol_hash definition changed; derived differences only (scope: T2-7, T2-8, T2-10) |
| `protocol_hash_embedding_file` | phash_file | `**.checkpoints.*.sha256` | DD-A53 (c): RESOLUTION_HASH_EXCLUDED += resolution.paths and no realpath/etri_root recorded (contract 1.5.0) -> protocol_hash definition changed; derived differences only; the stub checkpoint file embeds the protocol hash (allowed only when the row's hashes.protocol differs by the same mapping) (scope: T2-7, T2-8, T2-10) |
| `realpath_dropped` | absent_new | `validation.resolved.resolution.paths.*.realpath` | DD-A53 (c): RESOLUTION_HASH_EXCLUDED += resolution.paths and no realpath/etri_root recorded (contract 1.5.0) -> protocol_hash definition changed; derived differences only (scope: T2-7, T2-8, T2-10) |
| `hash_excluded_extended` | list_plus | `validation.resolved.resolution.hash_excluded` | DD-A53 (c): RESOLUTION_HASH_EXCLUDED += resolution.paths and no realpath/etri_root recorded (contract 1.5.0) -> protocol_hash definition changed; derived differences only (scope: T2-7, T2-8, T2-10) |
| `module_path_string` | normaliser | 문자열 치환표(아래) | task: module path strings (DD-A2/DD-A12 이동) |
| `module_path:structural_hash` | normaliser | 원시 architecture hash → 정규화 hash | DD-A29: structural_hash는 `type(m).__module__`을 해시 — ETRI 연산자 모듈 경로만 `<etri_mixed_blocks>`로 바꾼 정규화 hash가 같아야 허용 |
| `path` | normaliser | work/snapshot/repo 루트 → `<WORK>`/`<SNAPSHOT>`/`<REPO>` | task: paths (normalise) |
| `generated_id` | normaliser | job/run/val/ck id → 첫 등장 순서 라벨 | plan T2-7 job_id |

모듈 경로 문자열 치환표:

- `yolo_ga_runtime/mixed_blocks.py` ↔ `mbnas_model_runtime/operators/etri_mixed_blocks.py (vendored from yolo_ga_runtime/mixed_blocks.py sha256 4233dd32…)` → `<ETRI_OPS_SOURCE>`
- `mbnas_server/adapters/etri_hybrid/model.py` ↔ `mbnas_model_runtime/adapters/etri_hybrid/model.py` → `<ETRI_HYBRID_SOURCE>`
- `mbnas_server.search.etri_bridge:ensure_mixed_registered` ↔ `mbnas_model_runtime.operators:ensure_registered` → `<MIXED_REGISTER_HOOK>`
- `mixed_search_space.make_mixed_seed_variants` ↔ `mbnas_core.search.mixed_pool.seed_variants.make_mixed_seed_variants` → `<SEED_VARIANTS_FN>`
- `etri_mixed_seed_variant` ↔ `vendored_mixed_seed_variant` → `<SEED_VARIANT_KIND>`
- `etri_files_sha256` ↔ `mixed_pool_files_sha256` → `<POOL_FILES_SHA256>`
- `yolo_ga_runtime.mixed_blocks` ↔ `mbnas_model_runtime.operators.etri_mixed_blocks` → `<ETRI_OPS_MODULE>`
- `mbnas_server.adapters.etri_hybrid.model` ↔ `mbnas_model_runtime.adapters.etri_hybrid.model` → `<ETRI_HYBRID_MODULE>`

## 방법·하네스 조치

- 양쪽 runner는 모든 MBNAS import 전에 `uuid.uuid4`를 결정적 단조 수열로 교체(작업 lease의 `ORDER BY priority, created_at, id` 동률 해소를 재현 가능하게; id는 비교기에서도 정규화), `pynvml` 차단, CUDA 비활성.
- `adapters.common.structural_hash`를 반환값 불변 래퍼로 감싸 원시→정규화 hash 쌍을 기록(`meta.arch_hash_map`).
- 2단계 입력: T2-3 부모 pool 유전자형, T2-4 프로토콜, T2-7/T2-8/T2-10 요청·resolved 프로토콜은 ref 산출을 new가 그대로 소비(입력 sha256 일치 확인). 각 측의 자체 검증 결과(validation)는 별도로 비교.
- T2-7/T2-8 엔진 흐름: HTTP 계층 없이 `POST /runs`의 생성 트랜잭션(create_run → plan_run → settle_run)을 그대로 재현하고, 각 측 자기 하네스의 InlineWorker(원본: snapshot tests/worker/harness.py, new: tests/_shared/harness.py)와 stub 핸들러(MBNAS_STUB_EPOCH_S=0)로 drain.
- 하네스 파일 sha256: `tests/owner/t2/t2_core.py` `de49c6fc9d4e…`, `tests/owner/t2/reference_runner.py` `5e8b600f2377…`, `tests/owner/t2/new_runner.py` `63f7e8151ab8…`, `scripts/owner/t2_compare.py` `b34867c15bac…`

## 관찰 사항 (설계자 판단 요청)

- `worker_device_identity` (T2-7/T2-8/T2-10의 `costs[*].device_uuid`): 원본 worker는 NVML uuid(여기서는 NVML 없음 → null)를, 새 owner worker는 합성 값 `gpu-index-N`을 원장에 기록한다(DD-A27: core에서 NVML 제거). 결정에 쓰이지 않는 저장 필드라 plan의 worker_id 허용으로 분류했지만, 원장의 장치 식별 의미가 바뀐 것이므로 설계자 확인을 요청한다. 같은 worker의 `worker.updated` 이벤트에는 `device` = `<hostname>-gpu-N`(원본 `host-gpu-N`), `device_name` = "unverified device (owner worker has no NVML)"가 실린다(호스트명이 owner DB 이벤트에 기록됨 — 공개 투영(WP-4)에서 제거되는지 확인 필요).
- T2-1 `err_huge_row`(repeats 64, 출력 채널 65536)는 양쪽 `check_structure`에서 PASS(채널은 scale의 max_channels로 잘림)이며, `err_unknown_module`은 양쪽 모두 BLOCK_UNSUPPORTED FAIL. 새 콘솔 계층의 DD-A20 선검사 `validate_declared`는 이 두 YAML을 빌드 전에 거부한다(정보 기록: `T2-1.new.json` info.validate_declared) — check_structure 결정 자체는 동일.
- 원시 architecture_hash는 ETRI 연산자(NASPatchMerge/NASWindowTransformer/NASGlobalTransformer)를 포함하는 모델에서만 달라지고, 모듈 경로를 정규화한 hash는 모든 경우 같다(기존 run의 architecture_hash와 새 run의 hash는 직접 비교 불가 — DD-A29에 이미 기록된 성질).

## 한계 (확인 못 함)

- GPU 경로(실제 학습·평가·latency)는 T2 범위 밖: T2-7/T2-8의 학습·평가는 stub 핸들러(결정적 합성 metric)이며 실제 trainer/evaluator 동등성은 T7/WP-G2(GPU 창) 대상. CPU 빌드·forward·구조 검사·제안기는 실제 코드로 실행.
- 원격 runner 경로(`/core/v1/runner`, envelope, ticket)는 실행하지 않았다: T2-7/T2-8은 owner InlineWorker로 실행되며 새 측 결과 반영은 owner worker `_finalize` → `runner_api.settle.settle_job`(원격 결과와 같은 함수)을 거친다.
- HTTP 계층(validate/runs 라우트, 공개 투영, gateway)은 비교하지 않았다: run 생성은 라우트의 트랜잭션을 직접 재현.
- ETRI mixed pool은 DD-A12 Option A 이식 범위만 비교(seed variants, 연산자, parse_model 확장). 이식되지 않은 `mutate_operators/_legal_choices/symbolic_shape_propagate`는 서버 호출자가 없어 비교 대상 없음.
- 결정성 조치(uuid4 결정화)는 양쪽 동일 적용이며, 원본 자체의 무작위 id 동률 해소(같은 created_at)에 의한 비결정성은 이 시험이 다루지 않는다. `ck-` checkpoint id는 os.urandom이라 정규화로 비교.
- 새 패키지는 다른 에이전트가 동시에 수정 중이다: 결과는 보고서 머리의 새 패키지 source tree hash와 각 산출 JSON의 `meta.isolation.side_module_origins`(모듈별 sha256) 시점의 코드에 대한 것이다.

## 재현

- 전체: `CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 runtime/venv/bin/python -B scripts/owner/t2_compare.py` (기본 출력 `docs/internal/evidence/T2/`, `--scenario T2-n` 반복 지정 가능, `--work-root DIR`, `--keep-work`).
- pytest: `runtime/venv/bin/python -m pytest tests/owner/t2 -q` (빠른 T2-5/T2-6/T2-9만; 전체는 `MBNAS_TEST_T2=1`).


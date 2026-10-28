# C06 복구·재현 — 독립 평가 1회차 (EVAL_1)

| 항목 | 값 |
|---|---|
| 기준셋 | C06 `docs/internal/criteria/C06.json` v1.0 (search_train_rng · public_id · full_state · policy_compat, 각 25) |
| 평가자 | claude-opus-5-5 (독립 평가자: 채점만, 판정은 설계자) |
| 시각 | 2026-10-28 03:16–03:37 KST |
| tree hash (`runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`, mbnas-source-tree/2) | 시작 `sha256:634a083bb910a28d1aa183c9a6a2cee66da7c1fc937f01f8aa85d1bb4c4a301d` (221 files) → 종료 `sha256:76ff726a1fd34353c085913f33d0bbee8ce646ed7beeb41f68000d19e3559fc0` |
| 트리 이동 사유 | 평가 중 다른 에이전트가 DD-A55(a) 수정 투입: `mbnas_core/.../orchestration/retention.py`, `console/routes_runs.py`(checkpoint `artifact_id`), 신규 `tests/owner/artifacts/test_checkpoint_artifact_id.py`. 이 diff가 닿는 public_id 묶음은 76ff726a에서 재실행함(아래 2절). |
| 실행 환경 | `runtime/venv/bin/python -B -m pytest -p no:cacheprovider`, `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `--basetemp`=스크래치, 마커 `not gpu and not live and not legacy_records and not host_data`. GPU·/data·etri_nas·build/source_snapshot 미기록. CPU 사용 합계 약 6.5분. |
| 원 출력(세션 임시) | `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c06/` (pytest_rng.txt, pytest_projection.txt, pytest_projection_rerun.txt, pytest_fullstate.txt, pytest_compat.txt, pytest_salt.txt, t2_9/, t2_T2-2/, t2_T2-3/, t2_T2-10/, preset_hash_rerun.json, import_closure.txt) |

결론: **85/100 — 미통과**(조건 >90 미달; Blocker 0, Critical 0). 감점 3건은 모두 Minor(D1 공개 runner id 기본값, D2 현 트리 2프로세스 재개 기록 부재, D3 hook 예외 만료 라벨). 이 중 두 건을 해소하면 95가 된다.

---

## 1. search_train_rng — 25/25 (anchor 25)

**근거**
- `mbnas_core/src/mbnas_core/search/streams.py` sha256 `ea4f6241…f27f24` = 스냅샷 `build/source_snapshot/application/MBNAS_server/src/mbnas_server/search/streams.py`와 바이트 동일(sha256sum). 스트림 키 `mbnas-search-stream/1|seed=<s>|gen=0|prior` / `arm=<a>|gen=<g>`(streams.py:16, 25-32), training seed `sha256(mbnas-train-seed/1|base|gen|idx)[:8] mod 2^31`, base는 `training.seed_policy` 또는 search seed(streams.py:17, 35-49).
- `search/generation.py`(OFF 경로, `random.Random(seed)`+journal `rng_state`, :110-125): 스냅샷 대비 차이는 import 재배치 5줄(25-28, 84)뿐. `orchestration/final_retrain.py:39, 149-156`(FINAL_SEED_ID·final_training_seed), `orchestration/engine.py:57-59`(derive_seed) 스냅샷과 동일. `search/handlers.py`는 스냅샷 대비 18줄 차이 중 seed/stream/rng/random 관련 0줄.
- 공개 문서: `docs/public/SECURITY_MODEL.md:92` "seed는 비밀이 아니다 … 같은 seed와 프로토콜로 결과를 재현할 수 있는 것은 의도된 성질".
- 유도식 비공개: 계약 1.5.0 `CandidateDetail.audit.orchestration.final.training_seed_policy`는 닫힌 객체 {base, base_source, final_id, id, requested} — `derivation`/`final_derivation` 없음. `rng_stream`은 owner 전용 canary(`tests/owner/projection/projection_world.py:197`)로 공개 0건 시험됨. 공개 패키지·`docs/public`·`mbnas_web/src`·`dist`에서 `mbnas-search-stream|mbnas-train-seed|mbnas-final-train-seed` grep 0건(exit 1).

**재실행 (tree 634a083b)**
- pytest: `tests/owner/api/test_generation_multi_seed.py` 7, `tests/owner/search/test_proposers.py` 12(:66 metrics 무관 random 순서, :82 같은 seed 재현, :223 training seed 정책이 제안을 바꾸지 않음), `tests/owner/qa/test_search_fairness_engine.py` 10, `tests/owner/search/test_generation_recovery_independent.py` 7, `tests/owner/search/test_generation_only.py` 19, `tests/owner/t2/test_t2_equivalence.py::test_t2_fast` 3(T2-5/6/9) → **58 passed, 0 failed, exit 0, 93.5 s**.
- `scripts/owner/t2_compare.py --scenario T2-9 --out-dir <scratch> --work-root <scratch> --no-report` → **exit 0, EXACT_MATCH(allowed 0, mismatches 0)**, snapshot_guard unchanged. 100 입력(seed 10 × arm 2 × gen 5)의 스트림 키·draw·RNG 상태·training seed(정책 5종)·candidate id·derive_seed·final seed가 원본 단일 패키지와 일치.
- 같은 방식 T2-2(random gen0) **ALLOWED_DIFF 78 / mismatch 0**, T2-3(GA gen0·gen1) **136 / 0**, T2-10(OFF 생성, journal RNG) **261 / 0**, 모두 exit 0·snapshot unchanged. 허용 차이는 문서화 규칙(/2→/3 space version·hash, module path 문자열, structural hash 모듈 매핑, timestamp, DD-A53 protocol_hash 정의)뿐.

**공백**: 실질 없음. 표기: C06.json mandatory_checks의 `tests/owner/search/test_generation_multi_seed`는 실제 `tests/owner/api/test_generation_multi_seed.py`(기준 파일 표기 오류, 프로젝트 결함 아님, 감점 없음).

## 2. public_id — 20/25 (anchor 20: 사소한 누락 외 충족)

**근거**
- 구현: `mbnas_core/src/mbnas_core/console/projection.py` — :66-79 PublicIds(HMAC-SHA256, DB별 salt) → `wrk-<16hex>`, `dev-<16hex>`; :86 `configure`; :268-283 `_worker`(owner 행 `hostname:pid`→`wrk-`, runner 행은 runner id 유지, `device_uuid` 제거→`device_id`); :298-310 중첩 uuid→device_id; :330-334 `audit.generation.worker_id`. salt는 `mbnas_core/src/mbnas_core/api/app.py:93-97`이 시작 시 `meta.public_id_salt`(`db/core.py:124-127`)로 설치.
- 계약 1.5.0(openapi.json sha256 `70e0cffa…` = DD-A53): `Generation` 닫힘·`group_key` 없음; `Worker.device_uuid`·`raw_metrics.device_uuid`는 deprecated 표기(DD-A42, DECISIONS.md:53) — 투영이 항상 제거.
- 시험: `tests/owner/projection/test_value_rules.py:82-95`(gateway·core 양쪽: worker id에 `:`·호스트명 없음, `device_uuid` 부재, `device_id`=`dev-[0-9a-f]{8,64}`), `test_canary_projection.py:187`(OWNER_ONLY_KEYS에 group_key·child_pid 등 → 공개 본문 키 0), `projection_world.py:549-555`(모든 generation 행 group_key에 canary 심음), `test_public_golden.py:176, 185`(uuid 유래 id·값 보존, group_key만 제외). uuid 유래 id 유지·candidate_id 비매핑은 DD-A19(DECISIONS.md:26).

**재실행**
- `tests/owner/projection` (tree 634a083b): **259 passed**(canary 47, revisions 9, contract_150 157, owner_routes 14, golden 16, T8 4, value_rules 12), **exit 0**, 19.3 s.
- 같은 묶음 + `tests/owner/api/test_checkpoint_state_row.py` 2 + `tests/owner/artifacts/test_checkpoint_artifact_id.py` 3 (tree **76ff726a**, DD-A55 반영 후): **264 passed, exit 0**, 35.7 s.
- salt 영속: `tests/owner/unit/test_db_migration.py` + `test_db.py` **17 passed, exit 0**.
- gateway가 자체적으로 uuid/worker id를 만들지 않음: `mbnas_server/src`에서 `uuid|hostname|getpid|worker_id` grep → `config.py:246-248`·`doctor.py:40`의 URL hostname뿐.

**공백 (D1, 감점 사유)**: runner 행 id는 투영을 그대로 통과하는데(projection.py:278), 기본 runner id가 호스트명에서 나온다 — `mbnas_runner/src/mbnas_runner/config.py:242` `runner-{socket.gethostname()}`, `scripts/install.sh:457-466`이 그 값으로 runner principal을 발급. "core 발급"은 형식(principal 바인딩)일 뿐 값은 runner 호스트명이다. 실측: 진행 중인 T4(owner ZIP 격리 설치, bwrap `--hostname t4-sandbox`, `runtime/test_root/t4t5/harness/sbx.sh:11`)가 gateway 공개 경로 `GET /api/v1/runs/{id}/snapshot`(harness/t45.py:156, 534)로 받은 `runtime/test_root/t4t5/t4f/evidence/t4_nvml_decision_snapshot.json`에 `"id": "runner-t4-sandbox"`·`"runner_id": "runner-t4-sandbox"` 각 6회. 시험이 못 잡는 이유: test_value_rules.py:95 정규식이 임의 `[A-Za-z0-9._-]+`를 허용하고, HOST_RE(:23)와 projection `_host_re`의 lookbehind가 `-` 뒤 호스트명을 매치하지 않으며, projection world는 `canary-runner`만 등록(projection_world.py:941). 문서는 모두 opaque라고 쓴다: `docs/public/COMPATIBILITY.md:25`, `API.md:98`("작업자·장치는 … opaque id(`wrk-…`, `dev-…`)"), `SECURITY_MODEL.md:71`. 정책 비밀 누출은 아니고(대개 고객 자기 호스트명) 기본값·시험·문서의 불일치라 Minor.

## 3. full_state — 20/25 (anchor 20)

**근거**
- 형식: `mbnas_model_runtime/src/mbnas_model_runtime/checkpoint.py:1-17, 36-38`(`mbnas.full_state.v1`, `weights_only=True` 로드, sha256 sidecar, GENERIC_REQUIRED에 rng.python/numpy/torch_cpu; `capture_rng` :43-61은 torch_cuda 포함). ultralytics REQUIRED_STATE(`adapters/ultralytics_adapter.py:54-56`: model_fp32, ema_fp32, ema_updates, optimizer, scheduler, scaler, loader_generator_state, …). ETRI(`adapters/etri_hybrid/adapter.py:29-30, 340-347, 394-401`: EMA 켜졌는데 상태가 없으면 `MISSING_STATE:ema_fp32`). RF-DETR native(`rfdetr_adapter.py:55-56`)는 공개 runner에서 pickle 재개 기본 비활성(DD-A20, COMPATIBILITY.md:55) — 승인된 기능 손실.
- 대체 금지: `tests/public/model_runtime/test_checkpoint.py`(:42 weights_only 아닌 값 거부, :105 stripped state 차단, :116 hash 불일치 차단, :123 외부 파일 FORMAT_UNKNOWN), `tests/public/runner/test_stage_trainer.py`(:76 전부 손상→NO_VALID_CHECKPOINT, :135 ckpt 디렉터리 삭제→MISSING_STATE, :152 payload 삭제→차단, :176 이전 stage 미달→차단; scratch는 첫 stage만, 이전 검증 full state로의 대체는 note로 기록 :164).
- T7(GPU, 기록 증거): `docs/internal/evidence/T7/resume_equivalence.json` verdict PASS — yolo11n 2→4→6(새 프로세스) vs 연속 6에서 경계 2/4/6마다 model·EMA bitwise(max|d| 0.0), optimizer digest·scheduler·scaler·counter·LR·loader generator 동일, rng_equal{python, numpy, torch_cpu, torch_cuda} 모두 true; 음성 3건 차단(`MISSING_STATE:optimizer` "no weights-only fallback", `HASH_MISMATCH:architecture`, `FORMAT_UNKNOWN`). `T7/README.md:22` G2-1(c): core·gateway·owner·runner 별도 프로세스(steps.jsonl item c, pid 기록)에서 GPU pause(full_e2)→resume attempt 2 `resumed_from=…/full_e0003.pt` 8/8 epoch PASS. 둘 다 tree 164292c6.
- 트리 차이 확인: 재개 경로 6파일(checkpoint.py, ultralytics_adapter.py, adapters/base.py, runner training/stage_trainer.py·handlers.py, scripts/runner/resume_equivalence.py)이 `runtime/test_root/g2_tree`·`g2_tree2` 소스와 g2_tree venv 설치본(5파일)에 대해 `cmp` 동일 → T7 결과는 현 코드의 재개 경로에 그대로 적용.

**재실행 (tree 634a083b)**: test_checkpoint 18, test_stage_trainer 19(:101 실제 ETRI adapter CPU 1→2→3 = 연속 3, model·EMA·optimizer·RNG bitwise), test_resume_equivalence_script 1, `tests/owner/api/test_checkpoint_state_row.py` 2, `tests/owner/runner_protocol/test_t3_control.py` 6(:65 T3-07 pause→PAUSED_AT_SAFE_BOUNDARY→resume attempt 2, epoch 1..6 정확히 1회, 원장 [paused, succeeded]; child는 실제 subprocess, 학습은 stub), `test_t3_restart.py` 3, `tests/owner/training/test_explicit_cpu.py` 1(실제 ultralytics CPU 학습 pause→full_e0001에 model_fp32/optimizer/scheduler/scaler/ema_fp32/rng/loader_generator_state 존재→resume SUCCEEDED, resumed_from full_e0001.pt), `tests/owner/worker/test_subprocess.py` 2 → **52 passed, 1 deselected(gpu: test_rng_roundtrip_includes_cuda), exit 0, 95.7 s**.

**공백 (D2, 감점 사유)**: 필수 점검 "WP-2 two-process smoke 또는 T4 재개"의 기록 증거가 현 트리에 없다. WP-2 smoke(`docs/internal/evidence/WP4/4C/wp2_two_process_smoke.txt`, 11단계)와 WP-3 smoke(`wp3_three_process_smoke.txt`, 17단계)에 pause/resume 단계가 없다(WP-2 본문 pause|resume 0건, WP-3의 해당 문자열은 registry capability 선언 `full_state_resume: NOT_RUN`뿐). T4는 평가 시점 진행 중: `runtime/test_root/t4t5/t4f/evidence/t4_nvml_steps.jsonl`에 train-paused(PAUSED_AT_EPOCH)·train-finish(resumed_from full_e0001.pt, full_state_resume true, attempt 2, COMPLETED) PASS가 있으나 같은 run의 summary는 FAIL(decision 단계)이고 `docs/internal/evidence/T4`는 없다 → 채점 근거로 쓰지 않고 보강 참고만. 현 트리에서 다중 프로세스 증거(T3-07)는 stub 학습, 실모델 증거(test_explicit_cpu)는 단일 프로세스이고, 실모델+다중 프로세스는 이전 트리의 T7뿐이다.

## 4. policy_compat — 20/25 (anchor 20: 라벨 누락)

**근거**
- hook shim: `mbnas_model_runtime/src/mbnas_model_runtime/adapters/base.py:30-33` REGISTER_HOOKS 정확히 2개(현 문자열, 구 `…etri_bridge:ensure_mixed_registered`→같은 대상), :194-204 `resolve_register_hook`(그 외 `REGISTER_HOOK_REFUSED`). core 생산자는 신 문자열만(`runner_api/envelope.py:65`, `training_runs/selection.py:187`). 문서 `docs/public/COMPATIBILITY.md:33-42`.
- mbnas-mixed-space/3 매핑: `docs/internal/provenance/MIXED_SPACE_HASH_MAP.json`(v3 space_hash·prior_hash, mixed_pool 3파일 sha, parse_model 복제본 sha, v2 vendor_origin 파일 sha, `comparable: false`). 문서 COMPATIBILITY.md:44-48.
- 과거 .pt 미지원 명시: COMPATIBILITY.md:53(`yolo_ga_runtime.mixed_blocks` pickle .pt 읽을 수 없음).
- DD-A53(c)(DECISIONS.md:65) 해시 정의 변경·1.4.x DB 비호환: `mbnas_contracts/src/mbnas_contracts/protocol_hash.py:27`(RESOLUTION_HASH_EXCLUDED에 `resolution.paths`), COMPATIBILITY.md:49, `docs/internal/evidence/WP4/4B/preset_hash_record.json`.

**재실행**
- pytest (tree 634a083b): `tests/public/model_runtime/test_adapters_cpu.py` 7(:68-83 hook 허용목록: None, `os:system` 거부, 현·구 문자열→`operators.ensure_registered`, 근접 문자열 2개 거부), `tests/owner/search/test_space_hash_content.py` 1(위치 독립·내용 변경 시 hash 변경, /3), `test_space_genotype.py` 11, `test_mixed_pool_bridge.py` 6, `test_operators_parity.py` 11, `tests/owner/t2/test_t2_comparator.py` 22(/2→/3 허용, protocol_hash 정의 변경 규칙과 그 외 거부), `tests/public/infra/test_import_closure.py` 10, `tests/owner/api/test_dd_a46_core_gateway_inputs.py` 2(`resolution.paths` ∈ hash_excluded, realpath 미기록) → **70 passed, 1 deselected(host_data: test_etri_extraction_is_verbatim), exit 0, 42.7 s**.
- hash map 재계산: `SearchSpace.create(nc=1, imgsz=640).describe()`를 새 프로세스 2회 → `mbnas-mixed-space/3`, `etri_mixed`, space_hash `sha256:f50d4904…a8b3`, prior_hash `sha256:0fbc5413…621f` — **기록값과 동일, 결정적**. mixed-pool 3파일 + `parse_model_8_4_173.py`의 sha256sum = 기록값.
- `docs/internal/evidence/WP4/4B/preset_hash_record.py` 재실행(TMPDIR=스크래치) **exit 0**: 요청 preset hash 불변 2/2, 검증 hash 1.4.3 대비 변경 2/2, fixed point·realpath 독립 true. 숫자 hash는 `console.paths`의 temp 경로 때문에 기록과 다름(예상된 차이).
- `scripts/dev/check_import_closure.py` **exit 0**: ok, violations 0, unresolved 0, forbidden_strings 0, excepted 1(base.py:32 구 hook 문자열).

**공백 (D3, 감점 사유)**: 그 예외가 `"expires": "WP-4"`(`scripts/dev/check_import_closure.py:77-83`, DD-A26 "만료 WP-4", DECISIONS.md:36)로 남아 있다. WP-4는 수용 완료(DD-A53)됐고, 이 리터럴은 DD-A12(DECISIONS.md:19)·COMPATIBILITY.md §4상 계속 있어야 하는 호환 요소다. 연장·영구화 결정 기록이 없어서, 만료를 근거로 매핑을 지우면 구 run 호환이 깨질 위험이 있다. 참고(감점 안 함): `/2` 전체 hash 값은 원본 문서에 접두사만 있어 맵에 "확인 못 함"으로 기록됨 — 정직한 표기라 결함 아님.

---

## 결함

| # | 심각도 | 기준 | 소유 패키지/파일 | 요약 | 구체적 수정 |
|---|---|---|---|---|---|
| D1 | Minor | public_id | `mbnas_runner/src/mbnas_runner/config.py:242`, `scripts/install.sh:457-466`, `mbnas_core/.../console/projection.py:268-283`, `tests/owner/projection/test_value_rules.py:23,95`, `projection_world.py:941`, 문서 COMPATIBILITY.md:25·API.md:98·SECURITY_MODEL.md:71 | 기본 runner id가 `runner-<hostname>`이고 투영이 그대로 내보냄 → 공개 Worker.id/runner_id·SSE에 호스트명(T4 캡처 `runner-t4-sandbox`). 시험이 못 잡고 문서는 "opaque"라고 과장 | 설치 시 `runner-<secrets.token_hex(6)>`을 생성해 runner.env에 기록(또는 `mbnas-core-principal create --kind runner`가 `--runner-id` 생략 시 core가 발급); 기본값에서 hostname 제거. projection world에 기본 id runner를 추가하고 worker id 검사를 `wrk-<hex>`·발급 형식으로 좁히며 `-<host>` 부분 문자열도 검사. 문서는 "runner 행 = 운영자가 발급한 runner id"로 정정 |
| D2 | Minor | full_state | WP-8 P2(T4), `scripts/dev/wp2_two_process_smoke.py` | 현 트리에 실모델 다중 프로세스 pause→full-state resume 기록 증거 없음(WP-2/3 smoke에 pause 단계 없음, T4 미완·미기록) | T4 `--pause` 결과(train-paused, train-finish `full_state_resume=true`, attempt 2)를 `docs/internal/evidence/T4/`에 기록하거나, wp2 smoke에 실 CPU 학습 pause→resume 단계를 추가 |
| D3 | Minor | policy_compat | `scripts/dev/check_import_closure.py:77-83`, DECISIONS | 구 hook 리터럴 예외가 "expires WP-4"로 만료 경과, 영구화 결정 없음 | DD 추가("DD-A26(2) 영구 — DD-A12 legacy hook shim"), `expires`를 `permanent (DD-A12)`로 변경, 관련 시험 기대 출력 갱신 |
| D4 | Minor | full_state/policy_compat(문서) | `mbnas_model_runtime/.../checkpoint.py:16`, `genotype.py:17`, `mbnas_core/.../orchestration/retention.py:17`, `mbnas_contracts/.../protocol_hash.py:18` | docstring에 옛 경로(`mbnas_server.adapters.*`, `python -m mbnas_server.orchestration.retention`), protocol_hash "code unchanged"(실제로는 `resolution.paths` 추가로 바뀜) | 새 모듈 경로로 바꾸고 protocol_hash 주석에 "1.5.0에서 resolution.paths 추가(DD-A53 c)" 명시 |
| D5 | Minor | policy_compat(문서) | `docs/public/COMPATIBILITY.md:12`, `SECURITY_MODEL.md:102` | GPU 검증 상태가 "예정"으로 남아 DD-A54(GPU_PATH_VALIDATED=YES, 9dafe4d0, 한계 포함)와 불일치 | 검증 트리·범위·한계(TensorRT BLOCKED_BY_ENV, BENCHMARK는 owner 스크립트)를 명시(7C 문서 작업 범위) |

프로젝트 외 표기: C06.json mandatory_checks 경로 2건(`tests/owner/search/test_generation_multi_seed`→`tests/owner/api/…`, `tests/public/runner test_checkpoint`→`tests/public/model_runtime/test_checkpoint.py`). 기준 파일 소유자(설계자)가 정정할 항목이며 감점 없음.

## 합계와 판정

| 기준 | 점수 | anchor |
|---|---|---|
| search_train_rng | 25 | 25 |
| public_id | 20 | 20 (D1) |
| full_state | 20 | 20 (D2) |
| policy_compat | 20 | 20 (D3) |
| **합계** | **85 / 100** | |

- 통과 조건 total > 90 → **미달**. Blocker 0, Critical 0(충족).
- **판정: 미통과**. D1·D2·D3 중 두 건을 해소하고 해당 시험을 재실행하면 95(통과)가 된다. 한 건만 해소하면 90이라 여전히 미달이다.
- 참고(내 실행 아님, WP-4C 기록): `docs/internal/evidence/WP4/4C/owner_gate_run4.*` 1510 passed, 8 skipped, 47 deselected, exit 0 (18:12–18:28Z); `pytest_public_run4.*` 1070 passed, 1 skipped, 1 xfailed, exit 0.

## 확인 못 함

1. GPU 필요 시험 `tests/public/model_runtime/test_checkpoint.py::test_rng_roundtrip_includes_cuda` — **BLOCKED_BY_ENV**(이번 평가는 `CUDA_VISIBLE_DEVICES=""` 규칙). torch_cuda RNG 동등은 T7 기록(tree 164292c6)으로만 확인.
2. 현 트리(634a083b/76ff726a)에서 실모델 다중 프로세스 pause→full-state resume — T4 진행 중이고 기록되지 않음.
3. T7 bitwise 재개 결과의 현 트리 재실행 — GPU 금지로 하지 않음. 재개 경로 파일이 바이트 동일함을 대신 확인함.
4. RF-DETR full-state 재개 — 공개 runner에서 설계상 비활성이라 미실행.
5. `mbnas-mixed-space/2` 전체 hash 값 — 원본 문서에 접두사만 있음(맵 기록 그대로).
6. `wrk-`/`dev-` id의 core 재시작 간 안정성 — 종단 시험 없음. salt 영속 시험(17 passed)과 HMAC 결정성으로 구성상 성립.
7. 구 `yolo_ga_runtime.mixed_blocks`로 pickle된 .pt를 넣었을 때의 실제 실패 형태(명시 오류 코드인지) — 문서 선언만 있고 시험 없음.
8. 1.4.x DB를 새 core에 연결했을 때의 실제 실패 형태 — 문서 선언만 있고 실행하지 않음.
9. 트리 이동: RNG·full_state·policy_compat 묶음은 634a083b에서 실행. 76ff726a(DD-A55 a) 이후에는 projection+checkpoint 묶음(264)만 재실행. 나머지 묶음의 코드 경로는 이번 diff(retention.py·routes_runs.py의 checkpoint `artifact_id`)와 무관함을 diff로 확인했지만 재실행하지는 않음.

## 실행 명령 (모두 TARGET_ROOT에서)

```
CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 runtime/venv/bin/python -B -m pytest -p no:cacheprovider --basetemp <scratch> -q \
  -m "not gpu and not live and not legacy_records and not host_data" <subset>       # 1~4절의 각 묶음
runtime/venv/bin/python -B scripts/owner/t2_compare.py --scenario {T2-9,T2-2,T2-3,T2-10} --out-dir <scratch> --work-root <scratch> --no-report
runtime/venv/bin/python -B -c "from mbnas_core.search.space import SearchSpace; ...describe()"   # hash map 재계산(2회, MBNAS_HOME=<scratch>)
TMPDIR=<scratch> runtime/venv/bin/python -B docs/internal/evidence/WP4/4B/preset_hash_record.py <scratch>/preset_hash_rerun.json
runtime/venv/bin/python -B scripts/dev/check_import_closure.py
runtime/venv/bin/python -B scripts/dev/source_tree_hash.py [--digest-only]
sha256sum / cmp / diff (스냅샷·g2_tree 대조), grep (문서·계약·캡처)
```

# C06 복구·재현 — 재채점 (EVAL_2)

| 항목 | 값 |
|---|---|
| 기준셋 | C06 `docs/internal/criteria/C06.json` **v1.0.1** (경로 표기 2건만 정정, 기준·배점·anchor·통과 조건 불변 — 확인함) |
| 평가자 | claude-opus-5-5 (독립 평가자: 채점만, 판정은 설계자). 1회차: `EVAL_1.md` (85/100, 미통과) |
| 시각 | 2026-10-28 07:43–08:01 KST |
| tree hash (`runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`, mbnas-source-tree/2) | 시작·종료 모두 `sha256:5a7bc5920ac3c1fe630a45c8ea0dd4661cb754661a90da5c09441fb7237c4223` (221 files) — 평가 중 트리 변동 없음 |
| 최종 ZIP(내가 sha256sum) | public `ce34b821806a27cd…`, owner `79cce02a8d36eec1…` (dist/) |
| 실행 환경 | EVAL_1과 동일: `runtime/venv/bin/python -B -m pytest -p no:cacheprovider`, `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `--basetemp`=스크래치, 마커 `not gpu and not live and not legacy_records and not host_data`. GPU·/data·etri_nas·build/source_snapshot 미기록, 새 `__pycache__` 0. 다른 에이전트 부하(load avg 약 67/48코어)로 wall 시간이 늘어남. |
| 원 출력(세션 임시) | `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c06e2/` (pytest_rng.txt, pytest_projection.txt, pytest_fullstate.txt, pytest_compat.txt, t2_T2-{9,2,3,10}/, rid_probe.json, import_closure.txt, preset_hash_rerun.json, tree_hash_start.json) |

결론: **100/100 — 통과**(total > 90, Blocker 0, Critical 0). EVAL_1 감점 3건(D1·D2·D3)이 모두 해소됐고 D4·D5·D6도 해소됐다. 남은 것은 감점 없는 Minor 2건(R1 docstring, R2 문서 한 문장)이다.

---

## A. EVAL_1 결함 처리 확인

| # | 상태 | 근거 (file:line / 시험 / 명령) |
|---|---|---|
| D1 공개 runner id | **해소** | 규칙(DD-A61 b, DECISIONS.md:76): `mbnas_runner/src/mbnas_runner/config.py:32-38`(설명), :64-111(`RUNNER_ID_FILE`, `GENERATED_RUNNER_ID`, `new_runner_id`=`runner-`+`secrets.token_hex(4)`, 하드링크 원자 생성, 형식 불일치·읽기 불가 시 ValueError·자동 교체 없음), :304-305(미설정 시 기본값). config/install/start에 `gethostname` 0건(daemon.py:226의 `host=`는 `runners.host` — 공개 `Worker`는 닫힌 스키마이고 host 없음, 확인). `scripts/install.sh:62-63, 417-426, 472-483`(같은 규칙으로 principal 발급). 시험: `tests/owner/projection/test_value_rules.py:96-103`(worker id는 `wrk-<hex>`·`runner-<hex>`만, runner_id는 `runner-<hex>`만), `projection_world.py:18, 911-921, 975-978`(실제 `RunnerSettings` 기본 id로 runner 등록), `tests/owner/unit/test_settings_split.py:49-56`. 수정 전후: `docs/internal/evidence/WP7/FIX1/c06d1_repro_before_fix.txt`(강화된 시험이 `runner-dev1`로 실패) → `c06d1_after_fix.txt`(12 passed, exit 0). 설치: `FIX1/install_runner_id_check.txt` ALL_PASS(`runner-88fb1032`), `WP7/7V_FINAL/40_install_owner.txt`(`runner-c867e7fc` 발급), 최종 owner ZIP 격리 설치 `docs/internal/evidence/T4/RERUN_README.md` Inputs 표(`runner-d3c27c94`, runner.env에 `MBNAS_RUNNER_RUNNER_ID` 줄 없음). 문서: `docs/public/API.md:98`, `COMPATIBILITY.md:25`, `SECURITY_MODEL.md:71`, `INSTALL.md:154`, `TROUBLESHOOTING.md:90`(구 `runner-<hostname>` 토큰 → `RUNNER_MISMATCH`). **내 재실행**: 아래 2절 297 passed; 독립 탐침(`rid_probe.json`) — 기본 id `runner-79ee89f7`이 두 프로세스에서 같고 파일 0644에 저장·호스트명 없음, 파일이 `runner-myhost`면 exit 1로 거부하고 파일을 바꾸지 않음, `MBNAS_RUNNER_RUNNER_ID` 지정 시 그 값·파일 미생성, 동시 첫 기동 8개가 한 id(`runner-07aa8c5a`)에 합의·tmp 잔재 0. 최종 public ZIP의 runner wheel `config.py`가 트리와 바이트 동일(gethostname 없음). |
| D2 2프로세스 재개 기록 | **해소** | `docs/internal/evidence/T4/README.md` 결과 표 "pause during epoch 1 → PAUSED(PAUSED_AT_EPOCH, full state e1) → resume → COMPLETED, job attempt 2 resumed_from=full_e0001.pt, epochs_done 2, device_used=cpu" = **PASS**(변형 B; 명령 표 #2 install exit 0, #3b start(core·owner worker·gateway·runner) exit 0/0). `T4/run/t4_nvml_steps.jsonl` train-pause-request(`t4_nvml-a6e725b7…`)·train-paused·train-resume-request(`t4_nvml-37f0fe37…`)·train-finish 모두 PASS. 격리: README Isolation 절 + `T4/isolation_proof.txt`(bwrap, 네트워크 lo만, 원본·/data 비가시); 학습은 실제 ultralytics CPU 학습(device_used=cpu). T4 전체는 PARTIAL이었으나(F1/F3/F4, DD-A60→A62에서 수정) 재개 항목은 PASS. **코드 동일성**: T4가 설치한 owner ZIP(`ba8ed91f…`)의 venv 설치본과 현 트리를 `cmp` — ultralytics_adapter.py, adapters/base.py, stage_trainer.py, runner training/handlers.py, daemon.py, child.py, orchestration/state.py, runner_api/envelope.py 동일; checkpoint.py는 docstring 2줄(D4)만 다름; engine.py·runner_api/routes.py의 차이에는 pause/resume/checkpoint/control 줄이 없음(NOT_EVALUATED 커밋·claim 규칙 변경). **최종 ZIP 재실행(07:57 게시, 주 근거)**: `docs/internal/evidence/T4/RERUN_README.md` — owner ZIP `79cce02a…`(`rerun/zips_sha256_used.txt`, = dist/owner), GPU 장치 노드 없는 엄격 샌드박스, Overall **PASS**; `rerun/t4f_local_console.txt` doctor·start·harness·stop 모두 exit 0; `rerun/run/t4f_local_steps.jsonl` train-pause-request(`t4f_local-75d35aae…`) → train-paused(PAUSED_AT_EPOCH) → train-resume-request(`t4f_local-6ee91f4d…`) → train-finish COMPLETED, resumed_from full_e0001.pt, full_state_resume true, attempt 2, lease owner `runner-d3c27c94`, device_used cpu; summary 11/11 PASS. 앞에 적은 1차 실행(이전 ZIP `ba8ed91f…`)은 이력·보강으로 둠. |
| D3 hook 예외 라벨 | **해소** | `scripts/dev/check_import_closure.py:33-34, 77-84` `"expires": "1.6"`, basis "DD-A26 (2), extended to 1.6 by DD-A58 (public ZIP compatibility shim, DD-A12)"; DD-A58(DECISIONS.md:71). 같은 만료를 `tests/public/infra/test_no_host_facts.py:22-24, 73`, `test_import_closure.py:26-29`가 단언. 내 실행: closure exit 0, violations 0, excepted 1(base.py:32, expires 1.6). |
| D4 docstring | **해소(지적 4곳)** | `mbnas_model_runtime/.../checkpoint.py:16`(`mbnas_model_runtime.adapters.base.ResumeBlocked`), `genotype.py:17`, `mbnas_core/.../orchestration/retention.py:16-17`(`python -m mbnas_core.orchestration.retention`), `mbnas_contracts/.../protocol_hash.py:14-20`(1.5.0에서 `resolution.paths` 추가로 hash 정의가 바뀌었음을 명시). 다른 곳의 잔여는 R1. |
| D5 GPU 상태 문서 | **해소** | `COMPATIBILITY.md:12`, `SECURITY_MODEL.md:102`: GPU 검증 완료(2026-10-27)와 한계(TensorRT 미검증, 벤치마크는 owner 스크립트, 0번 외 GPU는 학습·평가만, "검증 뒤 소폭 변경은 CPU 시험으로만 확인" — DD-A56 c와 일치). |
| D6 기준 파일 경로 | **해소** | C06.json `version` 1.0.1, mandatory_checks 경로 `tests/owner/api/test_generation_multi_seed`, `tests/public/model_runtime/test_checkpoint`; scoring 블록 4개 동일·pass_condition 불변(확인). |

## 1. search_train_rng — 25/25 (anchor 25)

- **재실행(tree 5a7bc592)**: test_generation_multi_seed 7, test_proposers 12, test_search_fairness_engine 10, test_generation_recovery_independent 7, test_generation_only 19, test_t2_equivalence::test_t2_fast 3 → **58 passed, exit 0**, 93 s.
- `scripts/owner/t2_compare.py --scenario … --out-dir <scratch> --no-report`: **T2-9 EXACT_MATCH(0/0)**, T2-2 ALLOWED_DIFF 78/0, T2-3 136/0, T2-10 261/0 — 모두 exit 0, snapshot unchanged, 허용 규칙은 문서화된 것뿐(EVAL_1과 같은 수치).
- 정적 확인: `search/streams.py` sha256 `ea4f6241…` = 스냅샷; `generation.py`는 import 외 차이 0; engine.py의 seed 관련 줄 전부 스냅샷과 동일(`derive_seed`는 :73-75로 이동만); `final_training_seed`·`FINAL_SEED_ID`(final_retrain.py:39) 동일; handlers.py seed 관련 차이 0줄. `docs/public/SECURITY_MODEL.md:92` "seed는 비밀이 아니다". 공개 계약 `training_seed_policy` 닫힘 {base, base_source, final_id, id, requested}; 공개 코드·문서·웹에 유도식 태그 grep 0건.
- 공백: 없음.

## 2. public_id — 25/25 (anchor 25)

- **재실행**: `tests/owner/projection` + `tests/owner/unit/test_db_migration.py` + `test_db.py` + `test_settings_split.py` + `tests/owner/runner_protocol/test_t3_auth.py` → **297 passed, exit 0**, 33 s (canary 47, revisions 9, contract_150 157, owner_routes 14, golden 16, T8 4, value_rules 12, db_migration 2, db 15, settings_split 15, t3_auth 6).
- 계약 1.5.0 sha256 `70e0cffa…` 불변: `Generation` 닫힘·`group_key` 없음, `Worker` 닫힘·`host`/`hostname` 없음·`device_uuid` deprecated. 투영 `mbnas_core/.../console/projection.py:66-79`(HMAC opaque id), :86, :268-283; salt 설치 `mbnas_core/.../api/app.py:195-199`.
- D1 해소(A절). 공백: 없음. 참고(감점 아님): 계약의 `Worker.id` 설명 "core-issued at registration"은 DD-A61 b에 따라 "runner 호스트에서 만든 값을 core가 발급한 runner principal에 묶음"으로 읽어야 하며, 산문 문서(API.md:98)는 이를 정확히 쓴다.

## 3. full_state — 25/25 (anchor 25)

- **재실행**: test_checkpoint 18, test_stage_trainer 19, test_resume_equivalence_script 1, test_checkpoint_state_row 2, test_t3_control 6, test_t3_restart 3, test_explicit_cpu 1(실제 CPU 학습 pause→full_e0001에 model/optimizer/scheduler/scaler/EMA/RNG/loader 상태→resume), test_subprocess 2 → **52 passed, 1 deselected(gpu: test_rng_roundtrip_includes_cuda), exit 0**, 245 s(부하).
- 필수 점검 "T4 재개": D2 해소(A절) — 최종 ZIP 기록 증거 `docs/internal/evidence/T4/RERUN_README.md`·`rerun/run/t4f_local_steps.jsonl`(core·owner worker·gateway·runner 별도 프로세스, 실제 CPU 학습, pause→full_e0001 재개→COMPLETED, 모든 단계 exit 0), 1차 실행 `T4/README.md`도 같은 결과.
- GPU bitwise 증거 `docs/internal/evidence/T7/resume_equivalence.json`(tree 164292c6)은 재개 경로 파일이 그대로라 계속 유효(EVAL_1에서 cmp, 이번에 T4 venv 대조로 재확인).
- 공백: 없음.

## 4. policy_compat — 25/25 (anchor 25)

- **재실행**: test_adapters_cpu 7(hook 허용목록), test_space_hash_content 1, test_space_genotype 11, test_mixed_pool_bridge 6, test_operators_parity 11, test_t2_comparator 22, test_import_closure 10, test_no_host_facts 8, test_dd_a46 2 → **78 passed, 1 deselected(host_data: test_etri_extraction_is_verbatim), exit 0**, 470 s(부하).
- `MIXED_SPACE_HASH_MAP.json`: mixed-pool 3파일 + parse_model 복제본 sha256 = 기록값; `SearchSpace.create(nc=1, imgsz=640).describe()` 새 프로세스 2회 → `mbnas-mixed-space/3`, space_hash `sha256:f50d4904…a8b3`, prior_hash `sha256:0fbc5413…621f` = 기록값.
- `docs/internal/evidence/WP4/4B/preset_hash_record.py` exit 0: 요청 preset hash 불변 2/2, 검증 hash 1.4.3 대비 변경 2/2, fixed point·realpath 독립 true(DD-A53 c).
- hook shim `mbnas_model_runtime/.../adapters/base.py:30-33, 194`; 문서 `COMPATIBILITY.md` §4(:33-42), §5(:44-49, 1.4.x DB 비호환 :49), §6(:51-56, 과거 .pt 미지원 :53). 최종 public ZIP의 model_runtime wheel `adapters/base.py`·`checkpoint.py`와 `docs/public/COMPATIBILITY.md`가 트리와 바이트 동일.
- D3·D5 해소(A절). 공백: 감점 없는 R2(아래).

---

## 잔여 결함 (감점 없음)

| # | 심각도 | 소유 | 요약 | 수정 제안 |
|---|---|---|---|---|
| R1 | Minor | mbnas_core docstring | 옛 단일 패키지 경로가 남음: `mbnas_core/src/mbnas_core/search/space.py:8-9`(`mbnas_server.search.etri_bridge…` → `mbnas_core.search.mixed_pool.bridge`), `search/__init__.py:3`, `protocol/validator.py:38`, `search/handlers.py:15`(`mbnas_server.search.ga` → `mbnas_core.search.ga`). `mbnas_server.api.*` 언급은 현 gateway 패키지라 맞고, "Source: …(monolithic package…)" 출처 줄은 의도된 것 | 네 곳의 참조 경로를 새 모듈로 바꾸기 |
| R2 | Minor | docs/public | `COMPATIBILITY.md` §4(:33-42)에 구 hook 문자열 대응이 계약 1.6에서 재검토된다는 사실(DD-A58)이 없음 | §4에 한 문장 추가("구 문자열 대응은 계약 1.6에서 재검토") |

C06 범위 밖 관찰(채점 안 함, C08/7C): `SECURITY_MODEL.md:71`이 아직 "누출 검사(T8)는 검증 예정"이라 쓰는데 같은 문서 :106은 T8 ZIP PASS와 런타임 1차 결과를 기록함.

## 합계와 판정

| 기준 | EVAL_1 | EVAL_2 | anchor |
|---|---|---|---|
| search_train_rng | 25 | 25 | 25 |
| public_id | 20 | **25** | 25 (D1 해소) |
| full_state | 20 | **25** | 25 (D2 해소) |
| policy_compat | 20 | **25** | 25 (D3 해소) |
| **합계** | 85 | **100 / 100** | |

- 통과 조건: total > 90 **충족**, Blocker 0·Critical 0 **충족** → **판정: 통과**.
- 참고(내 실행 아님, 트리 해시 미기록): `docs/internal/evidence/WP7/7V_FINAL/` owner gate 1569 passed·8 skipped·exit 0, public gate 1124 passed·1 skipped·exit 0, T2 32 passed. 채점에는 내 재실행만 사용함.

## 확인 못 함

1. GPU 시험 `test_rng_roundtrip_includes_cuda` — **BLOCKED_BY_ENV**(CPU 전용 규칙). torch_cuda RNG 동등은 T7 기록(tree 164292c6)뿐.
2. T7 GPU bitwise 재개의 현 트리 재실행 — GPU 금지로 하지 않음(DD-A56 c: 9dafe4d0 이후 변경은 CPU 검증만 수용). 재개 경로 파일 동일로 대체 확인.
3. T4 자체를 내가 다시 돌리지는 않음(다른 에이전트의 bwrap 샌드박스 실행). 07:57에 게시된 `T4/RERUN_README.md`와 `rerun/` 산출물(명령·exit code·step 기록)을 열람해 확인함.
4. RF-DETR full-state 재개 — 공개 runner에서 설계상 비활성.
5. `mbnas-mixed-space/2` 전체 hash 값 — 원본에 접두사만.
6. 구 `yolo_ga_runtime` pickle .pt, 1.4.x DB를 실제로 넣었을 때의 실패 형태 — 문서 선언만, 미실행.
7. `wrk-`/`dev-` id의 core 재시작 간 안정성 — 종단 시험 없음(salt 영속 시험 + HMAC 결정성으로 구성상 성립).

## 실행 명령 (모두 TARGET_ROOT에서)

```
CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 runtime/venv/bin/python -B -m pytest -p no:cacheprovider --basetemp <scratch> -q \
  -m "not gpu and not live and not legacy_records and not host_data" <subset>      # 1~4절의 각 묶음
runtime/venv/bin/python -B scripts/owner/t2_compare.py --scenario {T2-9,T2-2,T2-3,T2-10} --out-dir <scratch> --work-root <scratch> --no-report
runtime/venv/bin/python -B -c "from mbnas_core.search.space import SearchSpace; ...describe()"   # 2회, MBNAS_HOME=<scratch>
runtime/venv/bin/python -B <scratch>/rid_probe.py <scratch>/rid <python>                         # runner id 독립 탐침(MBNAS_HOME=<scratch>/h*)
TMPDIR=<scratch> runtime/venv/bin/python -B docs/internal/evidence/WP4/4B/preset_hash_record.py <scratch>/preset_hash_rerun.json
runtime/venv/bin/python -B scripts/dev/check_import_closure.py
runtime/venv/bin/python -B scripts/dev/source_tree_hash.py [--digest-only]
sha256sum / cmp / diff / python zipfile(읽기 전용: dist ZIP 내부 wheel 대조), grep
```

# C08 원격 산출물 — 독립 재평가 EVAL_2

- set: **C08** (`docs/internal/criteria/C08.json` v1.0). EVAL_1과 같은 anchor(25/20/15/0)를 문자 그대로 쓴다.
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자. 채점만 하고 결정은 설계자가 한다.
- time: 2026-10-28T11:23–11:35+09:00 (2026-10-28T02:23–02:35Z)
- tree hash: `sha256:f00987be7994203fc16f52140bd98b3f89e1b548b6a346255066727a36dcc40c` (mbnas-source-tree/2, 221 files).
  EVAL_1과 같은 도구 `runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`를 썼고 exit 0이다. 시작(11:23:18)과 끝(11:34:16)에 계산한 값이 같다.
  출력 전체는 `scratchpad/wp3/c08/treehash_eval2_{start,end}.json`에 있다.
- 배포 ZIP (평가 시점의 dist/):

  | 프로필 | sha256 | 크기 | BUILD_INFO 트리 | 패키지 소스와 현재 트리 비교 |
  |---|---|---|---|---|
  | public | `bc0500bd…` | 2,716,913 B | `9859adb2…` | 129/129 바이트 동일 |
  | registration | `dea510df…` | 2,792,584 B | `9859adb2…` | 129/129 바이트 동일 |
  | owner | `867cc577…` | 80,883,095 B | `f00987be…` | 221/221 바이트 동일 |

  owner ZIP은 11:27:33 KST에 다시 빌드되었다(DD-A72 테스트 수정). PFINAL2/SUMMARY.md에 적힌 `a43a8a2e…`는 그 이전 빌드이다.
  즉 아래에서 실행한 코드가 배포되는 코드와 같다.
- T4/T5 FINAL 증거와의 차이: 그 증거는 owner ZIP `79cce02a…`(트리 `5a7bc592…`)로 만들었다. 그 ZIP과 현재 트리를 비교하면 바뀐 파일은 17개이다.
  - `mbnas_server` 0개
  - core `artifacts/`(locations·registry) 0개, `console/routes_files.py` 0개
  - runner `transfer.py` 0개
  - runner `reports.py`는 docstring과 주석만 바뀌었다.
  - 나머지는 prune 왕복(DD-A66/A68)과 다른 기능 파일이다.
  
  **다운로드 경로 코드는 바뀌지 않았다.**
- 환경: CPU 전용이다. `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`를 썼다.
  - pytest basetemp, smoke 루트, probe는 모두 `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c08/` 아래에 두었다.
  - smoke는 `--device-source runners`로 돌려 NVML도 읽지 않았다.
  - GPU, /data, etri_nas, snapshot은 건드리지 않았다. 내가 직접 쓴 프로젝트 파일은 이 파일 하나다.
  - 예외가 하나 있다. 공용 test root의 ultralytics 설정 파일 `runtime/test_root/runs/ultralytics_cfg/tests/Ultralytics/settings.json`이 11:26:53 KST에 갱신되었다.
    E3 실행 중이었으므로 테스트 하네스가 기본 동작으로 고쳐 쓴 것으로 보인다(EVAL_1과 같은 현상).

## 결론

총점은 **95/100**이다(host_path 25, bytes_hash 25, real_download 25, resume_retention 20). Blocker 0, Critical 0이므로 **PASS**이다(기준: 총점 > 90, Blocker·Critical 0).

- EVAL_1 결함 D1–D5는 모두 **FIXED**이다. 실행과 파일로 확인했다(아래 표).
- 필수 점검 T3-34(prune 왕복)는 이제 구현되어 있다.
  - 테스트 6건이 통과했다.
  - 실프로세스 smoke를 현재 트리에서 다시 돌려 PASS였다.
- real_download의 25는 **잠정**이다. 최종 ZIP으로 돌린 T4/T5 rerun2가 작성 시점에 `docs/internal/evidence/T{4,5}/rerun2`로 공개되지 않았다.
  그래서 다음 근거로 채점했다.
  - 이전 T4/T5 FINAL 로그(다운로드 경로 코드 동일)
  - FIX5 smoke
  - 이 평가에서 다시 돌린 smoke
  - 테스트
  
  rerun2가 공개되면 추록으로 확인한다.
- resume_retention의 감점(25→20)은 Minor이고 문서화된 한계이다.
  - 기준 문구 "checkpoint affinity 또는 전송 후 재개"는 checkpoint를 가진 그 runner에서 재개하는 경우로만 충족된다.
  - affinity와 runner 사이 전송은 DD-A38/A68에 따라 범위 밖이다.
  - 조정자 지시대로 Minor를 넘겨 감점하지 않았다.

## EVAL_1 결함 추적

| ID | EVAL_1 내용 | 상태 | 근거 (파일:줄 / 실행) |
|---|---|---|---|
| D1 (Minor) | `model_path` 구조 검사의 `source_sha256`이 개행 변환된 텍스트의 hash였다 | **FIXED** | 수정: `mbnas_core/src/mbnas_core/console/model_services.py:620-640` — `read_bytes()`의 sha256을 쓰고 UTF-8로 decode한다. 테스트: `tests/owner/api/test_structure_report_hashes.py:89`(E3 통과). 재실행: P2 probe에서 `model_path source_sha256 1b33b5c1… == file bytes: True`이다(EVAL_1에서는 `c4189e36…`, False). 수정 전 코드에서 같은 테스트가 실패한다는 음성 대조: `WP7/FIX5_T3_34/negative_control/` |
| D2 (Minor) | StructureReport hash 3종과 `SOURCE_SHA256_MISMATCH`를 고정하는 테스트가 없었다 | **FIXED** | `test_structure_report_hashes.py:52`: LF / CRLF+주석 / 다른 구조로 source는 바뀌고 yaml_hash·architecture_hash는 정해진 대로 같거나 다르며, 세 값은 서로 다르다. `:72`: digest 불일치 3종은 422 `SOURCE_SHA256_MISMATCH`, 형식 오류는 `SOURCE_SHA256_INVALID`이다. E3에서 3/3 통과. P1 probe 결과도 같다 |
| D3 (Minor) | runner 절대경로가 core로 가는 사실을 라벨·DTO 표본·보안 문서가 빠뜨렸다 | **FIXED** | `runner_protocol.py:6-14`가 두 경로(E6 `uri`, E8 `outputs`)와 owner 전용 저장, 비투영을 명시한다. `:388-391` `uri` 필드 설명. `tests/public/contracts/test_runner_protocol.py:96`의 표본이 실제 형식(절대경로, 스킴 없음)이다. `docs/public/SECURITY_MODEL.md:96`이 "runner 호스트 절대경로(core DB에만 저장되는 owner 전용 값, 공개 응답에 투영되지 않음)"를 적는다. 비투영은 E4 projection canary 47/47 통과로 확인했다(`ckpt_path` seed, `projection_world.py:209`) |
| D4 (Minor) | 원격 keep_all 강제에 테스트가 없었다 | **FIXED(대체)** | DD-A66으로 강제 자체가 없어지고 정책이 원격 run에도 적용된다. 이를 고정하는 테스트: `tests/owner/orchestration/test_retention_remote_policy.py:101`(보호 2종), `:145`, `:157`(keep-everything 상태와 취소된 run), `:178`(core는 직접 지우지 않음). E3에서 10/10 통과. `REMOTE_RETENTION_FORCED`가 없음도 단언한다(`test_t3_34_prune_roundtrip.py:146`) |
| D5 (Minor) | FEATURES 보존 정책 문구와 VALIDATION_REPORT T3 행 | **FIXED** | `docs/public/FEATURES.md:91`(원격 적용, 보호 2종, store 사본 유지), `:125`(prune 한계), `OPERATIONS.md:125-130`, `COMPATIBILITY.md:26,57`, `docs/internal/VALIDATION_REPORT.md:14`(T3 행에 T3-34 구현과 테스트) |

## 재실행 요약 (cwd=TARGET_ROOT)

pytest 공통: `runtime/venv/bin/python -B -m pytest <대상> -m "not gpu and not live and not legacy_records and not host_data" -p no:cacheprovider -q -rfEs --timeout=300 --basetemp=<scratchpad>`

| # | 대상 | exit | 결과 | 시간 |
|---|---|---|---|---|
| E1 | `tests/owner/runner_protocol`(17 파일, 82건; `test_t3_34_prune_roundtrip.py` 6건 포함) + `tests/owner/artifacts`(15건) | 0 | **97 passed** | 239 s |
| E2 | `tests/public/contracts/test_runner_protocol.py`(116) · `tests/public/runner/test_prune_control.py`(14) · `test_transfer_local.py`(19) · `tests/public/server/test_artifact_download.py`(33) · `test_artifact_routes.py`(29) · `test_artifact_store.py`(69) | 0 | **280 passed** | 41 s |
| E3 | `test_retention_remote_policy.py`(10) · `test_retention.py`(11) · `test_structure_report_hashes.py`(3) · `test_config_revisions.py`(9) · `test_registry_structure.py`(34) · `test_explicit_cpu.py`(1) · `test_state_machine.py`(9) · `test_dd_a46_core_gateway_inputs.py`(5) · `test_stage_trainer.py`(19) · `test_checkpoint.py`(18) · `test_wp3_gateway_relay.py`(43) · `test_db_migration.py`(2) | 0 | **164 passed**, 1 deselected(gpu 마커 `test_rng_roundtrip_includes_cuda`) | 122 s |
| E4 | `tests/owner/projection/test_canary_projection.py` | 0 | **47 passed** | 18 s |
| S1 | FIX5 smoke(`docs/internal/evidence/WP7/FIX5_T3_34/fix5_prune_roundtrip_smoke.py`, 무수정) `--device-source runners --root <scratch> --evidence <scratch>` | 1 | validate에서 `422 DEVICE_UNAVAILABLE`(`console.benchmark.device`) | 17 s |
| S2 | 같은 smoke를 wrapper `fix5_smoke_cpu_bench.py`로 실행. `console.benchmark.device=cpu`만 덮어씀(T4/T5 harness와 같은 override). `--device-source runners` | 0 | **PASS**, STEP 15/15 | 32 s |
| P1/P2 | EVAL_1 probe 재실행 | 0/0 | 위 D1/D2 근거 | 약 12 s |
| Z | dist ZIP 3종, 이전 owner ZIP과 트리 바이트 비교 | — | 위 헤더 | <10 s |

- S1 실패는 harness 구성 때문이다. smoke 프로토콜이 GPU 벤치 장치를 그대로 두었고, CPU 전용 fleet 검증에서 정직하게 거부되었다(DD-A60/A62 동작). 제품 결함이 아니다.
- CPU 시간 합계는 약 9분이다.
- 로그는 같은 scratchpad 아래에 있다.
  - `e2_owner_rp_art.{txt,meta}`, `e2_public.{txt,meta}`, `e2_hash_resume.{txt,meta}`, `e2_canary_projection.{txt,meta}`
  - `e2_prune_smoke.{txt,console,meta}`(S1), `e2_prune_smoke2.{txt,console,meta}`(S2), `fix5_smoke_cpu_bench.py`
  - `e2_probe_crlf_source_sha.txt`, `e2_probe_structure_hashes.txt`

---

## 기준별 채점

### 1. host_path — **25 / 25** (anchor 25)

- **DTO (필수 점검).** E2에서 `test_runner_protocol.py` 116건이 통과했다.
  - 1.1.0 모델 4종을 포함해 모든 모델이 `extra="forbid"`이다.
  - 미지 필드는 최상위와 중첩 모두 거부된다.
  - runner→core 모델에 path·file·dir 이름의 필드가 없다(`:217`).
  - relpath 6종 거부(`:229`).
- **prune 메시지에도 고객 절대경로가 없다.**
  - `PruneOrder`(`runner_protocol.py:321-339`)와 `PruneReport`(`:489-509`)는 run 상대 `relpath`(`_relpath` 검증), `sha256`, `bytes`만 싣는다.
  - runner는 그 relpath를 자기 `<work_root>/runs/<run_id>` 안에서만 해석한다(`mbnas_runner/src/mbnas_runner/daemon.py:139-147`). 밖이면 `PRUNE_OUTSIDE_WORK_ROOT`이다.
  - 공개 상세의 prune 기록은 `deleted[]`에 bytes만 있고 runner id가 없다(`test_t3_34_prune_roundtrip.py:158-159`, E1 통과).
- **artifact 참조와 gateway→core.** EVAL_1에서 본 근거가 그대로이고, 다시 실행해 통과했다.
  - id/digest/runner_id 기록과 경로 없는 gateway record(`locations.py:20-21`)
  - `test_wp3_gateway_relay.py` 43건: gateway-local 경로 비전달
  - `test_split_output_root.py` 2건(E1)
- **D3 FIXED.** 라벨(`runner_protocol.py:6-14`, `:388-391`), 보안 문서(`SECURITY_MODEL.md:96`), DTO 표본(`:96`)이 이제 실제 동작과 같다.
  - 공개 응답에 나오지 않음은 E4 canary 47건으로 다시 확인했다.
- **관찰 (결함 아님).** E6 `uri`와 E8 `outputs`의 runner 절대경로는 여전히 core DB에 저장된다(설계 REMOTE_PROTOCOL_DESIGN §9.2). 공개 문서에 밝혀져 있고, 공개 투영에서는 테스트로 제거됨을 확인했다.

### 2. bytes_hash — **25 / 25** (anchor 25)

- **등록 시 runner 재계산:** `test_t3_files.py` 5/5(E1).
- **store 수신 재검증:** `test_artifact_store.py` 69와 `test_transfer_local.py` 19(E2).
- **다운로드 digest 대조:** download 33, routes 29(E2), `test_artifact_wiring.py` 10(E1).
  - S2 실프로세스: REAL 체크포인트 31,600,545 B. `sha256(body) == X-Artifact-Sha256 == 등록 digest`(`f4cf6c2c…`), `pre-send`이다.
- **prune도 digest를 대조한다.**
  - runner는 같은 inode의 sha256과 크기가 지시와 같을 때만 지운다. 다르면 `already_absent`로 보고하고 파일을 남긴다(`daemon.py:171-194`; `test_prune_control.py:68`).
  - core는 다른 바이트를 말하는 보고를 409 `PRUNE_REPORT_CONFLICT`로 거부한다(`retention.py:410-413`; `test_t3_34_prune_roundtrip.py:162`).
  - 저장소 전송이 끝나지 않은 파일은 지우지 않는다(`PRUNE_TRANSFER_PENDING`, `test_prune_control.py:215`).
- **원문/정규/구조 hash 구분 (필수 점검: StructureReport 3필드):** D1과 D2 모두 FIXED(위 표). 계약 설명은 EVAL_1과 같다.
- **관찰 (감점 아님, 변동 없음):** `source_sha256` 표기가 둘이다. StructureReport는 접두사 없는 hex이고(계약대로, 테스트 `:60`이 고정), Upload와 ModelConfigRevision은 `sha256:` 접두사를 붙인다.

### 3. real_download — **25 / 25** (anchor 25, **잠정 — T4/T5 rerun2 추록 대기**)

- **필수 점검 "T4/T5 다운로드 로그":** EVAL_1의 V1이 대조한 T4/T5 FINAL 로그가 그대로 있다.
  - 다운로드 14/14가 `body == X-Artifact-Sha256 == 목록`이었다.
  - export 2/2가 `X-Bundle-Sha256`과 SHA256SUMS로 검증되었다.
  - 양쪽 access log에서 같은 request id로 짝지었다.
  - 그 ZIP과 현재 트리 사이에 다운로드 경로 코드가 바뀌지 않았다(헤더의 17파일 비교).
- **현재 트리의 실프로세스 다운로드 (S2, 이 평가에서 실행).** core + owner worker + runner 2개 + gateway 실프로세스 구성이다.
  - A(REAL ultralytics): e3 checkpoint를 gateway로 받았다. 31,600,545 B, sha 일치, pre-send.
  - prune된 e1·e2(31,600,289 / 31,600,417 B)도 store 사본으로 200을 받았다.
  - B(stub): e3 163 B, sha 일치.
  - 공개 상세의 상태는 `{1: PRUNED, 2: PRUNED, 3: PRESENT}`이다.
  - FIX5 smoke(`WP7/FIX5_T3_34/apply/prune_roundtrip_smoke_after_apply.txt`, 00:42Z, PASS)와 결과가 같다.
- **브라우저 다운로드:** EVAL_1에서 본 V4B_FIX Playwright 증거(이전 dist)가 그대로이다.
- **공개 대기:** 최종 ZIP(`bc0500bd…` 등)으로 돌린 P2 T4/T5 rerun2는 작성 시점에 `docs/internal/evidence/T{4,5}/rerun2`에 없었다.
  - P2의 진행 중 scratch 출력은 보았다(`runtime/test_root/t4t5/final2/t5/evidence/t5r2_steps.jsonl`, 11:30 KST).
  - 거기서 다운로드 단계는 모두 PASS였다. train3 최신 checkpoint를 id로 받았고 31,600,545 B, sha 일치, `in_gateway_store`, pre-send였다.
  - prune-round-trip도 PASS였다(e1·e2 `already_absent` → PRUNED, e3 PRESENT).
  - **공개되지 않은 산출물이므로 채점에 쓰지 않았다.**

### 4. resume_retention — **20 / 25** (anchor 20: 사소한 누락 외 충족)

**retention prune 왕복 (필수 점검 T3-34) — 충족**

- `tests/owner/runner_protocol/test_t3_34_prune_roundtrip.py`(E1 6/6):
  - `:75`: 설계 표의 T3-34 행이다. runner 보고 전 PRESENT, 보고 후 PRUNED와 prune 기록. 같은 lease에 한 번만 전달, 재보고는 replayed, 최신 full state는 요청하지 않음(`:137`).
  - `:162`: 거부 6건이 있어도 행이 바뀌지 않는다.
  - `:198`: `already_absent` 보고.
  - `:223`: 전달 시점 재검사와 이후 lease 재전달.
  - `:268`: 실제 `RunnerDaemon`이 스스로 prune하고 store 사본은 그대로 둔다(`:296`).
  - `:304`: keep-everything 상태가 되면 열린 지시를 취소한다.
- 원격 정책 10건(E3)과 runner prune 제어 14건(E2)이 통과했다.
- 실프로세스 S2(이 평가)와 FIX5 smoke:
  - REAL `already_absent` ×2
  - stub 실삭제 ×2(각 163 B 해제)
  - 요청 DONE(전달 1회), 최신 PRESENT, 다운로드 가능, store 사본 유지

**재개 시나리오 — runner 프로토콜 경로에서 충족**

- 테스트:
  - `test_t3_control.py:65`(pause→HTTP resume)
  - `test_t3_restart.py:70`(runner 재시작 후 재개)
  - `test_explicit_cpu.py:10`(실제 CPU pause→full-state 재개)
  - `test_state_machine.py` 9, `test_stage_trainer.py` 19(최신 full state 재개, ETRI CPU bitwise, 누락 시 RESUME_BLOCKED)
  - `test_dd_a46…:174`(재개 시 gateway 입력으로 재검증)
  - `test_retention_remote_policy.py:101`(L1이 epoch 1에서 이어 감, 정책 적용 중 보호 유지)
  - 모두 E1/E3에서 통과했다.
- 실행 증거:
  - T4/T5 FINAL: attempt 2가 `resumed_from=full_e0001.pt`.
  - T7 GPU(트리 `9dafe4d0…`): pause 후 재개, SIGKILL→lease 만료 후 재개.
- 재개 기점 보호: 정책과 관계없이 최신 live full state는 prune 대상이 아니다(`retention.py:259-277`, `LATEST_FULL_STATE`).

**감점 (Minor, 문서화된 한계)**

- 기준 문구 "checkpoint affinity 또는 전송 후 재개"의 두 메커니즘이 모두 없다.
  - lease 배정이 checkpoint 위치를 고려하지 않는다(`runner_api/envelope.py:20` "affinity is not applied").
  - runner는 `resume.location`을 쓰지 않는다.
  - runner 사이 checkpoint 전송이 없다.
  - 근거 문서: DD-A38·DD-A68, `COMPATIBILITY.md:57`, `FEATURES.md:124`.
- 따라서 재개는 checkpoint를 가진 그 runner에서만 증명되었다. 배포 구성(runner 1대)에서는 충분하다.
- 함께 적는 0.1.0 한계(DD-A68, `FEATURES.md:125`, `OPERATIONS.md:130`):
  - prune 지시는 lease 중 heartbeat로만 전달된다. 유휴 runner와 `PRUNE_TRANSFER_PENDING` 연기분은 다음 lease를 기다린다.
  - `prune_requests` 조회 route가 없다(owner DB에만 있음).
- 조정자 지시대로 Minor를 넘겨 감점하지 않았다. anchor 20이다.

---

## 결함과 한계

EVAL_1 결함 D1–D5는 모두 FIXED이다. 새 결함은 없다. 남은 항목은 모두 Minor이고 문서화된 한계이다.

| ID | 등급 | 기준 | 위치 | 내용 | 권고 |
|---|---|---|---|---|---|
| L1 | Minor (문서화된 한계, DD-A38/A68) | resume_retention | `mbnas_core/src/mbnas_core/runner_api/envelope.py:20`, `mbnas_runner`(resume.location 미사용) | checkpoint affinity와 runner 사이 전송 후 재개가 없다. runner가 여러 대일 때 다른 runner가 재개 작업을 가져가면 그 runner에 파일이 없다 | 1.5.1 이후 둘 중 하나: lease 정렬에 `resume.location.runner_id` 우선(설계 D5 2차), 또는 store에서 digest 검증 전송 후 복원(WP-5 §2). 우선은 runner가 여러 대일 때의 동작(`RESUME_BLOCKED MISSING_STATE`, scratch 재시작 없음)을 시험 1건으로 고정할 것 |
| L2 | Minor (문서화된 한계, DD-A68) | resume_retention | `retention.py:345-392`(lease 중 heartbeat 전달) | 유휴 runner의 지시와 연기된 지시는 다음 lease까지 PENDING/DELIVERED로 남고 행은 PRESENT이다 | 유휴 runner 전달 경로(예: claim 204 응답이나 별도 poll)는 1.5.1 항목이다 |
| L3 | Minor (문서화된 한계, DD-A68) | resume_retention | owner DB `prune_requests` | 조회 route가 없어 운영자가 대기 요청을 API로 볼 수 없다 | owner route `GET /core/v1/owner/runs/{id}/prune-requests` 추가는 1.5.1 항목이다 |

**범위 밖 관찰 (C08 채점 아님, 공개 전 데이터).** P2의 진행 중 T5 rerun2 scratch 출력에 이것이 있었다.

- 위치: `runtime/test_root/t4t5/final2/t5/evidence/t5r2_steps.jsonl`, 단계 `genconfig-create`
- `source.kind=generated` 학습 생성이 **422 `STRUCTURE_INVALID`**였다(`location: core`).
- 원인 메시지: `BLOCK_UNSUPPORTED` "build at row 6 (NASGlobalTransformer): KeyError: 'NASGlobalTransformer'"
- 생성된 구성의 ETRI mixed 블록이 core 측 구조 검사에서 등록되지 않은 것으로 보인다.
- C11(generated-config 단계)과 C03 담당 평가자가 공개본으로 확인해야 한다. 나는 재현하지 않았다.

## 총점과 판정

| 기준 | 점수 | anchor |
|---|---|---|
| host_path | 25 | 25 |
| bytes_hash | 25 | 25 |
| real_download | 25 (잠정) | 25 |
| resume_retention | 20 | 20 |
| **합계** | **95 / 100** | |

Blocker 0, Critical 0, Major 0, Minor 3(문서화된 한계 L1–L3)이다. 총점이 90을 넘고 Blocker·Critical이 0이므로 **PASS**이다.

real_download는 T4/T5 rerun2 공개 후 추록에서 확정한다. rerun2에서 최종 ZIP의 다운로드나 sha 대조가 실패하면 real_download를 다시 매긴다. 총점이 90 이하가 될 수 있다.

## 확인 못 함

1. 최종 ZIP(`bc0500bd…`, owner `867cc577…`)으로 돌린 T4/T5 rerun2는 작성 시점에 공개되지 않았다. 진행 중 scratch 출력만 보았고 채점에 쓰지 않았다(추록 대상).
2. 최종 dist(`index-7f__3l0I.js`)에서 브라우저로 내려받는 것은 실행하지 않았다. 브라우저 증거는 이전 dist의 V4B_FIX뿐이다.
3. prune 왕복의 GPU 경로는 실행되지 않았다. T7 트리 `9dafe4d0…`는 fix 5 이전이다. 이번 증거는 모두 CPU이다.
4. runner가 여러 대일 때 다른 runner가 재개 작업을 가져가는 경우는 실행하지 않았다(L1). 코드로만 추정했다.
5. 유휴 runner의 대기 지시가 다음 lease에서 처리되는 실프로세스 경로(L2)는 실행하지 않았다. 테스트 `test_t3_34…:223`(이후 lease 재전달)만 통과를 확인했다.
6. 실제 ultralytics 학습에서는 학습기가 중간 상태를 먼저 지워 runner 보고가 `already_absent`였다. runner가 실제로 지우는 경로는 stub 학습기 실프로세스(S2 B)와 테스트로만 확인되었다(FIX5 기록과 같음).

# C08 원격 산출물 — 독립 재평가 EVAL_2

> **15:58 KST 확인 반영.** 맨 끝의 "Confirmation (DD-A75 ZIP 9f03c484…)"를 보라. 배포 owner ZIP `9f03c484…`(트리 `8fc93f2a…`) 기준 총점은 **95/100, PASS**이다.
> D6은 FIXED이다. 그 앞의 13:45 추록(90/100, FAIL)은 DD-A75 이전 ZIP `53e6a1f0…`에 대한 기록이다.
> 16:06에 DD-A76 지시대로 ZIP 단독(코드·시험·설정 모두 추출본)으로 다시 검증했다. 351건 모두 통과했고 점수 변동은 없다. 작업 트리 이동은 관찰 O2로 적었다.

> **13:45 KST 추록 반영.** 맨 끝의 "Addendum (T4/T5 attempt_dd_a73)"을 보라. 현재 트리 `1b4bd1a4…` 기준 총점은 **90/100, FAIL**이다.
> host_path가 25에서 20으로 내려갔다. 원인은 D6 Major(DD-A75 F1: 공개 `raw_metrics.resolved_recipe`에 runner 절대경로가 실림)이다.
> real_download 25는 최종 ZIP 증거로 확정되었다. 아래 본문은 11:35 시점 기록으로 그대로 둔다.

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

---

## Addendum (T4/T5 attempt_dd_a73) — 2026-10-28T13:35–13:45+09:00

**요약.** real_download는 최종 ZIP 증거로 25가 확정되었다. 그러나 host_path에서 Major 결함 1건(DD-A75 F1, 이미 알려져 있고 수정 중)을 직접 확인해 25→20으로 내린다.

- 현재 트리 `1b4bd1a4…` / owner ZIP `53e6a1f0…` 기준 총점: **95 → 90/100**, **FAIL**(90 초과가 아님). Blocker 0, Critical 0, Major 1.
- F1 수정이 들어간 owner 재빌드본에서 아래 조건을 확인하면 host_path는 25로 돌아가고 총점은 95(PASS)가 된다.

### 기준 상태
- tree hash: `sha256:1b4bd1a4a45196e3c558d62507f109301973eddc180df5968ed7014a72e6708c` (221 files).
  - `source_tree_hash.py`는 exit 0이다. 13:35:09와 13:39:24에 계산했고 값이 같다(`scratchpad/wp3/c08/treehash_add_{start,end}.json`).
- EVAL_2(`f00987be…`) 이후 바뀐 소스는 `mbnas_core/src/mbnas_core/training_runs/selection.py`(DD-A73) 하나다. 새 테스트 `tests/owner/api/test_dd_a73_bind_structure_mixed.py`가 추가되었다.
  - C08 코드 경로(artifact 등록·전송·다운로드·prune·재개)는 바뀌지 않았다. 따라서 EVAL_2의 재실행(E1–E4, S2)은 C08 범위에서 유효하다.
- owner ZIP `53e6a1f0…`(82,702,282 B):
  - `sha256sum` 결과가 `dist/owner/*.sha256`, `T4/rerun2/attempt_dd_a73/zips_sha256_used.txt`와 같다.
  - BUILD_INFO 트리는 `1b4bd1a4…`이고, 패키지 소스 221/221이 현재 트리와 바이트 동일하다.
- public ZIP `bc0500bd…`는 변경이 없다(129/129 동일).

### 직접 대조한 것 (읽기 전용)
- **다운로드 (V2, `scratchpad/wp3/c08/verify_a73_downloads.py`, `python3 -I`, exit 0): 16/16 통과.**
  - 대상: T4 local 7건, T4 runners 2건, T5 7건.
  - 각 건마다 64자리 전체에서 `sha256(body) == X-Artifact-Sha256 == 목록 sha`, http 200, verdict PASS를 확인했다.
  - 같은 request id의 gateway access 줄(200)과 core `/record` 줄(200)을 짝지었다.
  - runner 객체는 `in_gateway_store`이고 `pre-send`이다. core 생성 YAML은 `verification core`이다.
- **export 2/2.**
  - T4 `af9ad22d…`: 23,611 B, 멤버 21개
  - T5 `ea1a57d0…`: 22,710 B, 멤버 20개
  - 둘 다 단계 sha = `X-Bundle-Sha256` = `run/*_export_*.zip.sha256`이고, SHA256SUMS 검증이 통과했다.
- **단계표.**
  - T4 local 36행 모두 PASS(항목 15/15). runners 15행 모두 PASS(7/7).
  - T5 35행 모두 PASS. coredown 2행 PASS.
  - 콘솔 exit: T4 두 변형 모두 doctor/start/harness/stop `exit=0`(`t4a73_{local,runners}_console.txt`). T5는 phase 전부와 `docker stop`이 `exit=0`(`t5a73_console.txt`).
- **request id 연결.**
  - T4: harness id 150/150이 echo되었고, 129개는 양쪽 access log에 한 줄씩 있다.
  - T5: 101/101, 81개가 한 줄씩이다(`run/t*a73_request_id_linkage.json`).
- **재개 (최종 ZIP).** T4 local과 T5 모두 같은 흐름이다.
  - PAUSED(`PAUSED_AT_EPOCH`, full state e1) → attempt 2 COMPLETED
  - `resumed_from=full_e0001.pt`, `full_state_resume=true`, device cpu
  - runner는 `runner-b491a985` / `runner-3c560d74`이다.
- **prune 왕복 (최종 ZIP).** T4 `train-8d6a02429d37`, T5 `train-9bf2edcef48a`.
  - e1·e2는 `prune_requests` DONE(전달 1회, `RETENTION_LAST_RUNG_END_ONLY`, `already_absent`)이고 행은 PRUNED이다.
  - e3는 PRESENT이고 `latest_full_state_kept`이다. id로 받았을 때 31,600,545 B, sha 일치였다.
  - prune된 checkpoint도 store 사본으로 200을 받았다.
- **DB v5→v6 이관.** T4 FINAL DB 사본으로 확인했다(`migration_v5_to_v6_check.txt`).
  - 결과 PASS: `prune_requests`만 추가, 기존 행 208→208, integrity ok, 원본 sha 불변.
- **PRUNED→PRESENT 표시 변화.** `run/t5a73_pruned_state_flip_check.json`에 기록되어 있다.
  - store 다운로드 뒤 공개 상태 3건이 모두 PRESENT로 바뀐다. core 행은 PRUNED / PRUNED / PRESENT 그대로이고 요청은 DONE이다.
  - 이월 #11로 이미 알려진 항목이다(설계 DD-A66/A68: store 사본 항상 유지). 아래 L4로 적되 이중 감점하지 않는다.

### host_path에 속하는 새 결함: D6 (Major, 알려짐, DD-A75 F1 수정 중)
- **현상.** 공개 응답에 runner 절대경로가 실린다.
  - 위치: snapshot과 candidate detail의 `evaluations[]/candidates[].raw_metrics.resolved_recipe.{data,model,project,save_dir}`(검증 ON → `NOT_EVALUATED` 행)
  - T4 증거 `run/t4a73_local_trainval_candidate_detail.json`: `<W>/t4/zip/mbnas_application/runtime/runner/runs/runs/train-…/…`(증거 사본에서 가려진 host 경로)
  - T5 증거 `run/t5a73_trainval_candidate_detail.json`: `/srv/mbnas/runtime/runner/runs/runs/train-554acdd5b4ea/…`
  - T4 T8 분류(`t8/responses_host_path_classification.txt`): 24회, 4개 응답
- **원인 확인 (현재 트리).** `mbnas_core/src/mbnas_core/console/projection.py:288-295`의 `_raw_metrics`는 device uuid만 치환하고 경로는 스크럽하지 않는다.
  - probe `scratchpad/wp3/c08/probe_raw_metrics_paths.py`(exit 0)로 확인했다. 절대경로가 그대로 나오고, 같은 값에 `scrub_deep`을 적용하면 `<path>`가 된다.
- **위반하는 문서.**
  - `docs/public/API.md:99`("호스트 절대경로는 나오지 않는다")
  - EVAL_2가 D3 FIXED와 host_path 25의 근거로 삼은 서술: `runner_protocol.py:6-14`("never projects them into a public response"), `SECURITY_MODEL.md:96`("공개 응답에는 투영되지 않는다")
- **평가자 누락 고지.** 이 노출은 EVAL_1/EVAL_2에서 읽은 T4/T5 FINAL 증거에도 이미 있었다(`T4/rerun/run/t4f_local_trainval_candidate_detail.json`, `T5/rerun/run/t5f_trainval_candidate_detail.json`).
  - 나는 비투영 주장을 projection canary 스위트로만 확인했다. 그 스위트는 `resolved_recipe`를 심지 않는다. T4/T5 응답 본문은 경로 스캔을 하지 않았다.
  - 그러므로 EVAL_2의 host_path 25는 과대했다.
- **점수.** host_path **25 → 20**이다.
  - id/digest/kind/runner_id 참조, DTO extra=forbid, prune 메시지의 상대경로, gateway→core 비전달은 그대로 증거가 있다.
  - 다만 "runner 경로는 owner 전용이며 공개 투영되지 않는다"는 조건과 상충하는 Major가 있다.
  - Major 상충을 20으로 둔 C04 EVAL_2 선례를 따랐다. 한 결함이므로 다른 기준에서 다시 감점하지 않는다.
- **25로 되돌릴 조건 (재빌드 sha를 받으면 한 줄로 확인).**
  1. core projection이 `raw_metrics` 전체(snapshot, candidate detail, SSE payload)에서 절대경로를 스크럽하고 회귀 테스트가 있다.
  2. 재빌드 owner ZIP의 BUILD_INFO 트리가 그 수정 트리와 같다.
  3. 검증 ON 행 응답(T4/T5 재캡처 또는 동등한 실행)에서 runner 절대경로가 0건이다.
  4. `runner_protocol.py:6-14`와 `SECURITY_MODEL.md:96`의 서술이 사실과 일치한다.

### 기준별 점수 (현재 트리 `1b4bd1a4…`)

| 기준 | EVAL_2 | addendum | 이유 |
|---|---|---|---|
| host_path | 25 | **20** | D6 Major(DD-A75 F1, 수정 중) |
| bytes_hash | 25 | 25 | 최종 ZIP 다운로드 16/16 digest 일치, prune은 digest 대조, 변경 없음 |
| real_download | 25(잠정) | **25(확정)** | 최종 ZIP T4(bwrap, GPU 노드 없음)와 T5(docker + host core)의 다운로드 로그·export·access log 연결을 직접 대조함 |
| resume_retention | 20 | 20 | 최종 ZIP에서 pause→full-state 재개와 3 epoch prune 왕복을 확인함. affinity와 runner 사이 전송 후 재개는 여전히 없음(Minor) |
| **합계** | 95 | **90 / 100** | |

판정: **FAIL**이다(총점 > 90 미충족). Blocker 0, Critical 0, Major 1(D6, 수정 중).

### 남은 결함·한계

| ID | 등급 | 기준 | 내용 | 상태 |
|---|---|---|---|---|
| D6 | **Major** | host_path | 공개 `raw_metrics.resolved_recipe.*`에 runner 절대경로가 실린다(`projection.py:288-295`). API.md:99, `runner_protocol.py:6-14`, `SECURITY_MODEL.md:96` 서술과 상충한다 | 알려짐, DD-A75 F1 수정 중 |
| L1 | Minor | resume_retention | checkpoint affinity와 runner 사이 전송 후 재개가 없다(DD-A38/A68) | 문서화된 한계 |
| L2 | Minor | resume_retention | 유휴 runner에는 prune 지시가 전달되지 않는다(lease 중 heartbeat만 사용) | 문서화된 한계(DD-A68) |
| L3 | Minor | resume_retention | `prune_requests` 조회 route가 없다 | 문서화된 한계(DD-A68) |
| L4 | Minor | resume_retention | store 다운로드 뒤 공개 checkpoint 상태가 PRUNED→PRESENT로 바뀐다(core 행은 PRUNED) | 알려짐, 이월 #11(이중 감점 안 함) |

**정정.** EVAL_2 재실행 S1의 `DEVICE_UNAVAILABLE`을 나는 "제품 결함 아님"이라고 적었다.

- 같은 원인이 DD-A75 **F2**(Major, owner 운영)로 분류되었다. 내용은 `scripts/dev/wp3_three_process_smoke.py`의 GPU 벤치 장치 전제 때문에 GPU 없는 sandbox에서 owner 통합 스위트가 14/16이 되는 것이다.
- 공개 API 동작으로는 정직한 거부가 맞다. 운영 도구 결함으로는 C09/C12 범위이고, C08 anchor에는 반영하지 않는다.

### 확인 못 함 (addendum)
1. DD-A75 F1 수정은 아직 트리에 없다(13:39 기준 소스 변경 없음). 재빌드 sha를 받은 뒤 위 1–4로 확인한다.
2. SSE payload의 같은 노출(DD-A75 서술)은 내가 직접 보지 않았다. snapshot과 candidate detail만 확인했다.
3. 최종 dist(`index-7f__3l0I.js`)에서 브라우저로 내려받는 것은 실행되지 않았다. T4/T5는 stdlib harness를 썼다.
4. prune과 재개의 GPU 경로는 실행되지 않았다(T7 `9dafe4d0…`는 fix 5 이전).
5. T4 sandbox 안의 `verify.sh` integration 14/16과 relocation 4/7(DD-A75 F2/F3)은 C08 범위 밖이라 다시 확인하지 않았다.

---

## Confirmation (DD-A75 ZIP 9f03c484…) — 2026-10-28T15:46–15:58+09:00

**결론.** 대상은 배포되는 owner ZIP `9f03c484…`이다.

- 추록에서 제시한 25 복귀 조건 4개 중 1–3은 충족되었다. 4는 C08이 평가하는 경로 값 채널에 대해 충족되었다.
- host_path는 **20 → 25**이고 총점은 **95/100, PASS**이다. Blocker 0, Critical 0, Major 0(D6 FIXED)이다.
- 자유 텍스트 안의 공백 경로 꼬리(이월 #14)는 **알려진 관찰 O1**로 적는다. C08 점수에는 넣지 않는다. 이유는 아래 조건 4에 있다.

### 대상 확인 (조건 2)
- `dist/owner/…owner-private-runtime-linux_x86_64.zip`
  - `sha256sum` = `9f03c484bbc8c0a2227cc325658892e3efe286d08cca9a1978383158075c2f2a`, 85,188,530 B
  - `.sha256` 파일 및 `T4/rerun2/attempt_dd_a75/zips_sha256_used.txt`와 같다.
- BUILD_INFO: build `r-07c7cef95cd73775`, tree `8fc93f2a…`(221).
  - ZIP 안의 패키지 소스 221개를 scratch에 풀었다(`scratchpad/wp3/c08/zipsrc/owner`).
  - 같은 알고리즘(`mbnas_contracts.util.source_hash.compute_source_tree_hash`)으로 다시 계산한 값이 **`8fc93f2a…`로 일치**했다(`zip_tree_hash.py`, exit 0).
  - public ZIP `bc0500bd…`도 다시 계산한 값이 `9859adb2…`(129)로 BUILD_INFO와 일치했다. public과 registration은 바뀌지 않았다.
- `1b4bd1a4…` → `8fc93f2a…` 사이에 바뀐 파일은 `mbnas_core/console/projection.py` 하나다.
  - 근거 1: FIX7 manifest(`WP7/FIX7_DD_A75/F1/00_tree_manifest_before.json`, `F1b/50_tree_manifest_after.json`)를 비교했다.
  - 근거 2: 두 manifest의 항목으로 다시 계산한 집계가 각각 `1b4bd1a4…`(내가 13:35에 직접 잰 값)와 `8fc93f2a…`이다.
  - 근거 3: after manifest의 파일별 digest가 ZIP에서 푼 소스와 전부 같다(`manifest_check.py`).
  - 따라서 attempt_dd_a73(owner `53e6a1f0…`)의 다운로드·재개·prune 증거는 이 ZIP에도 유효하다. 바뀐 것은 공개 투영뿐이다.
- **작업 트리 이동 (주의).** ZIP 빌드(15:14) 뒤에도 작업 트리가 계속 바뀌고 있다. tree hash는 15:46에 `4a1b9e3c…`, 15:52에 `b2ee6348…`였다.
  - 내용은 내부 참조(DD-*, WP-*)를 지우는 docstring·주석 정리이다(15:46 기준).
  - 36개 파일은 docstring을 뺀 AST가 같다.
  - `runner_protocol.py`, `artifacts/registry.py`, `console/routes_files.py`와 `schema.sql`은 diff상 문자열·주석만 바뀌었다.
  - 그래서 핵심 시험은 **ZIP에서 푼 코드로** 다시 돌렸다. `PYTHONPATH`=풀어 둔 소스이고, import 출처를 assert하는 시험을 함께 넣었다.

### 조건 1 — 수정과 회귀 시험
- 수정 코드(ZIP 판): `mbnas_core/console/projection.py:291-319`
  - `_path_values`: "/"로 시작하는 값은 `/api/v1`을 빼고 통째로 `<path>`로 바꾼다. 공백과 unicode가 있어도 마찬가지이다.
  - `_raw_metrics`가 `scrub_deep(_path_values(raw))`를 쓴다.
- **ZIP 코드로 실행:**
  - DD-A75 시험 4파일 42건 + canary 47건 + T3-34 6건 = **95 passed**, exit 0
  - origin 확인 시험 + C08 핵심 묶음(runner_protocol 82, artifacts 15, 공개 download/routes/store/transfer/prune 164) = **262 passed**, exit 0, 254 s(`c_zip_e1.{txt,meta}`).
    - 이 실행의 경고 29건은 모두 `PytestUnknownMarkWarning`이다. scratch의 origin 시험 파일을 함께 넘겨 pyproject 마커 등록이 읽히지 않았기 때문이다.
- 작업 트리로 실행: DD-A75 + canary 89 passed, runner_protocol + artifacts 97 passed(모두 exit 0).
- 음성 대조(FIX7 기록):
  - 새 F1 시험은 수정 전 코드에서 8 failed / 7 passed(`F1/11_…`)였다.
  - F1b 시험은 F1 코드에서 11 failed / 12 passed(`F1b/11_…`), F1b 코드에서 42 passed(`F1b/21_…`)였다.
- probe(ZIP 코드, `probe_scrub_residual.py`): `resolved_recipe.data`/`save_dir`에 공백·unicode가 있어도 `<path>`가 된다. `/api/v1/...`는 그대로 남는다.

### 조건 3 — 검증 ON 행 응답에서 runner 경로 0건
- P2 `T4/rerun2/attempt_dd_a75/run/t4a75_local_pathcheck_surfaces.json`, `T5/rerun2/attempt_dd_a75/run/t5a75_pathcheck_surfaces.json`.
  - install root와 runner work root에 공백, unicode, 심은 토큰을 넣었다.
  - snapshot, candidate detail, SSE replay(16 events), export 네 표면 모두 recipe 값이 `<path>`이다.
  - runner work root 토큰은 0건이고, `other` 절대경로도 0건이다. 남은 것은 client가 보낸 protocol 경로의 echo뿐이며 이는 설계대로이다.
- 탐지기 양성 대조(`run/pathcheck_detector_positive_control.json`): 수정 전 DD-A73 응답에서 누출을 찾는다(T4 8/4건, T5 4건). 탐지기가 순환 논리가 아님을 보여 준다.
- **내 독립 스캔:** a75 `run/*.json`에 저장된 공개 응답 19개를 직접 훑었다.
  - 심은 토큰(`rootX7kQ2`, `Zq9mT4`, `coreX8mR5`, `작업`, `공백`), 가림 표식(`<W`, `<ROOT`), `/home/`, `/srv/mbnas/runtime/rw`가 모두 0건이다.
  - recipe 값은 `<path>`뿐이다.
  - 예외는 `t4a75_verify_long.json`의 `rootX7kQ2` 6건이다. 운영자용 verify 보고서이며 공개 응답이 아니다.
- 기능 재확인 (`verify_a75_downloads.py`, `python3 -I`, exit 0):
  - a75 다운로드 5/5(T4 2, T5 3)가 64자리 전체로 `body == X-Artifact-Sha256 == 목록`이고, 같은 request id의 gateway access 200과 core `/record` 200에 짝지어진다.
  - T5 prune 왕복: e1·e2 PRUNED(`already_absent`), e3 유지. prune된 사본도 store에서 200을 받았고 sha가 일치했다.
  - ETRI 후보로 generated-config 생성이 PASS(200)였다.
  - 콘솔은 start/doctor/harness/stop과 docker stop이 모두 `exit=0`이다.
  - 연결: T4 48/48(43개가 양쪽 한 줄씩), T5 60/60(50개).

### 조건 4 — 서술과 실제
- `runner_protocol.py:6-14`(ZIP 판, 공개 멤버, 미편집): 두 채널(E6 `uri`, E8 `outputs`의 rung result와 resolved recipe 경로)을 owner 전용으로 저장하고 공개 투영하지 않는다고 쓴다.
  - F1b 이후 이 두 채널의 경로 값은 공개 표면에서 `<path>`이거나 id·run 상대경로이다.
  - 위 조건 1·3으로 **사실과 일치**한다. 이월 #14가 지적한 "resolved recipe가 공개 raw_metrics에 스크럽되어 복사된다"는 설명 누락은 완전성 문제이고, 주장 자체는 참이다.
- `SECURITY_MODEL.md:96`(두 ZIP 모두 같은 문장)의 "checkpoint·결과 파일의 runner 호스트 절대경로 … 공개 응답에는 투영되지 않는다"는 경로 값 채널에 대해 참이다.
  - 다만 자유 텍스트(예: 실패 메시지)에 섞인 공백 있는 경로는 꼬리가 남는다. ZIP 코드 probe로 재현했다: `"RESUME_BLOCKED MISSING_STATE: <path> 작업 Zq9mT4/runs/…/full_e0001.pt not found"`.
  - 수정 에이전트 판정 파일(`WP7/FIX7_DD_A75/F1b/52_security_model_96_verdict.txt`)도 "NOT unconditionally true (free-text residual)"이라고 적는다.
  - 공개 문서에는 이 한계가 적혀 있지 않다(두 ZIP의 docs/public 검색 0건).
- **판정과 근거.** 이 꼬리는 C08이 평가하는 runner 경로 값 채널(uri, outputs)의 누출이 아니다. 오류·로그 메시지 전반의 스크럽 한계이다.
  - 이미 C02 추록 D9와 이월 #14로 집계되어 있고, 범위상 C05 path_error에 속한다.
  - 그래서 C08에서는 관찰 O1로만 적고 다시 감점하지 않는다.
  - 설계자가 line 96을 실패 메시지 속 언급까지 포함하는 문장으로 읽는다면, `52_…verdict.txt`의 제안 문구로 고치기를 권한다. 고치려면 public·registration docs-only 재빌드가 필요하다.

### 점수 (배포 owner ZIP `9f03c484…` / 트리 `8fc93f2a…`)

| 기준 | EVAL_2 | addendum | confirmation | 근거 |
|---|---|---|---|---|
| host_path | 25 | 20 | **25** | D6 FIXED(조건 1–4). O1은 관찰이다(C02 D9 / #14 / C05) |
| bytes_hash | 25 | 25 | **25** | 변경 없음. a75 다운로드 5/5 digest 일치 |
| real_download | 25(잠정) | 25 | **25** | a73 전체 흐름(코드 차이는 projection.py뿐) + a75 다운로드 5/5 + access log 연결 |
| resume_retention | 20 | 20 | **20** | 변경 없음. L1–L4 Minor |
| **합계** | 95 | 90 | **95 / 100** | |

판정: **PASS**이다(총점 > 90, Blocker·Critical 0). Major 0이다.

### 남은 항목
| ID | 등급 | 기준 | 내용 |
|---|---|---|---|
| L1 | Minor | resume_retention | checkpoint affinity와 runner 사이 전송 후 재개가 없다(DD-A38/A68) |
| L2 | Minor | resume_retention | 유휴 runner에는 prune 지시가 전달되지 않는다(DD-A68) |
| L3 | Minor | resume_retention | `prune_requests` 조회 route가 없다(DD-A68) |
| L4 | Minor | resume_retention | store 다운로드 뒤 공개 상태가 PRUNED→PRESENT로 바뀐다(이월 #11) |
| O1 | Minor (관찰, 채점 제외) | — | 자유 텍스트 속 공백 경로의 꼬리가 남는다. 공개 문서에 그 한계가 적혀 있지 않다(이월 #14, C02 D9, C05 path_error) |

### 확인 못 함 (confirmation)
1. 실패 메시지에 공백 경로가 실제로 실리는 흐름(예: 다른 runner가 재개를 가져가 `FILE_MISSING`이 나는 경우)은 실프로세스로 돌리지 않았다. 꼬리 노출의 실제 크기는 probe로만 확인했다.
2. 작업 트리는 이 확인 중에도 계속 바뀌었다(`b2ee6348…`, 15:52). 이 판정은 배포 ZIP `9f03c484…`(트리 `8fc93f2a…`)에 대한 것이다. 이후 정리본으로 다시 빌드하면 다시 확인해야 한다.
3. 최종 dist로 브라우저가 내려받는 흐름과 GPU 경로는 여전히 실행되지 않았다(EVAL_2와 같다).

### ZIP 단독 재검증 (DD-A76 지시, 15:59–16:06 KST)

**점수 변동 없음: 95/100, PASS.** 아래는 지시대로 코드·시험·설정을 모두 owner ZIP `9f03c484…` 하나에서 가져와 다시 검증한 기록이다.

- **추출.** ZIP을 scratch(`scratchpad/wp3/c08/zipfull/mbnas_application`)에 풀었다. 크기(215 MB) 때문에 `docs/internal/**`는 처음에 제외했다.
  - 푼 패키지 소스를 다시 계산하니 tree hash가 `8fc93f2a…`(221)로 BUILD_INFO와 일치했다(조건 2, ZIP 자체 기준).
- **실행.** cwd와 설정은 모두 추출본 것이다: `-c <추출본>/pyproject.toml --rootdir=<추출본>`, `PYTHONPATH=<추출본 5 패키지 src>`, `MBNAS_TEST_ROOT=<scratch>`.
  - 인터프리터는 `runtime/venv/bin/python`이다. 제3자 의존성만 이 venv에서 쓰고, mbnas 패키지는 PYTHONPATH가 앞선다.
  - 출처 시험(`origin_check/test_origin_full.py`)을 함께 돌렸다. `projection`, `runner_protocol`, `mbnas_server`, `mbnas_runner`, `harness`가 모두 추출본에서 import됨을 assert한다.
- **대상과 결과.** 추출본의 시험 파일로 다음 351건을 돌렸다(`zipfull_tests.{txt,meta}`).
  - 대상: DD-A75/DD-A75b 4파일 42건, canary 47건, `tests/owner/runner_protocol` 82건(T3-34 포함), `tests/owner/artifacts` 15건, 공개 download/routes/store 131건, transfer 19건, prune 제어 14건, 출처 시험 1건
  - 결과: **350 passed, 1 failed**
  - 실패한 1건은 `test_canary_projection.py::test_every_owner_only_allowlist_path_has_a_seed`이다. 원인은 내가 추출에서 뺀 ZIP 멤버 `docs/internal/analysis/DTO_ALLOWLIST_PROPOSAL.json`의 FileNotFoundError였다. 제품 결함이 아니다.
  - 그 파일 하나를 같은 ZIP에서 추가로 풀고 canary + 출처 시험을 다시 돌렸다: **48 passed**, exit 0(`zipfull_tests2.{txt,meta}`).
  - 따라서 ZIP 단독 기준으로 351건 모두 통과이다.
- **앞서 작업 트리를 쓴 실행 (고지).**
  - 작업 트리의 코드와 시험으로 돌린 것:
    - `c_dd_a75`: DD-A75 + canary, 89 passed, 15:48–15:49
    - `c_owner_rp_art`: runner_protocol + artifacts, 97 passed, 15:48–15:52
    - 이 시간대에 트리는 정리 편집 중이었다.
  - ZIP 코드(PYTHONPATH)에 작업 트리의 시험 파일을 붙여 돌린 것: `c_zipcode` 95 passed, `c_zip_e1` 262 passed.
  - probe `probe_scrub_residual.py`는 ZIP 코드로 돌렸다.
  - 위 ZIP 단독 실행이 이들을 대체하는 판정 근거이다.
- **관찰 O2 — 작업 트리 이동 (릴리스 세트의 결함 아님, DD-A76).**
  - 내가 잰 tree hash: 15:46:04 `4a1b9e3c…`, 15:52:36 `b2ee6348…`, 15:59:52 `308a16519c04fdfffeedf0d70074e2756737514e82da39cec65f0acbd11fde7d`(DD-A76의 15:58 값과 같음)
  - HEAD는 `903f95fe1`(15:13:24)이다. `git status` 변경 항목은 221개였다.
  - 15:46 시점 비교에서 코드 차이는 0이었고 docstring·주석만 달랐다(36개 파일 AST 동일, 3개 파일과 `schema.sql`은 문자열·주석만 다름). 그 뒤의 변경은 내가 대조하지 않았다.
  - 출하 ZIP 3종은 각자의 기록된 트리(`8fc93f2a…` / `9859adb2…`)에서 빌드되고 게이트된 산출물이다. 이 판정은 그 ZIP에 대한 것이다.

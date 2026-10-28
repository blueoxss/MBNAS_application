# C08 원격 산출물 — 독립 평가 EVAL_1

- set: **C08** (`docs/internal/criteria/C08.json` v1.0). anchor 25/20/15/0을 문자 그대로 쓴다.
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자. 채점만 하고 결정은 설계자가 한다.
- time: 2026-10-28T08:08–08:35+09:00 (2026-10-27T23:08–23:35Z)
- tree hash: `sha256:e220e72137165c57b08cd24388c11d64762b689b376a17911a774b8a3bb45236` (mbnas-source-tree/2, 221 files).
  `runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`는 exit 0이다. 08:08:40과 08:24:30 KST 두 번 계산했고 값이 같았다.
  지정 명령의 `| tail -3`은 JSON 닫는 괄호 3줄만 출력한다. 그래서 전체 출력의 `sha256` 값을 적었다(`scratchpad/wp3/c08_treehash.json`).
- 최종 ZIP과의 차이: 최종 ZIP의 트리는 `5a7bc592…`이다(owner ZIP `79cce02a…`의 `BUILD_INFO.json`).
  ZIP의 패키지 소스 221개를 현재 트리와 바이트 단위로 비교했다. 다른 파일은 `mbnas_core/src/mbnas_core/runner_api/routes.py` 하나다.
  이 파일은 08:06:54 KST에 바뀌었고(DD-A64 claim 직전 reclaim), diff는 25줄이다. E6/E7 등록, 다운로드, 보존 코드는 건드리지 않았다.
  따라서 T4/T5 다운로드 증거는 C08 범위에서 현재 트리에도 유효하다.
- 환경: CPU 전용이다. `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`를 썼다.
  pytest `--basetemp`와 probe 파일은 모두 세션 scratchpad(`…/scratchpad/wp3/c08/`)에 두었다.
  GPU, /data, etri_nas, build/source_snapshot은 건드리지 않았다. 내가 직접 쓴 프로젝트 파일은 이 파일 하나다.
  예외가 하나 있다. 공용 test root의 ultralytics 설정 파일 `runtime/test_root/runs/ultralytics_cfg/tests/Ultralytics/settings.json`이 08:21:12 KST에 갱신되었다.
  R3 실행 시각과 겹치므로 테스트 하네스가 기본 동작으로 고쳐 썼을 수 있다. 같은 시각에 다른 세션도 테스트를 돌리고 있었다.

## 결론

총점은 **80/100**이다(host_path 20, bytes_hash 20, real_download 25, resume_retention 15). Blocker 0, Critical 0이지만 총점이 90 이하이므로 **FAIL**이다.

- 가장 큰 감점은 resume_retention(15)이다. 필수 시험 T3-34(prune 왕복)는 구현되지 않았다. checkpoint affinity와 다른 runner로의 전송 후 재개도 없다.
  모두 DD-A38에서 범위를 줄이며 **한계로 문서화**한 항목이다. 숨은 결함은 아니다. 하지만 anchor를 문자 그대로 적용하면 "핵심 절반만 증거 있음"이다.
- 결함은 Minor 5건이다(D1–D5). Blocker, Critical, Major는 없다.
- 통과 조건(총점 > 90)을 맞추려면 resume_retention이 20점 이상이어야 한다. D1–D3을 고쳐 host_path와 bytes_hash가 25가 되어도 25+25+25+15 = 90이다.
  따라서 T3-34와 재개 전송(또는 affinity)을 구현하고 실행하거나, 설계자가 C08의 범위를 다시 정해 기준 버전을 올려야 한다.

## 재실행 요약 (cwd=TARGET_ROOT)

pytest 명령 공통: `runtime/venv/bin/python -B -m pytest <대상> -m "not gpu and not live and not legacy_records and not host_data" -p no:cacheprovider -q -rfEs --timeout=300 --basetemp=<scratchpad>`

| # | 대상 | exit | 결과 | 시간 |
|---|---|---|---|---|
| R1 | `tests/owner/runner_protocol`(16 파일, 76건) + `tests/owner/artifacts`(3 파일, 15건) | 0 | **91 passed**, 0 failed/skipped | 220 s |
| R2 | `tests/public/server/test_artifact_download.py`(33) · `test_artifact_routes.py`(29) · `test_artifact_store.py`(69) · `tests/public/runner/test_transfer_local.py`(19) · `tests/public/contracts/test_runner_protocol.py`(103) | 0 | **253 passed** | 41 s |
| R3 | `tests/owner/projection/test_config_revisions.py`(9) · `orchestration/test_retention.py`(11) · `training/test_explicit_cpu.py`(1) · `api/test_state_machine.py`(9) · `api/test_dd_a46_core_gateway_inputs.py`(5) · `api/test_registry_structure.py`(34) · `tests/public/runner/test_stage_trainer.py`(19) · `model_runtime/test_checkpoint.py`(18) · `server/test_wp3_gateway_relay.py`(43) | 0 | **149 passed**, 1 deselected(`test_rng_roundtrip_includes_cuda`, gpu 마커) | 95 s |
| P1 | probe `probe_structure_hashes.py`: 실제 in-process CPU 구조 검사 3건(gateway가 실어 보내는 텍스트 경로) | 0 | StructureReport의 hash 3종이 서로 다르게 동작함(아래 bytes_hash) | 약 10 s |
| P2 | probe `probe_crlf_source_sha.py`: CRLF YAML을 `model_path`와 텍스트 전달 두 경로로 비교 | 0 | `model_path` 경로의 `source_sha256` ≠ 파일 바이트 sha256(D1 재현) | 약 2 s |
| V1 | `verify_t4t5_downloads.py`(`python3 -I`): T4/T5 최종 ZIP 단계 로그의 다운로드 14건과 export 2건을 64자리 전체로 대조하고, 같은 request id의 gateway access 줄과 core `/record` 줄을 짝지음 | 0 | **14/14** 일치, export 2/2 일치 | <1 s |
| H | `source_tree_hash.py` 2회, owner ZIP과 트리 비교 | 0 | 값 동일, 1개 파일 차이(위 설명) | <5 s |

CPU 시간 합계는 약 8분이다. 로그와 probe는 프로젝트 밖에 있다. 위치는 `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c08/` 아래이다.

- `owner_rp_art.{txt,meta}`
- `public_art.{txt,meta}`
- `hash_resume.{txt,meta}`
- `probe_structure_hashes.{py,txt}`
- `probe_crlf_source_sha.{py,txt}`
- `verify_t4t5_downloads.{py,txt}`

---

## 기준별 채점

### 1. host_path — **20 / 25** (anchor 20: 사소한 누락(문서·라벨) 외 충족)

**충족 근거**

- **DTO (필수 점검).** `mbnas_contracts/src/mbnas_contracts/runner_protocol.py:62-63`에서 모든 wire 모델의 기반이 `extra="forbid"`이다.
  - `:66-70` `_relpath`는 절대경로, `..`, 드라이브 문자, NUL을 거부한다.
  - `ArtifactItem`(`:361`~)의 경로 필드는 run 상대 `relpath` 하나다. 나머지는 `sha256`·`bytes`·`kind`·`mime_type`·id 류·`state`·자유 dict `meta`이다.
  - 이를 고정하는 테스트(R2에서 103건 모두 통과):
    - `tests/public/contracts/test_runner_protocol.py:152` 모든 모델 extra=forbid
    - `:162` 미지 최상위 필드 거부
    - `:167` 중첩 필드 거부
    - `:196` runner→core 모델에 path·file·dir 이름의 필드 없음
    - `:209` relpath 6종 거부
- **artifact는 id/digest/kind/runner_id로 참조한다.**
  - core E6/E7은 `artifact_locations`에 `runner_id`, `store=runner_local`, `relpath`, `sha256`을 기록한다(`mbnas_core/src/mbnas_core/runner_api/routes.py:487-491`, `:524-527`).
  - artifact id는 `hash(run_id, relpath, evaluation_id)`이다(`:510`).
  - gateway가 받는 record에는 경로와 runner id가 없다(`mbnas_core/src/mbnas_core/artifacts/locations.py:20-21`).
  - core는 runner 객체를 자기 디스크로 판정하거나 제공하지 않는다(`console/routes_files.py:9-13`). 이를 `tests/owner/artifacts/test_artifact_wiring.py:177`이 확인한다.
  - gateway 다운로드는 id로 core record를 찾고, 그 digest로만 store에 접근한다(`mbnas_server/src/mbnas_server/artifacts/download.py:1-8`).
- **gateway→core 고객 경로 비전달.**
  - `tests/public/server/test_wp3_gateway_relay.py:304-305`: `upload_id`는 텍스트와 sha로 바뀌고, "core never receives a gateway-local path"를 확인한다.
  - `test_artifact_routes.py:572`: 브라우저가 넣은 host 입력을 제거한다(DD-A51 e).
  - `tests/owner/artifacts/test_split_output_root.py:31,40`: 고객 측에만 있는 output root로 만든 run은 core 단독으로는 거부되고, gateway 경유로는 COMPLETED와 다운로드까지 간다.
  - 같은 내용을 T5 최종 ZIP(`T5/RERUN_README.md`의 customer-only output root)에서도 실행했다.
- **재실행:** R1 91/91, R2 253/253.

**감점 사유 (D3, Minor)**

runner→core 채널의 checkpoint 경로와 결과 경로는 고객 runner의 **절대경로**로 core에 전달된다.

- `CheckpointReport.uri`(`runner_protocol.py:346`)에는 runner의 realpath가 들어간다(`mbnas_runner/src/mbnas_runner/reports.py:74`).
  core는 이를 `checkpoints.path`와 `artifact_locations.uri`에 저장한다(`routes.py:485`, `:490`).
- job 결과에도 runner 절대경로가 그대로 저장된다.
  T7 `dbviews/rerun_full_*.json`(예: `rerun_full_c_train-8fa9589076fc.json`)에서 `/home/`으로 시작하는 값이 든 필드는 다음과 같다.
  - `checkpoints[].path`
  - `evaluations[].checkpoint_path`
  - `jobs[].rung_result.resumed_from`
  - `jobs[].error.outputs.rung_result.checkpoints[].path`
  - `jobs[].error.outputs.rung_result.resolved_recipe.{data,model,project,save_dir}`

이 동작은 설계가 허용한 것이고(`docs/internal/analysis/REMOTE_PROTOCOL_DESIGN.md:396`, `:510`, `:529`), 공개 응답에는 나오지 않는다.

- 공개 투영 canary가 `CandidateDetail :: checkpoints[].path`에 심겨 있다(`tests/owner/projection/projection_world.py:209`).
- 공개 `checkpoint_path`는 run 상대경로이다(`T4/rerun/run/t4f_local_train_candidate_detail.json`).

그러나 다음 셋이 이 사실과 맞지 않는다.

- `runner_protocol.py:6-9`의 라벨은 "runner에서 나가는 모델에 host-path 필드 없음"이라고 쓴다.
- DTO 테스트(`test_runner_protocol.py:196-205`)는 필드 이름만 검사하므로 `uri`와 자유 형식 `outputs`를 잡지 못한다.
  표본 값도 `file:///w/a.pt`(`:94`)인데, 실제 runner는 접두사 없는 절대경로를 보낸다.
- `docs/public/SECURITY_MODEL.md:93`의 "core로 가는 고객 정보" 목록에 runner 로컬 경로가 빠져 있다.

기준 문구 "고객 경로 core 비전달"과 라벨·문서가 다르므로 20점이다.

### 2. bytes_hash — **20 / 25** (anchor 20)

**충족 근거**

- **등록 시 runner 재계산.**
  - `mbnas_runner/src/mbnas_runner/reports.py:50-76`(`verify_checkpoint`)은 run dir 내부인지, 크기가 같은지 본 뒤 `sha256_file`로 다시 계산해 보고값과 대조한다.
  - `:79-99`(`artifact_registration`)도 같은 방식으로 재계산한다.
  - 테스트:
    - T3-23 `tests/owner/runner_protocol/test_t3_files.py:31`: 변조 파일은 보고하지 않는다.
    - `:82`: core는 digest 누락, 잘못된 kind, 오래된 attempt를 거부한다.
- **store 수신 시 재검증.**
  - local 전송에서는 임시 사본을 다시 읽어 sha와 크기를 대조한다. 다르면 `SOURCE_CHANGED`이다.
    이미 있는 digest도 다시 해시하며, 다르면 `STORE_CONFLICT`이다(`mbnas_runner/src/mbnas_runner/transfer.py:17-31`).
  - gateway store는 스트리밍하며 재계산하고, 불일치면 `LOAD_FAILED`를 내고 원자적으로 게시한다(`mbnas_server/src/mbnas_server/artifacts/store.py:15-18`).
  - 테스트:
    - `test_transfer_local.py:194`, `:218`, `:258`, `:434`
    - `test_artifact_store.py:120`, `:133`, `:147`, `:223`, `:259`, `:312`, `:357`, `:389`
- **다운로드 시 digest 대조.**
  - `download.py:10-30`: 64 MiB 이하는 보내기 전에 검증하고, 큰 객체는 스트리밍 끝에서 대조한다.
  - gateway 테스트:
    - `test_artifact_download.py:117`, `:138`, `:157`, `:293`
    - `test_artifact_routes.py:142`, `:172`, `:187`, `:195`, `:224`
  - owner 테스트 `tests/owner/artifacts/test_artifact_wiring.py`:
    - `:48`: 등록 → 전송 → 다운로드 digest 일치, NOT_READY → READY
    - `:128`: 변조 바이트 409, `LOAD_FAILED` 기록
    - `:147`: 전송 안 된 객체 404
    - `:230`: export YAML 바이트
- **원문/정규/구조 hash 구분 (필수 점검: StructureReport hash 3필드).**
  - 계약 `mbnas_contracts/src/mbnas_contracts/openapi/openapi.json`의 `StructureReport` 설명:
    - `source_sha256`: "sha256 hex of the checked YAML bytes"
    - `yaml_hash`: "canonical YAML dict (not of the file bytes)"
    - `architecture_hash`: "O. Set only after a build"
    - `additionalProperties: false`
  - 모델 정의는 `schemas/records.py:577-640`이다.
  - **실행 산출물:** 공개 gateway 응답 캡처 `docs/internal/evidence/WP7/7V_FINAL/T8/responses_public/structure_check.json`
    - `source_sha256 = ecb6c44b…`. `sha256sum examples/models/yolo11n_user.yaml`로 다시 계산한 값과 같다.
    - `yaml_hash = sha256:a48faaf5…`
    - `architecture_hash = sha256:6f39fc80…`
    - `structure_check_broken.json`은 빌드에 실패해 `architecture_hash`가 null이다.
  - **P1:** 같은 모델을 LF와 CRLF+주석으로 보내면 source는 다르고, yaml_hash와 architecture_hash는 같다.
    채널 수를 바꾸면 yaml_hash와 architecture_hash가 둘 다 바뀐다. 한 문서 안의 세 값은 모두 다르다.
  - 구성 개정(ModelConfigRevision)의 원문과 정규 hash 구분: `tests/owner/projection/test_config_revisions.py:136-146`, `:151-157`, `:162-166`(R3 통과).
    - CRLF와 UTF-8 주석을 바이트 그대로 보존하고, `X-Artifact-Sha256` = `source_sha256`이다.
    - 변조하면 409이다.
- **재실행:** 모두 통과했다.
  - R1: `test_t3_files.py` 5/5, `test_artifact_transfer.py` 2/2, `test_artifact_wiring.py` 10/10
  - R2: store 69, download 33, routes 29, transfer_local 19
  - R3: `test_config_revisions.py` 9/9, `test_registry_structure.py` 34/34
  - P1, P2 (아래)

**감점 사유**

- **D1 (Minor):** `mbnas_core/src/mbnas_core/console/model_services.py:628-633`, `:665`
  - `model_path`로 받은 사용자 YAML의 `source_sha256`을 `Path.read_text()` 결과에서 계산한다. read_text는 universal newline으로 CRLF를 LF로 바꾼다.
  - 그래서 CRLF 파일이면 "원문" hash가 파일 바이트의 hash가 아니다.
    P2 결과: 파일 바이트 `1b33b5c1…`, 보고값 `c4189e36…`(LF로 바꾼 텍스트의 hash).
  - 같은 파일로 학습할 때의 original 개정은 `sha256_file`과 `read_bytes`("no newline translation")를 쓴다(`training_runs/selection.py:145`, `:148`). 따라서 두 "원문" hash가 서로 어긋난다.
  - gateway 업로드 경로(텍스트 전달)는 정상이다(P2의 carried text = 파일 바이트).
- **D2 (Minor):** StructureReport의 hash 3종과 `SOURCE_SHA256_MISMATCH`를 고정하는 프로젝트 테스트가 없다.
  - grep 결과 상태 코드 매핑 `tests/owner/unit/test_schemas_contract.py:486-487`뿐이다.
  - 증거는 T8 캡처 1회와 이 평가의 probe뿐이다.
- **관찰(감점 아님):** `source_sha256` 표기가 둘이다.
  - StructureReport는 접두사 없는 hex이다. 계약 설명대로이다.
  - Upload와 ModelConfigRevision은 `sha256:` 접두사를 붙인다(T8 `upload.json`과 `structure_check.json` 비교).

### 3. real_download — **25 / 25** (anchor 25)

**충족 근거 (필수 점검: T4/T5 다운로드 로그)**

V1로 다시 대조했다. 각 다운로드에 대해 다음을 확인했다.

- 64자리 전체에서 `sha256(body) == X-Artifact-Sha256 == 목록 sha`, http 200, 단계 verdict PASS
- 같은 request id의 gateway access 줄이 `status=200`
- core `/record` 줄이 `status=200`

결과는 다음과 같다.

- **T4 (최종 owner ZIP `79cce02a…`):** `sha256sum`이 `dist/owner/*.sha256`과 같은 것을 확인했다. bwrap 안이고 GPU 노드는 없었다.
  - `local` 변형 6건: OFF architecture, runner artifact 73,072 B, core architecture, training_full e2 31,600,417 B, weights_only, 검증 ON 학습 checkpoint
  - `runners` 변형 2건
  - runner 객체는 `in_gateway_store=true`, `X-Artifact-Verification: pre-send`이다. 다운로드 뒤 NOT_READY에서 READY로 바뀌었다.
  - export는 23,211 B이고 sha가 `X-Bundle-Sha256`과 같다. 멤버 21개, `SHA256SUMS` 검증 통과이다.
  - 근거 파일: `T4/rerun/run/t4f_local_steps.jsonl`, `t4f_runners_steps.jsonl`
  - access 줄: `T4/rerun/logs/gateway.log:62,118-121,123,141,173,185`, `T4/rerun/logs/core.log:214,216,218,220`
  - doctor, start, harness, stop은 모두 `exit=0`이다(`T4/rerun/t4f_local_console.txt:4,6,40,48`).
- **T5 (최종 public ZIP `ce34b821…`):** docker 이미지와 host core 구성이다.
  - 다운로드 6건과 export 1건(22,378 B, 멤버 20개, SHA256SUMS 검증)이 모두 일치한다.
  - 근거 파일: `T5/rerun/run/t5f_steps.jsonl`
  - access 줄: `T5/rerun/run/t5f_gateway_container.log:24,49-52,54,66`, `T5/rerun/logs/core.log:83,85,86,88`
  - 단계는 모두 `exit=0`이다(`T5/rerun/t5f_console.txt:51,57`).
- **브라우저 실제 다운로드:** `WP6/V4B_FIX/e2e_steps.jsonl`
  - V4-10: Playwright가 UI에서 checkpoint `ck-3ac98a4e…`(31,600,161 B)를 내려받았다. 저장 파일 sha `9a583668…`가 기록값과 같다.
  - V4-09: 개정 3종의 저장 sha가 `source_sha256`과 같다.
- **GPU:** `T7/steps_rerun.jsonl`
  - `f2-download-via-gateway-store`와 `f-download-via-gateway`가 `digest_match: true`이다(42.1 MB checkpoint 포함).
  - `f-store-contents`에서 객체 201개를 모두 검증했다.
- **재실행:** R1 `test_artifact_wiring.py` 10/10, R2 download/routes 62/62.

**남는 점 (감점 안 함)**

- T4/T5의 다운로드는 stdlib harness가 했다. 브라우저는 V4B_FIX에서 host로 돌렸고, 그때 dist는 `index-BnpZcnwU.js`였다.
  최종 dist `index-CY9ctObG.js`는 T4/T5에서 서빙만 확인했다. REVIEW_LOG #12 residual_risk에 이미 기록되어 있다.
- 64 MiB를 넘는 객체는 스트리밍 끝에서 대조한다. 공개 문서에 적힌 한계이다.

### 4. resume_retention — **15 / 25** (anchor 15: 핵심 절반만 증거 있음)

**실행 증거가 있는 절반**

- **runner 프로토콜 경로의 재개**
  - `tests/owner/runner_protocol/test_t3_control.py:65`: epoch 경계에서 pause한 뒤 HTTP로 resume한다.
  - `test_t3_restart.py:70`: runner가 결과 없이 재시작되면 CHILD_EXIT_NO_RESULT를 보고하고 재개한다.
  - `tests/owner/training/test_explicit_cpu.py:10`: 실제 CPU 학습에서 pause 후 full-state로 재개한다.
  - 모두 R1/R3에서 통과했다.
  - R3에서 함께 통과한 재개 관련 파일:
    - `test_state_machine.py` 9/9
    - `test_dd_a46_core_gateway_inputs.py` 5/5 (`:174` 재개 시 gateway 입력으로 재검증)
    - `test_stage_trainer.py` 19/19 (최신 full state 재개, ETRI CPU 재개 bitwise, 누락 시 RESUME_BLOCKED)
    - `test_checkpoint.py` 18/18
- **최종 ZIP (T4/T5)**
  - PAUSED(`PAUSED_AT_EPOCH`, full state e1) → resume → COMPLETED이다.
  - attempt 2가 `resumed_from=full_e0001.pt`로 이어 갔다(T4 runner `runner-d3c27c94`, T5 runner `runner-0a6ab6db`).
- **GPU (T7, 트리 `9dafe4d0…`)**
  - (c) full_e0003에서 재개했다.
  - (e) runner SIGKILL → lease 만료 → attempt 2가 full_e0002에서 재개했다. attempt 1의 늦은 결과는 LEASE_LOST였다.
- **보존 (local/inline 경로):** `tests/owner/orchestration/test_retention.py` 11건이 통과했다(R3).
  - `:182`: PRUNED된 full state로는 재개를 거부한다.
  - `:196`: 살아 있는 후보의 재개 원본은 지우지 않는다.
  - `tests/owner/artifacts/test_checkpoint_artifact_id.py`는 PRUNED와 store-present 표시를 다룬다.
- **원격 run의 안전 대체:** `mbnas_core/src/mbnas_core/orchestration/retention.py:21-25`, `:159-200`은 원격 run을 keep_all로 강제한다.
  - 실행 흔적은 T7 snapshot 21개 파일 모두에 있다. `REMOTE_RETENTION_FORCED` 이벤트가 73건이다(`last_rung_end_only` → `keep_all`).

**증거가 없는 절반 (문서화된 한계 — DD-A38, DD-A18 D5/D7)**

- **T3-34**(`test_prune_request_round_trip_marks_checkpoint_pruned_only_after_runner_report`, `REMOTE_PROTOCOL_DESIGN.md:601`)
  - 테스트가 없다(grep 0건).
  - `prune_requests` 테이블과 `/checkpoints/{id}/pruned` 라우트가 없다.
  - heartbeat의 `control.prune`은 항상 `None`이다(`routes.py:393`).
  - 기준 notes의 "미실행 시험은 0점"을 이 절반에 적용했다.
- **checkpoint affinity가 없다.** `runner_api/envelope.py:20`에 "affinity is not applied, D5"라고 적혀 있다.
  - runner는 `resume.location`을 쓰지 않는다(`mbnas_runner` grep 0건).
- **다른 runner로 전송한 뒤 재개하는 경로가 없다.** WP-5 §2의 해당 단락과 §4 수용조건 "다른 runner 재개 시 전송 후 `resumed_from` 경로 복원"이 미충족이다.
- 공개 문서에는 이 한계가 적혀 있다: `docs/public/FEATURES.md:124`, `COMPATIBILITY.md:56`, `OPERATIONS.md:123`.

**추가 결함**

- **D4 (Minor):** 원격 keep_all 강제(`is_remote_run`·`_remote_override`)를 직접 고정하는 테스트가 없다(tests grep 0건). 실행 흔적은 T7 snapshot뿐이다.
- **D5 (Minor):** `docs/public/FEATURES.md:91`은 보존 정책 3종을 "시험 통과(CPU)"로 적는다.
  - 0.1.0에서 학습 kind는 모두 runner가 실행한다(`owner_worker/main.py:3-6`, `:89`). 그래서 모든 학습 run이 원격 run이 되고, `rung_end_only`와 `last_rung_end_only`는 배포 구성에서 **한 번도 적용되지 않는다**(T7 73건 강제).
  - 정책 시험은 inline worker 경로에서만 통과한 것이다.
  - VALIDATION_REPORT의 T3 행에도 T3-34 상태가 없다. 이 점은 C04 EVAL_2 N2에서 이미 지적되었다.

---

## 결함

| ID | 등급 | 기준 | 소유 패키지·파일 | 요약 | 구체적 수정 |
|---|---|---|---|---|---|
| D1 | Minor | bytes_hash | `mbnas_core/src/mbnas_core/console/model_services.py:628-633,665` (WP-4B 소유 경로) | `model_path` 구조 검사의 `source_sha256`이 개행 변환된 텍스트의 hash이다. CRLF 파일이면 원문 hash와 다르고(P2), 학습 original 개정 hash(`selection.py:145,148`)와도 어긋난다 | `data = path.read_bytes()`, `digest = sha256(data)`, `text = data.decode("utf-8")`로 바꾼다. `tests/owner/api/test_registry_structure.py`에 CRLF 파일 테스트를 넣는다(`doc["source_sha256"] == sha256(file bytes)`, 학습 original 개정과 같음) |
| D2 | Minor | bytes_hash | `tests/owner/api/test_registry_structure.py`, `tests/public/server/test_wp3_gateway_relay.py` (테스트 부재) | StructureReport hash 3종의 의미(원문·정규·구조)와 `SOURCE_SHA256_MISMATCH` 422를 고정하는 테스트가 없다 | P1과 같은 테스트를 추가한다. LF와 CRLF+주석이면 source는 다르고 yaml_hash·architecture_hash는 같다. 구조를 바꾸면 둘 다 다르다. digest를 틀리게 보내면 422 `SOURCE_SHA256_MISMATCH`이다 |
| D3 | Minor | host_path | `mbnas_contracts/src/mbnas_contracts/runner_protocol.py:6-9,346`, `mbnas_runner/src/mbnas_runner/reports.py:74`, `tests/public/contracts/test_runner_protocol.py:94,196-205`, `docs/public/SECURITY_MODEL.md:93` | E6 `uri`와 E8 `outputs`(rung_result 경로, resolved_recipe)로 runner 절대경로가 core DB에 저장된다. 설계상 허용이고 공개 응답에는 없다. 그러나 라벨·DTO 테스트·보안 문서가 "경로 없음"이라고 하거나 이를 빠뜨린다 | (a) 최소 수정: docstring을 바로잡는다. SECURITY_MODEL §6 목록에 "runner 로컬 checkpoint·결과 경로(owner DB 전용, 공개 투영에서 제거)"를 추가한다. DTO 테스트가 `uri`를 명시적으로 다루게 한다. 또는 (b) 기준 문구에 맞춘다: runner가 `relpath`만 보내고 `<work_root>/runs/<run_id>/<relpath>`를 스스로 계산한다. E8 전에 결과의 경로 값을 run 상대로 바꾼다 |
| D4 | Minor | resume_retention | `mbnas_core/src/mbnas_core/orchestration/retention.py:159-200` (테스트 부재) | prune 왕복을 대신하는 원격 keep_all 강제를 고정하는 테스트가 없다 | owner 테스트를 추가한다. runner가 실행한 run에 `last_rung_end_only`를 주면 PRUNED 행 0, 파일 불변, `REMOTE_RETENTION_FORCED` 로그 1건 이상, report에 `remote: true`이어야 한다 |
| D5 | Minor | resume_retention | `docs/public/FEATURES.md:91` (+`OPERATIONS.md:123`), `docs/internal/VALIDATION_REPORT.md:11` | 보존 정책이 배포 구성에서 적용되는 것처럼 읽힌다. 실제로는 runner 실행 run 전부가 keep_all로 강제된다. T3 행에 T3-34 미구현이 없다 | FEATURES 행을 "0.1.0 배포 구성에서는 keep_all 강제(REMOTE_RETENTION_FORCED); 3정책 시험은 in-process 경로만"으로 고친다. VALIDATION_REPORT T3 행에 "T3-34: 미구현(DD-A38)"을 적는다 |

### 문서화된 한계 (결함 아님, 점수에는 anchor대로 반영)

| ID | 한계 | 근거 | 영향 |
|---|---|---|---|
| L1 | retention prune 왕복(T3-34)이 없다. 원격 run은 keep_all로 강제된다 | DD-A38, DD-A18 D7, `FEATURES.md:124`, `COMPATIBILITY.md:56`, `OPERATIONS.md:123` | resume_retention 절반이 0이다. runner 디스크는 운영자가 직접 관리해야 한다 |
| L2 | checkpoint affinity와 다른 runner로의 전송 후 재개가 없다 | DD-A38, DD-A18 D5, `envelope.py:20` | runner가 여러 대일 때 다른 runner가 재개 작업을 가져가면 실패할 수 있다(아래 확인 못 함 6) |
| L3 | runner→gateway `upload` 전송 모드가 없다(local만 있다) | `transfer.py:15-16`, `FEATURES.md:124` | 분리 호스트의 runner 바이트는 store에 들어갈 수 없다 |
| L4 | 64 MiB를 넘는 객체는 스트리밍 끝에서 대조한다(응답 시작 뒤 본문이 끊김) | `download.py:23-30`, `FEATURES.md:125` | 받는 쪽이 헤더와 직접 대조해야 한다 |
| L5 | checkpoint `artifact_id`는 gateway가 처음 검증한 뒤에야 채워진다(웹은 ck id로 대체) | DD-A55 완료 기록 | 웹 대체 경로를 V4B_FIX V4-10에서 실행으로 확인했다 |
| L6 | 생성 architecture YAML은 core_local에서 제공된다(`X-Artifact-Verification: core`) | DD-A63 (c) | T4/T5에서 sha 일치를 확인했다 |

## 총점과 판정

| 기준 | 점수 | anchor |
|---|---|---|
| host_path | 20 | 20 |
| bytes_hash | 20 | 20 |
| real_download | 25 | 25 |
| resume_retention | 15 | 15 |
| **합계** | **80 / 100** | |

Blocker 0, Critical 0, Major 0, Minor 5이다. 통과 조건은 총점 > 90이고 Blocker·Critical이 0이며 필수 증거가 실행 산출물이어야 한다. 총점 80이므로 **FAIL**이다.

## 확인 못 함

1. T4/T5와 T7은 다시 실행하지 않았다. 각각 bwrap/docker 재구성, GPU 사용 금지, 15분 예산 때문이다. 단계 로그와 양쪽 access log를 읽어 대조했다.
2. 최종 dist(`index-CY9ctObG.js`)로 브라우저가 다운로드하는 것은 실행되지 않았다. 브라우저 증거는 이전 dist의 V4B_FIX뿐이다.
3. T7 GPU 증거의 트리는 `9dafe4d0…`로 현재 트리보다 이전이다. 이후 runner/gateway의 artifact 경로가 바뀌었는지 파일별로 대조하지 않았다.
4. 네트워크 `upload` 전송은 구현되지 않아 검증할 대상이 없다.
5. core DB에 저장된 runner 절대경로(D3)가 owner·admin 라우트나 owner export에 어떻게 나타나는지 확인하지 않았다. 공개 투영 canary의 존재만 확인했고 C06 canary suite는 다시 돌리지 않았다.
6. runner가 여러 대일 때 다른 runner가 재개 작업을 가져가면 무슨 일이 생기는지는 코드로만 추정했다. 추정은 핸들러가 `RESUME_BLOCKED MISSING_STATE`를 내고 scratch 재시작은 하지 않는다는 것이다(`mbnas_runner/training/handlers.py:14`, `envelope.py:20`). 이를 실행한 테스트는 보지 못했다.
7. `tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py`는 R1 시작 5초 뒤(08:11:16 KST)에 수정되었다. R1이 수정 전 판을 돌렸는지 수정 후 판을 돌렸는지 모른다. 수집 수 9는 같았다. C04 범위이고 C08 판정에는 영향이 없다.

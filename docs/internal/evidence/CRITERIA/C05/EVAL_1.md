# C05 입력·인증 — 독립 평가 EVAL_1

- set: **C05** (`docs/internal/criteria/C05.json` v1.0)
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자 — 채점만 하며 결정은 설계자
- time: 2026-10-28T03:18+09:00 시작, 03:50+09:00 보고 작성
- tree hash (mbnas-source-tree/2, 221 files; 지시된 `| tail -3`은 JSON 닫는 괄호만 출력하므로 `sha256` 필드를 인용):
  시작 `sha256:634a083bb910a28d1aa183c9a6a2cee66da7c1fc937f01f8aa85d1bb4c4a301d`,
  종료 `sha256:76ff726a1fd34353c085913f33d0bbee8ce646ed7beeb41f68000d19e3559fc0`.
  평가 도중(03:26–03:27) 다른 에이전트가 `mbnas_core/console/routes_runs.py`, `mbnas_core/orchestration/retention.py`를 수정했다(DD-A55 (a), checkpoint 표시). C05 표면(auth·ticket·sandbox·오류·경로)과 무관하며, 03:27 이후 실행분(R5~R18)은 종료 트리에서 돌았고, 그 전 실행분 중 auth/ticket 부분은 종료 트리에서 다시 돌렸다(R19, R20).
- ZIP: public `529243cf…`, registration `d5748c13…`, owner `ba8ed91f…` — sidecar `.sha256`과 일치, 스캔 전후 동일.
- 환경: CPU 전용(`CUDA_VISIBLE_DEVICES=""`, `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `--basetemp`는 scratchpad). GPU·원본 체크아웃(SOURCE_ROOT)·build/source_snapshot·/data 접근 없음. 프로젝트에 쓴 파일은 이 파일 하나다.
- 표기: 경로는 TARGET_ROOT 기준 상대경로. 평가자 산출물은 `$SCRATCH/wp3/c05/`(이 세션 scratchpad; 경로에 계정명이 들어 있어 표기 생략, eval_common.txt와 같은 `wp3/` 아래).
- 금지 문자열 needle은 조각 결합으로 만들었고 이 보고서에는 리터럴을 쓰지 않았다(HF1 owner 데이터 루트, HF2 원본 호스트 루트, HF3 원본 호스트 IPv4, HF4 원본 호스트명, HF5 owner 계정명 = SECURITY_BASELINE §5.2 HOST_FACT; 정보용 X1 옛 연구 런타임 모듈명(DD-A25가 import 금지), X2 원본 호스트 A100 GPU UUID, X3 owner 데스크톱 프로젝트 폴더, X4 원본 체크아웃 폴더명(연구 프로젝트명과 같음), X5 빌드 호스트 단축 호스트명).

## 결론

총점 **75/100**(scope_tls 20 + ticket_expiry 20 + yaml_operator_resource 20 + path_error 15), Blocker 0, Critical 0, Major 2 → **FAIL**(90 초과 미달, 그리고 path_error의 필수 시험 "금지 문자열 0건"이 런타임 공개 응답에서 불통과).

- 실행으로 확인된 것: T3-30a/b/c·31·32·33·18·05 전부 통과, 보안 음성(SECURITY §4 1~6, 8, 9) 23 + 기존 108 통과, sandbox(T3-35 설계 이름 3개 포함) 14 통과, 게이트웨이 테스트 527 통과, 로그 리댁션·T8 응답 캐너리 19 통과, 공개·등록 ZIP의 HOST_FACT 0건(이름·내용·중첩 wheel·base64 변형까지; 프로젝트 스캐너도 PASS).
- 감점 원인: (D1, Major) 분리 구성에서 공개 `GET /api/v1/health`가 core 호스트의 절대 allow root를 그대로 내보낸다 — 프로젝트 T8이 FAIL로 기록했고 평가자가 재현했다(DD-A59 (a)로 수정 결정됨, 현재 트리 미수정). (D2, Major) ticket 위조·만료 시험이 유지되는 테스트 스위트에 없고 WP-2A scratch에만 있으며 그 API 부분은 fixture 변경으로 낡았다. 그 밖에 CA 검증·manifest mismatch 분기에 프로젝트 테스트가 없다(동작은 평가자 probe로 확인).

## 재실행 요약 (cwd=TARGET_ROOT, `runtime/venv/bin/python -B -m pytest -p no:cacheprovider -m "not gpu and not live and not legacy_records and not host_data" --timeout=300`)

| # | 명령 / 대상 | exit | 결과 |
|---|---|---|---|
| R1 | `tests/owner/runner_protocol` 전체 (시작 트리) | 0 | 44 passed, 133.6 s |
| R2 | `test_t3_auth.py` 단독 + `test_t3_commit.py::test_stale_attempt_of_same_runner_cannot_renew_touch_or_finish` + `test_t3_claim.py::test_lease_without_start_is_not_billed_after_sweep`, `-v` | 0 | 8 passed(T3-30a/b/c, 31, 32, 33, 18, 05). 단독 실행 flaky(REVIEW_LOG WP-3 Minor, TTL 0.5 s) 수정 유지 확인 |
| R3 | WP-2A scratch self-test(`docs/internal/evidence/WP2/2A/selftest_*.py.txt`를 그대로 scratch에 복사) | 1 | `test_ticket_issue_verify_unit` PASS(위조 MAC·다른 키·만료·다른 job/attempt/runner); 나머지 9건 FAIL = `client` fixture가 이제 게이트웨이라 `/core/v1/runner/*`가 405, 본문 한도 413 — 보안 회귀 아님, 증거가 낡음 |
| R4 | 평가자 probe `test_c05_ticket_probe.py`(runner_protocol fixture 재사용, 실제 core 앱) | 0 | garbage·MAC 1자 변조·다른 키·만료(exp=now−1)·다른 job·다른 runner·다른 attempt·버전 바꿈 8종 × {start, heartbeat} → 전부 403 `TICKET_INVALID`, 빈 ticket → 422 `VALIDATION_FAILED`(min_length), 응답에 ticket 미반향, DB 무변경, 정상 ticket은 이후 그대로 동작 |
| R5 | `tests/owner/qa/test_wp3_security_negatives.py` + `tests/owner/qa/test_security_negatives.py` | 0 | 131 passed(23 + 108), 1 deselected(host_data: 실제 env 값 grep) |
| R6 | `tests/owner/structure_sandbox` | 0 | 14 passed, 25.3 s |
| R7 | sandbox 기본값 probe(CoreSettings + 실제 child 1회) | 0 | AS 8 GiB, cpu 900 s, wall 300 s, max_input 1280, max_output 1 MiB, unshare true; 실제 격리 `unshare-rn+setsid+rlimits`; allowlist 64개 이름에 위험 이름 5종(check_requirements, torch_load, check_file, nn.Linear, TorchVision) 없음; worker env 14키, `MBNAS_*` 0 |
| R8 | 평가자 probe `test_c05_manifest_probe.py` | 0 | 실제 manifest → `verified`, operator 해시 1개 변조 → `mismatch`, 빈 해시 → `unknown`(register 응답·owner runners 행 모두) |
| R9 | `tests/public/server` 전체 | 0 | 527 passed, 1 skipped(mini run 부재), 25 deselected(host_data), 1 xfailed(`test_health_reports_archive_roots`, DD-A29) |
| R10 | `tests/owner/api/test_access_log_redaction.py` + `tests/public/contracts/test_log_redact.py` + `tests/owner/projection/test_t8_canary_scan.py` | 0 | 19 passed |
| R11 | 평가자 host-fact 스캐너(`$SCRATCH/wp3/c05/tools/hostfact_scan.py zip`, 멤버 이름 + 바이트, 중첩 zip/whl/tar/gz, HF base64 3정렬 변형, 대소문자 무시) | 0 | public 601 files·registration 611 files: HF1~HF5 이름 0·내용 0·base64 0. owner 1593 files: HF 6540건(owner 허용, 양성 대조: 스캐너 상수의 base64도 탐지) |
| R12 | 같은 스캐너 `tree` — `mbnas_contracts`, `mbnas_model_runtime`, `mbnas_runner`, `mbnas_server`, `mbnas_web/src`, `mbnas_web/dist`, `docs/public` | 0 | HF는 추적된 `__pycache__/*.pyc` 35개에서만(HF5, 컴파일 경로 co_filename). `.py`/json/ts/dist/docs public = 0 (D7) |
| R13 | `scripts/dev/canary_scan.py dist/{public,registration}/*.zip --min-files 100` | 0 / 0 | 602 / 612 files, hits 0, PASS |
| R14 | 평가자 probe `test_c05_scope_matrix_probe.py` | 0 | runner 토큰 → console·owner 403; server → runner·owner 403, console 200; admin → owner 200; admin → runner 라우트는 scope 통과(409 RUNNER_NOT_REGISTERED, `auth.py:9,36,186-187`에 문서화된 설계); 토큰 없음 → 401 ×3 |
| R15 | 평가자 probe `test_c05_error_shape_probe.py`(게이트웨이 경유 오류 11종) | 1 | 10/11이 code·message·request_id(= `X-Request-ID`)·location을 가짐; **413 PAYLOAD_TOO_LARGE만 location 없음**(D3). 어떤 본문에도 호스트 경로·traceback 없음 |
| R16 | runner child env(`test_child_env_forces_transfer_none_and_drops_upload_token`), CLI insecure URL, `tests/public/contracts/test_runner_protocol.py` | 0 | 105 passed |
| R17 | 평가자 probe `test_c05_health_allowlist_probe.py`(게이트웨이 roots 비움 = public 설치 기본) | 1 | **core 호스트 allow root 2/2가 공개 health 본문에 나옴**(D1 재현) |
| R18 | 평가자 TLS probe(`tools/tls_probe.py`, 로컬 HTTPS + 사설 테스트 CA, scratch 인증서) | 0 | runner: CA 없음·다른 CA → `CERTIFICATE_VERIFY_FAILED`, ca_file → 200. gateway: 같음; `core_tls_verify=false` → 신뢰 안 되는 인증서도 200(D5) |
| R19 | R2 재실행(종료 트리) | 0 | 8 passed |
| R20 | 평가자 probe 3종 재실행(종료 트리: ticket, manifest, scope) | 0 | 3 passed |
| R21 | `grep -E "shell=True|os.system(|os.popen(|subprocess.*(\"…"` 패키지 5종 + scripts | 0 | 0건; subprocess 호출 5곳 모두 argv 리스트(`daemon.py:501`, `owner_worker/main.py:292`, `structure/sandbox.py:124,173`, `gpu_guard.py:150`); `start.sh`의 `bash -c`는 위치 인자 방식 |

프로젝트 증거(직접 열람): `docs/internal/evidence/WP4/4C/owner_gate_run4.{meta,txt}` exit=0 1510 passed/8 skipped/47 deselected(03:12–03:28 KST, 위 C05 테스트 전부 포함); `…/pytest_public_run4.{meta,txt}` exit=0 1070 passed/1 skipped/1 xfailed; `docs/internal/evidence/WP3/3A/sandbox_rlimit_as.txt`(모델별 AS 측정과 8 GiB 결정 행); `docs/internal/evidence/WP2/2A/selftest.txt`(10 passed exit=0, 10-26 트리); `docs/internal/evidence/WP7/7V/canary_scan_{public,registration,owner}.txt`(PASS); `docs/internal/evidence/T8/README.md`(Verdict FAIL).

## 기준별 채점

### 1. scope_tls — **20/25** (앵커 20: 사소한 누락 외 충족)

필수 시험 "T3-30/32; gateway→core 토큰으로 owner 라우트 403"은 모두 실행 증거가 있고 재실행으로 통과했다.
- scope별 토큰: `tests/owner/runner_protocol/test_t3_auth.py:27-40`(T3-30a 다른 runner 이름 → 403 RUNNER_MISMATCH, DB 무변경), `:72-91`(T3-30c server→runner 403, runner/server→owner 403, admin만 owner, principals에 token_hash 없음), `tests/owner/qa/test_wp3_security_negatives.py:195-201`(SECURITY §4-8 server 토큰 → owner 403), `:204-207`(사용자 bearer는 core 자격 아님), `tests/public/server/test_wp3_gateway_relay.py:62-72`(게이트웨이 서비스 토큰은 사용자 자격 아님, 인증 전 중계 0), `:75-85`(사용자 토큰을 서비스 토큰으로 바꿔 중계). 구현 `mbnas_core/src/mbnas_core/runner_api/auth.py:36`(SCOPE_ACCEPTS). R1·R2·R5·R9·R14.
- 비-loopback TLS 강제: runner `test_t3_auth.py:112-129`(T3-32, CLI exit 2 + `CORE_URL_INSECURE`, loopback·https 허용), gateway `tests/public/server/test_wp3b_gateway_fixes.py:254-268`(규칙 6행), `:271-288`(CLI exit 2, doctor가 토큰을 평문으로 보내지 않음, userinfo 미노출). 구현 `mbnas_runner/src/mbnas_runner/client.py:88-96`, `mbnas_server/src/mbnas_server/config.py:243-256`, `mbnas_server/src/mbnas_server/api/core_client.py:86-88`(DD-A47 (b)). R2·R9·R16.
- CA 검증: 프로젝트 근거는 정적 코드뿐이다(`client.py:124` `verify = ca_file or True # never False`, `core_client.py:48-53`). 프로젝트 테스트·증거 파일 없음, `docs/public/SECURITY_MODEL.md:42,107`이 "호스트 사이 실제 TLS 경로는 아직 검증하지 않았다"고 정직하게 표기. 평가자 probe R18에서 동작은 확인됐다(사설 CA 없음/다른 CA 거부, ca_file 수용).
- 감점 사유: CA 검증의 프로젝트 실행 증거 부재(D4), 게이트웨이 `core_tls_verify=false` 스위치가 경고 없이 검증을 끈다(D5). 참고: admin 토큰은 runner 라우트에서 아무 runner로 행동할 수 있다(설계상 문서화, 기본 TTL 24 h) — 결함 아님.

### 2. ticket_expiry — **20/25** (앵커 20)

필수 시험 "T3-30/31 + ticket 위조/만료 테스트".
- 토큰 만료·폐기·회전 grace: `test_t3_auth.py:43-69`(T3-30b 만료/폐기/미지 토큰 모두 401 AUTH_REQUIRED, 쓰기 없음), `:94-109`(T3-31 grace 안 둘 다 200, 이후 구 토큰 401). R1·R2·R19 통과.
- job ticket 바인딩: 구현 `mbnas_core/src/mbnas_core/runner_api/tickets.py:26-59`(HMAC `job|attempt|stage|runner|exp`, 상수시간 비교, 어떤 불일치든 같은 403), `routes.py:135-138`(모든 job 쓰기에서 검증), `:239-241`(claim 시 발급, stage = rung). 유지되는 스위트에는 attempt 교차만 있다: `tests/owner/runner_protocol/test_t3_commit.py:191-226`(T3-18, `:215-216` attempt-1 ticket → 403 TICKET_INVALID), `test_t3_claim.py:188-199`(T3-05 회수 후 낡은 lease → 409).
- 위조·만료·다른 job/runner: 프로젝트 증거는 WP-2A scratch self-test(`docs/internal/evidence/WP2/2A/selftest_test_wp2a.py.txt:117-133` 단위, `:136-142` API 위조 `v1.x.y`; `selftest.txt` 10 passed exit=0, 10-26 트리)뿐이다. R3에서 단위 시험은 현 트리에서도 통과했지만 API 부분은 fixture 변경으로 그대로는 못 돈다. 평가자 probe R4가 현 트리의 API 동작(8종 × 2 라우트 403, DB 무변경)을 확인했다.
- 감점 사유: 필수 시험인 ticket 위조/만료 시험이 게이트 스위트에 없고 scratch 증거가 일부 낡았다(D2). 참고: ticket TTL 기본 7일(`mbnas_core/config.py:121`) — 실효 만료는 attempt fencing이 담당, 결함 아님.

### 3. yaml_operator_resource — **20/25** (앵커 20)

필수 시험 "음성 테스트(SECURITY §4 1~6) 통과; sandbox 테스트 T3-35" — 둘 다 실행 증거가 있고 재실행 통과(R5, R6).
- §4-1 allowlist 밖 이름 → 빌드 전 FAIL, 부작용·네트워크 없음: `test_wp3_security_negatives.py:28-37`(5종 BLOCK_UNSUPPORTED), `:161-175`(API 경유 4종: 빌드 spy 미발동, 소켓 시도 0, 출력 루트 무변경), `:178-184`(음성 대조: 허용 YAML에선 spy 발동). §4-2 `:187-191`(input_size 1281 → 422). §4-3 `:60-72`(alias 64 통과/65 거부). §4-4 `:75-89`(행 256/257, 채널 4096/4097), 크기 `:92-96`(1 MiB 초과), `nn.Linear` 거대 차원 `:40-44`.
- §4-5·6 / T3-35: `tests/owner/structure_sandbox/test_sandbox.py:43-52`(CPU RLIMIT 초과 → RESOURCE_LIMIT, API RSS 증가 < 64 MiB), `:55-60`(AS), `:63-70`(wall timeout, 프로세스 그룹 종료), `:73-78`(출력 상한), `:81-105`(동시 3건 직렬화), `:108-116`(대기 초과 Busy), `:126-138`(부모 사망 시 종료); 설계 이름 3개 `tests/owner/structure_sandbox/test_sandbox_api.py:67-73`(timeout → UNVERIFIED/STRUCTURE_CHECK_TIMEOUT), `:76-82`(출력 1 MiB 기본·초과 거부), `:85-101`(실제 child env에 MBNAS_* 없음, GPU 비노출); `:104-117`(API 프로세스에서 빌드 없음).
- SAFE_LOAD env: `test_sandbox.py:33-40`(ULTRALYTICS_SAFE_LOAD=1, YOLO_AUTOINSTALL=false, YOLO_OFFLINE=1, CUDA_VISIBLE_DEVICES=""). 한도 정의 `mbnas_model_runtime/src/mbnas_model_runtime/genotype.py:151-171`. AS 8 GiB 근거 `docs/internal/evidence/WP3/3A/sandbox_rlimit_as.txt` 마지막 결정 행(DD-A44 (c)), R7에서 기본값 일치 확인.
- operator manifest는 주장: core가 runner 보고를 `verified|mismatch|unknown`으로 라벨링(`mbnas_core/src/mbnas_core/runner_api/routes.py:148-158`), 계약 enum 테스트(`tests/owner/projection/test_contract_150.py:63`), T7 실GPU 증거에 `verified` 라벨(`docs/internal/evidence/T7/steps*.jsonl`). mismatch/unknown 분기의 프로젝트 테스트는 없다 — 평가자 probe R8로 동작 확인.
- 감점 사유: manifest mismatch 분기 증거 누락 1건(D6). 이것만 채우면 25점 대상이다.

### 4. path_error — **15/25** (앵커 15: 핵심 절반만 증거)

필수 시험 "tests/public/server 오류 형식 테스트; log redaction 테스트; 금지 문자열 0건".
- 오류 형식: `tests/public/server/test_wp3_gateway_relay.py:87-95`(core 오류를 같은 code + location core + request_id로 중계), `:151-158`(429 RATE_LIMITED, location gateway, 거부는 core 미도달), `:190-206`(core 다운 → 503 CORE_UNAVAILABLE location core, 로컬 대체 없음), `test_wp3b_gateway_fixes.py:68-77`(dot segment → 게이트웨이 404 JSON, SPA 흡수 없음), `:177-209`(DD-A44 (d) 오류 코드 규칙, 호스트 경로 미반향), `:244-247`(UPLOAD_TYPE_UNSUPPORTED), `tests/owner/qa/test_security_negatives.py:269-279`(오류 본문에 경로·비밀·traceback 없음). R5·R9 통과. R15: 11종 중 10종이 code/location/request_id 완비, **413은 location 없음**(D3).
- 로그 리댁션·owner 로그 토큰 없음: `tests/owner/api/test_access_log_redaction.py:30`, `tests/public/contracts/test_log_redact.py:23-65`, `tests/public/server/test_gateway_skeleton.py:284`, `test_wp3_gateway_relay.py:181-186`(core 토큰 미노출), `:310-318`(감사 로그에 토큰 대신 해시), `test_security_negatives.py:282-311`(export·caplog·출력 루트에 비밀 없음). R5·R9·R10 통과. runner child env 비밀 제거 R16 통과.
- 고객 경로 비전달: `test_wp3_gateway_relay.py:295-307`(upload_id → 텍스트+sha로 변환, core는 게이트웨이 경로를 받지 않음), `:169-178`(realpath를 요청 경로로 치환), `tests/public/server/test_artifact_routes.py:572-584`(브라우저가 넣은 path_checks 등 제거, DD-A51 (e)). R9 통과.
- argv-only: 정적 근거만(R21, 테스트 없음 — D9).
- 금지 문자열 0건: 배포 ZIP은 충족(R11, R13, 프로젝트 `WP7/7V/canary_scan_*.txt` PASS). 그러나 **런타임 공개 응답은 불충족**: `docs/internal/evidence/T8/README.md:3-6, :52` Verdict FAIL — 분리 구성의 `GET /api/v1/health`가 core 호스트의 절대 `allowlists.dataset_roots/output_roots`(owner 계정명 포함 경로)를 중계. 원인 `mbnas_server/src/mbnas_server/api/relay.py:217-220`(게이트웨이 자체 roots가 있을 때만 교체). R17에서 재현(2/2). DD-A19와 계약 1.5.0 "Health.allowlists는 고객 호스트 루트만" 위반(D1). 공개 패키지 소스 트리의 HF는 추적된 `.pyc`에만 있다(D7).
- 감점 사유: 필수 시험 하나(런타임 금지 문자열)가 증거와 상충하고 수정 전이다(D1). 나머지 절반(오류 형식·리댁션·ZIP 스캔)은 실행 증거로 충족. D1 수정 + T8 §2 재실행 + D3 반영 시 재채점 대상.

## 결함

| # | 심각도 | 소유 패키지/파일 | 내용 | 구체적 수정 |
|---|---|---|---|---|
| D1 | **Major** (DD-A59 (a)와 같은 판정; 문서가 REQUIRED로 표시한 게이트웨이 roots를 설정하면 회피 가능) | mbnas_server `src/mbnas_server/api/relay.py:217-220` | 게이트웨이 dataset/output roots가 비면(= public 설치 기본, `config/examples/gateway.env.example:71,78` 주석 처리) 공개 `/api/v1/health`가 core 호스트 절대 allow root를 중계. T8 FAIL, R17 재현 | health는 항상 게이트웨이 자기 roots(빈 목록 가능)와 archive_roots만 반환하고 core `allowlists`는 버린다. 분리 구성 테스트(`harness.combined_app(..., gateway_overrides={"dataset_roots": [], "output_roots": []})`에서 core roots 문자열 0건) 추가, xfail `test_health_reports_archive_roots` 정리, T8 §2 재실행 |
| D2 | **Major** | tests `tests/owner/runner_protocol/`(신설), 증거 `docs/internal/evidence/WP2/2A/` | ticket 위조·만료·다른 job/runner 시험이 게이트 스위트에 없음(유지 스위트는 attempt 교차 1건). scratch self-test는 9/10이 fixture 변경으로 못 돎 | `test_t3_tickets.py` 신설: 위조·MAC 변조·다른 키·만료·다른 job/runner/attempt → 403 TICKET_INVALID + DB 무변경(평가자 `$SCRATCH/wp3/c05/probe_ticket/test_c05_ticket_probe.py` 이식 가능) + `tickets.verify` 단위 시험(`selftest_test_wp2a.py.txt:117-133` 이식) |
| D3 | Minor | mbnas_contracts `src/mbnas_contracts/asgi.py:65-72` | BodyLimitMiddleware의 413 PAYLOAD_TOO_LARGE 본문에 `location` 없음(게이트웨이·core 공통) | 생성자에 `location` 인자 추가(gateway → "gateway", core → "core"), `test_body_limit_413_before_relay`에 location 단언 추가 |
| D4 | Minor | tests; `mbnas_runner/src/mbnas_runner/client.py:124`, `mbnas_server/src/mbnas_server/api/core_client.py:48-53` | CA 검증의 프로젝트 실행 증거 없음(정적 코드뿐) | 로컬 HTTPS + 사설 CA 테스트(평가자 `tools/tls_probe.py` 방식): CA 없음/다른 CA → 실패, ca_file → 성공을 runner·gateway 각각 고정. 호스트 간 실 TLS는 T5/T6에서 별도 |
| D5 | Minor | mbnas_server `src/mbnas_server/config.py:141`, `api/core_client.py:49-50`, `doctor.py:55-62` | `core_tls_verify=false`가 경고 없이 인증서 검증을 끔(R18: 신뢰 안 되는 인증서 200). runner는 "never off" 정책 — 비대칭 | doctor에 warning 항목 + 기동 로그 경고, 또는 스위치 제거(runner와 같게) |
| D6 | Minor | tests; `mbnas_core/src/mbnas_core/runner_api/routes.py:148-158` | operator manifest mismatch/unknown 라벨 분기 테스트 없음 | register에 변조·빈 `operator_files_sha256`을 보내 `runtime_verification`이 mismatch/unknown인지, owner runners 행에도 같은지 단언(평가자 `test_c05_manifest_probe.py` 이식 가능) |
| D7 | Minor | 저장소 위생: `mbnas_contracts`(17)·`mbnas_model_runtime`(14)·`mbnas_runner`(2)·`mbnas_server`(2)의 추적된 `__pycache__/*.pyc` | 컴파일 경로에 owner 계정명(HF5) 포함. ZIP에는 없음(R11 0건), mbnas_application git 저장소에는 추적됨 → 이관(NEXT_TASK) 시 누출 위험. 기존 DD-A52 (d)/DD-A29 항목 | `git rm --cached` + `.gitignore`의 `__pycache__/`, 이관 복사에서 `*.pyc` 제외, 이관 후 HF 스캔 |
| D8 | Minor | tests `tests/_shared/conftest_common.py:58`(public·registration ZIP에 동봉) | 원본 호스트 A100 GPU UUID 리터럴(하드웨어 식별자; 공식 HOST_FACT 5종 밖이라 스캐너가 못 잡음) | 합성 UUID로 교체, check:bundle/canary extra needle에 추가 |
| D9 | Minor | tests `tests/public/infra/`(신설) | argv-only 불변식의 자동 시험 없음(정적 grep만) | 패키지 소스에서 `shell=True`·`os.system`·문자열 명령을 금지하는 정적 테스트 |
| D10 | Minor | mbnas_runner daemon 진입점, mbnas_core owner_worker | 공용 `install_log_redaction()`이 core·gateway 앱에만 설치됨(runner·owner worker 로그는 비밀을 안 찍는 관행에 의존; T8 runner.log 비밀 0건) | runner `run`·owner worker main에서 `install_log_redaction()` 호출 |

Blocker 0, Critical 0, Major 2, Minor 8.

## 합계와 판정

| 기준 | 점수 | 앵커 |
|---|---|---|
| scope_tls | 20 | 사소한 누락(CA 검증 실행 증거) 외 충족 |
| ticket_expiry | 20 | 사소한 누락(위조·만료 시험이 게이트 밖) 외 충족 |
| yaml_operator_resource | 20 | 사소한 누락(manifest mismatch 분기) 외 충족 |
| path_error | 15 | 핵심 절반(런타임 금지 문자열 상충) |
| **합계** | **75/100** | **FAIL**: 90 초과 미달, 필수 시험(런타임 금지 문자열 0건) 불통과. Blocker/Critical 0 |

재채점 경로: D1 수정 + T8 §2 재실행(path_error → 20~25), D2·D4·D6 테스트 추가(각 기준 → 25), D3 반영. 모두 처리되면 100 대상.

## 확인 못 함

- 호스트 사이 실제 TLS 경로(원격 runner↔core, gateway↔core, nginx 종단): 평가자 probe는 loopback HTTPS + 사설 CA만 확인했다. 프로젝트도 "검증 예정"으로 표기.
- host_data 시험 `test_real_env_secrets_are_absent_from_logs_evidence_sources_and_bundles`: 실제 env 파일이 필요해 실행하지 않았다(비밀 값을 읽지 않기 위해서이기도 함).
- T8 런타임 캡처(`T6/t6_t8_driver.py`) 전체 재실행은 하지 않았다. D1은 in-process 분리 구성으로 재현했고, 실제 프로세스 재현은 T8 README 근거다.
- 실제 배포의 owner core 로그 파일·SSE 스트림의 토큰 부재: T8도 SSE를 캡처하지 않았고 owner 측 로그는 범위 밖이었다.
- 사용자 네임스페이스가 없는 호스트의 sandbox 폴백(`setsid+rlimits`): 이 호스트는 `unshare-rn`이 동작해서 폴백 경로는 실행되지 않았다(테스트는 두 값을 모두 허용).
- WP-2A scratch self-test의 API 부분 9건: 현재 fixture로는 그대로 실행되지 않아, 평가자 probe R4로 대신 확인했다.
- R1(runner_protocol 44건)은 시작 트리에서 돌았다. 종료 트리에서는 auth·ticket 부분(R19, R20)만 다시 돌렸다. 바뀐 두 파일은 runner_api와 관련이 없다.

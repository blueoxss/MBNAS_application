# C02 위협·비밀 — 독립 재평가 EVAL_2

- set: **C02** (`docs/internal/criteria/C02.json` v1.0), 재채점 2/3(`scratchpad/wp3/rescore2.txt` 절차). 이전 보고: `EVAL_1.md`(85 FAIL, D1–D7).
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자 — 채점만 하며 결정은 설계자
- time: 2026-10-28T10:16+09:00 시작, 10:4x+09:00 보고 작성
- tree hash(같은 도구 `scripts/dev/source_tree_hash.py`, mbnas-source-tree/2, 221 files; `| tail -3`은 닫는 괄호만 나오므로 `sha256` 필드 인용): 시작(10:16)·중간(10:30)·종료(10:32) 모두 `sha256:f00987be7994203fc16f52140bd98b3f89e1b548b6a346255066727a36dcc40c`. 모든 시험의 `.meta`에 시작·종료 트리 해시를 기록했고 전부 같다. PFINAL2(`docs/internal/evidence/WP7/PFINAL2/01_tree_hash.json`)도 같은 트리를 빌드 중이다.
- **dist/는 평가 시점에 낡았다**: `dist/*/*.zip`은 여전히 07:0x 빌드(public `ce34b821…`, registration `95b541e9…`, owner `79cce02a…`, 트리 5a7bc592… 기준)이고 `docs/internal/evidence/WP7/PFINAL2/SUMMARY.md`는 10:32에도 없었다(게이트 실행 중). 그래서 ZIP에 의존하는 점검(배포본 스캔, 멤버 grep, 실린 문서)은 평가자가 현재 트리를 scratch로 복사해 직접 빌드한 ZIP으로 했다(E12–E14). PFINAL2 결과(재빌드 ZIP, T6 rerun2, T8 §2)가 나오면 짧은 보충(addendum)이 필요하다.
- 환경: CPU 전용(`CUDA_VISIBLE_DEVICES=""`), `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `MBNAS_TEST_ROOT`·`MBNAS_HOME`·`--basetemp`는 scratch. GPU·/data·etri_nas·build/source_snapshot 접근 없음. 프로젝트에 쓴 파일은 이 보고서 하나다. docker는 `nginx:1.26-alpine`(로컬 이미지, `--network none`)만 썼다.
- 표기: `$S` = 이 세션 scratchpad의 `wp3/c02/`(계정명이 들어 있어 생략). 이번 산출물은 `$S/e2/out/`, 도구 `$S/e2/tools/`, probe `$S/e2/probe_prune/`, 실기동 `$S/e2/live2/`(비밀 0600), scratch 빌드 `$S/e2/pkgtree/`. 비밀 값·canary 토큰 값은 출력하거나 여기 쓰지 않았다.

## 결론

총점 **100/100**(customer_root 25 + core_boundary 25 + result_limits 25 + admin_subject 25), Blocker 0, Critical 0, Major 0, Minor 3(D3 열림·문서화, D6 열림, D8 신규) → **PASS**(90 초과, Blocker/Critical 0).
단서: ZIP 의존 점검은 평가자 scratch 빌드 기준이고 프로젝트의 현재 트리 T8 §2(재빌드 ZIP)는 PFINAL2에서 진행 중이다 — 그 결과로 짧은 보충이 필요하다(현재 트리에서 평가자가 직접 실기동해 확인한 E15는 0건).

EVAL_1 결함 판정: D1 **FIXED**, D2 **FIXED**, D3 **OPEN**(1.5.1 항목으로 정직하게 문서화; 평가자가 현재 트리에서 자식 프로세스 env를 캡처해 0건 확인), D4 **FIXED**, D5 **FIXED**, D6 **OPEN**, D7 **FIXED**(문서 + proxy 예시; core 코드 강제는 없음 — EVAL_1 수정안 (b)). Fix 5(runner 프로토콜 1.1.0 prune)는 C02 관점에서 문제 없음(E7, E17).

## EVAL_1 결함 상태

| ID | 판정 | 근거 |
|---|---|---|
| D1 RunnerSettings db_path 부재 시험 | FIXED | `tests/public/runner/test_runner_settings_no_db.py`(7개): `:78-90` 두 클래스에 `db_path`·`ticket_key`·core 자격 필드 없음 + 이름 토큰 검사, `:93-98` 비밀 필드가 정확히 자기 것(Runner: token/token_file/gateway_upload_token_file, Gateway: api_token/core_token/artifact_signing_key), `:101-111` env 접두가 자기 것이고 `MBNAS_CORE*` alias 없음, `:128-154` `MBNAS_CORE_*`·오명명 `MBNAS_RUNNER_DB_PATH` 등을 env·env 파일에 심어도 어떤 필드에도 안 들어감. 공개 패키지만 import. E2 7 passed; scratch 빌드 public ZIP에 트리와 같은 바이트로 실림(E14) |
| D2 runner 경로 응답 canary 시험 | FIXED | `tests/owner/projection/test_runner_route_canary.py`(2개, 평가자 probe 이식 + 강화: `:62-64` 필수 경로 8개를 실제로 거쳤는지 단언, `:187` PROPOSE_GENERATION 명시 요청 400 `KIND_NOT_ALLOWED` 단언). E2 2 passed. 평가자 음성 대조 E9: `filter_params`를 항등으로 바꾸면 첫 시험이 marker 18건으로 FAIL — 시험이 누출을 잡는다 |
| D3 runner 작업 자식 프로세스 env 미캡처 | OPEN (문서화) | 프로젝트는 캡처를 추가하지 않고 한계로 적었다: `docs/public/SECURITY_MODEL.md:34`(자식 환경은 이 검사에서 캡처하지 않았다, 근거는 정적 제거 목록과 업로드 토큰 단위 시험뿐, 다음 판에 추가), `:109`(캡처하지 않은 것), `docs/internal/VALIDATION_REPORT.md:23`(1.5.1 항목). 평가자가 현재 트리에서 학습 job의 자식을 캡처해 core 비밀·owner 문자열·runner 자신의 토큰 0건을 확인(E15) — 제품은 문제없고 프로젝트 증거 공백만 남음 |
| D4 "재실행/검증 예정" 문구 | FIXED | 트리의 `docs/public`, `docs/registration`, `README.md`에 해당 문구 0건(E18); scratch 빌드 public·registration ZIP 안의 모든 `.md`에서도 0건(E14). 새 문구는 결과를 PASS로 적고, 재빌드 뒤 재확인이 남았다는 사실은 `README.md:5`, `docs/registration/PROGRAM_OVERVIEW.md:74`에 정직하게 남김 |
| D5 VALIDATION_REPORT ADMIN_BOUNDARY 행 | FIXED | `docs/internal/VALIDATION_REPORT.md:27` "BLOCKED_BY_ENV — 미충족" + 조건 4개(별도 관리 기계/VM, TLS proxy + 인증서 검증 + owner 경로 차단, 그 배치에서 T5 흐름 + T8 §2를 고객 root로, 결과 기록); T5 행(`:16`)에 "Same host, same operator uid, `--network host`: functional separation, not an administrative boundary" 단서 |
| D6 ACCEPTANCE_MATRIX 재판정 | OPEN | `docs/internal/ACCEPTANCE_MATRIX.md`는 03:29 이후 그대로(10:32 확인). ADMIN 행(`:11`)은 여전히 정직("미충족 … 동일 host 프로세스 분리는 기능 검증일 뿐")하지만 날짜·T5 근거가 없고 다른 행 다수가 VALIDATION_REPORT와 어긋남 |
| D7 owner API loopback 표기 | FIXED (문서 + 예시) | `SECURITY_MODEL.md:30`(admin scope만 검사, 주소 미검사, 원격 시 proxy가 `/core/v1/owner/` 차단), `:44`(proxy는 console·runner만 전달), `:110`(§9 행), `docs/public/OPERATIONS.md:90,94`, `docs/public/INSTALL.md:95`, `README.md:124`, `docs/registration/INTERFACES.md:132`, `docs/registration/SYSTEM_STRUCTURE.md:122`("loopback 전용" → "원격 배치 시 프록시에서 차단"), `deploy/public/nginx.example.conf:62-126`(owner 호스트용 주석 블록: `location ^~ /core/v1/owner/ { return 403; }`, console·events·runner만 proxy, 나머지 404). 평가자 재시험 E16: `nginx -t` exit 0, 원시 요청 25개 중 owner 변형 15개 403·그 밖 404·전달 경로 502(upstream 없음). core 쪽 주소 검사는 추가되지 않음(EVAL_1 수정안 (b) 채택; 코드 강제는 여전히 없음) |

## 재실행 요약 (현재 트리 `f00987be…`; pytest = `runtime/venv/bin/python -B -m pytest -p no:cacheprovider -m "not gpu and not live and not legacy_records and not host_data" -rA --timeout=300`)

| # | 명령 / 대상 | exit | 결과 (산출물 `$S/e2/out/`) |
|---|---|---|---|
| E1 | `python -B scripts/dev/check_import_closure.py` | 0 | ok: violations 0, unresolved 0, forbidden 0, allowlisted 0, 예외 1(`adapters/base.py`, DD-A26/A58) (`closure.{txt,json}`) |
| E2 | pytest `tests/public/runner/test_runner_settings_no_db.py tests/owner/projection/test_runner_route_canary.py` | 0 | 9 passed(7 + 2) (`d1_d2.*`) |
| E3 | pytest `tests/public/infra` | 0 | 65 passed (`infra.*`) |
| E4 | pytest `tests/owner/projection` | 0 | 261 passed(259 + D2 2) (`projection.*`) |
| E5 | pytest `tests/owner/runner_protocol/test_t3_auth.py test_t3_tickets.py` | 0 | 10 passed — T3-30a/b/c·31·32·33 + ticket 4 (`t3auth.*`) |
| E6 | pytest `tests/public/runner` | 0 | 141 passed, 9 deselected — D1 7, prune 실행 제한(`test_prune_control.py`) 포함 (`public_runner.*`) |
| E7 | pytest `tests/owner/runner_protocol` | 0 | 82 passed — T3-34 prune 왕복, DD-A64 포함 (`runner_protocol.*`) |
| E8 | pytest health 분리 2, archive roots 1, DD-A51 (e) 2, `tests/owner/unit/test_settings_split.py`, `tests/owner/qa/test_wp3_security_negatives.py`, `tests/public/ops/test_ops_install.py` | 0 | 51 passed (`boundary.*`) |
| E9 | D2 음성 대조: `PYTHONPATH=$S/e2/negctl pytest -p c02_negctl_plugin tests/owner/projection/test_runner_route_canary.py`(플러그인이 `mbnas_core.runner_api.envelope.filter_params`를 항등으로 교체; 프로젝트 파일 무변경) | 1 (기대) | `test_runner_route_bodies_hold_no_owner_only_data` FAIL(marker_hits 18), 정책 job 시험 PASS (`d2_negative_control.txt`) |
| E10 | WP-4C 음성 대조 `MBNAS_TEST_WP4C_MUTATION=sse,export` pytest `tests/owner/projection` | 1 (기대) | 21 failed / 240 passed — EVAL_1 R12와 같은 21개 실패 + 새 2개는 통과(해당 변이와 무관) (`probe_db_and_negctl.txt`) |
| E11 | `$S/tools/principal_storage_probe.py`(scratch DB, 스키마 v6) | 0 | server·runner·admin 토큰과 비밀 부분이 DB 바이트에 0회, sha256 존재, 행에 `token_hash` 없음, 다른 runner id → `RUNNER_MISMATCH` (`probe_db_and_negctl.txt`) |
| E12 | scratch 빌드: 현재 트리를 `$S/e2/pkgtree/mbnas_application`로 복사(runtime·dist·build·.git·node_modules·`__pycache__` 제외, `build/allowlists` 포함; 복사본 트리 해시 = `f00987be…`) → `runtime/venv/bin/python -I -B scripts/dev/package_build.py --root <복사본> --profile {public,registration}` | 0 / 0 | 0 violation·0 warning, `private_package` PASS(코어 경로·import·문자열 참조·요구사항·엔트리포인트·wheel 이름 0). public 476 staged·`a5c2d632…`, registration 486·`948387b3…`(비재현 빌드; PFINAL2의 `--reproducible` sha와는 다를 수 있음) (`build_{public,registration}.txt`) |
| E13 | `canary_scan.py --manifest docs/internal/canary/CANARY_MANIFEST.json --profile {public,registration} --min-files 100 <E12 ZIP>` | 0 / 0 | public 624 files·registration 634 files, hits 0 (`canary_scan_scratch_zips.txt`) |
| E14 | E12 ZIP 멤버(바깥 + 중첩 wheel) grep + 실린 문서 비교 | 0 | public 623 항목(476 + 147), registration 633(486 + 147): `mbnas_core`·`owner_worker`·`tests/owner`·`docs/internal`·`config/owner`·`core.env`·`all-in-one`·`deploy/owner`·canary·`.sqlite`·`etri_nas` 등 0건. `SECURITY_MODEL.md`(`234e9724…`)·FEATURES·INSTALL·OPERATIONS·README·`nginx.example.conf`·D1 시험 = 트리와 동일. 모든 `.md`에서 "재실행 예정/검증 예정/수행 예정/loopback 전용" 0건. registration 문서 8개 중 7개가 트리와 동일, `FILE_LIST.md`는 빌드 때 매니페스트로 생성(예상된 차이) (`grep_members_scratch.txt`, `doc_identity_scratch.txt`, `zip_doc_phrases.txt`) |
| E15 | **현재 트리 실기동 재캡처**(`$S/e2/tools/live2_capture.py`): dev venv(트리의 editable 설치)로 core + owner worker(scratch MBNAS_HOME·DB, canary 형태 OWNER_SECRET, `DEVICE_SOURCE=runners`) → principal 2개 → 현재 `scripts/start.sh`·`stop.sh` 사본의 scratch ROOT에서 `start.sh --profile public`(cwd `/`, **호출자 env에 PYTHONPATH·PYTHONSTARTUP·PYTHONUSERBASE·OLDPWD를 일부러 넣음**) → T5 harness 사본으로 health·structure·waitdev·**CPU 학습(2 epoch)** → gateway·runner·**runner 작업 자식** `/proc` 캡처 → stop | 0 (start/stop) | gateway·runner environ 키 10개(CUDA_VISIBLE_DEVICES, HOME, LANG, MBNAS_HOME, PATH, PWD, PYTHONDONTWRITEBYTECODE, PYTHONNOUSERSITE, PYTHONUNBUFFERED, SHLVL) — 넣은 4개 변수와 OLDPWD 없음(round 4 `start.sh:344,356,360` 확인). 학습 PASS(`device_used=cpu`). 자식 environ 키 30개: `MBNAS_RUNNER_*` 비밀 아닌 설정 9개 + `MBNAS_HOME`·`MBNAS_STOP_REQUEST_FILE`·`MBNAS_WORKER_ID` + CUDA/스레드 변수 — `MBNAS_RUNNER_TOKEN`·core 키 없음. 스캔(needles = core.env·gateway.env·runner.env 비밀 값 + owner 문자열 6): (a) gateway·runner·자식 environ+cmdline·health 7 files **0 hits**, (b) 토큰 형태 0, (c) harness가 받은 gateway 응답 0, (d) 양성 대조(ticket key를 env에 넣은 프로세스) 3 hits, (e) env 파일 자체 24 hits. health `allowlists` = gateway 자신의 루트. stop 0, 남은 프로세스 0 (`live2_capture.txt`, `live2_scan.txt`, `live2_scan_[a-e].json`) |
| E16 | `deploy/public/nginx.example.conf`의 owner 호스트 블록을 scratch에서 주석 해제: (1) ssl + 임시 인증서로 `docker run --rm --network none … nginx:1.26-alpine nginx -t`, (2) 같은 블록을 평문 :8443으로 띄워 컨테이너 안 `nc`로 원시 요청 25개 | 0 | `nginx -t` syntax ok. 403: `/core/v1/owner/{runs,,principals,runs/run-1/candidates/c/audit}`, `/core/v1/console/../owner/runs`, `%2e%2e`·`%2E%2E`·`..%2F`·`x/..%2F..%2F` 형태, `/core/v1/runner/../owner/runs`, `//core/v1/owner/runs`, `/core//v1/owner/runs`, `/core/v1/./owner/runs`, `/core/v1/%6Fwner/runs`, `/core/v1/owner%2Fruns`, 절대형식 `http://…/core/v1/owner/runs`. 404: `/core/v1/owner;x/runs`, `/CORE/V1/OWNER/runs`, `/core/v1/owner`, `/core/v1/ownerx`, `/api/v1/runs`, `/`. 502(전달됨, upstream 없음): console, console events, runner claim (`nginx_t.txt`, `nginx_probe.txt`) |
| E17 | **평가자 probe** `$S/e2/probe_prune/test_c02_prune_channel_probe.py`(프로젝트 fixture 재사용) | 0 | 2 passed. (1) 1.1.0 prune 채널: job params·후보 lineage/audit·`prune_requests.planned_by`에 표식을 심고 T3-34식 왕복 — heartbeat가 준 prune order 2개는 정확히 8키(bytes, checkpoint_id, policy, reason, relpath, request_id, run_id, sha256), E11 200×2, heartbeat·E11 포함 runner 응답 13개에 **표식 0·owner 전용 멤버 이름 0**; 양성 대조: admin owner 경로에 표식, DB에 planned_by 표식 2행. (2) runner 호스트 절대경로: core DB 덤프에 runner work root 13회(양성), 공개 candidate detail·snapshot·export(zip 멤버 포함) 0회 (`prune_channel_probe.txt`, `prune_channel_probe2.txt`) |
| E18 | `grep -rn "재실행 예정\|검증 예정\|수행 예정\|재실행이 남은" docs/public docs/registration README.md`; `grep -rn "loopback 전용\|/core/v1/owner" …`; `grep -n "ADMIN\|BLOCKED_BY_ENV" docs/internal/VALIDATION_REPORT.md` | 1 / 0 / 0 | 낡은 문구 0건(grep exit 1). owner 경로 문구는 D7 표의 위치들. ADMIN_BOUNDARY 행 `:27`(10:28 갱신 뒤 재확인) |

## 기준별 채점

### 1. customer_root — **25/25** (앵커 25)

필수 점검 두 개 모두 프로젝트 실행 증거가 있고 평가자가 현재 트리에서 다시 실행했다.
- RunnerSettings에 db_path 없음(EVAL_1에서 정적 근거뿐이던 항목): `tests/public/runner/test_runner_settings_no_db.py`(D1 표), E2 통과, 공개 번들에 실림(E14).
- runner/gateway `/proc` environ·cmdline의 OWNER_SECRET/DB_CRED 0건: 프로젝트 증거 `docs/internal/evidence/WP7/7V_FINAL/T8/README.md`(직전 트리 5a7bc592, 최종 ZIP 스택) + `T5/rerun/`(컨테이너 env·14 레이어 0). 현재 트리는 평가자 E15: round 4 `start.sh`가 호출자의 PYTHONPATH·PYTHONSTARTUP·PYTHONUSERBASE·OLDPWD를 실제로 지우고, gateway·runner·**runner 작업 자식**의 environ·cmdline에 core 비밀·DB 경로·owner 문자열·runner 자신의 토큰이 0건(양성 대조 검출).
- runner 토큰으로 닿는 범위: claim envelope 등 8개 경로(D2 시험, E9로 민감도 확인)와 1.1.0 heartbeat prune·E11(E17)에 owner 전용 데이터 0, 정책 job 임대 불가, owner 경로 403(T3-30c).
- 코드: scratch 빌드 ZIP에 core 없음(E12 `private_package` PASS, E13, E14), closure 0(E1), 공개 패키지 import 시 core 미적재(E3).
- 남은 Minor(앵커 불변): D3(자식 env 캡처가 프로젝트 증거에 없음 — 문서화, 평가자 E15로 확인), D8(D2 시험이 1.1.0 heartbeat·E11 응답을 필수 경로로 묶지 않음 — 평가자 E17로 확인). 프로젝트의 현재 트리 T8 §2는 PFINAL2 대기(보충 필요).

### 2. core_boundary — **25/25** (앵커 25)

- 공개 패키지에서 `mbnas_core` import 불가: `tests/public/infra/test_import_closure.py`(E3 65 passed), closure 0(E1), scratch ZIP에 core 없음(E12–E14).
- T3-30/31: `tests/owner/runner_protocol/test_t3_auth.py:27-109`(E5 10 passed; E7 runner_protocol 82 passed, DD-A64 claim 변경 포함).
- principals hash 저장: `mbnas_core/src/mbnas_core/runner_api/auth.py:39-40,63-86,123-139`, 스키마 v6에서도 평문 없음(E11).
- ticket 키·서명키 분리: `mbnas_core/src/mbnas_core/config.py:77,79,108`, `api/app.py:177-182`(변경 없음), 설치 시험(E8 `test_ops_install.py` 8 passed).
- runner 토큰 runner_id 결속: T3-30a(E5), E11 `RUNNER_MISMATCH`; 새 E11 경로도 보고 runner 본인 확인(`check_runner`, T3-34 거부 행 — E7 통과).
- EVAL_1 D7(범위 밖이었음)은 문서·예시로 해결(D7 표, E16). core가 주소를 검사하지 않는다는 사실은 이제 문서에 그대로 적혀 있다.

### 3. result_limits — **25/25** (앵커 25)

- 정책 trace 비공개(DTO allowlist): `tests/owner/projection` 261 passed(E4), 투영 음성 대조 21 실패(E10), runner 경로 canary(D2)와 그 음성 대조(E9). EVAL_1에서 인용한 근거 줄은 그대로 유효(`test_canary_projection.py:73-271`, `test_owner_routes.py:35-114`, `test_contract_150.py:148`).
- 승인 모델 gene/YAML/가중치 공개: `test_public_golden.py`, `test_config_revisions.py`(E4 포함), 가중치 다운로드 `T5/RERUN_README.md`(직전 ZIP). 공개 계약 `mbnas_contracts/src/mbnas_contracts/openapi/openapi.json` sha `70e0cffa…` 불변(1.1.0은 runner 계약만 바뀜).
- prune 기록의 공개 투영: `tests/owner/runner_protocol/test_t3_34_prune_roundtrip.py:148-159`(공개 키만, 삭제 항목은 bytes만, runner id 없음; E7 통과) + 평가자 E17(runner 절대경로 공개 0, DB에는 있음).
- 신뢰 한계 문서: `docs/public/SECURITY_MODEL.md` §8(85-98) — 92행 측정값 진실성 한계, 93행 관리 경계 전제, **96행에 runner 호스트 절대경로가 core DB에만 저장되고 투영되지 않는다는 문장 추가**(Fix 5와 일치, E17로 확인). 공개·등록 ZIP에 같은 바이트로 실림(E14).
- EVAL_1 D4(낡은 상태 문구) FIXED: 트리와 scratch ZIP 모두 0건(E14, E18). 재빌드 뒤 재확인이 남았다는 사실은 README·PROGRAM_OVERVIEW에 정직하게 적혀 있다.

### 4. admin_subject — **25/25** (앵커 25)

- 필수 점검 두 행이 모두 있다: `docs/internal/VALIDATION_REPORT.md:27`(BLOCKED_BY_ENV — 미충족, 조건 4개, 근거 경로)과 `docs/internal/ACCEPTANCE_MATRIX.md:11`(미충족, 동일 host 분리는 기능 검증).
- "동일 host 분리는 기능 검증"이 보고서에 명시됨: VALIDATION_REPORT T5 행(`:16`) 단서, `SECURITY_MODEL.md:93`(§8-3), `:112`(§9: T5는 같은 호스트·호스트 네트워크·운영자와 같은 uid의 기능 분리), `FEATURES.md:133`, `README.md:5`(별도 관리 주체 배치와 호스트 사이 TLS는 검증하지 않음).
- 이 기준이 채점하는 것은 미충족 상태의 정직한 표기다. **관리 경계 자체는 여전히 검증되지 않았고**(BLOCKED_BY_ENV), EVAL_1 §4의 판정(T5는 관리 주체 분리를 충족하지 않음)은 그대로다.
- 남은 Minor(앵커 불변): D6(ACCEPTANCE_MATRIX 날짜·근거 미갱신, 다른 행 낡음 — ADMIN 행 내용 자체는 정확).

## Fix 5 (runner 프로토콜 1.1.0 prune 왕복) — C02 관점 검증

- runner는 자기 work root 안에서만 지운다: `mbnas_runner/src/mbnas_runner/daemon.py:121-191`(`execute_prune_order`: relpath·run id 검증 → `realpath(work_root)/runs/<run_id>` 포함 검사 → store 안 거부 → `dir_fd`·`O_NOFOLLOW`·inode 재검사 → sha·크기 일치 시 unlink), 시험 `tests/public/runner/test_prune_control.py:85-133`(`../`·절대경로·`..` run id·심볼릭 디렉터리·심볼릭 파일·store·경로 밖 → 거부; E6 통과).
- prune order에 owner 전용 데이터 없음: `mbnas_core/src/mbnas_core/orchestration/retention.py:389-391`(명시 8키), `reason = RETENTION_<정책>`(`:283`, 사용자 정책 이름에서 유래); 평가자 E17(심은 표식 0).
- runner 절대경로는 core DB에만: SECURITY_MODEL §8-6 문장과 일치(E17: DB 13회, 공개 0회). `prune_requests` 조회 경로 없음(DD-A68), 공개 계약 불변.
- E11 보고는 보고 runner 본인만(`check_runner`, T3-34 거부 시험 E7).

## 결함 (현재 열린 것)

| ID | 심각도 | 기준 | 소유 / 파일 | 내용 | 구체적 수정안 |
|---|---|---|---|---|---|
| D3 | Minor | customer_root(앵커 불변) | WP-7V T8 §2 driver, `tests/public/runner/test_transfer_local.py:119-129` | runner 작업 자식 프로세스 environ이 프로젝트 누출 검사에 없음(문서화된 1.5.1 항목). 평가자 E15에서는 0건 | PFINAL2 또는 1.5.1의 T8 §2에 CPU 학습 job 1개 + 자식 pid 캡처를 넣는다(`$S/e2/tools/live2_capture.py`가 틀); 단위 시험은 `SECRET_ENV` 전 키(`MBNAS_CORE_*` 포함)가 자식 env에서 빠짐을 단언 |
| D6 | Minor | admin_subject(앵커 불변) | `docs/internal/ACCEPTANCE_MATRIX.md`(설계자) | 10-23 판정 그대로: ADMIN 행에 날짜·T5 근거 없음, 다른 행 다수가 VALIDATION_REPORT와 어긋남(OFFLINE_INSTALL 미충족 vs PASS 등) | 날짜 붙인 갱신으로 VALIDATION_REPORT와 맞추고 ADMIN 행에 `VALIDATION_REPORT.md:27`을 인용 |
| D8 (신규) | Minor | customer_root(앵커 불변) | `tests/owner/projection/test_runner_route_canary.py:62-64` | 필수 경로 목록에 1.1.0 경로(`POST /jobs/{id}/heartbeat`의 `control.prune`, `POST /checkpoints/{id}/pruned`)가 없어 새 채널이 canary 스캔 밖. 평가자 E17에서는 0건 | `$S/e2/probe_prune/test_c02_prune_channel_probe.py`를 이식(`last_rung_end_only` run, `prune_requests.planned_by` 포함 seed, heartbeat·E11 응답 스캔, 절대경로 공개 0 + DB 양성 대조) |

Blocker 0, Critical 0, Major 0, Minor 3. 실패로 판정한 시험은 없다(E9·E10은 의도한 음성 대조).

## 총점과 판정

| 기준 | 점수 | 앵커 |
|---|---|---|
| customer_root | 25 | 필수 점검 둘 다 실행 증거 + 현재 트리 재실행(D3·D8은 범위 밖 Minor) |
| core_boundary | 25 | 필수 점검·관찰 항목 모두 실행 증거 + 재실행 |
| result_limits | 25 | 투영 시험·음성 대조·신뢰 한계 문서, D4 해결 |
| admin_subject | 25 | 두 문서의 ADMIN_BOUNDARY 행 정직, 기능 분리 명시 |
| **합계** | **100/100** | |

판정: **PASS**(총점 > 90, Blocker 0, Critical 0). 단, ZIP 의존 점검(E12–E14)은 평가자 scratch 빌드 기준이므로 PFINAL2의 재빌드 ZIP·T8 §2 결과로 보충이 필요하다 — 보충에서 볼 것: 재빌드 public/registration ZIP canary 스캔·멤버 grep·실린 문서, 재빌드 스택의 T8 §2(`/proc`에 OLDPWD·PYTHON* 없음, 비밀 0), 가능하면 D3 자식 캡처.

## 확인 못 함

1. 별도 관리자 host(관리 주체 분리) — BLOCKED_BY_ENV(두 번째 관리 주체 host 없음), 점수 아님, 미충족 유지.
2. 호스트 사이 실제 TLS 경로 — 미시험(`SECURITY_MODEL.md:45,111`).
3. 재빌드된 최종 ZIP(PFINAL2) — 평가 시점에 없음. 평가자 scratch 빌드는 비재현 빌드라 sha가 PFINAL2와 다를 수 있다.
4. 현재 트리의 공개 CPU 이미지(컨테이너 env·레이어) — 재빌드하지 않음(직전 ZIP의 T5 재실행 증거만).
5. 실서버 SSE 스트림 캡처 — 프로젝트 T8과 평가자 모두 하지 않음(인프로세스 SSE replay는 E4).
6. nginx 예시는 문법·경로 시험만 했다(실제 원격 배치·TLS 종단 없음).

## Addendum (final ZIPs) — 2026-10-28 11:5x KST

대상: P-final-2 최종 ZIP `dist/public/…public-runtime-linux_x86_64.zip` sha256 `bc0500bdfa43ddf5fb74bde4cf51a967e06f45bd2b901ba26da0794c1f0853d3`(476 members), `dist/registration/…registration-runtime-linux_x86_64.zip` sha256 `dea510dfb54dd85da556ac38423f747a0a7d1afbff7398c501127bd0f8b92f6e`(486) — `sha256sum` 재계산 일치. 두 ZIP의 BUILD_INFO 원천은 공개 4패키지 해시 `9859adb2…`이고, 현재 트리에서 `source_tree_hash.py --package mbnas_contracts --package mbnas_model_runtime --package mbnas_runner --package mbnas_server`를 다시 계산해도 `sha256:9859adb218145b9feccb1c0be8c1c6a51eaed90dfe575a0b5e1b10dd63aff4a3`이다(5패키지 해시는 DD-A73 core 수정으로 `sha256:1b4bd1a4a45196e3c558d62507f109301973eddc180df5968ed7014a72e6708c`). owner ZIP은 지시대로 제외했다. 산출물 `$S/e2/add/out/`.

| # | 재실행 | exit | 결과 |
|---|---|---|---|
| A1 | `canary_scan.py --manifest docs/internal/canary/CANARY_MANIFEST.json --profile {public,registration} --min-files 100 dist/<p>/*.zip` | 0 / 0 | public 624 files·registration 634 files, hits 0 (`canary_final.txt`) |
| A2 | 멤버 목록(바깥 + 중첩 wheel) + 비공개 이름 grep(E14와 같은 패턴 + `LICENSE_STATUS`) | 0 | public 623(476 + 147)·registration 633(486 + 147), 비공개 이름 0건; 멤버 목록이 평가자 scratch 빌드(E12)와 **완전히 같다** (`grep_members_final.txt`) |
| A3 | 최종 ZIP과 scratch 빌드의 파일별 바이트 비교 | 0 | 다른 것은 빌드 메타데이터(BUILD_INFO, FILE_MANIFEST, PACKAGE_CONTENTS, PACKAGE_PROFILE), 빌드 때 생성되는 `docs/registration/FILE_LIST.md`, wheel 4개의 압축 메타데이터뿐이며 wheel 멤버는 바이트 동일 → EVAL_2 E12–E14 결론이 최종 ZIP에 그대로 적용된다 (`zip_docs_final.txt`, `wheel_compare_final.txt`) |
| A4 | ZIP 안 문서 | 0 | public 12·registration 20개 `.md`에서 "재실행 예정/검증 예정/수행 예정/재실행이 남은/loopback 전용" 0건; `SECURITY_MODEL.md`(`234e9724…`)·FEATURES·INSTALL·OPERATIONS·README·`nginx.example.conf` = 현재 트리와 동일; owner 호스트 proxy 블록(`location ^~ /core/v1/owner/` → 403) 포함; D1 시험 `tests/public/runner/test_runner_settings_no_db.py` 포함 |

새 프로젝트 증거(열람): P-final-2 T8 §2 — 최종 public·registration 스택의 응답·로그·`/proc` environ 4개 스캔 0 hits(needles 124, 정규화 불필요 = OLDPWD 없음; `docs/internal/VALIDATION_REPORT.md:25`, `docs/internal/evidence/WP7/PFINAL2/t8/runtime_scan_summary.json`); T5 rerun2(public `bc0500bd…` 이미지) — 학습(train3) 중 runner 컨테이너의 모든 프로세스 environ을 캡처했고 job 자식 `python -B -m mbnas_runner.child`에는 비밀 키가 없으며(`docs/internal/evidence/T5/rerun2/t8/proc_environ_key_names.txt:27-29`), runner `/proc` 스캔 0 hits(`T5/rerun2/t8/scan_proc_runner.txt`, needles = core·owner 설치·gateway 비밀).

결함 갱신: **D3 → 증거로 해결**(T5 rerun2가 학습 중 job 자식 env를 캡처·스캔, 0건; `SECURITY_MODEL.md:34,109`의 "T8에서 캡처하지 않음"은 T8 §2에 대해서는 사실이므로 결함 아님 — T5 rerun2 근거를 덧붙이면 더 정확). **D6 → FIXED**(`docs/internal/ACCEPTANCE_MATRIX.md` 11:3x 날짜 갱신, ADMIN 행 `:13` "BLOCKED_BY_ENV — 미충족"이 VALIDATION_REPORT ADMIN 행을 인용, 10-23 표는 `## 이력` 아래로). **D8 → OPEN**(Minor, `tests/owner/projection/test_runner_route_canary.py` 08:39 이후 변경 없음; 1.1.0 heartbeat·E11 경로는 평가자 E17로만 확인).

판정: **점수 유지 — 100/100 PASS**(Blocker 0, Critical 0). 남은 결함은 D8(Minor) 하나.

## Addendum (DD-A75 F1) — 2026-10-28 14:2x KST

트리 `sha256:26e02259bb83eca0cf56e7e11bd9e216d383361a57fd70082f2ff9c0c346de13`(1b4bd1a4… 대비 `mbnas_core/src/mbnas_core/console/projection.py`만 변경, sha `7604d3f2…`; 14:13·14:21 재확인). 산출물 `$S/e3/out/`, probe `$S/e3/probe_space/`, 실기동 `$S/e3/live3/`(본문 0600).

**먼저 정정**: EVAL_2의 100은 그 트리(`f00987be…`)에 대해 성립하지 않는다. 그때 이미 F1(공개 `raw_metrics.resolved_recipe`에 runner 절대경로, Major)이 있었고, 내 E17 probe는 stub runner를 썼는데 그 결과에는 `resolved_recipe`가 없어 F1을 볼 수 없었다. result_limits 25(및 "runner 절대경로는 공개 0" 판정)는 근거가 부족했다.

| # | 확인 | exit | 결과 |
|---|---|---|---|
| F1-1 | diff 열람 `docs/internal/evidence/WP7/FIX7_DD_A75/F1/20_projection_py.diff`: `_raw_metrics`가 device id 매핑 뒤 `scrub_deep(raw)` 반환(`projection.py:289-299`), 규칙은 기존 `_ABS_PATH`(`:99`) → `<path>`, `/api/v1` 유지, pid·호스트 이름 scrub, 키는 그대로 | — | 확인 |
| F1-2 | pytest `tests/owner/projection/test_dd_a75_raw_metrics_paths.py tests/owner/api/test_dd_a75_raw_metrics_paths_routes.py`; pytest `tests/owner/projection` | 0 / 0 | 19 passed(15 + 4); projection 276 passed (`a75.*`, `projection.*`) |
| F1-3 | **실기동 전체 공개 표면 probe** `$S/e3/tools/live3_surfaces.py`: dev venv(현재 트리)로 core + owner worker + `start.sh --profile public`, **모든 경로에 공백**("pub root", "own home", "data dir", "out dir"); T5 harness 사본으로 학습 2건(검증 OFF, **검증 ON → NOT_EVALUATED**) 뒤 gateway로 health·presets·registry·block-catalog·runs, run마다 run·snapshot·candidate detail·config-revisions(+YAML)·SSE replay(`events?after_seq=0`)·export(기본·`?profile=public`, 멤버 전부) 29개 본문 수집 → `$S/e3/tools/analyze_surfaces.py`가 모든 문자열 값(JSON pointer)을 표지별로 분류 | 0 | core 홈 표지(`own home`) **0건**(모든 표면). 절대경로는 사용자 입력 메아리(`resolved_protocol.console.paths`, `resolution.paths.*.requested`, export `protocol/*`), health의 gateway 자기 루트, preset 자리표시, export README의 경로 이름(`/runs/`, `/export`, `/core/v1/owner/runs/`)뿐 — 모두 설계상 허용. **runner 경로는 `raw_metrics` 안에만 남는데, `<path> root/runtime/runner/runs/runs/train-…/data_view/…/data.yaml` 형태의 꼬리로 남는다**: 검증 ON run의 `resolved_recipe.{data,model,project,save_dir}` 4값 × 8 표면(snapshot `candidates[]`·`events[].payload`, candidate detail `evaluations[]`, SSE frame, export 2종의 `evaluations.jsonl`·`events.jsonl`). 로그 payload·worker·generations·config revisions·jobs.json·artifacts.json·checkpoints.json·csv에는 runner 표지 0 (`live3_surfaces.txt`, `analyze_surfaces.txt`) |
| F1-4 | 투영 단위 probe `$S/e3/probe_space/test_c02_spaced_path_probe.py`: DD-A75 시험의 fixture에 공백 포함 runner 경로(`/srv/mbnas public r2/…`, `/home/John Smith/…`)를 넣어 4 표면 투영 | 0 | 4 표면 모두 `"<path> public r2/mbnas_application/runtime/runner/runs/…"`, `"<path> Smith/mbnas/runtime/…"`가 남음(snapshot 12, detail 4, SSE 8, export 4건). 같은 본문에 DD-A75 시험의 검출기(`_ABS_PATH` 재사용 + 전체 경로 문자열 비교)는 **0건** — scrubber와 같은 정규식을 써서 이 꼬리를 볼 수 없다 (`spaced_path_probe.txt`) |

(2) `CandidateEvaluation`/`raw_metrics`를 싣는 공개 component와 확인 결과: Snapshot `candidates[]`·`events[].payload`(candidate.updated/evaluated), CandidateDetail `evaluations[]`, SSE `candidate.*` frame, export `evaluations.jsonl`·`events.jsonl`(`evaluations.csv`에는 runner 표지 없음) — 모두 F1 수정이 적용되고 위 꼬리 문제만 있다. 그 밖에 runner가 보고한 문자열이 닿는 곳: worker(`device`·`device_name` scrub, id는 runner id/opaque), 로그 payload(`message`·`message_detail`·`checks` scrub, `path`는 owner 전용으로 제거), checkpoint 보기(id만), prune 기록(공개 키만) — F1-3에서 runner 표지 0. 로그 payload·`CandidateDetail.lineage.recipe`·`audit.orchestration.final.recipe`·`StructureReport.lineage.recipe`의 `recipe`는 core가 프로토콜 값으로 만든 최종 재학습 recipe(`orchestration/final_retrain.py:125-145`: epoch·horizon·init·rung·group, 경로 없음)라 runner 경로가 들어갈 경로가 없다(최종 재학습은 GPU 판정 경로라 실행으로는 확인 못 함).

(3) `22_scrub_rule_probe.txt`의 미처리 형태 판정(0.1.0): Windows 드라이브·UNC — 검증 플랫폼이 Linux x86_64뿐(`docs/public/COMPATIBILITY.md:9`)이라 해당 없음(정보). `file://`, `~/`, `host:/path`, 사전 키 — runner는 realpath로 만든 POSIX 절대경로를 보고하고 실제 run(F1-3)에서 이런 형태·경로 키가 나오지 않았다(정보, 심층 방어 항목). **목록에 없던 실제 공백**: 공백이 든 경로는 첫 공백 앞까지만 `<path>`가 되고 나머지가 남는다 — 공백 포함 설치 경로는 T6가 검증한 지원 배치(`docs/public/INSTALL.md:3`)이므로 0.1.0 결함(D9, Minor; 아래). `//server/share`도 미처리(Linux runner가 만들지 않음, 정보).

(4) 문장 판정: `mbnas_contracts/src/mbnas_contracts/runner_protocol.py:6-14`("never projects them into a public response")와 `docs/public/SECURITY_MODEL.md:96`("공개 응답에는 투영되지 않는다")는 **공백 없는 경로에 대해서는 이제 참**(F1-2, 프로젝트 실프로세스 smoke `31_real_process_postfix_v2_values.txt`: raw_metrics 일치 0, 값 `<path>`)이지만, **공백이 든 경로에 대해서는 참이 아니다**(첫 공백 뒤 경로 조각이 투영됨, F1-3·F1-4). 또 runner_protocol.py의 저장 위치 목록(`checkpoints.path`, `artifact_locations.uri`, `jobs.result_json`)에는 resolved recipe가 공개 멤버인 평가 행 `raw_metrics.resolved_recipe`에도 복사된다는 사실(지금은 scrub됨)이 빠져 있다.

결함 갱신:
| ID | 심각도 | 기준 | 소유 / 파일 | 내용 | 수정안 |
|---|---|---|---|---|---|
| F1 (DD-A75) | Major → **FIXED**(공백 없는 경로) | result_limits | `mbnas_core/src/mbnas_core/console/projection.py:289-299` | 위 F1-1~F1-3 | — |
| D9 (신규) | Minor | result_limits | `projection.py:99`(`_ABS_PATH`), `:289-299`; 시험 `tests/owner/projection/test_dd_a75_raw_metrics_paths.py:56-59`, `tests/owner/api/test_dd_a75_raw_metrics_paths_routes.py:43` | 공백이 든 runner 경로는 꼬리(호스트 디렉터리 이름·run 구조)가 공개 `raw_metrics`에 남는다(실기동 8 표면, 투영 4 표면). DD-A75 시험 검출기는 scrubber와 같은 정규식이라 이를 볼 수 없다. 사람 이름이 든 홈 경로라면 이름 일부가 남는다. 경계 넘는 owner 비밀은 아님(분리 배치에서 runner 경로는 고객 자신의 것) | `raw_metrics`(최소 `resolved_recipe`)의 문자열 값이 `/`로 시작하면(`/api/v1` 제외) 값 **전체**를 `<path>`로 바꾸거나 run 상대경로로 바꾼다(또는 경로 키 허용목록); 시험 fixture에 공백·유니코드 경로를 넣고 검출기를 비순환으로(심은 경로의 디렉터리 이름 조각이 본문에 없음을 단언); runner_protocol.py 6-14와 SECURITY_MODEL §8-6을 실제 동작에 맞춘다 |
| D8 | Minor | customer_root | (EVAL_2와 같음) | 열림 | (EVAL_2와 같음) |

채점(현재 트리 `26e02259…`): customer_root 25(변화 없음 — runner 경로는 고객 쪽 정보이고 core 비밀·owner 경로는 공개 표면 0, F1-3), core_boundary 25(변화 없음), **result_limits 20**(F1 수정은 실행 증거로 확인, 그러나 지원 배치에서 경로 조각이 남고 새 시험이 그것을 볼 수 없음 = 사소한 누락), admin_subject 25 → **95/100 PASS**(90 초과, Blocker 0, Critical 0). **EVAL_2의 100은 유지되지 않는다.** 남은 결함: D9 Minor, D8 Minor.

확인 못 함: owner ZIP 재빌드본과 P2 T4/T5 재확인(진행 중 — 공백 없는 경로라면 "검증 ON 응답에 절대경로 0"을 기대하지만 공백 경로 꼬리는 그 점검 방식으로도 보이지 않을 수 있다), 최종 재학습 recipe 로그의 실제 실행(GPU 경로), 다중 고객이 core 하나를 공유할 때의 run 격리(0.1.0 범위 밖으로 보임).

## Confirmation (DD-A75 F1b, ZIP 9f03c484…) — 2026-10-28 15:5x KST

검증 대상: owner ZIP `dist/owner/…owner-private-runtime-linux_x86_64.zip` sha256 `9f03c484bbc8c0a2227cc325658892e3efe286d08cca9a1978383158075c2f2a`(재계산 일치), BUILD_INFO `r-07c7cef95cd73775`, source tree `8fc93f2a…`. ZIP을 `$S/e4/ownerzip/`에 풀고 `PYTHONPATH=<ZIP의 5개 패키지 src>`로 실행하면 모듈이 ZIP 쪽에서 로드되고(`projection.__file__` 확인) `source_tree_hash.py`가 `sha256:8fc93f2a596891174c2884e1697fd6003377b713102c0b21b01eb45d3f6f7312`, 공개 4패키지 `sha256:9859adb2…`(= 최종 public/registration ZIP 원천)을 낸다. 아래 시험·probe는 모두 이 ZIP 코드에서 돌렸다.

**주의 — 작업 트리가 평가 중에 바뀌고 있었다**: 15:43–15:54 사이 다른 세션이 소스·문서의 docstring을 정리하고 있었다(내부 참조 제거; 트리 해시 15:45 `4a1b9e3c…` → 15:53 `8c3bfae2…`, 공개 4패키지 `cc9291b4…` → `f13233e4…`). 15:54:14에 ZIP 코드와 작업 트리를 AST로 비교하면(모든 docstring 문 제거) 111개 파일의 텍스트가 다르지만 **코드 차이는 0**이다(`$S/e4/out/ast_compare_zip_vs_tree3.txt`). 따라서 아래 판정은 동작상 현재 트리에도 해당한다. 다만 공개 패키지 텍스트가 이미 출하된 public/registration ZIP(`9859adb2…` 원천)과 달라지므로, 정리가 끝나면 ZIP과 소스의 대응을 다시 맞춰야 한다(C02 결함 아님, 기록만).

| # | 확인 | exit | 결과 |
|---|---|---|---|
| B1 | ZIP의 `mbnas_core/src/mbnas_core/console/projection.py` 열람 | — | `_path_values`(`:291-302`): raw_metrics의 문자열 값 중 첫 공백 아닌 문자가 `/`인 것(`/api/v1` 제외)을 깊이와 상관없이 값 전체 `<path>`로 바꾸고 키는 그대로 둔다. `_raw_metrics`는 `scrub_deep(_path_values(raw))`를 반환(`:319`). docstring `:18-23`, `:306-312`에 규칙과 resolved recipe의 raw_metrics 사본이 적혀 있다 |
| B2 | ZIP의 pytest `tests/owner/projection/test_dd_a75b_raw_metrics_path_values.py tests/owner/api/test_dd_a75b_raw_metrics_path_values_routes.py` | 0 | **23 passed**(19 + 4). 검출기는 심은 조각 9개(공백·유니코드·NBSP 등, `:49`)를 문자 그대로와 JSON `\u` 이스케이프로 찾는다 — scrubber 정규식과 무관한 **비순환** 검출기. 양성 대조: 원래 pass-through는 9개 모두, F1 정규식만 쓰면 꼬리가 남는다(`:147-168`) (`$S/e4/out/f1b.*`) |
| B3 | ZIP의 pytest DD-A75 시험 2개 파일; ZIP의 `tests/owner/projection` 전체 | 0 / 0 | 19 passed; **295 passed** (`f1.*`, `projection.*`) |
| B4 | 평가자 단위 probe(EVAL_2 F1-4와 같은 공백 경로 fixture, ZIP 코드) | 0 | 4 표면(snapshot, detail, SSE, export) 모두 `data/model/project/save_dir` = `<path>`, 꼬리 0, 전체 경로 0 (`spaced_path_probe_f1b.txt`) |
| B5 | **평가자 실기동 probe**(F1-3 방식, ZIP 코드로 core·owner worker·gateway·runner를 직접 기동; 경로에 공백과 유니코드: `pub 루트 dir`, `own home 홈`, `data dir`, `out dir`); 검증 OFF·ON 학습 뒤 공개 본문 29개 | 0 | runner 표지(`pub 루트 dir`, `루트 dir`, `runtime/runner`) **0**, core 표지(`own home`, `홈/core`, `core/runs`, `core/db`) **0**, `<path> …` 꼬리 0. 검증 ON 행의 `resolved_recipe` 4값은 `<path>`. 남은 절대경로는 모두 허용 범주(사용자 프로토콜 메아리, health의 gateway 자기 루트, preset 자리표시, export README의 경로 이름) (`live4_surfaces.txt`, `analyze_live4.txt`) |
| B6 | P2 증거 열람 `docs/internal/evidence/T4/rerun2/attempt_dd_a75/`, `T5/rerun2/attempt_dd_a75/` | — | 둘 다 ZIP `9f03c484…` 사용(`zips_sha256_used.txt`). `pathcheck-validation-on-run` PASS: snapshot·detail·SSE replay·export에서 `fragment_other` 0, `runner_fragment_raw_bytes_hits` 0, `abs_path_other` 0, recipe 키 `<path>`(T4는 클라이언트 메아리 18건을 따로 셈). T5 t8: responses 5123 files·logs·proc(gateway, runner, core host) 0 hits, 양성 대조 검출 |

판정:
- **D9 → FIXED**(구조화된 경로 값): 원래 보고한 결함(raw_metrics의 공백 경로 꼬리 + 순환 검출기)은 B1–B6으로 해결이 확인됐다.
- **D9b (Minor, 열림)**: 자유 텍스트 안의 공백 경로. 기존 `_ABS_PATH` scrub은 그대로라 `scrub_text("FileNotFoundError: /srv/mbnas pub r2/runtime/runner/runs/x.pt")` → `"FileNotFoundError: <path> pub r2/runtime/runner/runs/x.pt"`가 된다. raw_metrics 안에서도 `/`로 시작하지 않는 문장 속 경로는 마찬가지다(ZIP 코드로 재현). 이 텍스트가 실제로 공개되는 통로가 있다: 실패한 제안 job의 로그 `message_detail`(`mbnas_core/orchestration/engine.py:953`, owner worker 실패 문구 — core 호스트 경로라면 owner→고객 방향의 경로 조각)과 그 밖의 scrub 대상 문구. 성공 run(B5, P2)에서는 나오지 않았고 실패 경로는 실행하지 않았다. 프로젝트도 이를 기록했다: `docs/internal/evidence/WP7/FIX7_DD_A75/F1b/52_security_model_96_verdict.txt`("NOT unconditionally true", 1.5.1), 이월 #14. 그러나 출하된 공개 문서 `docs/public/SECURITY_MODEL.md:96`은 여전히 단서 없이 "공개 응답에는 투영되지 않는다"고 적는다.
- **D8 (Minor, 열림, 변화 없음)**: `tests/owner/projection/test_runner_route_canary.py:62-64`의 필수 경로에 heartbeat·E11 경로가 여전히 없다(ZIP 사본 동일).
- `runner_protocol.py:6-14`의 저장 위치 목록 누락은 이월 #14(공개 패키지라 편집 보류)로, D9b와 같은 문서 정정 대상이다.

채점(ZIP `9f03c484…` = 트리 `8fc93f2a…`, 동작상 현재 트리와 동일):
| 기준 | 점수 | 앵커 |
|---|---|---|
| customer_root | 25 | 변화 없음(B5·B6: owner·core 경로, 비밀 0) |
| core_boundary | 25 | 변화 없음 |
| result_limits | **20** | 구조화된 결과 값의 경로 투영은 비순환 시험·두 실기동(P2, 평가자)으로 증명됨. 다만 신뢰 한계 절의 공개 문장 1건(§8-6)이 D9b를 반영하지 않아 무조건적 주장으로 남았다 = "문서 1건·라벨" — 그 문장에 단서를 달거나(공개 문서 재빌드 필요) 자유 텍스트 규칙을 고치면 25 |
| admin_subject | 25 | 변화 없음 |
| **합계** | **95/100** | |

판정: **PASS**(총점 > 90, Blocker 0, Critical 0). 남은 결함: D9b Minor, D8 Minor. 확인 못 함: 공백 경로가 든 **실패** 경로(제안 job 실패 등)의 실제 공개 본문, 정리 세션이 끝난 뒤의 최종 소스와 출하 ZIP의 대응.

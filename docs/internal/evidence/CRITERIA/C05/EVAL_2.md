# C05 입력·인증 — 독립 평가 EVAL_2 (수정 라운드 후 재채점)

- set: **C05** (`docs/internal/criteria/C05.json` v1.0), 앵커는 EVAL_1과 같다(25/20/15/0).
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자. 채점만 하고, 결정은 설계자가 한다.
- time: 2026-10-28T07:43+09:00 시작, 08:0x+09:00 보고 작성
- tree hash (mbnas-source-tree/2, 221 files): 시작 `sha256:5a7bc5920ac3c1fe630a45c8ea0dd4661cb754661a90da5c09441fb7237c4223`.
  - 재실행을 모두 마친 07:55에 다시 계산한 값도 같았다. 평가 중 트리 변경은 없었다.
  - 패키지 소스는 06:40 이후 수정된 파일이 없다. 따라서 최종 게이트(06:43–07:03)와 최종 ZIP(07:05–07:06)도 이 트리에서 만들어졌다.
  - 지시된 `| tail -3`은 JSON 닫는 괄호만 출력한다. 그래서 `sha256` 필드를 인용했다.
- 최종 ZIP: public `ce34b821…`, registration `95b541e9…`, owner `79cce02a…`. sidecar `.sha256`·`7V_FINAL/SHA256SUMS_final.txt`와 일치하고, 스캔 전후에도 같았다.
- 환경
  - CPU 전용: `CUDA_VISIBLE_DEVICES=""`, `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `--basetemp`는 scratchpad.
  - GPU·원본 체크아웃(SOURCE_ROOT)·build/source_snapshot·/data에는 접근하지 않았다.
  - 프로젝트에 쓴 파일은 이 파일 하나다.
- 표기
  - 경로는 TARGET_ROOT 기준 상대경로다.
  - 평가자 산출물: EVAL_2 실행 로그는 `$SCRATCH/wp3/c05e2/`, EVAL_1의 도구·probe는 `$SCRATCH/wp3/c05/`에 있다. `$SCRATCH`는 이 세션의 scratchpad다(경로에 계정명이 들어 있어 표기하지 않는다).
  - 금지 문자열 needle은 EVAL_1과 같은 조각 결합 스캐너로 만들었고, 리터럴은 쓰지 않았다.

## 결론

총점 **100/100**(25+25+25+25), Blocker 0, Critical 0 → **PASS**.

- EVAL_1 결함 10건 중 9건이 수정됐고, 각각 유지되는 테스트나 실행 증거가 있다.
  - D1 health 누출: 수정됐다. 분리 구성 테스트와 음성 대조가 있고, T8 최종 재실행은 PASS다. 평가자 probe에서 core root 노출이 0/2다.
  - D2·D4·D5·D6·D9: 게이트 스위트에 테스트가 생겼고, 변이 검사로 회귀 탐지력이 확인됐다.
  - D3·D8: 수정됐다. D10은 코드로 수정됐다(runner 진입점 회귀 테스트 부재는 잔여로 기록).
- 남은 1건(D7)은 사용자 git index에 추적된 `.pyc`다. 작업트리와 배포 ZIP에는 0건이고, 정리는 사용자 git 조작으로 보고돼 있다(DD-A61 (c)). 그래서 C05 증거 결함으로 보지 않았다.
- 필수 시험은 모두 현재 트리에서 다시 통과했다.
  - T3-30/31/32와 ticket 위조/만료
  - SECURITY §4 1~6, T3-35
  - 게이트웨이 오류 형식·리댁션
  - 금지 문자열 0건: 최종 ZIP 스캔과 T8 런타임

## 재실행 요약 (cwd=TARGET_ROOT, `runtime/venv/bin/python -B -m pytest -p no:cacheprovider -m "not gpu and not live and not legacy_records and not host_data" --timeout=300`)

| # | 명령 / 대상 | exit | 결과 |
|---|---|---|---|
| E1 | `tests/owner/runner_protocol` 전체 | 0 | 67 passed(230 s). 이 중 `test_t3_tickets.py` 4, `test_runtime_manifest_status.py` 7, `test_t3_auth.py` 6(T3-30a/b/c, 31, 32, 33) |
| E2 | `tests/owner/qa/test_wp3_security_negatives.py` + `tests/owner/qa/test_security_negatives.py` + `tests/owner/structure_sandbox` | 0 | 145 passed(23 + 108 + 14), 1 deselected(host_data) |
| E3 | `tests/public/server` 전체, `tests/public/runner/test_tls_private_ca.py`, `tests/public/infra/test_argv_only_invariant.py`, `tests/public/infra/test_no_host_facts.py`, `tests/public/contracts/test_log_redact.py`, `tests/public/contracts/test_runner_protocol.py`, runner child env, CLI insecure URL | 0 | 696 passed, 1 skipped(mini run 부재), 25 deselected. 이 중 health 분리 2, TLS 사설 CA 10(gateway 6 + runner 4), argv-only 26, no_host_facts 8. EVAL_1의 xfail `test_health_reports_archive_roots`는 이제 PASS |
| E4 | `tests/owner/api/test_access_log_redaction.py`, `tests/owner/projection/test_t8_canary_scan.py`, `tests/owner/worker/test_dd_a60_owner_worker.py::test_worker_entry_point_log_lines_are_redacted`·`::test_configure_logging_installs_the_redaction_filter_on_its_handler` | 0 | 7 passed |
| E5 | 평가자 probe(EVAL_1 R4·R8·R14와 같은 파일): ticket, manifest, scope 행렬 | 0 | 3 passed. 결과는 EVAL_1과 같다 |
| E6 | 평가자 probe(EVAL_1 R15·R17과 같은 파일): 오류 11종, health 분리 구성 | 0 | 2 passed. 오류 11/11이 code·message·request_id(= `X-Request-ID`)·location을 모두 갖는다(413 → `gateway`). 공개 health 본문에 core root 0/2 |
| E7 | 평가자 host-fact 스캐너(멤버 이름 + 바이트, 중첩 wheel, HF base64 변형)로 최종 ZIP 3종 검사 | 0 | public 613 files, registration 623 files: HF1~HF5 0, 원본 호스트 GPU UUID 0, 빌드 호스트명 0. owner 2379 files: HF 존재(owner 허용, 양성 대조) |
| E8 | `scripts/dev/canary_scan.py dist/{public,registration}/*.zip --min-files 100`(HOST_FACT#6 포함 13 needles) | 0 / 0 | 614 / 624 files, hits 0, PASS |
| E9 | 평가자 스캐너 `tree` 모드 | 0 | 대상: 공개 패키지 4종, `mbnas_web/src`, `mbnas_web/dist`, `docs/public`, `tests/_shared`, `tests/public`, `scripts/public`. HF는 `tests/public/infra/test_no_host_facts.py`(스캐너 테스트 자신의 리터럴)에만 있다. 이 파일은 public/registration ZIP에서 제외되며(DD-A40 (f)/DD-A41 (b)), E7·E8로 확인했다. 패키지 소스의 `.pyc`는 0개 |
| E10 | 평가자 TLS probe(EVAL_1 R18 재실행) | 0 | runner·gateway 모두 CA 없음·다른 CA → `CERTIFICATE_VERIFY_FAILED`, 올바른 CA → 200. gateway `core_tls_verify=false`는 여전히 연결되지만, 이제 `CORE_TLS_VERIFY_DISABLED` 경고 1줄이 남는다(토큰 미포함) |
| E11 | sandbox 기본값 probe(EVAL_1 R7) | 0 | 변화 없음: AS 8 GiB, cpu 900 s, wall 300 s, max_input 1280, 출력 1 MiB, `unshare-rn+setsid+rlimits`, worker env에 `MBNAS_*` 없음, SAFE_LOAD 3종, allowlist 64개(위험 이름 0) |
| E12 | runner 리댁션 probe: 새 인터프리터에서 `mbnas_runner.__main__.install_redaction()` 호출 후 httpx·mbnas_runner·기타 logger로 합성 비밀 기록 | 0 | 합성 비밀 0건 노출(sig/exp/token/Authorization → `***`) |
| E13 | `tests/owner/api/test_dd_a60_access_log.py`(DD-A60 (5) 새 접근 로그) | 0 | 3 passed. gateway 쪽 `test_dd_a60_access_log_gateway.py` 3건은 E3에 포함 |
| E14 | `tests/owner/worker/test_dd_a62_core_local_generation.py::test_a_core_local_file_is_registered_only_when_it_is_the_candidate_yaml`·`::test_core_download_route_serves_a_core_local_architecture`(DD-A63 (c) 새 경로 경계) | 0 | 2 passed. 변조·심볼릭 링크·다른 디렉터리 거부 |

프로젝트 증거(직접 열람)
- 최종 게이트
  - `docs/internal/evidence/WP7/7V_FINAL/gate_owner.{meta,txt}`: exit=0, 1569 passed / 8 skipped / 47 deselected
  - `…/gate_public.{meta,txt}`: exit=0, 1124 passed / 1 skipped / 44 deselected
- 최종 ZIP 스캔: `…/30_canary_scan_{public,registration,owner}.txt` 모두 PASS
- T8 런타임 재실행: `…/7V_FINAL/T8/README.md` Verdict PASS. health `allowlists`가 세 키 모두 빈 목록이다. 정규화 후 응답·로그 HF 0, 비밀 0, 캐너리 0
- D1 음성 대조: `docs/internal/evidence/WP7/FIX1C/05_negative_controls.txt`. 새 테스트가 수정 전 코드에서 2건 FAIL한다
- 수정 1d
  - `docs/internal/evidence/WP7/C05_FIX1D/INDEX.txt`
  - `…/scratch_verifications.txt`의 변이 검사: ticket 테스트는 no_expiry 2·no_runner 3·oracle 3·hb_unchecked 1 FAIL, manifest 테스트는 label_always_verified 6·label_empty_verified 2 FAIL, TLS 테스트는 gw_verify_off 2·runner_verify_off 4·no_tls_warning 2·warn_not_logged 1 FAIL
  - D10 lastResort 대조/수정 기록

## 기준별 채점

### 1. scope_tls — **25/25** (앵커 25)
- scope별 토큰: 다음 테스트가 재실행에서 통과했다(E1, E2, E5).
  - `tests/owner/runner_protocol/test_t3_auth.py:27-40`(T3-30a), `:72-91`(T3-30c)
  - `tests/owner/qa/test_wp3_security_negatives.py:195-201`(server 토큰 → owner 403)
  - scope 행렬 probe
- 비-loopback TLS 강제: 다음 테스트가 통과했다(E1, E3).
  - runner: `test_t3_auth.py:112-129`(T3-32, CLI exit 2)
  - gateway: `tests/public/server/test_wp3b_gateway_fixes.py:254-288`
- CA 검증: EVAL_1 D4에서 정적 근거뿐이던 것이 이제 유지되는 테스트로 바뀌었다(E3에서 10 passed). E10 probe 결과도 같다.
  - gateway: `tests/public/server/test_tls_private_ca.py:155-212`
    - CA 없음·관계없는 CA → 503 `CORE_UNAVAILABLE`, `CERTIFICATE_VERIFY_FAILED`, 요청 0건(서비스 토큰 미송신)
    - `core_ca_file` → 200
  - runner: `tests/public/runner/test_tls_private_ca.py:134-155`
    - 같은 거부·수용 동작
    - 검증을 끄는 설정 필드가 없음
  - 구현: `mbnas_runner/src/mbnas_runner/client.py:126-128`(SSLContext, 절대 False 아님), `mbnas_server/src/mbnas_server/api/core_client.py:48-53`
- `core_tls_verify=false` 경고(EVAL_1 D5):
  - `mbnas_server/src/mbnas_server/config.py:258-276`: `core_tls_problem`, 앱 시작 시 WARNING
  - `mbnas_server/src/mbnas_server/doctor.py:67-75`: `core_tls` 경고 항목
  - `test_tls_private_ca.py:188-212`, `:221-224`(userinfo 미노출)
- 남은 것: 호스트 간 실제 TLS 경로는 여전히 미검증이고, 문서(`docs/public/SECURITY_MODEL.md:42,107`)도 "검증 예정"으로 정직하게 표기한다. C05 필수 시험 범위 밖이라 "확인 못 함"에 둔다.

### 2. ticket_expiry — **25/25** (앵커 25)
- 토큰 만료·폐기·회전 grace: `test_t3_auth.py:43-69`(T3-30b), `:94-109`(T3-31). E1에서 통과했다.
- ticket 위조·만료·바인딩(EVAL_1 D2): `tests/owner/runner_protocol/test_t3_tickets.py`가 생겼고, 4건 모두 E1에서 통과했다.
  - `:52-70` 단위: 자기 lease에서만, 만료 전에만 검증된다. 다른 job/attempt/runner/key와 만료(exp == now 포함)는 같은 403 응답이다.
  - `:73-92`: MAC 변조·절단, 다른 키, payload 변조(MAC 유지), 버전 교체, 쓰레기 값, 빈 값, None은 decode 자체가 안 되고, 응답은 하나로 같다.
  - `:95-99`: 구분자 거부.
  - `:163-184` API(실제 core 앱):
    - 나쁜 ticket 10종 × start/heartbeat, 다른 runner(R2)의 유효 토큰으로 탈취한 ticket, 다른 job 라우트 → 모두 403 `TICKET_INVALID`.
    - LEASED·RUNNING 두 단계 모두에서 전 테이블이 변하지 않고, 응답에 ticket이 반향되지 않는다.
    - 정상 ticket은 이후에도 계속 동작한다.
- 기존: `tests/owner/runner_protocol/test_t3_commit.py:191-226`(T3-18 attempt 교차). 변이 검사 기록이 회귀 탐지력을 보여 준다(위 프로젝트 증거).

### 3. yaml_operator_resource — **25/25** (앵커 25)
- SECURITY §4 1~6과 T3-35는 EVAL_1과 같은 테스트이고, 현재 트리에서 다시 통과했다(E2: 23 + 14). 대상:
  - allowlist 밖 이름은 빌드 전에 FAIL하고 부작용·네트워크 시도가 없다
  - input_size 422, alias 64/65, 행 256/257, 채널 4096/4097, 1 MiB
  - CPU·AS RLIMIT, wall timeout, 출력 상한, 직렬화·Busy, RSS 불변
  - 설계 이름 3개 테스트(`tests/owner/structure_sandbox/test_sandbox_api.py:67-101`)
- SAFE_LOAD env와 8 GiB 근거(`docs/internal/evidence/WP3/3A/sandbox_rlimit_as.txt`)는 E11에서 다시 확인했다.
- operator manifest는 주장(EVAL_1 D6): `tests/owner/runner_protocol/test_runtime_manifest_status.py`가 생겼다(7건, E1 통과). 근거 구현은 `mbnas_core/src/mbnas_core/runner_api/routes.py:148-158`이다.
  - `:52-54` 단위: verified 1개, mismatch 3종(해시 변경·파일 누락·추가 파일), unknown 1개
  - `:57-63`: core가 자기 해시를 못 구하면 unknown
  - `:79-93`: register 응답·owner runners 행·workers 행(공개 `runtime_manifest_status`) 세 곳의 라벨이 같고, 등록은 거부되지 않으며, 재등록하면 라벨이 바뀐다

### 4. path_error — **25/25** (앵커 25)
- 오류 형식: 공개 오류 11/11에 code/location/request_id가 있다(E6). 413 location 수정(EVAL_1 D3)과 고정 테스트:
  - 구현: `mbnas_contracts/src/mbnas_contracts/asgi.py:39-48`(`body_limit_location`: runner/owner 프로토콜 경로는 그 ErrorBody 스키마에 location 필드가 없어 생략), `:51-54`, `:88-100`
  - 앱 배선: `mbnas_server/src/mbnas_server/api/app.py:162-170`(gateway), `mbnas_core/src/mbnas_core/api/app.py:158-169`(core)
  - 테스트: `tests/public/server/test_wp3_gateway_relay.py:148`, `tests/public/server/test_gateway_skeleton.py:259`, `test_t3_auth.py:150`(core console 413 → `core`)
  - 그 밖의 오류 형식 테스트(EVAL_1 인용분)는 E3에서 모두 통과했다.
- 로그 리댁션·owner 로그 토큰 없음: 아래 테스트가 E3·E4·E13에서 통과했다. runner 진입점 리댁션(EVAL_1 D10)은 E12 probe로 확인했다.
  - 기존: `tests/owner/api/test_access_log_redaction.py:30`, `tests/public/contracts/test_log_redact.py:23-65`
  - owner worker: `mbnas_core/src/mbnas_core/owner_worker/main.py:113-128`, `tests/owner/worker/test_dd_a60_owner_worker.py:275,288`
  - 새 접근 로그 양측: `tests/owner/api/test_dd_a60_access_log.py:71`, `tests/public/server/test_dd_a60_access_log_gateway.py:73`
  - runner 진입점: `mbnas_runner/src/mbnas_runner/__main__.py:25-41,72`
- 고객 경로 비전달: EVAL_1 인용 테스트가 E3에서 다시 통과했다. 새 core-local 산출물 경계(DD-A63 (c))도 E14에서 통과했다.
- argv-only(EVAL_1 D9): 정적 근거만 있던 것이 이제 `tests/public/infra/test_argv_only_invariant.py`로 고정됐다(E3에서 26 passed).
  - 규칙별 합성 스니펫으로 검사기 자체를 확인한다(`:335`, `:342`).
  - 실제 소스 전수 검사(`:348`)는 runner child spawn을 반드시 찾아야 통과한다.
- 금지 문자열 0건
  - 최종 public/registration ZIP: HF 0(E7·E8). 원본 GPU UUID는 합성값으로 바뀌었고(`tests/_shared/conftest_common.py:66-68`), 스캐너 needle에 추가됐다(`scripts/dev/canary_scan.py:60-64`, HOST_FACT#6). 이것이 EVAL_1 D8이다.
  - 런타임 공개 응답(EVAL_1 D1):
    - 수정: `mbnas_server/src/mbnas_server/api/relay.py:217-221`. 게이트웨이 자기 roots(비면 빈 목록)와 archive_roots만 내보내고, core allowlists는 버린다.
    - 테스트: `tests/public/server/test_health_allowlists_split.py:71-93`(빈 roots·설정된 roots 2행, 계약 검증 포함). xfail이던 `tests/public/server/test_routes.py:292`는 이제 통과한다.
    - 음성 대조: `FIX1C/05_negative_controls.txt`
    - 런타임: T8 최종 PASS, 평가자 probe에서 0/2
  - 공개 패키지 소스 트리: HF 0(E9). 패키지 소스의 `.pyc`는 작업트리에서 삭제됐다.

## EVAL_1 결함 처리 현황

| # | EVAL_1 심각도 | 상태 | 근거 |
|---|---|---|---|
| D1 health가 core allow root 중계 | Major | **수정** | `relay.py:217-221`; `test_health_allowlists_split.py:71-93` 2 passed; `test_routes.py:292` PASS(xfail 해소); FIX1C 음성 대조; `7V_FINAL/T8/README.md` PASS; E6 probe 0/2 |
| D2 ticket 위조/만료 시험 부재 | Major | **수정** | `tests/owner/runner_protocol/test_t3_tickets.py` 4 passed; 변이 검사 4종 탐지 |
| D3 413 location 없음 | Minor | **수정** | `asgi.py:39-48,51-54,88-100`; 양 앱 배선; 테스트 3곳; E6 11/11 |
| D4 CA 검증 실행 증거 없음 | Minor | **수정** | `tests/public/{server,runner}/test_tls_private_ca.py` 10 passed; 변이 검사 |
| D5 `core_tls_verify=false` 무경고 | Minor | **수정** | `config.py:258-276`, `doctor.py:67-75`; `test_tls_private_ca.py:188-224`; E10 경고 1줄 |
| D6 manifest mismatch/unknown 미시험 | Minor | **수정** | `test_runtime_manifest_status.py` 7 passed; 변이 검사 |
| D7 추적된 `.pyc`에 owner 경로 | Minor | **작업트리·ZIP에서 해소, git index 잔여(사용자 조치)** | 패키지 소스 `.pyc` 0개(E9). `git ls-files`에는 46개가 삭제 상태로 남아 있다(HEAD는 사용자 커밋). DD-A61 (c)대로 사용자 git index 정리가 필요하다. 배포 ZIP에는 처음부터 없었다 |
| D8 원본 호스트 GPU UUID | Minor | **수정** | `conftest_common.py:66-68` 합성값; `canary_scan.py:60-64` HOST_FACT#6; E7 0, E8 PASS |
| D9 argv-only 자동 시험 없음 | Minor | **수정** | `test_argv_only_invariant.py` 26 passed |
| D10 runner·owner worker 리댁션 미설치 | Minor | **수정**(runner 회귀 테스트 잔여) | `mbnas_runner/__main__.py:25-41,72`, `owner_worker/main.py:113-128`; owner worker 테스트 2건; runner는 `C05_FIX1D/scratch_verifications.txt`(d10, lastResort 대조)와 E12 probe로만 확인됨 |

새 결함: 없음(Blocker/Critical/Major 0).

참고(점수 무관, Minor 관찰)
- O1. `docs/public/SECURITY_MODEL.md:71`에 "누출 검사(T8)는 검증 예정"이라는 문장이 남아 있다. 최종 ZIP이 T8 최종 재실행 전에 빌드돼서 생긴 순서상 지연으로 보인다. 결과보다 보수적으로 쓴 문장이라 위험은 없다. 다음 빌드에서 "T8 PASS(최종 ZIP 기준)"로 갱신하기를 권한다.
- O2. runner 진입점 `install_redaction()`에는 유지되는 회귀 테스트가 없다. `tests/public/runner`에 E12와 같은 형태의 테스트(새 인터프리터, lastResort 포함)를 추가하면 D10 근거가 scratch에 의존하지 않게 된다.
- O3. T8 /proc 정규화 후에도 `OLDPWD`가 남는다. 고객 셸의 cwd이고 owner 데이터는 아니다(7V_FINAL/T8 README의 Minor note). `start.sh`에서 `unset OLDPWD`를 하면 없앨 수 있다.
- O4. admin 토큰은 runner 라우트에서 아무 runner로 행동할 수 있다. `auth.py:9,36,186-187`에 문서화된 설계이고 기본 TTL은 24 h다. EVAL_1과 같다.

## 합계와 판정

| 기준 | EVAL_1 | EVAL_2 | 앵커 |
|---|---|---|---|
| scope_tls | 20 | **25** | 증거·명령·exit code 모두 존재, 재실행 확인 |
| ticket_expiry | 20 | **25** | 위와 같음 |
| yaml_operator_resource | 20 | **25** | 위와 같음 |
| path_error | 15 | **25** | 위와 같음(D1 수정, T8 PASS) |
| **합계** | 75 | **100/100** | **PASS**: 90 초과, Blocker 0 / Critical 0, 필수 시험 전부 통과 |

## 확인 못 함
- 호스트 간 실제 TLS 경로(원격 runner↔core, gateway↔core, nginx 종단): 테스트와 probe는 loopback HTTPS와 사설 CA까지만 확인했다. 문서도 "검증 예정"이다.
- host_data 시험(실제 env 비밀 grep): 실행하지 않았다. 비밀 값을 읽지 않기 위해서다.
- T8 런타임 캡처 재실행: 프로젝트의 최종 T8 결과를 열람하고, health 누출은 in-process probe로 재확인했다. 실제 프로세스 캡처를 내가 다시 돌리지는 않았다.
- 사용자 git index·이력의 `.pyc` 정리(D7 잔여): 사용자 조치 사항이다. 평가자는 git을 조작하지 않았다.
- 사용자 네임스페이스가 없는 호스트의 sandbox 폴백(`setsid+rlimits`): 이 호스트에서는 실행되지 않았다.

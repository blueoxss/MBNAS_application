# C12 EVAL_1 — 운영·최종회귀 (독립 평가 1회차)

- 기준셋: C12 v1.0 (`docs/internal/criteria/C12.json`). 점수는 평가자 의견이며 판정은 설계자가 한다.
- 평가자: claude-opus-5-5 (Opus 5.5), 독립 평가자 — 채점만 한다.
- 시각: 2026-10-28 08:09–08:2x KST.
- tree hash: `runtime/venv/bin/python -B scripts/dev/source_tree_hash.py` (mbnas-source-tree/2, 221 files)
  = `sha256:e220e72137165c57b08cd24388c11d64762b689b376a17911a774b8a3bb45236` (08:09, 08:20 같음). 지정 명령의 `| tail -3`은
  JSON 닫는 괄호만 출력하므로 같은 명령의 `sha256` 필드와 `--digest-only` 값을 적었다.
  - 최종 ZIP 트리: owner ZIP `79cce02a…`의 `BUILD_INFO.json` `source_tree.sha256` = `sha256:5a7bc592…`(221 files) = C01/C03–C06 iter2의 입력.
  - 현재 트리와의 차이: owner ZIP 안 패키지 파일 221개와 작업 트리 221개를 파일별 sha256으로 비교했다. 차이는
    `mbnas_core/src/mbnas_core/runner_api/routes.py` 1개다(ZIP `ef331bdd…`, 트리 `1cbcc212…`, mtime 08:06:54 = 수정 3 / DD-A64).
  - 4패키지(contracts, model_runtime, runner, server) 해시를 다시 계산하면 `sha256:83af6d5f…`이고, public/registration ZIP의 BUILD_INFO와 같다.
    따라서 수정 3은 public/registration ZIP에 영향이 없다.
- 평가 중 변경: 수정 3이 반영되는 중이었다(routes.py 08:06:54, 새 테스트
  `tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py` 08:11:16). REVIEW_LOG에는 08:20:54에 C02 iter1(:23)이 추가되었고, 이 평가에 반영했다.
- 경계: GPU 미사용(`CUDA_VISIBLE_DEVICES=""`). 프로젝트 파일 중에는 이 파일만 썼다. pytest basetemp와 산출물은 세션 scratchpad `wp3/c12/`에 있다.

**결론: 70/100, 미통과**(>90 미달). Blocker 0, Critical 0, Major 3, Minor 7.
감점의 대부분은 문서 연결과 회귀 순서에서 나왔다. 운영 동작 자체는 다시 실행해 모두 통과했다.

| 기준 | 점수 | 기준점 |
|---|---|---|
| ops_failure | 20 | 20 (T5 최종 로그에 runner 쪽이 없음. 근거는 다른 곳에 있으나 인용되지 않음) |
| model_routing | 20 | 20 (기록끼리 일치하고 미지원 ID 없음. MODEL_ROUTING 표가 낡았고 일부 라운드 기록이 빠짐) |
| evidence_complete | 15 | 15 (교차표 T1–T8 중 T1·T2·T3·T7 4개가 "확인 못 함") |
| regression_11 | 15 | 15 (기록된 회귀는 수정 3 이전 트리에서 11셋 중 6셋뿐. C07–C11 기록 없음. 수정 3 이후 회귀 미실행) |

---

## 1. ops_failure — 20/25

**근거(열람)**
- T5 최종 core 종료(public ZIP `ce34b821…`, image `403a1b43…`):
  - `evidence/T5/rerun/t5f_console.txt:52-57`: core_stop은 자기 pid 파일(owner.pid, core.pid)에 SIGTERM을 보내 stopped, exit=0.
  - `:58-61`: harness coredown exit=0. `:64-68`: docker stop exit 0, containers left [].
  - `t5f_coredown.txt:1`:
    - health 200 `degraded`, `core: unreachable`, warning `core: CORE_UNAVAILABLE`.
    - `POST /protocols/validate` 503 `{code CORE_UNAVAILABLE, location core}`. `valid` 키가 없어 fallback이 없다. `Retry-After: 5`.
    - `GET /runs` 503.
  - `run/t5f_gateway_container.log:75-80`: "core call GET /health failed: ConnectError (request_id=t5f-225fea6a…)" 다음에 access status=200, validate status=503, runs status=503. `run/t5f_http.log:64-66`과 같다.
  - `T5/RERUN_README.md:48`.
- runner 쪽(최종 코드):
  - 최종 owner ZIP `79cce02a…`의 T6 integration 로그 `runtime/test_root/t6/final/mbnas owner final/mbnas_application/runtime/logs/verify/20261027T221827Z-owner/integration_smoke.txt`:
    - `:76-80`: core stopped (exit -15) → GW validate/runs 503.
    - `:129-130`: runner "claim failed: POST /claim: ConnectError … (after 6 attempts)", "deregister failed … (after 2 attempts)". 명시적으로 대기·실패하고 fallback이 없다.
    - 이 실행은 `verify_owner.json` integration 17/17, exit 0이다.
  - mbnas_runner wheel `bd74a23c…`(BUILD_INFO 기준)는 세 ZIP에서 같으므로 T5 이미지의 runner 코드와 같다.
  - run 1(구 ZIP): `evidence/T5/run/t5c_runner_container.log:7-9`도 같은 줄을 담고 있다.
- stop.sh 자기 pid만:
  - `scripts/stop.sh:69-107` 신원 확인: marker가 argv basename이고, argv가 ROOT 아래에 있거나 cwd == ROOT. 공유 블록이다.
  - `:155-163`: 남의 pid는 신호 없이 pid 파일만 지운다.
  - `WP7/7V_FINAL/T6/{owner,public,reg}_stop.txt` exit 0, `summary_down.json`, `summary_reinvoke.json`(잔여 프로세스 0).
  - T4 rerun stop exit 0(`T4/RERUN_README.md:30`).
- doctor --strict: `WP7/7V_FINAL/T6/owner_doctor_strict.txt` PASS=28 WARN=0 FAIL=0 exit=0, `public_doctor_strict.txt`·`reg_doctor_strict.txt` 23/0/0 exit 0.

**재실행**
1. `CUDA_VISIBLE_DEVICES="" runtime/venv/bin/python -B -m pytest tests/public/ops -m "not gpu and not live and not legacy_records and not host_data" -p no:cacheprovider -v -rfEs --timeout=180 --basetemp=<scratch>/pt_ops`
   → **exit 0, 59 passed, 2 deselected(live), 35.3 s** (23:10:44Z–23:11:19Z, 현재 트리 e220e721).
   - start/stop 11/11 PASS: `test_stop_never_signals_a_foreign_pid`, `test_pid_of_a_process_without_cmdline_is_not_ours`,
     `test_start_drops_a_stale_pid_file`, `test_stop_escalates_to_sigkill_and_reaps_descendants`, `test_runner_needs_a_reachable_core`,
     `test_start_stop_cycle_from_external_cwd` 등.
   - static: `test_no_eval_no_pkill_no_killall[stop]`, `test_identity_block_is_shared_by_start_and_stop`.
   - doctor: `test_doctor_reports_root_and_strict_exit`(WARN 상태에서 strict → 1).
2. `pytest tests/public/server/test_wp3_gateway_relay.py::test_core_down_health_degraded_and_relays_503_without_fallback tests/public/runner/test_runner_daemon_unit.py tests/public/server/test_health_allowlists_split.py`(같은 옵션)
   → **exit 0, 25 passed, 44.1 s**. gateway는 core down이면 503이고 `valid` 키가 없으며, runner client는 재시도 후 `CoreUnavailable`을 낸다.
3. 위 T5 core-down 로그 줄과 T6 owner integration의 runner 줄을 직접 열람했다.

**공백**
- (m1) T5 최종 기록은 gateway 쪽만 보여 준다.
  - runner 컨테이너 로그(`run/t5f_runner_container.log:7`)는 23:00:33Z에서 끝난다. core 정지는 23:01:40Z, 로그 수집은 23:01:54Z다.
  - 첫 "claim failed"는 6회 재시도, 약 15 s 뒤에 나오므로 수집 시점에는 아직 없었다.
  - runner 근거는 위 T6 integration 로그에 있지만 T5 README와 VALIDATION_REPORT 어디에서도 인용되지 않는다.
- (m2) `scripts/start.sh:348,351`은 여전히 호출자의 `OLDPWD`를 서비스 환경에 넘긴다. P-final의 Minor이며 1.5.1로 미뤄져 있다. stop/start 스크립트 mtime은 10-27 13:50으로 변경이 없다.
- (m3) `scripts/stop.sh:170-178`: SIGKILL 단계에서 먼저 모아 둔 자손 pid를 신원 재확인 없이 kill한다. pid 재사용 창은 이론적이다.
- 수정 3은 gateway, runner, scripts를 바꾸지 않는다. 그래서 이 기준의 근거는 수정 3 이후에도 유효하다(core 종료 경로 불변).

## 2. model_routing — 20/25

**근거(열람)**
- `MODEL_ROUTING.md:5-10`:
  - 요청은 `fable5.1 ultra`, 세션은 `claude-fable-5-1`이다.
  - `ultra`는 이 환경에 노출된 ID가 아니므로 별칭으로 기록하고 `HIGH_MODEL_REVIEW_PENDING`으로 두었다(`ACCEPTANCE_MATRIX.md:14`).
  - `:8` 하위 모델: sonnet=claude-sonnet-5-5, opus=claude-opus-5-5, haiku=claude-haiku-5-5.
  - `:12` 규칙: 라운드마다 (requested, resolved id, mode, role)를 REVIEW_LOG에 적는다.
- REVIEW_LOG 23항목 모두에 requested_model, resolved_model, mode가 있다. 대조 결과:
  - `:1`(WP-0, claude-fable-5-1) = `MODEL_ROUTING:17`.
  - `:2`(WP-1 opus A,B,C,D,G,V / sonnet E,F,H) = `MODEL_ROUTING:20`.
  - 기준셋 라운드 `:7,8,10,11,13,17-21,23`은 "evaluator opus; designer adjudication"으로 적혀 있다. 평가 파일 헤더도 같다:
    C01 EVAL_1/2:4, C03 EVAL_1/2:4, C04 EVAL_1/2:4, C05 EVAL_1/2:4, C06 EVAL_1/2:6이 모두 "claude-opus-5-5"다.
  - 모순은 0건이다.
- ID 유효성:
  - `claude-fable-5-1`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`는 모두 claude-api 스킬의 모델 표(2026-10-06 캐시)에 있다.
  - 이 평가자의 런타임 ID는 claude-opus-5-5이고(시스템 프롬프트), "evaluator opus"와 맞는다.
  - `ultra`는 요청 별칭으로만 나온다(ACCEPTANCE_MATRIX:14, MODEL_ROUTING:5-7,10,17-18, REVIEW_LOG:1-2). resolved에는 한 번도 없다. 미지원 ID 주장은 0건이다.

**재실행**: REVIEW_LOG를 JSON으로 파싱했다(23항목, 필드 점검). docs/internal의 모델 ID를 grep했다(고유 4개: fable-5-1 ×13, opus-5-5 ×3, sonnet-5-5 ×3, haiku-5-5 ×1).

**공백**
- `MODEL_ROUTING.md:14-20` "실제 사용 기록"이 WP-1에서 멈춰 있다. `:19`에 "WP-1 구현(예정)"이 남아 있고, 파일 mtime은 10-26 19:35다.
- REVIEW_LOG에 라우팅 기록이 없는 라운드:
  - WP-4A, 5A, 5C, 6A, 7A, 7B, 7C, 7D.
  - V4-B(W4 `:9`에서 언급만), P1 1차(03:xx).
  - 수정 1a(DD-A61), 1c(WP7/FIX1C), 2(DD-A62), 2b(DD-A63), 수정 3(DD-A64, 진행 중).
  - `:9`의 "+ fix agents"는 모델을 적지 않았다.
- `:3–:23`의 resolved_model은 별칭 "opus"이고 resolved ID가 아니다. `:9,:12,:14-16,:22`는 "designer review"라고만 적고 ID가 없다.
- TARGET_ROOT에는 하네스가 만든 기록(workflow journal)이 없다. 구현 에이전트의 모델은 REVIEW_LOG의 설계자 진술, 즉 정적 근거에 기댄다. 평가자 라운드만 평가 파일의 자기 보고로 교차 확인된다.

## 3. evidence_complete — 15/25

**근거(검증)**
- ZIP digest: `sha256sum dist/*/*.zip` = `ce34b821…`/`95b541e9…`/`79cce02a…`. 다음 값과 모두 같다:
  `VALIDATION_REPORT.md:30-32`, `dist/*/*.sha256`, REVIEW_LOG:16,22, `T5/rerun/zips_sha256_used.txt`.
- gate 연결:
  - `WP7/7V_FINAL/gate_public.meta`·`gate_owner.meta`에 cwd, 인터프리터, UTC 시작·끝, exit 0, 시간이 있다.
  - `run_gates_final.sh`에 정확한 명령이 있다. 로그 끝줄은 1124 passed, 1569 passed다.
- 로그 hash: owner ZIP `FILE_MANIFEST.jsonl`의 7V_FINAL 65행을 현재 파일 sha와 비교했다.
  - gate_*.txt/.meta, t2_compare.txt, pytest_t2.txt, placement, closure, t2/*는 모두 동일하다.
  - DIFF 2개(`13_package_owner_final.log`, `21_unshare_network_proof.txt`)는 owner staging 이후에 쓰인 파일이라 예상된 결과다.
- 트리 연결: owner BUILD_INFO `source_tree` = `5a7bc592…`, public/registration = `83af6d5f…`(내가 다시 계산한 값과 같음).
- lock: `constraints.txt`와 `requirements-*.txt`가 ZIP 안에 있고(PACKAGE_PROFILE에 나열), FILE_MANIFEST와 payload_sha256이 이를 덮는다.
- T6 `transcript.jsonl`: 46 step마다 argv, cwd, env, exit, seconds, output이 있다.

**공백**
- (M) 교차표 `VALIDATION_REPORT.md`에서 T1(:9), T2(:10), T3(:11), T7(:18)이 여전히 "확인 못 함"이다. T1–T8 시험 8개 중 4개에 상태가 없다.
  - 실행 근거는 모두 다른 곳에 있다(§확인 못 함 목록).
  - `:10`은 같은 표의 `:23`과 모순된다(T2 10/10 PASS).
  - `:18`은 `ACCEPTANCE_MATRIX.md:21`(GPU_PATH_VALIDATED 충족)과 모순된다.
  - 이미 알려진 항목이다: FIX_ROUND_1.md:27,30, REVIEW_LOG:18.
- (m) gate와 트리의 결속이 기록되지 않았다.
  - 7V_FINAL README에 tree hash가 없다(FIX_ROUND_1.md:27 미완).
  - REVIEW_LOG P-final(:16)과 T4/T5 final(:22)에 `inputs` 필드가 없다. WP-8 §2 필수 필드이며 `:4,:5,:6,:9,:12,:14,:15`에도 없다.
  - 그래서 `5a7bc592`는 BUILD_INFO와 mtime 스캔으로 추론했다. 06:43:00 이후 패키지, tests, scripts 중 바뀐 파일은 routes.py와 새 테스트뿐이다.
- (m) `ACCEPTANCE_MATRIX.md:3-15`가 낡았다.
  - OWNER_BUNDLE, PUBLIC_ZIP, REGISTRATION_ZIP, OFFLINE_INSTALL, SOURCE_INDEPENDENCE가 여전히 "미충족"이다. VALIDATION_REPORT에서는 T4/T5/T6/T8/offline이 PASS다.
  - ADMIN_BOUNDARY 행이 VALIDATION_REPORT에 없다. C02 iter1(:23)의 지적과 같다.
- (m) `VALIDATION_REPORT.md:88-162`는 1차 패스 내용이다(T6 실패 1건, T8 "1 leak"). `:72` 말고는 superseded 표시가 없어 현재 상태로 오해할 수 있다.
- TASK_STATE.md는 10-28 04:11 이후 갱신이 없다(P-final, T4/T5 final, 수정 3 미기재).
- T5 rerun 로그(08:04)는 아직 어떤 manifest에도 hash로 고정되지 않았다. 예정된 owner 문서 재빌드에서 고정된다.

## 4. regression_11 — 15/25

**기록된 내용**
- 요구사항: `WP-8_VALIDATION.md:19` "마지막 변경 후 영향 셋·최종 ZIP 재검증(C12 회귀)".
- 마지막 회귀 기록은 P-final(REVIEW_LOG:16, 07:43)이다.
  - 7V_FINAL gate 06:43:25–07:03:05 KST: public 1124/0, owner 1569/0, exit 0.
  - closure 0, placement 221/221, t2_compare 10/10(0 MISMATCH), T2 32 passed.
  - 재현 가능 ZIP, T6/T8/offline PASS.
  - T4/T5 final(REVIEW_LOG:22, 08:08)은 최종 ZIP으로 PASS다.
- `5a7bc592`에서의 재채점:
  - C01 iter2 100(:17)
  - C04 iter2 95 — provisional이다. Major N1이 남아 수정 3과 iter3이 필요하다(:18).
  - C06 iter2 100(:19), C05 iter2 100(:20), C03 iter2 95(:21).
  - C02 iter1 85 FAIL(:23, 08:20, 라운드 4 예정).
- **C07, C08, C09, C10, C11은 라운드 기록이 전혀 없다.** REVIEW_LOG 항목도 `evidence/CRITERIA/C07–C11/`도 없다.
  - WP-8 §2의 순서(C01→C12)와도 어긋난다.
- 위 모든 기록 이후에 마지막 변경이 있었다: 수정 3(DD-A64, DECISIONS.md:80), `mbnas_core/src/mbnas_core/runner_api/routes.py` 08:06:54와 새 테스트 08:11:16.
  - 그 이후의 회귀 라운드는 없다(진행 중이므로 예상된 상태).
  - C02 라운드 4가 그 뒤에 문서와 테스트를 더 바꿀 예정이다.

**재실행**
- tree hash: 5패키지 e220e721, 4패키지 83af6d5f.
- owner ZIP과 트리의 파일별 diff: 221/221이 양쪽에 있고, 다른 파일은 routes.py 1개다.
  diff 내용: claim 반복마다 `sweeper.reclaim(conn, settings, now)`를 먼저 호출하고 `_notify`를 부른다. docstring도 함께 바뀌었다.
- `scripts/dev/check_import_closure.py`(현재 트리): exit 0, ok, violations 0, unresolved 0, forbidden 0.
- ZIP sha256 3개.

**수정 3 이후 다시 실행해야 할 것**(라운드 4 변경이 있으면 그 뒤에)
1. **owner gate 전체**: `pytest tests/owner -m "not gpu and not live and not legacy_records and not host_data" --timeout=180`, 약 15분.
   - `tests/owner/runner_protocol`(T3)과 `test_dd_a64_claim_reclaims_first.py`를 포함한다.
   - 시작과 끝의 tree hash를 .meta에 기록한다.
2. 마지막 편집 뒤 `placement_coverage.py`. closure는 위에서 ok였지만 최종 편집 뒤 다시 돌린다.
3. `t2_compare.py`와 `MBNAS_TEST_T2=1 pytest tests/owner/t2`. DD-A48 c의 "최종 트리" 규칙 때문이다. claim 경로는 T2 시나리오 밖이고 약 3분 걸린다.
4. **owner ZIP 재빌드**: `--reproducible`로 2회 동일하게 만들고, owner canary(0 canary, 0 failing)를 돌린다.
   - 새 digest를 VALIDATION_REPORT와 REVIEW_LOG에 적는다.
   - `BUILD_INFO.source_tree`가 수정 후 트리 해시와 같아야 한다.
5. **재빌드한 owner ZIP으로 T6**: install --offline → doctor --strict → start → verify unit,integration,relocation → stop.
   - integration smoke가 바뀐 claim 경로로 실제 claim을 수행한다.
   - 근거 규칙: VALIDATION_REPORT:86 "code changed → T6".
6. C04 iter3(N1 종결). claim과 과금 경로에 걸린 셋을 다시 확인한다: C03(retry 과금), C11(T4를 `79cce02a`로 인용하는 경우).
7. 권장: 재빌드한 owner ZIP으로 T4의 claim·pause→resume 부분. T4 PASS는 `79cce02a`에 묶여 있다.
   - T5의 host core는 수정 전 owner 트리였다. public 쪽은 불변(4패키지 해시 = BUILD_INFO)이므로 T5 public 결과는 유지된다.
8. 수정 3만으로는 필요 없는 것: public/registration gate와 ZIP. public 패키지와 tests 변경이 없고, public tests는 mbnas_core를 import하지 않는다.
   - 단, C02 라운드 4(docs/public, 테스트 이식)가 들어오면 다음이 필요하다: public/registration ZIP 재빌드, T8 ZIP scan, T6 package suite, 테스트가 바뀌면 public gate.
9. 그다음 C07–C11 라운드를 마치고, REVIEW_LOG에 "C12 regression" 항목(inputs.tree_hash, ZIP digest 포함)을 남긴 뒤 이 기준을 다시 채점한다.

---

## 결함

| # | 심각도 | 위치·소유 | 내용 | 수정 |
|---|---|---|---|---|
| D1 | Major | 설계자 / P 단계 | 마지막 변경(수정 3, routes.py 08:06:54) 이후 회귀 라운드가 없다. owner ZIP `79cce02a`는 수정 전 routes.py(`ef331bdd`)를 담고 있다. 트리 e220e721 ≠ BUILD_INFO 5a7bc592. | §4 목록 1–6을 실행하고 REVIEW_LOG에 기록 |
| D2 | Major | 설계자 / WP-8 §2 | C07–C11 라운드 기록이 0건이다. C02는 85 FAIL, C04는 provisional이다. "이전 11셋 회귀"를 성립시킬 수 없다. | C07–C11 채점, C02 라운드 4, C04 iter3를 마친 뒤 C12 회귀 |
| D3 | Major | `docs/internal/VALIDATION_REPORT.md:9-11,18` (WP-8) | T1·T2·T3·T7이 "확인 못 함"이다. 근거는 있다. `:23`과 ACCEPTANCE_MATRIX:21과 모순된다. | 각 행에 상태, 증거 경로, 명령, exit, tree hash를 채움(목록 아래) |
| D4 | Minor | 7V_FINAL README, REVIEW_LOG :16,:22 등 | gate와 트리 결속(`inputs.tree_hash`)이 기록되지 않았다. | gate .meta에 시작·끝 tree hash, REVIEW_LOG에 inputs |
| D5 | Minor | `ACCEPTANCE_MATRIX.md:3-15` | §15 상태가 최종 PASS들과 맞지 않고, ADMIN_BOUNDARY 행이 없다. | 수정 3 재빌드 후 상태와 근거 링크 갱신 |
| D6 | Minor | `T5/RERUN_README.md`, VALIDATION_REPORT T5 행 | core down 시 runner 쪽 근거가 인용되지 않았다(T5 로그가 첫 claim 실패 전에 수집됨). | T6 integration_smoke.txt:129-130과 run-1 t5c 로그 인용, 또는 coredown 단계에서 20 s 이상 대기 후 수집 |
| D7 | Minor | `scripts/start.sh:348,351` (WP-7A) | `OLDPWD`가 서비스 환경으로 전달된다(미해결, 1.5.1). | exec 전에 `unset OLDPWD` / `env -u OLDPWD` |
| D8 | Minor | `scripts/stop.sh:170-178` (WP-7A) | SIGKILL 단계에서 자손 pid를 재확인 없이 kill한다(이론적 pid 재사용). | kill 직전 ppid 체인 또는 시작시각 재확인, 또는 그룹 kill에 의존 |
| D9 | Minor | `MODEL_ROUTING.md:14-20`, REVIEW_LOG | 사용 기록이 WP-1에서 멈췄다. 4A/5A/5C/6A/7A–7D/V4-B/수정 1a·1c·2·2b의 라우팅 기록이 없다. "opus" 별칭과 "designer review"를 ID 대신 썼다. | 행 추가 또는 포인터 표, resolved_model_id 기재 |
| D10 | Minor | `VALIDATION_REPORT.md:88-162` | 1차 패스 섹션에 superseded 표시가 없다. TASK_STATE.md는 04:11 이후 갱신이 없다. | 섹션 머리에 "history (superseded)" 표시, TASK_STATE 갱신 |

## 합계와 판정
- 20 + 20 + 15 + 15 = **70/100** → **미통과**(조건: >90, Blocker/Critical 0. Blocker 0, Critical 0은 충족).
- 통과 경로:
  - D3·D4·D5·D9를 채우면 evidence_complete와 model_routing은 문서 작업만으로 25가 될 수 있다.
  - D6을 인용하면 ops_failure도 25가 될 수 있다.
  - regression_11은 D1과 D2가 해소되어야 올라간다.

## 확인 못 함
VALIDATION_REPORT에서 아직 "확인 못 함"인 항목과, 다른 곳에 근거가 있는지:

| 위치 | 항목 | 다른 곳의 근거 |
|---|---|---|
| :9 | T1 기준선 | **있음**: `evidence/WP1/pytest_owner_gate.txt`(1116 passed/0 failed, exit 0, 10-26 19:21, 2140 s), `WP1/pytest_public_designer_rerun.txt`(564 passed, exit 0), REVIEW_LOG:2 `T1_baseline`, `WP1/SUMMARY.md` |
| :10 | T2 core 동등성 | **있음**: `evidence/T2/T2_REPORT.md`(10/10), `WP7/7V_FINAL/t2_compare.txt`(10/10, 0 MISMATCH, exit 0, 77 s, 최종 트리), `pytest_t2.txt`(32 passed, exit 0). hash는 owner ZIP manifest로 고정됨. 수정 3 이후 재실행 필요(DD-A48 c) |
| :11 | T3 분산·보안 | **있음**: `evidence/WP2/2V/pytest_runner_protocol.txt`(40 passed, exit 0, 96 s), C04 EVAL_2(T3 id 13/13 매핑, runner_protocol 67 passed @5a7bc592), `7V_FINAL/gate_owner.txt`(runner_protocol 포함 1569/0). claim 경로라 수정 3 이후 재실행 필수 |
| :18 | T7 GPU | **있음**: `evidence/T7/RERUN_README.md`(tree 9dafe4d0, 전 항목 PASS, TensorRT BLOCKED_BY_ENV), REVIEW_LOG:6, ACCEPTANCE_MATRIX:21. 현재 트리는 GPU 재실행 안 됨(DD-A56 c 정책) |
| :151 | `-offline` 변형 ZIP(wheel 동봉) | **없음**: 빌드되지 않음 |
| :211, T5 RERUN_README:60 | 평가 ON PROPOSE_GENERATION의 gateway dataset-summary 경로 E2E | **단위 수준만**: `tests/owner/worker/test_dd_a60_owner_worker.py:201` (owner gate 포함), `WP7/FIX2/EVIDENCE_INDEX.txt:20`. E2E는 GPU fleet가 필요해 없음 |
| :22 | registration offline install | 미시험(`offline_install: false`). 근거 없음 |

이 평가자가 확인하지 못한 것:
- 설계자 런타임 모델 ID(`claude-fable-5-1`): ID 자체는 유효하지만, 내가 확인할 수 있는 것은 내 ID(claude-opus-5-5)뿐이다.
- 구현 에이전트별 실제 모델: TARGET_ROOT에 하네스 기록이 없다.
- 7V_FINAL gate가 정확히 5a7bc592에서 돌았는지: 기록이 없다. BUILD_INFO와 mtime으로 추론만 했다.
- 수정 3의 정확성: 새 DD-A64 테스트와 owner gate는 돌리지 않았다. 반영 중이었고 C12 CPU 예산 밖이다.
- 작업 실행 중 core가 내려갔을 때의 runner 동작(heartbeat 손실): T5와 T6 어디에도 없다.
- C02 EVAL_1 본문: 열람하지 않았다. REVIEW_LOG:23만 사용했다.

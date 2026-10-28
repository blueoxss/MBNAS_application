# C12 EVAL_2 — 운영·최종회귀 (재채점 2회차, 릴리스 세트 = 출하 ZIP 기준)

- 기준셋: C12 v1.0 (`docs/internal/criteria/C12.json`). 앵커는 EVAL_1과 같다(25/20/15/0). 점수는 평가자 의견이고 판정은 설계자가 한다.
- 평가자: claude-opus-5-5 (Opus 5.5), 독립 평가자(EVAL_1 평가자 재개).
- 시각: 2026-10-28 15:51–16:1x KST.
- 평가 대상(릴리스 세트, 코디네이터 지시와 DD-A77): 출하 ZIP 3종. `sha256sum`으로 직접 확인했다.
  - public `bc0500bdfa43ddf5fb74bde4cf51a967e06f45bd2b901ba26da0794c1f0853d3` (2,716,913 B, 476 members, BUILD_INFO r-6ada31a4db777349, 4패키지 트리 `9859adb2…`)
  - registration `dea510dfb54dd85da556ac38423f747a0a7d1afbff7398c501127bd0f8b92f6e` (2,792,584 B, 486, r-63bd409df0a7d79a, `9859adb2…`)
  - owner `9f03c484bbc8c0a2227cc325658892e3efe286d08cca9a1978383158075c2f2a` (85,188,530 B, 3693, r-07c7cef95cd73775, 트리 `sha256:8fc93f2a596891174c2884e1697fd6003377b713102c0b21b01eb45d3f6f7312` 221 files, mbnas_core wheel `2348b6e3…`)
  - 세 ZIP의 공개 패키지 소스 129개는 바이트가 같다(zipfile 대조). public 4 wheel(`a947997e`/`65a13736`/`a8401c99`/`2243162f`)과 운영 스크립트 5종의 sha도 세 ZIP에서 같다. public/registration에 `mbnas_core`는 없다.
- 작업 트리 관찰(결함 아님, DD-A76/DD-A77): `runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`의 값이 계속 바뀌었다.
  - `sha256:4be9f0f79bab0cbb3611c6e0e8aa32f6eb29ced595f93a15b3f8bc3647481336`(15:51) → `78534e68…`(15:55) → `644548cd…`(16:08).
  - 4패키지 해시는 15:51에 `a92d9946…`였다.
  - 15:52에 owner ZIP의 패키지 파일 221개와 비교하면 84개가 달랐다. 표본 2개(`constants.py`, `schema.sql`)는 주석·docstring만 바뀌었다.
  - peer 세션 `etri-hd-d8`의 배포본 정리(REVIEW_LOG 61, 65, 66)로 보인다.
- 작업 트리를 쓴 점검(§7에 자세히): 위 해시·diff 관찰, 운영 스크립트 바이트 대조, 기록 문서 열람만 했다.
  기능 점검(설치·doctor·start/stop·core 정지·외부 pid·ops 시험·verify integration)은 전부 owner ZIP을 새로 추출한 트리와 그 venv에서 실행했다.
- 경계: GPU 계산 없음(`CUDA_VISIBLE_DEVICES=""`), `/data`와 etri_nas 미접근, `PYTHONDONTWRITEBYTECODE=1`과 `-B`, 프로젝트 파일 중에는 이 파일만 썼다.
  추출 트리(7.0 GB)와 pytest basetemp(79 MB)는 끝나고 삭제했다(§8).

**결론: 80/100, 미통과**(>90 미달). Blocker 0, Critical 0, Major 1, Minor 3, Observation 4.

| 기준 | 점수 | 기준점 |
|---|---|---|
| ops_failure | **25** | 25. 최종 owner ZIP 실설치에서 장애·종료·doctor 경로를 모두 직접 재현했고, 프로젝트 증거와 exit code가 모두 있다 |
| model_routing | **20** | 20. 기록끼리 일치하고 미지원 ID가 없다. 남은 것은 행 1개의 낡은 자리표시와 설계자 구현 역할 표기 누락뿐이다 |
| evidence_complete | **20** | 20. 행마다 상태와 증거가 있고, 인용 경로는 모두 존재하며, gates_r4 로그 hash가 ZIP manifest로 고정되어 있다. 라벨 3건이 남았다 |
| regression_11 | **15** | 15. 최종 회귀 기록(gates_r4 + T6 ×3 + T4/T5 a75)은 완전하다. 그러나 기준셋 단위로 릴리스 세트에서 다시 확인된 것은 C02·C07·C08·C09 4셋뿐이고, 영향 셋 C03·C10·C11(그리고 C05·C06)은 확인되지 않았다 |

---

## 1. ops_failure — 25/25

**실설치 재현** (owner ZIP `9f03c484…`를 공백·한글 경로 `…/scratchpad/wp3/c12e2/x/owner 공백 root/mbnas_application`에 새로 추출. 외부 cwd `…/cwd ext`. 포트 core 19700, gateway 19710. core는 `MBNAS_CORE_DEVICE_SOURCE=runners`.)

| 단계 | 명령 | 결과 |
|---|---|---|
| 설치 | `bash <ROOT>/scripts/install.sh --profile owner --offline --wheelhouse <SRC>/build/wheelhouse --python <mbnas310>/python3.10` | exit 0, 90 s(06:56:10Z–06:57:40Z). 5 패키지가 ROOT 안에서 import된다. env 파일 3개 0600. server·runner principal 발급, runner id `runner-ed887a73` |
| doctor --strict (기동 전) | `doctor.sh --profile owner --strict` | exit 0: PASS 26, WARN 2(core 미기동), FAIL 0 |
| start | `start.sh --profile owner --wait-s 120` | exit 0, 14 s. core(HTTP 401 = 인증 필요), owner-worker, gateway(`status=ok core=reachable`), runner 모두 ready |
| doctor --strict (기동 중) | `doctor.sh --profile owner --strict --json …` | exit 0: **PASS 32 / WARN 0 / FAIL 0**. `env:core/owner-worker/gateway/runner`에 PYTHONPATH·PYTHONHOME 없음 |
| 서비스 환경 | `/proc/<pid>/environ`(키 이름만 확인) | 4 서비스 모두 금지 키(OLDPWD, PYTHONPATH, PYTHONHOME, PYTHONSTARTUP, PYTHONUSERBASE) 0개. cwd는 ROOT, `CUDA_VISIBLE_DEVICES=''`. 호출 셸은 cd를 해서 OLDPWD가 있는 상태였다 |
| core 정상 | probe(stdlib 클라이언트, 토큰은 출력하지 않음) | 토큰 없음 → 401 `AUTH_REQUIRED`. health 200 ok/core reachable. validate(빈 protocol) → 422 `VALIDATION_FAILED` location core. runs 200 |
| **core 정지** | `stop.sh --profile owner --role core` | exit 0, 1.1 s: `owner-worker: stopped (pid 280971, SIGTERM)`, `core: stopped (pid 280905, SIGTERM)`. gateway·runner pid 파일은 그대로 남는다 |
| core 정지 중 gateway | probe(07:00:06Z) | health 200 **`degraded`, core `unreachable`, warning `core: CORE_UNAVAILABLE`**, worker_count 0. **validate 503 `CORE_UNAVAILABLE` location core, `Retry-After: 5`, `valid` 키 없음(fallback 없음)**. runs 503. gateway 로그에 요청마다 "core call … failed: ConnectError (request_id=…)"와 access 줄(status=503) |
| core 정지 중 runner | `runtime/logs/runner.log` | **07:00:23Z, 07:00:40Z, 07:00:58Z에 `runner runner-ed887a73: claim failed: POST /claim: ConnectError: [Errno 111] Connection refused (after 6 attempts)`**. 약 17.5 s 간격으로 명시적으로 대기·실패하며 다른 경로로 대체하지 않는다 |
| 외부 pid | 외부 `sleep 300` pid를 `web.pid`에 기록 → `stop.sh --role web` | exit 0, "pid 285404 is not this tree's web (pid reused?): not signalled; stale pid file removed". 프로세스는 살아 있다 |
| 위장 pid | argv0가 `mbnas-gateway`이고 ROOT 밖에 있는 프로세스의 pid를 `gateway.pid`에 기록 → `stop.sh --role gateway` | exit 0, "pid 285406 is not this tree's gateway … not signalled". 위장 프로세스와 실제 gateway(281108) 모두 살아 있다. pid 파일은 복원했고 위장 프로세스는 내가 종료했다 |
| 중복 기동 거부 | `start.sh --profile owner --role gateway` | exit 1 "already running: gateway (pid 281108)" |
| core 재기동 | `start.sh --profile owner --role core --wait-s 60` | exit 0. health 200 ok, worker_count 1, validate가 다시 core까지 도달(422). 재기동 뒤 claim 실패 줄은 없다 |
| 전체 정지 | `stop.sh --profile owner` | exit 0: runner, gateway, owner-worker, core 각각 SIGTERM으로 stopped. `runtime/pids/` 비었음, 서비스 잔존 프로세스 0, 포트 19700·19710 비었음 |
| 로그 비밀 | env 파일의 비밀값 7개를 core/gateway/owner-worker/runner 로그에서 검색 | 0 hits |
| request id 연결 | probe id | core 정상 중 요청은 gateway 1줄 + core 1줄. core 정지 중 validate는 gateway 2줄, core 0줄 |

**ZIP에 들어 있는 시험 재실행**
- ZIP 트리에서 ZIP venv로 실행: `runtime/venv/bin/python -B -m pytest tests/public/ops -m "not gpu and not live and not legacy_records and not host_data" -p no:cacheprovider -v --timeout=180`
  → **exit 0, 65 passed, 2 deselected(live), 52.2 s** (07:00:31Z–07:01:23Z, rootdir = 추출 트리).
  - start/stop 11/11 통과.
  - EVAL_1 이후 추가된 6개도 통과: `test_ops_role_env.py::test_start_removes_the_callers_python_variables_and_oldpwd`, `::test_doctor_fails_a_running_role_that_has_pythonpath`,
    `test_ops_stop_identity.py::test_kill_verified_signals_only_the_recorded_process`, `::test_every_sigkill_target_of_stop_one_is_reverified`,
    `::test_sigkill_reaches_a_descendant_outside_the_process_group`, `test_ops_relocation_origins.py::…`.
- `verify.sh --profile owner --suite integration`(ZIP의 `scripts/dev/wp3_three_process_smoke.py`) → **exit 0, 17/17**, 47 s.
  - `core-down-degraded-503` PASS.
  - runner `claim failed … (after 6 attempts)`와 `deregister failed … (after 2 attempts)`가 나온다.

**프로젝트 증거(열람)**
- T6 owner attempt4(`9f03c484…`): `evidence/T6/rerun2/owner/attempt4_dd_a75/verify_20261028T062616Z-owner/integration_smoke.txt:85`(core-down 503 PASS), `:134-135`(runner claim failed / deregister failed).
  `doctor_owner_strict.txt`, `stop_owner.txt`, `leftover_check.txt`도 있다.
- T5 attempt_dd_a75: `evidence/T5/rerun2/attempt_dd_a75/t5a75_coredown.txt:1-3`(health degraded, validate 503 CORE_UNAVAILABLE, Retry-After 5, exit 0), `run/t5a75_gateway_container.log:63-67`, `t5a75_console.txt:49-53`(자기 pid 파일에 SIGTERM).
- `scripts/start.sh:344,356,360`(OLDPWD·Python 변수 제거). `scripts/stop.sh:111-131`(`proc_starttime`/`kill_verified`), `:134-158`(자손과 시작시각 수집), `:168-176`(그룹 kill 전 리더 시작시각 재확인).

**남은 관찰(감점 없음)**: T5 하네스는 rerun2 세 번 모두 core 정지 후 4–16 s 만에 로그를 수집했다(`t5r2_console.txt:69-80`, `attempt_dd_a73/t5a73_console.txt:64-75`, `attempt_dd_a75/t5a75_console.txt:49-60`).
첫 claim 실패(약 17 s)보다 이르다. runner 쪽 근거는 T6 attempt4(위)와 이 재현이 덮는다. 다만 `T5/RERUN_README.md:66-70`이 인용하는 경로는 대체된 ZIP `79cce02a…`의 `runtime/test_root/t6/final/…`이고, 이 경로는 사용자 정리 승인 대상이다 → R4.

## 2. model_routing — 20/25

**근거**
- `MODEL_ROUTING.md:5-10`: `ultra` 요청은 별칭으로 기록했고 `HIGH_MODEL_REVIEW_PENDING`이다(`ACCEPTANCE_MATRIX.md:16`).
- `:14-57` 행별 표가 REVIEW_LOG 1–70을 덮는다. 대조 결과:
  - 23–30 → :40–41. 31–32 → :42. 33 → :43. 34–37 → :44. 38·46 → :45. 39 → :46. 41 → :47. 40·42 → :48. 43–45·47–48 → :49. 49–54 → :50.
  - 55·56·57·59 → :51–52. 58·60·62·63 → :53. 64·67–70 → :55. 65·66 → :56.
  - REVIEW_LOG 61(peer 세션의 clean-1 행)은 :56에서 언급만 하고, peer 모델은 "이 세션에서 확인 못 함"으로 적었다. 정직한 처리다.
- 모델 ID는 4개(`claude-fable-5-1`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`)이고 모두 유효하다(EVAL_1 근거와 같음).
  평가 라운드는 평가 파일 머리의 자기 보고와 일치한다. 이 평가자의 런타임 ID도 claude-opus-5-5다.

**공백(라벨 수준)**
- `MODEL_ROUTING.md:51`(수정 라운드 7)이 아직 "10-28 13:37– … 완료 시 REVIEW_LOG fix-7 행"으로 진행 중처럼 적혀 있다. fix-7 행(REVIEW_LOG 55, 56, 59)과 완료 기록(:52–53)은 이미 있으므로 낡은 자리표시다.
- 설계자가 직접 구현한 편집이 MODEL_ROUTING 역할 열에 일부만 있다.
  - DD-A73 core 패치는 :48에 있다.
  - DD-A72 시험 수정(REVIEW_LOG 38 "designer review + test fix")이 :45에 없다.
  - DD-A74 allowlist 편집과 시험 실행(REVIEW_LOG 51–53 "claude-fable-5-1 (designer edit + test run)")이 :50에 "판정"으로만 적혀 있다.
  - 이 편집들(DD-A69 텍스트, DD-A72, DD-A73, DD-A74)은 사용자 지시 "구현·실행은 하위 모델"과 다른데, 예외를 기록한 DD가 없다. REVIEW_LOG에는 정직하게 남아 있다.
- `:54`(이 C11/C12 라운드)는 설계자의 진행 중 행이다. 이 보고 뒤에 닫아야 한다. 지금 열려 있는 것은 정상이다.

## 3. evidence_complete — 20/25

**검증**
- VALIDATION_REPORT 표 36행(12–47)을 모두 파싱했다.
  - T1(:12), T2(:13), T3(:14), T7(:41)이 PASS로 채워졌다. ADMIN_BOUNDARY(:47)는 BLOCKED_BY_ENV이고, 행마다 환경 열이 있다.
  - 인용된 증거 경로는 전부 존재한다. 중괄호 경로(`owner_c/{build,canary,t8}`, `owner_d/…`)와 상대 하위 경로(`T6/rerun2/owner/attempt1…4*`)도 직접 확인했다.
- gates_r4의 `.meta` 13개는 모두 cmd, cwd, 인터프리터, 시작 tree(`8fc93f2a…`)와 public4(`9859adb2…`), exit, 시간, end를 기록한다.
  `gate_owner.meta`에는 "tree after the gates: 8fc93f2a…"도 있다.
  - 결과: public 1190 passed(exit 0, 367 s), owner 1686 passed(exit 0, 936 s), T2 32 passed, t2_compare exit 0, snapshot_compare ok(603/603), placement 221/221, closure exit 0.
- **로그 hash 고정**: gates_r4 최상위 파일 31개가 owner ZIP `9f03c484…`의 FILE_MANIFEST(3692행)에 들어 있고, 현재 파일과 31/31 바이트가 같다.
  ZIP sha는 직접 확인했으므로 gate 로그 → manifest → ZIP sha로 이어진다.
- BUILD_INFO: owner `source_tree` = `8fc93f2a…`(221), public/registration = `9859adb2…`(129).
  `PFINAL2/gates_r4/public_registration_member_check.txt`는 public/registration ZIP 멤버 차이 0을 기록한다. 내 대조에서도 세 ZIP의 공개 패키지 소스가 같았다.

**공백(라벨 3건)**
- `VALIDATION_REPORT.md:15-16`의 T4/T5 첫 행이 여전히 "FINAL owner ZIP `79cce02a…`", "FINAL public ZIP `ce34b821…`"로 시작한다.
  본문에는 재실행이 P-final-2 몫이라고 적혀 있지만, 다른 이력 행(T6/T8/offline/regression, :37–46)처럼 "(history … superseded)" 표시는 없다.
- `ACCEPTANCE_MATRIX.md:1` 머리가 "갱신 2026-10-28 15:50 … P-final-2 gates_r2·DD-A73 owner ZIP 반영"이다. 본문(16:00 수정)은 `9f03c484…`·gates_r4 기준이다.
- REVIEW_LOG 47·67의 C07 점수 키 {block_table, revisions_diff_compare, real_stack, dist_shipping}와 49의 C10 키 {a1..a4}가 구속 앵커 id와 다르다.
  구속 앵커는 `criteria/C07.json`(gene_links, block_table, yaml_original_executed, compare_onoff_baseline)와 `C10.json`(naming_allowlist, public_private, final_unpack_run, manifest_checksum)이다. C07 EVAL_2 본문은 구속 id를 쓴다.
- (참고, 감점 없음) VALIDATION_REPORT(15:50)에는 작업 트리가 검증 트리와 달라졌다는 주석이 없다. DD-A77과 REVIEW_LOG 65–66에만 있다.

## 4. regression_11 — 15/25

**최종 회귀 기록(ZIP 트리 기준)과 트리 계보**: 각 변경은 다시 게이트되었다. gate `.meta`와 REVIEW_LOG로 확인했다.

| 트리 | 변경 | 게이트 | 결과 |
|---|---|---|---|
| `f00987be…` | 라운드 4, 수정 3/5 이후 | `PFINAL2/gates` 01:19–01:44Z, 전후 같은 트리 | public 1190/0, owner 1601/0 |
| `1b4bd1a4…` | DD-A73(+DD-A72 시험) | `gates_r2` 02:53–03:19Z, 전후 같음 | 1190/0, 1604/0 |
| `26e02259…` | DD-A75 F1 | `gates_r3` 05:12–05:37Z. owner 도중에 F1b가 반영되어 끝 트리가 `8fc93f2a`(혼합 실행, 기록대로) | 1190/0, 1663/0 |
| **`8fc93f2a…`** | DD-A75 F1b | **`gates_r4` 05:47–06:12Z, 전후 같음** | **1190/0, 1686/0** |

- 그 위에 `9f03c484…`의 P1 phase B(REVIEW_LOG 63: 2회 빌드 동일, FILE_MANIFEST 0 불일치, canary 0, T6 attempt4 unit 2829/0, integration 17/17, relocation 8/8, package 3692/3692)가 있다.
- P2 T4/T5 attempt_dd_a75(REVIEW_LOG 62)와 T6 rerun2 public/registration(`bc0500bd`/`dea510df`, 멤버가 8fc93f2a 트리와 같음)도 있다.
- 이 평가자가 9f03c484로 직접 재현한 §1의 결과가 더해진다.
- "최종 회귀 기록"은 ZIP 트리 `8fc93f2a…`의 gates_r4다. 작업 트리(15:40 이후 DD-A76 정리)의 기록이 아니다.

**기준셋 단위(REVIEW_LOG 최신 행과 그 뒤의 변경)**

| 셋 | 최신 기록 | 기준 | 그 뒤 범위 안 변경 | 릴리스 세트 확인 |
|---|---|---|---|---|
| C01 | 100 (17, 07:53) | `5a7bc592` | 패키지 배치·closure는 gates_r4 정적 게이트로 재실행(closure 0, placement 221/221, snapshot 603/603) | 셋 재채점 없음(기계 점검은 재실행됨) |
| C02 | 95 (64, 15:58) | `9f03c484`/`8fc93f2a` | — | **확인** |
| C03 | 95 (21, 08:03) | `5a7bc592` | **DD-A73**(REVIEW_LOG 40·42 "C11/C03 fix-6": 생성/탐색 후보 학습 선택의 구조 검사 = structure_gate) | **없음** |
| C04 | 100 (34, 10:25) | `f00987be` | DD-A73, F1/F1b는 lease/claim/commit 범위 밖. runner_protocol 시험은 gates_r4 포함 | 셋 재채점 없음(영향 낮음) |
| C05 | 100 (20, 08:00) | `5a7bc592` | 수정 5(runner 프로토콜 1.1.0), DD-A73(yaml 연산자 자원), F1/F1b(경로 스크럽), 라운드 4 start.sh 환경 | 없음(시험은 gates_r4에서 통과) |
| C06 | 100 (19, 08:00) | `5a7bc592` | 수정 5(스키마 v6·retention prune), F1/F1b(투영) | 없음(시험은 gates_r4에서 통과) |
| C07 | 100 (67·70, 16:00) | `9f03c484` | — | **확인** |
| C08 | 95 (69, 16:00) | `9f03c484`/`bc0500bd` | — | **확인** |
| C09 | 100 (68·70, 16:00) | 세 ZIP | — | **확인** |
| C10 | 100 (49, 13:26) | `53e6a1f0`/`1b4bd1a4` | **owner ZIP 재빌드 `9f03c484`**(manifest 451 추가/4 삭제/21 변경), DD-A74 allowlist. `ACCEPTANCE_MATRIX.md:15`도 "9f03c484…/CLEAN1 재확인 필요"라고 적는다 | **없음** |
| C11 | 75 FAIL (27, 08:30) | `e220e721` | 이후 모든 변경. 재채점 진행 중(MODEL_ROUTING :54) | **없음** |

정리하면 최종 게이트와 ZIP 재검증은 완전하다. 그러나 이전 11셋 중 릴리스 세트에서 다시 확인된 것은 4셋이다. 영향이 분명한 C03·C10·C11과 범위가 겹치는 C05·C06은 확인되지 않았다.
REVIEW_LOG에는 아직 "C12 regression" 행이 없다. 핵심의 절반 정도만 증거가 있으므로 앵커 15다.

**25로 가는 데 필요한 것**
1. C11 재채점이 DD-A75 세트에서 PASS.
2. C10 확인(9f03c484의 naming/allowlist/manifest/unpack-run. P1 phase B 기록을 C10 앵커로 판정).
3. C03 확인(DD-A73 이후 structure_gate. T4/T5 a73·a75의 ETRI 생성 후보 200→COMPLETED, gates_r2–r4 t2_compare).
4. C05·C06은 한 줄 확인, 또는 "영향 없음" 근거를 기록.
5. 위 결과를 묶은 "C12 regression" REVIEW_LOG 행(inputs: ZIP sha 3개, tree `8fc93f2a…`).

## 5. EVAL_1 결함 상태

| # | EVAL_1 내용 | 상태 | 근거 |
|---|---|---|---|
| D1 Major | 수정 3 이후 회귀 없음 | **FIXED** | 수정 3이 `f00987be` gates(owner 1601/0)와 이후 모든 게이트에 포함됨. C04 iter3 100(REVIEW_LOG 34) |
| D2 Major | C07–C11 기록 0건 | **부분 FIXED** | C07 100, C08 95, C09 100, C10 100, C02 95 기록. C11은 iter1 75 FAIL, 재채점 진행 중 → R1에 포함 |
| D3 Major | VALIDATION_REPORT T1/T2/T3/T7 "확인 못 함" | **FIXED** | `VALIDATION_REPORT.md:12,13,14,41` PASS, 증거 경로 존재 확인 |
| D4 Minor | gate↔tree 결속 미기록 | **FIXED** | PFINAL2 gates/gates_r2–r4의 모든 `.meta`에 tree 해시. 7V_FINAL 이력 행(:46)에 "추론값" 명시 |
| D5 Minor | ACCEPTANCE_MATRIX 낡음 | **FIXED(본문)** | `:7-18` 상태별 근거와 ADMIN 행. 머리 라벨만 낡음(R3) |
| D6 Minor | runner 쪽 core-down 미인용 | **FIXED** | `T5/RERUN_README.md:62-73` 인용, T6 attempt4 `integration_smoke.txt:134-135`(docs 증거), 이 평가에서 재현. 잔여 관찰 R4 |
| D7 Minor | start.sh OLDPWD | **FIXED** | `start.sh:344,356,360`. 실설치 4 서비스에서 금지 키 0. 시험 통과 |
| D8 Minor | SIGKILL 전 신원 재확인 없음 | **FIXED** | `stop.sh:111-131,168-176`. 시험 3개 통과 |
| D9 Minor | MODEL_ROUTING 사용 기록 WP-1에서 멈춤 | **FIXED(대부분)** | `:21-57` 행별 표. 잔여 R2 |
| D10 Minor | 1차 패스 superseded 표시, TASK_STATE | **FIXED(대부분)** | 이력 행 :37–46 표시, TASK_STATE 15:52까지 기록. 잔여: T4/T5 첫 행(R3), `TASK_STATE.md:3` 머리 "갱신: 2026-10-26 16:2x" |

## 6. 결함(EVAL_2)

| # | 심각도 | 위치·소유 | 내용 | 수정 |
|---|---|---|---|---|
| R1 | Major | 설계자 / WP-8 §2 | 릴리스 세트에서 영향 셋 재확인이 안 됐다: C11(진행 중, 마지막 75 FAIL), C10(owner ZIP 변경 후 미확인), C03(DD-A73 후 미확인). C05·C06은 범위 안 변경 후 미확인. "C12 regression" 행 없음 | §4의 1–5 |
| R2 | Minor | `MODEL_ROUTING.md:51,45,50,54` | :51 낡은 진행 중 자리표시. DD-A72/DD-A74의 설계자 구현 역할 누락. 설계자 구현 예외(DD-A69/72/73/74)를 기록한 DD 없음. :54는 마감 필요 | :51을 완료 행으로 바꾸고, :45·:50에 "구현: claude-fable-5-1(설계자)"를 넣고, 예외를 DD로 기록, :54 마감 |
| R3 | Minor | `VALIDATION_REPORT.md:15-16`, `ACCEPTANCE_MATRIX.md:1`, REVIEW_LOG 47·49·67 | T4/T5 첫 행의 "FINAL" 라벨(대체된 ZIP). 매트릭스 머리 라벨 낡음. C07/C10 점수 키가 구속 앵커 id와 다름 | "(history — superseded by attempt_dd_a75)" 표시, 머리 갱신, 점수 키를 criteria JSON id로 |
| R4 | Minor(관찰) | `T5/RERUN_README.md:66-70`, T5 하네스 | runner 쪽 인용이 정리 예정인 `runtime/` 경로(대체 ZIP 79cce02a)를 가리킴. T5 하네스는 첫 claim 실패 전에 로그를 수집함 | docs 증거 `T6/rerun2/owner/attempt4_dd_a75/verify_20261028T062616Z-owner/integration_smoke.txt:85,134-135`로 인용 교체, 또는 coredown 단계에서 20 s 이상 대기 후 수집 |
| O1 | Observation | 작업 트리(DD-A76/77) | 트리 해시 4be9f0f7 → 78534e68 → 644548cd. ZIP 대비 84 패키지 파일 텍스트 상이(표본은 주석만). 릴리스 세트의 결함은 아니다. 다시 게이트하기 전에는 재빌드 금지(DD-A77) | 사용자 결정(ⓐ/ⓑ, DD-A77) |
| O2 | Observation | REVIEW_LOG 61 vs 63/65/66 | peer 행 61은 "9f03c484 … superseded before validation", 이 세션 행 63·66은 "code-validated baseline". 두 설계자 세션이 같은 기록을 쓰고 있다(66 residual) | 인계 시 61의 범위를 주석으로 명시(CLEAN1 배포본 기준의 대체) |
| O3 | Observation | ZIP의 verify integration(F2 smoke) | `CUDA_VISIBLE_DEVICES=""`에서도 smoke core가 NVML 장치 목록을 읽기 전용으로 조회해 "FLEET MODE GPU"(host-gpu-0/1)로 실행했다. job은 runner에서 cpu로 매핑. 내 프로세스의 GPU 계산 0(nvidia-smi compute apps는 캠페인 sg26뿐). T6 attempt4도 같다 | 필요 시 smoke에 `MBNAS_CORE_DEVICE_SOURCE=runners` 옵션(1.5.1) |
| O4 | Observation | runtime/ 인용 | `T5/RERUN_README.md`가 인용하는 `runtime/test_root/t6/final/…/integration_smoke.txt:129-130`은 현재 존재한다. runtime/test_root는 사용자 정리 승인 대상이라 사라질 수 있다 | R4와 같음 |

## 합계와 판정
- 25 + 20 + 20 + 15 = **80/100** → **미통과**(조건: >90, Blocker/Critical 0. Blocker 0, Critical 0은 충족).
- 통과 경로:
  - R2·R3를 고치면 model_routing과 evidence_complete가 25가 된다(문서 작업만).
  - R1이 해소되면 regression_11이 25가 된다.
  - 그러면 100이다. R1만 남으면 25+25+25+15 = 90으로, 여전히 >90에 미달한다.

## 7. 작업 트리를 쓴 점검과 영향
- 이번 회차:
  - (a) tree 해시 관찰 3회.
  - (b) owner ZIP 패키지 소스와 작업 트리의 파일별 diff(84개 상이)와 표본 diff 2개.
  - (c) 운영 스크립트 5종의 ZIP↔트리 바이트 대조(15:5x 기준 동일).
  - (d) 기록 열람: REVIEW_LOG, DECISIONS, VALIDATION_REPORT, ACCEPTANCE_MATRIX, MODEL_ROUTING, TASK_STATE, FIX/evidence 파일.
  - 이 가운데 (a)–(c)는 관찰용이고 점수 근거가 아니다.
  - (d)의 기록은 작업 트리에만 있고 두 설계자 세션이 함께 쓴다(O2). 내가 읽은 기록은 16:08 시점의 것이다.
  - gates_r4 증거는 owner ZIP manifest로 31/31 바이트 동일을 확인했으므로 드리프트 영향이 없다.
  - T6 attempt4와 T4/T5 attempt_dd_a75 증거는 ZIP 빌드 후에 쓰여 manifest 고정이 없다. 열람한 그대로다.
- 소스 트리의 `runtime/venv` python은 해시 도우미 실행과 `-I` zipfile 읽기에만 썼다. 드리프트 영향이 없다.
- 기능 점검(§1 전부, ZIP ops 시험 65, verify integration 17/17)은 추출한 ZIP 트리와 그 venv에서만 실행했다. 드리프트 영향이 없다.
- EVAL_1(08:09–08:2x)은 전부 작업 트리 `e220e721`에서 실행했다. DD-A76 정리 이전이라 영향이 없으나 이번 회차로 대체된다.

## 8. 정리
- 삭제: 추출 트리 `…/scratchpad/wp3/c12e2/x/`(7.0 GB, 설치 venv 6.7 GB 포함), pytest basetemp `…/c12e2/pt_zip_ops`(79 MB), `…/c12e2/zipsrc`, EVAL_1의 `…/wp3/c12/pt_ops`·`pt_cd`.
- 서비스 잔존 프로세스 0, 포트 19700·19710 비었음을 확인했다.
- 남긴 것(작은 파일): `…/scratchpad/wp3/c12e2/`의 명령 출력, probe jsonl, 비밀 0건을 확인한 서비스 로그 사본(`logs_copy/`), ZIP verify integration 로그 사본.

## 확인 못 함
- public/registration ZIP은 내가 직접 설치하지 않았다. 대신 세 ZIP의 wheel·운영 스크립트·공개 소스가 바이트 동일함을 확인했고, P1 T6 rerun2 증거(doctor 24 PASS/1 WARN, unit 761/0)를 열람했다.
- 설계자와 peer 세션 `etri-hd-d8`의 런타임 모델 ID: 확인할 수 있는 것은 내 ID(claude-opus-5-5)뿐이다.
- 84개 파일 드리프트가 모두 주석·docstring뿐인지: 표본 2개만 직접 봤다. 전체 AST 동일은 C02의 비교(REVIEW_LOG 65)에 기댄다.
- C11 재채점 결과: 16:08까지 REVIEW_LOG에 없다.
- 작업 실행 중 core가 내려갔을 때의 runner 동작(heartbeat 손실 중 job): 재현하지 않았다. 유휴 claim 경로만 재현했다.
- GPU 경로(T7)는 트리 `9dafe4d0…` 기준 기록만 있고, 현재 세트는 CPU로만 검증되었다(GPU 사용 금지).

# C09 설치 폐쇄성 — 독립 평가 EVAL_1

- set: **C09** (`docs/internal/criteria/C09.json` v1.0)
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자. 채점만 하고 결정은 설계자가 한다.
- time: 2026-10-28T08:10+09:00 시작, 08:27+09:00 재실행 종료, 이후 보고서 작성
- tree hash (mbnas-source-tree/2, 221 files). 지시된 `| tail -3`은 JSON의 닫는 괄호만 출력하므로 `sha256` 필드를 인용한다:
  시작(08:10)과 종료(08:26) 모두 `sha256:e220e72137165c57b08cd24388c11d64762b689b376a17911a774b8a3bb45236`. 평가 중 변화 없음.
  참고로 owner ZIP의 BUILD_INFO 원본 트리 해시는 `sha256:5a7bc592…`다. 차이는 DD-A64 파일 1개이며 아래 D4에서 다룬다.
- ZIP(`sha256sum dist/*/*.zip`, 시작과 종료에 같은 값): public `ce34b821…`, registration `95b541e9…`, owner `79cce02a…`.
  `7V_FINAL/SHA256SUMS_final.txt` 및 각 `.sha256`과 일치한다.
- 환경: CPU 전용(`CUDA_VISIBLE_DEVICES=""`), `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `--basetemp`는 scratch.
  GPU는 쓰지 않았다(doctor 재실행 때도 가짜 `nvidia-smi` 사용). etri_nas, build/source_snapshot, /data에는 쓰지 않았다.
  프로젝트에 쓴 파일은 이 보고서 하나다. T6 트리는 읽기 전용으로만 사용했다(bwrap `--ro-bind`, 또는 `-I -B` import만).
- 표기: `<TR>` = TARGET_ROOT. `$S` = 이 세션 scratchpad의 `c09_eval/`(경로에 계정명이 있어 생략). 평가자 도구는 `$S/scripts/`,
  산출물은 `$S/out/`, `$S/sbx_out/`, `$S/pytest_ops.txt`, `$S/startsh_probe.json`에 있다.
  `7V_FINAL` = `docs/internal/evidence/WP7/7V_FINAL`.

## 결론

총점 **90/100**: import_origin 20 + package_data 20 + clean_env 25 + relocation 25. Blocker 0, Critical 0, Major 1, Minor 5.
**판정: FAIL.** 통과 조건은 총점 90 초과다. Blocker/Critical 0 조건은 충족한다.

- 실행으로 확인한 사항:
  - 최종 ZIP 3종의 wheel에 선언된 package data가 모두 들어 있다. 공개 wheel 4종은 현재 소스와 바이트 단위로 같다.
  - T6 최종 트리 3개에서 mbnas_* 모듈 전수(public 126, registration 126, owner 215)가 각 트리의 `runtime/venv/.../site-packages`에서 로드된다.
  - 원본 트리와 TARGET_ROOT가 보이지 않는 bwrap 안에서도 같은 결과가 나오고, 리소스 로드에 성공한다.
  - 공백 경로 symlink와 외부 cwd에서 doctor와 `--help`가 동작한다.
  - `tests/public/ops`는 59 passed다.
- 감점 원인 1 (import_origin): `scripts/start.sh`가 호출자의 `PYTHONPATH`를 role 프로세스에 그대로 넘긴다.
  - 평가자 probe에서 gateway와 runner의 `sys.path[1]`이 호출자의 디렉터리였다.
  - `-I` 없이 실행하는 서비스 argv는 그림자 `mbnas_server`를 import했다.
  - doctor와 verify의 origin 검사는 `-I`로 돌기 때문에 이 상황을 볼 수 없다(D1, Major).
  - 필수 증거인 "relocation 로그의 모듈별 origin 표"는 요약 한 줄과 doctor의 패키지 단위 행뿐이다(D2).
- 감점 원인 2 (package_data): 최종 wheel의 멤버·package data 목록을 담은 프로젝트 산출물이 없다.
  `WP7/7B/wheels_build.txt`는 10-27 빌드 기준이다(D3).
- 90 초과로 가는 최소 수정은 D1, D2, D3이다. 수정 뒤 T6 relocation과 package 단계를 재실행해야 한다.

## 재실행 요약 (cwd=TARGET_ROOT 또는 `$S/ext cwd`)

| # | 명령 / 대상 | exit | 결과 |
|---|---|---|---|
| R1 | `sha256sum dist/*/*.zip` (시작·종료) | 0 | ce34b821… / 95b541e9… / 79cce02a…, SHA256SUMS_final과 일치, 평가 중 불변 |
| R2 | `unzip -l` ZIP 3종(466/476/2115 항목) → 각 ZIP의 wheel을 `unzip -p`로 꺼내 `unzip -l` | 0 | 아래 R2 상세 참고 |
| R3 | wheel 멤버·내용 대 현재 소스 비교(python zipfile + sha256) | 0 | 공개 4종: 누락 0, 추가 0, 내용 차이 0. core: 92/92 멤버, 내용 차이 1(`mbnas_core/runner_api/routes.py`, DD-A64, 08:06:54 수정 > ZIP 07:06:56) |
| R4 | wheel 문자열 스캔(`/home/`, `/root/`, `etri_nas`, `build/staging`, `source_snapshot`, 계정명, `/tmp/claude`) | 0 | 런타임 절대경로 0건 |
| R5 | `$S/scripts/c09_origin_probe.py`를 각 T6 최종 트리의 venv python으로 실행(`env -i … python -I -B`, cwd=`$S/ext cwd`) | 0/0/0 | 아래 R5 상세 참고 |
| R6 | R5와 같은 probe를 **bwrap 안에서** 실행 | 0/0 | 아래 R6 상세 참고 |
| R7 | bwrap(트리 ro, 가짜 nvidia-smi) 안에서 `/tmp/path with spaces/mbnas app root`(symlink)를 거쳐 외부 cwd `/tmp/elsewhere`에서 `bash …/scripts/doctor.sh --profile public` 실행 | 0 | 아래 R7 상세 참고 |
| R8 | `runtime/venv/bin/python -B -m pytest -p no:cacheprovider tests/public/ops -m "not gpu and not live and not legacy_records and not host_data" --basetemp=$S/pytest_ops_tmp/base` | 0 | **59 passed, 2 deselected**, 36 s (`$S/pytest_ops.txt`, `$S/ops_junit.xml`). `WP7/7A/pytest_ops.txt:72`, T6 public junit `tests.public.ops` 59와 같다 |
| R9 | 그림자 probe, `env -i … PYTHONNOUSERSITE=1 PYTHONPATH=$S/shadow <T6 public>/runtime/venv/bin/python <venv>/bin/mbnas-gateway --help`(start.sh:290–293의 argv 형태) | 1 | `C09-SHADOW: mbnas_server imported from $S/shadow/mbnas_server/__init__.py`. 같은 env에 `-I`를 주면 site-packages의 `mbnas_server` |
| R10 | `$S/scripts/c09_startsh_env_probe.py`: WP-7A ops 하네스(`tests/public/ops/opstest_support.py`, 읽기 전용 import)로 `$S/startsh_probe`에 일회용 ROOT를 만들고, stub이 PYTHONPATH와 sys.path를 기록하게 한 뒤 호출자 env에 `PYTHONPATH=<shadow dir>`를 넣고 **실제 `scripts/start.sh --profile public`** 실행 | 0 | start 0, stop 0, 남은 프로세스 0. gateway와 runner 모두 `pythonpath=<shadow dir>`, `sys.path=[<venv>/bin, <shadow dir>, stdlib…]` (`$S/startsh_probe.json`) |
| R11 | `runtime/venv/bin/python -B scripts/dev/check_import_closure.py` | 0 | ok. violations 0, unresolved 0, forbidden_strings 0, 문서화된 예외 1(base.py, DD-A58) |
| R12 | T6 설치 트리 unit junit 열람 | — | 아래 R12 상세 참고 |
| R13 | T6 트리 출처 대조 | — | BUILD_INFO build_id가 최종 ZIP과 같다(public `r-decdba6e9ffb5195`, registration `r-c95ac6643111f10f`, owner `r-3d034c7813d3da90`). public·owner T6 트리의 `wheels/*.whl` sha도 ZIP 안의 wheel과 같다(registration ZIP의 wheel은 public과 바이트 동일, R2) |

R2 상세:
- 공개 wheel 4종의 멤버 수:
  - contracts 31개: `openapi/openapi.json` 353 938 B, `HISTORY.json` 7 305 B, `openapi_runner.json` 71 667 B
  - model_runtime 53개: `operators/__init__.py`, `operators/etri_mixed_blocks.py`, `operators/parse_model_8_4_173.py`
  - runner 28개
  - server 35개
- owner의 core wheel 97개: `db/schema.sql` 13 124 B, `protocol/handoff_protocol_20261006.json` 9 083 B, `registry/model_registry.example.json` 16 363 B
- 이름이 같은 wheel은 3개 ZIP 사이에서 sha가 같다.
- `mbnas_web/dist` 3파일이 3개 ZIP 모두에 있고 sha가 소스 dist와 같다: index `08772e74…`, js `6c3f29a2…`, css `1e0f94d5…`.
- ZIP 안의 egg-info는 0개다.

R4 상세:
- 적중은 출처 산문뿐이다(`VENDORED_FROM … etri_nas/…`, `paths.py`의 "relative/home/placeholder").
- handoff 예시의 `console.paths`(`/data/drone/…`)는 presets가 placeholder로 교체한다(`presets.py:65-71`).

R5 상세 (`$S/out/origin_{public,registration,owner}.json`):
- 모듈 수: public 126/126, registration 126/126, owner 215/215가 모두 `<tree>/runtime/venv/lib/python3.10/site-packages` 아래에 있다. 트리 밖 0, ZIP 안 src 사본 0, 오류 0.
- `sys.path` = 기반 stdlib 3개 + venv site-packages.
- `.pth`는 `coloredlogs.pth`와 `distutils-precedence.pth`뿐이다(서드파티, 경로 줄 없음).
- 리소스 로드 성공:
  - openapi.json 1.5.0, sha `70e0cffa…`, path 38개
  - HISTORY.json
  - openapi_runner.json 1.0.0
  - operators의 NAS 블록 3종
  - owner: `schema_sql()` sha `f585e876…`(CREATE TABLE 17개, wheel·소스와 같음), `handoff_protocol()`, registry 예시

R6 상세 (`$S/sbx_out/origin_{public,owner}_sbx.json`):
- 샌드박스 구성: `env -i`, `--unshare-net/pid/ipc/uts`. `/usr`, `/etc`, 기반 py env, 트리 자신만 ro-bind했다. `/tmp`는 tmpfs다.
- 보이지 않음: etri_nas, `<TR>/pyproject.toml`, `<TR>/mbnas_server/src`, `<TR>/build`, `/data`, `~/.ssh`, `/dev/nvidia*`(0개). PYTHONPATH는 unset이다.
- venv python을 `-I` 없이 실행해도 `ENABLE_USER_SITE=False`다.
- public 126/126, owner 215/215, 리소스 전부 성공.

R7 상세:
- doctor가 root를 `/tmp/path with spaces/mbnas app root`로 인식했다.
- interpreter, `package:` 5행, entry_points, path_strings는 PASS다.
- FAIL 4행은 core 미기동, 그리고 읽기 전용 마운트 때문에 store와 log 디렉터리를 쓸 수 없는 데서 나온 예상 결과다.
- start, stop, install, verify `--help`(공백 symlink 경유)는 모두 exit 0이다.

R12 상세:
- owner 트리: `test_db_migration` 2 passed(schema.sql), `test_protocol_presets` 6 passed / 1 skipped(handoff), `test_registry` 50, `test_contract_history` 37
- public 트리: `test_contract_history` 37(설치본의 `CONTRACT_PATH`/`HISTORY_PATH`), `test_runner_protocol` 102 / 1 skipped, `test_gateway_skeleton` 34
- 합계: public 717 passed / 0 failed / 222 skipped. 위치는 `<TR>/runtime/test_root/t6/final/<tree>/runtime/logs/verify/*/unit.junit.xml`

## 기준별 채점

### 1. import_origin — **20/25** (앵커 20: "사소한 누락(문서 1건·라벨) 외 충족")

증거:
- verify `--suite relocation`, 외부 cwd. "module __file__ under ROOT" ok, 8/8, exit 0:
  - `7V_FINAL/T6/public_verify_unit_relocation.txt:6,14,16`
  - `reg_verify_unit_relocation.txt:6,14,16`
  - `owner_verify_unit_integration_relocation.txt:8,16,18`
- 패키지 단위 origin 행:
  - `7V_FINAL/T6/public_doctor_strict.txt:3-7`(`package:mbnas-* … from <tree>/runtime/venv/…/site-packages/…`, core absent), `doctor_public.json:19-`
  - relocation 부속 로그 `<tree>/runtime/logs/verify/20261027T221210Z-public/relocation_doctor_external_cwd.log`
- 설치 시점 origin 행:
  - `7V_FINAL/40_install_public.txt:152-155`, `40_install_registration.txt:152-155`, `40_install_owner.txt:153-157`(전부 "inside ROOT")
  - `T4/rerun/install_owner_offline.txt:154-158`
- 실행 중 서비스 env에 PYTHONPATH 없음:
  - `7V_FINAL/T6/summary_reinvoke.json:9-21`(gateway `environ_keys`)
  - 생성 env 정의는 `7V_FINAL/scripts/final_driver.py:40-44`

재실행: R5와 R6에서 모듈 전수와 `.pth` 표를 평가자가 만들었다(전부 site-packages, 외부 `.pth` 없음). R9와 R10은 반례 probe다.

감점과 공백:
- (a) 필수 점검이 요구하는 "모듈별 origin 표"가 relocation 로그에 없다.
  - `scripts/verify.sh:368-376`의 `ORIGIN_CODE`가 모듈별 `origins`를 계산하지만, `:454-456`에서 "all under ROOT" 요약만 남긴다.
  - `relocation.json`(`:511`)에는 표가 없다.
  - 검사 대상은 최상위 패키지 4~5개(`:135`)뿐이다. 하위 모듈과 `.pth` 목록은 기록되지 않는다(D2).
- (b) 서비스 기동 경로가 "원본/부모 PYTHONPATH 없음"을 보장하지 않는다.
  - `scripts/start.sh:290-293`은 `"$VENV_PY" "$VENV/bin/<entry>"`를 `-I` 없이 실행한다.
  - `:338`은 PYTHONNOUSERSITE 등만 export하고 PYTHONPATH와 PYTHONHOME은 지우지 않는다.
  - `:348,:351`의 exec는 env를 그대로 상속한다.
  - doctor(`scripts/doctor.sh:87`)와 verify(`:448`)는 `-I`로 실행되어 이 상황을 감지할 수 없다.
  - R9와 R10으로 실증했다(D1).
  - T4/T5/T6에서 이 성질이 성립한 것은 시험 env가 깨끗했기 때문이다(T4 `env -i`, T6 생성 env, T5 이미지 env).

### 2. package_data — **20/25** (앵커 20: 문서 1건 누락)

증거:
- 선언: `mbnas_contracts/pyproject.toml:21-22`, `mbnas_core/pyproject.toml:33-36`
- wheel 내용 목록(프로젝트 산출물): `WP7/7B/wheels_build.txt:21-27`
  - wheel별 `package_data` 목록이 최종본과 같다.
  - 다만 10-27 빌드라서 멤버 수가 다르다. core 91 대 최종 97, server 32 대 최종 35.
- 설치본에서 리소스 로드:
  - R12의 T6 설치 트리 unit(schema.sql, handoff, registry, openapi/HISTORY 로더 시험 통과)
  - `7V_FINAL/T6/transcript.txt`: public gateway를 거친 presets 200(owner core의 handoff), `/` 200 733 B(웹 dist)
  - `owner_doctor_strict.txt`: 28 PASS
  - `T4/RERUN_README.md`와 `T5/RERUN_README.md`: 제공된 index와 asset의 sha가 최종 dist와 같음
- 웹 dist 게이트: `7V_FINAL/32_pytest_packaging.txt`(52 passed, `test_web_dist_missing_fails_public_unless_allowed` 포함)

재실행: R2와 R3(최종 wheel 목록, 소스 대비 0 차이), R5와 R6(설치본에서 리소스 로드, 호스트와 bwrap 양쪽).

공백(D3):
- 최종 ZIP의 wheel 멤버와 package data 목록을 담은 산출물이 TARGET_ROOT에 없다. `7V_FINAL/1*_package_*.log`에는 wheel 파일명만 있다.
- `scripts/dev/package_build.py`에는 선언된 package-data가 wheel에 있는지 확인하는 게이트가 없다.
- 설치본 리소스 로드는 전용 시험이 아니라 unit과 흐름 시험을 통한 간접 근거다.

### 3. clean_env — **25/25** (앵커 25)

증거:
- **T4, bwrap, owner 최종 ZIP**
  - `T4/rerun/harness/sbx.sh:11-22`: `exec env -i … bwrap --unshare-net --unshare-pid --unshare-ipc --unshare-uts`. ro-bind는 `/usr`, `/etc`, 기반 py, wheelhouse뿐이고 작업 트리만 bind다. `--tmpfs /tmp`, 새 `HOME`, `PIP_CONFIG_FILE=/dev/null`, `CUDA_VISIBLE_DEVICES=""`.
  - `T4/rerun/isolation_proof.txt`: etri_nas, /data, ~/.ssh, `<ROOT>/pyproject.toml`, `<ROOT>/build`이 보이지 않는다. 인터페이스는 lo뿐이고 https는 exit 6으로 실패한다. `/dev/nvidia*`는 `[]`.
  - `install_owner_offline.txt:10`: `--no-index … --no-cache-dir`. `:154-158`: inside ROOT. 마지막 줄 `INSTALL_EXIT=0 seconds=162`.
  - `t4f_local_console.txt:2,4,6,40,48`: 격리 줄, doctor/start/harness/stop exit 0. runners 변형도 같다.
- **T5, docker, public 최종 ZIP**
  - `T5/rerun/docker_build_nocache.txt:2`: `docker build --no-cache`, 컨텍스트는 압축 해제한 ZIP 루트다.
  - `docker_build_nocache.txt:290`: ENV `PYTHONNOUSERSITE=1`, `PIP_NO_CACHE_DIR=1`, `PIP_CONFIG_FILE=/dev/null`.
  - `leakscan/image_env.json`: PYTHONPATH 0건, `HOME=/tmp/mbnas-home`.
  - `harness/t5f_run.sh:93-106`: volume은 `run/runtime`, `run/data:ro`뿐이다.
  - `run/t5f_container_checks.txt`: `mbnas_core` 없음, 배포본 4종.
- **T6**
  - `7V_FINAL/install_final.sh`(명령), `40_install_*.txt`(`--no-cache-dir`, exit 0)
  - `21_unshare_network_proof.txt`, `22_offline_install_public_final_unshare.txt`(unshare -rn 설치 exit 0)
  - 서비스 생성 env는 `final_driver.py:40-44`

재실행: R6(bwrap 안 import와 리소스 확인, user-site 비활성, PYTHONPATH 없음), R7.
T4와 T5 자체는 열람만 했다. 확인 못 함 목록에 적었다.

공백(D5, 점수 영향 없음):
- registration ZIP은 파일시스템 격리 아래에서 설치된 적이 없다(호스트 T6만).
- public ZIP의 `install.sh` 경로는 네트워크만 격리되었다(unshare -rn). T5는 Dockerfile 경로를 썼다.
- 셋 다 기준 문구("bwrap/docker 격리에서 … 설치·실행")는 owner(T4)와 public(T5)으로 충족한다.

### 4. relocation — **25/25** (앵커 25)

증거:
- 실행 기록: `7V_FINAL/T6/transcript.txt`
  - 외부 cwd `<scratchpad>/t6final`에서 install, configure, start, doctor --strict, API, verify, stop이 전부 exit 0이다.
  - 재호출 단계는 cwd `/`에서 실행했다. `re_owner_start.txt:1`, `re_public_start.txt:1`, `re_public_doctor_relative.txt`가 근거다.
- 트리 위치: `mbnas {public,registration,owner} final/mbnas_application`, 공백을 포함한 실제 경로다.
- relocation 8/8 ×3(위 인용 줄): 외부 cwd doctor, 공백 symlink를 거친 doctor, `--help` ×3, ROOT 자체의 공백.
- 단계별 exit 요약: `summary_{up,verify,down,reinvoke}.json`
- VALIDATION_REPORT.md:14-17이 위 내용과 일치한다.

재실행: R13(T6 트리가 최종 ZIP에서 나왔음), R7(공백 symlink와 외부 cwd에서 doctor exit 0, `--help` ×4 exit 0), R5(외부 공백 cwd).

공백(점수 영향 없음):
- (a) owner ZIP이 현재 트리보다 오래되었다(D4). T6 owner 재실행이 필요하다.
- (b) T6 트리가 `<TR>/runtime/test_root` 아래에 있다. "원본 경로 문자열" needle도 2개뿐이다(D6). 부모 트리 독립성은 T4, T5, R6이 보완한다.
- (c) `summary_reinvoke.json`의 `external_cwd` 키는 scratch 경로를 표시하지만 실제 명령은 `/`에서 실행되었다(라벨 불일치, 사소).
- (d) 관찰 사항, 결함 아님: gateway의 웹 dist 기본값은 cwd에 의존한다(`mbnas_server/src/mbnas_server/config.py:78-83`).
  - 제공 명령인 start.sh는 cwd=ROOT로 실행하므로(`start.sh:341`) 문제없다.
  - console script를 다른 cwd에서 직접 실행하면 `MBNAS_GW_WEB_DIST`가 필요하다. 예시 파일 `config/examples/gateway.env.example:43`에서는 이 줄이 주석 처리되어 있다.

## 결함

| ID | 심각도 | 소유 WP / 파일 | 내용 | 구체적 수정 |
|---|---|---|---|---|
| D1 | **Major** | WP-7A `scripts/start.sh:290-293,338,348,351` (+ `scripts/doctor.sh:87`, `scripts/verify.sh:448`) | 아래 "D1 상세" 참고 | 아래 "D1 상세" 참고 |
| D2 | Minor | WP-7A `scripts/verify.sh:368-376,454-456,511,135` | relocation 로그에 모듈별 origin 표가 없다. 계산한 `origins`를 버리고 요약 한 줄만 남기며, 최상위 패키지만 검사하고 `.pth` 목록이 없다 | `origins` 전체와 모든 mbnas_* 하위 모듈(`pkgutil.iter_modules` + `find_spec`, `$S/scripts/c09_origin_probe.py` 방식), site-packages의 `*.pth`·`__editable__*` 줄을 `relocation.json`에 기록하고 표로 출력한다. 트리 밖을 가리키는 항목이 있으면 FAIL |
| D3 | Minor | WP-7V / `scripts/dev/package_build.py` | 최종 wheel의 멤버·package data 목록 산출물이 없다(7B 목록은 10-27 빌드). package-data 존재 게이트도 없다 | 빌드 때 `[tool.setuptools.package-data]` 각 파일이 wheel namelist에 있고 sha가 소스와 같은지 확인한다(아니면 빌드 실패). wheel별 멤버 목록은 `build/package_reports/<p>.json`과 7V_FINAL에 기록한다. verify에 설치본 리소스 로드 단계를 추가한다(외부 cwd에서 `contract()`, `schema_sql()`, `handoff_protocol()`, `EXAMPLE_REGISTRY` 호출) |
| D4 | Minor (과정) | WP-7V `dist/owner/…79cce02a….zip` | 현재 트리보다 오래되었다. `mbnas_core/runner_api/routes.py`(DD-A64, 08:06:54)가 다르다(BUILD_INFO 원본 해시 5a7bc592… 대 현재 e220e721…). T4 재실행과 T6 owner의 C09 증거가 이 ZIP 기준이다 | 예정된 owner 재빌드 후 T6 owner(install --offline → doctor --strict → verify unit,integration,relocation)를 재실행한다. 최소한 T4 bwrap install과 doctor도 다시 하고, 새 sha를 7V_FINAL과 VALIDATION_REPORT에 기록한다(C12 회귀) |
| D5 | Minor (범위) | WP-8 T4/T5 하네스 | registration ZIP은 격리 설치된 적이 없다. public ZIP의 `install.sh`도 파일시스템 격리 아래에서 돌지 않았다(unshare -rn만) | `T4/rerun/harness/sbx.sh`로 public과 registration ZIP을 같은 방식으로 실행한다(`install.sh --offline` → doctor → verify relocation). `isolation_proof`를 남긴다. GPU 불필요 |
| D6 | Minor | WP-7A `scripts/verify.sh:134` + WP-7V T6 배치 | "원본 경로 문자열" needle이 `/etri_nas/`와 `/root/detection` 2개뿐이다. T6 트리가 TARGET_ROOT 아래에 있어서 부모 트리 의존을 T6로는 잡을 수 없다. 평가자 R4와 R6에서는 결함이 관찰되지 않았다 | BUILD_INFO에 기록된 빌드 원본 루트를 needle에 추가한다. T6 1회는 소스 트리 밖(`$TMPDIR/"mbnas t6 ext"/…`)에서 실행한다 |

**D1 상세 (Major).** `start.sh`가 호출자의 `PYTHONPATH`(와 `PYTHONHOME`)를 gateway, runner, core 프로세스에 넘긴다.
- role은 `-I` 없이 실행되므로 PYTHONPATH가 설치 site-packages보다 먼저 온다.
  - R10: 실제 start.sh에서 role의 `sys.path[1]`이 호출자 디렉터리였다.
  - R9: 그림자 `mbnas_server`가 import되었다.
- doctor와 verify의 origin 검사는 `-I`로 돌아서 이를 보지 못하고 PASS를 낸다.
- 원본 프로젝트 문서는 `PYTHONPATH=src`(원본 패키지명도 `mbnas_server`)를 쓴다. 공개 문서에는 PYTHONPATH 안내가 없다.
- 잠재 결함이다. 상속된 PYTHONPATH가 있을 때만 드러나며 T4/T5/T6에서는 실행되지 않았다.

D1 수정:
- `launch()` 직전에 `unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE OLDPWD`를 넣거나 `env -u …`로 exec한다. 기존 Minor인 OLDPWD도 함께 해결된다.
- doctor의 `process:` 검사에서 이 트리 role의 `/proc/<pid>/environ`에 PYTHONPATH나 PYTHONHOME이 있으면 FAIL로 처리한다.
- verify relocation에 호출자 env로 `-I` 없이 실행하는 origin probe를 추가한다.
- ops 시험을 추가한다. R10 probe와 같은 방식으로, stub이 기록한 PYTHONPATH가 없어야 통과한다.

기존 결함(참고, 이번 채점의 결함 수에는 넣지 않음): P-final Minor "start.sh services inherit OLDPWD". D1과 같은 자리에서 고치면 된다.

## 총점과 판정

| 기준 | 배점 | 점수 | 앵커 |
|---|---|---|---|
| import_origin | 25 | 20 | 사소한 누락(라벨: 모듈별 origin 표) 외 충족. Major D1은 별도 표기 |
| package_data | 25 | 20 | 사소한 누락(문서 1건: 최종 wheel 목록) 외 충족 |
| clean_env | 25 | 25 | 증거 파일, 명령, exit code가 모두 있고 열람과 부분 재실행(R6)으로 확인 |
| relocation | 25 | 25 | 증거 파일, 명령, exit code가 모두 있고 열람과 재실행(R5, R7, R13)으로 확인 |
| **합계** | 100 | **90** | **FAIL**: 90 초과 조건 미달. Blocker 0, Critical 0. BLOCKED_BY_ENV 없음 |

통과 조건 대조:
- 총점 >90: 미달(90)
- Blocker 0: 충족
- Critical 0: 충족
- 필수 증거가 TARGET_ROOT에서 실행된 산출물인가: 충족. 4기준 모두 실행 로그가 있고, 원천 문서의 보고값만으로 채점한 항목은 없다.

## 확인 못 함

- T4(bwrap, owner)와 T5(docker, public) 전 과정은 재실행하지 않았다.
  - T4 재실행은 `runtime/test_root` 아래에 쓰게 된다.
  - T5는 온라인 `--no-cache` 빌드(247 s)와 컨테이너 기동이 필요하다.
  - 그래서 로그 열람과 R6(설치 트리 import·리소스 확인을 bwrap에서)으로 대신했다. `install.sh` 자체를 격리 아래에서 재실행하지는 않았다.
- T6 전 과정(install, start, API, verify unit)은 재실행하지 않았다. 재실행한 것은 읽기 전용 doctor와 `--help`(R7), 그리고 import probe(R5)다.
- R6 샌드박스는 `--unshare-net`으로 만들었지만, `/sys`를 마운트하지 않아 네트워크 차단 여부는 확인하지 않았다. 네트워크 격리 근거는 T4의 `isolation_proof.txt`와 `7V_FINAL/21_unshare_network_proof.txt`뿐이다.
- 실제 고객이나 owner 호스트에 PYTHONPATH가 설정되는 빈도는 모른다. D1의 발생 가능성은 원본 문서의 사용례로만 추정했다.
- 예정된 owner 재빌드 후의 ZIP은 아직 없어서 평가하지 못했다(D4).
- T5 이미지 `403a1b43…`의 레이어 내용은 다시 스캔하지 않았다. `T5/rerun/leakscan/`을 열람만 했다.

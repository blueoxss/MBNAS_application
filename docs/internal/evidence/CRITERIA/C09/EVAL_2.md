# C09 설치 폐쇄성 — 재채점 EVAL_2

- set: **C09** (`docs/internal/criteria/C09.json` v1.0). 이전 평가: `EVAL_1.md`(90/100 FAIL).
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자. 채점만 하고 결정은 설계자가 한다. 공통 지침은 `scratchpad/wp3/rescore2.txt`.
- time: 2026-10-28T10:16+09:00 시작, 10:33+09:00 재실행 종료, 이후 보고서 작성
- tree hash (`runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`, mbnas-source-tree/2, 221 files):
  `sha256:f00987be7994203fc16f52140bd98b3f89e1b548b6a346255066727a36dcc40c`.
  10:16, 10:18, 10:32, 그리고 보고서 작성 직후에 같은 값이었다.
  P-final-2의 `docs/internal/evidence/WP7/PFINAL2/01_tree_hash.json`도 같은 값이다.
- **dist/는 평가 시점에 구판이었다.** `sha256sum dist/*/*.zip`:
  - owner `79cce02a…`, public `ce34b821…`, registration `95b541e9…`
  - 07:05–07:06 빌드이며, 원본 트리는 public/registration 83af6d5f…, owner 5a7bc592…(DD-A69)
  - 10:32 시점에 `PFINAL2/SUMMARY.md`와 `docs/internal/evidence/T6/rerun2/`가 없었다(PFINAL2는 게이트 실행 중).
- 그래서 ZIP에 의존하는 항목은 지침대로 **현재 트리와 평가자 scratch 빌드**로 채점했다. 새 ZIP과 T6 rerun2에 대한 확인은 추가 보고(addendum)로 미룬다.
- 환경: CPU 전용(`CUDA_VISIBLE_DEVICES=""`), `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `--basetemp`는 scratch.
  - doctor와 verify는 가짜 `nvidia-smi`(exit 9)를 PATH 맨 앞에 두고 실행했다.
  - GPU, /data, etri_nas, build/source_snapshot은 건드리지 않았다.
  - 프로젝트에 쓴 파일은 이 보고서 하나다. 빌드는 scratch `--root`로만 했고, 10:16 이후 `<TR>/dist`와 `<TR>/build/package_reports`는 바뀌지 않았다.
- 표기:
  - `<TR>` = TARGET_ROOT
  - `$S2` = 이 세션 scratchpad의 `c09_eval2/`(계정명 생략). 평가자 도구는 `$S2/scripts/`, 산출물은 `$S2/out/`과 `$S2/out_sbx/`, scratch 빌드 루트는 `$S2/src_root/mbnas_application`, 설치 트리는 `$S2/inst/mbnas c09 {public,owner}/mbnas_application`
  - `R4` = `docs/internal/evidence/WP7/ROUND4`

## 결론

총점 **100/100**: import_origin 25 + package_data 25 + clean_env 25 + relocation 25.
Blocker 0, Critical 0, Major 0, Minor 4(D4 과정·대기, D5, D6, 신규 D7).
**판정: PASS (잠정).** 총점이 90을 넘고 Blocker/Critical은 0이다.

- **잠정 조건**: 현재 dist/ ZIP은 구판이다. 이 점수는 현재 트리(f00987be…)를 평가자가 scratch로 빌드하고 설치한 결과에 근거한다.
- P-final-2 결과가 나오면 추가 보고에서 다음을 대조해야 한다:
  - 새 ZIP sha
  - `build/package_reports/<p>_wheels.txt`와 owner ZIP 안의 `docs/internal/evidence/package_build/owner_wheels.txt`
  - T6 rerun2 `relocation.json`의 `module_origins`
  - P2의 T4/T5 재실행
- EVAL_1의 감점 결함 D1(Major), D2, D3은 모두 **FIXED**다. 평가자가 직접 재실행해 확인했다.

## 재실행 요약 (P = 통과, 시간은 KST)

| # | 명령 / 대상 | exit | 결과 |
|---|---|---|---|
| E1 | `source_tree_hash.py` ×4 | 0 | 위 header의 tree hash와 같은 값, 불변. `sha256sum dist/*/*.zip`도 구판 3종 그대로 |
| E2 | `rsync`로 `<TR>`를 `$S2/src_root/mbnas_application`에 복사(runtime/, dist/, .git, node_modules, build/* 제외, build/allowlists 포함) | 0 | `diff -rq`: 5개 패키지, scripts, build/allowlists, mbnas_web/dist, tests/public, config, deploy가 `<TR>`과 같다 |
| E3 | `<TR>/runtime/venv/bin/python -I -B $SR/scripts/dev/package_build.py --root $SR --profile public` | 0 | 11 s, 19개 검사 중 18개 실행(reproducibility 미요청), 0 violation. `wheel_contents` PASS. `public_wheels.txt` 생성(아래 E3 상세) |
| E4 | 같은 명령, `--profile owner` | 0 | 21 s, `wheel_contents` PASS. `owner_wheels.txt` 생성. owner ZIP에 `docs/internal/evidence/package_build/owner_wheels.txt`와 `PACKAGE_CONTENTS.md`가 들어 있다(아래 E4 상세) |
| E5 | 음성 대조 1: scratch 복사본의 `mbnas_contracts/pyproject.toml`에서 `HISTORY.json` 선언을 지우고 public 빌드 | **1** | `VIOLATION wheel_member_missing: …!mbnas_contracts/openapi/HISTORY.json`, `check wheel_contents FAIL`, "FAILED: nothing published", dist 없음 |
| E6 | 음성 대조 2: 선언은 두고 `openapi_runner.json` 파일을 삭제한 뒤 public 빌드 | **1** | `VIOLATION package_data_missing … 'openapi_runner.json' matches no staged file`, FAIL, 게시 없음 |
| E7 | pytest `tests/public/ops` (`--basetemp=$S2/pytest_ops_tmp/base`) | 0 | **65 passed, 2 deselected**, 51 s. 신규 `test_ops_role_env` 2, `test_ops_stop_identity` 3, `test_ops_relocation_origins` 1 포함 |
| E8 | pytest `tests/public/packaging` | 0 | **63 passed**, 116 s. `test_package_contents_and_gates` 21, `test_package_build` 28, `test_canary_tools` 14 |
| E9 | `$S2/scripts/c09_startsh_env_probe2.py`(ops 하네스를 읽기 전용 import, 일회용 ROOT는 `$S2/startsh_probe2`) | 0 | 아래 E9 상세 |
| E10 | **bwrap 안에서 public scratch ZIP 신규 설치와 relocation 검증** | 0 | 아래 E10 상세 |
| E11 | E10과 같은 방식으로 **owner** scratch ZIP | 0 | 설치 exit 0(96 s, `inside ROOT` 5행). `verify --profile owner --suite relocation` exit 0, 8/8, **215 모듈**(core 89) 전부 ROOT. `.pth` 2개 |
| E12 | **bwrap 안의 실제 owner role 기동** | 0 | 아래 E12 상세 |
| E13 | 신규 설치 트리에 평가자 probe(EVAL_1의 `c09_origin_probe.py`, `env -i … -I -B`, cwd=`$S2/ext cwd`) 실행 | 0/0 | 아래 E13 상세 |
| E14 | 신규 public 설치에서 `verify.sh --profile public --suite unit --pytest-args tests/public/contracts/test_contract_history.py`를 (a) 깨끗한 env와 (b) `PYTHONPATH=<그림자 mbnas_contracts>`로 실행 | 0 / **1** | (a) **37 passed**: 설치본의 `CONTRACT_PATH`/`HISTORY_PATH` 사용. (b) 그림자 패키지의 ImportError로 FAIL. 즉 unit suite는 호출자의 PYTHONPATH를 그대로 쓴다(D7) |

E3 상세 (`$SR/build/package_reports/public_wheels.txt`):
- contracts: 27 members, gate PASS, 선언된 package data 3/3이 wheel에 있다.
  - `[data]` HISTORY.json 7 305 B
  - openapi.json 353 938 B, sha `70e0cffa…`
  - openapi_runner.json 80 942 B, sha `0f3dac4d…`
- model_runtime 49, runner 23, server 30, 모두 PASS.
- ZIP은 476 항목 / 2 717 216 B. 평가 전용이며 release용이 아니다.

E4 상세:
- core: 92 members, 3/3 package data
  - `[data]` schema.sql 14 833 B, sha `84240467…`
  - handoff json, sha `f0f43d78…`
  - registry example, sha `27db9251…`
- 스테이징 2627 파일, ZIP 65 296 083 B

E9 상세 (`$S2/out/startsh_probe2.json`):
- (A) 호출자 env에 PYTHONPATH(그림자 `mbnas_server`), PYTHONHOME(유효한 base prefix), PYTHONSTARTUP, PYTHONUSERBASE, OLDPWD를 넣고 실제 `start.sh --profile public` 실행:
  - start 0
  - gateway와 runner의 `/proc/<pid>/environ`에 남은 Python 변수는 `PYTHONNOUSERSITE`뿐이다.
  - `sys.path`에 그림자 디렉터리가 없다.
  - doctor의 `env:gateway`와 `env:runner`는 PASS. stop 0.
- (B) PYTHONPATH와 PYTHONHOME을 가진 채 손으로 띄운 stub gateway와 pid 파일:
  - doctor `env:gateway` **FAIL**("PYTHONPATH, PYTHONHOME set", 키 이름만 출력), blocking에 포함
  - `doctor --strict` **exit 1**. stop 0.

E10 상세:
- 샌드박스 구성: `env -i`, `--unshare-net/pid/ipc/uts`. ro-bind는 `/usr`, `/etc`, 기반 py env, wheelhouse(`/opt/wheelhouse`)이고, 설치 트리만 rw. `/tmp`는 tmpfs.
- 샌드박스 안에서:
  - etri_nas, `<TR>/pyproject.toml`, `<TR>/mbnas_server/src`, `<TR>/build`, `/data`, `~/.ssh`, `/dev/nvidia*`가 모두 보이지 않았다. PYTHONPATH는 unset.
  - 외부 1.1.1.1:443 TCP 연결은 실패했다.
- 외부 cwd `/tmp/elsewhere`에서 공백 경로 `$S2/inst/mbnas c09 public/mbnas_application`으로 `install.sh --profile public --offline --wheelhouse /opt/wheelhouse` 실행:
  - **exit 0**, 92 s, `--no-cache-dir`, `inside ROOT` 4행
- `verify.sh --profile public --suite relocation`: **exit 0, 8/8**. 새 `relocation.json`의 내용:
  - `module_counts`: contracts 24, model_runtime 49, runner 23, server 30
  - `module_origins`: **126행 전부 ROOT**, origin은 상대경로
  - `sys_path`: BASE ×3 + ROOT
  - `pth_files`: `coloredlogs.pth`, `distutils-precedence.pth`(import 줄 1개씩, path 줄 없음)
  - `caller_env_python_vars`: `[]`
  - `relocation_origins.tsv`: 127줄
- 산출물: `$S2/out_sbx/{install,verify_relocation}.txt|json`, 증거 디렉터리는 `<inst>/runtime/logs/verify/20261028T012437Z-public/`

E12 상세 (`$S2/out/sbx_owner_live_console.txt`):
- E11의 설치 트리를 썼다. 호출자 env에는 PYTHONPATH(import하면 즉시 `SystemExit`를 일으키는 그림자 `mbnas_server`), PYTHONHOME(base), PYTHONSTARTUP, PYTHONUSERBASE를 넣고, 외부 cwd에서 `start.sh --profile owner --wait-s 120`을 실행했다.
- **exit 0**(14 s). 그림자를 import했다면 gateway가 죽었을 것이다.
- core, owner-worker, gateway, runner 4개 role 모두 `/proc/<pid>/environ`에 남은 Python 변수가 `PYTHONNOUSERSITE`뿐이다. OLDPWD도 없다.
- PYTHONSTARTUP은 실행되지 않았다.
- doctor: `package:` 5행이 site-packages를 가리키고, `process:`와 `env:core|owner-worker|gateway|runner`가 **모두 PASS**.
- core DB는 설치본의 schema.sql로 초기화되었다(`schema_version 6`, 테이블 19).
- stop 0. 남은 python 프로세스 0.

E13 상세 (`$S2/out/origin_{public,owner}_fresh.json`):
- public 126/126, owner 215/215가 site-packages에 있다. 오류 0.
- 리소스 로드 성공:
  - openapi.json 1.5.0 `70e0cffa…`
  - openapi_runner.json **1.1.0** 80 942 B
  - operators의 NAS 블록 3종
  - owner: schema.sql `84240467…`(CREATE TABLE 18), handoff, registry 예시

## EVAL_1 결함 판정

| ID | EVAL_1 심각도 | 판정 | 근거 (file:line, 시험, 평가자 재실행) |
|---|---|---|---|
| D1 | Major | **FIXED** | 아래 "D1 근거" 참고 |
| D2 | Minor | **FIXED** | 아래 "D2 근거" 참고 |
| D3 | Minor | **FIXED** | 아래 "D3 근거" 참고 |
| D4 | Minor (과정) | **OPEN — 대기** | dist/ ZIP 3종 모두 구판이다. 10:32 기준 sha는 그대로이고 SUMMARY.md는 없다. 이제 public/registration도 현재 트리와 다르다(contracts `openapi_runner.json` 1.0.0 → 1.1.0 71 667 → 80 942 B, core schema v6). P-final-2가 같은 트리 해시로 재빌드 중이다 → 추가 보고에서 판정 |
| D5 | Minor (범위) | **OPEN** (프로젝트 증거 기준) | 아래 "D5 근거" 참고 |
| D6 | Minor | **OPEN** | `scripts/verify.sh:136` NEEDLES는 여전히 2개다. T6 rerun2의 위치는 아직 알 수 없다. 평가자 설치(E10, E11)는 `<TR>` 밖 공백 경로에서 통과했다(결함 관찰 없음) |
| (P-final Minor) | Minor | **FIXED** | start.sh의 OLDPWD 상속이 사라졌다. `start.sh:344`, `:356`, `:360`. E12에서 4개 role 모두 OLDPWD가 없었다 |

D1 근거:
- `scripts/start.sh:339-346`: role 시작 전에 `unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE OLDPWD`(:344)를 하고 `export PYTHONNOUSERSITE=1`(:346)을 한다.
- `:356`, `:360`: launch 하위 셸에서 cd 직후 같은 unset을 다시 한다. `--help` `:40-43`에도 이 동작이 적혀 있다.
- `scripts/doctor.sh:350-370`: 새 검사 `env:<role>`. 실행 중인 Python role의 `/proc/<pid>/environ`에 PYTHONPATH나 PYTHONHOME이 있으면 FAIL(blocking)이며 키 이름만 출력한다. 도움말 `:35-38`.
- 시험: `tests/public/ops/test_ops_role_env.py`(2개, E7 통과)
- 평가자 재실행: E9(stub role 양성·음성, `--strict` exit 1)과 **E12(실제 owner role 4종)**
- `R4/ops/README.txt`의 "확인 못 함: 실제 role에 대한 env:<role>"은 E12로 해소되었다.

D2 근거:
- `scripts/verify.sh:370-414`: `ORIGIN_CODE`가 모든 하위 모듈을 path-entry finder로 import 없이 찾고, `sys.path`, site dir, `*.pth`를 수집한다.
- `:499-522`: 모든 모듈 행에 대해 ROOT 밖이면 FAIL, 최상위 누락도 FAIL.
- `:573-595`: `relocation.json`에 `module_counts`, `module_origins`, `sys_path`, `site_dirs`, `pth_files`, `caller_env_python_vars`를 기록하고 `relocation_origins.tsv`(:587)를 만든다.
- 시험: `test_ops_relocation_origins.py`(음성 포함, E7 통과)
- 평가자 end-to-end: E10(public 126행), E11(owner 215행)
- 참고, 결함 아님: `.pth`의 path 줄이 트리 밖을 가리키면 표시만 하고 FAIL하지 않는다. path 줄은 site-packages 뒤에 붙어 설치 패키지를 가릴 수 없고, import 줄로 설치된 finder는 모듈 행이 OUTSIDE로 잡힌다. 그래서 수용한다.

D3 근거:
- `scripts/dev/package_build.py:804-869` `check_wheel_contents`: wheel 멤버 집합과 sha가 스테이징된 `<pkg>/src`와 같아야 하고, 선언된 package-data는 스테이징과 wheel 양쪽에 있어야 한다.
- `:871-` `render_wheel_listing`
- `:1566-1567`과 `:1652`: `<p>_wheels.txt`를 쓰고, owner ZIP에는 `wheel_listing_member`(`build/allowlists/owner.txt:17`)로 동봉한다.
- 시험: `test_package_contents_and_gates.py` 21개(E8 통과)
- 평가자 재실행: E3, E4(양성), **E5, E6(음성: exit 1, 게시 없음)**
- `R4/packaging/05_scratch_build/owner_wheels_gate_lines.txt`는 fix 5 이전 트리에서 만든 것이다. 현재 트리 기준 목록은 E3/E4가 재생산했다.

D5 근거:
- `R4`에도 public이나 registration ZIP을 파일시스템 격리 아래에서 설치한 산출물은 없다.
- 평가자가 E10(public)과 E11(owner)로 현재 트리 ZIP을 bwrap에 설치해 메웠다. registration은 하지 않았다.
- DD-A69의 다음 단계 'P2 T4/T5'에서 다룰 수 있다.

### 신규 결함

| ID | 심각도 | 소유 WP / 파일 | 내용 | 수정 |
|---|---|---|---|---|
| D7 | Minor | WP-7A `scripts/verify.sh:161-165` (`base_env`) | `verify.sh`의 unit, integration, gpu suite가 호출자의 PYTHONPATH, PYTHONHOME, PYTHONSTARTUP, PYTHONUSERBASE를 그대로 넘긴다. E14에서 설치 트리의 시험이 호출자의 그림자 `mbnas_contracts`를 import했다. 이번 그림자는 오류를 냈지만, 오래된 원본 사본 같은 무해한 그림자라면 "unit PASS" 증거가 실제로는 설치본이 아닌 코드를 가리키게 된다. relocation suite는 `-I`라서 영향이 없고, 변수 이름을 `caller_env_python_vars`로 기록한다. 기존 T6 증거는 호스트 셸에 PYTHONPATH가 없어서(EVAL_1 확인) 영향이 없다 | `base_env()`에서 위 4개 키를 제거한다(start.sh와 같은 목록). 각 suite 결과에 제거한 키 이름을 기록한다. ops 시험을 추가한다(그림자 PYTHONPATH로 `verify --suite unit`을 돌려 PASS하고 그림자가 import되지 않아야 함) |

## 기준별 채점

### 1. import_origin — **25/25** (앵커 25)

- 필수 증거("relocation 로그의 모듈별 origin 표")는 프로젝트의 `verify.sh`가 이제 직접 만든다(D2 FIXED).
- 평가자가 현재 트리 ZIP을 새로 설치해서 실행했다(E10, E11). 모듈 126/215개 전부 ROOT, `.pth`는 서드파티 import 줄 2개, PYTHONPATH 없음.
- "원본/부모 PYTHONPATH 없음"은 실행 중 role에서 강제되고 감지된다(D1 FIXED, E9, E12).
- 프로젝트 증거: `R4/ops/pytest_ops_after.txt`(65 passed), `R4/ops/negative_control.txt`(변이 8회, 전부 의도한 이유로 exit 1), `R4/ops/origin_probe_real_installs.txt`
- 남은 것:
  - release ZIP의 T6 rerun2 `relocation.json`은 아직 없다(추가 보고).
  - D7은 필수 증거 경로 밖의 시험 도구 결함이다. Minor로 따로 표기한다.

### 2. package_data — **25/25** (앵커 25)

- wheel 내용 목록과 package-data 게이트는 패키저가 빌드할 때마다 실행하고 기록한다(D3 FIXED).
- 현재 트리에서 E3/E4로 PASS 목록을 확인했고, E5/E6 음성 대조로 게이트가 실제로 막는 것을 확인했다. 시험은 E8에서 63 passed.
- 설치본에서 리소스 로드:
  - 신규 public 설치에서 `verify --suite unit`의 `test_contract_history` 37 passed(E14a)
  - 평가자 probe(E13)
  - 실제 core가 설치본 schema.sql로 DB v6을 초기화(E12)
  - 웹 dist는 scratch ZIP에 들어 있고 `web_dist`와 `web_bundle_gate` 검사가 PASS(E3)
- 남은 것: release ZIP의 `<p>_wheels.txt` 대조(추가 보고). 설치본 리소스 로드 전용 verify 단계는 없지만, unit 시험과 probe로 충족한다.

### 3. clean_env — **25/25** (앵커 25)

- 기존 T4(bwrap, owner)와 T5(docker, public) 증거는 구판 ZIP 기준이며 EVAL_1에서 열람으로 확인했다.
- 현재 트리 ZIP으로는 평가자가 bwrap 격리 설치를 했다(E10 public, E11 owner).
  - 파일시스템과 네트워크 모두 차단, `env -i`, PYTHONPATH 없음, `--no-cache-dir`, HOME은 tmpfs
- 같은 샌드박스에서 실제 role 기동, doctor, stop도 했다(E12).
- 남은 것: D5(registration 미격리, 프로젝트 증거 기준). 새 ZIP에 대한 T4/T5 재실행은 DD-A69의 P2 단계에서 이뤄진다.

### 4. relocation — **25/25** (앵커 25)

- 기존 T6 최종 증거는 구판 ZIP 기준이며 EVAL_1에서 확인했다.
- 현재 트리 ZIP으로는 평가자가 `<TR>` 밖의 공백 ROOT(`mbnas c09 public`, `mbnas c09 owner`)에서 외부 cwd로 다음을 실행했다:
  - install, `verify --suite relocation` 8/8 ×2(외부 cwd doctor, 공백 symlink doctor, `--help` ×3, ROOT 공백 포함)
  - owner start, doctor, stop(E12)
- 남은 것: T6 rerun2(추가 보고). D6은 needle이 약하다는 문제로 Minor다.

## 총점과 판정

| 기준 | 배점 | EVAL_1 | EVAL_2 | 앵커 |
|---|---|---|---|---|
| import_origin | 25 | 20 | **25** | 증거, 명령, exit code가 있고 평가자 재실행으로 확인(E9–E12) |
| package_data | 25 | 20 | **25** | 같음(E3–E8, E13, E14a) |
| clean_env | 25 | 25 | **25** | 같음(E10–E12, 기존 T4/T5 열람) |
| relocation | 25 | 25 | **25** | 같음(E10–E12, 기존 T6 열람) |
| **합계** | 100 | 90 | **100** | **PASS (잠정)**: 90 초과, Blocker 0, Critical 0, Major 0, Minor 4(D4 대기, D5, D6, D7). BLOCKED_BY_ENV 없음 |

추가 보고에서 확인할 항목(P-final-2와 P2 이후):
1. 새 ZIP 3종의 sha와 트리 해시가 f00987be…인지
2. `build/package_reports/{public,registration,owner}_wheels.txt`가 전부 PASS인지, owner ZIP 안의 `owner_wheels.txt`와 같은지
3. T6 rerun2 `relocation.json`의 `module_origins`(public/registration 126, owner 215, 전부 ROOT), `pth_files`, `caller_env_python_vars=[]`, 공백 경로·외부 cwd·전 단계 exit 0
4. T6 rerun2가 `<TR>` 밖에 배치되었는지(D6)
5. P2의 T4/T5 재실행과 registration 격리 설치 여부(D5)
6. D4 종결

## 확인 못 함

- release ZIP(P-final-2)과 T6 rerun2: 평가 시점에 없었다. 위 추가 보고 항목으로 미룬다.
- registration ZIP의 설치와 relocation: scratch 빌드도 하지 않았다. wheel은 public과 같다는 EVAL_1의 사실만 있다.
- T4/T5 전 과정(학습, 다운로드, export, docker 이미지)은 이번에 재실행하지 않았다. E10–E12는 설치, relocation, role 기동·정지까지만이다.
- scratch 빌드는 `--reproducible` 없이 1회만 했다. 그래서 ZIP sha는 release와 비교할 수 없다(재현성은 C10의 범위).
- `stop.sh`의 pid 재사용 경합(`R4/ops`)은 이번 C09 범위 밖이라 보지 않았다.
- shellcheck는 설치되어 있지 않다(R4와 같음).

## Addendum (final ZIPs) — 2026-10-28T11:5x+09:00

결론: **public과 registration에 대해서는 100점이 유지된다.** owner 부분은 설계자가 재빌드 sha를 보내 올 때까지 대기다.
Blocker 0, Critical 0, Major 0이다. 남은 Minor는 D7(DD-A71에 따라 1.5.1로 이월)과 owner 대기 항목(D4, D5, D6의 owner 부분)이다.

입력:
- `docs/internal/evidence/WP7/PFINAL2/SUMMARY.md`
- `PFINAL2/build/{build1,build2}/<p>/`
- `docs/internal/evidence/T6/rerun2/{public,registration,harness,owner}/`
- dist/ 실물

현재 트리 해시는 `sha256:1b4bd1a4a45196e3c558d62507f109301973eddc180df5968ed7014a72e6708c`다. DD-A73(core 수정, 릴리스 후보 트리)로 바뀌었다.
공개 4개 패키지 해시는 `--package` ×4로 다시 계산해도 `sha256:9859adb2…`로 변하지 않았다.

| 확인 항목 | 결과 | 근거 / 평가자 재실행 |
|---|---|---|
| 새 sha | 일치 | 아래 "새 sha 상세" 참고 |
| ZIP과 현재 트리 대조 | 일치 | public은 생성 파일과 wheel을 뺀 468개 파일이 현재 트리와 전부 같다. registration은 477개가 같고, 다른 1개는 `docs/registration/FILE_LIST.md`인데 이는 `[generated]`(`build/allowlists/registration.txt`) 생성물이다(`PFINAL2/build/file_list_check_shipped_registration.txt` check OK). → **D4는 public과 registration에 대해 CLOSED** |
| `<p>_wheels.txt` PASS 및 ZIP과의 동일성 | 일치 | 아래 "wheel 목록 상세" 참고 |
| relocation 행 | 일치 | 아래 "relocation 상세" 참고 |
| 배치(D6) | public·registration CLOSED | 아래 "D6 상세" 참고 |
| registration·public 격리 설치(D5) | CLOSED | 아래 "D5 상세" 참고 |
| 실제 role의 env | 일치 | `T6/rerun2/{public,registration}/doctor_strict.txt`: `env:gateway`, `env:runner` PASS(실제 gateway pid 55, runner pid 76), PASS=24, WARN=1(GPU 없음), FAIL=0, exit 0. start/stop exit 0. 샌드박스 pid namespace에 잔여 프로세스 없음 |
| D7 | OPEN, 이월 | 1.5.1로 이월(DD-A71). 운영 지침은 "verify는 깨끗한 셸에서". `T6/rerun2/*/verify_caller_env.txt`에 호출자 셸의 PYTHON* 변수가 `PYTHONDONTWRITEBYTECODE=1`뿐이었다고 기록되어 있다 |

새 sha 상세:
- 3곳이 같다: `sha256sum dist/*/*.zip`, `dist/*/*.sha256`, `PFINAL2/build/SHA256SUMS_dist.txt`.
  - public `bc0500bdfa43ddf5fb74bde4cf51a967e06f45bd2b901ba26da0794c1f0853d3`(2 716 913 B)
  - registration `dea510dfb54dd85da556ac38423f747a0a7d1afbff7398c501127bd0f8b92f6e`(2 792 584 B)
- BUILD_INFO: public `r-6ada31a4db777349`, registration `r-63bd409df0a7d79a`. source tree는 둘 다 `9859adb2…`(129 files). 현재 공개 4개 패키지 해시와 같다.
- `build_twice_console.txt`: 프로필별 두 빌드의 sha가 같다.

wheel 목록 상세:
- 평가자 도구 `$S3/scripts/check_wheel_listing.py`(`$S3` = scratchpad `c09_eval3/`)로 `PFINAL2/build/build{1,2}/{public,registration}/<p>_wheels.txt`를 dist ZIP 안의 wheel과 대조했다.
- 4개 wheel 모두 다음이 성립한다(public, registration 양쪽):
  - wheel sha와 bytes가 ZIP과 같다.
  - 멤버 행(sha, bytes)이 ZIP과 같다(27/49/23/30).
  - gate는 PASS(missing 0, extra 0, mismatch 0)이고, contracts의 package data는 3/3이다(HISTORY.json, openapi.json, openapi_runner.json).
- build1과 build2 목록은 바이트 단위로 같다.
- 출력은 `$S3/out/wheel_listing_check.txt`

relocation 상세:
- 대상: `T6/rerun2/{public,registration}/relocation_*.json`(2회분, 바이트 동일)
- 내용:
  - **module_origins 126행 전부 ROOT**(contracts 24, model_runtime 49, runner 23, server 30)
  - `caller_env_python_vars=[]`
  - `.pth`는 `coloredlogs.pth`와 `distutils-precedence.pth`, 둘 다 import 줄뿐
  - sys.path는 BASE ×3 + ROOT
  - 검사 8/8
- `verify_unit_relocation.txt`: unit 761 passed / 0 failed / 223 skipped / 44 deselected, relocation PASS, exit 0
- `verify_package.txt`: PASS, exit 0

D6 상세:
- 호스트의 실제 위치는 `<TR>/runtime/test_root/t6/rerun2/mbnas {public,registration} r2`로 TARGET_ROOT 안이다.
- 그러나 public과 registration의 설치와 실행은 `harness/sbx_t6r2.sh`의 bwrap 안에서 이뤄졌다. 트리는 **새 절대경로 `/srv/mbnas <p> r2`(공백 포함)**에 bind되었다.
- `isolation_proof.txt`: `/home`, 프로젝트 트리, etri_nas, `/data`, `/root`가 모두 `no`였다. 설치 시점의 네트워크 인터페이스는 lo뿐이었고 외부 연결은 Network unreachable였다.
- 따라서 부모 트리 의존이 있었다면 실패했을 조건에서 통과한 것이다. 배치 문제는 해소되었다.
- needle 2개(`verify.sh:136`)는 그대로지만, 이 동적 검사가 그 위험을 대신 덮는다. 결함으로 세지 않고 관찰 사항으로 남긴다.
- owner는 attempt1이 호스트의 `<TR>/runtime/test_root/t6/rerun2/mbnas owner r2`에서 실행되었다(`owner/attempt1_test_defect/install_owner.txt:1-3`). 재실행분을 보고 판정한다.

D5 상세:
- 프로젝트 증거: `T6/rerun2/{public,registration}/install_public_sandbox.txt`
  - cwd `/tmp`, `/home` 보이지 않음, 네트워크 lo뿐
  - `install.sh --offline --no-cache-dir`, `inside ROOT` 4행, exit 0
- 평가자 확인: 설치된 rerun2 트리를 같은 `/srv` 경로에 **읽기 전용**으로 bind하고 bwrap에서 실행했다(`/home`, 프로젝트 트리, etri_nas 보이지 않음, PYTHONPATH unset, `env -i`).
  - EVAL_1의 `c09_origin_probe.py` 결과: registration 126/126, public 126/126이 site-packages에 있다. 오류 0.
  - 리소스: openapi 1.5.0 `70e0cffa…`, openapi_runner **1.1.0** 80 942 B, operators 정상(`$S3/sbx_out/origin_{registration,public}_r2.json`)
  - 트리 안 wheel 4개의 sha가 dist ZIP 안의 wheel과 같고, BUILD_INFO build_id도 같다.

owner 대기:
- 현재 `dist/owner`의 `867cc577…`(11:27)는 BUILD_INFO source tree가 `f00987be…`다. 즉 DD-A73 이전에 빌드되었고, 현재 트리 `1b4bd1a4…`와 다르다.
- 설계자의 sha를 받으면 다음을 확인한다:
  1. BUILD_INFO source tree = `1b4bd1a4…`
  2. `owner_wheels.txt`(ZIP 동봉본과 `build/package_reports`) PASS, ZIP wheel과 동일, core package data 3/3
  3. owner T6 relocation 8/8, `module_origins` 215행 전부 ROOT, `caller_env_python_vars=[]`, cwd `/` 재호출
  4. P2 T4 격리 설치
- 이것이 확인되면 owner 부분 D4, D5, D6을 닫는다.

점수: import_origin 25, package_data 25, clean_env 25, relocation 25 = **100**. public/registration 기준으로 **PASS가 유지된다.** owner 확인 후 최종 확정한다.

## Owner addendum (DD-A73 ZIP 53e6a1f0…) — 2026-10-28T13:0x+09:00

결론: **100점(PASS)이 확정이다.** 세 프로필 모두 최종 ZIP 기준이다. Blocker 0, Critical 0, Major 0이고, 남은 것은 Minor D7(DD-A71에 따라 1.5.1로 이월) 하나다.

- tree hash: `sha256:1b4bd1a4a45196e3c558d62507f109301973eddc180df5968ed7014a72e6708c`. 13:03과 13:08에 같았다.
- `sha256sum dist/*/*.zip`: owner `53e6a1f0…`, public `bc0500bd…`, registration `dea510df…`.
- 평가자 산출물은 `$S4` = scratchpad의 `c09_eval4/`에 있다.

| # | 항목 | 판정 | 근거(읽은 파일, 실행) |
|---|---|---|---|
| 1 | owner sha와 BUILD_INFO 트리 해시 | **PASS** | 아래 "항목 1 상세" 참고 |
| 2 | 동봉된 `owner_wheels.txt`가 PASS이고 ZIP의 `wheels/`와 같은지 | **PASS** | 아래 "항목 2 상세" 참고 |
| 3 | attempt3의 relocation, 호출자 env, cwd `/` 재호출 | **PASS** | 아래 "항목 3 상세" 참고 |
| 4 | 배치(TARGET_ROOT 밖) | 아래 판정 참고 | 아래 "항목 4 상세" 참고 |
| 5 | a43a8a2e… 대비 FILE_MANIFEST 차이 범위 | **PASS** | 아래 "항목 5 상세" 참고 |
| 6 | D4 | **CLOSED (3종 모두)** | owner BUILD_INFO source tree가 현재 트리와 같다(1). core wheel 92/92가 현재 `mbnas_core/src`와 내용 차이 0이다. 공개 wheel 4개는 owner·public·registration ZIP 사이에서 sha가 같다(contracts `a947997e…`, model_runtime `65a13736…`, runner `a8401c99…`, server `2243162f…`) |

항목 1 상세:
- `sha256sum` = `.sha256` = `53e6a1f0cf100cb813ff500d4936a4dcf03feea623886b43879c51de572434c9`, 82 702 282 B, 3246 멤버(`unzip -l`)
- BUILD_INFO `build_id r-99eeb029485c9de8`, `source_tree` = `1b4bd1a4…` (221 files). 현재 트리와 같다.
- `PFINAL2/owner_c/build/build_twice_console.txt`: 두 빌드의 sha가 같다.
- `check_owner_c.txt`: manifest == members, digest mismatch 0

항목 2 상세:
- 평가자 도구 `check_wheel_listing.py`로 세 목록을 owner ZIP 안의 wheel 5개와 대조했다:
  - ZIP에서 `unzip -p`로 꺼낸 `docs/internal/evidence/package_build/owner_wheels.txt`
  - `PFINAL2/owner_c/build/build{1,2}/owner_wheels.txt`
- 결과:
  - 5개 wheel 모두 wheel sha와 bytes가 같고 멤버 행(sha, bytes)도 같다(27/49/23/30/92).
  - gate는 모두 PASS다. contracts와 core의 package data는 3/3이다.
  - `mbnas_core` wheel은 `a2d97415…`다.
  - 세 목록은 바이트 단위로 같다.
- 출력: `$S4/out/owner_wheel_listing_check.txt`

항목 3 상세 (`T6/rerun2/owner/attempt3_dd_a73_final/`):
- `relocation_20261028T033646Z-owner.json`:
  - **215행 전부 ROOT**(core 89 포함)
  - `caller_env_python_vars=[]`
  - `.pth`는 import 줄뿐
  - sys.path는 BASE ×3 + ROOT
  - 검사 8/8
- `verify_owner.txt`: unit 2747 passed / 0 failed / 56 skipped, integration 17/17, relocation PASS, exit 0. `verify_owner_package.txt`: PASS.
- `verify_caller_env.txt`: 호출자 셸에는 `PYTHONDONTWRITEBYTECODE=1`과 `OLDPWD=<TR>`만 있었다(PYTHONPATH, PYTHONHOME, PYTHONSTARTUP, PYTHONUSERBASE, VIRTUAL_ENV 없음).
- `doctor_owner_strict.txt`: `env:core|owner-worker|gateway|runner` PASS(실제 role), PASS=32.
- cwd `/` 재호출:
  - `reinvoke_start_core_from_root.txt` exit 0
  - `reinvoke_doctor_from_root.txt` exit 0, PASS=30, `env:core`와 `env:owner-worker` PASS
  - `reinvoke_stop_from_root.txt` exit 0
  - `leftover_check.txt`: 남은 프로세스 0

항목 4 상세:
- **attempt3의 추출 경로(`<TR>/runtime/test_root/t6/rerun2/mbnas owner r2c`, 호스트에서 샌드박스 없이 실행)는 "TARGET_ROOT 밖 배치" 조건을 충족하지 않는다.** 그 경로에서는 부모 트리가 보인다.
- 충족하는 근거는 다음 두 가지다.
  - (a) public/registration: 앞 addendum의 T6 rerun2 bwrap 실행. 트리를 `/srv/mbnas <p> r2`로 다시 bind했고 `/home`이 보이지 않았다.
  - (b) owner: **이번 평가자 실행**. 평가자 소유의 빈 디렉터리 `$S4/owner_x`에 owner ZIP을 풀고, bwrap 안에서 새 경로 `/srv/mbnas owner c09`(공백 포함)에 bind했다. `/home`, `<TR>`, etri_nas, `/data`가 보이지 않았고, 네트워크와 `/dev/nvidia*`도 없었다. 외부 cwd `/tmp/elsewhere`에서:
    - `install.sh --profile owner --offline --wheelhouse /opt/wheelhouse`: **exit 0**(91 s), `--no-cache-dir`, `inside ROOT` 5행
    - `verify --suite relocation`: **8/8**, 215행 전부 ROOT, `caller_env_python_vars=[]`
    - 리소스 probe: 215/215, schema.sql `84240467…`(18 CREATE TABLE), openapi 1.5.0, runner openapi 1.1.0
    - PYTHONPATH(종료하는 그림자 `mbnas_server`), PYTHONHOME, PYTHONSTARTUP, PYTHONUSERBASE가 섞인 호출자 env로 `start.sh` 실행: **exit 0**. 4개 role env에 남은 Python 변수는 `PYTHONNOUSERSITE`뿐이고, doctor `env:` 4개 PASS, stop 0, 남은 프로세스 0이다.
  - 출력: `$S4/out/owner_final_console.txt`, `$S4/out_sbx/`
- 판정: **D6 CLOSED**. public/registration은 프로젝트 증거로, owner는 평가자 증거로 닫는다. owner의 프로젝트 쪽 격리 증거는 P2 T4 재실행에서 나온다.
- relocation 기준 자체(다른 절대경로, 공백 경로, 외부 cwd에서 제공 명령 동작)는 attempt3만으로도 충족한다. 그래서 점수 25는 유지된다.
- `verify.sh:136`의 needle이 2개뿐인 점은 관찰 사항이다(결함으로 세지 않음).

항목 5 상세:
- 비교 대상: `pfinal2/builds/build1/owner/…zip`(a43a8a2e…, 읽기 전용)과 dist의 53e6a1f0…
- `FILE_MANIFEST.jsonl`의 `relpath` 기준으로 직접 대조했다. 추가 461, 삭제 0, 변경 17.
- docs/internal 밖의 변경은 정확히 다음뿐이다:
  - 추가: `tests/owner/api/test_dd_a73_bind_structure_mixed.py`
  - 변경: `BUILD_INFO.json`, `PACKAGE_CONTENTS.md`, `PACKAGE_PROFILE.json`(build-metadata), `mbnas_core/src/mbnas_core/training_runs/selection.py`, `tests/public/packaging/test_package_contents_and_gates.py`, `wheels/mbnas_core-0.1.0-py3-none-any.whl`
- 나머지는 모두 `docs/internal/**`다(추가는 T6 207, WP7 188, T5 63, FINAL_REPORT.md 1, CRITERIA 1. 변경 11).
- `selection.py`의 차이는 DD-A73 블록(+18줄)뿐이다. 패키지 내부 import(`model_services.MIXED_NAMES`, `mixed_pool.bridge.load_etri_mixed`)만 쓰고, `load_etri_mixed`는 vendored 모듈만 적재한다(`bridge.py:107-`: `sys.path`나 외부 root를 쓰지 않음). 폐쇄성에 영향이 없다.
- ZIP 안의 `selection.py`는 현재 트리와 같다.
- 출력: `$S4/out/manifest_diff.txt`

| 기준 | 점수 | 앵커 |
|---|---|---|
| import_origin | 25 | 최종 3종 모두 relocation 로그에 모듈별 origin 표(126/126/215, 전부 ROOT)가 있고, 실제 role의 `env:` PASS. 평가자 재실행으로 확인 |
| package_data | 25 | 최종 3종의 wheel 목록과 게이트 PASS가 ZIP과 같다. 설치본에서 리소스 로드도 성공(T6 unit과 평가자 probe) |
| clean_env | 25 | public/registration은 T6 rerun2 bwrap, owner는 평가자 bwrap(최종 ZIP). 기존 T4/T5도 열람했다 |
| relocation | 25 | T6 rerun2 3종(공백 경로, 외부 cwd, cwd `/` 재호출) + 평가자 `/srv` 재배치 실행 |
| **합계** | **100** | **PASS**: Blocker 0, Critical 0, Major 0, Minor 1(D7 이월) |

확인 못 함(이 addendum):
- P2 T4/T5를 최종 owner ZIP으로 재실행한 결과(학습, 다운로드, export, docker)는 아직 없다.
- 예정된 docs-only owner 재빌드(DD-A70)는 이후의 일이다. 그 FILE_MANIFEST 차이가 `docs/internal/**`와 build metadata로 한정되는지는 그때 대조해야 한다. 이 보고서 자체도 owner ZIP에 동봉되므로 이번 추가로 다시 바뀐다.
- registration을 `unshare -rn`으로 설치하는 것과 `requirements-cpu.txt` 오프라인 설치는 PFINAL2 SUMMARY에 미시험으로 적혀 있다. C09 앵커 밖이다.

## Confirmation (DD-A75 ZIP 9f03c484…) — 2026-10-28T15:5x+09:00

결론: **ZIP 3종 {owner `9f03c484…`, public `bc0500bd…`, registration `dea510df…`}에 대해 100점(PASS)을 확인한다.** Blocker 0, Critical 0, Major 0이다. Minor 2개는 모두 1.5.1 이월분이다(아래).

**단, 평가 시점(15:53–15:56)에 작업 트리가 DD-A76(배포본 정리, 15:3x)으로 계속 바뀌고 있었다.**
- tree hash가 `8c3bfae2…`(15:53)에서 `5da47f98…`(15:56)로 바뀌었다. 15:30 이후 변경된 파일은 134개이고, 그중 패키지 `.py`는 15:55까지 수정되었다.
- DD-A76은 DD-A70을 대체하고 ZIP 3종을 모두 재빌드하도록 정한다.
- 따라서 이 확인은 위 ZIP 3종에 한정된다. CLEAN1 ZIP이 나오면 C09를 다시 채점해야 한다(맨 아래 "주의" 참고).

| 항목 | 판정 | 근거(읽은 파일, 실행) |
|---|---|---|
| sha와 BUILD_INFO | **PASS** | `sha256sum` = `.sha256` = `9f03c484bbc8c0a2227cc325658892e3efe286d08cca9a1978383158075c2f2a`, 85 188 530 B, 3693 멤버. BUILD_INFO `r-07c7cef95cd73775`, source tree `sha256:8fc93f2a596891174c2884e1697fd6003377b713102c0b21b01eb45d3f6f7312`(221). `PFINAL2/owner_d/build/build_twice_console.txt`: 두 빌드의 sha가 같다 |
| `owner_wheels.txt` PASS 및 ZIP과의 동일성 | **PASS** | ZIP 동봉본과 `owner_d/build/build{1,2}/owner_wheels.txt`를 평가자 `check_wheel_listing.py`로 ZIP의 wheel 5개와 대조했다. wheel sha와 멤버 행(27/49/23/30/92)이 같고, gate는 모두 PASS다(contracts 3/3, core 3/3). 세 목록은 바이트 단위로 같다(`$S5/out/owner_d_wheel_listing_check.txt`, `$S5` = scratchpad `c09_eval5/`) |
| 53e6a1f0… 목록 대비 wheel 5개의 sha | **PASS** | 아래 "wheel sha 대조 상세" 참고 |
| FILE_MANIFEST 차이(53e6a1f0 → 9f03c484) | **PASS** | 아래 "FILE_MANIFEST 차이 상세" 참고 |
| T6 attempt4 relocation.json 등 | **PASS** | 아래 "T6 attempt4 상세" 참고 |
| 배치(TARGET_ROOT 밖)와 격리 설치 | **PASS (프로젝트 증거)** | 아래 "배치와 격리 설치 상세" 참고 |
| 평가자 bwrap 설치 probe 재실행 | **안 함 (판단)** | 아래 "재실행하지 않은 이유" 참고 |

wheel sha 대조 상세:
- contracts `a947997e…`, model_runtime `65a13736…`, runner `a8401c99…`, server `2243162f…`는 그대로다. public·registration ZIP의 wheel과도 같다.
- 바뀐 것은 `mbnas_core`뿐이다(`a2d97415…` → `2348b6e3…`). core wheel 안에서 바뀐 멤버는 `mbnas_core/console/projection.py` 하나다.

FILE_MANIFEST 차이 상세:
- 비교 대상: `pfinal2/builds_owner_c/build1`의 53e6a1f0 사본(읽기 전용)과 dist의 9f03c484
- 3245 → 3692행. 추가 451, 삭제 4, 변경 21.
- docs/internal 밖:
  - 추가: owner 시험 5개(`tests/owner/api/test_dd_a75_*` 2, `tests/owner/projection/test_dd_a75*` 2, `tests/owner/qa/test_wp3_smoke_fleet_mode.py`)
  - 변경: `BUILD_INFO.json`, `PACKAGE_CONTENTS.md`, `PACKAGE_PROFILE.json`, `mbnas_core/src/mbnas_core/console/projection.py`, `scripts/dev/wp3_three_process_smoke.py`, `wheels/mbnas_core-…whl`
  - 삭제: 0
- 나머지는 모두 `docs/internal/**`다.
- `projection.py`의 차이는 DD-A75 F1/F1b 블록(`_path_values` 추가, `_raw_metrics`가 `scrub_deep(_path_values(raw))`를 반환)과 docstring뿐이다. **import 줄 변화가 없고**, 경로를 읽거나 쓰지 않으며 package data에도 손대지 않는다. 폐쇄성에 영향이 없다.
- 출력: `$S5/out/manifest_diff_53e6_9f03.txt`

T6 attempt4 상세 (`T6/rerun2/owner/attempt4_dd_a75/`):
- `ZIP_USED.txt`: 9f03c484, tree 8fc93f2a
- `relocation_20261028T062616Z-owner.json`:
  - **215행 전부 ROOT**
  - `caller_env_python_vars=[]`
  - `.pth`는 import 줄뿐
  - sys.path는 BASE ×3 + ROOT
  - 검사 8/8
- `verify_owner.txt`: unit 2829 passed / 0 failed, integration 17/17, relocation PASS
- `verify_owner_package.json`: manifest 3692/3692, mismatch 0, PASS
- `verify_caller_env.txt`: 호출자 셸에는 `PYTHONDONTWRITEBYTECODE=1`과 `OLDPWD`만 있었다.
- `doctor_owner_strict.txt`: `env:core|owner-worker|gateway|runner` PASS, PASS=32
- cwd `/` 재호출: start, doctor(PASS=30), stop이 모두 exit 0이다. `leftover_check.txt`: 0

배치와 격리 설치 상세:
- T6 attempt4의 추출 경로 `<TR>/runtime/test_root/t6/rerun2/mbnas owner r2d`는 TARGET_ROOT 안이고 호스트에서 실행되었다. 그래서 그 자체로는 배치 조건을 충족하지 않는다.
- 대신 **P2 T4 attempt_dd_a75**(`T4/rerun2/attempt_dd_a75/`)가 이 ZIP을 **TARGET_ROOT 밖(scratchpad `p2a75`)**에 풀어 bwrap에서 실행했다.
  - `harness/sbx2.sh`로 WORK, wheelhouse, harness, 기반 py만 보이게 했다. 네트워크는 lo뿐이고 GPU 노드는 없다.
  - `t4a75_local_console.txt:2`: 원본 트리, etri_nas, /data가 모두 보이지 않았다.
  - 공백과 유니코드가 섞인 경로(`t4 공백 rootX7kQ2`)에서 install --offline exit 0, integration 17/17, relocation 8/8(215행 ROOT, `caller_env_python_vars=[]`), package PASS
  - 짧은 경로(`/r/s 1/…`)에서도 relocation 8/8(215행 ROOT)
- 이로써 owner의 배치·격리 항목은 **프로젝트 증거로도** 닫힌다. 직전 owner addendum의 평가자 증거를 대체한다.

재실행하지 않은 이유:
- 런타임 변경은 core wheel 안의 `projection.py` 하나다. import, 경로, package data가 바뀌지 않았다.
- 같은 ZIP에 대해 P2 T4(격리, TARGET_ROOT 밖, 경로 2종)와 T6 attempt4가 이미 실행되었다.
- 디스크 사용률이 87%다.
- P2의 설치 트리는 이미 삭제되어 읽기 전용 probe도 할 수 없었다.
- 이번에 평가자 추출본은 만들지 않았다. 53e6a1f0과의 비교는 ZIP 멤버를 직접 읽어서 했다.

| 기준 | 점수 | 앵커 |
|---|---|---|
| import_origin | 25 | relocation 로그의 모듈별 origin 표(owner 215, public/registration 126, 전부 ROOT), 실제 role의 `env:` PASS. 증거를 열람하고 평가자 대조로 확인 |
| package_data | 25 | 최종 3종의 wheel 목록이 gate PASS이고 ZIP과 같다. 설치본 리소스 로드는 T6 unit(owner 2829, public/registration 761)과 T4 하네스(schema v6)로 확인 |
| clean_env | 25 | owner는 P2 T4 bwrap(TARGET_ROOT 밖), public/registration은 T6 rerun2 bwrap |
| relocation | 25 | T6 rerun2 3종 + T4의 두 경로(공백+유니코드, 짧은 경로), 외부 cwd, cwd `/` 재호출 |
| **합계** | **100** | **PASS**: Blocker 0, Critical 0, Major 0, Minor 2(아래) |

남은 결함(Minor, 둘 다 1.5.1 이월로 기록됨):
- **D7**: `verify.sh` `base_env`가 호출자의 PYTHON* 변수를 suite에 넘긴다(DD-A71). T6 증거에는 호출자 env가 함께 기록된다.
- **DD-A75 F3 (#13)**: `scripts/doctor.sh:171-179`와 `scripts/verify.sh:192-200`(9f03c484 ZIP 사본의 `script_interpreter`)이 pip `/bin/sh` 트램펄린에서 따옴표가 있는 경로만 파싱한다. 그래서 공백 없는 긴 설치 경로에서는 entry_points와 "console scripts bound" 검사가 거짓 FAIL을 낸다(명령 자체는 동작). relocation 도구의 한계로, 이번에 C09 결함 목록에 추가한다. 평가자가 직접 재현하지는 않았다. T6/T4 증거의 경로는 모두 공백을 포함하므로 점수에는 영향이 없다.

확인 못 함:
- P2 T5와 T8 runtime의 9f03c484 결과는 열람 범위 밖이다.
- 평가자 자신의 설치 probe는 위 이유로 하지 않았다.

**주의 — DD-A76 CLEAN1 재채점 때 C09에서 볼 점:**
- (1) tests/**가 출하되지 않으므로 배포본의 `verify --suite unit`이 NOT_APPLICABLE이 된다(`CLEAN_DISTRIBUTION_SPEC.md` §4). 그러면 package_data의 "설치본에서 리소스 로드 테스트"를 뒷받침하던 T6 unit 경로가 사라진다. 다음 중 하나로 실행 증거를 대신 남겨야 한다:
  - 선언된 package data를 설치본에서 모두 적재하는 doctor/verify 검사(EVAL_1 D3에서 제안한 단계)
  - T6/T4 API 흐름에서 contract 버전, presets(handoff), schema 초기화를 명시적으로 기록
- (2) DD-A76 §3의 주석 정리로 패키지 소스가 바뀐다. 다음을 다시 확인해야 한다:
  - wheel gate
  - `openapi*.json` sha 불변(R3)
  - relocation 215/126행
  - `relocation.json` 기록 형식, 그리고 verify.sh 변경(§4) 이후에도 relocation과 package suite가 그대로인지

### Confirmation 보충 — ZIP 자체 대조와 트리 변동 (2026-10-28T15:59+09:00)

설계자의 NOTICE에 따라 9f03c484 ZIP을 **ZIP이 담은 자기 소스**와 대조했다. 작업 트리와의 대조는 관찰로만 남긴다.

- **자기 일관성: PASS.**
  - ZIP 안의 `mbnas_*/src`만 평가자 소유의 빈 디렉터리 `$S6/zipsrc`(`$S6` = scratchpad `c09_eval6/`)에 풀었다.
  - ZIP에 들어 있는 `mbnas_contracts/util/source_hash.py`를 경로로 직접 적재해 mbnas-source-tree/2 digest를 계산했다. 결과는 **`sha256:8fc93f2a…`(221 files)로 BUILD_INFO `source_tree`와 같다.**
  - wheel 5개의 payload(dist-info 제외)는 ZIP 자신의 `<pkg>/src`와 멤버, sha 모두 같다. 차이 0이며 core `2348b6e3…` 92/92 포함이다.
  - 도구는 `$S6/scripts/zip_self_check.py`, 출력은 `$S6/out/zip_self_check.txt`
- **관찰 (결함 아님, DD-A76)**: 작업 트리는 다른 세션의 docstring/provenance 정리로 바뀌고 있다.
  - 관찰한 tree hash는 15:53 `8c3bfae2…`, 15:56 `5da47f98…`, 15:59 `308a16519c04fdfffeedf0d70074e2756737514e82da39cec65f0acbd11fde7d`다.
  - 15:59 기준으로 ZIP의 패키지 소스 221개 중 137개가 작업 트리와 다르고, 트리에 없는 파일은 0개다.
  - 검증된 릴리스 트리는 BUILD_INFO의 `8fc93f2a…`다. 위 PASS 판정은 ZIP 자체에 대한 것이므로 이 변동과 무관하다.
  - 직전 owner addendum의 "core wheel = 현재 소스" 대조는 당시 트리(`1b4bd1a4…`) 기준으로만 유효하다.
- 점수는 변하지 않는다(25/25/25/25 = **100, PASS**). 남은 결함은 Minor 2(D7, DD-A75 F3 #13, 둘 다 이월)다.

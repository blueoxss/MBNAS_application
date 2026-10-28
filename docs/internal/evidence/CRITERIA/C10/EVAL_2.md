# C10 ZIP 배포 — 독립 평가 EVAL_2 (재채점 2차, 최종 ZIP 3종)

- set: **C10** (`docs/internal/criteria/C10.json` v1.0, sha256 `14abb1ff…`). 앵커는 EVAL_1과 같은 25/20/15/0을 그대로 적용했다.
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자. 채점만 하고 결정은 설계자가 한다.
- time: 2026-10-28T13:03+09:00 시작, 13:2x+09:00 보고 작성.
- tree hash (`runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`, mbnas-source-tree/2, 221 files):
  `sha256:1b4bd1a4a45196e3c558d62507f109301973eddc180df5968ed7014a72e6708c`
  - 시작(13:03)과 끝(13:22)의 값이 같다. 평가 중 트리 변경은 없었다.
  - public 4패키지 해시는 `sha256:9859adb2…`로, public/registration BUILD_INFO와 같다.
- 평가 대상 ZIP과 `sha256sum -c` 결과(dist/<profile>/에서 실행, 3개 모두 OK·exit 0):
  - public `bc0500bdfa43ddf5fb74bde4cf51a967e06f45bd2b901ba26da0794c1f0853d3` (2 716 913 B, 476 멤버)
  - registration `dea510dfb54dd85da556ac38423f747a0a7d1afbff7398c501127bd0f8b92f6e` (2 792 584 B, 486 멤버)
  - owner `53e6a1f0cf100cb813ff500d4936a4dcf03feea623886b43879c51de572434c9` (82 702 282 B, 3246 멤버)
- 환경
  - CPU 전용(`CUDA_VISIBLE_DEVICES=""`, `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `--basetemp`는 scratchpad).
  - GPU, etri_nas, build/source_snapshot, /data에는 접근하지 않았다. 압축 해제는 매번 새 빈 디렉터리에 했다.
  - 프로젝트에 쓴 파일은 이 파일 하나다. 평가자 산출물은 `$SCRATCH/wp3/c10eval/`의 `out2/`, `rebuild2/`, `x2/`, `perf/`, `posctl2 work/`에 있다. `$SCRATCH`는 이 세션의 scratchpad다(경로에 계정명이 있어 풀어 쓰지 않는다).
  - CPU 사용량은 약 19분으로, 지침의 15분을 넘었다. 원인은 D8의 스캐너 이차 시간이다.
    - 첫 owner 재빌드는 8분 49초가 지나도 소스 검사 단계였다. 그래서 중단했다(exit 143).
    - 이어서 docs/internal 증거 덤프 2개(`PFINAL2/owner_c/t8/zip_leak_scan_owner.{json,txt}`)를 사본에서 빼고 다시 빌드했다(4분 40초).
    - 이렇게 빼도 비교 규칙(docs/internal 차이는 허용)에 걸리지 않는다.

## 결론
총점 **100/100**(25+25+25+25), Blocker 0, Critical 0 → **PASS**(총점 > 90, Blocker·Critical 0).

- EVAL_1 결함 7건(D1–D7)이 모두 **FIXED**다. 각 판정에는 파일:줄, 재실행, 테스트 증거를 붙였다(아래 표).
- 가장 강한 확인은 세 가지다.
  - **독립 재빌드**(공백 포함 scratch 경로, `--reproducible`): public과 registration이 dist/와 **바이트 단위로 같다**(`bc0500bd…`, `dea510df…`). registration은 FILE_LIST placeholder가 있을 때와 없을 때 모두 같다.
  - owner 재빌드(`a0147d7e…`)와 dist `53e6a1f0…`의 FILE_MANIFEST를 행 단위로 대조했다.
    - 3234행이 같다. 추가 42행은 모두 docs/internal이고 삭제는 0행이다. 변경 11행은 docs/internal 기록 8개와 메타데이터 3개(BUILD_INFO, PACKAGE_PROFILE, PACKAGE_CONTENTS; build id만 다름)다.
    - **docs/internal과 메타데이터 밖의 차이는 0**이다. 비문서 멤버 767개(wheel 5개 포함)는 바이트 단위로 같다.
  - T6 rerun2 트리 3개(`runtime/test_root/t6/rerun2/mbnas {public r2, registration r2, owner r2c}`)가 최종 ZIP과 바이트 단위로 같다(475/475, 485/485, 3245/3245).
- 새 결함은 **D8(Minor)** 1건이다. 점수에는 영향이 없다.
  - `canary_scan.py`가 매치마다 줄 번호를 처음부터 다시 세서 시간이 이차로 늘어난다(1 MB 1.07 s, 2 MB 3.6 s, 4 MB 12.7 s).
  - owner allowlist가 T8 owner 스캔 덤프 `PFINAL2/owner_c/t8/zip_leak_scan_owner.{json,txt}`(30 MB + 19 MB, 계정명 매치 각 97 822건)를 싣는다. 이 덤프는 스캔할 때마다 커진다(2.8 MB → 9.9 MB → 30 MB).
  - 그래서 DD-A70 docs-only 최종 owner 재빌드는 수십 분이 걸릴 것으로 추정한다. 덤프는 압축하면 1.3 MB라 ZIP 크기 영향은 작지만, 압축을 풀면 49 MB다.
  - **DD-A70 재빌드 전에 처리하기를 권한다.**

## 기준별 채점

### 1. naming_allowlist — **25/25** (앵커 25)
**근거**
- 이름 규칙: `WORK_PACKAGES/WP-7_OPS_PACKAGING.md:13`(owner는 `-owner-private-`).
- package.sh 로그(모두 exit 0, "two builds IDENTICAL", 19개 검사 표의 모든 행 PASS; owner는 17/19가 실행됐고 deny_importers와 private_package는 프로필상 not run):
  - `PFINAL2/build/build{1,2}/{public,registration}/package.log`
  - `PFINAL2/owner_c/build/build{1,2}/package.log`
- allowlist 현재 sha256이 각 ZIP BUILD_INFO의 `allowlist.sha256`과 같다: public `62cc9656…`, registration `c960f62b…`, owner `e5eb88fc…`.
- packager `930e98aa…`, scanner `5de3402c…`, tree-hash 도구 `307551b1…`도 BUILD_INFO 기록과 같다.

**재실행과 결과**
- `zipaudit2.py` ×3:
  - 이름 정규식 3/3 일치. version 0.1.0이 VERSION 파일, BUILD_INFO, wheel 이름과 같고, platform은 `linux_x86_64`다.
  - 모든 entry가 최상위 폴더 `mbnas_application/` 하나 아래에 있다(476/486/3246).
- staging 대 ZIP(`tree_vs_zip.py`, `build/staging/<p>/mbnas_application`): 3/3 동일, 추가 파일 0.
- allowlist 재선택(`drift.py`, 현재 트리):
  - public ZIP의 tree 행 468개는 현재 선택 472개에서 deny-importers 4개를 뺀 것과 같다.
  - registration 477개 = 482 − 4 − 생성 파일 FILE_LIST.md 1개.
  - 세 ZIP 모두 allowlist 밖 tree 행은 0이다.
- **독립 재빌드**(`package_build.py --root "$SCRATCH/…/rebuild2/<p> root" --reproducible`):
  - public: exit 0, 19/19 PASS, **`bc0500bd…` 동일**(18.9 s).
  - registration: placeholder 없이 **`dea510df…` 동일**, placeholder가 있어도 **`dea510df…` 동일**(generated_shadow는 보고서 info 63에만 있고 BUILD_INFO에는 들어가지 않는다).
  - owner: exit 0, 17/19 PASS(나머지 2개는 프로필상 not run), 2회 IDENTICAL(`71d40ffe…`), ZIP `a0147d7e…`(82 882 397 B).
    - 단, 원본 사본 그대로의 첫 시도는 D8 때문에 8분 49초 뒤 중단했다.
    - `manifest_diff.py`(dist 대 재빌드): 같음 3234행, 추가 42(전부 docs/internal), 삭제 0, 변경 11(docs/internal 8, 메타데이터 3).
    - **예상 밖 차이 0, 비문서 멤버 767/767 바이트 동일.** wheel 5개, 소스, 테스트, scripts, web dist가 포함된다.
    - PACKAGE_PROFILE과 BUILD_INFO의 차이는 build id(내용 유래)뿐이다. source_tree, packages(wheel sha), allowlist, tools는 같다.

### 2. public_private — **25/25** (앵커 25)
**근거**
- `PFINAL2/canary/canary_scan_{public,registration}.*`, `PFINAL2/owner_c/canary/*`
- `PFINAL2/t8/runtime_scan_summary.json`(responses·logs·/proc 4개 스캔, 124 needles, 모두 0건)
- `T6/rerun2/{public,registration}/loader_check.txt`(exit 0, mbnas_core loader·spec 없음)
- DD-A72(2): owner의 docs/internal 밖 host fact 38건/12파일은 owner-private에 한해 수용됐다.

**재실행과 결과**
- 멤버 grep(37개 패턴, public/registration): **전부 0건**이다. `mbnas_core`, `tests/owner`, `deploy/owner`, `docs/internal`, `canary`, `runtime/`, `config/<profile>/`, `.env`, `LICENSE_STATUS`, `fitness_oracle`, `test_no_host_facts`, `scripts/{dev,owner,runner}`, `package.sh`, `tests/public/packaging`, `harness.py`, `qa_handlers.py`, `requirements-owner`, `config/examples/{core,owner}*`, `*.map`, `.pyc`, `build/`, `wheelhouse`, `.pt/.pth/.onnx/.db/.sqlite/.pem/.key/id_rsa/.log`, `mbnas_core-` 등이 대상이다.
  - owner ZIP도 `runtime/`, `config/<profile>/`, `.env`, `docs/internal/canary`, `SOURCE_SNAPSHOT_*`, build/dist, DB, 키, 가중치가 0건이다.
- `canary_scan.py --manifest … --profile <p>`:
  - public: 624 files, **0 hits**, exit 0
  - registration: 634 files, **0 hits**, exit 0
  - owner: 3553 files, canary 0, failing 0, host fact 5003건은 정보성, exit 0(2분 1초)
    - docs/internal 밖 host fact는 38건/12파일이다. 목록이 DD-A72(2)와 정확히 일치한다: `check_import_closure.py` 10, `test_no_host_facts.py` 5, owner 스크립트와 레거시 테스트의 원본 호스트 데이터 경로 기본값 21, `t2_core.py` GPU UUID 2.
- **양성 대조 1회**(`canary_posctl.py`, 출력은 `$SCRATCH/…/posctl2 work`만, dist/ 무관):
  - canary 영역 9/9 ZIP: exit 1, FAIL.
  - 중첩 wheel 은닉 ZIP: exit 1, 18 hits.
  - host fact ZIP: exit 1.
  - 깨끗한 ZIP: exit 0.
  - 프로젝트 쪽 end-to-end 양성 대조 `PFINAL2/owner_c/canary/canary_positive_control_owner.log`(owner `--canary` 빌드, 8/8 영역 "present in owner")는 열람만 했다.
- 내용 스캔(`core_refs.py`, 중첩 wheel 포함):
  - `import/from mbnas_core`, 동적 import, requirement와 entry point 참조가 모두 0이다.
  - 문자열 참조 2개는 음성 단언 테스트다.
  - 산문 언급은 public 50파일, registration 56파일이다(DD-A41 f).
- **"env만 바꿔도 코어 미출현"**(새로 푼 public 트리 `$SCRATCH/…/x2/public neg tree`):
  - `install.sh --profile owner`: exit 1(requirements-owner.txt 없음).
  - `--requirements requirements-public.txt`를 붙여도 exit 1(`this is a public package (no mbnas-core)`, `scripts/install.sh:203-205`). venv가 만들어지지 않는다.
  - `start.sh --profile owner --role core`: exit 1.
- runtime 쪽(열람):
  - T6 rerun2 public과 registration은 bwrap에서 실행됐다(네트워크 인터페이스 없음, `/home` 비가시).
  - T8 runtime 4개 스캔이 0건이다(정규화 없이).
  - sandbox 경로에는 계정명이 없어서, 이 0건만으로는 OLDPWD 제거를 증명하지 못한다. D6은 코드(`scripts/start.sh:344, 356, 360`)와 테스트(`test_ops_role_env.py` 통과)로 판정했다.

### 3. final_unpack_run — **25/25** (앵커 25)
**근거**
- 하네스: `T6/rerun2/harness/console_extract.txt:2-3`. public은 `bc0500bd…`, registration은 `dea510df…`에서 풀었다.
- public(`T6/rerun2/public/`):
  - install --offline 0, doctor --strict 0(시작 전 22 PASS/1 WARN, 시작 후 24 PASS/1 WARN; WARN은 sandbox에 GPU가 없는 것), start 0
  - API 17호출(`api_probe.txt`): health 200, 무토큰 401, presets, `/`, upload, structure-check PASS, 깨진 YAML FAIL(MERGE_INPUT_MISMATCH·SHAPE_MISMATCH), validate valid, run COMPLETED(후보 2), snapshot, 후보 상세, config-revisions, export 11 512 B, runs
  - loader 0, verify unit **761/0**(223 skipped, 44 deselected), relocation 8/8, package 475/475, stop 0
- registration: install 0, doctor 0, start 0, API 6호출(health, presets, `/`, upload, structure-check PASS), loader 0, unit 761/0, relocation 8/8, package 485/485, stop 0.
- owner(`T6/rerun2/owner/attempt3_dd_a73_final/`, `ZIP_USED.txt` = `53e6a1f0…`):
  - install 0, start 0(4개 역할), doctor --strict 32 PASS
  - verify unit **2747/0**(56 skipped), integration 17/17, relocation 8/8, package 3245/3245·0 hits, stop 0
  - cwd `/`에서 재호출: start --role core 0, doctor 0, stop 0, 잔존 프로세스 0

**재실행과 결과**
- **T6 트리와 최종 ZIP의 동일성**(`tree_vs_zip.py`): `mbnas public r2` 475/475, `mbnas registration r2` 485/485, `mbnas owner r2c` 3245/3245로 sha가 모두 같다.
  - 추가된 것은 `runtime/`과 install이 만든 `config/<profile>/*.env`뿐이다.
  - 따라서 위 로그는 정확히 최종 ZIP 3종의 압축 해제본에서 나왔다.
- 이번 라운드에서 내가 새로 압축을 풀어 install→start→API→verify→stop을 다시 돌리지는 않았다(예산).
  - EVAL_1에서는 직전 public ZIP으로 같은 절차를 독립 실행해 통과했다.
  - 이번에는 트리 동일성과 로그 열람으로 확인했다. 앵커 문구 "재실행/열람"에 해당한다.

**공백(점수 영향 없음)**: registration API는 축약판이다. public과 코드가 같아 앵커에 영향을 주지 않는다.

### 4. manifest_checksum — **25/25** (앵커 25)
**근거**
- `PACKAGE_CONTENTS.md`(3개 ZIP 모두)
- `FILE_MANIFEST.jsonl`
- 19개 검사 표(로그)와 BUILD_INFO `checks.run`(15개) + `after_this_file`(4개)
- 구현: `scripts/dev/package_build.py:187-231`(CHECKS 정의), `:145-168`(licence 규칙)
- `.sha256` sidecar

**재실행과 결과**
- `sha256sum -c` ×3: exit 0.
- `zipaudit2.py` ×3, 전수 대조:
  - 경로·형식: absolute, `..`, backslash, 제어문자, 빈 component, symlink, 특수 파일, 중복, 대소문자·Unicode 충돌이 모두 **0**이다. testzip(CRC) OK, entry 정렬, 시각 1980-01-01, 모드 0644/0755다.
  - manifest: **행 수 = 멤버 − 1**(475, 485, 3245), 키 6개, 빈 값 0, 자기 자신 없음, 정렬, 중복 없음.
  - **모든 행의 sha256과 bytes가 멤버 바이트와 같다: 불일치 0.**
- `PACKAGE_CONTENTS.md`: 표의 행이 474/484/3244로, ZIP 멤버에서 자기 자신과 manifest를 뺀 집합과 정확히 같다. 각 행의 bytes, sha256 앞 16자리, role, licence가 manifest와 같다. build id가 BUILD_INFO와 같다(`r-6ada31a4…`, `r-63bd409d…`, `r-99eeb029…`).
- `LICENSE`는 3개 모두에 있다(DD-A65의 '권리자 미정' 자리표시). `THIRD_PARTY_NOTICES_WEB.md`도 3개 모두에 있다. `LICENSE_STATUS.md`는 owner에만 있다.
- **wheel gate 파일**: `build/package_reports/{public,registration,owner}_wheels.txt`와 owner 동봉본 `docs/internal/evidence/package_build/owner_wheels.txt`가 있다(동봉본은 외부본과 바이트 동일).
  - 각 wheel의 sha256, bytes, 멤버 목록(dist-info 제외, 멤버별 sha256과 bytes)을 ZIP 안 `wheels/`와 대조했다: **4/4, 4/4, 5/5 일치**, gate 줄은 모두 PASS다.
- `pytest tests/public/packaging/test_package_contents_and_gates.py tests/public/ops/test_ops_role_env.py`: **23 passed**, exit 0. 실행 전후 dist sha 불변.

## EVAL_1 결함 판정
| ID | EVAL_1 내용 | 판정 | 증거 |
|---|---|---|---|
| D1 (Major) | owner ZIP이 트리보다 오래됨 | **FIXED** | owner BUILD_INFO source_tree가 `1b4bd1a4…`로 현재 트리와 같다. `drift.py`: tree 행 3228/3236이 동일하고, 변경 8개와 이후 신규 44개는 **모두 docs/internal/**이다(밖은 0). T6 owner는 이 ZIP으로 다시 실행됐다(위 3). 내 독립 재빌드와 비교하면 docs/internal과 메타데이터 밖의 차이가 0이다 |
| D2 (Minor) | PACKAGE_CONTENTS.md 없음 | **FIXED** | 3개 ZIP 모두 있고 manifest와 정합한다. packager 검사 `package_contents`(재렌더 대조) PASS. 테스트 `test_package_contents_md_in_every_profile_matches_the_manifest`(test_package_contents_and_gates.py:50) 통과 |
| D3 (Minor) | licence 열이 없는 LICENSE_STATUS.md를 가리킴 | **FIXED** | 3개 ZIP 모두 LICENSE_STATUS를 인용하는 행이 0이다. 규칙은 `package_build.py:145-168`(LICENSE / THIRD_PARTY_NOTICES*.md만). 테스트 `test_manifest_licence_column_names_only_files_of_the_same_bundle`(:91) 통과 |
| D4 (Minor) | 검사 출력이 집계뿐 | **FIXED** | 빌드 로그에 19행 표가 있고, BUILD_INFO `checks.run`이 검사별 상태와 건수를 담는다. 테스트 `test_report_and_output_name_every_check_with_its_status`(:144), `test_a_failing_check_is_named_fail_and_later_checks_not_run`(:165) 통과 |
| D5 (Minor) | FILE_LIST placeholder 유무에 따라 registration digest가 달라짐 | **FIXED** | 내 재빌드에서 placeholder가 있든 없든 `dea510df…`. docstring `package_build.py:60-61`. 테스트 `test_registration_build_does_not_depend_on_the_file_list_placeholder`(:246) 통과 |
| D6 (Minor) | start.sh OLDPWD | **FIXED** | `scripts/start.sh:344`와 `:356`, `:360`(subshell에서 다시 unset). 테스트 `tests/public/ops/test_ops_role_env.py`(:26, :94) 통과. T8 /proc 스캔 0건은 보조 근거다(sandbox 경로) |
| D7 (Minor) | web 테스트 role이 `other` | **FIXED** | `mbnas_web/tests/**`와 `e2e/**` 68/68/72행이 role `test`이고, `other` role은 0행이다. 테스트 `test_web_test_and_e2e_rows_have_role_test`(:129) 통과 |

## 결함 표 (남은 것)
| ID | 심각도 | 소유 WP / 파일 | 내용 | 수정안 |
|---|---|---|---|---|
| D8 | Minor(운영) | WP-7D / `scripts/dev/canary_scan.py:209-213, 228-238`; `build/allowlists/owner.txt`(docs/internal/**) | (a) `_line_of`가 매치마다 `data.count(b"\n", 0, pos)`를 호출하고, 그 호출이 per-needle 상한 판정보다 먼저 일어나 시간이 이차로 는다. 실측 1/2/4 MB에서 1.07/3.6/12.7 s다. (b) owner allowlist가 T8 owner ZIP 스캔 덤프를 싣고, 덤프는 스캔할 때마다 커진다(2.8 → 9.9 → 30 MB + 19 MB). 외삽하면 다음 DD-A70 owner 재빌드는 덤프 2개만으로 패스당 약 12분(30 MB)과 약 7분(19 MB)이 걸릴 것으로 추정한다(덤프는 deflate 후 1.3 MB, 압축 해제 시 49 MB). 내 재빌드는 8분 49초 뒤에도 소스 검사 단계에 머물러 중단했다 | 줄 번호는 기록되는 hit(상한 이내)에서만 계산하거나 이전 위치부터 누적해 센다. owner allowlist에서 원시 hit 덤프(`**/t8/zip_leak_scan_owner.*`)를 deny하고 요약만 남긴다. DD-A70 재빌드 전에 처리한다 |

## 범위 밖 관찰 (채점 제외)
- **O1 (push하면 Critical, 사용자 조치 대기).** TARGET_ROOT git의 로컬 커밋이 이제 2개다(`3e5bbecb9`, 새로 생긴 `8722e3062 update` 11:58).
  - HEAD는 여전히 `runtime/` 146 689개(env 5개 포함), `build/canary/**`·`dist/owner` 등 5 638개, `docs/internal`을 추적한다.
  - 로컬 `origin/main`에는 README 1개만 있고 브랜치는 2 커밋 앞서 있다. 원격은 fetch하지 않았다.
  - DD-A67의 `.gitignore`와 `GIT_HYGIENE_URGENT.md`는 작성돼 있다. 그러나 `.gitignore`는 이미 추적 중인 파일을 해제하지 않는다. 사용자에게 다시 알려야 한다.
- **O2 (위생, 변동 없음).** public 루트 `pyproject.toml:6,15`에 owner testpaths와 `mbnas_core.owner_worker` 산문이 있다. 정책상 허용(info)이다.
- **잔여 공정.** DD-A70 docs-only 최종 owner 재빌드가 남아 있다. 그 FILE_MANIFEST를 53e6a1f0과 행 단위로 대조하면, 차이는 docs/internal/**와 메타데이터(BUILD_INFO, PACKAGE_PROFILE, PACKAGE_CONTENTS)뿐이어야 한다. 이 평가의 `manifest_diff.py`를 그대로 쓸 수 있다.

## 합계와 판정
| 기준 | 점수 | 앵커 |
|---|---|---|
| naming_allowlist | 25 | 25 |
| public_private | 25 | 25 |
| final_unpack_run | 25 | 25 |
| manifest_checksum | 25 | 25 |
| **합계** | **100** | |

- Blocker 0, Critical 0, Major 0, Minor 1(D8).
- 판정: **PASS**.

## 재실행 목록 (명령 → exit·수치)
1. `source_tree_hash.py`: `1b4bd1a4…`(시작 13:03)
2. `sha256sum -c` ×3: 0, 0, 0
3. `zipaudit2.py` ×3(외부 wheel 목록 포함): 위 기준 1과 4의 값, 불일치 0
4. `drift.py` ×3:
   - public 468/468, registration 477/477 동일
   - owner 3228 동일. 변경 8개와 신규 44개는 전부 docs/internal
5. `tree_vs_zip.py`: staging ×3 동일, T6 rerun2 트리 ×3 동일
6. `canary_scan.py --manifest … --profile`: public 0(624/0), registration 0(634/0), owner 0(3553, failing 0)
7. `canary_posctl.py`: 9/9는 1, 중첩 1, host fact 1, clean 0
8. `core_refs.py` ×2: 참조 0
9. 음성(새 public 트리): install owner 1, install owner + public requirements 1, start owner core 1
10. `package_build.py --root <scratch> --reproducible`: public 0(`bc0500bd…`), registration 0과 0(`dea510df…` ×2), owner 143(D8로 중단) → 덤프 2개를 뺀 사본으로 0(`a0147d7e…`, 2회 IDENTICAL)
11. `manifest_diff.py`(owner dist 대 재빌드): 같음 3234, 추가 42, 삭제 0, 변경 11. 예상 밖 0, 비문서 바이트 차이 0/767
12. `pytest tests/public/packaging/test_package_contents_and_gates.py tests/public/ops/test_ops_role_env.py`: 0(23 passed), dist 불변
13. 스캐너 성능(`canary_scan.py --profile owner`, 덤프 앞부분 1/2/4 MB): 1.07/3.60/12.67 s
14. 끝 tree hash(13:22): `1b4bd1a4…`로 시작과 같다. 그 직후 `sha256sum -c` ×3 OK이고, 잔존 평가 프로세스는 0이다

## 확인 못 함
- 이번 라운드에서 public, registration, owner 번들을 새로 풀어 install→start→API→verify→stop을 직접 실행하지 않았다. 트리 동일성과 로그 열람으로 확인했다.
- offline(`unshare -rn`) 설치, T4/T5, T8 runtime 캡처는 열람만 했다.
- `requirements-cpu.txt` 오프라인 설치 불가(DD-A72(3))는 C11 범위라 보지 않았다.
- owner의 docs/internal 밖 host fact 38건이 실제로 무해한지(DD-A72 수용 근거)는 목록 일치만 확인했다.
- 지침 §11 원문은 TARGET_ROOT에 없다.
- 사용자 GitHub 원격 상태는 fetch하지 않았다.

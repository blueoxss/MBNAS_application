# C10 ZIP 배포 — 독립 평가 EVAL_1

- set: **C10** (`docs/internal/criteria/C10.json` v1.0). 앵커는 25/20/15/0을 그대로 적용했다.
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자. 채점만 하고 결정은 설계자가 한다.
- time: 2026-10-28T08:09+09:00 시작, 08:3x+09:00 보고 작성.
- tree hash (`runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`, mbnas-source-tree/2, 221 files):
  `sha256:e220e72137165c57b08cd24388c11d64762b689b376a17911a774b8a3bb45236`
  - 시작(08:09)과 끝(08:30)의 값이 같다. 평가 중 패키지 소스·scripts·allowlist 변경은 없었다.
  - 지시된 `| tail -3`은 JSON 닫는 괄호만 출력하므로 `sha256` 필드를 인용했다.
- 평가 대상 ZIP: public `ce34b821…`, registration `95b541e9…`, owner `79cce02a…`.
  - `sha256sum -c`는 세 개 모두 시작과 끝에 OK(exit 0)였다.
- 환경
  - CPU 전용(`CUDA_VISIBLE_DEVICES=""`, `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, `--basetemp`는 scratchpad).
  - GPU, etri_nas, build/source_snapshot, /data에는 접근하지 않았다.
  - 프로젝트에 쓴 파일은 이 파일 하나다. 평가자 산출물은 `$SCRATCH/wp3/c10eval/`의 `scripts/`, `out/`, `rerun/`, `rebuild/`에 있다. `$SCRATCH`는 이 세션의 scratchpad이고, 경로에 계정명이 들어 있어 풀어 쓰지 않는다.
  - 내가 띄운 서비스(포트 19870–19872)는 모두 내렸다. 남은 프로세스는 0이다.
  - 재실행에 쓴 CPU는 약 9분(추정)이다.

## 결론
총점 **90/100**(25+25+20+20), Blocker 0, Critical 0. 통과 조건 "총점 > 90"을 넘지 못해 **FAIL**이다(90은 90 초과가 아니다).

감점은 두 가지다. 둘 다 고칠 방법이 명확하다.
1. **final_unpack_run 20점.** owner ZIP `79cce02a`는 현재 트리보다 오래됐다.
   - 07:06 빌드 뒤 08:06에 DD-A64(fix 3) 변경이 들어왔다. `mbnas_core/src/mbnas_core/runner_api/routes.py`가 바뀌었고 테스트 1개와 internal 문서 6개가 추가·변경됐다.
   - 그래서 owner의 T6(unit 2657/0, integration 17/17)는 대체될 빌드에서 나온 증거다.
   - owner를 재빌드하고 T6 owner를 다시 돌리면 25점이 된다.
2. **manifest_checksum 20점.** C10.json:52의 필수 점검 `PACKAGE_CONTENTS.md`가 프로젝트 어디에도 없다.
   - 실질 요건(manifest 필드, 외부 .sha256, 경로 검사)은 모두 충족하고 재실행으로 확인했다. 앵커의 "문서 1건 누락"에 해당한다.
   - public/registration manifest의 licence 열이 그 배포본에 없는 `LICENSE_STATUS.md`를 가리키는 라벨 문제도 있다.
   - 문서를 생성하거나, DD로 대체를 승인하고 기준 라벨을 고치면 25점이 된다.

가장 강한 확인은 세 가지다.
- scratch 복사본(공백 포함 경로)에서 **독립 재빌드**한 public과 registration ZIP이 공개본과 **바이트 단위로 같다**(`ce34b821…`, `95b541e9…`).
- T6에 쓰인 세 트리가 최종 ZIP과 바이트 단위로 같다.
- 내가 새로 압축을 풀어 실행한 public 번들이 install→doctor→start→API→verify→stop을 모두 exit 0으로 마쳤다. unit 결과도 717/0/222로 같다.

## 기준별 채점

### 1. naming_allowlist — **25/25** (앵커 25)
**근거**
- dist/{public,registration,owner}/ 각각에 ZIP 하나와 `.sha256` 하나만 있다(`7V_FINAL/31_dist_listing.txt`, `ls` 재확인).
- 이름은 WP-7 계약 `WORK_PACKAGES/WP-7_OPS_PACKAGING.md:13`의 규칙을 따른다: `mbnas_application-<version>-<profile>-runtime-<platform>.zip`, owner는 `-owner-private-`.
- package.sh 로그 `7V_FINAL/11_package_public_final.log`, `13_package_registration_final.log`, `13_package_owner_final.log`는 모두 exit 0이고, 0 violation, 0 warning, "two builds IDENTICAL"이다.
- allowlist는 `build/allowlists/{public,registration,owner}.txt`다. 현재 sha256(`1e7c7c5d…`, `4dde6f21…`, `8a88b5d7…`)이 각 ZIP의 BUILD_INFO `allowlist.sha256`과 같다.
- packager(`129be019…`)와 scanner(`5de3402c…`)의 sha도 BUILD_INFO에 기록된 값과 같다.

**재실행과 결과**
- 이름 정규식(`zipaudit.py`): 3/3 일치. version 0.1.0은 VERSION 파일, BUILD_INFO, wheel 이름 5개와 일치하고, platform은 `linux_x86_64`다.
- 최상위 폴더: 세 ZIP의 모든 entry가 `mbnas_application/` 아래에 있다(466/476/2115).
- staging 대 ZIP(`tree_vs_zip.py`, `build/staging/<p>/mbnas_application`): 3/3 동일하고, 추가 파일은 0이다.
- allowlist 재선택(`drift.py`, 프로젝트 Allowlist 로더 사용):
  - public ZIP의 tree 행 459개는 현재 allowlist 선택 463개에서 deny-importers 폐포 4개(fitness_oracle, DD-A43 a)를 뺀 것과 정확히 같다.
  - registration은 468개 = 473 − 4 − 생성 파일 FILE_LIST.md 1개다.
  - 세 ZIP 모두 allowlist 밖의 tree 행은 0이다.
- **독립 재빌드**: `runtime/venv/bin/python -I -B scripts/dev/package_build.py --root "$SCRATCH/…/rebuild/public root" --profile public --reproducible`
  - 입력은 allowlist가 선택하는 파일, allowlist, canary manifest, offline 기록의 복사본이다.
  - 결과: exit 0, 2회 IDENTICAL, 0 violation, **sha256 `ce34b821…`(공개본과 동일, 2 638 943 B)**.
  - registration도 같은 방식으로 **`95b541e9…` 동일**하다. D5의 조건이 붙는다.

**공백**: 점수에 영향을 주는 공백은 없다. owner ZIP이 오래된 문제는 기준 3에서 채점했다.

### 2. public_private — **25/25** (앵커 25)
**근거**
- `7V_FINAL/30_canary_scan_{public,registration,owner}.txt/.json`
- `7V_FINAL/T8/README.md`(runtime 판정 PASS, 정규화 규칙 명시)
- `7V_FINAL/T6/{public,reg}_no_mbnas_core_loader.txt`(exit 0)
- `build/allowlists/public.txt:88-144`(deny와 deny-importers)
- DD-A41(b, c), DD-A43(a, c), DD-A59(a)

**재실행과 결과**
- 멤버 grep(30개 패턴, public/registration): 모두 **0건**이다. `mbnas_core`, `tests/owner`, `deploy/owner`, `docs/internal`, `canary`/`CANARY`, `runtime/`, `config/(public|owner|registration)/`, `.env`, `LICENSE_STATUS`, `fitness_oracle`, `test_no_host_facts`, `scripts/dev`, `scripts/owner`, `tests/public/packaging`, `harness.py`, `qa_handlers.py`, `requirements-owner`, `config/examples/core*`, `*.map`, `.pyc`, `node_modules`, `.db`, `.pem`, `.key`, `.pt` 등이 대상이다.
  - owner ZIP도 `runtime/`, `config/<profile>/`, `.env`, `docs/internal/canary`, `SOURCE_SNAPSHOT_*`이 0건이다.
- `canary_scan.py --manifest docs/internal/canary/CANARY_MANIFEST.json --profile <p>`:
  - public: 614 files, **hits 0**, exit 0
  - registration: 624 files, **hits 0**, exit 0
  - owner: 2382 files, 0 failing, host fact 3439건은 정보성, exit 0
  - 세 결과 모두 evidence 값과 같다.
- **양성 대조**(`canary_posctl.py`, scratch ZIP, 토큰 값은 출력하지 않음):
  - canary 영역 9개(POLICY_PRIOR … MANIFEST_SELF)를 각각 심은 ZIP: 9/9 exit 1, FAIL.
  - 모든 토큰을 중첩 wheel에 숨긴 ZIP: exit 1, 18 hits.
  - host fact 문자열: exit 1.
  - 깨끗한 ZIP: exit 0.
  - 따라서 0건 결과는 스캐너가 탐지 능력을 가진 상태에서 나온 것이다.
- 내용 스캔(`core_refs.py`, 중첩 wheel 포함):
  - `import/from mbnas_core`, 동적 import, requirement와 entry point 참조가 모두 **0**이다.
  - 문자열 참조 2개는 "mbnas_core가 로드되지 않았다"를 단언하는 음성 테스트다(`tests/public/runner/test_child_spec_file.py:89`, `tests/public/server/test_gateway_skeleton.py:271`).
  - 그 밖에는 산문 언급만 있다(public 46파일, registration 52파일). DD-A41 f와 packager 정책상 info로 분류된다.
- **"env만 바꿔도 코어 미출현"**(새로 푼 public 트리에서):
  - (a) `install.sh --profile owner`: exit 1(requirements-owner.txt 부재).
  - (b) `install.sh --profile owner --requirements requirements-public.txt`: exit 1, `this is a public package (no mbnas-core)`(`scripts/install.sh:203-205` metadata guard). venv는 만들어지지 않았다.
  - (c) 설치 후 `start.sh --profile owner --role core`: exit 1, `mbnas-core-api is not installed`(`scripts/start.sh:332`).
  - (d) public venv에서 `find_loader`/`find_spec('mbnas_core')`가 None이다. 공개 앱을 import한 뒤에도 `sys.modules`에 mbnas_core가 없다. 기본 env와 owner 유사 env(MBNAS_HOME=owner runtime 등) 모두 exit 0이다.
  - (e) `runtime/venv/bin/mbnas-core-api`가 없다.

**공백(점수 영향 없음)**
- T8 runtime의 정규화 스캔 중 proc 스캔(OLDPWD, 각 2건)과 install log 스캔(20건)은 상태가 "FAIL"로 기록돼 있다(`T8/t8_final_scan_summary.json`).
  - 둘 다 고객 측 경로(호출 셸 cwd, 운영자가 넘긴 인자)이고 owner 데이터가 아니다. README가 근거를 들어 PASS로 판정했고, 나도 동의한다.
  - start.sh OLDPWD 문제는 D6로 남긴다.
- T8 runtime 캡처는 내가 다시 실행하지 않았다(열람만 했다).

### 3. final_unpack_run — **20/25** (앵커 20: 사소한 누락 외 충족)
**근거**
- `7V_FINAL/T6/transcript.txt`와 `summary_{configure,up,verify,down,reinvoke}.json`
- `7V_FINAL/40_install_{public,registration,owner}.txt`: 각각 exit 0
- `7V_FINAL/README.md:55-82`
- 프로필별 결과:
  - public: doctor --strict 23 PASS, start 0, API 전 흐름(OFF run COMPLETED, export 11 637 B), unit 717/0/222, relocation 8/8, package 465/465, stop 0
  - registration: doctor 23 PASS, API 축약(health, presets, `/`, upload, structure-check), unit 717/0, relocation 8/8, package 475/475
  - owner: doctor 28 PASS, unit 2657/0, integration 17/17, relocation 8/8
  - cwd `/`에서 재호출: PASS, 잔존 프로세스 0

**재실행과 결과**
- **T6 트리와 최종 ZIP의 동일성**(`tree_vs_zip.py`): `runtime/test_root/t6/final/mbnas {public,registration,owner} final/mbnas_application`의 manifest 행이 각각 465/465, 475/475, 2114/2114로 sha가 모두 같다.
  - 추가된 것은 `runtime/`과 install이 만든 `config/<profile>/*.env`뿐이다.
  - 따라서 T6 로그는 정확히 이 ZIP들에서 나온 것이다.
- **독립 재실행**(`rerun_t6.py`, 새 경로 `"$SCRATCH/…/rerun/{public,owner} zip tree"`, 외부 cwd):
  - public과 owner ZIP을 새로 풀었다.
  - `install.sh --offline --wheelhouse build/wheelhouse`: owner 0, public 0(93.5 s).
  - owner start 0, doctor --strict 0. public doctor --strict 0(시작 전), start 0, doctor --strict 0(23 PASS).
  - API:
    - health 200(own roots만 `{dataset_roots:[],output_roots:[],archive_roots:[]}`), 토큰 없으면 401
    - presets 200, `/` 200
    - upload와 structure-check는 PASS, 깨진 YAML은 FAIL `SHAPE_MISMATCH`
    - validate valid, run 200 → **COMPLETED**(후보 2), export 200(zip 11 738 B)
  - verify: relocation 8/8, package(manifest 465/465, scan 0), **unit 717 passed / 0 failed / 222 skipped / 44 deselected**, 모두 exit 0.
    - skip 222건의 사유는 215 "owner-only tool not in this bundle", 6 collection skip, 1 "mini run absent"다(junit 집계).
  - stop: public 0, owner 0. 잔존 프로세스 0.
  - 참고: 첫 validate는 core allowlist 밖의 output_root를 넣어 `PATH_OUTSIDE_ALLOWLIST`(valid=false)를 받았다. 이는 의도된 거부다. T6과 같이 core runs 아래 경로로 다시 했다.

**공백(감점 사유)**
- **owner ZIP이 최종 트리가 아니다**(D1, Major).
  - owner BUILD_INFO의 source_tree는 `5a7bc592…`이고 현재 트리는 `e220e721…`이다.
  - 07:06 빌드 이후 다음이 바뀌었다:
    - `mbnas_core/src/mbnas_core/runner_api/routes.py`(DD-A64 reclaim-before-lease, 08:06:54)
    - 신규 `tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py`
    - `docs/internal/{DECISIONS,FIX_ROUND_1,VALIDATION_REPORT}.md`, `REVIEW_LOG.jsonl`
    - ZIP 안의 자기 빌드 로그 `13_package_owner_final.log`(빌드 중 사본이라 잘려 있음)와 `7V_FINAL/21_unshare_network_proof.txt`
    - 이후 추가된 evidence 227개
  - REVIEW_LOG.jsonl:22("final owner rebuild after fix 3")와 DECISIONS.md:80(DD-A64)이 재빌드를 예고하고 있다. 그러나 지금 dist의 owner ZIP으로는 "최종 압축 해제본 실행"을 주장할 수 없다.
  - public과 registration은 영향이 없다. 두 ZIP의 tree 행은 현재 트리와 0건 차이이고, core를 포함하지 않는다.
- registration과 owner의 unit/integration은 내가 다시 돌리지 않았다(예산). 열람과 트리 동일성으로만 확인했다.

### 4. manifest_checksum — **20/25** (앵커 20: 문서 1건·라벨 누락 외 충족)
**근거**
- `FILE_MANIFEST.jsonl`(ZIP 내부)
- `7V_FINAL/SHA256SUMS_final.txt`, `31_dist_listing.txt`
- package.sh 로그의 "checks: 0 violation(s), 0 warning(s)"
- `build/package_reports/<p>.json`(status built, findings는 info만)
- 검사 구현: `scripts/dev/package_build.py:605-631`(check_paths: 절대경로, `.`/`..`, backslash, 제어문자, 대소문자·Unicode 충돌), `:374-406`(symlink/특수 파일), `:1056-1094`(verify_zip: 중복 entry, CRC, staging 대조, manifest 자기 등재·누락·불일치)

**재실행과 결과**
- `sha256sum -c` ×3: exit 0(시작과 끝).
- `zipaudit.py` ×3:
  - 경로·형식 검사: absolute, `..`, backslash, 제어문자, 빈 component, symlink, 특수 파일이 모두 **0**이다. 중복 entry 0, 대소문자·Unicode 충돌 0이다.
  - entry는 정렬돼 있고, 시각은 1980-01-01, 모드는 0644/0755로 고정이다.
  - manifest는 465/475/2114행이다. 키는 `{relpath, bytes, sha256, role, origin, licence}`이고 빈 값은 0이다. 자기 자신은 목록에 없고, 정렬돼 있으며 중복이 없다.
  - **모든 행의 sha256과 bytes를 멤버 바이트와 대조했다(표본 20개가 아니라 전수): 불일치 0.**
- 새로 푼 트리의 `verify.sh --suite package`: manifest 465/465 OK, exit 0.
- `pytest tests/public/packaging -p no:cacheprovider --basetemp $SCRATCH/…`: **42 passed**, exit 0. 실행 전후 dist sha는 같았다.
  - symlink와 case collision 실패 경로, 재현성, manifest 대조, private import·requirement, host fact, canary, 비밀 값 검사가 포함된다.
- registration `docs/registration/FILE_LIST.md`(ZIP 내부)는 manifest의 474개 경로를 모두 담고 있다.

**공백(감점 사유)**
- C10.json:52 필수 점검의 `PACKAGE_CONTENTS.md`가 프로젝트와 3개 ZIP 어디에도 없다(D2).
  - grep 결과 C10.json 자신만 나온다. 대체를 승인한 DD도 없다.
  - 실질적으로는 FILE_MANIFEST.jsonl, PACKAGE_PROFILE.json(`contents`, `exclusions_with_reason`), FILE_LIST.md가 그 역할을 한다. 그래서 0점이나 15점이 아니라 "문서 1건 누락"(20점)으로 봤다.
- public manifest의 461행과 registration manifest의 471행에서 licence 열이 "`…; LICENSE_STATUS.md`"를 가리킨다(D3). 그런데 이 파일은 DD-A41 c에 따라 두 배포본에서 제외된다.
  - THIRD_PARTY_NOTICES.md:3이 "공개 배포본에는 없음"이라고 밝히고는 있다. 그래도 공개 독자에게는 끊긴 참조다.
- package.sh 검사 출력은 집계만 보여 준다. 실행한 검사 목록과 검사별 0건이 기록되지 않는다(D4). 나는 독립 감사와 테스트로 보완했다.

## 결함 표
| ID | 심각도 | 소유 WP / 파일 | 내용 | 수정안 |
|---|---|---|---|---|
| D1 | **Major** | WP-7V(P-final) / `dist/owner/…-owner-private-…zip` | owner ZIP `79cce02a`가 현재 트리보다 오래됐다. DD-A64 core 변경(`runner_api/routes.py`), 신규 테스트, internal 문서 6개가 빠졌다. ZIP 안의 자기 빌드 로그는 잘려 있다. T6/T4 owner 증거는 이 구 빌드에서 나왔다 | `bash scripts/package.sh --profile owner --reproducible`로 재빌드한다. `sha256sum -c`와 canary scan(owner)을 다시 돌린다. 새 ZIP으로 T6 owner(install→doctor→start→verify unit,integration,relocation→stop)를 다시 돌린다. VALIDATION_REPORT, 7V_FINAL README, SHA256SUMS를 갱신한다. public/registration은 재빌드할 필요가 없다(core 미포함, tree 행 차이 0) |
| D2 | Minor | WP-7D, WP-7C / (없음) / `criteria/C10.json:52` | 필수 점검 문서 `PACKAGE_CONTENTS.md`가 없다 | 프로필별 `PACKAGE_CONTENTS.md`를 FILE_MANIFEST에서 자동 생성한다(영역별 개수·바이트, 역할, 제외 사유, manifest sha; FILE_LIST.md 생성기를 재사용). 또는 DD로 "FILE_MANIFEST.jsonl + PACKAGE_PROFILE.json + FILE_LIST.md = contents 문서"를 승인하고 C10.json 라벨을 버전업한다 |
| D3 | Minor | WP-7D / `scripts/dev/package_build.py:128-137`(licence 규칙) | public/registration manifest의 licence 값이 배포본에 없는 `LICENSE_STATUS.md`를 가리킨다 | `LICENSE_STATUS.md`가 deny인 프로필에서는 "(THIRD_PARTY_NOTICES.md; registration: docs/registration/RIGHTS_REVIEW.md)"로 바꿔 쓴다 |
| D4 | Minor | WP-7D / `package_build.py`(report와 BUILD_INFO `checks`) | 검사 출력이 집계("0 violation")뿐이다. 절대경로, `..`, symlink, 중복, 대소문자 검사가 실행됐다는 기록이 산출물에 없다 | report와 BUILD_INFO에 `checks_run: {path_safety: 0, symlink: 0, case_collision: 0, zip_duplicate_entry: 0, manifest_*: 0, private_*: 0, content: 0 …}`을 기록한다 |
| D5 | Minor | WP-7D / `docs/registration/FILE_LIST.md`(소스 placeholder) | 출하되지 않는 placeholder가 있으면 `generated_shadow` info가 생기고, 이것이 BUILD_INFO 집계, FILE_LIST.md, manifest를 거쳐 ZIP digest를 바꾼다. 내 재빌드에서 placeholder가 없으면 `da05506e…`, 있으면 `95b541e9…`였다(payload는 동일). 저장소 이관 때 재현성이 깨질 수 있다 | info 집계에서 생성 경로의 shadow를 제외한다. 또는 placeholder를 삭제하고 그 사실을 문서화한다. 이관 지침(NEXT_TASK)에 빌드 입력으로 명시한다 |
| D6 | Minor | WP-7A / `scripts/start.sh` | 서비스 env에 `OLDPWD`(호출자 cwd)가 남는다. T8 정규화 proc 스캔에서 public과 registration이 각 2건이다(기지 결함, REVIEW_LOG:16) | exec 전에 `unset OLDPWD`(1.5.1) |
| D7 | Minor(외관) | WP-7D / `package_build.py:139-149` ROLE_RULES | `mbnas_web/tests/**`, `e2e/**`, playwright 설정 등 78행의 role이 `other`다 | `mbnas_web/tests/**`와 `mbnas_web/e2e/**`는 `test`, 설정 파일은 `web-config`로 규칙을 추가한다 |

## 범위 밖 관찰 (C10 채점 제외, 설계자 전달 필요)
- **O1 (push하면 Critical).** TARGET_ROOT 자체 git 저장소(origin `github.com/blueoxss/MBNAS_application`)의 로컬 커밋 `3e5bbecb9 update`(사용자 커밋)에 다음이 들어 있다:
  - `runtime/` 146 689개 파일. `runtime/test_root/**/config/{owner,public}/*.env` 시험 비밀이 포함된다.
  - `build/canary/**`(5 660개). canary를 심은 owner 트리 사본, `mbnas_core` 소스, `CANARY_MANIFEST.json`이 포함된다.
  - `dist/owner/…-owner-private-…zip`
  - `docs/internal/**`

  읽기 전용 확인(`git ls-tree -r HEAD`, `git status -sb`) 결과는 이렇다. 로컬 `origin/main`(`c9a29408`)에는 README.md 1개만 있고, 브랜치는 1 커밋 앞서 있다. 즉 로컬 기준으로는 아직 push되지 않았다. 원격의 실제 상태는 fetch하지 않아 확인하지 못했다.
  - 이 커밋을 push하면 private core, canary manifest, 시험 비밀이 공개된다.
  - 권고: 사용자에게 push 금지를 알린다. `.gitignore`에 runtime/, build/, dist/, docs/internal/canary/를 넣고 커밋을 다시 만들도록 안내한다. 이는 사용자 행위 영역이며 우리는 git을 조작하지 않는다.
- O2 (위생). public ZIP의 루트 `pyproject.toml`에 owner 경로 `testpaths = ["tests/owner", …]`와 marker 설명 `mbnas_core.owner_worker`가 있다(`pyproject.toml:6`, `:15`). 산문 언급이라 DD-A41 f와 packager 정책상 허용(info)이다. 공개 변형을 따로 만들지는 선택이다.

## 합계와 판정
| 기준 | 점수 | 앵커 |
|---|---|---|
| naming_allowlist | 25 | 25 |
| public_private | 25 | 25 |
| final_unpack_run | 20 | 20(D1) |
| manifest_checksum | 20 | 20(D2, D3) |
| **합계** | **90** | |

- Blocker 0, Critical 0. Major 1(D1), Minor 6(D2–D7).
- 판정: **FAIL**. "총점 > 90" 조건을 넘지 못했다.
- D1(owner 재빌드와 T6 owner 재실행)과 D2(PACKAGE_CONTENTS 문서 또는 DD 승인과 라벨 수정)를 처리하면 100점까지 갈 수 있다.
  - 둘 중 하나만 처리해도 95점으로 통과한다.
- 설계자가 C10 평가 대상을 "07:06 빌드 시점의 ZIP"으로 한정하면 D1이 감점 사유에서 빠져 95점이 된다. 다만 이 경우에도 최종 owner 재빌드 후 C12에서 다시 검증해야 한다.

## 재실행 목록 (명령 → exit code·수치)
1. `sha256sum -c` ×3(dist/<p>): 0, 0, 0(08:09와 08:30 두 번)
2. `zipinfo -1`, `unzip -l` ×3: 0. 멤버 grep 30개 패턴은 public/registration 모두 0건
3. `python3 -I zipaudit.py` ×3: 위 기준 4의 값. manifest 전수 대조 불일치 0/0/0
4. `runtime/venv/bin/python -I -B drift.py` ×3:
   - public 459/459 동일, registration 468/468 동일
   - owner는 2100/2107 동일, 7개 변경(routes.py 포함). 신규 선택 대상 227개
5. `source_tree_hash.py --package <public 4개> --digest-only`: `83af6d5f…`로 public/registration BUILD_INFO와 일치. owner BUILD_INFO `5a7bc592…`는 현재 `e220e721…`과 불일치
6. `canary_scan.py --manifest … --profile public|registration|owner`: 0(614 files/0 hits), 0(624/0), 0(2382, failing 0)
7. `canary_posctl.py`(양성·음성 대조): 영역 9/9는 1, 중첩 wheel 1, host fact 1, clean 0
8. `core_refs.py` ×2: import·동적·requirement 참조 0
9. `tree_vs_zip.py`: T6 트리 ×3 동일(465, 475, 2114), staging ×3 동일
10. `rerun_t6.py`: extract → 음성 install 1, 1 → install 0, 0 → owner start 0, doctor 0 → public doctor 0, start 0, doctor 0 → API 흐름(위 기준 3) → loader 검사 0, 0 → `start.sh --profile owner --role core` 1 → verify relocation 0, package 0, unit 0(717/0/222/44) → stop 0, 0, 잔존 0
11. `package_build.py --root <scratch 복사본> --reproducible`:
    - public: 0, `ce34b821…` 동일
    - registration: 0. placeholder가 없으면 `da05506e…`, 있으면 `95b541e9…` 동일
12. `pytest tests/public/packaging -p no:cacheprovider --basetemp $SCRATCH/…`: 0(42 passed), dist 불변

## 확인 못 함
- 지침(directive) §11 원문은 TARGET_ROOT에 없다. 명명 규칙은 WP-7 계약(`WP-7_OPS_PACKAGING.md:13`), 공개 문서(`docs/public/INSTALL.md:213`, `docs/registration/SUBMISSION_SCOPE_TABLE.md:13`), 평가 의뢰문으로 대조했다.
- `PACKAGE_CONTENTS.md`가 지침상 ZIP 멤버로 요구되는지, 아니면 기준 작성 때의 라벨인지 확인하지 못했다.
- registration과 owner의 unit/integration 스위트, offline 설치(`unshare -rn`), T8 runtime 캡처(responses, logs, /proc)는 직접 다시 실행하지 않았다. evidence 열람과 트리 동일성 검증만 했다.
- 재빌드된 owner ZIP의 T6는 아직 산출물이 없어 확인할 수 없다.
- 사용자 GitHub 원격 저장소의 실제 상태(O1)는 fetch나 네트워크 조회를 하지 않았다.
- owner `--canary` 빌드 양성 대조(심은 영역이 모두 발견되는지)는 최종 패스에서 다시 실행된 기록이 없다(7D, 10-27). 나는 스캐너 단독 양성 대조로 탐지 능력만 확인했다.

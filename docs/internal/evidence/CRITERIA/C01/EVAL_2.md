# C01 사실·기준선 — 독립 재평가 EVAL_2

- set: **C01** (`docs/internal/criteria/C01.json` v1.0). anchor는 EVAL_1과 같다(25/20/15/0).
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자. 채점만 하고 결정은 설계자가 한다.
- time: 2026-10-28T07:43:26+09:00 시작, 07:52+09:00 보고 작성
- tree hash (mbnas-source-tree/2, 221 files): `sha256:5a7bc5920ac3c1fe630a45c8ea0dd4661cb754661a90da5c09441fb7237c4223`
  - 시작과 종료의 `--manifest`를 `--diff`로 비교한 결과 added 0, removed 0, changed 0, unchanged 221이다. 평가 중 트리 변화는 없었다.
  - 지시된 `| tail -3`은 닫는 괄호만 출력하므로 `sha256` 필드를 인용했다.
- 환경과 경계
  - CPU 전용으로 실행했다(`CUDA_VISIBLE_DEVICES=""`, `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`). `MBNAS_TEST_ROOT`와 `TMPDIR`는 scratchpad를 썼다.
  - **/data에는 접근하지 않았다.** D7은 scratchpad에 만든 합성 캠페인으로 검증했다.
  - etri_nas는 `snapshot_compare.py`(읽기 전용 git)와 `find`/`stat`로 읽기만 했다. `.git/index`와 `HEAD`의 mtime·크기는 실행 전후 같다.
  - 연구 환경 인터프리터는 실행하지 않았다. 메타데이터 파일만 읽었다.
  - 프로젝트에 쓴 파일은 이 파일 하나다. 평가자 산출물은 `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/c01e2/`에 있다.

## 결론

총점은 **100/100**(25+25+25+25)이다. Blocker 0, Critical 0이고 90을 넘으므로 **PASS**다.

EVAL_1 결함 D1~D9는 **9건 모두 해결**됐다. 각 항목을 현재 트리에서 재실행하거나 독립 방법으로 다시 확인했다(아래 표).
EVAL_1 이후 새로 C01 결함으로 잡을 것은 없다. 범위 밖 관찰 2건은 맨 아래에 따로 적었다.

## 재실행 요약 (cwd=TARGET_ROOT, `runtime/venv/bin/python -B`)

| # | 명령 | exit | 결과 |
|---|---|---|---|
| 1 | `scripts/dev/check_import_closure.py` | 0 | package_modules 215, host_files 101, violations 0, unresolved 0, forbidden 0, errors 0, excepted 1 (`adapters/base.py:32`, expires **1.6**) |
| 2 | `check_import_closure.py --include-web` | 0 | forbidden 0 (EVAL_1에서는 1) |
| 3 | `scripts/dev/placement_coverage.py` (json/text) | 0/0 | PLACEMENT 636행. package files 221(py 215, data 6), **covered 221/221**. missing 0, deleted-but-present 0, unrecorded deletions 0, row errors 0. recorded deletion 1, sha drift 48(정보용) |
| 4 | 평가자 독립 커버리지(EVAL_1 방식) | 0 | 221/221, missing 0. EVAL_1의 D1 9건은 `PLACEMENT_MANIFEST.jsonl:589-597`의 `kind:new` 행에 있다(.py 8개는 현재 sha와 일치). 1.5.0 draft는 `:598` `deleted`(FIX1, DD-A61(c))이고 파일도 없다 |
| 5 | `python -I -B scripts/dev/snapshot_compare.py --source-root …/etri_nas --mtime-scan --json-out <scratch>` | 0 | 아래 "원본 무변경 수치" |
| 6 | `find etri_nas -path .git -prune -o -type f -newermt '2026-10-26 19:41'` (`__pycache__`·`.venv`·`node_modules` 포함) | 0 | **0 files** |
| 7 | `pytest tests/owner/search/test_block_catalog.py tests/owner/api/test_block_catalog_route.py` | 0 | **26 passed** |
| 8 | `pytest tests/public/server/test_campaign_adapter.py` | 0 | **19 passed, 1 skipped** (`:568` host data) |
| 9 | WPS8·DD-39 계약 부분집합(EVAL_1 #7과 같은 `-k`) | 0 | **19 passed**, 245 deselected |
| 10 | `pytest tests/public/infra/test_placement_coverage.py tests/public/infra/test_import_closure.py` | 0 | **17 passed** (placement 7, 음성 대조 포함 + closure 10) |
| 11 | D7: `gen_synth_campaign.py <scratch>`로 합성 12-arm 캠페인을 만든 뒤 `MBNAS_TEST_HOST_DATA=1 MBNAS_TEST_ARCHIVE_RUNS_ROOT=<scratch> pytest … -k real_campaign` | 0/0 | 1 passed. `REAL_CAMPAIGN {"arms": 12, "list_cold_ms": 128.1, "budget_ms": 3523, "load1": 21.58, "cpus": 48, "opened_files": 179}`. 429는 0건 |
| 12 | venv: import·버전, `pip freeze --all`, `pip check` | 0/0/0 | py 3.10.22, torch 2.6.0+cu124 (CUDA 12.4), torchvision 0.21.0+cu124, ultralytics 8.4.173, rfdetr 1.11.2. freeze 137/137 = constraints, 미핀 0, 누락 0, editable 5. pip check clean. `pyvenv.cfg` system-site false |
| 13 | D3 독립 교차검증: dist-info/egg-info 메타데이터로 freeze 재구성(평가자 도구, 연구 인터프리터 미실행). site.py 순서는 user, venv, base | 0 | sg26 **275/275** 이름 일치(핀 165 버전 동일, direct-url 110), sg **271/271**(162+109), mbnas310 **4/4**, 원본 .venv **141/141**. 차이 0 |
| 14 | 연구 환경 mtime 대리 검사(항목과 디렉터리 자체) | 0 | sg26·sg·mbnas310·원본 .venv·user site(py3.9)에서 10-23 20:00 이후 항목 0. 디렉터리 mtime: sg26 09-29, sg 07-13, user 2024-05-03, mbnas310 10-15, 원본 .venv 10-21. 프로젝트 시작 뒤 설치·삭제 0 |
| 15 | D2 사본 대조: `PROVENANCE/historical/*` 7개 sha256과 scratchpad 원본 | 0 | **7/7** 일치(`snapshot_copy.py`, `snapshot_delta.py`, `delta.py`, `apply_delta.py`, `delta.json`, `delta2.json`, `block_catalog.before.py`) |
| 16 | MODULE_MAP AST 재계산(EVAL_1 도구, 현재 트리) | 0 | Part A(스냅샷): third_party 148/148, lazy 148/148, mapping 143/148(설명된 5). Part B(설치본): 129/138 일치, 9개 차이 |

Part B의 차이 9개는 EVAL_1의 8개에 `mbnas_runner.__main__ → mbnas_contracts` 1개가 더해진 것이다. 모두 DECLARED 의존 안에 있다.

### 원본 무변경 수치 (#5, counts only)

- **스냅샷 사본**: 603/603 일치. differ, missing, extra, pyc, `__pycache__` 모두 0.
- **delta 사슬**: 파일 2개, 57행. 마지막 행이 manifest와 같은 것 51/51, supersedes 6/6, summary 목록 불일치 0.
- **summary**: 최상위 counts가 manifest와 같다(True).
- **라이브 etri_nas**
  - HEAD 18c48151 = 스냅샷 HEAD이고 이후 커밋은 0이다.
  - sha256은 603/603 일치하고 differ 0, missing 0이다. git 필드도 603/603 일치한다(416/93/94, HEAD tree 509).
  - 선택 파일 620개 중 새 코드 0, 새 문서 17(병행 세션의 owner 문서, 이식 대상 아님)이다. 마지막 delta 이후 수정 0이다.
- **mtime scan**: 116,134 files 중 첫 복사 뒤 수정 551(전부 application/), 마지막 delta 뒤 수정 0. 가장 늦은 mtime은 10-26 19:30:52다.
- **실행 중 상태**: `.git/index`와 HEAD는 실행 중 변하지 않았다(True). 프로젝트 쪽 로그 `PROVENANCE/live_compare_20261028T035809.txt`(03:58)와 수치가 같다.

## EVAL_1 결함 상태

| ID | EVAL_1 심각도 | 상태 | 근거(현재 트리) |
|---|---|---|---|
| D1 | Major | **해결** | `PLACEMENT_MANIFEST.jsonl:589-597` backfill 9행과 `:598` draft 삭제 행. `scripts/dev/placement_coverage.py`를 재실행해 221/221, exit 0(#3)이고 독립 검산도 221/221(#4)이다. 게이트 테스트 `tests/public/infra/test_placement_coverage.py`는 7 passed다. 이 게이트가 fix 중에 `owner_worker/paths.py` 누락을 실제로 잡았다(`PROVENANCE/INDEX.txt`, PLACEMENT `:613`). 최종 빌드 증거는 `WP7/7V_FINAL/placement_coverage.txt`(`README.md:17`) |
| D2 | Major | **해결** | 도구 `scripts/dev/snapshot_copy.py`, `snapshot_delta.py`, `snapshot_compare.py`(read-only git 허용목록 `snapshot_copy.py:95-108`). 비교 로그 `PROVENANCE/live_compare_20261028T035809.{txt,json}`(명령, exit=0, 수치). 원본 사본은 `PROVENANCE/historical/` 7개(#15에서 7/7 일치)와 `ORIGIN.txt`. self-test는 `selftest_snapshot_tools.txt`. REVIEW_LOG `:14`가 이 증거를 참조한다. #5 재실행 수치가 로그와 같다 |
| D3 | Major | **해결** | `ENV/env_baseline_run_20261028T041035.txt`(명령, exit=0), `env_baseline_*.json`, `freeze_{sg26,sg,mbnas310}.txt`, `freeze_original_venv.metadata.txt`, `freeze_project_runtime_venv.txt`. 평가자 독립 재구성 #13은 차이 0, mtime 검사 #14는 변경 0이다. freeze_sg26 sha256 `fb130030ab35effe…`는 로그와 같다 |
| D4 | Minor | **해결** | `SOURCE_SNAPSHOT_SUMMARY.json:16-18`이 603, 10,373,926 bytes, 416/93/94다. `:57` `counts_basis`, `:58-59` `initial_copy`(597). manifest 재계산과 role group까지 같다. `TASK_STATE.md:8-9`는 603(597+delta, 재복사 45, 신규 6)이다. `snapshot_compare` summary 검사가 True다 |
| D5 | Minor | **해결** | `PLACEMENT_MANIFEST.jsonl:447` source_sha256 = `77e2f1a5…`. 사슬 157→266(target `abcee67f…` = `historical/block_catalog.before.py` sha, 직접 확인)→447이고 근거는 `PROVENANCE/D5_block_catalog_source.txt`다 |
| D6 | Minor | **해결** | `scripts/dev/check_import_closure.py:81-84`가 `"expires": "1.6"`이고 basis는 DD-A26(2), DD-A58로 1.6까지 연장(`DECISIONS.md:71`)됐다. closure 출력의 excepted expires가 1.6이다(#1) |
| D7 | Minor | **해결** (합성 데이터 기준) | fixture가 설정 override를 받고(`test_campaign_adapter.py:161`) smoke는 `rate_limit_enabled=False`(`:577`)를 쓴다. 부하 비례 cold-list 예산은 `:35-52`(유휴 시 1500 ms 그대로)다. 합성 12-arm smoke는 1 passed, 429 0(#11), 기본 게이트는 19 passed/1 skipped(#8)다. 실제 /data 캠페인은 이번 규칙상 미실행이다(확인 못 함 1) |
| D8 | Minor | **해결** | `build/make_dev_venv.sh:17-19`가 `importlib.metadata.version`을 쓴다. `ENV/make_dev_venv_rerecord.txt`에서 전체 스크립트를 scratch venv에 재실행했고 exit 0, 버전 줄에 rfdetr 1.11.2가 나온다. 평가자 #12에서 `importlib.metadata.version('rfdetr')` = 1.11.2다 |
| D9 | Minor | **해결** | `mbnas_web/src/lib/validate.ts:3`에 `MBNAS_server`가 0회다. `--include-web` forbidden 0(#2)이고 근거는 `PROVENANCE/D9_web_comment.txt`다 |

## 기준별 채점

### 1. call_graph — **25/25**
- **필수 검사**
  - closure exit 0: 재실행 #1, 최종 증거 `WP7/7V_FINAL/import_closure.txt`(`README.md:16`).
  - PLACEMENT 전 패키지 모듈 커버: 221/221. 재실행 #3과 독립 검산 #4가 같고, 게이트 테스트(#10)가 있다.
- **관찰 증거**: MODULE_MAP을 AST로 다시 계산했다(#16).
  - 원본 기준 사실은 일치한다(148/148, 148/148, 143/148, 차이 5는 설명됨).
  - 설치본의 차이 9개는 DECLARED 안에 있고 결정(DD-A16, WP-3 gateway)이나 contracts 간선 추가로 설명된다. 위치가 바뀐 34개는 PLACEMENT로 추적된다.
- **잔여(감점 아님)**: MODULE_MAP.json은 WP-0 시점에 동결된 분석이고, MODULE_MAP과 AST의 대조는 평가자 도구로만 수행됐다. 반복 검증이 필요하면 이 도구를 `scripts/dev/`로 옮기기를 권장한다.

### 2. latest_changes — **25/25**
- 필수 테스트가 모두 통과했다: block catalog 26, campaign adapter 19(+1 host-data skip), WPS8·DD-39 19.
- delta manifest 사슬이 일관된다(#5).
- DD-38, DD-39, WPS8 기능이 대상에 있다(EVAL_1 근거 유지, 트리 재확인).
- D7을 고쳐 12-arm 규모 smoke가 rate limit 없이 통과한다(합성).

### 3. env_compat — **25/25**
- `runtime/venv/pyvenv.cfg`: 3.10.22, system-site false.
- `constraints.txt`: torch 2.6.0+cu124(`:117`), ultralytics 8.4.173(`:128`). freeze 137/137 일치, pip check clean(#12).
- `WP1/install.log`와 venv 생성 재기록(`ENV/make_dev_venv_rerecord.txt`, exit 0)이 있다.
- **sg26 freeze 무변경**
  - freeze 기준선이 기록됐고, 평가자의 메타데이터 독립 재구성과 차이가 0이다(#13).
  - 프로젝트 시작 이후 site-packages와 bin 디렉터리에 추가·삭제가 0이다(디렉터리 mtime까지 확인, #14).
  - 프로젝트 이전의 freeze 기준선은 원래 없었다. 이 점은 증거에 명시돼 있고 대리 증명으로 보완됐다.

### 4. provenance — **25/25**
- **MANIFEST·SUMMARY·DELTA**
  - 파일별 원본 경로, HEAD, dirty, sha256, 복사 시각, 라이선스가 있다(EVAL_1 검증 유지).
  - SUMMARY counts가 manifest와 같다(D4 해결).
- **설계자 sha 비교 로그**
  - 비교 로그(`PROVENANCE/live_compare_*.txt`, 명령, exit 0), 역사적 원본(`historical/delta2.json` 등 7/7), 도구(`scripts/dev/snapshot_*.py`)가 TARGET_ROOT에 있다.
  - REVIEW_LOG `:14`가 이를 참조한다.
- **원본 무변경**: 재실행 #5·#6에서 603/603 일치이고, 마지막 delta 이후 etri_nas 쓰기는 0이다. `__pycache__`와 venv까지 포함해 0이다.

## 총점과 판정

| 기준 | EVAL_1 | EVAL_2 | anchor |
|---|---|---|---|
| call_graph | 20 | **25** | 증거·명령·exit 모두 있음, 재실행 확인 |
| latest_changes | 25 | **25** | 같음 |
| env_compat | 20 | **25** | 같음 |
| provenance | 20 | **25** | 같음 |
| **합계** | 85 | **100** | |

판정: **PASS**(합계 100 > 90, Blocker 0, Critical 0, 열린 C01 결함 0).

## 범위 밖 관찰 (채점 안 함, 다른 셋·다음 과제 참고)

1. 프로젝트 트리에 `__pycache__`가 1개 남아 있다: `tests/owner/t2/__pycache__`(10-28 06:42, P-final의 T2 heavy 실행 무렵 생성). pyc 정리 이후 다시 생긴 것으로 보이며 DD-A29 위생 문제다(C12/패키징 확인 대상).
2. 사용자 git index가 `.pyc` 45,991개를 추적한다(`runtime/test_root` 30,785, `runtime/venv` 15,155, 패키지 src 42). 작업트리에서 지워진 추적 파일은 51개다. DD-A61(c)대로 사용자 git 조치가 필요하며, 다음 과제(이관 저장소)의 allowlist에서 처리해야 한다.

## 확인 못 함

1. 실제 캠페인(/data)을 대상으로 한 read-only smoke. 이번 라운드 규칙상 /data에 접근할 수 없어 합성 12-arm 루트로만 검증했다.
2. 프로젝트 이전 freeze 기준선과의 freeze 대 freeze 비교. 기준선이 원래 없었으므로 메타데이터 재구성(현재 = 기록)과 디렉터리·항목 mtime(시작 이후 변경 0)으로 대신했다.
3. 10-23 21:07~10-26 19:30에 수정된 etri_nas 파일 551개(+`.pyc` 17개)의 작성 주체. 시각과 delta 사유로 판단했다. 그 이후 쓰기 0은 확인됐다.
4. 전체 owner 게이트와 public suite는 다시 실행하지 않았다. P-final 기록(REVIEW_LOG `:16`)은 public 1124/0, owner 1569/0이며, 이번에는 C01이 지정한 부분집합만 재실행했다.

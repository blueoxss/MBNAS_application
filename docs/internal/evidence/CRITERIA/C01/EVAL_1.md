# C01 사실·기준선 — 독립 평가 EVAL_1

- set: **C01** (`docs/internal/criteria/C01.json` v1.0)
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자 — 채점만 하며 결정은 설계자
- time: 2026-10-28T03:18:03+09:00 시작, 03:38+09:00 보고 작성
- tree hash (mbnas-source-tree/2, 221 files):
  시작 `sha256:634a083bb910a28d1aa183c9a6a2cee66da7c1fc937f01f8aa85d1bb4c4a301d`,
  종료 `sha256:76ff726a1fd34353c085913f33d0bbee8ce646ed7beeb41f68000d19e3559fc0`.
  평가 도중 다른 에이전트가 `mbnas_core/console/routes_runs.py`와 `mbnas_core/orchestration/retention.py`를 수정했다(DD-A55 (a)).
  `source_tree_hash.py --diff` 결과는 changed 2, added 0, removed 0이다. 종료 트리에서 closure 검사를 다시 실행했고 결과가 같았다(exit 0).
  지시된 `| tail -3`은 JSON 닫는 괄호만 출력하므로 `sha256` 필드를 인용했다.
- 환경: CPU 전용으로 실행했다(`CUDA_VISIBLE_DEVICES=""`, `-B`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`). `MBNAS_TEST_ROOT`와 `TMPDIR`는 세션 scratchpad를 가리켰다.
  etri_nas에는 `git --no-optional-locks`와 파일 읽기만 했다(`.git/index` mtime은 10-26 19:31 그대로다). /data는 D7을 재현할 때 읽기만 했다.
  프로젝트에 쓴 파일은 이 파일 하나다. 평가자 산출물은 `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/c01/`에 있다.

## 결론

총점은 **85/100**(20+25+20+20)이다. Blocker 0, Critical 0이지만 90을 넘지 못하므로 **FAIL**이다.

사실 자체는 재실행으로 모두 확인됐다.
- closure 검사 exit 0
- 스냅샷 sha 일치: 표본 30/30, 전수 603/603
- 라이브 etri_nas sha 일치 603/603. 이 프로젝트가 원본을 바꾼 흔적은 0건이다.
- venv 핀 137/137 일치
- 지정 테스트 64 passed, 1 skipped(host data)

감점은 결함이 아니라 증거 누락 세 가지 때문이다.
- D1: `PLACEMENT_MANIFEST`에 WP-4/5에서 새로 생긴 패키지 파일 9개의 행이 없다.
- D2: 설계자 sha 비교 로그와 스냅샷 도구가 TARGET_ROOT에 없고 세션 scratchpad에만 있다.
- D3: sg26 freeze가 바뀌지 않았다는 증거 파일이 없다.

세 가지를 고치면 각 기준을 25점으로 다시 채점할 수 있다(→ 100).

## 재실행 요약 (cwd=TARGET_ROOT, interpreter `runtime/venv/bin/python -B`)

| # | 명령 | exit | 결과 |
|---|---|---|---|
| 1 | `scripts/dev/check_import_closure.py` (시작 트리) | 0 | package_modules 214, host_files 90, violations 0, unresolved 0, forbidden 0, errors 0, excepted 1 (`adapters/base.py:32`, DD-A26(2)). 3.07 s |
| 2 | 같은 명령 (종료 트리 76ff726a) | 0 | 1과 같은 수치 |
| 3 | `check_import_closure.py --include-web` (참고, 필수 아님) | 1 | forbidden 1: `mbnas_web/src/lib/validate.ts:3` 주석의 `MBNAS_server` (D9) |
| 4 | `pytest tests/public/infra/test_import_closure.py` | 0 | 10 passed |
| 5 | `pytest tests/owner/search/test_block_catalog.py tests/owner/api/test_block_catalog_route.py` | 0 | **26 passed** (12.3 s) |
| 6 | `pytest tests/public/server/test_campaign_adapter.py` | 0 | **19 passed, 1 skipped** (`:550` real campaign root 부재, host_data) |
| 7 | `pytest tests/owner/api/test_registry_structure.py tests/owner/api/test_training_selection.py tests/owner/unit/test_schemas_contract.py -k "wrong_resolution or fixed_resolution or initialise_cuda or rfdetr_preset or non_ultralytics or archive_campaign or campaign_roots or native_fixed_resolution"` (WPS8·DD-39 계약) | 0 | **19 passed**, 245 deselected |
| 8 | 7과 같은 방식, host data 실제 캠페인 읽기 전용 smoke (`MBNAS_TEST_HOST_DATA=1 MBNAS_TEST_ARCHIVE_RUNS_ROOT=/data/mgjeong/etri_nas_runs -k real_campaign`) | 1, 1 | run1: cold list 2992 ms > 1500 ms (load avg 22–25). run2: `429 RATE_LIMITED` (`:583`). D7 |
| 9 | 스냅샷 무결성 (python -I, sha256+bytes) | 0 | 표본 30 (seed 20261028; tracked_clean 18, untracked 9, modified 3, delta 행 5): **30/30**. 전수 **603/603**. manifest 밖 파일 0, `.pyc` 0, `__pycache__` 0 |
| 10 | delta 사슬 | 0 | DELTA_20261023T220106: 11/11 일관(6행은 delta3이 다시 대체). DELTA_20261026T194059: 46/46이 MANIFEST와 일치(changed 40 + new 6) |
| 11 | 라이브 비교 (`git --no-optional-locks` rev-parse/log/ls-tree/ls-files/status + 파일 sha256, 읽기 전용) | 0 | 아래 "원본 무변경 수치" |
| 12 | MODULE_MAP AST 재계산 (평가자 도구 `scratchpad/c01/tools/mm_recompute.py`, python -I) | 0 | Part A/B: call_graph 절 |
| 13 | PLACEMENT 커버리지·출처 사슬 (python -I) | 0 | call_graph 절 |
| 14 | `python -c "import torch,torchvision,ultralytics…"`, `pip freeze --all`, `pip check` | 0/0/0 | py 3.10.22, torch 2.6.0+cu124 (CUDA build 12.4), torchvision 0.21.0+cu124, ultralytics 8.4.173, rfdetr 1.11.2. freeze 137/137 = constraints, 미핀 0, 누락 0, editable 5. "No broken requirements found." |
| 15 | 연구 환경 mtime 검사 (`find -newermt '2026-10-23 20:00'`, 읽기 전용) | 0 | env_compat 절 |

### 원본 무변경 수치 (counts only)
- etri_nas HEAD = `18c48151…` = 스냅샷 HEAD. 스냅샷 이후 커밋 0. branch `work/mbnas-integration-20261021`.
- MANIFEST 603행 대 라이브 파일: sha256 **일치 603**, 불일치 0, 라이브 부재 0.
- 행별 git 필드도 현재 git 상태와 603/603 일치한다.
  - tracked_clean 416: live blob = HEAD blob
  - tracked_modified 93
  - untracked 94: HEAD·index에 없음
  - head_blob = HEAD tree 509
- 복사 대상 디렉터리(server src/tests/contracts/scripts, web src/tests/e2e, yolo_ga_runtime)에 manifest에 없는 새 라이브 파일은 0이다.
- etri_nas에서 10-23 21:07 이후 수정된 파일(.git/node_modules/.venv 제외)은 568개다(docs 395, web 135 중 evidence 115, server 38).
  - 가장 늦은 mtime은 **10-26 19:30**이고 delta3 복사(19:40:59) 전이다. **그 뒤 32시간 동안 수정은 0이다.**
  - 이 중 12개가 mbnas_application을 언급한다. 모두 병행 세션의 S06 문서이고, "Other sessions' pytest (mbnas_application) … not touched" 같은 내용이다.
- 10-28 03:00 이후 수정 0. 캠페인 재개(03:29:20)는 /data에만 썼다.
- 스냅샷 이후 생성된 `.pyc` 17개(server src 6, tests 11)는 모두 10-23 21:08~10-26 17:18이다. 병행 세션이 delta 파일을 편집한 시각과 겹친다.

## 기준별 채점

### 1. call_graph — **20/25** (anchor 20: 사소한 누락(문서 1건) 외 충족)

증거
- closure 실행 증거는 `docs/internal/evidence/WP7/7V/closure_after.txt`다(1행 명령, 111행 `exit=0`; package_modules 214, host_files 89). 이전 증거로 `WP1/import_closure.{txt,json}`, `WP2/2V/import_closure.json`, `SYNC1/import_closure.txt`가 있다. 재실행 #1·#2·#4가 모두 통과했다.
- `MODULE_MAP.json`에는 203개 항목이 있다. WP-0 정적 분석 산출물이다(`REVIEW_LOG.jsonl:1` "static analysis of snapshot only; no execution").
- `PLACEMENT_MANIFEST.jsonl`에는 588행이 있다(moved 320, new 148, split 75, modified 22, deleted 19, reference_copy 4).

평가자 재계산 (#12, 설치본 = editable install로 TARGET_ROOT src를 가리킴. `.pth` 5개 확인)
- Part A: 스냅샷 원본 148개 모듈의 AST를 MODULE_MAP과 비교했다.
  - third_party: **148/148** 일치
  - lazy internal import(모듈과 행 번호): **148/148** 일치
  - imports_after_mapping: **143/148** 일치. 나머지 5개는 설명된다.
    - yolo_ga_runtime 4개: target이 null("not vendored")이라 매핑하지 않는 것이 설계다.
    - 분할 모듈 `training.selection` 1개: MODULE_MAP은 [contracts, core, model_runtime], 모듈 단위 AST는 [contracts, model_runtime, runner]다. split_plan의 runner 이관부(run_official_evaluation)를 반영한 표기로 해석했다. 해석이므로 확인 못 함 목록에 넣었다.
- Part B: 설치본의 패키지 간 import 집합을 `imports_after_symbol_moves`와 비교했다.
  - **130/138** 일치.
  - 8개가 다르지만 모두 선언된 의존(DECLARED) 안에 있다.
    - 제거된 core 간선: `mbnas_server.api.app`(WP-3 gateway), `mbnas_runner.training.context`(DD-A16)
    - 추가된 contracts 간선 5개
    - `mixed_pool/__init__`: 간선 없음
  - 계획 경로에서 옮겨진 대상 34개(DD-A10 history, DD-A12 `etri_mixed_blocks`, `owner_worker`, `console/routes_files` 등)는 **34/34**가 PLACEMENT로 추적된다.
- PLACEMENT 출처 사슬 (#13)
  - MODULE_MAP 원본 203/203에 PLACEMENT 행이 있다.
  - source_sha256이 스냅샷 MANIFEST(또는 delta가 대체한 이전 판)와 이어지는 비율: moved 320/320(이전 판 14 포함), split 74/75(1행 null, D5), deleted 19/19, reference_copy 4/4. reference_copy 4개는 사본 sha도 4/4 일치한다.

부족한 점
- **필수 검사 "PLACEMENT 전 패키지 모듈 커버" 미충족(D1).** .py는 206/214, 데이터 파일은 6/7이다. PLACEMENT에는 WP-4A/4B/4C, WP-5B/5C, WP-6의 행이 하나도 없다.
- MODULE_MAP과 설치본 AST를 대조한 프로젝트 쪽 산출물이 없다. 이 대조는 위의 평가자 재계산으로만 확인됐다.
- 참고: covered 212개 중 60개는 현재 sha가 PLACEMENT의 `target_sha256`(배치 시점 값)과 다르다. 이후 WP가 수정했기 때문이며 결함이 아니다. 다만 manifest 문서에 이 필드의 의미를 적어 두는 것이 좋다.
- 25점이 아닌 이유: 필수 검사 하나가 96%만 충족했다.
- 15점이 아닌 이유: closure가 현재 트리에서 실행돼 exit 0이고, 출처 추적은 96% 커버하며, MODULE_MAP 사실은 재실행으로 확인됐다.

### 2. latest_changes — **25/25**

증거
- delta manifest
  - `SOURCE_SNAPSHOT_DELTA_20261023T220106.jsonl`: 11행, 계약 1.4.2(DD-38/39)
  - `SOURCE_SNAPSHOT_DELTA_20261026T194059.jsonl`: 46행, 1.4.3·gate fix·DD-44/45
  - 사유: `SOURCE_SNAPSHOT_SUMMARY.json`의 `deltas[]`
  - 반영 기록: `SYNC1/HUNKS.md`, `SYNC1/delta_list.tsv`, `SYNC1/live_diffs/*`
- 프로젝트 테스트 증거: `SYNC1/pytest_touched_and_structure_gate.txt:18-19` "346 passed, 1 skipped … exit 0", `SYNC1/block_catalog_table_equality.txt`(라이브 = 이전 판 데이터 동일)
- 대상 존재
  - DD-38: `mbnas_model_runtime/src/mbnas_model_runtime/block_catalog.py`. 사용처는 `mbnas_core/src/mbnas_core/search/structure.py:29`, `mbnas_core/src/mbnas_core/console/routes_registry_structure.py:20`, `mbnas_model_runtime/src/mbnas_model_runtime/adapters/structure_native.py:22`
  - DD-39: `mbnas_server/src/mbnas_server/archive/campaign.py`, `config.py:124`(`MBNAS_GW_ARCHIVE_CAMPAIGN_ROOTS`), `archive/scanner.py:144`
  - WPS8: `tests/owner/api/test_registry_structure.py:127,140,148,346,357,399`, `tests/owner/api/test_training_selection.py:584`, `tests/owner/unit/test_schemas_contract.py:844`
  - PLACEMENT 행: block_catalog(WP-1C + designer split), campaign.py(WP-1F), 테스트 3개(WP-1G)
- 스냅샷의 untracked/modified 파일이 대상에 도달했는지 (#13)
  - server-source: 배치 34, 결정에 따른 삭제 6(egg-info 빌드 산출물)
  - server-tests: 배치 40, 삭제 1(DD-A12)
  - web: 47개가 같은 경로에 존재, 2개는 PLACEMENT로 배치
  - 문서화된 미이식 2건: block_catalog lazy refactor(데이터 동일, 실행 증거 있음), `test_registry_structure`의 delta3 hunk(ETRI root 개념 삭제)
- 재실행: #5 26 passed, #6 19 passed(+1 host-data skip), #7 19 passed. delta 사슬은 #10에서 일관을 확인했다.

참고 (감점 아님)
- 기준 문구는 "tests/owner의 test_campaign_adapter"이지만 테스트는 `tests/public/server/`에 있다. DD-A19(Archive는 public server)와 PLACEMENT WP-1G 행에 따른 배치이므로 프로젝트 결함이 아니다.
- host-data 실제 캠페인 smoke는 활성화하면 실패한다(D7). 필수 검사 밖이므로 Minor로 기록했다.

### 3. env_compat — **20/25** (anchor 20: 문서 1건 누락)

증거
- `runtime/venv/pyvenv.cfg:1-3`: home mbnas310, `include-system-site-packages = false`, version 3.10.22
- `constraints.txt`: `:117` torch==2.6.0+cu124, `:120` torchvision==0.21.0+cu124, `:128` ultralytics==8.4.173, `:102` rfdetr==1.11.2 (137 핀)
- `docs/internal/evidence/WP1/install.log`: editable 5개 각각 `exit=0`, `:33` import ok, console --help 4개 exit=0, `:43-44` pip check "No broken requirements"
- venv 생성 기록: `build/logs/make_dev_venv.log:6` `install exit=0`, `:12` freeze 137. system-site 검사 가드는 `build/make_dev_venv.sh`(실패 시 exit 3)에 있다.
- 재실행 #14: 버전과 핀이 모두 일치한다. `.pth`는 editable 5개(TARGET_ROOT src)뿐이고 etri_nas를 가리키는 것은 0개다.
- 재실행 #15 (mtime 대리 검사): 다음 4개 환경 모두 2026-10-23 20:00 KST 이후 site-packages 최상위 항목 변경 0, bin 변경 0이다.

  | 환경 | 비고 | 최신 dist-info |
  |---|---|---|
  | sg26 (`~/venvs/sg26`) | py3.9.21, system-site=true(base sg), ultralytics 8.4.165 | 2026-09-29 14:45 |
  | sg | | 2026-07-13 |
  | mbnas310 base | | 2026-10-15 |
  | 원본 etri_nas `.venv` | | 2026-10-21 |

부족한 점
- **필수 증거 "sg26 freeze 변화 없음"이 TARGET_ROOT에 없다(D3).** 프로젝트 시작 전 freeze 기준선도, 비교 로그도 없다. grep 'sg26'은 `TASK_STATE.md`의 주장 한 줄과 `criteria/C01.json`만 찾는다.
- 평가자의 mtime 검사로 사실은 뒷받침되지만, freeze끼리의 비교는 확인 못 함이다.
- 사소한 점: `make_dev_venv.log:10-11`은 버전 출력 단계에서 `AttributeError: rfdetr.__version__`이 났다(D8). 버전은 pip의 "Successfully installed" 줄로만 남아 있다.

### 4. provenance — **20/25** (anchor 20: 문서 1건 누락·라벨)

증거
- `SOURCE_SNAPSHOT_MANIFEST.jsonl`: 603행 모두에 다음 필드가 있다.
  - 원본 경로 `source_relpath`
  - HEAD: `head_blob_sha1`/`index_blob_sha1`/`git_blob_sha1`, 커밋은 SUMMARY `:7` head 18c48151…
  - dirty `git_status`, `sha256`, `bytes`, 복사 시각 `copied_at`, `license`
  - delta 51행에는 `supersedes_sha256`도 있다.
- `SOURCE_SNAPSHOT_SUMMARY.json`: branch, origin_main, status 175→175(복사 중 변경 없음), tool `snapshot_copy.py`(py 3.12.2 -I), deltas 2개와 사유
- DELTA 파일 2개
- 그 밖의 근거: `MIXED_SPACE_HASH_MAP.json` v2 vendor_origin, `vendored_reference/` 4개, `T2/T2_snapshot_guard.json`(스냅샷 sha 1475baa5… 전후 동일, 603 files)
- 재실행: #9 30/30·603/603, #11 라이브 603/603·git 필드 603/603
  - license 필드 검증: etri_nas 루트의 license류 파일 0. 행 값은 601행 "no LICENSE file…", 원고 2행 "owner reference only"
  - vendor_origin 8/8, reference_copy 4/4

부족한 점
- **"설계자 sha 비교 로그"가 TARGET_ROOT에 없다(D2).** 비교 도구와 결과물은 세션 scratchpad에만 있다(`scratchpad/delta.py`, `delta.json`(10-26 19:40, changed 40 + new 6), `delta2.json`(19:41, 0/0/0)). 스냅샷 도구 `scratchpad/tools/snapshot_copy.py`, `snapshot_delta.py`도 마찬가지다.
  - `REVIEW_LOG.jsonl:1-4`의 env_boundary_check와 `WP1/SUMMARY.md:52`는 명령도 exit code도 없는 서술이다.
  - `WP1/SUMMARY.md:18`에는 etri_nas 검사가 "확인 못 함"으로 남아 있다.
- 라벨: SUMMARY 최상위 수치가 첫 복사 시점 값 그대로다(`:16` files 597, `:19-21` 425/84/88). 현재 MANIFEST는 603행, 416/93/94다. `TASK_STATE.md:8`도 "597 파일"이다(D4).
- 이번에 직접 재실행해 원본 무변경은 확인됐다. 다만 감점은 재현 가능한 증거가 프로젝트 안에 없다는 점에 대한 것이다.

## 결함

| ID | 심각도 | 소유(WP/파일) | 내용 | 구체적 수정 |
|---|---|---|---|---|
| D1 | Major | WP-4A/4B(`db/rows.py`, `console/owner_routes.py`, `console/routes_revisions.py`, `orchestration/config_revisions.py`, `openapi/drafts/openapi-1.5.0.draft.json`), WP-5A/5B/5C(`mbnas_runner/transfer.py`, `mbnas_server/api/artifact_routes.py`, `mbnas_core/artifacts/locations.py`), WP-3A/5B(`mbnas_server/api/gateway_inputs.py`) → `docs/internal/provenance/PLACEMENT_MANIFEST.jsonl` | 패키지 파일 9개(.py 8 + 데이터 1, mtime 10-27 13:36~16:12)에 PLACEMENT 행이 없다. 필수 검사 "전 패키지 모듈 커버"가 206/214에 그친다 | 9개를 `kind:new`, `owner_wp`, `target_sha256`(현재), `at`, notes와 함께 추가한다. 패키지 파일마다 행이 있는지 검사하는 가드(예: `scripts/dev/check_placement_coverage.py` 또는 `tests/public/infra` 테스트)를 P 게이트에 넣고 재실행한다 |
| D2 | Major | 설계자 / `docs/internal/provenance`, `scripts/dev` | 설계자 sha 비교 로그와 스냅샷·delta·비교 도구가 세션 scratchpad에만 있다(`delta.py`, `delta.json`, `delta2.json`, `tools/snapshot_copy.py`, `tools/snapshot_delta.py`). REVIEW_LOG의 env_boundary_check는 명령도 exit code도 없는 서술이다 | 도구를 `scripts/dev/`로 옮긴다. `git --no-optional-locks` 기반 비교를 다시 실행해 `docs/internal/evidence/C01/live_compare_<ts>.{json,txt}`(명령, exit, 일치/불일치/부재/신규 수, HEAD)를 남기고 REVIEW_LOG에서 참조한다. 이번 평가의 수치(603/603, 0 new, 10-26 19:30 이후 0 수정)가 기대값이다 |
| D3 | Major | WP-1V/WP-8(env) / `docs/internal/evidence` | sg26(및 sg) freeze 무변경 증거가 없다. 기준선도 비교 로그도 없다 | `~/venvs/sg26/bin/python -B -m pip freeze --all`과 sg의 freeze, 그 sha256, dist-info mtime 목록(최신 2026-09-29, 프로젝트 시작 10-23 이전)을 `evidence/C01/research_env_freeze.txt`에 기록한다. "프로젝트 전 freeze 기준선 없음, mtime으로 대체"라고 명시하고 이후 단계에서 같은 명령으로 비교한다 |
| D4 | Minor | `SOURCE_SNAPSHOT_SUMMARY.json:16-21`, `TASK_STATE.md:8` | 최상위 수치가 첫 복사 값 그대로다(597, 425/84/88). 현재는 603, 416/93/94다 | SUMMARY에 `current`(files 603, by_status, bytes, deltas 반영) 블록을 넣거나 기존 값에 "initial" 라벨을 붙인다. TASK_STATE를 정정한다 |
| D5 | Minor | `PLACEMENT_MANIFEST.jsonl:447` (designer split, block_catalog) | `source_sha256`이 null이다 | 실제 분할 기준판의 sha를 기입한다(delta3 미이식이므로 이전 판 `77e2f1a5…`일 가능성이 높다. 설계자가 확인) |
| D6 | Minor | `scripts/dev/check_import_closure.py:80-83` | DD-A26(2) 데이터 예외가 WP-4 완료 후에도 `expires: "WP-4"` 라벨로 남아 있다. DD-A12는 구 hook 문자열 매핑을 상시 허용한다 | 상시 예외로 바꾸는 DD를 추가하고 라벨을 고친다. 아니면 리터럴을 제거한다 |
| D7 | Minor | WP-3C/4C 테스트 / `tests/public/server/test_campaign_adapter.py:143-165` | host-data 실제 캠페인 smoke를 활성화하면 실패한다. fixture가 rate limit를 끄지 않아 `429 RATE_LIMITED`(`:583`)가 난다(`test_perf.py:115`는 `rate_limit_enabled=False`를 쓴다). cold list 1500 ms 기준은 부하에 민감하다(2992 ms, `:560`). 기본 게이트에서는 skip이라 대상에서 실제 데이터로 검증된 적이 없다 | `make_gateway_settings(..., rate_limit_enabled=False)`로 바꾼다. 유휴 시점에 `MBNAS_TEST_HOST_DATA=1 MBNAS_TEST_ARCHIVE_RUNS_ROOT=/data/mgjeong/etri_nas_runs`로 재실행한다. 시간 기준은 부하 조건을 명시하거나 완화한다 |
| D8 | Minor | `build/make_dev_venv.sh`, `build/logs/make_dev_venv.log:10-11` | 버전 출력 단계에서 `rfdetr.__version__` AttributeError가 났다 | `importlib.metadata.version("rfdetr")`를 쓰고 다시 기록한다 |
| D9 | Minor (C01 기준 밖, C04/C08 참고) | WP-6 / `mbnas_web/src/lib/validate.ts:3` | 주석에 원 배치 경로 `MBNAS_server/src/mbnas_server/protocol/validator.py`가 있다. `--include-web` closure에서 forbidden 1건이다 | 주석을 `mbnas_core` 경로나 일반 표현으로 바꾼다 |

관찰 (채점 밖): 프로젝트 트리에 `__pycache__` 19개가 있다. `mbnas_server/src/mbnas_server/__pycache__/*.pyc`는 사용자 git에 추적되어 있다. `scripts/dev/__pycache__/package_build.cpython-310.pyc`는 03:17:43에 패키징 실행이 썼다(이 평가 시작 전). DD-A29/DD-A52(d)의 P 단계 정리 대상이다.

## 총점과 판정

| 기준 | 점수 | anchor |
|---|---|---|
| call_graph | 20 | 사소한 누락(PLACEMENT 9행) 외 충족 |
| latest_changes | 25 | 증거·명령·exit 모두 있음, 재실행 확인 |
| env_compat | 20 | sg26 freeze 증거 1건 누락 |
| provenance | 20 | 설계자 sha 비교 로그 미보존 + 라벨 |
| **합계** | **85** | |

판정: 90을 넘지 못하므로 **FAIL**이다(Blocker 0, Critical 0). D1~D3를 고치면(D4·D5 동반 권장) 각 기준을 25점으로 다시 채점할 수 있다.

## 확인 못 함
1. sg26/sg의 freeze 대 freeze 비교. 프로젝트 이전 기준선이 없어서 site-packages와 bin의 mtime만 확인했다.
2. 10-23 21:07~10-26 19:30에 수정된 etri_nas 파일 568개의 작성 주체. 시각, delta 사유, 병행 세션 문서로 미루어 판단했을 뿐 내용 단위로 귀속하지는 않았다. 10-26 19:30 이후 수정 0은 확인됐다.
3. 실제 캠페인 smoke(DD-39 실데이터)가 rate limit를 끈 유휴 상태에서 통과하는지.
4. 웹 archive/compare 화면이 12-arm 캠페인을 열 때 게이트웨이 한도(10 rps/burst 50)를 넘는지. D7에서 파생된 위험이며 검증하지 않았다.
5. `training.selection`의 imports_after_mapping 차이를 split 의미로 본 해석이 WP-0 작성 의도와 같은지.
6. 테스트 #5~#7은 시작 트리에서 실행했다. 평가 중 바뀐 파일 2개(`routes_runs.py`, `retention.py`)를 반영한 종료 트리에서는 closure만 다시 실행했다. 전체 owner 게이트와 public suite는 다시 실행하지 않았다.
7. etri_nas `.pyc` 17개의 작성 주체. 타임스탬프로만 판단했다.

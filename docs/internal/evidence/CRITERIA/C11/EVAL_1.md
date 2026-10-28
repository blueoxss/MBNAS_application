# C11 실구동·등록 — 독립 평가 EVAL_1

- set: **C11** (`docs/internal/criteria/C11.json` v1.0)
- evaluator: claude-opus-5-5 (Opus 5.5), 독립 평가자. 채점만 하며 결정은 설계자가 한다.
- time: 2026-10-28T08:09+09:00 시작, 08:26+09:00 보고 작성
- tree hash (mbnas-source-tree/2, 221 files): 시작과 종료 모두 `sha256:e220e72137165c57b08cd24388c11d64762b689b376a17911a774b8a3bb45236`이다. 평가 중 변경은 없었다.
  지시된 `| tail -3`은 JSON 닫는 괄호만 출력하므로 `sha256` 필드를 인용했다.
  - public 4 패키지 hash(`--package` 4개)는 `83af6d5f…`이고 public·registration ZIP의 `BUILD_INFO.source_tree`와 같다. 공개 쪽은 최종 ZIP 이후 바뀌지 않았다.
  - 5 패키지 hash `e220e721…`는 owner ZIP의 `BUILD_INFO.source_tree` `5a7bc592…`와 다르다. 차이는 `mbnas_core/src/mbnas_core/runner_api/routes.py`(mtime 08:06:54, DD-A64) 하나다. owner ZIP 빌드(07:06:56) 이후 바뀐 패키지 소스는 이 파일뿐이다(`find -newer`).
- 환경: CPU 전용(`CUDA_VISIBLE_DEVICES=""`, `-B`, `-p no:cacheprovider`, pytest `--basetemp`는 scratchpad)으로 실행했다. GPU와 docker는 쓰지 않았다. etri_nas에서는 `_2026_IVC_/main.tex` 한 줄만 읽었다.
  프로젝트에 쓴 파일은 이 파일 하나다. 실행할 때마다 앞뒤 `git status`가 같았고 `dist/` sha도 그대로였다.
  평가자 산출물(스크립트, ZIP 추출본, 실행 출력)은 `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/c11eval/`에 있다.

## 결론

총점은 **75/100**(20 + 15 + 20 + 20)이다. Blocker 0, Critical 0이지만 90을 넘지 못하므로 **FAIL**이다.

- 실구동 증거 자체는 탄탄하다. T5 재실행(최종 public ZIP `ce34b821…` = 현재 `dist/`)의 request id 연결을 원시 로그에서 다시 셌다. 결과(66/66 echo, 양쪽 56개, 1:1 49개)가 linkage json과 정확히 같다.
- registration ZIP의 FILE_LIST는 474행 모두 ZIP 멤버와 경로·크기·sha256이 일치한다. 생성기 `--check`와 `check_docs.py`는 둘 다 exit 0이다.
- 가장 큰 감점은 **필수 점검 대상인 VALIDATION_REPORT 집계표에 GPU 절반이 없다는 점**이다. T7 행이 "확인 못 함"으로 남아 있다(D1). GPU 실측(T7 재실행, GPU_PATH_VALIDATED YES)은 증거 폴더와 REVIEW_LOG에만 있다.
- 나머지 감점은 각각 사소한 누락이다.
  - 생성 후보를 runner로 돌린 public 위상 증거가 없다(D3).
  - T5의 core가 최신 core 변경보다 앞선다(D4).
  - 범위표 3행이 실제 ZIP과 다르다(D6).
  - 라이선스 검토 항목이 일부 빠졌다(D8).

## 기준별 채점

### 1. real_core_runner — 20/25 (anchor 20: 사소한 누락 외 충족)

증거:
- `docs/internal/evidence/T5/RERUN_README.md:3-12`(요약), `:22-31`(입력·위상·단계), `:35-53`(결과표).
- 입력 ZIP sha는 `rerun/zips_sha256_used.txt`에 있고, public `ce34b821…`와 owner `79cce02a…`가 현재 `dist/` 파일과 같다(`sha256sum`, registration `.sha256 -c` 성공).
- `rerun/t5f_console.txt`의 모든 단계가 exit=0이다(setup :3, core_start :7, runner_id :10, principals :13, containers_start :23, harness main :51, core_stop :57, coredown :61, docker stop :67). 남은 컨테이너는 `[]`이다(:68).
- 사슬(같은 파일 :25-59):
  - 업로드(`t5f-337d61fe…`, sha 일치)
  - 구조 검사: 정상 YAML PASS, 깨진 YAML FAIL(`SHAPE_MISMATCH`, `MERGE_INPUT_MISMATCH`, row 12)
  - OFF 생성 `run-81d06544bf80` COMPLETED, 후보 2개. 구조 artifact 985 B, sha `6999dc2b…`, verification core
  - 학습 `train-aa1d88e939f5`. `source_sha256 = ecb6c44b…`가 검사를 통과한 업로드 파일의 sha와 같다(:27 ↔ :33).
  - runner `runner-0a6ab6db`에서 pause → `PAUSED_AT_EPOCH` → `full_e0001.pt`로 resume, `device_used=cpu`, COMPLETED
  - 결과 행
  - 웹 `/`의 index와 asset이 dist와 바이트 동일
  - 다운로드 4건과 export의 sha 일치
  - validation ON 학습은 `NOT_EVALUATED`/`EVALUATION_REQUIRES_CUDA`, 평가 ON 탐색은 validate에서 거부
  - core 중단 시 health `degraded`, `503 CORE_UNAVAILABLE`
- 두 컨테이너 모두 `mbnas_core` import가 exit 1이고 find_spec는 None이다(`rerun/run/t5f_container_checks.txt:1-24`).

재실행·재계산:
- `linkcount.py`로 `rerun/run/t5f_request_ids.jsonl`, `rerun/run/t5f_gateway_container.log`(`mbnas_server.access`), `rerun/logs/core.log`(`mbnas_core.access`)를 직접 셌다.
  - harness id 66개가 모두 echo되었고, gateway 접근 로그에 66/66, core 접근 로그에 56개 있다.
  - **양쪽 1줄씩인 id가 49개**다. 이 49개의 method 불일치는 0이다.
  - gateway 1줄 + core 2줄인 id가 7개다. artifact·checkpoint 다운로드 6건(`…/record` + bytes 또는 `POST …/locations`)과 resume 1건(`GET run` + `POST resume`)이다.
  - gateway에만 있는 id가 10개다. 정적 웹 4, 업로드 2, 토큰 없는 health 1, core 중단 중 3이다.
  - harness host-check id 6개도 1:1로 연결된다. 따라서 양쪽 로그에 함께 나오는 id는 모두 62개다.
  - 이 값은 `rerun/run/t5f_request_id_linkage.json`과 정확히 같다.
- T4도 같은 방식으로 셌다(`linkcount_t4.py`). id 171개 중 양쪽에 있는 것이 162개, 1:1이 153개, 다중이 9개, gateway에만 있는 것이 9개다. `T4/RERUN_README.md:48-49`와 같다.
- `pytest tests/public/server/test_dd_a60_access_log_gateway.py tests/owner/api/test_dd_a60_access_log.py tests/owner/api/test_access_log_redaction.py tests/owner/api/test_error_trace_id.py` → **10 passed, exit 0**.

25가 아닌 이유:
- (D3) "생성 → 승인 YAML → runner 실행" 가운데 **생성된 후보를 runner가 실행한 증거가 public+원격 core 위상에 없다**.
  - T4와 T5의 명시 학습은 모두 `source.kind = "user"`(업로드 YAML)였다(`T5/rerun/harness/t45f.py:349`; T4·T5 재실행 증거에서 `"source": {"kind": "user"` 9회, 전체 증거에서 `generated`/`search` 학습 요청 0회).
  - 계약 `TrainingRequest.source.kind`는 `generated`와 `search`를 허용하므로 CPU에서도 가능했던 시험이다.
  - 생성 후보의 runner 학습은 T7(GPU, 단일 호스트 owner 설치, tree `9dafe4d0…`)에만 있다.
- (D4) T5의 core는 owner ZIP `79cce02a…`에서 나왔다. 그 뒤 core가 바뀌었다(routes.py 08:06:54, DD-A64 claim 경로). owner 재빌드와 T4/T5 재실행이 대기 중이다(`REVIEW_LOG.jsonl` 22행 `next`).
- (D5) T5의 "Web 조회"는 HTTP 수준 확인(index와 asset 바이트, SPA fallback, API JSON)이다. 브라우저 UI 확인은 단일 호스트 CPU 스택의 `WP6/V4B_FIX`(12/12, 더 이른 트리)에만 있다.
- 15가 아닌 이유: 사슬의 모든 고리에 실행 증거가 있다(업로드, 검사, 생성, 승인 YAML의 runner 실행, 결과, 다운로드, core 중단). request id 필수 점검도 독립 재계산으로 확인됐다.

### 2. cpu_gpu_evidence — 15/25 (anchor 15: 핵심 절반만 증거 있음)

필수 점검 대상은 `docs/internal/VALIDATION_REPORT.md` 집계표다(mtime 08:06:23, 08:24에 다시 확인해도 그대로였다).

- CPU 절반은 있다.
  - `:12-17`, `:19-23`: T4, T5, T6 ×3, T8 ×3, offline, 회귀 게이트. ZIP hash와 개수, exit 0이 함께 적혀 있다.
  - CPU 전용이라는 명시: `:169`, `:195`.
- **GPU 절반은 없다.**
  - `:18`이 `| T7 GPU | approved GPU window | 확인 못 함 (row owner: G2; see DD-A49/A52) | T7/ | — |`이다.
  - 보고서 어디에도 GPU 측정값, GPU 증거 트리 hash, TensorRT `BLOCKED_BY_ENV`가 없다. 줄 단위로 grep해 보면 GPU 언급은 `:12,:18,:19,:69,:169,:187,:211` 7줄뿐이고 모두 GPU 실측이 아니다.
- GPU 증거 자체는 실행되었고 일관된다.
  - `T7/RERUN_README.md:3`(tree `9dafe4d0…`), `:58-69`(GPU_PATH_VALIDATED YES, 한계: TensorRT BLOCKED_BY_ENV, BENCHMARK는 owner 스크립트, 0이 아닌 GPU의 CANDIDATE_TRAIN은 확인 못 함)
  - `T7/steps_rerun.jsonl`을 다시 세면 **62/62 ok**다. `t7_rerun_summary_20261027T180728Z.json`과 `t7_rerun_full_summary_20261027T181305Z.json`의 `tree_hash`는 모두 `9dafe4d0…`이다.
  - `REVIEW_LOG.jsonl` 6행은 ACCEPTED, `DECISIONS.md:66`(DD-A54)은 YES다.
- 같은 표의 T1, T2, T3 행(`:9-11`)도 "확인 못 함"이다. 그런데 REVIEW_LOG 2, 3, 5행에는 각각 수용 기록이 있다. 같은 보고서 `:23`은 `t2_compare 10/10`과 T2 pytest 32 passed를 적고 있어 T2 행(`:10`)과 보고서 안에서 모순된다.
- GPU 증거 트리(`9dafe4d0…`) 이후의 변경(DD-A64: 파일 25개 수정 + 1개 추가, 이후 routes.py)이 CPU로만 검증되었다는 구분은 `DECISIONS.md:80`에만 있고 집계표에는 없다.
- 등록 문서 `docs/registration/PROGRAM_OVERVIEW.md:68-72`는 반대 방향으로 어긋난다. T7은 YES인데 T4/T5는 "PARTIAL … 재실행 예정"이라고 적혀 있다(D2).
- WP-8 §2의 "C11 GPU 항목 BLOCKED_BY_ENV + 0점" 규칙은 GPU 창이 실제로 주어지기 전의 규칙이다. T7이 실행되었으므로(DD-A35, DD-A54) BLOCKED_BY_ENV 0점으로 처리하지 않고 집계 상태로 채점했다.
- 20이 아닌 이유: 필수 집계표에서 GPU 절반 전체가 빠져 있다. 라벨 하나가 빠진 수준이 아니고, 요약 12행 가운데 4행(T1, T2, T3, T7)이 비어 있다.
- 0이 아닌 이유: CPU 집계는 실행 증거로 완결되어 있다. GPU 증거도 실행되어 서로 맞는다. "확인 못 함"은 반대되는 결과를 주장한 것이 아니라 채워지지 않은 자리표시다.

### 3. registration_scope — 20/25 (anchor 20: 사소한 누락 외 충족)

재실행:
- `runtime/venv/bin/python -B scripts/dev/gen_registration_docs.py --check` → **exit 0**, `check: OK`. SYSTEM_STRUCTURE, mmd, svg, INTERFACES, FILE_LIST(자리표시) 5개 모두 unchanged다. 실행 전후 `git status`가 같았다.
- `runtime/venv/bin/python -I -B docs/internal/evidence/WP7/7C/check_docs.py` → **exit 0**, `RESULT PASS (0 problem(s))`.
  - 프로그램 13개(스크립트 6 + console 7)의 옵션과 문서가 일치한다.
  - 링크가 모두 해석된다.
  - svg ids missing `[]`.
  - needles: 55개 파일에서 0건.
  - 실행 전후 `git status`, `runtime/`, `runtime/runner`가 같았다.
- registration ZIP `95b541e9…`(`sha256sum -c` 성공)의 멤버는 476개다(`unzip -Z1`).
  - ZIP 안의 `docs/registration/FILE_LIST.md`는 **474행**이다. 476에서 자기 자신과 `FILE_MANIFEST.jsonl`, 즉 문서에 명시된 순환 제외 2개를 뺀 수다.
  - 경로·크기·sha256 불일치 0, 중복 0이다. 바이트 합계 9,111,376이 요약과 같다.
  - `FILE_MANIFEST.jsonl`은 475항목이고 불일치 0이다(`filelist_vs_zip.py`).
- ZIP 안의 docs/registration 9개 파일과 README, THIRD_PARTY_NOTICES, VERSION, docs/public/INSTALL은 트리와 바이트가 같다. FILE_LIST만 다른데, 트리 쪽은 "생성 대기" 자리표시라서 설계대로다.
- `tests/public/packaging/test_package_build.py -k "registration_zip_has_docs_and_generated_file_list or file_manifest_lists_every_other_entry or real_allowlists_keep_the_profile_boundaries"` → **3 passed, exit 0**. 끝난 뒤에도 `dist/`는 바뀌지 않았다.

내용 검토:
- 구조
  - `SYSTEM_STRUCTURE.md` §1: 계층 정의(S0, SS1–SS7, B, Module)
  - §2: 신뢰 영역
  - §3: Subsystem 표(생성)
  - §5: Block·Module 표(생성, Block 37개)
  - SS6 core는 Block과 하위 패키지 이름까지만 적혀 있다(DD-A41 f).
- 인터페이스
  - `INTERFACES.md` ①은 `openapi.json`(1.5.0, 경로 38, 연산 39)에서, ③은 `openapi_runner.json`(1.0.0, 연산 10, sha `8316ed7d…`)에서 생성되었다.
  - 손으로 쓴 ④ owner API 5개 경로는 코드와 같다: `mbnas_core/src/mbnas_core/runner_api/routes.py:663,674`, `mbnas_core/src/mbnas_core/console/owner_routes.py:56,74,87`.
- 편집 가능한 도식
  - SVG가 XML로 정상 파싱된다. viewBox 1280×1060, `id`가 붙은 `<g>`가 53개(SS1–SS7, Block 37개, 영역)다.
  - 인터페이스 화살표 `if-1`…`if-5`에 번호 배지가 있고, raster 이미지는 0개다.
  - `diagrams/system_structure.mmd`는 SYSTEM_STRUCTURE.md 안의 mermaid 블록과 같다.
  - WP-7C 렌더 PNG(05:15, SVG mtime 05:10 이후)를 열어 보았고 판독 가능하다.
- 범위표: ZIP 멤버와 대조하면 41개 주장 가운데 37개가 맞는다.

25가 아닌 이유:
- (D6) `SUBMISSION_SCOPE_TABLE.md`가 실제 ZIP과 다르다.
  - `:45` 루트 `pyproject.toml`: public·registration을 ○로 적었지만 실제로 들어 있다(`build/allowlists/registration.txt:83`, DD-A43 후속).
  - `:29` `tests/public/contracts/test_contract_history.py`와 `tests/public/tools/smoke_api_only.py`: 제외("정리 후 해제 예정")로 적었지만 들어 있다(deny 해제, VALIDATION_REPORT "Allowlist change").
  - `:42` `LICENSE*`: ●로 적었지만 LICENSE 파일이 없다. 사용자 결정 대기라는 사실이 표에 없다.
  - 낡은 문구: `SUBMISSION_SCOPE_TABLE.md:62`("T8에서 수행 예정"), `INTERFACES.md:20`, `:85`("누출 검사는 검증 예정"), `:139`(T5 "PARTIAL … 재실행 예정").
- (D2) 등록 ZIP에 들어간 `PROGRAM_OVERVIEW.md:69-72`의 T4, T5, T6, T8 상태가 최종 결과보다 이전 것이다.
- (D7) 문서에 적힌 재생성 점검을 출하된 최종 매니페스트로 돌리면 실패한다. `gen_registration_docs.py --root <추출본> --manifest <추출본>/FILE_MANIFEST.jsonl --only file-list --check` → **exit 1**(drift 1). 차이는 머리말의 "항목 474개" 대 "475개" 한 줄이고, 474개 목록 행은 같다.
- 참고(감점 아님): 추출한 registration 트리에 `--only structure --check`를 돌리면 drift가 난다. core와 owner 스크립트가 없기 때문이며, 구조 문서는 전체 트리에서 생성하는 설계라 정상이다.
- 15가 아닌 이유: 5개 구성요소(구조, 인터페이스, 파일 목록, 범위표, 도식)가 모두 있다. 실행한 점검이 모두 통과했고, 불일치는 문서 한 개(범위표)의 몇 행과 상태 문구에 국한된다.

### 4. license_promise — 20/25 (anchor 20: 사소한 누락 외 충족)

재실행·대조:
- `THIRD_PARTY_NOTICES.md`의 137행을 `runtime/venv`의 `importlib.metadata`에서 다시 만들었다(`venv_dists.json`).
  - 배포본 142개에서 mbnas 5개를 빼면 137개다. 누락 0, 초과 0, 버전 불일치 0이다.
  - 표시 내용: ultralytics와 ultralytics-thop의 AGPL-3.0(`:134-135`), 메타데이터 미확인 2건(`:26`, `:124`), 수기 추가 절 `:147-158`(YOLO11 파생 예시 YAML의 AGPL, parse_model 수정 사본, CUDA·python 기반 이미지, nginx, npm).
  - public·registration ZIP 안의 NOTICES는 트리와 같다(sha `327e340c…`).
- `LICENSE_STATUS.md:1-15`
  - 상태 "미확정"
  - 원본 무 LICENSE(`:7`), AGPL과 parse_model 파생물(`:8`), ETRI mixed와 hybrid(`:9-10`), 논문 각주(`:11`), npm(`:13`), RELEASE_REVIEW_PENDING(`:15`)
  - owner 전용이다. owner ZIP의 사본은 트리와 sha `33275de1…`로 같고, public·registration ZIP에는 0건이다(DD-A41 c).
- 각주 원문을 읽기 전용으로 대조했다. `etri_nas/_2026_IVC_/main.tex:407`에 "…the source code will be made available upon request during the review process via an anonymized and encrypted library link, and will be publicly released upon publication."이 있다.
- `docs/registration/RIGHTS_REVIEW.md:3`이 "결론이 아니라 질문 목록"이라고 명시한다.
  - Q1-1~Q1-5: AGPL, §13 네트워크와 비공개 core의 충돌
  - Q2-1~Q2-3: ETRI 연산자
  - Q3-1~Q3-2: hybrid
  - Q4-1~Q4-4: 가중치, NVIDIA, 미확인 2건, 데이터
  - Q5-1: npm 고지
  - Q6-1~Q6-3: 자체 라이선스
  - Q7-1~Q7-2: 원고 공개 약속
  - §8: 결론 기록 방법
- 공개본 라이선스 절은 `docs/public/INSTALL.md:219-226`에 있다. `check_docs.py` needles는 0건이었다.

25가 아닌 이유(D8):
- npm 런타임 의존성의 라이선스 목록이 없다. dist 번들에 react와 react-dom 18.3.1(그리고 전이 의존성)이 들어 있는데 `@license` 주석이 0개이고 라이선스 파일도 없다. Q5-1, `THIRD_PARTY_NOTICES.md:158`, `LICENSE_STATUS.md:13`에 미결 항목으로 적혀 있기는 하다.
- 각주의 **심사 기간 요청 시 제공**("made available upon request during the review process via an anonymized and encrypted library link") 절과 본문의 "open to public" 문구가 검토 항목에 없다. `LICENSE_STATUS.md:11`은 이 부분을 말줄임으로 생략했고, `RIGHTS_REVIEW.md:80`은 "게재 시 공개"만 옮겼다.
- 낡은 기록이 있다.
  - `LICENSE_STATUS.md`가 10-23 이후 갱신되지 않았다(`:13` "WP-7에서 생성").
  - `RIGHTS_REVIEW.md:50`은 예제 데이터가 "작성 중"이라고 하지만 `examples/synthetic4`가 이미 들어 있다.
  - 출하된 FILE_LIST의 라이선스 열 459행이 registration ZIP에 없는 `LICENSE_STATUS.md`를 가리킨다.
- `THIRD_PARTY_NOTICES` 자동 생성 명령이 저장소에 남아 있지 않다. 내용은 이 평가에서 재도출로만 확인했다.
- 사용자가 아직 정하지 않은 라이선스·권리자 선택은 지시대로 채점하지 않았다(자리표시가 정상).
- 15가 아닌 이유: 기준이 요구하는 항목(NOTICES, LICENSE_STATUS, AGPL·ETRI·논문 각주 검토 항목)이 모두 결론 없이 기록되어 있다. 빠진 것은 하위 항목과 갱신 상태뿐이다.

## 결함 표

| ID | 심각도 | 기준 | 소유(패키지/파일) | 요약 | 구체적 수정 |
|---|---|---|---|---|---|
| D1 | Major | cpu_gpu_evidence | WP-8 / `docs/internal/VALIDATION_REPORT.md:9-11,18` (행 소유 G2, WP-1V, WP-2/3) | 집계표의 T7 GPU 행이 "확인 못 함"이고 GPU 결과, GPU 트리, TensorRT BLOCKED_BY_ENV가 보고서 어디에도 없다. T1, T2, T3 행도 비어 있다. T2 행은 같은 보고서 `:23`과 모순된다 | 표에 "환경(CPU 실측 / GPU 실측 / BLOCKED_BY_ENV)" 열을 추가한다. T7 행에 PASS(GPU 사용자 승인 창, 10-27 18:02–18:27Z, tree `9dafe4d0…`, steps 62/62, 한계: TensorRT BLOCKED_BY_ENV, BENCHMARK는 owner 스크립트, 0이 아닌 GPU의 CANDIDATE_TRAIN 확인 못 함, G2-2/3/4는 tree `164292c6…`)를 기재한다. GPU 트리 이후 변경은 CPU로만 검증했다는 한 줄(DD-A64 + routes.py)을 넣는다. T1–T3 행은 REVIEW_LOG 2·3·5행과 `T2/`에서 채운다 |
| D2 | Major | cpu_gpu_evidence, registration_scope | WP-7C / `docs/registration/PROGRAM_OVERVIEW.md:68-72`, `INTERFACES.md:139` (registration ZIP `95b541e9…`에 포함) | 출하된 등록 문서의 검증 상태가 VALIDATION_REPORT와 양방향으로 어긋난다(T4/T5/T6/T8은 "PARTIAL·재실행 예정", T7은 YES) | D1 뒤에 §5 표와 INTERFACES §7 문단을 VALIDATION_REPORT에서 다시 쓴다. registration ZIP을 재빌드하고 T8 ZIP 스캔을 다시 한다 |
| D3 | Minor | real_core_runner | WP-8 P2 / `docs/internal/evidence/T5/rerun/harness/t45f.py:349` | public+원격 core 위상에서 생성 후보를 runner로 실행하지 않았다(`source.kind=user`만 사용). 계약상 `generated`와 `search`는 CPU에서도 가능하다 | T5(또는 T4)에 단계 하나를 추가한다. `POST /api/v1/training-runs` `{source:{kind:"generated", run_id:"run-…", candidate_id:"…"}}` → runner CPU 학습 → 결과 행·다운로드, request id 연결까지 기록한다 |
| D4 | Minor | real_core_runner | WP-7 P(owner 재빌드) + WP-8 / `mbnas_core/src/mbnas_core/runner_api/routes.py` | T4/T5 증거의 core(owner ZIP `79cce02a…`, tree `5a7bc592…`)가 현재 core(`e220e721…`, DD-A64 08:06)보다 앞선다 | owner ZIP을 재빌드한 뒤 T4와 T5의 host-core 부분(claim/lease 경로, 연결 집계)을 다시 실행하고 새 hash를 VALIDATION_REPORT에 적는다 |
| D5 | Minor | real_core_runner | WP-8 / `T5/RERUN_README.md:38` | T5의 Web 조회가 HTTP 수준이다. 브라우저 확인은 단일 호스트 스택(`WP6/V4B_FIX`, 이전 트리)에만 있다 | T5 gateway 컨테이너 URL에 `playwright.real.config.ts`의 V4 하위 집합을 돌리거나, VALIDATION_REPORT T5 행에 V4B_FIX를 브라우저 증거로 트리 hash와 함께 명시한다 |
| D6 | Minor | registration_scope | WP-7C / `docs/registration/SUBMISSION_SCOPE_TABLE.md:29,42,45,62`, `INTERFACES.md:20,85` | 범위표 3행이 실제 registration ZIP과 다르다(루트 pyproject.toml, 시험 2개). LICENSE*는 존재하지 않는 파일이다. "검증 예정" 문구가 낡았다 | 표를 allowlist 03:53판과 FILE_LIST에 맞춰 고친다(pyproject ●, 두 시험 ●). LICENSE 행에 "미정: 사용자 결정 대기"를 적고, 예정 문구를 T8 PASS 결과로 바꾼다. 이후 생성기 `--check`, `check_docs.py`, registration 재빌드를 한다 |
| D7 | Minor | registration_scope | `scripts/dev/gen_registration_docs.py` (file-list 머리말) | 출하된 최종 매니페스트로 file-list `--check`를 돌리면 exit 1이다(머리말 474/475). 문서화된 검토자 점검이 출하본에서 재현되지 않는다 | 매니페스트 항목 수를 셀 때 `FILE_LIST.md`와 `FILE_MANIFEST.jsonl`을 빼고 세어 1차·최종 매니페스트 결과가 같게 만든다(또는 "목록 행 N개"로 표기). 회귀 시험을 추가한다 |
| D8 | Minor | license_promise | WP-7C / owner / `THIRD_PARTY_NOTICES.md:158`, `LICENSE_STATUS.md:11,13`, `docs/registration/RIGHTS_REVIEW.md:50,77-86` | npm 라이선스 목록이 없고 dist에 고지가 0건이다. 각주의 심사 기간 요청 시 제공 절과 본문 "open to public"이 검토 항목에 없다. LICENSE_STATUS가 10-23판이다. RIGHTS_REVIEW:50이 낡았다. FILE_LIST 라이선스 열이 미동봉 파일을 가리킨다. NOTICES 생성 명령이 저장소에 없다 | Q7-3("심사 기간 익명 링크 제공 약속과 비공개 core의 관계")을 추가하고 LICENSE_STATUS `:11`에 원문 전체를 적는다. `package-lock.json`의 production 의존성으로 npm 목록을 만들어 NOTICES에 넣는다(dist 고지 파일 동봉 여부는 Q5-1로 유지). LICENSE_STATUS 날짜와 항목을 갱신하고 `:50`을 고친다. FILE_LIST 열 문구를 "licence undetermined (RIGHTS_REVIEW.md)"로 바꾼다. NOTICES 생성 스크립트를 `scripts/dev/`에 둔다 |
| D9 | Minor | real_core_runner(증거 표기) | WP-8 P2 / `T5/rerun/run/t5f_container_checks.txt:35`, `T5/RERUN_README.md:53` | "public responses … host name 'dev1': 1 files"라고 적혀 있지만 실제 hit은 harness가 owner DB에서 읽은 `lease_owner dev1:3505064`(`t45f.py:161,315`)다. 공개 응답 점검은 0/6이다. README의 "record + bytes" 설명은 7개 중 1개(core-local architecture)에만 맞고, 5개는 `POST …/locations`, 1개는 resume이다 | 표기를 "harness 출력(owner DB 읽기 포함)"으로 고치고 다중 줄 7건의 실제 경로를 README에 적는다 |

"실패"와 "미검증"의 구분:
- 재실행에서 **실패**한 것은 D7(exit 1) 하나다.
- D1과 D2는 문서 상태 불일치다.
- D3, D4, D5는 **미검증**(시험하지 않았거나 최신 트리에서 다시 돌리지 않음)이다.
- D6과 D8은 문서 누락·부정확이다.

## 총점·판정

| 기준 | 점수 | anchor |
|---|---:|---|
| real_core_runner | 20 | 사소한 누락 외 충족 |
| cpu_gpu_evidence | 15 | 핵심 절반(CPU)만 집계 |
| registration_scope | 20 | 사소한 누락 외 충족 |
| license_promise | 20 | 사소한 누락 외 충족 |
| **합계** | **75** | > 90 미달, Blocker 0, Critical 0 → **FAIL** |

효과가 큰 순서:
1. D1 + D2(문서 수정만으로 cpu_gpu_evidence가 25까지 오를 수 있다)
2. D3 + D4(owner 재빌드 후 T4/T5를 한 번 다시 실행하면 함께 해결된다)
3. D6 + D7
4. D8

## 확인 못 함

1. T5와 T4 자체의 재실행. docker `--no-cache` 빌드 247 s, 컨테이너 2개, host core가 필요하고 `runtime/test_root`에 쓰게 되므로 평가자 규칙 밖이다. 증거 열람과 원시 로그 독립 재계산으로 대신했다.
2. T7 GPU 재실행. GPU 사용이 금지되어 있어 `steps_rerun.jsonl` 62/62와 요약 JSON 열람으로 대신했다.
3. 로그가 실제 실행에서 나왔다는 진정성. 내부 일관성(시각, id, method 대응, sha 사슬)만 확인했다.
4. 현재 core(DD-A64 이후)에서 T4/T5의 동작.
5. 현재 SVG를 평가자가 직접 렌더하는 것. rsvg와 cairosvg가 없고 브라우저는 시간 초과였다. WP-7C의 렌더 PNG(05:15)로 대신했다.
6. AGPL, ETRI, 논문, NVIDIA 조건의 법적 결론과 사용자의 라이선스·권리자 선택. 설계상 평가 대상이 아니다.
7. 원고가 현재 심사 중인지, 그래서 각주의 심사 기간 요청 시 제공 약속이 지금 유효한지.
8. npm 전이 의존성의 라이선스 목록. 프로젝트도 평가자도 만들지 않았다.

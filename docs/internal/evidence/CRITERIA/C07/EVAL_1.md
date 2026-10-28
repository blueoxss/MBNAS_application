# C07 UI·기능 — 독립 평가 1회차 (EVAL_1)

| 항목 | 값 |
|---|---|
| 기준셋 | C07 `docs/internal/criteria/C07.json` v1.0 (gene_links · block_table · yaml_original_executed · compare_onoff_baseline, 각 25) |
| 평가자 | claude-opus-5-5 (독립 평가자: 채점만, 판정은 설계자) |
| 시각 | 2026-10-28 07:44–08:20 KST |
| tree hash (`runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`, mbnas-source-tree/2) | 시작 `sha256:5a7bc5920ac3c1fe630a45c8ea0dd4661cb754661a90da5c09441fb7237c4223` (221 files) → 종료 `sha256:e220e72137165c57b08cd24388c11d64762b689b376a17911a774b8a3bb45236` (221 files). `\| tail -3`은 JSON 닫는 괄호만 출력하므로 `sha256` 필드를 적었다. |
| 트리 이동 사유 | 평가 중 다른 에이전트가 `mbnas_core/src/mbnas_core/runner_api/routes.py`(08:06 수정)와 `tests/owner/runner_protocol/test_dd_a64_claim_reclaims_first.py`(08:11)를 바꿨다(runner claim, C07 범위 밖). 웹은 source_tree_hash 대상이 아니며 평가 내내 불변: `mbnas_web` src·tests·e2e·scripts·dist + 설정 7개(151 파일) 다이제스트 `981d172e4bfb0c59…`, `git status mbnas_web` 시작 = 종료. |
| 평가 대상 dist | `mbnas_web/dist`: `assets/index-CY9ctObG.js` `6c3f29a2…`, `assets/index-CG8HuodM.css` `1e0f94d5…`, `index.html` `08772e74…` (WP7/PREPACK 빌드) |
| 실행 환경 | CPU만(`CUDA_VISIBLE_DEVICES=""`), node v24.20.0, `npm_config_cache=<ROOT>/build/npm-cache`(+`npm_config_logs_max=0`), `TMPDIR`=스크래치. vitest는 `--no-cache`(git이 추적하는 `node_modules/.vite/vitest/.../results.json`를 건드리지 않기 위해). Playwright: `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64`, `PLAYWRIGHT_BROWSERS_PATH=<ROOT>/runtime/pw-browsers`. mock e2e는 **public ZIP에서 꺼낸 dist**를 스크래치 설정(프로젝트 `playwright.config.ts`를 import하고 testDir·outputDir·포트 4199·webServer만 바꿈; `npm run build` 미실행)으로 서빙했다. pytest: `runtime/venv/bin/python -B -m pytest -p no:cacheprovider`, `MBNAS_TEST_ROOT`·`--basetemp` 스크래치, 마커 `not gpu and not live and not legacy_records and not host_data`. 실스택: `scripts/dev/web_real_stack.py`를 그대로 쓰고 `--root`·`--web-dist`(ZIP dist)·`--evidence`를 스크래치로 지정, `MBNAS_HOME`만 스크래치로 돌리는 래퍼(`Stack.base_env` 오버라이드) 사용. |
| 원 출력(세션 임시) | `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c07/` — `web_gates.txt`, `vitest_c07_verbose.txt`, `mock_e2e_full.txt`(+`e2e_mock/cwd/evidence/` png 137), `pytest_yaml.txt`, `pytest_onoff_structure.txt`, `v4/`(`e2e_real_v4_part1.txt`, `e2e_real_v4_part2.txt`, `e2e_steps.jsonl`, `v4_state.json`, `stack_log.txt`, `screenshots/` png 42), `probe/`(`structure_probe.json`, png 2), `v4c/export_chain.json`, `zipdist/dist` |
| 부수 효과(공개) | `npm_config_logs_max=0` 때문에 npm이 `build/npm-cache/_logs`의 기존 npm 디버그 로그를 정리(삭제)했다(git 미추적, 문서·스크립트 미참조). 그 밖의 프로젝트 파일 변경 없음: `mbnas_web/dist` 3개·`results.json` sha 시작 = 종료, `git status mbnas_web` 동일, `runtime/`·`mbnas_web/` 신규 산출물 없음. vite 설정 번들 임시 파일은 생성 후 삭제(`node_modules/.vite-temp` 디렉터리 mtime만 변경). GPU·/data·etri_nas·build/source_snapshot 미기록. 실스택 기동 4회(그중 1회는 같은 root 재사용 시도라 시작 단계 `FileExistsError`로 프로세스 0개) 모두 `stop-own-processes: PASS, still_alive: []`. |

결론: **95/100 — 통과 조건 충족(합계 >90, Blocker 0, Critical 0).** 감점은 1건(gene_links −5, Minor D1): 블록 표(BlockTable) 행에서 같은 블록 상세로 가는 연결이 없다. 기존 브라우저 증거(mock 208, 실스택 V4)가 최종 dist 이전 빌드에서 만들어진 공백(D3)은 이번 재실행으로 메웠다: ZIP dist로 mock e2e 208/208, 실스택 V4 12단계 PASS, 실데이터 구조 probe, 업로드→개정→export 바이트 대조.

---

## 0. 배포 dist 대조 (지시 항목)

- `sha256sum dist/*/*.zip` = `docs/internal/evidence/WP7/7V_FINAL/SHA256SUMS_final.txt` 3건 일치: public `ce34b821…`, registration `95b541e9…`, owner `79cce02a…`.
- ZIP 멤버 해시(파이썬 zipfile, 읽기 전용): 세 ZIP 모두 `mbnas_application/mbnas_web/dist/` 멤버 3개이고 각 sha256이 로컬 `mbnas_web/dist` 3개와 일치(3/3 × 3). public ZIP의 `mbnas_web/` 멤버 149개 전부 로컬과 바이트 동일, 로컬에만 있는 4개(`e2e/weights.spec.ts`, `tests/fitness_oracle.ts`, `tests/fixtures_oracle.ts`, `tests/fixtures_oracle.test.ts`)는 owner ZIP에만 있음(DD-A43과 일치).
- 실스택 gateway가 ZIP dist를 실제로 서빙: 스택 `logs/gateway.log`에 `GET /assets/index-CY9ctObG.js status=200`, 스택 기동 단계 `asset same_bytes: true`.
- 참고(C07 범위 밖): 위 트리 이동으로 owner ZIP의 `mbnas_core/src/mbnas_core/runner_api/routes.py`는 현재 트리와 다르다(O4).

## 1. gene_links — 20/25 (anchor 20: 사소한 누락 외 충족)

**근거**
- 구현: `mbnas_web/src/components/ModelStructure.tsx:156-161`(scope별 gene 버튼 목록, `data-testid=gene-N`), `:175`(SVG 연결 그래프 `role="group"`), `:197`(그래프 노드 `role="button"`, Enter/Space 선택), `:111-118`(`select()` → 같은 BlockDetail + URL `?block=`), `:90`(엣지 = 보고서 엣지/`from` 참조; gene 순서로 잇지 않음). `src/lib/structure.ts:145-151`(CNN/Transformer/Merge/Detection/Other 분류), `:168` structureNodes, `:227` structureEdges.
- 프로젝트 증거: `docs/internal/evidence/WP6/V4B_FIX/screenshots/desktop-06-detail-structure.png`(열람: backbone 11 / neck 12 / head 1 gene, 종류 범례, 연결 출처 범례), `V4B_FIX/e2e_steps.jsonl:20`(V4-06 gene_nodes 24), `V4B_FIX/mock_e2e_full.txt:220-221`(208 passed, exit 0), `V4B_FIX/web_gates.txt:16`(vitest 591), `WP7/PREPACK/web_gates.txt:32,119`(최종 소스 vitest 593, exit 0).

**재실행**
- `npm run check:types` exit 0("types.gen.ts matches the contract") → `npm run typecheck` exit 0 → `npx vitest run --no-cache` **45 files / 593 passed, exit 0** → `npm run check:bundle` "bundle hygiene: OK (0 hits)", exit 0 (`web_gates.txt`, 23 s).
- vitest verbose 15 파일 **196 passed, exit 0**: model_detail_v2 30, structure 12("resolves relative, absolute, multi-input, skip and detection edges without flattening them", "labels operator classes and synchronizes selected gene with block details"), structure_parity 9("the edges the view derives from `from` are exactly the edges of the real report"), registry_structure_button 10, generated_detail_fixture 3.
- mock e2e(ZIP dist, 6 workers) **208 passed (1.0 m), exit 0**. 관련: `e2e/generation.spec.ts:154`(생성 후보 구조 그래프, Concat list-from [-1,2]·Detect [7,4] 엣지 출처 검사, 클릭한 블록·탭이 URL에 기록), `:183`(reload 후 block·model_tab 복원), `e2e/checkpoint_structure.spec.ts:36`(그래프 role=group·axe 위반 0·Tab/Enter로 노드 선택 → selected-block) — desktop-1280·mobile-375 모두 통과.
- 실스택 V4(ZIP dist): V4-01..11 **9 passed (1.3 m), exit 0**, V4-12 **1 passed, exit 0**; 스크린샷 42장, 모바일 가로 넘침 0/21, axe serious/critical 0·전체 위반 0(server, settings, structure-check-dialog, monitor, candidate-dialog, lineage).
- 실스택 구조 probe(스크래치 Playwright 스크립트, 학습 완료된 `yolo11n_user` 후보, page error 0): gene 24 = 그래프 노드 24; 종류 CNN 16 · Transformer 1(C2PSA) · Merge 4(Concat) · Detection 1 · Other 2(nn.Upsample); 엣지 30. 다중 입력 `block-0012←{0011,0006}`, `0015←{0014,0004}`, `0018←{0017,0013}`, `0021←{0020,0010}`, Detect `0023←{0016,0019,0022}` = YAML `from` [-1,6]·[-1,4]·[-1,13]·[-1,10]·[16,19,22]와 일치. 그래프 노드 #12 클릭과 gene 버튼 #12 클릭의 selected-block 본문·`?block=block-0012`가 같다(`graph_and_gene_same_detail: true`). 스크린샷 `probe/probe-structure-graph-block12.png`.
- pytest 구조 4 파일 **47 passed**(아래 §4 묶음 118 passed, exit 0의 일부): `tests/owner/search/test_structure_runtime_cpu.py` 3(실제 CPU forward의 multiscale head 엣지, Concat 공간 불일치 거부), `test_structure_observation.py` 23(:166 Concat 병합 행 실패, :219 Add 규칙, :383 ETRI mixed NAS repeats/wrapper — CNN/attention 혼합 풀; 단독 재실행 PASSED 확인), `test_structure_cpu.py` 7, `test_structure_adversarial.py` 14.

**공백**
- **"표 클릭→동일 상세" 미충족(D1, Minor)**: 후보 상세의 블록 표 `mbnas_web/src/components/CandidateView.tsx:99-110`(`BlockTable`, 사용 `:509`)에는 행 동작이 없어 같은 블록 상세로 가지 않는다(probe: block-table 행 24, 행 안의 버튼·링크 0). 연결된 경로는 gene 목록과 그래프 2개뿐이고, 표와 gene은 Pos 번호로만 눈으로 맞춰야 한다.
- 해석 메모: "그래프/표/목록"을 후보 단위(산점도 점·후보 표·계보 목록 → 같은 CandidateView)로 읽으면 충족한다(`tests/components.test.tsx:110` 산점도 점 click/Enter/Space → onOpen, e2e·probe의 candidate-table → 같은 dialog). 기준 id가 gene_links이고 WP-6의 상세 3요소(gene 구성도·블록 표·구성 YAML)와 짝을 이루므로 블록 단위로 읽어 −5 했다. 설계자가 후보 단위로 해석하면 25.
- Add 행은 실스택 모델(yolo11n에 Add 행 없음)에서 관측하지 못했다. 다중 입력 일반 로직(Concat과 같은 `from` 목록 처리)과 백엔드 Add 규칙 시험으로만 근거가 있다(감점 없음, 확인 못 함 목록).

## 2. block_table — 25/25 (anchor 25)

**근거**
- 구현: BlockDetail `ModelStructure.tsx:251-316` — block ID·stable_id·YAML 위치·native path·from·args·채널·shape·stride·params, 반복 수 3열(선언 D / scale R / 관측 O, `:283`, 서로 대체하지 않음), 소스별 입력 표, 내부 layer 표(path·module·parameters·공유 배지, `:306`), 기록이 없으면 "YAML 반복 수로 내부 layer를 추정하지 않습니다"(`:316`). 논리 블록↔YAML 행 표는 `CandidateView.tsx:85-110`(Pos·Block·Source·Operation·parent_block_id·YAML row). `docs/internal/analysis/FUNCTION_BASELINE.md` F3이 정의한 "블록 표"(선택 블록 상세·내부 layer 펼침·공유 표시·1:1 가정 금지)와 범위 일치.
- 프로젝트 증거: `V4B_FIX/e2e_steps.jsonl:20`(gene 24 = block_rows 24, YAML 1), `V4B_FIX/screenshots/desktop-06-detail-blocks-yaml.png`(열람: Concat `from=[-1,6]` 등 YAML row 열), 구조 route 실기록 V4-02 PASS / V4-03 FAIL(`SHAPE_MISMATCH`, `MERGE_INPUT_MISMATCH`).

**재실행**
- vitest(593 중): `tests/model_detail_v2.test.tsx:183` `block detail` 5건(반복 3열, 관측 없으면 "미관측"이며 공식 값으로 채우지 않음, v1 문서 의미 유지, 입력 표·Detect 필드·내부 layer 공유 표시, stable_id/native_path/module_class/section_basis/검사별 evidence level), `tests/structure.test.tsx` "uses explicit service IDs, YAML mapping and recorded internal layers".
- 실스택(ZIP dist): V4-02 `structure_status: PASS`(업로드 sha `ecb6c44b…`), V4-03 `FAIL` + `SHAPE_MISMATCH`·`MERGE_INPUT_MISMATCH` 표시, V4-06 gene 24 / 표 24행 / YAML 섹션 1.
- 실스택 probe: 블록 #2 C3k2 — 반복 D 2 / R 1 / O 1, 내부 layer 18행 중 공유 4(SiLU 공유 인스턴스) → YAML 1행 ↔ 내부 18계층(1:1 아님). 스크린샷 `probe/probe-internal-layers-block2.png`(열람).
- pytest(위 118 passed 묶음): `test_structure_runtime_cpu.py:51`("repeated gene retains internal layer mapping without one-to-one claim"), `test_structure_observation.py:261`(sequential wrapper), `:280`(관측 반복이 공식 값을 대신하되 곁에 보존), `:292`(공유 활성화가 모든 Conv 블록에 `shared=true`).

**공백**: 없음. 참고: 내부 계층은 선택 블록 단위로 펼치는 설계라 전 블록의 3자 대응을 한 표에서 보지는 못한다(F3 정의대로, 감점 없음).

## 3. yaml_original_executed — 25/25 (anchor 25)

**근거**
- 구현: gateway `mbnas_server/src/mbnas_server/api/uploads.py:87`(POST `/uploads/model-config`), `api/artifact_routes.py:390,414`(개정 목록·원문 바이트 relay), `:346`(export); core `mbnas_core/src/mbnas_core/console/routes_revisions.py:36,45`, `console/routes_files.py:216`(export-manifest: `architectures/*.yaml` objects); web `mbnas_web/src/components/ConfigRevisions.tsx:71`(개정 표·diff·원문 다운로드), `CandidateView.tsx:512-519`("구성" 탭).
- 결정: DD-A50(업로드 파일명 `?filename=`, 개정 목록 1회 조회·404면 탭 숨김, WebCrypto 없으면 "대조 못 함"), F5g/DD-A23(export에 `architectures/*.yaml`).
- 프로젝트 증거: `V4B_FIX/e2e_steps.jsonl:27`(V4-09 개정 3개 저장 파일 sha = source_sha256, match 3/3), V4-07(original = 업로드 sha), V4-08(+133/−26), `V4B_FIX/screenshots/desktop-08-diff.png`(열람: A/B 받은 바이트 sha = 기록 source_sha256 = X-Artifact-Sha256 배지), `docs/internal/evidence/T5/RERUN_README.md:45`(최종 public ZIP export zip, SHA256SUMS 검증).

**재실행**
- pytest 7 파일 **170 passed, exit 0 (104 s)**: `tests/owner/projection/test_config_revisions.py` 9(:120 업로드 id·sha·bytes, :128 original→selected→executed 순서·parent 체인·canonical_hash, :151 gateway·core 양쪽에서 원문 바이트 = `X-Artifact-Sha256` = source_sha256, CRLF·UTF-8 주석 보존, :162 미지 404·변조 409 `LOAD_FAILED`, :170 탐색 제안 generated 개정), `tests/public/server/test_wp3_gateway_relay.py` 43(:272 원본 바이트 저장, :285 multipart·415/422·413, :296 upload_id → 본문·sha 전달, core에 경로 미전달), `tests/public/server/test_artifact_routes.py` 29(:333 export zip에 store·core YAML 바이트, SHA256SUMS·결정성, :386 변조 객체 제외), `tests/owner/artifacts/test_artifact_wiring.py` 10(:230 export zip의 executed YAML = store 바이트), `tests/owner/api/test_artifacts_export.py` 9, `tests/owner/qa/test_wp3_security_negatives.py` 23(모듈 허용목록·1 MiB·alias/row/channel 한도, 빌드 전 거부), `tests/public/server/test_wp3b_gateway_fixes.py` 47.
- vitest(593 중): `tests/config_revisions.test.tsx` 22(lineDiff LCS·CRLF 차이 보고, 기본 쌍 selected↔executed, 받은 바이트 sha = source_sha256, 원문 다운로드와 표시본 다운로드 구분, 404면 탭 숨김·5xx 재시도), `tests/config_upload.test.tsx` 12(원본 바이트 그대로·`?filename=`, 413/422/507 코드 표시, 서버 sha와 브라우저 sha 불일치 표시).
- mock e2e: `e2e/config_upload.spec.ts:43`(업로드→검사→개정→diff→다운로드 전체, 브라우저에서 모든 sha 대조), `:125`(개정 route 없는 gateway), `:136`(업로드 거부) — 양 프로젝트 통과.
- 실스택(ZIP dist): V4-02 업로드 sha `ecb6c44b…` = `examples/models/yolo11n_user.yaml` sha256(직접 계산); V4-07 original/selected/executed = `ecb6c44b…`/`0e47ec6b…`/`e1d49bbe…`(V4B_FIX와 같은 바이트, 결정적); V4-08 diff +133/−26, 양쪽 verdict match; V4-09 다운로드 3개 sha = source_sha256(3/3).
- **업로드→개정→export 연쇄(별도 스택, 스크래치 스크립트 `v4c/export_chain_check.py`)**: 업로드로 학습한 run `train-f64352dd29b7`의 `GET /api/v1/runs/{run}/export` 200, 멤버 21, `X-Bundle-Sha256` = 본문 sha, `SHA256SUMS` 전 멤버 일치, 호스트 경로 0; `architectures/art-9547….yaml`(1318 B) = executed 개정 바이트, `architectures/art-e4fc….yaml`(975 B) = selected 개정 바이트(`v4c/export_chain.json`).
- V4B_FIX 기록 대조: 게이트웨이 store의 checkpoint 3개 재해시(`runtime/test_root/web_real_stack/20261027T184218Z/gateway/artifacts`) = 기록 `9a583668…`(31,600,161 B)·`a27736e6…`·`2a610529…` 일치.

**공백**: 감점 없음. 관찰 O1: export zip에는 selected·executed YAML이 들어가고 original 업로드 바이트는 들어가지 않는다. 원본은 보존되고 원문 다운로드로 확인되며(V4-09), F5g 설계 범위와 일치한다.

## 4. compare_onoff_baseline — 25/25 (anchor 25)

**근거**
- 구현: 평가 스위치 `mbnas_web/src/screens/Settings.tsx:439`. 웹 소스에서 `evaluation_enabled`를 쓰는 곳은 이 체크박스뿐이고(`lib/validate.ts:293`은 안내, `lib/generation.ts:6`은 읽기), 오류·연결 실패 처리에서 바꾸는 코드는 없다(grep). UNTRAINED · UNEVALUATED 배지 `lib/evaluation.ts:29`, `screens/Lineage.tsx:57`, `screens/Monitor.tsx:145`, `components/CandidateView.tsx:434`. gateway 503 `mbnas_server/src/mbnas_server/api/core_client.py:44`, health degraded `api/relay.py:213`; 계약 `docs/internal/WORK_PACKAGES/WP-3_GATEWAY.md:31`("자동 OFF/로컬 fallback 없음(테스트)").
- 프로젝트 증거: `V4B_FIX/e2e_steps.jsonl:35`(V4-11 생성 6행 = 배지 6 = 계보 6), `:43`(V4-12 "HTTP 503 CORE_UNAVAILABLE", validation_result_nodes 0), `V4B_FIX/screenshots/desktop-12-core-down-explicit-error.png`(열람: status degraded, 503 배너 2곳, Run 비활성 "Validation is not valid."), `desktop-11-generated-table.png`(열람: 보이는 행 모두 구조 PASS·UNTRAINED · UNEVALUATED, Seed 101·202·303 각 COMPLETE 2/2), `T5/RERUN_README.md:48`(최종 public ZIP: core 정지 → degraded, validate 503, `valid` 키 없음), `T7/README.md:19`(G2-1 BASELINE_TRAIN GPU via gateway, 이전 트리).

**재실행**
- mock e2e `e2e/generation.spec.ts` 8건 × 2 프로젝트 = **16 통과**: `:52` 기본 ON, OFF가 validate/run 본문에 실리고 reload 후 복원, `:89` 다시 ON, `:107` 계보에 생성 후보 UNTRAINED · UNEVALUATED, `:129` 생성 순서·결측 지표를 0으로 바꾸지 않음, `:206` 생성 후보 학습에 evaluate_only 미제공, `:235` baseline evaluate_only 제출에 epoch 키 없음. 비교: `e2e/archive.spec.ts:150`(두 run 비교, 다른 데이터셋 표시), `e2e/console.spec.ts:422`(compare 화면 axe 0).
- vitest(593 중): compare_conditions 6(기록 값이 다를 때만 "조건 상이", null은 추정 안 함), model_detail_v2 `model comparison`(:350) 3, wp6a_badges_workers 9, lineage_generated 10, generation_workflow 10, training_panel 13(evaluate_only는 baseline 소스에만, DD-A63 a 라벨).
- pytest 9 파일 **118 passed, exit 0 (70 s)**: `tests/owner/api/test_generation_real_cpu_training_status.py:23`(실제 OFF run 생성 후보 전부 UNTRAINED), `test_off_run_guards.py` 11, `test_training_selection.py` 37(:44 baseline 단일 단계 생성·멱등, :133 evaluate_only 학습 없음, :253 epoch 0 기록, :276 baseline에 가짜 부모 없음, :572 registry baseline scale), `tests/owner/search/test_generation_only.py` 19, `test_candidate_detail_lineage.py` 3 (+구조 4 파일 47). 장애 주입: `tests/public/server/test_wp3_gateway_relay.py:191` core 종료 → health degraded, validate·runs·presets·pause·snapshot 모두 503 `CORE_UNAVAILABLE`·`location: core`, 응답에 `valid` 없음(§3 묶음 170 passed에 포함).
- 실스택(ZIP dist): V4-11 OFF run `run-578893d8c2d8` COMPLETED, 생성 6행 = GeneratedTable 배지 6 = Lineage 배지 6, axe 0; V4-12 SIGUSR1로 core만 정지(stack 로그 "core stopped on SIGUSR1") → "status: degraded", "✖ HTTP 503 CORE_UNAVAILABLE: the core service is not reachable (ConnectError)", VALID 판정 노드 0, Run 비활성.

**공백**: 감점 없음.
- D2(Minor, 기록된 잔여): "학습 완료 · 미평가" 배지 툴팁(`CandidateView.tsx:432`, `Monitor.tsx:116`)이 평가 ON 탐색 arm의 CPU NOT_EVALUATED(`EVALUATION_REQUIRES_CUDA`) 행에도 "explicit training with validation OFF"라고 쓴다. 보이는 배지 문구는 맞고, DD-A63 보충이 1.5.1 잔여로 기록했으므로 감점하지 않았다.
- 최종 트리에서 registry baseline 학습을 UI로 끝까지 돌린 기록은 없다(확인 못 함). 필수 검사(generation.spec, 장애 주입)는 아니다.

## 5. 결함

| # | 심각도 | 기준(감점) | 소유 · 파일 | 내용 | 구체적 수정 |
|---|---|---|---|---|---|
| D1 | Minor | gene_links (−5) | WP-6 · `mbnas_web/src/components/CandidateView.tsx:99-110`, `ModelStructure.tsx:111-118` | 블록 표 행에서 같은 블록 상세로 가는 연결이 없다(그래프·gene 목록만 연결) | Pos/Block 셀을 버튼으로 바꿔 `?block=<그 위치의 structure block id>`를 설정하고 `model_tab=structure`로 전환(선택 상태를 CandidateView로 올리거나 ModelStructure가 URL·prop 변경을 따르게). vitest·e2e에 "그래프·표·목록 클릭 → 같은 selected-block·URL" 단언 추가 |
| D2 | Minor | compare_onoff_baseline (0, DD-A63 보충 기록) | WP-6 · `CandidateView.tsx:432`, `screens/Monitor.tsx:116` | 툴팁이 평가 ON CPU NOT_EVALUATED 행에도 "validation OFF"라고 씀 | `reason_code`별 title 분기(예: "평가 ON, CUDA runner 없음: latency 미측정, 지표 null") |
| D3 | Minor | 증거 (0, 재실행으로 확인) | WP-8/P · `docs/internal/evidence/WP6/V4B_FIX/*` | 브라우저 e2e(mock 208, 실스택 V4)가 이전 dist `index-BnpZcnwU.js`에서만 기록됨. 최종 dist `index-CY9ctObG.js`는 TARGET_ROOT에 vitest·check:bundle 증거만 있음 | 최종 dist의 mock e2e·V4 결과를 evidence에 기록(이 평가의 재실행: 208/208, V4 12단계 PASS) |
| D4 | Minor | 증거 라벨 (0) | `scripts/dev/wp3_three_process_smoke.py:476` | `# command:` 줄과 제목이 `wp3_three_process_smoke.py`로 고정되어 V4B·V4B_FIX `stack_log.txt` 머리말이 실제 실행 명령(`web_real_stack.py`)과 다름(README는 맞음) | `sys.argv[0]` 또는 Stack 클래스 속성으로 스크립트명 기록 |
| O1 | 관찰 | yaml (0) | core · `console/routes_files.py` export objects | export zip에 original 업로드 바이트 없음(F5g 설계 범위) | 필요하면 `config_revisions/<kind>-<rev>.yaml` + 색인 추가 |
| O2 | 관찰 | gene_links (0) | `mbnas_web/src/lib/structure.ts:147` | `patchmerge`(ETRI NASPatchMerge, 단일 입력 다운샘플)를 Concat/Add와 같은 Merge로 표시 | 별도 분류 또는 범례 설명 |
| O3 | 관찰(범위 밖) | — | `mbnas_web/playwright.config.ts` webServer, vitest 캐시 | 문서대로 `npm run e2e`를 돌리면 `mbnas_web/dist`를 재빌드하고 `mbnas_web/evidence/`를 만들며, `npx vitest run`은 git 추적 중인 `results.json`을 고친다 | 게이트 문서에 `--no-cache`, e2e 산출물 경로를 env로 |
| O4 | 관찰(범위 밖) | — | owner ZIP | 평가 중 `runner_api/routes.py` 변경으로 owner ZIP이 트리와 다름 | C12/P 재빌드 |

Blocker 0 · Critical 0 · Major 0 · Minor 4(감점은 D1만).

## 6. 총점·판정

| 기준 | 점수 | 앵커 |
|---|---|---|
| gene_links | 20 | 20 (사소한 누락 외 충족) |
| block_table | 25 | 25 |
| yaml_original_executed | 25 | 25 |
| compare_onoff_baseline | 25 | 25 |
| **합계** | **95/100** | |

판정: 합계 >90 충족, Blocker 0 · Critical 0 → **통과**(설계자 확인 대상). D1을 고치거나 설계자가 "그래프/표/목록"을 후보 단위로 해석하면 100.

## 7. 확인 못 함

- 최종 트리에서 registry baseline 학습을 UI로 끝까지 실행한 기록(실스택 V4는 사용자 YAML 학습; baseline은 API·unit·mock 시험과 이전 트리의 GPU G2-1만).
- GPU 경로: 평가 ON의 EVALUATED 행·실측 latency가 UI에 나오는 화면(GPU 미사용).
- ETRI mixed(NASWindowTransformer / NASGlobalTransformer / NASPatchMerge) 후보의 구조도를 실스택 UI로 본 것(백엔드 시험과 분류 regex만).
- Add 행이 있는 모델의 실스택 구조도(yolo11n에 Add 행 없음).
- 실제 run으로 한 Compare 화면(mock e2e만).
- 최종 dist에서 V4-10을 게이트웨이 검증 뒤 다시 실행해 `artifact_id`가 채워지는 것(V4-12가 core를 먼저 멈춤; V4-10 1차는 통과, 3개 sha 일치).
- registration·owner ZIP dist의 실서빙(바이트 동일성만 확인, 실서빙은 public ZIP dist).
- 실스택 모바일 기기 프로젝트: real 설정은 real-desktop 하나이고 모바일 스크린샷은 뷰포트를 바꾼 캡처다(V4B와 같음).
- 해소된 항목: V4B README의 "OFF candidate_count=2인데 6행"(확인 못 함) → seed 101/202/303 × 2 = 6(`V4B_FIX/screenshots/desktop-11-generated-table.png`, 이번 실스택도 6행).

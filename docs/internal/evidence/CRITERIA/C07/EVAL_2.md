# C07 UI·기능 — 독립 평가 2회차 (EVAL_2, spot re-score)

| 항목 | 값 |
|---|---|
| 기준셋 | C07 `docs/internal/criteria/C07.json` v1.0 (gene_links · block_table · yaml_original_executed · compare_onoff_baseline, 각 25) — EVAL_1과 같은 앵커 |
| 평가자 | claude-opus-5-5 (독립 평가자: 채점만, 판정은 설계자) |
| 시각 | 2026-10-28 10:16–10:30 KST |
| tree hash (`runtime/venv/bin/python -B scripts/dev/source_tree_hash.py`, mbnas-source-tree/2, `sha256` 필드) | 시작 = 종료 `sha256:f00987be7994203fc16f52140bd98b3f89e1b548b6a346255066727a36dcc40c` (221 files). EVAL_1 종료 `e220e721…` 이후 fix 3(DD-A64)·라운드 4(DD-A69)·fix 5(DD-A66/A68)·설계자 docstring 수정이 반영된 트리. |
| 웹 트리 | `mbnas_web/dist` = `assets/index-7f__3l0I.js` `df0d6b2d9ab5dce5…`, `assets/index-CG8HuodM.css` `1e0f94d5…`, `index.html` `9e8c8f76…`. EVAL_1 이후 바뀐 웹 파일(EVAL_1 시점 사본 = 07:06 owner ZIP의 `mbnas_web/` 멤버와 대조): `src/components/CandidateView.tsx`, `src/components/ModelStructure.tsx`, `src/lib/evaluation.ts`, `src/screens/Monitor.tsx`, `tests/detail_fixtures.ts`, `e2e/checkpoint_structure.spec.ts`, 신규 `tests/block_table_link.test.tsx`, dist. `src/api/*`·계약 타입·그 밖의 화면은 그대로. |
| 배포 ZIP 상태 | **평가 시점(10:16–10:30) `dist/`의 ZIP 3종은 구판**(07:05–07:06 빌드, 웹 dist `index-CY9ctObG.js`)이고 `docs/internal/evidence/WP7/PFINAL2/SUMMARY.md`는 아직 없었다(10:28 확인). 따라서 현재 트리와 내가 만든 스크래치 웹 빌드로 채점했다. `scripts/package.sh`에는 출력 경로 옵션이 없고 `dist/<profile>/`·`build/staging`·`build/wheels`에 쓰므로(진행 중인 P-final-2와 충돌, 프로젝트 쓰기 금지) 스크래치 패키징은 하지 않았다. ZIP 멤버 대조는 PFINAL2 뒤 추록 대상. |
| 실행 환경 | EVAL_1과 같음: CPU만(`CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`), node v24.20.0, `npm_config_cache=<ROOT>/build/npm-cache`(+`logs_max=0`), vitest `--no-cache`, Playwright `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64`·`PLAYWRIGHT_BROWSERS_PATH=<ROOT>/runtime/pw-browsers`, pytest `MBNAS_TEST_ROOT`·`--basetemp` 스크래치, 실스택은 `web_real_stack.py` + `MBNAS_HOME`만 돌리는 스크래치 래퍼. |
| 원 출력(세션 임시) | `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c07r2/` — `web_gates.txt`, `vitest_c07_verbose.txt`, `scratch_build.txt` + `build/dist/`, `mock_e2e_full.txt`, `pytest_yaml.txt`, `pytest_onoff_structure.txt`, `v4/`(`e2e_real_v4_part1.txt`, `e2e_real_v4_part2.txt`, `e2e_steps.jsonl`, `v4_state.json`, `stack_log.txt`, `export_chain.json`, `screenshots/` png 42), `probe/`(`structure_probe.json`, `badge_title_probe.json`, png 3), `web_src_diff.patch` |
| 부수 효과 | 프로젝트 파일 변경 없음: `mbnas_web/dist` 3개 sha·`git status mbnas_web`·git 추적 `results.json`(`03ba474c…`, 08:40 라운드 4 기록) 시작 = 종료. `build/npm-cache/_logs`는 이미 비어 있었음(0 파일). 실스택 1회 `stop-own-processes: PASS, still_alive: []`, 스크래치 `stack.out`의 bearer는 가림. GPU·/data·etri_nas·build/source_snapshot 미기록. |

결론: **100/100 — 통과(합계 >90, Blocker 0, Critical 0).** EVAL_1의 감점 원인 D1(블록 표 → 같은 블록 상세)이 고쳐졌고 vitest·mock e2e·**새 dist의 실스택**으로 확인했다. D2(툴팁)·D4(증거 머리말)도 FIXED, D3(최종 dist 증거)는 이전 dist 분은 프로젝트 증거로 옮겨졌고 새 dist 실스택 분은 이 평가의 재실행으로 확인(프로젝트 기록은 P-final-2 대기). 단서: 배포 ZIP이 구판이라 "ZIP 안의 dist = 현재 dist" 대조는 PFINAL2 뒤 추록에서 확인해야 한다(§0).

---

## 0. 요청 (a)(b)(c)

- **(a) 웹 게이트 (현재 트리)**: `npm run check:types` exit 0("types.gen.ts matches the contract") → `npm run typecheck` exit 0 → `npx vitest run --no-cache` **46 files / 600 passed, exit 0** → `npm run check:bundle` "scanned 3 file(s), 546817 bytes … bundle hygiene: OK (0 hits)", exit 0 (`web_gates.txt`, 22 s).
- **(b) 스크래치 빌드 = 배포 dist**: `mbnas_web`에서 `vite build --outDir <scratch>/build/dist --emptyOutDir` exit 0 → 3개 파일 sha256이 `mbnas_web/dist` 3개와 **바이트 동일**(`df0d6b2d…`, `1e0f94d5…`, `9e8c8f76…`; `diff` 무출력). 라운드 4 기록(`docs/internal/evidence/WP6/ROUND4_WEB/web_gates.txt:19-22`)과 일치.
- **(c) 새 dist의 실스택 e2e 공백**: 이전 dist와 새 dist의 소스 차이는 블록 표 링크(CandidateView·ModelStructure)와 배지 툴팁(evaluation.ts·Monitor·CandidateView) 두 가지뿐이고(`web_src_diff.patch` 242행; API client·계약 타입 불변, check:types 동일 계약), 두 변경 모두 vitest(`tests/block_table_link.test.tsx` 7건)와 mock e2e(`e2e/checkpoint_structure.spec.ts:71`, 양 프로젝트)로 덮여 있다. 그래서 공백만으로는 점수가 바뀌지 않는다. 다만 이번에는 공백 자체를 닫았다: 스크래치 빌드(= 배포 dist)를 실스택 gateway로 서빙해(`gateway.log`: `GET /assets/index-7f__3l0I.js 200`) V4-01..12 전부 PASS(아래 각 절). 프로젝트 증거 폴더에는 아직 새 dist의 실스택 기록이 없다(D3 잔여, P-final-2 예정).

## 1. gene_links — 25/25 (anchor 25)

**EVAL_1 D1 확인 → FIXED**
- 구현: `mbnas_web/src/components/CandidateView.tsx:99`(`structureBlockIdsByPosition`: ModelStructure와 같은 `structureNodes` 호출로 위치 → 구조 block id), `:114-131`(BlockTable Pos 셀이 `button`, `data-testid=block-row-N`, `data-block-id`, 접근 이름 "N · 이름: 구조·gene 탭에서 이 블록 상세 보기"; 구조 노드가 없는 위치·native 문서는 일반 셀), `:358-361`(`openBlock` → `model_tab=structure` + 선택 요청 seq), `:516`(`selectRequest={blockRequest}`), `:541`; `ModelStructure.tsx:40`(prop), `:129-139`(요청마다 그래프/gene 클릭과 같은 `select()` 후 상세 제목으로 포커스), `:298`(`<h4 tabIndex={-1}>`).
- vitest `tests/block_table_link.test.tsx` D1 4건 통과: 위치→id 대응(보고서 id, 없으면 `<model>#<pos>`, native는 0), 그래프 노드·gene 버튼·표 행의 selected-block 본문과 `?block=` 동일 + `model_tab=structure` + 포커스, 노드 없는 행은 버튼 없음, native 문서는 버튼 없음.
- mock e2e `e2e/checkpoint_structure.spec.ts:71` 양 프로젝트 통과(표 버튼 9개, 표 영역 axe serious 0·nested-interactive 없음, 세 경로 동일 본문·URL, 탭 전환, 포커스).
- **실스택 probe(새 dist, 현재 백엔드)**: 학습된 `yolo11n_user` 후보에서 그래프 노드 #12, gene 버튼 #12, 블록 표 행 #12가 같은 selected-block 본문과 `?block=block-0012`(`same3: true`), 표 클릭 후 `model_tab=structure`, gene #12 `aria-pressed=true`, 포커스 `H4`; 블록 표 24행에 버튼 24개. 스크린샷 `probe/probe-block-table-link-block12.png`.

**재확인(변경 없음)**
- 실스택 probe: gene 24 = 그래프 노드 24, 종류 CNN 16 · Transformer 1(C2PSA) · Merge 4 · Detection 1 · Other 2, 엣지 30, 다중 입력 `0012←{0011,0006}`·`0015←{0014,0004}`·`0018←{0017,0013}`·`0021←{0020,0010}`·Detect `0023←{0016,0019,0022}` = YAML `from`과 일치(EVAL_1과 같음). page error 0.
- vitest 16 파일 verbose **203 passed, exit 0**(model_detail_v2 30, structure 12, structure_parity 9, block_table_link 7 포함). mock e2e **210 passed (59.6 s), exit 0**(generation.spec:154·:183, checkpoint_structure.spec:40·:71, 양 프로젝트).
- pytest 구조 4 파일 47 passed(아래 §4 묶음 118 passed, exit 0).
- 실스택 V4: 9 passed (1.4 m) + V4-12 1 passed, exit 0; 스크린샷 42장, 모바일 가로 넘침 0/21, axe serious/critical 0·전체 위반 0(server, settings, structure-check-dialog, monitor, candidate-dialog — 이제 표 버튼 포함, lineage).

**공백**: 없음(Add 행·ETRI mixed 실스택 관측은 EVAL_1과 같이 확인 못 함, 감점 없음).

## 2. block_table — 25/25 (anchor 25)

- vitest: model_detail_v2 `block detail`(:183) 5건, structure.test "uses explicit service IDs, YAML mapping and recorded internal layers" — 600 중 통과.
- 실스택(새 dist): V4-02 `structure_status PASS`(업로드 sha `ecb6c44b…` = `examples/models/yolo11n_user.yaml`), V4-03 `FAIL` + `SHAPE_MISMATCH`·`MERGE_INPUT_MISMATCH`, V4-06 gene 24 / 표 24행 / YAML 1; probe 블록 #2 C3k2 반복 D 2 / R 1 / O 1, 내부 layer 18행 중 공유 4(1:1 아님).
- pytest: `test_structure_runtime_cpu.py:51`, `test_structure_observation.py:261/:280/:292` 포함 118 passed.
- 공백: 없음.

## 3. yaml_original_executed — 25/25 (anchor 25)

- pytest 8 파일 **173 passed, exit 0 (122 s)**: EVAL_1 묶음 7 파일(170) + 신규 `tests/owner/api/test_structure_report_hashes.py` 3(DD-A66/A68 D1: `model_path` 경로의 CRLF 파일도 source_sha256 = 파일 바이트 sha, 3-hash 정의, `SOURCE_SHA256_MISMATCH`·`SOURCE_SHA256_INVALID` 422). `test_config_revisions.py:151`(원문 바이트·CRLF·UTF-8 보존), `test_artifact_routes.py:333`·`test_artifact_wiring.py:230`(export zip YAML 바이트), `test_wp3_gateway_relay.py:272/:285/:296`(업로드).
- vitest config_revisions 22, config_upload 12; mock e2e `config_upload.spec.ts:43/:125/:136` 양 프로젝트 통과.
- 실스택(새 dist): V4-07 original/selected/executed = `ecb6c44b…`/`0e47ec6b…`/`e1d49bbe…`(EVAL_1과 같은 바이트), V4-08 diff +133/−26 양쪽 verdict match, V4-09 다운로드 3/3 sha = source_sha256; **export 연쇄**: 업로드 학습 run `train-57f84ea94249` export 200, 멤버 21, `architectures/art-3506….yaml`(975 B) = selected 개정 바이트, `architectures/art-97b4….yaml`(1318 B) = executed 개정 바이트, `SHA256SUMS` 전 멤버·`X-Bundle-Sha256` 일치, 호스트 경로 0(`v4/export_chain.json`).
- 공백: 감점 없음(O1 유지: export에 original 업로드 바이트 없음, F5g 설계 범위).

## 4. compare_onoff_baseline — 25/25 (anchor 25)

**EVAL_1 D2 확인 → FIXED**
- 구현: `mbnas_web/src/lib/evaluation.ts:38-49`(`trainedNotEvaluatedTitle`: `reason_code=EVALUATION_REQUIRES_CUDA` → "runner에 CUDA가 없습니다…", run의 `console.extra.training.validation_enabled === false` → "validation OFF 명시 학습(DD-5)…", 그 밖 → 일반 문구; 서버 필드 위치는 `mbnas_core/src/mbnas_core/protocol/training_validation.py:87`과 일치), `CandidateView.tsx:464`, `screens/Monitor.tsx:116`이 같은 함수 사용.
- vitest `block_table_link.test.tsx` D2 3건 통과(세 경우 문구, 상세·Monitor 표가 같은 title, 배지 문구 불변).
- 실스택(새 dist): validation OFF 업로드 학습 후보의 배지 title이 Monitor 표와 상세에서 모두 "validation OFF 명시 학습(DD-5): SEARCH_COMPLETE + performance_status NOT_EVALUATED로 끝나며 지표를 측정하지 않았습니다."(`probe/badge_title_probe.json`, `same: true`).

**재확인**
- mock e2e `generation.spec.ts` 8건 × 2 = 16 통과(:52 ON/OFF 본문·복원, :107 UNTRAINED · UNEVALUATED, :235 baseline evaluate_only), archive.spec:150·console.spec:422(비교).
- pytest 9 파일 **118 passed, exit 0 (65 s)**: `test_generation_real_cpu_training_status.py:23`, `test_off_run_guards.py` 11, `test_training_selection.py` 37(baseline :44/:133/:253/:276/:572), `test_generation_only.py` 19 등. 장애 주입 `test_wp3_gateway_relay.py:191`(core 종료 → degraded, 503 `CORE_UNAVAILABLE`, `valid` 없음)은 §3 묶음 173 passed에 포함.
- 실스택(새 dist): V4-11 OFF run `run-2dff7eb5d7b7` 생성 6행 = GeneratedTable 배지 6 = Lineage 배지 6; V4-12 core만 정지("core stopped on SIGUSR1") → "✖ HTTP 503 CORE_UNAVAILABLE …", VALID 판정 노드 0, Run 비활성.
- 공백: 감점 없음. 관찰 O5: Lineage의 "학습 완료 · 미평가" 배지(`screens/Lineage.tsx:59`)에는 title이 없다 — DD-A69가 1.5.1로 이월.

## 5. EVAL_1 결함 재검증

| # | EVAL_1 내용 | 상태 | 근거 |
|---|---|---|---|
| D1 (Minor, −5) | 블록 표 행 → 같은 블록 상세 없음 | **FIXED** | `CandidateView.tsx:99,114-131,358-361,516,541`, `ModelStructure.tsx:40,129-139,298`; vitest D1 4건; mock e2e `checkpoint_structure.spec.ts:71` ×2; 실스택 probe `same3: true`, 버튼 24/24 |
| D2 (Minor) | 툴팁이 CPU NOT_EVALUATED 행에도 "validation OFF" | **FIXED** | `evaluation.ts:38-49`, `CandidateView.tsx:464`, `Monitor.tsx:116`; vitest D2 3건; 실스택 title 확인(validation OFF 경우). CPU 경우의 실스택 관측은 확인 못 함 |
| D3 (Minor) | 브라우저 e2e 증거가 이전 dist에서만 | **부분 FIXED** | 이전 dist(`index-CY9ctObG.js`) 분은 `docs/internal/evidence/WP6/V4B_FINAL/`(README 포함 65 파일)로 옮겨짐; 새 dist는 `ROUND4_WEB/`(vitest 600, mock 210)만 프로젝트에 있음. 새 dist 실스택은 이 평가에서 PASS(스크래치), 프로젝트 기록은 P-final-2 대기 |
| D4 (Minor) | 실스택 로그 머리말이 실제 실행 명령과 다름 | **FIXED** | `scripts/dev/wp3_three_process_smoke.py:78-84`(`_script_display`), `:483-487`; 이번 `v4/stack_log.txt` 머리말 "# stack_wrapper.py on the WP-3 three-process stack …", "# command: … stack_wrapper.py …" |
| O1 | export에 original 업로드 바이트 없음 | 유지(설계) | 이번 export도 selected·executed만 |
| O2 | `patchmerge`를 Merge로 분류 | OPEN(관찰) | `mbnas_web/src/lib/structure.ts:147` 변경 없음 |
| O3 | `npm run e2e`가 dist 재빌드·`mbnas_web/evidence` 생성, vitest가 git 추적 results.json 수정 | OPEN(관찰, 범위 밖) | `playwright.config.ts` 불변; results.json은 08:40 라운드 4 실행으로 바뀐 상태(DD-A67 git 위생은 사용자 조치) |
| O4 | owner ZIP이 트리와 다름 | 진행 중 | 이제 ZIP 3종 모두 구판; P-final-2 재빌드 중 |

## 6. 남은 결함

| # | 심각도 | 소유 · 파일 | 내용 | 조치 |
|---|---|---|---|---|
| D3 | Minor (감점 0) | WP-8/P · `docs/internal/evidence/WP6/` | 새 dist(`index-7f__3l0I.js`)의 실스택 V4 기록이 프로젝트 증거에 없음 | P-final-2 실스택 결과 기록(또는 이 평가의 `c07r2/v4/` 복사, bearer 없는 파일만) |
| D5 | Minor (감점 0, C07 추록 조건) | P-final-2 · `dist/` | 평가 시점 배포 ZIP 3종이 구판(웹 dist `index-CY9ctObG.js`, D1·D2 수정 미포함) | PFINAL2 ZIP의 `mbnas_web/dist/` 3개 = `df0d6b2d…`/`1e0f94d5…`/`9e8c8f76…` 확인(추록) |
| O1·O2·O3·O5 | 관찰 | 위 표 | 설계 범위 또는 1.5.1 이월 | — |

Blocker 0 · Critical 0 · Major 0 · Minor 2(감점 없음).

## 7. 총점·판정

| 기준 | EVAL_1 | EVAL_2 | 앵커 |
|---|---|---|---|
| gene_links | 20 | **25** | 25 (D1 FIXED, 실스택 포함 확인) |
| block_table | 25 | **25** | 25 |
| yaml_original_executed | 25 | **25** | 25 |
| compare_onoff_baseline | 25 | **25** | 25 |
| **합계** | 95 | **100/100** | |

판정: 합계 >90, Blocker 0 · Critical 0 → **통과**. 조건: PFINAL2 ZIP의 웹 dist가 현재 dist와 바이트 동일한지 추록에서 확인(다르면 ZIP 쪽 증거만 다시 본다; 소스·dist 수준 판정은 위와 같다).

## 8. 확인 못 함

- 새 dist가 들어간 배포 ZIP의 멤버 대조(평가 시점 `dist/` 구판, PFINAL2/SUMMARY.md 없음).
- 평가 ON인 CPU NOT_EVALUATED(`EVALUATION_REQUIRES_CUDA`) 행의 툴팁을 실스택에서 본 것(vitest·컴포넌트 시험만; 실스택은 validation OFF 경우만 확인).
- 최종 트리에서 registry baseline 학습을 UI로 끝까지 실행한 기록, GPU 경로 화면, ETRI mixed·Add 행 모델의 실스택 구조도, 실제 run의 Compare 화면, 게이트웨이 검증 뒤 V4-10 재실행, 실스택 모바일 기기 프로젝트 — EVAL_1과 같음.

## Addendum (final ZIPs) — 2026-10-28 11:53 KST

- 대상(`dist/`, `docs/internal/evidence/WP7/PFINAL2/SUMMARY.md:29`와 일치): public `bc0500bdfa43ddf5fb74bde4cf51a967e06f45bd2b901ba26da0794c1f0853d3` (2,716,913 B, 10:47), registration `dea510dfb54dd85da556ac38423f747a0a7d1afbff7398c501127bd0f8b92f6e` (2,792,584 B, 10:48). `sha256sum dist/*/*.zip` = 각 `.zip.sha256` 사이드카.
- 방법: ZIP마다 새 스크래치 디렉터리에 `unzip -q <zip> 'mbnas_application/mbnas_web/dist/*'`(exit 0) 후 `sha256sum`(읽기만, 실행 없음). 원 출력 `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c07r2/addendum/{public,registration}/`.
- 결과: 두 ZIP 모두 `mbnas_web/dist/` 멤버가 정확히 3개(`unzip -Z1` 집계)이고 해시가 기대값과 일치한다.

| 파일 | 기대값 | public ZIP | registration ZIP |
|---|---|---|---|
| `index.html` | `9e8c8f761e7b56c7e66ecf90d563a2ff3a4118df75f49d9004423b1ea9897552` | 일치 | 일치 |
| `assets/index-7f__3l0I.js` | `df0d6b2d9ab5dce599cbb352062be2a2065634c5fe43a152b534f537664171a9` | 일치 | 일치 |
| `assets/index-CG8HuodM.css` | `1e0f94d5b6e2984c057f8522dde0a11beae25c1d5bdff42376f0211413c18da2` | 일치 | 일치 |

  세 파일은 현재 `mbnas_web/dist`와도, EVAL_2의 스크래치 빌드(현재 웹 소스)와도 바이트 동일하다(`diff` 무출력). 즉 두 ZIP에는 D1·D2 수정이 든 dist(`index-7f__3l0I.js`)가 들어 있고, EVAL_2 실스택 V4·probe가 돌린 바이트와 같다.
- **D5: public·registration은 CLOSED.** owner ZIP(`dist/owner`, 11:27 빌드 `867cc577…`)은 DD-A73 재빌드 중이라 이 추록에서 보지 않았다 — 코디네이터가 sha를 주면 같은 3파일 대조로 닫는다(OPEN, owner만).
- 참고: 추록 시점 tree hash(같은 도구) `sha256:1b4bd1a4a45196e3c558d62507f109301973eddc180df5968ed7014a72e6708c` — EVAL_2(`f00987be…`) 이후 core 변경(DD-A73)이며 웹 dist 3개 해시는 그대로다. C07 점수 100/100·통과는 변동 없음.

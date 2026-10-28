# WP-6A test adaptations (web: mbnas_web/tests, mbnas_web/e2e)

작성: WP-6A, 2026-10-27. 기준: WP-6_WEB.md §1 항목 1·2·4·5·8(부분)·9·10, WEB_PRIVATE_INVENTORY.md §8/§9(R1–R8),
DD-A8, DD-A21, DD-A23. 규칙: 단언 약화 없음, 조용한 skip 없음. 모든 경로는 `mbnas_web/` 기준.
기준선(변경 전, 같은 세션 12:49 실행): vitest 37 files / 504 tests 통과. 변경 후: 39 files / 527 tests 통과(web_checks.txt).

## 1. 삭제 (2 파일, 29 tests)

| 파일 | 사유 | 커버리지가 옮겨간 곳 |
|---|---|---|
| `tests/fitness.test.ts` (6 tests; 스냅샷 원본 sha256 `95605bcc880c875aa2fb063a7f9a52a71593e3af0a1d5b08b71694e5f5ef7074`, WP-1에서 변경 없음) | R1 / DD-A8: 테스트 대상 `computeFitness` / `boundedReward` / `metricInvalidReason`이 `src/lib/fitness.ts`에서 삭제됨(콘솔은 서버 fitness만 표시). WP-6 계약 §1-1 "fitness.test.ts 삭제(서버 unit이 수식 고정)". | 수식·클리핑·null·무효 사유·음수 가중치·anchor 무효 → 서버 `tests/owner/unit/test_protocol_fitness.py`(내용 대조 완료: formula 110 ms/52.5 MB 예, clip 10/20/110/200/500, METRIC_MISSING/NOT_FINITE/AP·RECALL_OUT_OF_RANGE/LATENCY·SIZE_NONPOSITIVE, legacy .43 합·음수·키 집합 ValueError, 허용오차 +5e-7 수용, anchor (200,20)/(0,20) 거부). src에 **남는** `weightsSum`/`weightsSumOk`의 단언(.43 차단·비정규화, 1e-6 경계 수용/거부)은 그대로 `tests/fitness_weights.test.ts`로 이동(약화 없음; "바로 밖 2e-6 거부"는 서버 unit에 없으므로 웹에 유지). |
| `tests/preset_parity.test.ts` (23 tests; 스냅샷 원본 sha256 `4e6e1ed9e25e225fc1208edf3fde4e5f285e680f4f671432d3ed637804009b92`, WP-1에서 `console.paths` 행 제외(DD-A28)) | R2 / DD-A8: 비교 대상인 클라이언트 PILOT_SMOKE 템플릿(`pilotSmokePolicy`)이 삭제됨. 콘솔은 이제 서버 preset 응답만 쓰므로 "클라이언트 fallback이 서버 preset과 어긋나지 않음"이라는 테스트 목적 자체가 소멸. | 서버 preset 값 고정은 `tests/owner/unit/test_protocol_presets.py::test_pilot_smoke_values`(rungs 2/4/6, 가중치, gates, training, search, benchmark, retention, seeds, baselines 5종) + `test_example_config_files_in_sync`(config/examples 동기화)가 담당. |

## 2. 신규 (4 테스트 파일 + 1 테스트 전용 헬퍼)

- `tests/fitness_oracle.ts` (헬퍼, 테스트 전용): R1/DD-A21 — 픽스처 행의 `fitness`를 지표·가중치·anchor와 일관되게 채우는 공식(서버 공식과 동일, 무효 입력은 null). src에서 import 금지(`tests/bundle_hygiene.test.ts`가 검사). 사용처: `tests/fixtures.ts:68`, `e2e/weights.spec.ts:11,50`.
- `tests/fitness_weights.test.ts` (4): 위 §1의 이동 단언 3 + "fitness 모듈은 `WEIGHT_TOLERANCE`/`weightsSum`/`weightsSumOk`만 export"(DD-A8 회귀 방지).
- `tests/preset_auto_apply.test.tsx` (8): R2/R3 — 연결 전 빈 편집기·배너·클라이언트 템플릿 옵션 0·사전검사 대상 없음 안내(`issues-empty-policy`, clientIssues 0)·요청 0건; 연결 시 `/health`→`/protocols/presets` 자동 호출, DEFAULT_BASELINE14 자동 적용 + 라벨 "server preset DEFAULT_BASELINE14 (design example)" + "auto-applied on connect" 배지(Server Validate 카드·Settings 양쪽), Validate 본문 = preset; 연결 전 편집 보존(자동 적용 생략, 라벨 'manual edits …'); 적용 후 편집 시 배지 해제·PILOT_SMOKE 선택 라벨; 재연결이 로드된 run의 resolved protocol을 덮지 않음; presets 500 → 연결은 성공·편집기 비움·Settings 배너에 원인; 연결(health+presets) 진행 중 Validate 비활성; DEFAULT_BASELINE14 없는 서버 → 아무것도 대체하지 않음.
- `tests/wp6a_badges_workers.test.tsx` (8): 항목 5 — `training_status`(1.5.0) 우선, 1.4.x 생성 후보(필드 없음)는 UNTRAINED로 폴백, 다른 행은 추측하지 않음; GeneratedTable 모든 행에 "UNTRAINED · UNEVALUATED" + `NOT_EVALUATED` 문구 유지; 1.5.0 TRAINED 행은 배지 없음; CandidateView 생성 후보 → 배지, DD-5 행 → "학습 완료 · 미평가"(문구 불변), 평가 행 → 없음, detail의 training_status 사용. 항목 8(부분) — Worker 표 `Runner ID`/`Device ID` 열(값 또는 대시). R6 — 서버 대표 후보가 가시 행의 최고 fitness와 달라도 "differs from client rule" 배지 없음(서버 값만, 열 머리 "Representative (server)").
- `tests/bundle_hygiene.test.ts` (31): R2 수용 기준의 소스 측 가드 — src(번들 소스, `types.gen.ts` 제외)에 handoff/호스트 needle 14종 0건, 삭제 코드 식별자·문구 15종 0건, `BASELINE14` 리터럴 목록 0건, oracle/픽스처 import 0건. (빌드 산출물은 `npm run check:bundle`이 검사; vitest에 dist 조건부 skip 테스트는 추가하지 않았다. needle은 조각을 이어 붙여 만들어 이 파일 자신이 HOST_FACT 스캔에 걸리지 않게 했다.)

## 3. 수정 (단언 의미 유지)

| 파일:줄 | 코드 | 변경 | 사유 |
|---|---|---|---|
| `tests/fixtures.ts:7,68` | R1 | `computeFitness` → `fitnessOracle`(테스트 전용) | src에서 삭제된 함수. 값은 동일 공식. |
| `tests/fixtures.ts:208-308` | R2/R3 | `FIXTURE_BASELINE14`, `fixtureDesignExampleProtocol()`(10/30/90, AdamW, baseline14), `fixturePilotSmokeProtocol()`(2/4/6, `console.preset: PILOT_SMOKE`), `fixturePresets()`를 PILOT_SMOKE 1개(rung 1개) → **DEFAULT_BASELINE14 + PILOT_SMOKE**(서버 순서·이름 형태)로 확장 | WEB_PRIVATE_INVENTORY §8 A5 조치. 값은 handoff 설계 예시(DD-A21 허용), 경로는 합성 `/srv/...`(R4), device 합성. `value_provenance` 키·handoff 원문·호스트 경로는 넣지 않음. |
| `tests/validate.test.ts:2-9` | R2 | `defaultPolicy`/`pilotSmokePolicy` import → 같은 이름의 픽스처 별칭 | 템플릿 삭제. 모든 기존 단언(경로·가중치·epoch·warmup·budget·threshold·anchor·seed·epochs_added·중복 baseline·파생값) 그대로. |
| `tests/validate.test.ts:26,30,32`, `tests/qa_hardening.test.ts:248` | R4 | `/root/detection/...`, `/data/drone/NPS_YOLO/...` → `/srv/datasets/synthetic_*/...` | 호스트 경로 제거(SPLIT_SAME_ROOT 사례는 "search_val_root + '/'", "output_root = train_root"로 같은 구조 유지). |
| `tests/validate.test.ts` (신규 it 1) | R2 | `addRung({})` → L0 epoch 1, 균등 가중치 0.25, 0.5/0.05 기본 임계값 유지 | `SMOKE_WEIGHTS` 삭제에 따른 폴백 변경의 회귀 테스트(DD-A21 "addRung 기본값 유지"). |
| `tests/training_panel.test.tsx:10-18` | R2 | `defaultPolicy` → `fixtureDesignExampleProtocol` 별칭 | 기대값 90 epoch·AdamW 그대로(픽스처가 같은 설계 예시 값). |
| `tests/generation_workflow.test.tsx:9-19, 34-42` | R2/R3 | 별칭 동일; "checkbox OFF …" 테스트는 Settings 렌더 후 `applyServerPreset(fixturePresets()[0])`로 preset을 먼저 적용하고 `rungs` 길이 3을 추가 단언 | 편집기가 빈 상태로 시작(연결된 콘솔과 같은 경로로 정책 공급). 이후 단언(OFF 시 rung 입력 비활성·값 보존·경로 비활성/보존) 그대로. |
| `tests/settings_loss_profile.test.tsx:2-16` | R2 | 같은 방식으로 preset 적용 후 검사 | loss_profile 값이 템플릿이 아닌 preset에서 옴. 단언 그대로. |
| `tests/components.test.tsx:309-311` | R1 | `getByText(/Client recompute/)` → `getByText(/Fitness \(server\)/)` + `queryByText(/Client recompute/)` null | 행 삭제(DD-A8). 서버 값 행 존재 + 재계산 행 부재를 함께 단언(강화). |
| `e2e/weights.spec.ts:11,39-58,107-110` | R1 | `computeFitness` → `fitnessOracle`; `not.toContainText('not computable')`(재계산 행이 사라져 공허해진 단언) → "Fitness (server)" dd가 mock 서버가 찍은 값(4자리)과 정확히 일치 + `Client recompute` 부재 | 강화(서버 값 표시를 실제로 확인). |
| `e2e/console.spec.ts:364-385`(신규 test) | R2/R3 | 연결 전 배너·`policy-label`='no policy loaded'·rung 0행·Preset 옵션 1개 → 연결 → presets 1회 자동 요청·라벨(Server/Settings)·자동 적용 배지·rung 90 | DD-A21 수용 시나리오(desktop+mobile). |
| `e2e/console.spec.ts:387-415` | R2 | `/#/settings` 직행 → 연결(‘status: ok’) 후 Settings; 선택 옵션 'PILOT_SMOKE (client template)' → 서버 preset 'Pilot smoke (server preset, SMOKE)'; 편집 후 자동 적용 배지 해제, PILOT 선택 라벨 단언 추가 | 클라이언트 템플릿 삭제. 기존 단언(+10/+20/+60, 90, .43 → WEIGHTS_SUM_NOT_ONE, EPOCHS_NOT_INCREASING, PATH_NOT_ABSOLUTE, PILOT 배지, rung-2=6) 그대로. |
| `e2e/hardening.spec.ts:514-519`(신규 test) | 10 | 연결 전 Settings(빈 편집기+배너) axe serious/critical 0 | 새 화면 상태의 접근성. |
| `e2e/hardening.spec.ts:521-528` | R2 | 'axe: settings with visible client-side validation errors': `/#/settings` 직행 → `connect()` + 'status: ok' 후 Settings | 정책은 연결 시 preset에서 옴. 단언(WEIGHTS_SUM_NOT_ONE 표시, axe 0) 그대로. |
| `e2e/real-server.spec.ts:5-8`, `e2e/archive-real.spec.ts:5` | R4 | 주석: 토큰 이름 `MBNAS_GW_API_TOKEN`, CORS 예시 origin 8720 | 주석만. |

변경 없이 통과한 spec: `e2e/generation.spec.ts`, `e2e/final.spec.ts`, `e2e/mobile.spec.ts`, `e2e/perf.spec.ts`, `e2e/archive.spec.ts`
(연결 완료('status: ok')가 이제 "presets 로드·자동 적용 후"를 뜻하므로 연결 직후 Settings/Validate를 쓰는 spec이 그대로 결정적이다;
연결 진행 중 Validate 비활성 → Playwright click이 활성화를 기다림).

## 4. 설정 파일 (테스트 실행 경로)

- `playwright.real.config.ts`, `playwright.archive-real.config.ts` (R4): preview 포트 5173 → **8720**(DD-A7/A13 web preview; 5173은 원본 콘솔 포트), 주석의 `MBNAS_DEV_API` 기본 8000 → **8710**, 호스트 경로 주석 `/data/mgjeong/etri_nas_runs` 삭제, `MBNAS_E2E_BASE_URL`(예: 게이트웨이 8710이 dist를 직접 서빙, DD-A30) 지정 시 webServer 생략. 이 두 opt-in 스위트는 실행하지 않았다(실 서버·GPU 필요, WP-6B/6V 범위).
- `playwright.config.ts`(mock): 변경 없음(4173, DD-A22).

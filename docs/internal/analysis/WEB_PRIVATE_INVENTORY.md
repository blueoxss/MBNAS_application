# WEB_PRIVATE_INVENTORY — A4 웹 private 인벤토리 (WP-0)

작성 2026-10-23 · 분석자 A4 · 대상: `build/source_snapshot/application/MBNAS_web` (src / tests / e2e / scripts / vite·playwright 설정 / package.json) + 대조용 `application/MBNAS_server`(presets.py, fitness.py, validator.py, contracts/openapi.json 1.4.1).
방법: 정적 읽기 + grep + Python(`python3 -I`) 비교(HANDOFF_JSON ↔ 서버 handoff 파일 JSON 동치, pilotSmokePolicy ↔ `config/protocol.pilot_smoke.example.json`, types.gen.ts ↔ openapi paths/schemas 집합). vitest·playwright·openapi-typescript는 실행하지 않았다(스냅샷에 node_modules 없음). 경로 표기: `web/…` = `application/MBNAS_web/…`, `srv/…` = `application/MBNAS_server/src/mbnas_server/…`.

## 0. 결론 요약

1. 번들(src)에 들어 있는 private 성격 항목은 **두 뿌리**에서 나온다. (a) `web/src/lib/fitness.ts` — 서버 `srv/protocol/fitness.py`와 같은 공식의 재구현, 사용처는 `web/src/components/CandidateView.tsx:338` 한 곳("Client recompute" 행, :479). (b) `web/src/lib/policy.ts:42-370` — 서버 번들 `srv/protocol/handoff_protocol_20261006.json`의 **JSON 동치 사본(키·값 차이 0, 서버 파일 sha256 f0f43d78…)** 과 그 위에 얹은 `defaultPolicy()`/`pilotSmokePolicy()`/`BASELINE14`/`SMOKE_WEIGHTS`/`PILOT_ROOT`. 파생 사용처는 `state/context.tsx:111-114, 228`, `components/TrainingPanel.tsx:121`, `screens/Settings.tsx:56, 150-183`.
2. 서버 대체 데이터는 이미 계약에 있다: `GET /api/v1/protocols/presets` → `PresetList{presets:[PresetSummary+protocol]}` (`srv/api/routes_meta.py:73-75`, `srv/protocol/presets.py:137-156`: `DEFAULT_BASELINE14`, `PILOT_SMOKE`), `Health.presets`(PresetSummary 목록), `openapi.json x-required-default-baselines`(14개 id). Candidate 행은 `fitness`, `rewards.latency_reward/size_reward`(= r_T/r_S), `fitness_weights`, `gate{eligible,reason_code,checks[]}`를 이미 싣고 있고 UI도 이미 표시한다(`CandidateView.tsx:476-478`). 따라서 DD-A8은 **삭제·대체만으로 충족**되며 새 엔드포인트가 필요 없다.
3. 회색지대 3건은 결정이 필요하다: `lib/validate.ts`(서버 validator 오류 코드·규칙을 거울처럼 재구현한 입력 사전검사, 코드명은 공개 계약 문서에 있음), `lib/lines.ts:104-116 selectRepresentative` + `screens/Monitor.tsx:100-106`(대표 후보 선택 규칙을 클라이언트가 재계산해 "differs from client rule" 배지), `lib/policy.ts:570-579 addRung`(기본 승급 임계값 0.5/0.05, 가중치 폴백 `SMOKE_WEIGHTS[0]`).
4. 하드코딩 호스트 경로는 모두 `policy.ts` 안에 있다: handoff 원문의 `/data/drone/NPS_YOLO/*`, `/data/nas_console/runs`(:231-235)와 소유자 호스트의 `/root/detection/MBNAS_data/pilot_visdrone_v1`, `/root/detection/MBNAS_runs/pilot_smoke`(:392, :417-421). `147.x` LAN 호스트, `mgjeong`, `etri_nas_runs`는 src·tests·e2e에 **없음**(확인). 유일한 예외는 `web/playwright.archive-real.config.ts:9` 주석의 `/data/mgjeong/etri_nas_runs`.
5. 노출 점검: `vite.config.ts:20 build.sourcemap=false`; src에 `console.log`/`debugger`/`import.meta.env`/`process.env`/동적 import 0건; `src → tests|e2e` import 0건(역방향만 27개 파일). 실제 `dist/` 산출물은 스냅샷에 없어 번들 내용 자체는 확인 못 함.
6. 계약 위치: `web/scripts/gen-types.mjs:15`는 `../MBNAS_server/contracts/openapi.json`을 기본값으로 가정(환경변수 `MBNAS_OPENAPI`로 덮어쓰기 가능). `types.gen.ts`는 openapi 1.4.1과 paths 34/34, schemas 96/96 일치. 단 웹이 호출하는 `GET /api/v1/structure/block-catalog`(`client.ts:277-280`)는 **계약에 없고**(contracts/history에 `migrate_1.4.1_to_1.4.2.py`가 미적용 상태, EXPECTED sha `__FILL__`), 타입은 `lib/structure.ts:39-45`에 손으로 써 있다 → mbnas_contracts에서 1.4.2 반영이 선행돼야 한다.
7. 테스트 영향(§8): 직접 깨지는 것은 `tests/fitness.test.ts`, `tests/preset_parity.test.ts`, `tests/fixtures.ts:7,68`(27개 테스트/e2e 파일이 import), `tests/components.test.tsx:263`, `e2e/weights.spec.ts:11,46`, `e2e/console.spec.ts:363-384`; 간접으로 `defaultPolicy()`를 픽스처로 쓰는 `tests/validate.test.ts`, `tests/training_panel.test.tsx`, `tests/generation_workflow.test.tsx`, 그리고 초기 정책이 비게 되면 Settings 화면을 바로 렌더하는 테스트들(`e2e/hardening.spec.ts:447-453`, `e2e/generation.spec.ts:95-97`, `tests/generation_workflow.test.tsx:32-50`).

## 1. 범위·전제

- 전제 결정: DD-A1(e) 웹은 공개 패키지 `mbnas_web`, DD-A3 투영은 core 내부, DD-A7 포트 8700/8710/8720, DD-A8 fitness·handoff 임베드 제거·대체, 사용자 편집 protocol JSON은 유지.
- "private"의 판정 기준(DD-A1 (a)): 정책을 *결정*하는 코드(검색 prior/repair/교차/변이/부모 선택, gate 규칙, fitness·승급·예산·종료, search_state). 사용자가 입력하는 protocol 값과 그 입력 위생(형식 검사)은 private가 아니다.
- 확인 범위 밖: 라이브 원본(`etri_nas`)은 읽지 않았다(스냅샷으로 충분). 빌드 산출물·evidence 디렉터리는 스냅샷에 없다.

## 2. 인벤토리 A — 정책·prior·fitness·템플릿 (번들 내부)

| ID | 위치 (file:line) | 종류 | 용도 | 대체할 서버 데이터 | 영향 테스트 | DD-A8 최소 변경 |
|---|---|---|---|---|---|---|
| A1 | `web/src/lib/fitness.ts:92-103 computeFitness`, `:60-63 boundedReward`, `:66-83 metricInvalidReason` | fitness 공식 재구현 (서버 `srv/protocol/fitness.py:51-53, 78-107`과 동일 수식·사유 코드; 차이: 서버는 가중치 키 집합이 정확히 4개가 아니면 `ValueError`(`fitness.py:68-69`), 웹은 `WEIGHTS_INVALID`) | CandidateView의 "Client recompute" 1곳 | `CandidateEvaluation.fitness`, `.rewards.latency_reward/size_reward`, `.fitness_weights`(openapi 1.4.1; 웹 `types.gen.ts:1265-1272`) — 이미 `CandidateView.tsx:476-478`에 표시 중 | `tests/fitness.test.ts` 전체; `tests/fixtures.ts:7,67-70`(cand()가 fitness를 계산해 채움) → 이를 import하는 27개 파일; `e2e/weights.spec.ts:11,46`; `tests/components.test.tsx:263` | 삭제. `weightsSum/weightsSumOk/WEIGHT_TOLERANCE`(:13, :50-57)만 남긴다(사용처 `validate.ts:18`, `Settings.tsx:11,306` — 합=1 검사는 공개 계약 오류코드 `WEIGHTS_SUM_NOT_ONE`의 입력 위생). 픽스처용 공식은 `tests/` 전용 헬퍼로 옮기거나 픽스처에 fitness 값을 직접 적는다(§9 R1). |
| A2 | `web/src/components/CandidateView.tsx:12`(import), `:330-339`(run.resolved_protocol의 anchors + 행의 fitness_weights로 재계산), `:479`("Client recompute" 행, Δ 표시) | A1 사용처 | 서버 fitness의 클라이언트 재검증 표시 | 위와 같음; 추가로 `rewards`로 r_T/r_S는 이미 표시(:476) | `tests/components.test.tsx:263`(`/Client recompute/` 존재 단언); `e2e/weights.spec.ts:99`(`not computable` 부재 단언) | :330-339·:479 삭제, 단언을 "Latency reward r_T / size reward r_S" 또는 "Fitness (server)" 행으로 교체 |
| A3 | `web/src/lib/policy.ts:41-370 HANDOFF_JSON` | 프로토콜 템플릿 원문 임베드(= `srv/protocol/handoff_protocol_20261006.json`과 JSON 동치, 차이 0; `status: DESIGN_EXAMPLE_NOT_EXECUTABLE`, 10/30/90 rungs·weights·thresholds, `proposal.GA/IndependentRandom` 정책 메모, `unresolved_before_real_execution`) | `defaultPolicy()`·`pilotSmokePolicy()`·`BASELINE14`의 원천 | `GET /protocols/presets` → `presets[id=DEFAULT_BASELINE14].protocol`(`presets.py:62-66, 140-143`; 단 console.paths는 서버 호스트 예시 경로로 치환됨 `:65`) | A4·A5·A6·A7의 모든 테스트 | 삭제(§9 R2) |
| A4 | `policy.ts:375-377 BASELINE14`, `:383-385 defaultPolicy`, `:387-391 SMOKE_WEIGHTS`, `:392 PILOT_ROOT`, `:395-440 pilotSmokePolicy`, `:442-444 isPilotSmoke` | 템플릿 빌더(서버 `presets.py:24-36, 69-134`와 1:1 미러; Python으로 옮겨 비교한 결과 `tests/preset_parity.test.ts`의 23개 FIELDS + baselines 모두 일치) | 클라이언트 템플릿 2종, baseline14 고정 목록, PILOT 배지 판정 | 같은 엔드포인트의 `PILOT_SMOKE`·`DEFAULT_BASELINE14` protocol; baseline14 id는 `openapi.json x-required-default-baselines`, name/family는 preset protocol의 `console.baselines[]`; `isPilotSmoke`는 사용자 protocol의 `console.preset` 읽기이므로 유지 가능 | `tests/preset_parity.test.ts` 전체(서버 example JSON을 `../../MBNAS_server/config/…` 상대경로로 읽음 :12 — 분리 후 경로 자체가 깨짐); `tests/validate.test.ts:23-27,32-34,37-69,74-93`; `tests/training_panel.test.tsx:36,62,66-67,71,84,126`; `tests/generation_workflow.test.tsx:22,112,128-129,133-137` | `BASELINE14`·`defaultPolicy`·`pilotSmokePolicy`·`SMOKE_WEIGHTS`·`PILOT_ROOT` 삭제. `isPilotSmoke`·`getIn/setIn/unsetIn/stableStringify/rungsOf/addedEpochs/fixedHorizon/effectiveBatch/normalizePolicy/removeLastRung`는 사용자 입력 편집기 유틸이므로 유지 |
| A5 | `web/src/state/context.tsx:19`(import), `:103-114 TemplatePreset/CLIENT_TEMPLATES`, `:228`(초기 정책 = `normalizePolicy(defaultPolicy())`), `:229`(presetLabel), `:531-535 applyTemplate` | A3/A4 사용처: 편집기 초기 상태와 "client" 템플릿 | 서버 연결 전에도 편집기가 채워져 있게 함 | `applyServerPreset`(:536-540)와 `loadPresets`(:331-340)가 이미 있음 | `e2e/console.spec.ts:363-384`(기동 직후 `+10/+20/+60`, `90`, 'PILOT_SMOKE (client template)' 옵션); `e2e/weights.spec.ts:68,83,147-148`(기본 rung 가중치 전제); `e2e/hardening.spec.ts:447-453`, `e2e/generation.spec.ts:95-97`, `tests/generation_workflow.test.tsx:32-50`(연결 없이 Settings에 rung 입력이 존재해야 함) | `CLIENT_TEMPLATES`·`applyTemplate`·`templates` 제거; 초기 정책을 빈 객체(또는 `{schema_version, console:{}}`)로 두고, 연결 성공 시 `loadPresets()` 자동 호출 + (결정 D1) `DEFAULT_BASELINE14` 자동 적용 |
| A6 | `web/src/components/TrainingPanel.tsx:4`(import), `:121`("학습 설정 예시 불러오기" 버튼이 `defaultPolicy()` 주입) | A3 사용처 | recipe 없는 생성 전용 run에 예시 recipe 제공 | 같은 컴포넌트의 "서버 학습 recipe 불러오기"(:113-114, `client.presets()` :92)가 이미 동일 역할 | `tests/training_panel.test.tsx`(버튼 자체 단언 없음 — 확인: grep 결과 버튼 문구 미등장) | :121 버튼 삭제 또는 불러온 presets 중 `DEFAULT_BASELINE14` 선택으로 대체 |
| A7 | `web/src/screens/Settings.tsx:13`(import), `:56`(PresetBar의 client 옵션), `:105`(안내문), `:147-195 BaselinesCard`(:150,153,183 `BASELINE14`), `:559`(PILOT 배지) | A4 사용처 | 프리셋 선택, baseline14 고정 순서 체크리스트 | BaselinesCard는 현재 정책의 `console.baselines[]` + 서버 registry(`health.registry`, :149)로 구성 가능; 고정 14개 순서가 필요하면 preset protocol에서 추출 | `tests/generation_workflow.test.tsx`(Settings 렌더), `e2e/console.spec.ts:379-383` | client 옵션 제거, BaselinesCard를 정책·registry 기반으로 전환(`extra` 분기 :150,185-191는 단순화) |
| A8 | `web/src/lib/validate.ts:53-63 checkServerPath`, `:89-109 checkWeights`, `:111-122 checkPromotion`, `:125-157 checkRungs`, `:164-188 checkTraining`, `:190-202 checkFitnessAndConstraints`, `:213-254 checkSearch`, `:256-272 checkBaselines`, `:274-285 checkFinal`, `:288-317 validatePolicy` | 서버 validator(`srv/protocol/validator.py:340,372,388,394,403,409,416,423,455,490,566,584,809`)의 오류 코드와 "값이 있는데 틀린" 규칙의 클라이언트 미러(헤더 :2-15가 범위를 명시) | 사용자 입력 사전검사(서버가 권위, :5-6) | 대체 불필요(규칙 자체가 공개 계약: ValidateResponse.errors 설명에 코드 열거, `openapi.json x-protocol-notes`) | `tests/validate.test.ts`, `tests/qa_hardening.test.ts`, `e2e/console.spec.ts:373-377`, `e2e/hardening.spec.ts:451` | **유지 권고**(결정 D3). private 요소 없음: 임계값·가중치 값이 아니라 사용자 입력의 형식·범위만 본다. 단 `:161-162 TRAIN_PRECISIONS/INIT_MODES`, `:206 ARMS`는 서버 enum 복제 → 유지 가능 |
| A9 | `web/src/lib/lines.ts:104-116 selectRepresentative`, `web/src/screens/Monitor.tsx:100-106`("differs from client rule" 배지) | 대표 후보 선택 규칙(최고 fitness → 낮은 latency → candidate_id)의 클라이언트 재계산 | 서버 `Generation.representative_*`를 클라이언트 규칙과 대조 | `Generation.representative_candidate_id/evaluation_id/representative_rule`(계약에 규칙 문자열이 공개 기술됨) | `tests/cohort_lines.test.ts:117-131`(selectRepresentative 단위 테스트) | 결정 D4: 규칙은 계약 설명에 공개돼 있어 private는 아니나 "정책 재계산" 범주. 최소 변경은 배지(:102-106)만 제거하고 `selectRepresentative`는 선 그리기용으로 유지 |
| A10 | `web/src/lib/policy.ts:570-579 addRung`(:575 `+ceil(last/3)` epoch, :576 `score_ge ?? 0.5`, `ap_ge ?? 0.05`, :577 가중치 폴백 `SMOKE_WEIGHTS[0]`) | 편집기 편의 기본값 | rung 추가 시 초기값 | 없음(편집기 UX) | `tests/validate.test.ts:84-89` | `SMOKE_WEIGHTS` 삭제에 맞춰 폴백을 마지막 rung 가중치(이미 1순위) 또는 균등 0.25로 변경; 0.5/0.05 기본 임계값은 결정 D5 |
| A11 | `web/src/screens/Settings.tsx:372-373`(`['yolo11n','yolo26n']` 기본 families) | 생성 전용 모드 기본 모델 계열 | 입력 placeholder 성격 | `Health.capabilities.search_space`/registry에서 유도 가능 | 없음(확인: grep 0건) | 선택: 빈 값 + 안내문으로 변경 |
| A12 | `web/src/lib/evaluation.ts`, `lib/generation.ts`, `lib/cohort.ts`, `lib/finalretrain.ts`, `lib/runflow.ts`, `lib/structure.ts:130-134 ORIGIN_LABEL` | 표시·그룹핑 규칙 | 서버 값의 표시 | — | — | private 아님(판정 근거: 서버 행 값만 읽고 정책을 결정하지 않음). `ORIGIN_LABEL`의 "ETRI mixed operator pool/ETRI controlled hybrid" 문자열은 서버 `block-catalog`의 family 명을 축약하는 표시 레이블 |

보조 확인: `src/state/reducer.ts:65`는 `gate`를 "평가 후에도 변하는 필드"로만 취급(계산 없음). `src/screens/History.tsx:69,152-153`은 `candidate.gate` JSON을 그대로 표시(계산 없음). `src/screens/Monitor.tsx:256,350`은 `run.budget`(frozen budget/admission policy, 계약 설명 "Frozen budget per arm/seed … and admission policy")을 원문 JSON으로 표시 → §5 투영 결정과 연결.

## 3. 인벤토리 B — 하드코딩 경로·URL

| 위치 | 값 | 성격 | 처리 |
|---|---|---|---|
| `web/src/lib/policy.ts:231-235` | `/data/drone/NPS_YOLO/{data.yaml,train,valid,test}`, `/data/nas_console/runs` | handoff 원문의 NPS 예시 경로(서버 `handoff_protocol_20261006.json`과 동일) | A3 삭제로 소멸. 참고: 서버 `default_protocol()`은 이 경로를 `/root/detection/...`로 바꿔 보낸다(`presets.py:63-66`) |
| `web/src/lib/policy.ts:392`, `:417-421` | `/root/detection/MBNAS_data/pilot_visdrone_v1/{data.yaml,images/train,images/search_val,images/final_eval}`, `/root/detection/MBNAS_runs/pilot_smoke` | 소유자 호스트 절대경로(서버 `presets.py:38-46`과 동일) | A4 삭제로 소멸 |
| `web/vite.config.ts:16` | `http://127.0.0.1:8000` (dev proxy 기본, `MBNAS_DEV_API`로 변경) | 개발 전용, 번들 미포함 | DD-A7에 맞춰 기본값 `8710`(mbnas_server) 또는 환경변수 필수화 |
| `web/src/api/client.ts:73,88,114` | `localhost/127.0.0.1/[::1]` 루프백 허용 규칙, `http://localhost` 폴백 | 보안 규칙(https 강제), 호스트 정보 아님 | 유지 |
| `web/playwright.archive-real.config.ts:9` | 주석 `/data/mgjeong/etri_nas_runs`, VisDrone smoke run 언급 | 소유자 호스트 경로가 주석으로 노출 | 주석 삭제·중립화 |
| `web/playwright*.config.ts`, `README.md` | `127.0.0.1:{4173,5173,8000,8001}` | 로컬 테스트 포트 | 포트 재배치(8720 preview 등) 시 갱신 |
| `web/tests/validate.test.ts:20,24,26`, `web/tests/qa_hardening.test.ts:248` | `/root/detection/MBNAS_data/pilot_visdrone_v1/...`, `/data/drone/NPS_YOLO/...` | 테스트 입력값(번들 외) | 중립 경로(`/srv/...`)로 교체 권고 |
| `web/tests/fixtures.ts:168,203`, `tests/archive_fixtures.ts:358,627`, JSON 픽스처 4종 | `/srv/x/...`, `/synthetic/...`, `source_path: "ultralytics/nn/modules/conv.py"` 등 패키지 상대경로 | 합성·중립 | 문제 없음 |
| 전체 src/tests/e2e | `147.`, `192.168`, `mgjeong`, `etri_nas_runs`, `/home/` | **검색 결과 0건**(위 playwright 주석 제외) | — |

## 4. 번들 노출 점검 (sourcemap / debug / 픽스처)

- `web/vite.config.ts:20` `build: { sourcemap: false, target: 'es2022' }` — 소스맵 미생성. `define`·`import.meta.env` 사용 없음(src grep 0건), `console.log/debug/info`·`debugger` 0건.
- 진입: `web/index.html` → `/src/main.tsx` → `App`·`AppProvider`만 import. `src` 내 `tests/`·`e2e/` import 0건, `import.meta.glob`·동적 `import()` 0건 → 픽스처·mock이 번들로 들어갈 정적 경로 없음. 역방향(`tests/e2e → src`)은 27개 파일(§8).
- `tsconfig.json include`에 `tests`, `e2e`, playwright 설정이 포함되지만 `noEmit: true`(타입검사 전용)라 번들과 무관.
- 남는 노출 경로: (1) A3/A4 삭제 전까지는 handoff 원문과 소유자 호스트 경로가 번들 문자열로 포함된다(확정: `policy.ts`가 `context.tsx`에서 import됨). (2) JSON 픽스처(`tests/structure_native_server_fixture.json` 241 KB 등)는 실 CPU 빌드 문서(`tests/native_real_documents.test.tsx:5`)로, 번들 밖이지만 공개 저장소에 포함되면 ETRI hybrid 구조 문서가 그대로 공개된다 → 결정 D7.
- 확인 못 함: 실제 `vite build` 산출물의 문자열 검사(스냅샷에 dist 없음, 빌드 미실행).

## 5. `types.gen.ts`의 owner 성격 스키마·필드 (openapi 1.4.1 기준, 웹 사용 여부 포함)

범주: **P**=정책·검색 상태 내부, **H**=호스트 내부(경로·pid·루트), **A**=감사(audit)·이력. "웹 읽음"은 src가 실제로 접근하는 키다(투영 allowlist 설계 시 깨지지 않게 할 최소 집합).

| 스키마.필드 (`types.gen.ts` 줄) | 범주 | 웹 읽음 (file:line) | DD-A3 투영 제안 |
|---|---|---|---|
| `CandidateDetail.audit` (:1552-1553; 키: structure, structure_history[], generation{index,attempt,seed,…,worker_id}, training_source, performance) | A/P | `CandidateView.tsx:301`(audit.generation), `:303`(audit.performance.status), `:306`(audit.execution_configuration.sha256), `:195-237 AuditPanel`(config_estimate, built, search_space_variant, fallback_reason) | 공개 유지 키: structure, performance.status, generation{index,attempt,seed,requested_method,effective_method,duplicate_scope,execution}, execution_configuration.sha256, config_estimate/built 요약. `worker_id`·`structure_history`는 owner 전용 후보 |
| `CandidateDetail.lineage.{crossover,mutations,repairs,template,template_detail}` (:1530-1535) | P | `operation`만 읽음(`CandidateView.tsx:327-328`) | `operation`·`template_detail`만 공개, `crossover/mutations/repairs` 객체(교차·변이·repair 내부 기록)는 owner |
| `CandidateDetail.gate_history`, `.checkpoints`, `.parent_evaluation_ids` (:1528,1550) | A/H | `checkpoints`는 `TrainingProgress.tsx:22-23,57-58`(kind=training_full의 `path`) | checkpoints는 재개 UI에 필요(경로 대신 artifact id/digest로 투영, DD-A6) |
| `CandidateEvaluation.gate` (:1231-1232) | P(결정 결과)·사용자 임계값 | `CandidateView.tsx:340,494`(GateChecks: eligible/reason_code/checks[name,passed,value,threshold,op]), `History.tsx:69,153` | 공개 가능(threshold는 사용자 protocol 값) |
| `CandidateEvaluation.cost`, `.raw_metrics`, `.rewards`, `.fitness_weights` (:1265-1272) | 계산 결과 | `CandidateView.tsx:330,341-342,476,478,484` | 공개 |
| `CandidateEvaluation.job_id/attempt/revision/architecture_path/checkpoint_path/checkpoint_sha256` (:1203-1204) | H | `CandidateView.tsx:397-398,447-449` | 경로는 고객 호스트 경로이면 공개 무방; owner 호스트 실행이면 opaque id로(WP-4) |
| `Run.budget` ("Frozen budget per arm/seed … admission policy", :1349-1350), `Run.cancel` (:1352) | P | `Monitor.tsx:256`(budget.generation_results), `:350`(JSON 전체) | 공개 DTO에는 generation_results와 예산 합계만; admission policy 세부는 owner |
| `Run.costs.*` (profile/overshoot 등) | 계산 결과 | `Monitor.tsx` costs 카드(`tests/components.test.tsx:233-239`) | 공개 |
| `Worker.pid/lease_expires_at/device_uuid/job_id` (:1389-1397) | H | 웹은 `power_w/sm_clock_mhz`(`Monitor.tsx:182-183`) 등만; pid 등 미사용(확인) | pid/lease는 owner |
| `Health.allowlists.{dataset_roots,output_roots,archive_roots}` (:1434-1438) | H | `Settings.tsx:140`(dataset/output roots 표시) | 고객 호스트 경로이면 공개; owner 호스트는 미노출 |
| `Health.capabilities.search_space{variant,root,reason,…}` (:1426), `ValidateResponse.capabilities.search_space.etri_root` (:1460), `Protocol.console.search.search_space_resolved.etri_root` (:1061-1064) | H | `Capabilities.tsx:26-31`(variant, reason만), `Server.tsx:180`(validate capabilities JSON 전체 표시) | `root/etri_root` 제거. 주의: resolved protocol 안의 `search_space_resolved.etri_root`는 편집기로 들어와(`context.tsx:601`) "정책 JSON 내보내기"(`Settings.tsx:70-79`)로 파일에 남는다 |
| `RegistryEntry.evidence.source{notebook,notebook_blob_sha1,etri_audited_head,…}`, `registry_roots_skipped[{path}]` (:1571-1572,1627,1654) | H/A | `Server.tsx:180`(capabilities만), evidence 미표시(확인) | owner 전용 |
| `StructureReport.observation.{fx_basis,source_blob_sha1}`, `parser_source_sha256`, `blocks[].native_config`, `effective.inputs[].source_path` (:2647,2657,2756) | A | `ModelStructure.tsx:259`(native path), 블록 표 | 모듈 경로는 패키지 상대 경로(픽스처 확인) → 공개 가능; blob sha는 감사용 |
| `GeneratedCandidate.duplicate_scope/execution` (:2515-2518) | P | `CandidateView.tsx:377` | 공개(값이 정책 선택 결과 이름뿐) |
| `Archive*` — `ArchiveRoot.path`, `ArchiveDriver.{path,pipeline_log_tail}`, `ArchiveProvenance.{cfg_path,yaml_path,jsonl_path}`, `ArchiveCheckpoint.path`, `ArchiveRun.path`, `ArchiveRunningRun.{nb_pid_alive,kernel_pid_alive}`, `ArchiveGpu`, `ArchiveCandLineage.mutation`, `ArchiveLineage.mutation` (:1646,1802-1831,2110-2113,2301-2304,2330-2349) | H/P | 아카이브 화면 전반(`components/archive/*`); mutation은 `ArchiveCandidateModal.tsx:207`, `CandidateTable.tsx:31`(mode) | 아카이브는 DD-A1(e)에서 공개 read-only. 고객 호스트 자기 파일이면 경로 공개 무방; **소유자 캠페인 아카이브를 서비스할 경우** path/pid/mutation 상세는 owner 전용으로 분리(결정 D6) |

## 6. `gen-types.mjs`와 계약 위치

- `web/scripts/gen-types.mjs:15`: `const CONTRACT = process.env.MBNAS_OPENAPI ?? resolve(root, '../MBNAS_server/contracts/openapi.json')` — 형제 체크아웃 가정(주석 :14). `--check` 모드는 임시 디렉터리에 생성 후 바이트 비교(:24-45). `package.json`의 `build`는 `tsc && vite build`만 실행하고 `check:types`는 포함하지 않는다.
- 대상 상태: `types.gen.ts`는 `openapi.json 1.4.1`과 paths 34/34·schemas 96/96 동일 집합(Python 집합 비교; 바이트 동일성은 openapi-typescript 미실행으로 확인 못 함).
- 계약 공백: `client.ts:277-280 blockCatalog()`가 `GET /api/v1/structure/block-catalog`를 호출하나 openapi 1.4.1에 경로가 없다(`grep -c block-catalog openapi.json` = 0). 서버에는 라우트가 있고(`srv/api/routes_registry_structure.py:76-78`, `..search.block_catalog` import) `contracts/history/migrate_1.4.1_to_1.4.2.py`가 BlockCatalog* 컴포넌트와 경로를 추가하도록 쓰여 있으나 `EXPECTED_1_4_2_SHA256 = '__FILL__'`로 미적용, `HISTORY.json` 마지막 항목은 1.4.1. 웹 타입은 `lib/structure.ts:39-45`에 수기.
- 제안: (1) 기본 경로를 `mbnas_contracts` 패키지 산출물로 변경 — 모노레포라면 `resolve(root, '../mbnas_contracts/openapi.json')`, 배포 분리라면 `MBNAS_OPENAPI` 필수 + 실패 시 exit 2 유지(:19-22). (2) 1.4.2 migration을 mbnas_contracts에서 적용해 block-catalog를 계약에 넣고 `structure.ts:39-45` 수기 타입을 생성 타입으로 교체. (3) `check:types`를 `build` 또는 CI 전단계로 승격. (4) `--empty-objects-unknown`(:27)과 openapi-typescript `^7.13.0` 핀 유지. (5) 투영(WP-4) 후 공개 openapi가 바뀌면 `types.gen.ts` 재생성이 필요하므로 §5의 "웹 읽음" 키가 공개 스키마에 남아 있어야 typecheck가 통과한다(`types.ts:16,31`은 `& {…}` 보강으로 일부 필드를 손으로 덧붙이고 있음).

## 7. 화면 ↔ `/api/v1` 엔드포인트

클라이언트 메서드 정의는 모두 `web/src/api/client.ts`(:237-388). 화면별 호출 지점:

| 화면 (`src/screens`) | 호출 엔드포인트 (메서드, 호출 지점) |
|---|---|
| Archive (`Archive.tsx`, `components/archive/*`) | `GET /archive/runs`(`context.tsx:358`), `GET /archive/runs/{id}`·`/baselines`·`/generations`(`context.tsx:384-386`), `GET /archive/runs/{id}/candidates?limit&offset` 페이지 순회(`archive/api.ts:53`), `GET /archive/live`(`context.tsx:401`), `GET /archive/runs/{id}/generations/{g}`(`GenerationDetail.tsx:29`), `GET /archive/runs/{id}/candidates/{cid}`(`ArchiveCandidateModal.tsx:36`), `GET /structure/block-catalog`(`ArchiveCandidateModal.tsx:89`), `GET /archive/runs/{id}/fidelity`·`/confirm`(`FidelityCard.tsx:12,79`), `GET /archive/runs/{id}/files/{kind}`(`PlotsCard.tsx:24` → `ArtifactImage` → `lib/artifacts.ts:64` bearer fetch) |
| Compare (`Compare.tsx`) | `GET /archive/compare?runs=`(`Compare.tsx:75`), 목록은 Archive와 공유 |
| Monitor (`Monitor.tsx`) | `GET /runs/{id}/snapshot` + `GET /runs/{id}/events?after_seq` SSE(`context.tsx:588,591` → `api/sync.ts`), 후보 상세 `GET /runs/{id}/candidates/{cid}`(`TrainingProgress.tsx:57`, `CandidateView.tsx:323`, `ModelComparison.tsx:21`), `POST /runs/{id}/candidates/{cid}/structure-check`(`CandidateView.tsx:322`), 진단 이미지 `GET {artifact.url}`·`POST /runs/{id}/artifacts/{aid}/refresh`(`lib/artifacts.ts:64,84`), 모달 내 TrainingPanel: `GET /protocols/presets`(`TrainingPanel.tsx:92`), `POST /training-runs`(`:80`), RegistryStructureButton: `GET /registry/{model}/structure`, `POST /registry/{model}/structure-check`, `POST /structure-check`(`RegistryStructureButton.tsx:46,65,66`) |
| Settings (`Settings.tsx`) | `GET /protocols/presets`(`context.tsx:336`), `GET /health`의 registry 상태(`Settings.tsx:149`)·allowlists(`:140`), 클라이언트 사전검사만(서버 Validate는 Server 화면 버튼), TrainingPanel(위와 동일) |
| Lineage (`Lineage.tsx`) | `CandidateView` 재사용(`Lineage.tsx:80`) → Monitor 모달과 동일 엔드포인트 |
| History (`History.tsx`) | `GET /runs?limit=`(`context.tsx:347`), 로드된 run의 snapshot/events |
| Server (`Server.tsx`) | `GET /health`(`context.tsx:325`), `GET /protocols/presets`(:336, `Server.tsx:97-118` 표 + "Use in editor"), `GET /runs`(:347), `POST /protocols/validate`(:556), `POST /runs`(`lib/runflow.ts:93`), `GET /runs/{id}/snapshot`+events, `POST /runs/{id}/{pause,resume,cancel}`(`context.tsx:679`), `GET /runs/{id}/export`(:695), `POST /runs/{id}/final-retrain`(`lib/runflow.ts:106`, 폼 `Server.tsx:292-340`) |

미사용 클라이언트 메서드(확인): `getRun`(`client.ts:291`), `registry`(`:245`). 계약에 있으나 웹이 쓰지 않는 경로: `GET /runs/{id}`, `GET /registry`, `GET /runs/{id}/artifacts/{aid}`(artifact.url로 간접 사용).

## 8. 테스트·e2e 영향 매트릭스

| 변경 | 직접 깨지는 파일:줄 | 조치 |
|---|---|---|
| A1/A2 `computeFitness` 제거 | `tests/fitness.test.ts`(전체, 62줄); `tests/fixtures.ts:7,67-70`; `e2e/weights.spec.ts:11,46-47`; `tests/components.test.tsx:263` | 공식을 `tests/fitness_oracle.ts`(테스트 전용)로 이동해 fixtures·weights.spec이 import; `fitness.test.ts`는 삭제(서버 측 `application/MBNAS_server/tests/unit/test_protocol_fitness.py`가 존재 — 파일 존재만 확인, 내용은 미대조) 또는 오라클 자체 테스트로 축소; `components.test.tsx:263`은 `/Fitness \(server\)/`로 교체 |
| A3/A4 `policy.ts` 템플릿 제거 | `tests/preset_parity.test.ts`(전체; `:12` 상대경로 `../../MBNAS_server/config/...`도 분리 후 무효); `tests/validate.test.ts:23-27,32-34,37-69,74-93`; `tests/training_panel.test.tsx:36,62,66-67,71,84,126`; `tests/generation_workflow.test.tsx:22,112,128-129,133-137` | preset parity는 서버 단위 테스트(`MBNAS_server/tests/unit/test_protocol_presets.py:86-88`)가 이미 담당 → 삭제. 나머지는 `tests/fixtures.ts`에 테스트 전용 protocol 픽스처(`fixtureRun().resolved_protocol` 확장: rungs 10/30/90, `console.baseline_epoch_target: 90`, `training.optimizer: 'AdamW'`)를 두고 교체(기대값 90·AdamW는 handoff 값에서 옴) |
| A5 `CLIENT_TEMPLATES`·초기 정책 제거 | `e2e/console.spec.ts:363-384`(`+10/+20/+60`, `90`, 'PILOT_SMOKE (client template)', `rung-2-epochs=6`); `e2e/weights.spec.ts:68,83,147-148`; `e2e/hardening.spec.ts:447-453`; `e2e/generation.spec.ts:95-97`; `tests/generation_workflow.test.tsx:32-50` | mock `fixturePresets()`(`tests/fixtures.ts:209-213`, 현재 rung 1개짜리 PILOT_SMOKE만)를 `DEFAULT_BASELINE14`(3 rung 10/30/90)+`PILOT_SMOKE`(2/4/6) 두 개로 확장하고, 각 spec이 연결 → preset 적용 후 Settings를 연다. `e2e/mock.ts:107`은 이미 `/protocols/presets`를 서빙 |
| A7 BaselinesCard 전환 | `e2e/console.spec.ts`(설정 스크린샷만), testid `baseline-<id>`(`Settings.tsx:175`)는 유지 가능 | 변경 없음(testid 유지 시) |
| gen-types 경로 변경 | `npm run check:types` | 새 기본 경로 또는 `MBNAS_OPENAPI` |
| 포트 8000→8710 | `playwright*.config.ts`(webServer는 vite preview만), README | 문서·환경변수 |
| 변경 불필요 | `e2e/real-server.spec.ts:115-116`(이미 서버 preset "Use in editor" 사용), `tests/app.test.tsx`(초기 정책 내용 비의존, 단 `:96-100` Validate 전에 정책이 비어 있으면 mock validate가 빈 protocol을 받음 → mock은 수용) | — |

## 9. 공개 웹 변경 목록 (위험도 순, DD-A8 최소 변경)

1. **R1 (낮은 위험·즉시)** `lib/fitness.ts`에서 `computeFitness/boundedReward/metricInvalidReason/FitnessMetrics/FitnessResult` 삭제, `CandidateView.tsx:12,330-339,479` 제거. 남는 export: `WEIGHT_TOLERANCE, weightsSum, weightsSumOk, FitnessWeights, FitnessAnchors`(타입). 테스트: `tests/fitness_oracle.ts` 신설(픽스처용), `fitness.test.ts` 삭제, `components.test.tsx:263`·`e2e/weights.spec.ts:46,99` 수정.
2. **R2 (중간 위험)** `lib/policy.ts`에서 `HANDOFF_JSON, handoff, BASELINE14, defaultPolicy, SMOKE_WEIGHTS, PILOT_ROOT, pilotSmokePolicy` 삭제; `addRung:577` 폴백을 균등 가중치(또는 마지막 rung 복제만)로. `context.tsx:19,103-114,228-229,531-535`에서 템플릿 제거, 초기 정책 빈 객체, `presetLabel` 초기값 "no policy loaded". `TrainingPanel.tsx:4,121` 버튼 제거. `Settings.tsx:13,56,105,147-195` 서버 preset 전용으로. 번들에서 handoff 원문·`/root/detection`·`/data/drone` 문자열이 모두 사라지는지 `grep -r` 로 검증(수용 기준).
3. **R3 (중간 위험·UX)** 연결 성공 시 `loadPresets()` 자동 호출(`context.tsx:320-329 connect` 직후) 및 결정 D1에 따라 `DEFAULT_BASELINE14` 자동 적용. 연결 전 Settings는 "서버 프리셋을 불러오세요" 배너 + rung 표 비활성(`rungsOf`가 빈 배열이면 이미 빈 표). e2e/mock 픽스처 확장(§8).
4. **R4 (낮은 위험)** 호스트 경로 정리: `playwright.archive-real.config.ts:9` 주석, `tests/validate.test.ts:20,24,26`, `tests/qa_hardening.test.ts:248`을 중립 경로로. `vite.config.ts:16` 기본 포트를 DD-A7(8710)로.
5. **R5 (낮은 위험·계약)** `scripts/gen-types.mjs:15` 기본 경로 → mbnas_contracts; `check:types`를 build 전단계에; 1.4.2(block-catalog) 적용 후 `lib/structure.ts:39-45` 수기 타입 제거; `types.ts:16,31`의 `& {…}` 수기 보강을 공개 스키마에 반영.
6. **R6 (결정 후)** `Monitor.tsx:100-106` 배지 제거(D4), `addRung` 기본 임계값(D5), validate.ts 유지(D3).
7. **R7 (WP-4 입력)** §5 "웹 읽음" 키 집합을 공개 DTO allowlist의 하한으로 전달; `Server.tsx:180`(validate capabilities JSON 전체)·`Monitor.tsx:350`(budget JSON 전체)·`History.tsx:153`(gate JSON 전체)은 원문 덤프라서 투영에서 빠진 키는 자동으로 사라진다(깨지지 않음, `rec()`로 방어).
8. **R8 (저장소 위생)** 테스트 JSON 픽스처(§4, D7)와 `evidence/`(README가 추적 폴더로 언급, 스냅샷에는 없음 — 확인 못 함) 공개 여부.

## 10. DD 충돌·보완 플래그 (재론 아님, 근거 제시)

- **DD-A8 ↔ 서버 preset 내용**: 대체 데이터인 `GET /protocols/presets`의 `DEFAULT_BASELINE14.protocol.console.paths`는 `presets.py:38-46,65`의 소유자 호스트 절대경로(`/root/detection/...`)를 "예시"로 보낸다. 웹에서 임베드를 지워도 같은 문자열이 응답으로 고객 브라우저에 도달한다 → core/contracts 측에서 preset 경로를 설정값(또는 `/path/to` placeholder)으로 바꿔야 DD-A8의 취지가 완성된다. 웹 사전검사는 `/path/to`를 `PATH_PLACEHOLDER`로 막으므로(`validate.ts:57`) placeholder 방식과 호환된다.
- **DD-A3 투영 ↔ 편집기 내보내기**: resolved protocol이 `search_space_resolved.etri_root`(호스트 경로)를 포함하면 `context.tsx:601`로 편집기에 들어와 `Settings.tsx:70-79` 파일로 나간다. 투영에서 `etri_root`를 빼거나 디렉터리 대신 해시만 남기는 것이 맞다(`openapi x-contract-changelog 1.2.3`: 검색 공간 해시 정의가 이미 "실행된 ETRI 모듈 내용"이므로 경로 없이도 식별 가능).
- **DD-A1(e) 아카이브 공개 ↔ 아카이브 DTO의 경로·pid**: §5 Archive* 행. 고객 자기 호스트 파일이면 무해하나, 소유자 캠페인 아카이브(VisDrone/NPS 실행 기록)를 같은 API로 노출할 계획이면 분리 필요(D6).
- **계약 드리프트**: `client.ts:277` 주석과 미적용 1.4.2 migration이 일치 — 웹은 계약 밖 엔드포인트를 1개 사용 중. mbnas_contracts 초기 버전에서 해결하지 않으면 `check:types` 체계가 이 경로를 영원히 놓친다.

## 11. 결정 필요 사항

- **D1 초기 편집기 상태**: 연결 전 빈 정책 + 연결 후 `DEFAULT_BASELINE14` 자동 적용(권고: 자동 적용하되 `presetLabel`에 "server preset DEFAULT_BASELINE14 (design example)" 명시) vs 사용자가 명시적으로 선택할 때까지 비움. e2e 다수가 영향.
- **D2 픽스처 내 handoff 값 허용 범위**: 테스트 전용 protocol 픽스처에 10/30/90·가중치 0.2/0.1/0.4/0.3 등 handoff 예시 값을 그대로 둘지(값은 `DESIGN_EXAMPLE`, 논문 값 아님), 아니면 완전히 합성 값(예: 2/4/6, 균등 가중치)으로 바꿀지. 후자면 `training_panel.test.tsx:66-67`·`generation_workflow.test.tsx:128`의 기대값(90, AdamW)도 바뀐다.
- **D3 `validate.ts` 유지 여부**: 권고 유지(공개 계약의 오류 코드 재현, 정책 값 없음). 대안은 서버 Validate만 쓰고 클라이언트 사전검사 삭제(`e2e/console.spec.ts:373-377`, `hardening.spec.ts:451` 등 다수 수정).
- **D4 대표 후보 규칙 재계산(`lines.ts:104-116`, `Monitor.tsx:100-106`)**: 배지만 제거(권고) vs 함수까지 제거(선 그리기 로직 `representativeLines`는 서버 representative id만 사용하므로 함수 제거도 가능하나 `tests/cohort_lines.test.ts:117-131` 삭제 필요).
- **D5 `addRung` 기본 임계값 0.5/0.05**: 편집기 편의값으로 유지 vs 직전 rung 값만 복제하고 없으면 0으로.
- **D6 아카이브 DTO의 호스트 내부 필드**(ArchiveRoot.path, ArchiveDriver.path/pipeline_log_tail, ArchiveRunningRun pid 플래그, ArchiveCandLineage.mutation 상세): 공개 유지 vs owner 전용 분리. 웹 화면(`OverviewCard`, `LiveCard`, `ArchiveCandidateModal`)이 일부를 표시하므로 분리 시 화면 축소가 따른다.
- **D7 공개 저장소에 포함할 테스트 자산**: 실 CPU 빌드 문서 픽스처 4종(`tests/*_server_fixture.json`, `fixtures_audit_etri_mixed.json`; ETRI hybrid 구조·모듈 경로 포함)과 `evidence/` 스크린샷. 포함 시 ETRI 모델 구조가 공개 문서가 된다(DD-A9 권리 검토와 연결).
- **D8 `presets.py`의 소속 패키지와 응답 경로 정책**(§10 첫 항목): 웹은 응답 shape(`PresetList`)만 필요하므로 어느 패키지든 무방하나, 응답의 `console.paths`를 placeholder/설정값으로 바꾸는 결정은 웹 R2와 같은 릴리스에 묶여야 사용자가 빈 편집기 대신 즉시 유효한 템플릿을 받는다.

## 12. 확인 못 함

- vitest·playwright 실제 실행 결과(현재 통과 여부), `npm run check:types` 바이트 동일성(집합 비교만 수행), `vite build` 산출물의 문자열 검사.
- 라이브 원본에서 `MBNAS_web/evidence/`, `public/` 존재 여부(스냅샷 allowlist에 없음; 서버 `tests/unit/test_provenance.py:58`가 `MBNAS_web/public`을 입력으로 열거).
- 서버 `tests/unit/test_protocol_fitness.py`의 내용(웹 `fitness.test.ts`의 7개 케이스 — 클리핑, null, WEIGHTS_SUM_NOT_ONE, 1e-6 허용오차, 음수 가중치, ANCHORS_INVALID — 를 모두 덮는지)은 미대조 — R1에서 `fitness.test.ts` 삭제 전 확인 필요.
- `Worker.pid/lease_expires_at`가 Monitor 워커 표 외 다른 곳(History 이벤트 요약 등)에서 표시되는지(History.tsx 전체는 읽지 않음).

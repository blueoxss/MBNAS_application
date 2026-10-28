# WP-6B/6C test adaptations (web: mbnas_web/tests, mbnas_web/e2e)

작성: WP-6B+6C, 2026-10-27. 기준: WP-6_WEB.md §1 항목 3·6·7·8, DD-A19/A23/A39/A40/A42/A43(a)/A45, 계약 1.5.0
(live `openapi.json` sha256 `6459fd35…c783`, 15:48 승격본). 규칙: 단언 약화 없음, 조용한 skip 없음, 삭제 없음.
모든 경로는 `mbnas_web/` 기준.

- 변경 전 기준선(같은 세션 15:10, 계약 1.4.3 타입): vitest 39 files / 527 tests 통과, mock e2e 196(6A 보고값).
- 변경 후: vitest **44 files / 585 tests** 통과, mock e2e **204 passed**(desktop 100 + mobile 104), flaky 0.
  증가분 58 = 신규 53(아래 §2) + bundle_hygiene 신규 needle 4 + training_progress 신규 1.

## 1. 수정 (계약 1.5.0 반영 또는 DD-A43 분리; 단언 의미 유지·강화)

| 파일 | 변경 | 사유 |
|---|---|---|
| `tests/fixtures.ts` | `fitness_oracle` import 제거. 행의 fitness = 서버가 찍은 값: (a) 지표 범위 밖이면 null, (b) 정규 행 19개 지표 tuple은 `RECORDED_FITNESS`(oracle로 한 번 계산해 기록한 값, bit 단위 동일), (c) 그 외 임시 행은 id 기반 결정적 stand-in(수식 아님, `standInFitness`). `metricKey`/`inMetricDomain`/`stampedFitness`/`openObject` 추가 | DD-A43 (a): 이 파일을 import하던 mockapi.ts·e2e/mock.ts와 웹 테스트 85.7%가 공개 번들에서 빠지던 문제. 정규 행(`fixtureCandidates`, final 행, 기본 `cand()`, e2e mock의 happy/live 행)의 값은 이전과 **완전히 같다**(owner 전용 `fixtures_oracle.test.ts`가 bit 단위로 재검증). |
| `tests/fixtures.ts` `BLOCKS` | `old_name`·`before`·`after`·`repair_reason`·source_kind `repair` 제거, `operation`·`params`(YAML 행 module/from/repeats/args/scope) 추가; 4번째 행(이전 repair)은 수리 원천 `parent_b`로 보고 | 계약 1.5.0 `Block`(additionalProperties false)·DD-A19 D1: repair trace는 OWNER_ONLY, 공개 DTO에서 접힘. 행 수 4 유지(`BLOCKS` 길이 단언 그대로). |
| `tests/fixtures.ts` `cand()` | `architecture_path`/`checkpoint_path` 절대경로 → run 디렉터리 상대값(`architectures/…`, `checkpoints/…`) | 1.5.0 공개값은 절대 호스트 경로가 아니다(스키마 description). |
| `tests/fixtures.ts` Health/Validate | `capabilities`·`versions`를 `openObject()`로 전달 | 1.5.0 생성 타입이 열린 객체를 약한 타입으로 표현(TS excess check). 런타임 JSON·ajv 계약 검사 결과는 동일(`contract.test.ts` 통과). |
| `tests/mockapi.ts` | raw body 보존(`raw`), 선택 라우트 `onUpload`·`revisions`·`revisionBytes` 추가 | 업로드는 JSON이 아닌 원문 바이트. 기본 응답(404 NOT_FOUND)은 이전과 같아 기존 테스트 동작 불변. |
| `tests/detail_fixtures.ts` | GeneratedCandidate에 `training_status: 'UNTRAINED'`; detail `audit.generation.method`→`effective_method`, `audit.structure`에 `schema`; detail `parameters` 제거(구조 보고서의 `parameters`가 같은 값) | 1.5.0: `training_status` 필수, `audit`·`CandidateDetail` 닫힘. 화면의 파라미터 수는 `structure.parameters`에서 그대로 나온다. |
| `tests/generation_workflow.test.tsx:60` | 헬퍼 행에 `training_status: 'UNTRAINED'` | 1.5.0 필수 필드. 단언 불변. |
| `tests/wp6a_badges_workers.test.tsx` | "1.4.x 폴백" 테스트: 1.5.0 픽스처에서 `training_status`를 떼어 1.4.3 행을 만들고, 픽스처가 `UNTRAINED`를 보고함을 추가 단언. 주석 "oracle values"→"recorded server values" | 픽스처가 1.5.0 모양이 됨. 폴백 단언(UNTRAINED 유도, 다른 행 추측 금지) 그대로 + 강화. |
| `tests/components.test.tsx` 'modal shows lineage…' | `R (repair)`·`channel mismatch`·`Conv-5x5` 단언 → A/B/M 배지, 수리 행이 B로 보고됨(B 2개), YAML 행 문자열, operation `mutate`, 그리고 `R (repair)`·`Repair reason`·`old_name`·`Before`·`After`가 **없음** | 1.5.0/DD-A19: repair 정보는 OWNER_ONLY라 콘솔에 오지 않는다. 부재 단언을 추가해 강화(비공개 필드 표시 회귀 방지). |
| `tests/training_progress_checkpoint.test.tsx` | 최신 training_full 표시 = checkpoint id + sha256(`ck-training_full-2 · sha256 222222222222…`)이며 경로는 보이지 않음; 신규 1건: 경로 없는 1.5.0 행(id만)도 선택되고 1.4.x 행(경로만)은 경로를 표시 | 1.5.0 `checkpoints[]`에는 path가 없다(id·sha·artifact_id). 선택 규칙(최신 epoch, PRESENT/PRUNED 우선, MISSING 마지막) 단언은 그대로. |
| `tests/registry_structure_button.test.tsx` TrainingPanel 통합 | 사용자 모델에서 서버 경로 필드가 기본으로 **없음**을 단언 → "고급: 서버 경로" 토글 후 입력 | WP-6 §1-6: model_path는 고급 토글 뒤에만. POST 본문 단언(`{model_path}`) 그대로. |
| `tests/bundle_hygiene.test.ts` | REMOVED needle 4개 추가: `repair_reason`, `Repair reason`, `R (repair)`, `old_name` | 콘솔이 더는 표시하지 않는 OWNER_ONLY 어휘가 src로 돌아오면 실패(강화). |
| `e2e/console.spec.ts` 'crossover candidate…' | components.test와 같은 1.5.0 블록 표 단언(YAML 행 문자열, `R (repair)`·`Repair reason` 부재) | 같은 사유. |
| `e2e/mock.ts` | 라우트 추가: `POST /uploads/model-config`(raw body 저장, 422/413 코드), `POST /structure-check`(upload_id 확인, `source_sha256` 보고), `GET …/config-revisions`, `GET …/config-revisions/{id}/yaml`(X-Artifact-Sha256); `POST /training-runs` 응답을 1.5.0 `TrainingRunCreated` 모양(upload이면 `source_sha256`·`model_name`)으로 | 업로드·개정 흐름 e2e. 기존 기본 응답(개정 없음 = 404 NOT_FOUND)은 이전과 같다. |
| `e2e/generation.spec.ts` | 신규 테스트 1건(아래 §2) | — |
| `scripts/check-bundle.mjs` | EXTRA needle 6개 추가(위 4개 + `fitnessOracle`, `fitness_oracle`): 7 required + **21** extra | dist에 비공개 어휘·테스트 oracle이 들어가면 실패(DD-A40(d) 게이트 강화). |

변경 없이 통과: 나머지 모든 test·spec(`contract.test.ts`는 live 1.5.0 계약으로 그대로 통과 — 새 클라이언트 경로 3개
`/uploads/model-config`, `…/config-revisions`, `…/config-revisions/{id}/yaml`도 계약에 선언되어 있음).

### stand-in fitness의 영향 확인
공개 `cand()`가 기록되지 않은 지표 tuple로 만든 임시 행은 이제 oracle 값 대신 stand-in 값을 받는다. 이 값을 단언하는 공개
테스트는 없다: 수정 전후 기대값 변경 없이 vitest 585 / e2e 204가 통과했고, fitness 값을 비교하는 테스트는 모두 값을
명시하거나(`cohort_lines` 대표 규칙, `reducer`) 기록된 정규 행만 쓴다(`wp6a_badges_workers` 대표 배지). 지표와 일관된
fitness가 필요한 테스트는 owner 전용 `candWithOracle`(tests/fixtures_oracle.ts) 또는 oracle 직접 사용(`e2e/weights.spec.ts`).

## 2. 신규 (테스트 파일 6 + 픽스처 모듈 2)

| 파일 | 테스트 수 | 내용 |
|---|---|---|
| `tests/config_upload.test.tsx` | 12 | 사전검사(.yaml/.yml, 빈 파일, 1 MiB 초과는 요청 없음), 원문 바이트 raw body + `?filename=`(유니코드 이름) + `Content-Type: application/x-yaml`, upload_id·source_sha256·브라우저 sha256 일치 표시, 구조 검사 `{upload_id, model_name, input_size, class_count}` + 검사 바이트 sha256 대조(일치/불일치), 학습 요청 `source {kind:user, upload_id, model_name}`(model_path 없음, Idempotency-Key = body key) + 응답 source_sha256 표시, 고급 토글의 배타성, 서버 거부 4종(413/422×2/507)을 code·location으로 표시, sha 불일치 표시 |
| `tests/config_revisions.test.tsx` | 22 | lineDiff(동일, 변경=del+add 쌍과 행 번호, LCS 정렬, CRLF·마지막 줄바꿈 차이, 빈 쪽, 정렬 예산 초과 시 전체 교체), 라벨·기본 diff 쌍(selected↔executed)·파일 이름·UTF-8 판정·bytes verdict, "구성" 탭: 개정 표 열 전부, diff(양쪽 sha256 재계산·기록 대조, 변조 바이트 mismatch, ARTIFACT_NOT_READY 코드), 원문 바이트 다운로드(Blob = 저장 바이트, 파일 이름, verdict), 표시본 다운로드와 구분 표기, 404 NOT_FOUND/CANDIDATE_NOT_FOUND → 탭 숨김+안내, `model_tab=config` 폴백, 5xx → 오류+재시도 |
| `tests/lineage_generated.test.tsx` | 10 | Lineage 행 = 평가 행 + 생성 후보(중복 없음, 키 충돌 없음), 화면에서 생성 후보마다 UNTRAINED · UNEVALUATED·"미평가"·random(0 parents), 평가 행은 상태 배지만, 생성 후보 열기 → 생성 기록 표시; CandidateView의 artifact/checkpoint id + sha256 표시(경로 문자열 없음), id로 다운로드 + sha 대조, 실패 상태를 Error.code로(ARTIFACT_NOT_READY→NOT_READY, LOAD_FAILED→LOAD_FAILED, ACCESS_DENIED), `stateFromError` 표(알 수 없는 code만 status 폴백), 진단 이미지도 같은 규칙 |
| `tests/oracle_isolation.test.ts` (공개) | 4 | 패키저(scripts/dev/package_build.py)의 import 정규식·해석 규칙을 그대로 재현해 oracle importer closure를 계산: owner 트리에서는 정확히 {fitness_oracle.ts, fixtures_oracle.ts, fixtures_oracle.test.ts, e2e/weights.spec.ts}, 공개 번들(oracle 부재)에서는 어떤 파일도 oracle 모듈을 import하지 않음; 공유 픽스처 6종은 closure 밖; 공개에서 빠지는 테스트 파일은 owner 전용 2개뿐 |
| `tests/fixtures_oracle.test.ts` (owner 전용) | 5 | RECORDED_FITNESS 19개가 oracle 값과 bit 단위로 같음(키 왕복 포함), 정규 행 전부 oracle 값(stand-in 미사용), 표에 고아 값 없음, 범위 밖 입력은 공개 stamp·oracle 모두 null, `candWithOracle` |
| `e2e/config_upload.spec.ts` | 3 × 2 프로젝트 | ① 업로드(정확한 바이트 저장 확인)→구조 검사(upload_id, 검사 바이트 sha 일치)→학습 요청(upload_id, model_path 없음)→"구성" 탭 개정 표→diff(+2/−2, 양쪽 sha 일치)→다운로드(저장 파일 sha256 = 기록 source_sha256), axe serious/critical 0(2회) ② 개정 라우트 없는 게이트웨이: 탭 숨김+안내 ③ 업로드 거부: .txt는 전송 안 함, 서버 413 code 표시 |
| `e2e/generation.spec.ts` (신규 1) | 1 × 2 | Lineage에 생성 후보 3개가 UNTRAINED · UNEVALUATED·미평가로, 평가 행은 EVALUATED로; 생성 후보 열기 |
| `tests/config_fixtures.ts` (공개 픽스처) | — | 합성 YAML 원본/실행본, 개정 3개(original→selected(같은 바이트)→executed), 실제 sha256(node:crypto), 업로드 응답 |
| `tests/fixtures_oracle.ts` (owner 전용 픽스처) | — | `oracleFitness`, `candWithOracle` |

## 3. owner 전용으로 남는 파일 (DD-A43 a, 패키저 [deny-importers] closure)

`tests/fitness_oracle.ts`(변경 없음), `tests/fixtures_oracle.ts`, `tests/fixtures_oracle.test.ts`, `e2e/weights.spec.ts`(변경 없음).
이전: closure 47 파일, 테스트 49개 중 42개(85.7%) 제외. 이후: closure 4 파일, 테스트 55개 중 2개(3.6%) 제외
(`oracle_closure.txt`, 패키저 함수로 계산).

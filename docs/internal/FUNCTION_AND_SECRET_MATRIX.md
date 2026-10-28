# FUNCTION_AND_SECRET_MATRIX — 기능·비밀·배치 매트릭스 (v1, 2026-10-23 22:0x)

근거: `docs/internal/analysis/FUNCTION_BASELINE.md`(67 기능·증거 등급), `DTO_ALLOWLIST_PROPOSAL.{md,json}`(969 필드), `MODULE_MAP.{md,json}`(139 모듈), `SECURITY_BASELINE.md`, `ETRI_RUNTIME_SCOPE.md`, 결정 DD-A1~A25. 이 문서는 요약이며 행별 코드 위치·증거 포인터는 위 분석 문서의 기능 ID(A1…G14)를 따른다.

## 1. 보호 대상(비밀 클래스)과 위치

| 비밀 클래스 | 내용 | 코드 위치(분할 후) | 저장 | 고객 측 노출 규칙 |
|---|---|---|---|---|
| S-GATE 구조 gate 규칙·repair | `check_structure`의 shape/merge/head 규칙, `SearchSpace.illegal_reason/repair/alternatives`, slot 정의, TOKEN_BUDGET/DEPTH/WIDTH 선택지 | `mbnas_core.search.{structure,space}` | core 프로세스 메모리; DB `candidates.audit/lineage`(repair trace) | 결과(PASS/FAIL/UNVERIFIED, 진단 코드·위치)만 공개. repair_reason/before/after/old_name/slot_kind 비공개(DD-A19) |
| S-PRIOR 검색 prior·RNG | 템플릿별 prior 테이블, PRIOR_NUM_DRAWS, 스트림 키 유도식, prior draw 기록 | `mbnas_core.search.{space,streams,random,ga}`, `mixed_pool.seed_variants` | DB `jobs.spec_json/result_json`(describe), lineage.prior_draw | API 밖(jobs) 유지; lineage의 prior_draw/selection/crossover mask/mutation events/space·prior hash 비공개 |
| S-GA GA 연산자·부모 선택 | tournament/elitism/crossover/mutation 구현, parent pool | `mbnas_core.search.ga`, `engine._parent_pool` | DB | parent pool·search_state는 envelope/API 밖. `lineage.elite`·`parent_rows` 비공개 |
| S-PROMO fitness·승급·예산 | fitness 계산(가중치는 사용자 값), 승급 gate, admission 추정기, 종료 규칙 | `mbnas_core.protocol.{fitness,promotion}`, `scheduling.{admission,costs,planner}`, `orchestration.engine` | DB evaluations/cost_ledger | fitness **값**·gate 결정 코드·cap/billed/remaining 공개; estimate_s/estimate_source/reserved_s·budget.policy 비공개; runner는 계산하지 않음(DD-A15) |
| S-STATE 권위 상태 | runs/candidates/evaluations/jobs/generations/cost_ledger/checkpoints 원본 행 | `mbnas_core.db` | core SQLite | 공개 DTO 투영본만(additionalProperties:false) |
| S-CRED 자격·키 | 서비스 토큰 hash, ticket key, 서명키(gateway), 관리 토큰 | core DB `principals`, gateway env | — | 고객 배포에 core 키·DB 자격 없음(T8 canary) |
| P-OPERATOR 공개 연산자 | NASPatchMerge/NASWindowTransformer/NASGlobalTransformer, parse_model 확장, 어댑터 | `mbnas_model_runtime.operators`, `adapters` | — | 공개(고객 장비에서 실행 필수; DD-A2/A12). 비밀로 둘 수 없음을 보고 |
| P-RESULT 공개 결과 | 승인 모델의 gene/YAML/가중치, 측정값, 구조 검사 결과 | — | runner artifact store, core DB 행 | 공개(사용자 소유) |

## 2. 기능 → 상태·배치·공개 여부 (A6 기능 ID 기준)

| 그룹 | 기능 ID | 판정(원본 기준) | 분할 후 배치 | 공개/비공개 | 이번 WP | 비고 |
|---|---|---|---|---|---|---|
| A 설정·경로 | A1~A3 | 구현·실시험 확인 | core(validator)/runner(allowlist)/server(archive) 설정 3분할 | 설정 키 공개, 값은 배포별 | WP-1 | 호스트 고정 기본값 제거(DD-A13) |
| B 학습 실행 | B1,B2,B5,B6,B7 | 구현·실시험 확인 | core(selection/orchestration)+runner(handlers)+model_runtime(adapters) | 요청 DTO 공개 | WP-1/2 | B3(생성 후보 실학습), B4(pretrained)는 구현·통합 미확인 → T4/T7 |
| C 가중치·epoch | C1~C4 | 구현·실시험 확인 | core | 사용자 값 공개 | WP-1 | 웹 computeFitness 제거(WP-6) |
| D pause/resume/cancel | D1~D6 | 구현·실시험 확인 | core(state)+runner(stop 처리)+원격 제어 플래그 | 상태 코드 공개 | WP-2 | lease 회수 sweeper, fencing attempt 추가 |
| E 모니터링·비교 | E1~E9 | 구현·실시험 확인 | core(bus/planner)+server(SSE relay, archive)+web | 투영 후 공개 | WP-3/4 | E10 조건 비교(콘솔)·E11 로그 tail = **누락, 후속 필수** |
| F 후보 상세 | F1,F2,F3,F6~F9,F12,F13,F15,F16 | 구현·실시험 확인 | core 문서 조립 + model_runtime 관측 + web | 투영 후 공개 | WP-4/6 | 구조 검사 sandbox(WP-3) |
| F 카탈로그 | F4 | 부분(계약 1.4.2 미반영) | model_runtime.block_catalog | 공개 | WP-1(1.4.2 계승) | delta 스냅샷으로 계약 반영됨 |
| F 구성 YAML | F5a 업로드, F5c 원본 sha, F5d 개정 이력, F5e diff, F5g export 바이트 | 누락/부분 | server(업로드·store)+core(revision 행)+web | 공개 | WP-3/5/6 | DD-A23 |
| F 캠페인 아카이브 | F11 | 부분(app 배선 누락) | server.archive | 공개(경로 치환) | WP-1F | DD-A23 |
| F Lineage 생성 후보 | F14 | 계획 | web | 공개 | WP-6 | DD-A23 |
| G 콘솔 유지 기능 | G1~G8, G10~G14 | 구현·실시험 확인 | 각 패키지 | — | WP-1~7 | G9 benchmark는 현재 코드 GPU 증거 없음 |
| 신규 | 원격 큐(claim/heartbeat/progress/artifact/result/ack), principals/ticket, envelope, sweeper | 없음 | core.runner_api + runner.daemon/client/journal | 프로토콜 계약 공개, 구현 core 비공개 | WP-2 | A3 설계 |
| 신규 | gateway `/api/v1` relay, 공개 투영, SSE relay, 업로드, artifact store/relay | 없음 | server + core.console 투영 | 공개 | WP-3/4/5 | A2 allowlist |
| 신규 | 설치/doctor/start/stop/verify/package, ZIP 3종, allowlist staging, canary | 없음 | scripts/, build/ | — | WP-7/8 | §10~§12 |

## 3. 증거 등급 정책
- 원본 문서의 CUDA(pc2300 2026-10-06~08)·WP-G(18c4815) 증거는 "원본 보고값"으로만 인용한다. 본 프로젝트의 판정은 TARGET_ROOT에서 실행한 결과만 사용한다(T1~T8 집계는 mock/CPU/GPU/배포로 분리).
- GPU 경로(T7)는 사용자 승인 창 전까지 BLOCKED_BY_ENV.

## 4. 공개 경계 요약(배포물 기준)
- public-runtime ZIP: `mbnas_web`(dist 포함), `mbnas_server`, `mbnas_runner`, `mbnas_model_runtime`, `mbnas_contracts`, `config/examples`, `examples/`, `tests/public`, `docs/public`, `deploy/public`, scripts(공개 프로필). **core 연결 필수.**
- owner-private ZIP: 위 + `mbnas_core`, `tests/owner`, `deploy/owner`, `docs/internal`(선별), provenance.
- registration-runtime ZIP: public-runtime + `docs/registration`, 패키지 식별자 유지.
- 어떤 공개 산출물에도: core 소스·reference backend·정책 fixture·private wheel·감사자료·관리 키·host 사실 문자열·canary 없음(T8).

# IMPLEMENTATION_PLAN v1 (2026-10-23 22:0x) — WP-0 완료 후 확정

순서(지침 §12): 최소 수직 경로 → 기존 queue/lease 원격화 → 학습·평가·재개·artifact → Web 상세 → ZIP. 각 WP는 `WORK_PACKAGES/WP-n_*.md`의 계약(입력·소유 파일·수용조건·증거 위치)을 따른다. 구현은 하위 모델(opus/sonnet), 수용 판정은 fable이 실제 출력·exit code를 읽고 내린다.

| WP | 내용 | 산출/수용 | 상태 |
|---|---|---|---|
| WP-0 분석 | 모듈 분류·DTO allowlist·원격 프로토콜·웹 private·테스트·기능 기준선·ETRI 범위·보안 기준선 | `docs/internal/analysis/*` 13 파일, DECISIONS DD-A10~A25, FUNCTION_AND_SECRET_MATRIX v1 | 완료(2026-10-23 21:5x) |
| WP-1 분할 | 스냅샷(+delta 1.4.2) → 5 패키지+web 배치, import 재작성(접두사+심볼 이동), 설정 3분할·runtime/ 기본값, ETRI vendoring(Option A, exec 없는 parse_model), runner fitness 제거, baseline arch 주입, 테스트 재배치(owner/public/_shared), 과도기 owner all-in-one, import·금지문자열 검사기 | 격리 venv에 5패키지 editable 설치 성공; import 검사 0 위반(과도기 허용 제외); owner 게이트·public 스위트 결과 기록(T1 기준선 = 재배치 직후 수치); parity 테스트·repair seed 116 게이트 통과; MIXED_SPACE_HASH_MAP | 계약 작성 → 워크플로 기동 |
| WP-2 원격 큐 | core `/core/v1/runner/*`(E1~E10), principals/ticket, envelope 빌더(child.load_context 이식), sweeper, fencing attempt, runner daemon/client/journal, 데이터 경로 재매핑, owner worker를 PROPOSE_GENERATION 전용으로, 과도기 허용 제거 | T3 매트릭스 37건 중 구현 범위 통과; 분산 실패 시나리오 테스트; 실제 2프로세스(core+runner) CPU 학습 1회 | 대기 |
| WP-3 gateway·콘솔 보안 | `mbnas_server` `/api/v1` relay(core client, 서비스 토큰), SSE relay, 오류 코드 relay, rate limit, 업로드 엔드포인트, 구조 검사 sandbox(core), YAML allowlist/인자 스키마, 하네스 (가) core+gateway 결합 | 웹 e2e(mock)·live 경로 통과; 음성 테스트(R5/R7/R13) | 대기 |
| WP-4 투영·계약 1.5.0 | 공개 DTO allowlist(additionalProperties:false), owner DTO 분리, SSE/export 투영, runner/device id, training_status, config_revisions·upload DTO, openapi 1.5.0 + migrate + TS 재생 | T8 canary 공개 응답 0 히트; 웹 contract 테스트 | 대기 |
| WP-5 artifact·재개 | artifact 등록/digest/store/relay/다운로드, checkpoint affinity·전송 후 재개, retention prune 왕복, export YAML 바이트, 캠페인 어댑터 완성 | T4/T5 다운로드·재개 | 대기 |
| WP-6 웹 | DD-A8/A21(fitness·템플릿 제거, preset 자동 적용), 상세 3요소 유지, UNTRAINED 배지, 업로드/개정/diff UI, Lineage 생성 후보, 포트/경로, production asset | vitest/typecheck/build/e2e | 대기 |
| WP-7 운영·패키징 | scripts/install·doctor·start·stop·verify·package(--profile/--suite), config/examples, deploy/public·owner, docs/public·registration, THIRD_PARTY_NOTICES, allowlist staging, ZIP 3종+sha256, BUILD_INFO/PACKAGE_PROFILE | T6 재배치(새 경로·공백·외부 cwd), 격리(bwrap/docker) 설치·실행 | 대기 |
| WP-8 검증·평가 | T1~T8 집계, 12 기준셋 루프(REVIEW_LOG), VALIDATION_REPORT, ACCEPTANCE_MATRIX 갱신 | 상태별 판정 | 대기 |

후속 필수 과제(이번 라운드 완료 주장 금지): E10 콘솔 run 후보 조건 포함 비교 화면, E11 로그 tail, Option B ETRI mutation 재현, mTLS/TEE/난독화(ADR), OFFLINE_INSTALL(npm 오프라인 캐시), ADMIN_BOUNDARY(별도 host), GPU_PATH(사용자 창).

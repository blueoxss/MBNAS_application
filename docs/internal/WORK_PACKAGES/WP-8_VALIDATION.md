# WP-8 검증·평가 계약 v1 (설계자 claude-fable-5-1, 2026-10-23 22:5x)

## 1. 시험 집계(T1~T8, 지침 §12) — 결과는 `docs/internal/VALIDATION_REPORT.md`와 `evidence/T*/`
| 시험 | 환경 | 실행 주체 | 증거 |
|---|---|---|---|
| T1 기준선 | runtime/venv | WP-1V | pytest owner/public 결과, skip 사유, 원본 보고값과의 대조표(원본 수치는 '보고값'으로만) |
| T2 core 동등성 | owner venv | WP-8 | 고정 입력(seed 116 repair, GA gen0/gen1 proposals, promotion 결정, admission) reference 대조 — reference = 스냅샷 원본 코드를 격리 서브프로세스에서 실행(owner 전용) |
| T3 분산·보안 | owner venv | WP-2/3 | T3 매트릭스 37 + 음성 테스트 |
| T4 owner 독립 실행 | bwrap(원본 미마운트) 또는 docker | WP-8 | owner ZIP 압축 해제본 설치 → core+owner worker+gateway+runner+web → 유효/무효 구조 검사, 후보 생성(OFF, CPU), 합성 데이터 CPU 학습(1 epoch→pause→resume)·최소 1 단계 결정·다운로드 |
| T5 public 독립 실행 | docker(python:3.10-slim + node 22) 또는 bwrap, host 네트워크로 core 접근 | WP-8 | public ZIP만 설치 → 호스트의 owner core(별도 프로세스·DB·포트)에 연결 → 업로드→검사→생성→승인 YAML→runner 실행→결과→Web 조회/다운로드; core 종료 시 명시 실패 |
| T6 ZIP 재배치 | 새 경로(공백 포함)·외부 cwd | WP-7V | 3 프로필 각각 install→doctor→start→API→verify→stop |
| T7 GPU | 사용자 승인 창 | — | BLOCKED_BY_ENV(현재) |
| T8 누출 | 빌드 산출물·응답·로그·프로세스 | WP-7D | canary scan + import closure + 분리 실행 근거 |

## 2. 12 기준셋 루프(지침 §14; 기준은 `docs/internal/criteria/C01~C12.json`에 고정)
- 순서 C01→C12. 각 라운드: 설계자(fable)가 증거를 직접 열람·재실행하여 4기준 채점(25점) → 90 초과·Blocker/Critical 0·필수 시험 통과 시 다음 셋; 미달 시 결함을 WP에 배정(하위 모델 수정) → 관련 검증 재실행 → 재채점. 이전 셋의 최종 수정본이 다음 셋의 시작점.
- REVIEW_LOG.jsonl 필드: set_id, round_id, at, requested_model, resolved_model, mode, verification_level, inputs(hash), scores{4}, total, defects[{severity, file, summary}], fixed_files, evidence[], residual_risk, next, instruction_quality_eval, implementation_acceptance, env_boundary_check.
- 막힌 셋(장비·외부 결정)은 blocker·최소 필요사항을 기록하고 통과로 건너뛰지 않는다(C11 GPU 항목은 BLOCKED_BY_ENV 표기 + 점수 0 → 셋 미통과 상태 유지; 다른 셋 작업은 계속).
- 마지막 변경 후 영향 셋·최종 ZIP 재검증(C12 회귀).

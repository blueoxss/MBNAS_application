# 운영 계획 2026-10-27 (설계자) — 목표: 10-28 07:00 이전 3종 ZIP + 격리 검증 + 보고

사용자 지시(10-27 12:0x): "가장 전략적으로 효율적으로 운영, 내일 07:00 전 완성". 범위 결정(DD-A38): 완성 = 3 프로필 ZIP 빌드·T4/T5/T6/T8 격리 검증·T7 GPU 증거(DD-A35 창)·T2 동등성·기준셋 채점·최종 보고. 계약 1.5.0은 **최소형**(owner 전용 필드 제거 + additive 필드 + revisions)으로 축소, WP-5는 local 전송 모드 + 다운로드까지(prune 왕복·교차 runner 전송은 한계로 문서화), 웹 e2e는 chromium 설치 가능 시만.

## 파도(동시 실행; 소유 파일 분리)
| 파도 | 작업 | 시작 조건 | 담당 |
|---|---|---|---|
| W1 | WP-3A/3B/3C 빌드 | 진행 중(12:0x 재개) | opus ×3 |
| W2 | WP-7A 스크립트, 7B requirements·Docker·config·examples, 7C 문서, 7D 패키저·canary, 6A 웹 정리·dist | 진행 중(12:1x) | opus ×5 |
| W2b | WP-4A 계약 1.5.0 **초안**(live openapi 미교체), 5A gateway store 모듈, 5C runner local 전송 | 12:3x | opus ×3 |
| V3 | WP-3 검증(설치·closure·public·owner 게이트 via gateway·음성·core-down·3프로세스 smoke·web build) + 수정 루프 | W1 보고 후 | 검증 에이전트 + 설계자 재확인 |
| G | DD-A35 캠페인 중단 → GPU 트랙(WP-G2: T7, resume 동등성, T2-GPU) | V3 통과 직후 | opus ×1(GPU 전용) |
| W4 | WP-4B/4C 투영 전환·owner routes·revisions, 5B core locations·다운로드 배선, 6B 업로드·개정·diff UI, 6C 타입 재생 | V3 통과 직후 | opus ×4 |
| V4 | 통합 검증(owner 게이트·public·web·smoke) + 수정 | W4 보고 후 | 검증 에이전트 |
| P | package 3종 → T6 재배치 → T4 owner(bwrap/docker) → T5 public(docker→host core) → T8 canary → offline(unshare) | V4 통과 | opus ×2 |
| C | 기준셋 C01–C12 채점(설계자) + 수정 라운드, VALIDATION_REPORT, FINAL_REPORT, 캠페인 재개 | P 후 | 설계자 |

## 시간표(목표)
12:00 W1/W2/W2b → 15:00 W1 보고 → 17:00 V3 통과 → 17:00 G 시작·W4 → 20:30 V4 → 22:30 P → 01:00 C → 05:00 보고·캠페인 재개 → 07:00 마감. 여유 2시간.

## 실패 시 축소 순서
1) 웹 e2e(chromium) → 2) offline 변형 ZIP → 3) ADMIN_BOUNDARY docker 분리 → 4) 기준셋 반복 라운드 수(각 1회 채점+1회 수정) → 5) WP-5 prune 왕복. 축소 항목은 보고서에 '미검증/미구현'으로 명시.

## 운영 규칙
- Workflow 도구는 이 세션에서 재개 시 에이전트가 사용자 대화를 작업으로 오인한 사고(10-27 11:3x)가 있어 빌드는 Agent 도구 + 파일 브리프(/tmp scratchpad/wp3/*.txt)로 운영. 각 에이전트 보고는 docs/internal/evidence/<WP>/에 남긴다.
- 세션 한도 중단 시: TASK_STATE.md와 이 문서로 재개. 에이전트는 SendMessage로 이어서.

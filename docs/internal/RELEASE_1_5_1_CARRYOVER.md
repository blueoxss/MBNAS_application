# 1.5.1 이월 목록 (owner 내부; 2026-10-28 10:4x 설계자)

0.1.0 릴리스 후보(트리 `f00987be…`)에서 의도적으로 수정하지 않고 다음 판으로 넘긴 항목. 모두 평가 점수 앵커에 영향이 없는 Minor 또는 범위 결정(DD-A38)이다. 각 항목의 근거는 REVIEW_LOG.jsonl의 해당 set/round 행과 DECISIONS의 DD 번호에 있다.

| # | 출처 | 항목 | 현재 상태/완화 | 수정 방향 |
|---|---|---|---|---|
| 1 | C04 iter3 M1 (DD-A70) | prune 상태 실패 경로 테스트 6건(계획 실패 격리, 전달 실패 PENDING 유지, E11 롤백+단일 재시도, 취소 run 지시 취소, stale attempt 409, 취소 후 늦은 보고) 미이식 | 평가자 probe로 동작 확인; 사본 `docs/internal/evidence/CRITERIA/C04/probe_e3/` | `tests/owner/runner_protocol/`로 이식(ROOT를 저장소 상대 fixture로) |
| 2 | C04 iter3 M2 (DD-A64 잔여) | 자기 RUNNING lease가 만료된 채 재시작한 runner가 다음 sweep(≤10 s)까지 claim 429 | 과금 영향 없음 | `runner_api/routes.py` busy 검사(337-338)를 reclaim(345) 뒤로 |
| 3 | C02 iter2 D3 | runner job 자식 프로세스 environ 캡처가 프로젝트 T8 스위트에 없음 | 평가자 실측 0건; SECURITY_MODEL:34,109 문서화 | T8 runtime 캡처에 자식 프로세스 environ/cmdline 포함 |
| 4 | C02 iter2 D8 | runner-route canary 테스트 필수 경로 목록에 1.1.0 경로(heartbeat prune 지시, E11) 없음 | 평가자 probe 0건; 사본 `docs/internal/evidence/CRITERIA/C02/probe_e2_prune/` | `tests/owner/projection/test_runner_route_canary.py` 경로 목록 확장 |
| 5 | C09 iter2 D7 | `scripts/verify.sh:161-165` base_env가 호출자 PYTHONPATH/PYTHONHOME/PYTHONSTARTUP/PYTHONUSERBASE를 unit/integration/gpu suite에 전달 | 운영 지침: verify는 깨끗한 셸에서; start/doctor는 이미 scrub(C09 D1) | base_env에서 4개 키 제거, 제거한 키 이름을 suite 결과에 기록, ops 테스트 추가 |
| 6 | C07 iter2 O5 (DD-A69) | Lineage 배지 툴팁 없음(`Lineage.tsx:59`) | 다른 배지는 사유별 툴팁 적용 | 동일 evaluation.ts 문구 사용 |
| 7 | C07/C11 (DD-A69) | History 실패 표가 reason_code가 있는 행을 모두 나열해 EVALUATION_REQUIRES_CUDA 행이 포함 | FEATURES 한계에 문서화 | 분류 열 추가 |
| 8 | DD-A38/A68 | 교차 runner checkpoint 전송·affinity 없음; 유휴 runner로의 prune 지시 전달 경로 없음(PENDING 유지); `prune_requests` 조회 route 없음; store 사본 삭제 옵션 없음 | 문서화(FEATURES/COMPATIBILITY/OPERATIONS) | 1.6 설계 항목 |
| 9 | T7 (DD-A52/A54) | TensorRT 경로 BLOCKED_BY_ENV(wheelhouse 미포함); BENCHMARK는 owner 스크립트 경로만; 컨테이너 GPU job 미검증 | VALIDATION_REPORT T7 한계 기재 | 승인된 GPU 창에서 재검증 |
| 10 | C12/ADMIN | ADMIN_BOUNDARY_VALIDATED는 별도 관리자 host 필요(BLOCKED_BY_ENV) | 같은 host 프로세스 분리만 검증 | 별도 host + 교차 host TLS + 고객 root T5/T8 |
| 11 | T5 rerun2 (P2 관찰) | PRUNED checkpoint를 gateway store 사본에서 내려받은 뒤 공개 `checkpoints[].state`가 PRUNED→PRESENT로 바뀜(core 행은 PRUNED 유지) | 설계상 동작이나 사용자 혼동 가능 | 상태 우선순위 정의 또는 `available`/`store_copy` 플래그 분리 |
| 12 | C10 iter2 D8 (DD-A74) | `scripts/dev/canary_scan.py:209-213`가 per-needle cap 이전에 모든 매치의 줄 번호를 계산해 큰 파일에서 2차식 시간(1/2/4 MB에 1.07/3.6/12.7 s) | 0.1.0은 owner allowlist에서 T8 owner 스캔 덤프를 제외해 우회(DD-A74) | 기록되는 hit에만 줄 번호 계산(또는 증분 계산); 덤프는 요약본만 동봉 |
| 13 | P2 T4 attempt_dd_a73 F3 (DD-A75) | `scripts/doctor.sh:179`·`scripts/verify.sh:192`의 entry_points/script_interpreter 검사가 pip `/bin/sh` 트램펄린의 따옴표 있는 경로만 파싱 → 공백 없는 긴 설치 경로(shebang 한계 초과)에서 'runs None' 오탐(진입점은 정상 동작; T4 sandbox relocation 4/7) | 문서화된 한계(FINAL_REPORT·VALIDATION_REPORT T4 행); 짧은 경로에서는 8/8 | 비인용 트램펄린 경로도 파싱; 긴 경로 회귀 테스트 추가; 세 프로필 재빌드 필요 |
| 14 | C02 addendum D9 / F1 observation (DD-A75) | `projection.scrub_text`의 `_ABS_PATH`는 공백에서 멈추고 Windows/UNC·`file://`·`~/`·`host:/path`·사전 키 경로를 다루지 않음(raw_metrics는 F1b의 값 전체 치환으로 해결; 일반 메시지 안의 공백 경로는 꼬리가 남을 수 있음); `mbnas_contracts/runner_protocol.py:6-14` 저장 위치 목록에 resolved recipe가 공개 raw_metrics에 복사(스크럽)된다는 사실 누락 | raw_metrics 외 표면에서 runner 경로 0건(C02 live probe 29 본문); runner_protocol.py는 public 멤버라 0.1.0에서 미편집 | 메시지 스크럽을 토큰 단위가 아닌 경로 인식형으로 개선·시험 추가; runner_protocol.py docstring 보완(public 재빌드와 함께) |

# WP-G2 GPU 검증 트랙 (설계자, 2026-10-27) — DD-A35로 확보되는 로컬 GPU 창

## 0. 전제
- 시작 조건: WP-3 수용(owner 게이트 0 실패, 3프로세스 smoke PASS). 그 시점에 DD-A35 절차로 캠페인 중단 → GPU 0/1 확보.
- 코드 기준: 중단 직후의 트리 상태를 `scripts/dev/source_tree_hash.py`로 고정(TREE_HASH)하고 모든 증거에 기록. 트랙 진행 중 코드 변경 시 재측정 대상 표시.
- 캠페인 재개(DD-A35 (2))는 트랙 종료 또는 사용자 지시 시.

## 1. 실험 (우선순위 순, 예산 합계 ≤ 1 GPU-일)
| ID | 내용 | 환경 | 증거 |
|---|---|---|---|
| G2-1 T7 GPU 경로 | core+owner worker+runner(GPU 등록, cuda:0) 실제 프로세스: BASELINE_TRAIN→OFF 생성→CANDIDATE_TRAIN(평가 ON, 실제 latency 측정→EVALUATED)→EVALUATE→BENCHMARK→FINAL_RETRAIN(짧게), pause→full-state resume, cancel, runner 강제 종료→lease 만료→재claim(attempt+1, 멱등 commit), artifact 등록·relay, export | runtime/venv, PILOT 합성/소규모 실데이터 | evidence/T7/{steps.jsonl, logs, snapshot.json} |
| G2-2 동등성(원본 대비) | 같은 seed·preset으로 원본 모노리스(build/source_snapshot, 격리 서브프로세스)와 mbnas_core 실행: gen0 후보 genotype/YAML·structure gate·param/FLOP 바이트 일치; 학습 지표(mAP50-95, loss)·latency는 동일 seed·deterministic=True로 교차 차이 ≤ 같은 쪽 반복 분산 | 동일 venv | evidence/T2_GPU/{ref,new}.json, T2_GPU_REPORT.md |
| G2-3 resume 동등성 | scripts/runner/resume_equivalence.py yolo11n horizon 6 cuts 2,4 | GPU | evidence/T7/resume_equivalence.json |
| G2-4 샌드박스·Docker | sandbox RLIMIT_AS 실측(GPU 호스트), nvidia/cuda:12.4.1 이미지에서 runner 설치·CUDA 인식(WP-7 Dockerfile 검증) | docker | evidence/T7/docker_runner.txt |

## 2. 금지
- 캠페인 CROOT·/data 산출물에 쓰기 금지. 중단·재개는 DD-A35 절차만.
- 가짜 측정 금지: latency 측정 실패 시 UNEVALUATED 유지.

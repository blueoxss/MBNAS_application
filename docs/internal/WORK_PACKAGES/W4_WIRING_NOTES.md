# W4 배선 메모 (설계자, 2026-10-27 13:4x) — WP-3 검증 통과 후 투입되는 4B/4C/5B/6B/6C가 반드시 읽을 것

## WP-5A store 모듈(완료, 미배선) → 배선 담당(5B gateway 측 포함)
- 모듈: mbnas_server/src/mbnas_server/artifacts/{store,signing,download}.py, 테스트 102개 통과, 증거 docs/internal/evidence/WP5/5A/.
- GatewaySettings: 기존 `artifact_store`(MBNAS_GW_ARTIFACT_STORE) 재사용(`artifact_store_dir` 신설 금지). 추가: `artifact_max_bytes`(기본 4 GiB), 선택 `artifact_verify_first_max_bytes`(64 MiB). 서명 키 = 기존 `artifact_signing_key` + `signed_url_ttl_s`(600) → `signing.signing_key(settings)`.
- 시작 시 `app.state.artifact_store = ArtifactStore.from_settings(settings)`; `store.sweep_tmp()` 선택.
- 다운로드 라우트: exp/sig 있으면 `require_valid_signature`, 없으면 bearer → core에서 (run_id, artifact_id) 레코드 조회 → `run_in_threadpool(serve_artifact, store, record)` → StreamingResponse(headers). refresh/JSON은 `artifact_view(record, base_url, key, ttl_s, run_id)`.
- `from __future__ import annotations` 모듈에서는 `Request`를 모듈 수준 import(422 함정). `X-Artifact-Verification`를 EXPOSED_HEADERS에 추가.
- core(5B): gateway용 run 범위 레코드 조회 엔드포인트 `{id, sha256, bytes, mime_type, state, run_id}`; READY = gateway store에서 바이트 검증됨(R8). 최소형: gateway가 store 존재 여부로 READY 판단하고 core location 상태 갱신은 `POST /core/v1/console/artifacts/{id}/locations`(server scope) 1개만.
- 웹(6B/6C): 404=ARTIFACT_NOT_READY, 409=LOAD_FAILED → 상태 코드가 아니라 Error.code로 매핑.
- 한계(문서화): 64 MiB 초과 변조 파일은 409 대신 불완전 본문(+X-Artifact-Sha256 대조), 업로드 API·prune 없음(DD-A38).

## WP-3 검증(V3)에 넘길 관찰
- tests/public/server에서 WP-3 테스트 8건 실패 관측(13:3x, 5A 보고): test_gateway_skeleton::test_docs_disabled_and_unknown_routes_are_contract_errors, test_wp3_gateway_relay::test_dot_segment_path_params_never_reach_another_core_route ×7 — 3B 코드 vs 3C 신규 테스트 불일치. V3에서 근본 원인(경로 세그먼트 `..`/`.` 정규화 거부가 relay에 없거나 테스트 기대가 다름) 판정.

## WP-5C runner local 전송(완료, 10-27 14:4x) → 5B/7/V3가 알아야 할 것
- RunnerSettings: `artifact_transfer=none|local`(upload 거부), `artifact_store_dir`(local 필수), `artifact_store_mode=same-uid|link-0644|copy-0644`, `artifact_transfer_max_tries=6`. 같은 호스트 프로파일: `MBNAS_RUNNER_ARTIFACT_TRANSFER=local`, `MBNAS_RUNNER_ARTIFACT_STORE_DIR` = `MBNAS_GW_ARTIFACT_STORE`(WP-7B env 예시·compose 공유 볼륨; uid 다르면 link-0644/copy-0644).
- in-place 재작성 파일(executed architecture.yaml, native plot)은 복사, checkpoint/평가 JSON/weights는 하드링크. `result.outputs.artifact_transfer={mode, ok, transferred, failed[]}`는 commit 시점 스냅샷 → 다운로드 가능 여부는 gateway store 존재로 판정(DD-A39).
- 5B: checkpoint 객체는 artifacts 테이블에 없음 → checkpoint 다운로드에는 checkpoint id→sha 매핑 엔드포인트 필요; relay 시 `sha256:` 접두사 제거(serve_artifact는 소문자 hex만).
- 5A 수정 요청(배선 담당이 반영): `ArtifactStore.sweep_tmp`는 `max(st_mtime, st_ctime)` 기준으로(하드링크 temp가 원본 mtime을 물려받아 전송 중 temp가 지워질 수 있음).
- V3: `scripts/dev/wp2_two_process_smoke.py`는 WP-3A 이후 core `/api/v1`이 기본 비활성이라 health 404 → 스모크가 `MBNAS_CORE_EXPOSE_LEGACY_API=true`를 명시 설정하도록 수정(owner 로컬 디버깅 경로). 사용자 경로의 정식 스모크는 wp3_three_process_smoke.py(gateway 경유).
- 증거: docs/internal/evidence/WP5/5C/ (owner runner_protocol 42 passed, public runner+infra 118, 실프로세스 local 전송 스모크 exit 0, store 13객체 재해시 일치).

## WP-7D 패키저(완료, 15:0x) → V3/W4/P가 알아야 할 것
- `bash scripts/package.sh --profile <p> [--platform auto|tag] [--reproducible] [--allow-missing-web] [--canary]`; exit 0/1/2/3; 보고 `build/package_reports/<profile>.json`; dist/<profile>에 최신 1개만. 현재 3종 ZIP은 중간 스냅샷(모든 파도 후 재빌드 필수).
- V3 작업: (1) tests/public/contracts/test_contract_history.py 20–21행 주석·tests/public/tools/smoke_api_only.py의 호스트 경로 → placeholder; (2) owner 도구 의존 public 테스트에 skip-if-missing(DD-A43 b); (3) wp2 smoke에 expose_legacy_api 명시.

## WP-7A 운영 스크립트(완료, 15:1x) → V3/P가 알아야 할 것
- install/doctor/start/stop/verify 완성, tests/public/ops 59 passed; 공백 경로에서 public·owner offline 설치 → start → doctor --strict → verify all(unit 246, integration: owner 15/15 = wp3_three_process_smoke, public 3/3, relocation 8/8) 통과. 증거 docs/internal/evidence/WP7/7A/.
- 이탈 승인(설계자): owner integration은 wp3 smoke 사용, gpu suite는 `--allow-gpu` 필요(DD-A7), `--role all`에 web 미포함(DD-A30), owner runner.env에 ALLOW_PICKLE_RESUME=true(DD-A20).
- V3 작업 추가: `scripts/dev/wp2_two_process_smoke.py`는 `/core/v1/console/health`를 폴링하도록 수정(또는 expose_legacy_api 명시); `tests/public/infra/test_no_host_facts.py::test_the_scan_covers_all_five_packages`는 mbnas_core/src 부재 시 skip.
- 스크래치 트리 runtime/test_root/wp7a/ 약 14 GB → P 단계 종료 후 설계자가 삭제.

## DD-A46 분리 배포 run 생성(4B core + 5B gateway 필수)
- validate 시 게이트웨이가 만든 `path_checks`/`dataset_manifests`를 프로토콜 해시 키로 캐시 → POST /runs, /training-runs, /final-retrain 릴레이 때 같은 입력을 본문에 동봉(GatewayInputs). core는 입력이 있으면 core-local 경로 검사 대신 그것으로 재검증 → STALE_VALIDATION 오판 제거. 증거: evidence/WP7/7B/split_output_root_probe.txt. 테스트: 서로 다른 output_root(게이트웨이 측 존재, core 측 부재)로 run COMPLETED.
- WP-7B 산출: Docker 이미지 3종 빌드·CPU compose 스모크 통과(evidence/WP7/7B/INDEX.txt), config/examples 전 키 문서화, examples/synthetic4.

## P 단계 체크리스트(설계자 누적)
- 7B env 예시: `MBNAS_GW_ARTIFACT_MAX_BYTES`, `MBNAS_GW_ARTIFACT_VERIFY_FIRST_MAX_BYTES` 추가; same-host 프로파일은 `MBNAS_RUNNER_ARTIFACT_TRANSFER=local` + store dir = gateway store(이미 compose 반영, env 예시·install.sh 생성 env 확인).
- 7D: `mbnas_web/README.md` include; oracle 폐포 4파일 재계산; check:bundle needle 7+21; 모든 파도 후 3종 재빌드 + run_evidence.sh.
- 7A: wp3_three_process_smoke step 9가 `snapshot["artifacts"]`(부재)를 읽어 항상 export 폴백 → 수정(4C/V4).
- 7C: PENDING B1–B7, C9(DD-A46 반영됨), 1.5.0 API.md, gateway 신규 설정 키.
- 정리: runtime/test_root/{wp7a(14 GB), g2_tree(11 GB), wp5b_smoke, wp3_smoke, wp2_smoke, wp5c_smoke} 최종 보고 후 삭제(설계자).
- 최종 GPU 재실행(DD-A49 f): 최종 트리에서 T7 a(device_used)·f2(다운로드)·g2(BENCHMARK 스크립트 경로) ≤30 min, 그 뒤 캠페인 재개.
- P 정리 추가: mbnas_web/evidence/(e2e 산출물) 삭제; dist는 V4B_FIX 빌드(index-BnpZcnwU.js)로 최종 ZIP에 포함.

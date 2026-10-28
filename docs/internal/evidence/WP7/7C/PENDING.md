# WP-7C PENDING — 문서 후속 작업 목록 (2026-10-27 13:3x)

이 목록은 WP-7C 문서(ROOT `README.md`, `docs/public/**`, `docs/registration/**`, `scripts/dev/gen_registration_docs.py`)가 **다른 WP의 결과를 기다리는 부분**과 **결정이 필요한 부분**이다. 문서에는 존재하지 않는 옵션을 적지 않았고, 미실행 검증은 "검증 예정"으로 적었다.

## A. 스크립트 상태 (WP-7A/7D)

| 스크립트 | 문서 작성 시점 상태 | 문서 반영 |
|---|---|---|
| `scripts/install.sh` | 존재, `--help` 확인 | README, INSTALL §3·§5 |
| `scripts/doctor.sh` | 존재, `--help` 확인 | README, INSTALL §5, OPERATIONS §3 |
| `scripts/start.sh` | 존재, `--help` 확인 | README, INSTALL §5, OPERATIONS §3 |
| `scripts/stop.sh` | 존재, `--help` 확인 | README, INSTALL §5·§6, OPERATIONS §3 |
| `scripts/verify.sh` | 존재, `--help` 확인 | README, INSTALL §5, OPERATIONS §10 |
| `scripts/package.sh` | 존재, `--help` 확인 | OPERATIONS §11 |

"(script pending WP-7A)" 표시가 필요한 항목은 없음(6개 모두 존재). 단 이 스크립트들은 작성 중이었으므로, 마지막 수정 뒤 `runtime/venv/bin/python -I -B docs/internal/evidence/WP7/7C/check_docs.py`를 다시 실행해 "문서에만 있는 옵션 0 / --help에만 있는 옵션 0"을 확인해야 한다(현재 PASS, `check_docs.txt`).

## B. 다른 WP 결과가 나오면 갱신할 문서

| # | 계기 | 갱신 대상 |
|---|---|---|
| B1 | 계약 1.5.0 적용(WP-4A/4B: `mbnas_contracts/openapi/openapi.json` 버전 변경) | `python scripts/dev/gen_registration_docs.py` 재실행(INTERFACES 연산 표 자동 갱신), `docs/public/API.md` §4·§8(계획→적용), `COMPATIBILITY.md` §2, `INTERFACES.md` §3(업로드가 계약 안으로)·§4(투영 상태), `SECURITY_MODEL.md` §6·§9, `FEATURES.md` H행 |
| B2 | WP-3 수용(V3) | `FEATURES.md`·`SECURITY_MODEL.md` §9·`INTERFACES.md` 끝 문단의 "작업 중" 상태. gateway가 비-loopback 평문 core URL을 거부하게 되면 `SECURITY_MODEL.md` §4, `OPERATIONS.md` §5 문구 |
| B3 | WP-5(artifact 저장소·runner 로컬 전송) 수용 | `FEATURES.md` G·H행, `SECURITY_MODEL.md` §8-6, runner의 `MBNAS_RUNNER_ARTIFACT_TRANSFER`·`MBNAS_RUNNER_ARTIFACT_STORE_DIR` 설명(INSTALL §4·OPERATIONS §2; compose는 이미 `local`을 씀) |
| B4 | WP-6(웹) 수용 | `FEATURES.md` F행(업로드·개정·diff·UNTRAINED 배지·계보 생성 후보), G행(preset 자동 적용) |
| B5 | WP-8 검증 결과 | `PROGRAM_OVERVIEW.md` §5, `KEY_CONTRIBUTIONS.md` §3, `FEATURES.md` 상태 열, README 상태 문단, `INSTALL.md` 머리말(독립 설치 검증) |
| B6 | GPU 컨테이너 실행(WP-G2/WP-8; 현재 NVIDIA container runtime 없음) | CPU 이미지·compose는 WP-7B 시험으로 실행 확인 반영됨(INSTALL §1, OPERATIONS §12, README). GPU 실행 후 "검증 예정" 문구와 FEATURES 한계 항목 갱신 |
| B7 | 패키징(WP-7D) | registration ZIP의 `docs/registration/FILE_LIST.md`는 패키저가 그 ZIP 매니페스트로 다시 만든다(생성기 `--only file-list --root <staging> --manifest <staging>/FILE_MANIFEST.jsonl`, WP-7D가 이미 호출). 소스 트리의 FILE_LIST.md는 의도적으로 "생성 대기" 상태다. allowlist 확정 후 `SUBMISSION_SCOPE_TABLE.md` §2를 최종 allowlist와 다시 대조 |

## C. 결정 항목 처리 현황 (DD-A41·DD-A43 반영, 14:1x)

| # | 항목 | 결정 | 문서 반영 |
|---|---|---|---|
| C1 | 웹 소스가 어떤 ZIP에도 없음 | DD-A41(b): public·registration에 웹 소스 + dist 포함. DD-A43(a): `fitness_oracle.ts`와 이를 import하는 웹 시험은 owner 전용(allowlist `[deny-importers]`, 14:02) | SUBMISSION_SCOPE_TABLE §2, REVIEW_CHECKLIST 12a, 생성기 SS1 배포 범위 |
| C2 | 공개 ZIP에 `tests/public` 없음 | DD-A41(b): 포함(`test_no_host_facts.py` 등 제외). DD-A43(b): owner 전용 도구에 의존하는 공개 시험은 skip 처리(V3/4C) | SUBMISSION_SCOPE_TABLE §2 |
| C3 | `LICENSE_STATUS.md`의 원고 문구 | DD-A41(c): owner 전용. 공개본은 THIRD_PARTY_NOTICES + docs/public 라이선스 절 | INSTALL §8 신설, README 라이선스 절, RIGHTS_REVIEW §7, SUBMISSION_SCOPE_TABLE |
| C4 | VERSION 1.0.0 vs 0.1.0 | DD-A41(a): 0.1.0 단일, 등록 문서 "v0.1.0 초판" | PROGRAM_OVERVIEW §1, REVIEW_CHECKLIST 7, README 버전 절 |
| C5 | migrate 스크립트 임시 제외 | DD-A41(b): 해제(scrub 완료) | SUBMISSION_SCOPE_TABLE §2, COMPATIBILITY §2 |
| C6 | gateway 평문 core URL 거부 | DD-A41(d): 반영됨(`mbnas_server.config.core_allow_insecure_http`, env `MBNAS_GW_CORE_ALLOW_INSECURE_HTTP`, 14:1x 확인) | SECURITY_MODEL §4, OPERATIONS §5, TROUBLESHOOTING §2, INTERFACES §2, README 보안 요약 갱신 |
| C7 | 등록 문서 공개 수준 | DD-A41(f): core 하위 패키지 이름·공개 계약 상수 인용 허용 | 변경 없음 |
| C8 | host fact가 남은 공개 시험 2개 | DD-A43(e): V3에서 placeholder로 교체 후 deny 해제 | 해제되면 SUBMISSION_SCOPE_TABLE §2 공개 시험 행에서 두 파일 언급 삭제 |
| C9 | 분리 배치 `output_root` → run 생성 409 STALE_VALIDATION | DD-A46 수정 완료, DD-A51 수용 시험(core 밖 output_root run COMPLETED) 통과 | "작업 중" 문구 삭제(INSTALL §4, TROUBLESHOOTING, FEATURES). 남은 제한(DD-A53 g: 분리 배치 학습은 train_root/search_val_root 명시)을 INSTALL §4·FEATURES에 기재 |
| C10 | gateway 사전검사 코드 규칙·경로 위생 | DD-A44(d)(e): 업로드 유형 거부 422, `model_path` 거부는 `MODEL_CONFIG_INVALID`·`PATH_*`, dot segment는 404 NOT_FOUND | API §1·§4·§7, TROUBLESHOOTING §5·§6 반영(코드 14:12 판 기준). `api/path_guard.py`는 구조표 B2.2에 배정 |

## D. 이번에 하지 않은 것

- Docker 이미지 빌드·컨테이너 기동, 독립 설치, ZIP 재배치 시험은 실행하지 않았다(WP-7B/7V/WP-8 범위). 문서의 해당 절차는 각 파일의 사용법 주석과 `--help`에서 옮겼다.
- 등록 기관 양식·제도는 확인하지 않았다(REVIEW_CHECKLIST는 사용자 기입 항목으로만 둠).
- `mbnas_web/README.md`(원본 시절 내용: 포트 8000/5173, `MBNAS_server` 경로)는 소유 범위 밖이라 고치지 않았다.

## E. 최종 문서 갱신 라운드 (2026-10-28 03시대)

| # | 항목 | 반영 |
|---|---|---|
| E1 | 계약 1.5.0 승격(sha `70e0cffa…`, DD-A53) | API.md(현재 계약·§8 1.4.3 대비 변경), COMPATIBILITY §2·§5, INTERFACES(생성 표 1.5.0: 경로 38·연산 39, 업로드·개정 경로 한국어 요약, 투영·owner 경로), README |
| E2 | DD-A46~A53 제한 사항 | FEATURES 한계(분리 배치 학습 경로, 1.4.x DB 비호환, 콘솔 벤치마크 미제공·TensorRT BLOCKED_BY_ENV, upload 전송 미구현, 64 MiB 초과 객체 변조 탐지, 브라우저 sha256 HTTPS/localhost 한정, ETRI attention GPU 비결정성, CUDA_DEVICE_ORDER), OPERATIONS §6·§13(벤치마크 스크립트), SECURITY_MODEL §6·§9 |
| E3 | config/examples | gateway·all-in-one에 `MBNAS_GW_ARTIFACT_MAX_BYTES`·`MBNAS_GW_ARTIFACT_VERIFY_FIRST_MAX_BYTES` 추가(`CORE_ALLOW_INSECURE_HTTP`는 이미 있음), runner·all-in-one에 `CUDA_DEVICE_ORDER` 안내(`ARTIFACT_STORE_MODE`는 이미 있음). WP-7B `check_env_examples.py` PASS(`env_examples_check_7c.txt`) |
| E4 | GPU 검증 1차(evidence/T7/README.md, PARTIAL, 결함 3건 수정) | FEATURES(GPU 1차 확인 표기), PROGRAM_OVERVIEW §5, KEY_CONTRIBUTIONS §3, INTERFACES 검증 상태. **재실행 결과(`steps_rerun.jsonl` 진행 중, RERUN_README 없음) 나오면 "재실행 예정" 문구 갱신** |
| E5 | 컨테이너 상태 정정 | 앞선 지시("GPU 컨테이너 미실행")와 달리 T7 G2-4에서 CUDA 이미지의 `docker run --gpus` GPU 인식 smoke가 PASS였다 → "GPU 인식만 확인, compose GPU runner 실작업은 검증 예정"으로 기재 |
| E6 | 새 gateway 모듈 | `api/artifact_routes.py` → B2.7, `api/gateway_inputs.py` → B2.1 |

## F. 최종 문서 패스 (2026-10-28 05시대, DD-A54~A62)

| # | 항목 | 반영 | 남은 것 |
|---|---|---|---|
| F1 | `scripts/public/make_pilot_subset.py` 공개 동봉(DD-A59 b) | 구조표 B7.4(공개)·B7.5(owner `scripts/runner`) 분리, SS7 5열, SUBMISSION_SCOPE_TABLE 03:53 allowlist 기준 | — |
| F2 | runner id 무작위 기본값(DD-A61 b) | runner·all-in-one env 예시, INSTALL §4, TROUBLESHOOTING `RUNNER_MISMATCH`(업그레이드 주의), FEATURES 작업자 행 | — |
| F3 | DD-A60/A62 문서 | TROUBLESHOOTING(`EVALUATION_REQUIRES_CUDA`, `DEVICE_UNAVAILABLE`, `WORKER_IO_ERROR`/`WORKER_JOB_ERROR`, 로그 추적), OPERATIONS §14(접근 로그 형식·request id 연결), §15(owner worker core 쪽 파일, 장치 목록·`console.benchmark.device`, health 허용 경로), FEATURES CPU runner 한계 | ~~확인 못 함 2건~~ → **해결(G1·G4, 10-28 06시대)**: DD-A62 (b)·(d)가 코드에 반영된 것을 확인(`mbnas_core/console/services.py` `_default_devices_fn`, `mbnas_core/runner_api/routes.py` `_lease_for_cpu_runner`/`requires_cuda`)하고 OPERATIONS §15·TROUBLESHOOTING `DEVICE_UNAVAILABLE` 행을 구현대로 고침(우회 문구 삭제). 요청받은 `--bench-device` 옵션은 코드에 없어 쓰지 않고 프로토콜 필드 `console.benchmark.device`로 기재(변경 없음) |
| F4 | GPU 검증 YES(DD-A54, RERUN_README) | FEATURES("GPU 확인", 한계 목록), PROGRAM_OVERVIEW §5, KEY_CONTRIBUTIONS §3, INTERFACES, README, OPERATIONS §13. COMPATIBILITY·SECURITY_MODEL의 GPU 행은 설계자가 04:02에 이미 갱신해 그대로 둠 | — |
| F5 | T4/T5/T6/T8 1차 결과 | PROGRAM_OVERVIEW §5, FEATURES I·H행, SECURITY_MODEL §3·§9, KEY_CONTRIBUTIONS §3, README: "1차 결과 + 수정 반영 + 재실행 예정" | **재실행 결과로 최종 수치 갱신**(코디네이터 담당 예정) |
| F6 | health 허용 경로(DD-A59 a) | API §2, OPERATIONS §15, SECURITY_MODEL §6, FEATURES H행 | — |

## G. 최종 사전 패키징 패스 (2026-10-28 06시대, DD-A62·DD-A63 구현 반영)

근거: `docs/internal/evidence/WP7/FIX2B/EVIDENCE_INDEX.txt`(split smoke 13/13 ×2, 합성 runner 신원 claim 시험), 소스 대조(아래 파일). 이 패스의 증거: `docs/internal/evidence/WP7/PREPACK/`.

| # | 항목(구현 위치) | 문서 반영 | 남은 것 |
|---|---|---|---|
| G1 | (b) `local` 장치 목록이 비면 등록(REGISTERED/ONLINE)된 CPU runner의 `cpu` 항목으로 대체, GPU runner는 합치지 않음(`console/services.py`) | OPERATIONS §15 표·불릿, TROUBLESHOOTING `DEVICE_UNAVAILABLE`·`EVALUATION_REQUIRES_CUDA` 행, FEATURES 한계, core.env.example `MBNAS_CORE_DEVICE_SOURCE` 주석(+all-in-one 재생성) | — |
| G2 | (a) NOT_EVALUATED 행: `SEARCH_COMPLETE` + `performance_status NOT_EVALUATED` + `reason_code EVALUATION_REQUIRES_CUDA`(없으면 `NOT_EVALUATED`), 지표·fitness·gate null, latency·evaluation_hash pending, split_hash null, checkpoint 유지, 명시 학습 run COMPLETED, 탐색 arm은 승급·부모 없음(`orchestration/engine.py` `commit_not_evaluated`, runner `evaluation/handlers.py` `not_evaluated_payload`) | OPERATIONS §6·§15, TROUBLESHOOTING §7(행 교체), API §2 후보 행, FEATURES F(표시 행)·한계, INTERFACES §5, PROGRAM_OVERVIEW §5·§6 | — |
| G3 | (c) core 쪽 생성 구조 YAML: `<MBNAS_CORE_OUTPUT_ROOT>/<run>/architectures/<cid>.yaml`만, 심볼릭 링크 금지·1 MiB·기록 YAML과 바이트 동일 시 등록, 서빙도 같은 경계(`artifacts/registry.py` `resolve_file`, `console/routes_files.py`), gateway는 core에서 받아 sha256 재대조(`mbnas_server/api/artifact_routes.py` `_from_core`), gateway 저장소에 없음 | OPERATIONS §5(출력 경로 문구 정정: core `OUTPUT_ROOTS`에 있을 필요 없음)·§8(백업)·§15, TROUBLESHOOTING §8(신설: 산출물 다운로드 코드; 아카이브 §9, 로그 §10으로 번호 이동), API §2 산출물 행, SECURITY_MODEL §6, INSTALL §4(모순 문구 정정), FEATURES H행, INTERFACES §3, core.env.example `OUTPUT_ROOT(S)` 주석 | — |
| G4 | (d) claim 규칙 fleet-aware: CPU runner는 CUDA 필요 작업을 같은 kind를 받는 ONLINE 비CPU runner가 있을 때만 건너뜀(204, attempt 유지), CPU 전용 fleet은 종전대로(`runner_api/routes.py`) | OPERATIONS §6·§15(규칙·CPU 전용 fleet 낭비·GPU runner OFFLINE/LOST 시 동작), TROUBLESHOOTING §7(204 행 신설), FEATURES H행·한계, INTERFACES §5 | 실제 GPU runner가 섞인 fleet 실행: **확인 못 함**(합성 runner 신원 시험만) — 문서에 그대로 기재 |
| G5 | (e) owner worker `--env-file` → 자기 프로세스의 `MBNAS_CORE_ENV_FILE`(core API와 동일), 자식에게는 전달 안 함(`owner_worker/main.py`) | OPERATIONS §1(표 + 문단), core.env.example 머리말 a) | — |
| G6 | 웹 `isTrainedNotEvaluated`: 탐색 arm의 `NOT_EVALUATED` + `EVALUATION_REQUIRES_CUDA` 행도 "학습 완료 · 미평가"(DD-5 문구 유지, `training_status UNTRAINED`면 제외) | FEATURES F행, OPERATIONS §15, TROUBLESHOOTING §7, FEATURES G행 웹 단위 시험 591→593 | 배지 `title`(Monitor.tsx·CandidateView.tsx: "explicit training with validation OFF")은 소유 밖이라 그대로 — 탐색 arm 행에는 맞지 않음(교차 요청) |
| G7 | INSTALL 머리말의 "독립 설치 검증 안 함" 문구가 FEATURES I와 모순 | FEATURES I 기준으로 정정(owner 오프라인 설치·T6 owner PASS, public 네트워크 차단 설치, T4·T5 재실행 예정) | — |

남은 것(이 패스 밖): F5의 T4·T5·T6(public·registration)·T8 런타임 재실행 결과 반영(코디네이터 담당), 실제 GPU 혼합 fleet(확인 못 함).


# SECURITY_BASELINE — A8 보안 기준선 감사 (지침 §8 대비)

작성 2026-10-23 (WP-0 분석가 A8). 독자: 설계자. 결정이 필요한 항목은 §6에 모았다.

**입력과 방법.** `build/source_snapshot`(HEAD 18c4815, 2026-10-23 21:07 KST 사본)과 원본 venv의 ultralytics 8.4.173 `nn/tasks.py`(읽기 전용, sha256 앞 16자리 `c2465c97a13a1e3b`)를 정적으로 추적했다. 서버 기동·테스트 실행·GPU·네트워크·프로세스 조작은 하지 않았다. 따라서 "도달 가능"은 코드 경로상의 판단이며 실행으로 재현한 것이 아니다. 실행 확인이 필요한 항목은 **확인 못 함**으로 표시했다(§7).

**경로 약어.** `S/` = `application/MBNAS_server/src/mbnas_server/`, `T/` = `application/MBNAS_server/tests/`, `D/` = `application/MBNAS_server/deploy/`, `W/` = `application/MBNAS_web/`, `U/` = 원본 venv `site-packages/ultralytics/`(스냅샷 밖, 읽기 전용). 상태 어휘: **충족 / 부분 / 없음**.

---

## 0. 결론 요약

1. 인증은 정적 bearer 토큰 **1개**가 전부다(S/api/auth.py:24-30, S/config.py:50). 브라우저·운영 스크립트·smoke가 같은 토큰을 쓰고, worker 유닛도 같은 EnvironmentFile을 받는다(D/mbnas-worker.service.example:14-16). 브라우저/runner/core-admin 자격 분리, scope·만료, 서비스 간 TLS 검증은 **없음**. WP-2/WP-3 신규 설계 대상.
2. **치명 갭(R5)**: 사용자 YAML 구조 검사에서 mbnas는 모듈명을 식별자 정규식으로만 거르고(S/search/structure.py:370-372) 허용목록이 없다. ultralytics `parse_model`은 모듈명을 `globals()[m]`로 해석하고 그 객체를 **호출**한다(U/nn/tasks.py:2127-2134, 2228). tasks.py 전역에는 `check_requirements`(pip 설치: U/utils/checks.py:527, 596-625), `check_file`(URL 다운로드: :775, 818-826), `torch_load`(`weights_only=False` 기본 pickle: U/utils/patches.py:184-185), `yaml_model_load`(임의 경로 YAML 읽기: U/nn/tasks.py:2246)가 import되어 있다(U/nn/tasks.py:93, 106). 즉 `[-1, 1, check_requirements, ["pkg"]]` 한 행이 **API 프로세스 안에서 `pip install`을 시도하는 경로가 정적으로 열려 있다**. ultralytics의 완화(`ULTRALYTICS_SAFE_LOAD`, `YOLO_AUTOINSTALL=false`)는 학습 adapter 안에서만 설정된다(S/adapters/ultralytics_adapter.py:83). 실행 재현은 하지 않았다(확인 못 함).
3. **R7 없음**: 구조 검사는 API 프로세스 안에서 동기 실행된다(S/api/model_services.py:517 → S/search/structure.py:409 → S/search/genotype.py:456). RLIMIT·타임아웃·서브프로세스·동시성 격리가 없고(`setrlimit|RLIMIT|signal.alarm|Semaphore` 검색 0건), `input_size` 상한이 없어(S/api/model_services.py:414-420) 거대 입력 텐서(S/search/genotype.py:483)로 OOM을 유발할 수 있다.
4. **R12 없음**: 공개 DTO allowlist가 없다. `prune()`은 null만 제거하고 미지 키는 통과시키며(S/api/contract.py:93-102), pydantic 거울은 `extra="allow"`(S/schemas/records.py:33, 358). snapshot은 candidate `audit`·`lineage` 전체를 내보낸다(S/api/routes_runs.py:278-289). allowlist형 투영은 artifact metadata 한 곳뿐(S/artifacts/registry.py:214-218). OpenAPI에 `repair*`, `mutation_rate`, `crossover_rate`, `fitness_weights` 어휘가 이미 있다(`application/MBNAS_server/contracts/openapi.json`).
5. **충족인 것**: 경로 봉쇄(S/util/paths.py:81-112; S/artifacts/registry.py:111-190; S/archive/scanner.py:129-137, 279-286), 서명 URL HMAC·상수시간·만료(S/artifacts/registry.py:78-108), argv-only 서브프로세스(S/worker/main.py:293-298; scripts/dev_stack.py:84-102; application/scripts/console_stack.sh:115-117, 130-131), 비밀 env 제거(S/worker/main.py:61, 288), 로그 리댁션(S/util/log_redact.py:18-26, 67-75), 오류 본문+request id(S/api/errors.py:29-33; S/api/auth.py:19, 47-53), 본문 1 MiB(S/api/limits.py:13), data.yaml 안전 파싱(S/datasets/yaml_safe.py:20-21, 79-90). 기존 테스트가 이를 고정한다(§4).
6. **R13 없음**: 비율 제한·쿼터·조회 감사가 전무하다(`429|RateLimit|throttl` 0건). SSE 구독은 무제한이고 구독마다 SQLite 연결을 연다(S/events/bus.py:82).
7. 공개 배포물에 소유자 인프라가 하드코딩되어 있다: `/root/detection/...`, `/data/mgjeong/...`, `147.46.114.253`(S/config.py:20, 26-27, 54, 56, 66; S/search/etri_bridge.py:37; S/protocol/presets.py:38, 45-46; S/adapters/rfdetr_adapter.py:55; application/scripts/console_stack.sh:23, 41; application/MBNAS_server/scripts/run_tests_host.sh:9-14). T8 canary의 고정 needle로 삼아야 한다(§5).

---

## 1. 요구사항 → 현재 통제 → 상태 → 갭 → 제안 → WP

| # | 요구사항(§8) | 현재 통제 (근거) | 상태 | 갭 | 제안 변경 | WP |
|---|---|---|---|---|---|---|
| R1 | 브라우저/runner/core-admin 자격 분리 | 단일 `MBNAS_API_TOKEN`을 모든 `/api/v1` 라우트가 공유(S/api/app.py:54-62; S/api/auth.py:24-30). 웹은 토큰을 React 메모리에만 보관·URL/스토리지 금지(W/src/state/context.tsx:5-6; W/src/api/client.ts:160; W/src/lib/artifacts.ts:59-61). 서명 URL은 별도 키(S/config.py:60). worker는 토큰 불필요지만 동일 env 파일 수령(D/mbnas-worker.service.example:14-16). 운영 스크립트도 같은 토큰(scripts/dev_stack.py:66-69; console_stack.sh:44-55). | 없음 (브라우저 위생은 양호) | runner/core 경로 자체가 없고, 생기면 같은 비밀을 재사용할 구조. | `Settings`에 `console_token`(브라우저→gateway), `core_console_token`(gateway→core), `runner_token`(runner→core, runner_id 바인딩), `owner_token`(owner→core, loopback/mTLS)을 **별도 필드**로 두고 라우터별 dependency를 분리해 상호 대체 불가하게 한다. gateway는 브라우저 토큰을 core로 전달하지 않는다. | WP-1(설정 골격), WP-3(gateway/console), WP-2(runner) |
| R2 | TLS 검증, scope·만료 있는 서비스 인증 | 앱 내 TLS 없음; nginx 예시로 종단(D/nginx.example.conf:5-8). 서버→서버 HTTP는 dev_stack 헬스 프로브(http, scripts/dev_stack.py:67)뿐. 토큰에 scope·만료 없음(S/api/auth.py:28-30). 서명 URL만 만료(S/artifacts/registry.py:106-108; TTL S/config.py:61). | 없음 | 서비스 간 클라이언트가 없으므로 검증 코드도 없다. | httpx 클라이언트 `verify=` 필수(기본 True, `MBNAS_*_TLS_CA`), 평문 http는 loopback만 허용(설정 검증기에서 거부). 서비스 토큰은 `scope`+`exp` 포함(HMAC 서명 토큰 또는 core DB 발급·해시 저장·회전 컬럼). | WP-2, WP-3 |
| R3 | 고객 배포에 core 키/DB 자격 없음 | child에서 토큰·서명키·dotenv 제거(S/worker/main.py:61, 288). 그러나 child에 `--db-path`·`MBNAS_DB_PATH`로 SQLite 직접 접근 부여(S/worker/main.py:69-75, 294-295; S/worker/child.py:181-188). gpu_guard는 환경 전체 전달(S/worker/gpu_guard.py:149). ETRI 소스 루트가 설정 기본값(S/config.py:66; S/search/etri_bridge.py:37). | 부분 | 단일 호스트 전제. 분할 후 mbnas_runner가 DB 경로·core 키를 어떤 형태로도 받으면 안 된다. | runner 설정 스키마에서 `db_path` 제거, child 컨텍스트는 파일 기반(DD-A4). `SECRET_ENV`를 core 토큰류·runner 토큰까지 확장. systemd 예시를 역할별 env 파일로 분리(runner 파일에는 runner 토큰과 store 경로만). T8 canary로 검증. | WP-2, WP-7 |
| R4 | job 티켓: job/attempt/stage/digest/만료 바인딩 | DB lease: `UPDATE … RETURNING`으로 1회 임대, `attempt` 증가(S/db/repo.py:403-411), 갱신·완료는 `lease_owner` 일치 조건(S/db/repo.py:414-420, 443-453; S/scheduling/jobs.py:68-72). child는 `--job-id`만으로 식별(S/worker/main.py:294), 진행 메시지는 job_id 일치만 검사(:394). 서명·만료·stage·digest 바인딩 없음. | 없음 (DB fencing은 부분) | 원격 runner가 job_id만 알면 타인의 job에 결과를 밀어 넣을 수 있는 구조가 된다. | claim 응답에 `ticket = HMAC-SHA256(core_ticket_key, job_id\|attempt\|stage\|runner_id\|exp)` 발급. heartbeat/progress/artifact/result는 ticket 필수, core가 `(job_id, attempt)`=lease_epoch 일치·만료·runner_id 검사. artifact 등록은 ticket+본문 digest를 core가 재검증(바이트 확인 후 READY). attempt 증가로 구 ticket 자동 무효. | WP-2 |
| R5 | YAML 안전 파싱: 모듈 allowlist, 인자 타입/shape/크기/연산/시간/메모리 한도, eval/callable/import/pickle/plugin-install/URL 금지 | 모든 로더 `yaml.safe_load`(S/search/genotype.py:97; S/api/model_services.py:501; S/datasets/yaml_safe.py:90; S/worker/child.py:63; S/registry/probe.py:62). data.yaml: 1 MiB·alias≤64·미지 키 무시(S/datasets/yaml_safe.py:20-21, 79-89, 110). 모델 YAML: 확장자+1 MiB(S/api/model_services.py:21, 488), 요청 키 strict(S/api/routes_registry_structure.py:31-40, 64), `activation` 정규식 가드(S/search/structure.py:396-400), 모듈명 식별자 정규식(:370-372), 행 형식·repeats 양의 정수(S/search/genotype.py:58-65). | 부분 → **치명 갭** | (a) 모듈 allowlist 없음 → `globals()[m]`/`getattr(torch.nn, …)`/`getattr(torchvision.ops, …)` 해석 후 호출(U/nn/tasks.py:2127-2134, 2228); 비제한 모드에서는 타입 검사 비활성(:2135-2137; `restricted()`는 `ULTRALYTICS_SAFE_LOAD` 또는 진행 중 weights_only 로드에서만 참 :1677, U/utils/__init__.py:73). 위험 전역: `check_requirements`(pip), `check_file`(URL), `torch_load`(pickle), `yaml_model_load`(임의 YAML 경로). 문자열 인자는 `locals()[a]`로 파서 지역변수(`d`, `ch`…) 바인딩(:2140-2141). (b) 모델 YAML alias 상한 없음; `canonical_sha256`/`yaml_text`가 `json.dumps`로 공유 참조를 완전 전개(S/util/canon.py:41-45)→지수 전개 가능(이론, 확인 못 함). (c) 행 수·채널·repeats·nc·args 크기 상한 없음. (d) `input_size` 상한 없음(S/api/model_services.py:414-420; S/search/structure.py:349-350). (e) `eval(act)`(U/nn/tasks.py:2062) 가드는 `check_structure`에만 있고 `inspect_genotype` 직접 호출자(S/search/space.py:340)에는 없음(현재는 서버 생성 genotype). | mbnas 측 `MODULE_ALLOWLIST`(ultralytics `base_modules`/`repeat_modules`/head 집합 + ETRI 3종 + `nn.Upsample` 등 명시 소수)로 **빌드 전** 선검사, 미등록은 `BLOCK_UNSUPPORTED` FAIL. 인자 스키마: 스칼라 int/float/bool/str(허용 문자열은 `nc` 등 열거형)만, 리스트 depth≤2, 정수≤2^16, 채널≤4096, repeats≤64, 행≤256, nc≤10,000, `input_size`≤설정 상한(기본 1280). alias≤64 + 원문 바이트 해시. 검사 프로세스 env에 `ULTRALYTICS_SAFE_LOAD=1 YOLO_AUTOINSTALL=false YOLO_OFFLINE=1`. `activation` 가드를 `Genotype` 생성 시점으로 이동. 음성 테스트(§4) 추가. | WP-1(mbnas_model_runtime genotype/structure), WP-3(API), WP-8(테스트) |
| R6 | 고객 operator manifest는 "주장"으로 취급 | ETRI mixed pool 등록 결과(`parser_source_sha256`, 버전)를 관측 데이터로 기록(S/search/genotype.py:233-237; yolo_ga_runtime/mixed_blocks.py:250-256), etri_bridge가 import 파일 해시 수집(S/search/etri_bridge.py:149-150). child는 `register_hook` 문자열로 훅 지정(S/worker/child.py:52, 68). 기대값 대조 없음. 체크포인트 digest는 보고값을 믿지 않고 재계산(S/worker/main.py:426-435) — 좋은 선례. | 부분 | 기록은 있으나 검증·라벨이 없다. | runner 결과의 `runtime_manifest`(패키지 버전, operator 모듈 sha256, parser sha)를 core가 `claimed`로 저장하고 릴리스 매니페스트(core 고정표)와 대조해 `verified\|mismatch\|unknown` 라벨을 공개 DTO에 노출. mismatch 처리(승급 불가 등)는 core 정책. | WP-2(프로토콜), WP-4(DTO), WP-5 |
| R7 | CPU 구조 검사: 자원 제한·비특권 서브프로세스, 전역 상태 정리, 동시성 격리 | API 프로세스 내 동기 실행(S/api/routes_registry_structure.py:62-73 → S/api/model_services.py:493-524 → S/search/structure.py:408-410 → S/search/genotype.py:454-462). 자원 제한·타임아웃 없음. 전역 상태: `Conv.default_act`는 ultralytics가 복원(U/nn/tasks.py:2058, 2242)하나 `torch.backends.mha` fastpath 토글은 프로세스 전역(S/search/genotype.py:478-481, 498); ETRI 등록은 `tasks.parse_model` 자체 교체(yolo_ga_runtime/mixed_blocks.py:263; S/api/model_services.py:508-513) → 이후 모든 검사에 잔류. 동시 요청은 같은 프로세스 스레드풀. | 없음 | OOM/CPU 독점/전역 오염이 API 가용성에 직결. restricted 모드에서도 `nn.Linear` 거대 차원 같은 `nn.Module` 서브클래스는 통과(U/nn/tasks.py:2135-2137)하므로 RLIMIT는 allowlist와 무관하게 필요. | 전용 러너 `python -I -S -m mbnas_model_runtime.structure_cli --in <json> --out <json>`: `preexec_fn`에서 `RLIMIT_AS`(예 4 GiB)·`RLIMIT_CPU`·`RLIMIT_NOFILE`·`RLIMIT_FSIZE`, `timeout=60`, env 최소화(비밀 제거, OFFLINE/SAFE_LOAD), cwd 임시 디렉터리, 가능하면 `bwrap --unshare-net --ro-bind`/`unshare -rn`(호스트 동작 확인: TASK_STATE), 비특권 uid. 동시성 세마포어(기본 1~2). 결과 JSON은 스키마 검증 후 채택. owner CPU worker(DD-A4)도 같은 러너 사용. | WP-1(러너), WP-3(API), WP-7(bwrap 문서/설치) |
| R8 | 원격 artifact: id/digest 참조, 고객 경로 금지, 바이트 검증 | 요청은 id만(S/api/routes_files.py:57-64), run dir 내부 realpath 강제(S/artifacts/registry.py:166-190), 다운로드 시 sha256 재계산·불일치 409(S/api/routes_files.py:67-75), 등록 시 digest·`..`·심링크 거부(S/artifacts/registry.py:140-154), 체크포인트 보고는 run dir 내부+크기+digest 재계산(S/worker/main.py:418-444). 모두 동일 호스트 FS 전제. | 부분 | 원격 runner 보고는 경로가 아니라 `(runner_id, artifact_id, digest, bytes, kind)`여야 하고(DD-A6) 바이트 검증은 업로드/relay 시점에 필요. | core `POST /core/v1/runner/jobs/{id}/artifacts` 본문 스키마 `extra=forbid`·경로 필드 없음. "존재 보고"만으로 READY 금지: store에 바이트가 들어와 digest가 확인된 뒤 READY. server 다운로드는 store 스트리밍+digest 대조(현 409 LOAD_FAILED 유지). | WP-5, WP-2 |
| R9 | 해시 3종(원문 바이트/정규 config/빌드 구조) + 알고리즘·버전 | `yaml_hash` = `sha256:`+canonical JSON(S/util/canon.py:53-55; S/search/genotype.py:143-145); `architecture_hash` scheme `mbnas-structural/1`(S/adapters/common.py:13, 16-25); `signature_hash` `mbnas-arch-sig/1`(S/search/genotype.py:40, 365, 521). 원문 바이트 해시는 서버가 쓴 architecture YAML artifact의 파일 sha256(S/orchestration/engine.py:954-958 → register_file)뿐, **사용자 제출 원문**의 해시는 없음. | 부분 | 원문 해시 부재, canonicalization 버전이 해시 입력/필드에 없음. | StructureReport/Artifact에 `source_yaml_sha256`(원문 바이트), `config_hash`(=yaml_hash, `canonicalization: mbnas-genotype/1` 필드 동반), `architecture_hash`(scheme 동반) 3종을 항상 함께 기록. | WP-1(contracts), WP-4(DTO) |
| R10 | argv-only 서브프로세스, 사용자 입력 셸 문자열 금지 | S/worker/main.py:293-298(list, `close_fds`), S/worker/gpu_guard.py:150, scripts/dev_stack.py:81-102(설명 22-25), console_stack.sh:115-117(배열 인용), 130-131(`bash -c` + 위치 인자), 토큰은 curl `-K -` stdin(:50-55). argv에 들어가는 사용자 유래 값: `job["id"]`(서버 생성). | 충족 (R5 조건부) | ultralytics `check_requirements`는 list argv지만 YAML로 촉발 가능(R5). console_stack.sh:112 `. "$ENV_FILE"`는 env 파일을 셸로 실행(소유자 통제 파일). | 공개 배포 스크립트는 env 파일을 source하지 않고 `MBNAS_ENV_FILE` 경로만 전달. | WP-7 |
| R11 | 공개 오류 = 안정 코드+위치+request id; owner 로그에 토큰 없음 | Error 본문 `{code,message,errors?,request_id}`(S/api/errors.py:29-33), 500 일반 메시지+서버 로그(:70-73), request id 형식 제한·에코(S/api/auth.py:19, 47-53), 리댁션 필터(S/util/log_redact.py:18-26, 67-75; S/api/app.py:41), 경로 제거(S/search/structure.py:64-68). | 부분 | `_unavailable(... f"{type(exc).__name__}: {exc}")`가 예외 문자열(OSError 경로 등)을 노출(S/api/model_services.py:403-404, 475-476, 519-520); `RESPONSE_CONTRACT_VIOLATION`이 내부 필드 오류를 본문에 포함(S/api/deps.py:55-59); `check_server_path` 메시지에 realpath+허용 루트 목록(S/util/paths.py:96-97 → S/protocol/validator.py:782, S/api/model_services.py:486); `location` 없음; worker/child stderr 로그(S/worker/main.py:283, 296)는 필터 미적용(토큰이 env에서 제거되어 현재는 안전). | 공개 오류는 `code`, `location`(예 `structure_check.parse`), `request_id`만; 예외 문자열은 owner 로그(request_id 연결). gateway→core 오류 relay는 코드 표로 재작성. gateway·runner·core 모두에 공통 redaction 필터. | WP-3, WP-2 |
| R12 | 공개 DTO vs owner 감사 DTO allowlist (API/SSE/OpenAPI/TS/번들/sourcemap/CSV/JSON/ZIP/로그) | `prune()` null 제거만·미지 키 통과(S/api/contract.py:93-102); pydantic `extra="allow"`(S/schemas/records.py:33, 358; S/schemas/archive.py:16); snapshot `audit`·`lineage` 전량(S/api/routes_runs.py:278-289); SSE 동일 prune(S/api/routes_stream.py:30); export ZIP은 키 선택형(S/export/bundle.py:45-47, 104-106)이나 `resolved_protocol` 전체·lineage 포함; artifact metadata만 allowlist(S/artifacts/registry.py:214-218); 웹 sourcemap 비활성(W/vite.config.ts:20); archive CSV는 외부 파일을 파싱해 전달(S/archive/scanner.py:613-615). | 없음 | deny-by-default 투영이 없다. | WP-4: 공개 OpenAPI와 owner OpenAPI 분리 생성, 공개 투영은 "스키마에 있는 키만 복사"(`additionalProperties:false` 강제)로 `prune` 대체, `audit` 공개분은 `evidence` 서브셋으로 명시, SSE 이벤트 타입별 allowlist, export ZIP 멤버별 allowlist 표, TS 재생성, §5 canary로 검증. | WP-4, WP-6 |
| R13 | 반복 조회 정책 추론에 대한 비율 제한·쿼터·감사 | 없음(검색 0건). 존재 상한: 본문 1 MiB(S/api/limits.py:13), SSE replay 500/커서 clamp(S/events/bus.py:24; S/api/routes_stream.py:35-48), archive 캐시 상한(S/archive/scanner.py:89, 213, 232), compare≤6(S/archive/routes.py:124-125), 파일 8 MiB(S/archive/views.py:19, 870-871). SSE 구독 무제한·구독당 DB 연결(S/events/bus.py:82). 접근 로그는 uvicorn access log뿐. | 없음 | 고비용 라우트(validate, structure-check) DoS와 candidate/lineage 반복 조회에 의한 정책 추론 모두 무방비. | gateway 토큰버킷(기본 10 rps, burst 50), 고비용 라우트 별도 쿼터(분당 N, 동시 1), SSE 동시 연결 상한(토큰당 4), 민감 라우트(candidate 상세·lineage·generation 목록) 조회를 core 감사 테이블에 기록(토큰 id, 라우트, 대상 id, 시각), 임계 초과 429+owner 알림. | WP-3, WP-4(감사 DTO), WP-8 |

### 1.1 §8 목록 밖 추가 발견

| # | 발견 | 근거 | 제안 | WP |
|---|---|---|---|---|
| F1 | 소유자 호스트 경로·IP 하드코딩이 공개 코드에 존재 | S/config.py:20, 26-27, 54, 56, 66; S/search/etri_bridge.py:37; S/protocol/presets.py:38, 45-46; S/adapters/rfdetr_adapter.py:55; application/scripts/console_stack.sh:23, 41; application/MBNAS_server/scripts/{run_tests_host.sh:9-14, bench_host_gpu.py:36-37, smoke_api_only.py:46, probe_registry.py:26-27, download_weights.py:54-55} | 기본값을 `runtime/` 상대(DD-A7)로 바꾸거나 필수 설정으로 전환(미설정 시 기동 거부). 해당 문자열을 §5 canary의 고정 needle로 등록. | WP-1, WP-7 |
| F2 | 동적 코드 로딩: 설정 루트에서 `sys.path` 삽입 import + ultralytics `parse_model` 소스 재작성·`exec` | S/search/etri_bridge.py:107-109, 130-142; yolo_ga_runtime/mixed_blocks.py:229-236, 243-245, 263 | DD-A2 vendoring 시 `exec` 없는 정적 구현으로 대체(ultralytics에 custom 모듈을 `base_modules`/`repeat_modules`에 넣는 공식 API가 없으므로 자체 `parse_model` 복제본 또는 채널 추론을 mbnas가 수행). 결정 D4. | WP-1 |
| F3 | `torch.load(weights_only=False)` 1곳 | S/adapters/rfdetr_adapter.py:343 (직전 sha256 대조 :339; PTL 네이티브 재개 체크포인트) | DB 기록 digest 일치 시에만 로드되므로 변조는 탐지되지만 "no pickle"은 아님. 결정 D5. | WP-1 |
| F4 | `/structure-check` 허용 루트에 `etri_nas_root`·`weights_dir` 포함 | S/api/model_services.py:482-483; T/api/test_registry_structure.py:220 | 소유자 호스트에서 ETRI 소스 트리 내 임의 YAML 구조를 반출하는 오라클. 분할 후 `etri_nas_root` 제거, 사용자 YAML 루트는 `output_roots`(+업로드 디렉터리)로 한정. | WP-1, WP-3 |
| F5 | 완화 플래그가 API 프로세스에 없음 | S/adapters/ultralytics_adapter.py:83(`YOLO_AUTOINSTALL=false`는 adapter 내부), scripts/search_dry_run.py:30-31 | R5/R7 러너 env에 포함. | WP-1 |
| F6 | health가 소스 트리 해시·versions·devices·registry 경로 진단 노출 | S/api/routes_meta.py:56-69 | 고객 호스트 정보라 허용 가능하나 gateway 공개 health는 `status/version/request_id`로 최소화하고 상세는 owner health로. | WP-3 |
| F7 | 서명 URL HMAC은 `artifact_id.exp`만 바인딩 | S/artifacts/registry.py:14-16, 78-84; 라우트가 run 범위를 별도 검사 S/api/routes_files.py:62-65 | 현 설계로 안전(id 전역 유일). 분할 후 public opaque ID로 바뀌면 서명 입력도 공개 ID 기준으로 재정의(WP-4 ID 매핑과 함께). | WP-4, WP-5 |

---

## 2. 상세 근거

### 2.1 ultralytics 8.4.173 `parse_model` 경로 (R5·R7의 핵심)

사용자 YAML은 `check_user_model`(S/api/model_services.py:493-524)에서 `yaml.safe_load`(501) → `derive_model_scale`(504) → `Genotype.from_yaml_dict`(505; `canonical_yaml` S/search/genotype.py:68-76는 `str(module)`만 수행 :65) → `check_structure`(517) → 선언 검사 통과 시 `inspect_genotype`(S/search/structure.py:409) → `DetectionModel(copy.deepcopy(yaml), ch=3, nc=…)`(S/search/genotype.py:439-443, 456) 순으로 흐른다. 선언 검사의 모듈명 규칙은 `(?:torch\.)?(?:nn\.)?[A-Za-z_][A-Za-z0-9_]*`(S/search/structure.py:370-372)로, `check_requirements`·`torch_load`·`check_file`은 모두 통과한다.

설치본 `parse_model`(U/nn/tasks.py:2030)의 행 처리:

| 단계 | 행 | 내용 |
|---|---|---|
| 모드 | 2057 | `restricted = _SafeLoad.restricted()` = `SUPPORTED and (SAFE_LOAD or thread.active)`(1677). `SAFE_LOAD`는 `ULTRALYTICS_SAFE_LOAD` env opt-in(U/utils/__init__.py:73). mbnas API 프로세스는 설정하지 않음 → **비제한 모드**. |
| activation | 2059-2062 | 비제한이면 `eval(act)`. mbnas는 `check_structure` 경로에서만 정규식으로 선차단(S/search/structure.py:396-400). 제한 모드의 `_SafeLoad.activation`은 AST 기반 `[torch.]nn.<Class>(literal…)`만 허용(1753-1789). |
| 모듈 해석 | 2127-2134 | `nn.` 접두 → `getattr(torch.nn, …)`; `torchvision.ops.` 접두 → `getattr(__import__("torchvision").ops, …)`; 그 외 → `globals()[m]`(tasks 모듈 전역 117개). |
| 타입 검사 | 2135-2137 | **제한 모드에서만** `nn.Module` 서브클래스가 아니면 TypeError. |
| 인자 해석 | 2138-2141 | 문자열 인자: `locals()[a] if a in locals() else ast.literal_eval(a)`(ValueError 억제). `d`·`ch`·`layers`·`save` 등 파서 지역변수가 인자로 바인딩될 수 있다. |
| 생성 | 2228 | `m(*args)` (n>1이면 `nn.Sequential(*(m(*args) …))`). 미등록 `m`은 2225-2226의 `else` 분기를 거쳐 그대로 호출된다. |

tasks 전역 중 부작용 있는 호출 가능 객체(U/nn/tasks.py:93 `from ultralytics.utils.checks import REMOTE_FILE_PREFIXES, check_file, check_requirements, check_suffix, check_yaml`; :106 `from ultralytics.utils.patches import torch_load`; :2246 `yaml_model_load`; :1834 `torch_safe_load`; :1981 `load_checkpoint`):

- `check_requirements(requirements, …, install=True)`(U/utils/checks.py:527): `install and AUTOINSTALL`(:625, 기본 True U/utils/__init__.py:71)이면 `attempt_install`이 pip/uv를 `subprocess.check_output`으로 실행(:596-619). → **plugin-install 금지 위반 경로**.
- `check_file(file, download=True)`(:775): `REMOTE_FILE_PREFIXES`로 시작하면 `safe_download`(:818-826). → **URL 금지 위반 경로**.
- `torch_load(*args)`(U/utils/patches.py:184-185): `weights_only=False` 기본. → **pickle 금지 위반 경로**(서버가 읽을 수 있는 임의 파일).
- `yaml_model_load(path)`(U/nn/tasks.py:2246-2266): 임의 경로 YAML을 읽고 `check_yaml`(다운로드 가능) 경유.

호출 뒤 2233행의 `m_.np = sum(…parameters())`에서 AttributeError가 나므로 응답은 `STRUCTURE_BUILD_FAILED`류 FAIL로 보이지만 부작용은 이미 발생한다. 이 경로는 코드 추적으로만 확인했고 실행하지 않았다(확인 못 함). 제한 모드로 바꾸더라도 `nn.Linear`·`nn.Conv2d` 같은 임의 `nn.Module`은 통과하므로(2135-2137) 거대 차원 할당에 의한 OOM은 **RLIMIT 서브프로세스(R7)** 없이는 막을 수 없다. mbnas 측 allowlist(R5)와 프로세스 격리(R7)는 서로 대체재가 아니라 둘 다 필요하다.

### 2.2 인증·자격 (R1–R4)

- `bearer_ok`는 헤더 스킴 `bearer` + `hmac.compare_digest`(S/api/auth.py:24-30). 토큰은 `app.state.api_token` 하나(S/api/app.py:48). 쿠키·쿼리 토큰은 거부되며 테스트가 고정한다(T/qa/test_security_negatives.py:177-193).
- 아티팩트 다운로드만 bearer 또는 서명 URL(S/api/routes_files.py:44-54). 서명은 `sign(key, "<artifact_id>.<exp>")`(S/artifacts/registry.py:78-84), 검증은 서명 먼저·만료 나중·bytes 비교(:87-108; 비ASCII 500 회귀 수정 :100-105).
- 서비스 간 인증·TLS 클라이언트는 현재 코드에 존재하지 않는다. DD-A3의 `/core/v1/{console,runner,owner}` 3분할이 R1의 자격 3분할과 1:1로 대응하므로, 라우터별 dependency(`require_console_token`/`require_runner_ticket`/`require_owner_token`)로 구현하면 된다.
- DB lease는 core 내부에서 그대로 쓰되(DD-A4) runner에는 ticket만 준다. `renew_lease`/`finish_job`의 `lease_owner` 조건(S/db/repo.py:414-420, 443-453)을 ticket의 `runner_id`·`attempt`와 묶으면 fencing이 유지된다.

### 2.3 구조 검사 프로세스 모델 (R7)

`inspect_genotype`은 빌드(S/search/genotype.py:456) → 훅 등록(472-476) → `torch.zeros(1,3,imgsz,imgsz)` 전방(483-491)을 **호출 스레드에서** 수행한다. `count_flops=False`로 호출되지만(S/search/structure.py:409) 전방 자체의 메모리·시간은 YAML이 결정한다. `_valid_input_size`는 32 배수 하한만 검사한다(S/api/model_services.py:414-420). 검색 프로세스(S/search/space.py:328-341)도 같은 함수를 쓰므로 owner CPU worker에도 같은 러너를 적용하는 것이 맞다. ETRI 등록은 프로세스 전역 `tasks.parse_model`을 교체하고(yolo_ga_runtime/mixed_blocks.py:263) `tasks`·`modules` 네임스페이스에 클래스를 심는다(:243-245) — 서브프로세스 격리가 "전역 상태 정리"를 가장 단순하게 해결한다.

### 2.4 경로·아티팩트·archive (R8)

- 사용자 경로 입력점은 8곳 모두 `check_server_path`를 거친다(S/protocol/validator.py:306, 779; S/api/model_services.py:482; S/training/selection.py:71, 170; S/training/context.py:60; S/search/handlers.py:119; S/benchmark/handlers.py:43). 규칙: 절대경로·NUL·`~`·placeholder·`..` 거부 후 realpath가 허용 루트 아래인지(S/util/paths.py:41-54, 93-97).
- 아티팩트: `resolve_file`은 NOT_READY 선거부, relpath 절대/`..` 거부, run dir이 output root 아래인지, 파일 realpath가 run dir 아래인지 순서로 검사(S/artifacts/registry.py:175-190).
- archive: run id `[A-Za-z0-9._-]{1,128}`·`.` 시작 금지(S/archive/analysis.py:35-37), 루트 탈출 심링크 skip(S/archive/scanner.py:134-137), 파일은 고정 상대명+realpath 봉쇄(:279-286), 종류는 enum(S/archive/views.py:23, 861-862), 8 MiB 상한(:19, 870-871), `no-store`+`nosniff`(S/archive/routes.py:19).

### 2.5 해시 (R9)

`canonical_json`은 `sort_keys`, `ensure_ascii`, `allow_nan=False`(S/util/canon.py:41-45). `yaml_hash`는 텍스트 정체성이 아니라 canonical dict 정체성이며 모듈 docstring도 그렇게 정의한다(S/search/genotype.py:7). 원문 바이트 해시는 사용자가 보낸 파일에 대해 존재하지 않으므로 "제출물 추적"이 불가능하다. `architecture_hash`는 모듈 클래스 순서+state 이름/shape/dtype(S/adapters/common.py:16-25)로, 학습 체크포인트와 같은 함수를 써서 한 후보에 하나의 해시가 유지된다(S/search/genotype.py:17-21).

### 2.6 오류·로그 (R11)

리댁션은 `uvicorn.access`/`uvicorn.error`/`uvicorn` 로거와 핸들러에 필터를 붙인다(S/util/log_redact.py:19, 67-75). 쿼리 키 `token|access_token|sig|exp`와 `Authorization` 값을 `***`로 바꾸며(:18, 22-26) 실제 uvicorn 서버로 검증하는 테스트가 있다(T/api/test_access_log_redaction.py:30-66). 그러나 애플리케이션 로거(`mbnas.api`, `mbnas_server.api`)에는 필터가 없다(S/api/errors.py:13; S/api/deps.py:20) — 현재 그 로거에 토큰을 쓰는 코드는 없지만, 분할 후 gateway/runner 클라이언트가 요청 헤더를 디버그 로깅하면 바로 누출되므로 공통 필터 설치를 권한다.

### 2.7 운영 스크립트 (R10)

`console_stack.sh`는 토큰을 `grep|cut|sed`로 읽어(44-47) curl 설정을 stdin으로 넘기므로(`-K -`, 54) `ps`에 노출되지 않는다. API 기동은 서브셸에서 `set -a; . "$ENV_FILE"; set +a` 후 `exec setsid nohup "$PY" -m uvicorn …`(111-118)으로 argv 배열이다. 웹은 `bash -c '… --host "$0" --port "$1"' "$WEB_HOST" "$WEB_PORT"`(130-131)로 값이 위치 인자로 전달된다. `dev_stack.py`는 `subprocess.Popen(argv, …, start_new_session=True, close_fds=True)`(84-85)이고 argv는 리스트 함수가 만든다(90-102). 토큰은 헬스 프로브 헤더에만 쓰이고 출력에 포함되지 않는다(66-78, 181).

---

## 3. DD-A1..A9와의 충돌·주의

| DD | 관계 | 비고 |
|---|---|---|
| DD-A2 | 주의 | vendoring 대상 `mixed_blocks.register_ultralytics_blocks`가 `exec` 기반(F2). 공개 패키지(mbnas_model_runtime)에 `exec`를 남기면 "no exec/eval" 요건과 어긋난다. 정적 구현으로 바꾸는 결정 필요(D4). |
| DD-A3 | 일치 | 제어면 3분할 = 자격 3분할(R1). 투영을 core에서 수행한다는 결정은 R12의 deny-by-default 투영 위치와 일치. |
| DD-A4 | 경계 명시 필요 | "기존 DB lease 재사용"은 core 내부에 한함. runner/child에 `db_path`를 주는 현 방식(S/worker/main.py:294-295)은 분할 후 금지(R3). |
| DD-A5 | 일치 | resolved protocol 전체가 envelope에 들어가므로 runner 측 로그·오류에도 R11 리댁션 필요(비밀은 아니지만 고객 경로). |
| DD-A6 | 결정 필요 | "runner→server 인증 업로드"의 자격이 R1의 runner 토큰인지 별도인지(D3). |
| DD-A7 | 일치 | `runtime/` 하위 기본값으로 F1 해소 가능. |
| DD-A8 | 일치 | 제거되는 `fitness.ts`·handoff 원문은 §5 canary 대상. |
| DD-A9 | 영향 없음 | — |

---

## 4. 기존 테스트 커버리지

| 영역 | 테스트 | 덮는 것 | 덮지 않는 것 |
|---|---|---|---|
| 모든 라우트 bearer·CORS·request id | T/api/test_auth_cors.py:9-36, 39-41, 44-51, 54-69, 72-76, 79-84, 87-90 | 401/403 코드·`WWW-Authenticate`, 와일드카드 거부, 무토큰 기동 거부, request id 에코/치환, `/docs` 비활성 | 서비스 토큰·scope·만료(존재하지 않음) |
| 경로 봉쇄(protocol) | T/qa/test_security_negatives.py:43-110 | traversal/placeholder/relative/NUL/sibling-prefix/심링크 체인·루프/스플릿 내 심링크 | `/structure-check`의 traversal은 별도(T/api/test_registry_structure.py:195-209: dataset_root/not_yaml/too_big/not_mapping) |
| YAML 실행 금지 | T/qa/test_security_negatives.py:112-137; T/search/test_structure_adversarial.py:22-41 | data.yaml python 태그·2 MiB·`download` 키; 모델 YAML `activation` 표현식·식별자 아닌 모듈명 | **식별자 형태의 위험 모듈명**(`check_requirements`, `torch_load`, `check_file`, `nn.Linear` 거대 차원), alias 폭탄, 행 수/채널 상한, `input_size` 상한 |
| 본문 크기 | T/qa/test_security_negatives.py:141-173 | Content-Length·chunked·인증 전 413 | SSE 연결 수, 고비용 라우트 비율 |
| 토큰 누출 | T/qa/test_security_negatives.py:177-204, 269-308, 320-360 | URL/쿠키/헤더 변종 거부, 오류 본문·export·로그·디스크에 비밀 없음, 실제 `.env` 값 전수 grep(zip 내부 포함) | canary 마커(§5), base64/hex 변형, 바이너리(sqlite 제외됨 :306) |
| 아티팩트 | T/qa/test_security_negatives.py:241-266; T/api/test_artifacts_export.py:38-124; T/api/test_artifact_signature_non_ascii.py:29 | id 추측 404/401 구분 없음, 위조 서명 403 동일, run 범위, 심링크/수정/NOT_READY, 비ASCII 서명 | 원격 업로드(없음) |
| archive | T/archive/test_security.py:20-86, 97-177, 187-200, 214-258 | id/kind/raw dot segment, 심링크 4종, 8 MiB, 로그 tail, GET-only, 읽기 전용(트리 digest), 패키지 내 write/exec 코드 부재, 응답에 비밀 없음 | CSV 내용 필터(없음) |
| 로그 리댁션 | T/api/test_access_log_redaction.py:30-66; T/archive/test_security.py:261-263 | 실제 uvicorn 서버 access/error 로그 | 애플리케이션 로거, runner/gateway 로그(없음) |

추가해야 할 음성 테스트(WP-8): (1) 모델 YAML 모듈명 allowlist 밖 식별자 → 빌드 전 FAIL이고 부작용 마커 파일/네트워크 시도 없음(소켓 차단 fixture); (2) `input_size` 상한 초과 422; (3) alias 65개 YAML 거부; (4) 행 257개·채널 4097 거부; (5) 구조검사 서브프로세스 RLIMIT 초과 시 `STRUCTURE_CHECK_FAILED`이고 API 프로세스 RSS 불변; (6) 동시 구조검사 N개에서 세마포어 대기; (7) runner ticket 위조/만료/attempt 불일치 403; (8) gateway→core 토큰으로 `/core/v1/owner` 접근 403; (9) 공개 투영에 미지 키 투입 시 제거되는지(prune 교체 검증).

---

## 5. T8 누출 canary 계획

목적: owner 전용 산출물에만 존재해야 하는 마커가 **어떤 배포 산출물·공개 응답·공개 로그에도 없음**을 기계적으로 증명한다. 기존 `test_real_env_secrets_are_absent…`(T/qa/test_security_negatives.py:320-360)의 "실제 값 전수 grep + zip 내부" 방식을 일반화한다.

### 5.1 마커 설계

- 형식: `MBNAS-CANARY-<AREA>-<16hex>`(16hex는 빌드마다 `secrets.token_hex(8)`). 접두 `MBNAS-CANARY-`로 전수 검색하고, 매니페스트로 AREA별 기대 위치를 대조한다.
- 매니페스트: `docs/internal/canary/CANARY_MANIFEST.json`(owner 전용, 어떤 ZIP에도 포함 금지). 항목: `{area, value, planted_in: [paths], expected_in: [owner-bundle 멤버], must_not_in: ["public_zip","registration_zip","web_dist","public_openapi","public_api_responses","gateway_logs","runner_logs"]}`.
- 변형 needle: 원문, base64(standard/urlsafe), hex, URL-encoded. gzip/zip/whl/tar.gz 멤버는 풀어서 검색. sqlite 파일은 원시 바이트로 검색(기존 테스트가 제외한 `.sqlite3` 포함).

### 5.2 심는 위치 (owner 전용 파일 → 마커)

| AREA | 심는 곳(분할 후 경로) | 형태 |
|---|---|---|
| POLICY_PRIOR | `mbnas_core/search/space.py` prior 테이블의 더미 키 | dict key 문자열 |
| POLICY_REPAIR | `mbnas_core/search/mixed_pool.py` `_legal_choices` 더미 항목 | 리스트 원소 |
| FITNESS | `mbnas_core/protocol/fitness.py`·`promotion.py` 모듈 docstring 끝 문장 | docstring |
| OWNER_DTO | owner 감사 DTO 샘플(`audit.proposal.canary_owner_field`)로 테스트 DB에 시드 | DB 행 → 공개 snapshot/SSE/export/CSV에 없어야 함 |
| OWNER_SECRET | owner env 예시 `MBNAS_OWNER_TOKEN=MBNAS-CANARY-OWNER_SECRET-…`, `MBNAS_CORE_TICKET_KEY=…` | env 파일(테스트 전용 값) |
| DB_CRED | core DB 파일 경로 문자열과 ticket key를 runner/gateway 프로세스 env·argv·로그에서 검색 | 프로세스 `/proc/<pid>/environ`·`cmdline` |
| OWNER_DOC | `application/docs/**`, handoff 문서, PLAN/CONTRACTS_v2 첫 줄 | 텍스트 |
| OWNER_OPENAPI | owner OpenAPI 스키마명 `CanaryOwnerOnly` | JSON |
| OWNER_LOG | owner worker 기동 로그 1줄 | 로그 |
| WEB_PRIVATE | DD-A8로 제거되는 `fitness.ts` 공식 상수·handoff 원문 embed 문자열 | TS 소스 → `dist/**` 번들·`.map`에 없어야 함 |
| HOST_FACT(고정) | `/data/mgjeong`, `/root/detection`, `147.46.114.253`, `pc2300`, `mgjeong`, 원본 `.env`의 실제 비밀 값 | 상수 needle(마커 아님) |

### 5.3 검색 대상 (must_not_in)

1. 공개 ZIP 3종(public, registration, 그리고 owner bundle은 **양성 대조군**: 마커가 반드시 있어야 스캐너가 유효).
2. 빌드 산출물: 각 패키지 wheel/sdist, `__pycache__`, `MBNAS_web/dist/**`(js/css/html/map), 생성된 `types.gen.ts`, 공개 `openapi.json`+history.
3. 런타임 응답: core+gateway+runner를 테스트 모드로 띄우고(DB에 OWNER_DTO 시드) 공개 라우트 전수 호출(health, registry, runs, snapshot, candidates 상세, generations, lineage, events SSE replay from 0, export ZIP 멤버 전부, archive 전 라우트·files 전 kind, CSV). `/core/v1/console`·`/core/v1/runner` 응답도 포함(gateway가 받는 것 자체에 owner 필드가 없어야 함, DD-A3).
4. 로그: gateway·runner 프로세스 stdout/stderr·파일 로그, runner child stderr, uvicorn access log. owner 로그는 `expected_in`.
5. 프로세스: runner·gateway의 `/proc/<pid>/environ`, `cmdline`에 OWNER_SECRET/DB_CRED 부재.
6. git: 공개 저장소를 만들 경우 전체 히스토리(`git log -p`)에 마커 부재.

### 5.4 절차와 판정

1. `scripts/canary_plant.py`(owner 전용, TARGET_ROOT 빌드 트리에서만 실행)가 마커 생성·삽입·매니페스트 기록. 삽입은 멱등(기존 마커 교체).
2. 빌드·패키징(WP-7) → `scripts/canary_scan.py --manifest … --zip … --dist … --api http://127.0.0.1:8710 --core http://127.0.0.1:8700 --logs …`가 §5.3 전수 스캔, 결과 JSON(`hits: [{needle_area, variant, location}]`)을 `REVIEW_LOG.jsonl`에 기록.
3. 판정: (a) `must_not_in` 히트 0건, (b) owner bundle·owner 응답에서 AREA별 히트 ≥1(양성 대조), (c) HOST_FACT·실제 `.env` 값 히트 0건(공개 산출물), (d) 스캔 파일 수가 기대 하한 이상(기존 테스트의 `scanned > 50` 방식). 하나라도 실패면 T8 FAIL이고 ADMIN_BOUNDARY/PUBLIC_ZIP 상태는 미충족 유지.
4. 마커는 테스트 빌드에서만 심는다(릴리스 빌드에는 HOST_FACT·실제 비밀 스캔만 적용). 결정 D9 참조.

---

## 6. 결정 필요 사항

| # | 결정 | 선택지 | 권고 |
|---|---|---|---|
| D1 | 사용자 YAML 모듈 허용 정책 | (a) mbnas allowlist만 (b) `ULTRALYTICS_SAFE_LOAD=1`만 (c) 둘 다 + 인자 스키마 | **(c)**. (b) 단독은 `nn.Module` 서브클래스 전부를 허용하고 `locals()` 바인딩도 남는다. |
| D2 | 구조검사 격리 수준 | (a) 서브프로세스+RLIMIT+timeout+env 최소화 (b) (a)+`unshare -rn` (c) (a)+`bwrap` ro-bind·비특권 uid | 최소 **(b)**를 기본, 설치 가능 시 (c). 네트워크 차단은 R5 URL/pip 경로의 2차 방어. |
| D3 | 서비스 자격 체계 | (a) 역할별 정적 토큰(해시 저장·회전) (b) 단기 HMAC 티켓(job)+정적 등록 토큰(runner) (c) mTLS | **(b)**: runner 등록 토큰은 정적, job 작업은 ticket(R4), gateway→core는 정적 scope 토큰, owner는 loopback 전용+토큰. DD-A6 업로드 자격은 runner 토큰+ticket으로 통일. |
| D4 | ETRI parse_model 확장 방식(DD-A2) | (a) 현 `exec` 패치 vendoring (b) 자체 `parse_model` 복제본(ultralytics 버전 고정 8.4.173) (c) ultralytics 호출 전 mbnas가 채널을 미리 계산해 custom 모듈 인자로 주입 | **(b)**(버전 고정 전제; 파서 sha 기록 유지). 공개 패키지에 `exec` 금지. |
| D5 | rfdetr `torch.load(weights_only=False)` | (a) 허용(digest 대조 후, owner 결정 기록) (b) 공개 runner에서 네이티브 재개 비활성(weights_only 경로만) | 보안 요건 문자 그대로면 (b). 기능 손실(재개 불가) 수용 여부는 설계자 판단. |
| D6 | 소유자 경로 기본값 | (a) 삭제·필수 설정 (b) `runtime/` 상대 기본값(DD-A7) | **(b)** + 절대경로 외부 루트는 명시 설정만. archive 기본 루트는 빈 목록. |
| D7 | 비율 제한 수치 | rps/burst, 고비용 라우트 동시성, SSE 연결 상한, 감사 임계 | 제안값(§1 R13)으로 시작하고 T3/T8에서 조정. |
| D8 | 공개 DTO 권위 | (a) 공개 OpenAPI 스키마가 유일 권위(미지 키 제거) (b) 코드 allowlist 상수 | **(a)** + `additionalProperties:false` 강제, `audit` 공개분은 `evidence` 서브셋으로 재정의(WP-4). |
| D9 | canary 마커 수명 | (a) 테스트 빌드에만 삽입 (b) owner 코드에 상시 상수로 존재 | **(a)**(릴리스 산출물에 "canary" 문자열 자체가 없도록) + HOST_FACT·실제 비밀 스캔은 상시. |
| D10 | 공개 오류의 `location` 정의 | 라우트+단계 코드 표를 contracts에 넣을지, 메시지 문자열만 둘지 | contracts에 `location` enum 추가(WP-1/WP-3 공통). |

---

## 7. 확인 못 함 (실행 미수행 항목)

- §2.1의 `check_requirements`/`check_file`/`torch_load` 호출 경로는 정적 추적만 했다. 실제 요청으로 pip 실행·다운로드가 일어나는지는 서버 미기동으로 **확인 못 함**(WP-8 음성 테스트로 재현·차단 확인 필요; 네트워크 차단 fixture 필수).
- alias 전개에 의한 `canonical_sha256` 지수 비용은 PyYAML 공유 참조 + `json.dumps` 전개라는 코드 사실에서 추론했고 측정하지 않았다(**확인 못 함**).
- `prune()`이 미지 키를 통과시키는 것은 코드로 확인했으나, 실제 snapshot 응답에 어떤 owner 필드가 포함되는지는 응답을 받아 보지 못했다(**확인 못 함**; WP-4의 DTO 분석 문서와 대조 필요).
- SSE 다중 구독 시 SQLite 연결 수 증가와 그 영향은 측정하지 않았다(**확인 못 함**).
- 원본 호스트 `.env`의 실제 값 존재 여부·형식은 읽지 않았다(지침상 원본 쓰기 금지, 비밀 열람 불필요).
- ultralytics 설치본은 원본 venv 파일을 읽은 것이며 wheelhouse로 재설치될 사본과 바이트 동일한지는 **확인 못 함**(WP-1 설치 후 `nn/tasks.py` sha256 대조 권장: 앞 16자리 `c2465c97a13a1e3b`).

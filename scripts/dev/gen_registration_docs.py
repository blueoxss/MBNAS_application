#!/usr/bin/env python3
"""Generate the machine-derived parts of ``docs/registration`` (WP-7C).

One data definition (System / Subsystem / Block / Module, below) feeds every structure view so the table, the
mermaid source and the SVG drawing cannot drift apart; the interface tables are read from the contract files that
ship in ``mbnas_contracts``; the file list is read from the ZIP manifest. Standard library only, no clock, no network,
no external renderer: the SVG is written as plain, hand-editable SVG text.

Outputs (relative to ``--root``)::

  docs/registration/FILE_LIST.md                     whole file, from FILE_MANIFEST.jsonl (or a "pending" notice)
  docs/registration/diagrams/system_structure.mmd    mermaid source of the structure diagram
  docs/registration/diagrams/system_structure.svg    the same diagram as plain SVG
  docs/registration/SYSTEM_STRUCTURE.md              blocks <!-- BEGIN GENERATED:structure-summary|structure-table|mermaid -->
  docs/registration/INTERFACES.md                    blocks <!-- BEGIN GENERATED:api-v1|runner-v1 -->

Usage::

  python scripts/dev/gen_registration_docs.py                  # write everything under the repository root
  python scripts/dev/gen_registration_docs.py --check          # write nothing; exit 1 on drift or module-coverage gaps
  python scripts/dev/gen_registration_docs.py --only file-list \\
      --root <staging>/mbnas_application --manifest <staging>/mbnas_application/FILE_MANIFEST.jsonl

Packaging order for the registration ZIP (circularity): stage files -> write FILE_MANIFEST.jsonl -> run this script with
``--only file-list`` against the staged tree -> recompute FILE_MANIFEST.jsonl -> the packager renders PACKAGE_CONTENTS.md
-> FILE_MANIFEST.jsonl a last time. FILE_LIST.md never lists itself, the manifest or PACKAGE_CONTENTS.md (whether or not
the manifest it reads already holds them), so the shipped ZIP regenerates it byte for byte:
``--only file-list --check --root <unpacked>/mbnas_application --manifest <unpacked>/mbnas_application/FILE_MANIFEST.jsonl``
exits 0 on a released registration ZIP.

Exit codes: 0 ok; 1 ``--check`` found drift / coverage gaps / text overflow; 2 usage or input error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
REG = Path("docs/registration")
STRUCTURE_MD = REG / "SYSTEM_STRUCTURE.md"
INTERFACES_MD = REG / "INTERFACES.md"
FILE_LIST_MD = REG / "FILE_LIST.md"
MMD = REG / "diagrams" / "system_structure.mmd"
SVG = REG / "diagrams" / "system_structure.svg"
OPENAPI_PUBLIC = Path("mbnas_contracts/src/mbnas_contracts/openapi/openapi.json")
OPENAPI_RUNNER = Path("mbnas_contracts/src/mbnas_contracts/openapi/openapi_runner.json")
# Only the manifest of the tree itself (a staged / unpacked registration ZIP). In the source tree there is none, so
# FILE_LIST.md stays in its "pending" form unless --manifest names one explicitly.
MANIFEST_CANDIDATES = (Path("FILE_MANIFEST.jsonl"),)
# never listed in FILE_LIST.md: itself and the manifest-derived files written after it (C11 D7: the same rows and the same
# header whether the input is the packager's first manifest or the final manifest shipped in the ZIP)
SELF_EXCLUDED = ("docs/registration/FILE_LIST.md", "FILE_MANIFEST.jsonl", "PACKAGE_CONTENTS.md")

# ----------------------------------------------------------------------------------------------------------------
# Structure data: the single source of SYSTEM_STRUCTURE.md tables, the mermaid source and the SVG.
# ``globs`` are relative to the subsystem ``root``; they drive the module column (public blocks) and the coverage check.
# Private blocks show ``display`` (sub-package names only) instead of a file list.
# ----------------------------------------------------------------------------------------------------------------
SYSTEM_ID = "S0"
SYSTEM_NAME = "MBNAS Research Console"
SYSTEM_NAME_KO = "MBNAS 모듈형 신경망 구조 탐색·실험 통합관리 프로그램"

PUBLIC = "public"
PRIVATE = "private"
SCOPE_TEXT = {PUBLIC: "owner · public · registration", PRIVATE: "owner 전용(비공개)"}


@dataclass(frozen=True)
class Block:
    bid: str
    name: str
    short: str          # one line for the drawing
    desc: str           # sentence for the table
    globs: tuple[str, ...] = ()
    display: str = ""   # private blocks: shown instead of the matched file list
    scope: str = ""     # overrides the subsystem's distribution scope text (SUBMISSION_SCOPE_TABLE.md)


@dataclass(frozen=True)
class Subsystem:
    sid: str
    name: str
    package: str
    root: str           # repository-relative directory the block globs are relative to
    zone: str           # browser | customer | owner | shared | ops
    visibility: str
    lang: str
    role: str
    blocks: tuple[Block, ...]
    module_suffixes: tuple[str, ...] = (".py",)   # files that must be covered by some block (coverage check)
    cols: int = 1
    svg_note: str = ""  # appended to the subsystem title in the drawing
    scope: str = ""     # overrides SCOPE_TEXT[visibility] (SUBMISSION_SCOPE_TABLE.md)


SUBSYSTEMS: tuple[Subsystem, ...] = (
    Subsystem("SS1", "웹 콘솔", "mbnas_web", "mbnas_web/src", "browser", PUBLIC, "TypeScript · React",
              "브라우저 화면. gateway가 정적으로 제공하는 빌드 산출물(dist/)이 실행된다.", (
                  Block("B1.1", "화면", "7개 화면",
                        "아카이브·Run 비교·탐색 모니터링·실험 설정·모델 계보·실행 기록·서버 연결 화면",
                        ("screens/*.tsx",)),
                  Block("B1.2", "화면 구성요소", "후보 상세·구조도·학습 패널",
                        "후보 상세(gene 구성도·블록 표·구성 YAML), 구조 비교, 학습 패널, 진행 곡선, 아카이브 카드",
                        ("components/*.tsx", "components/archive/*.tsx")),
                  Block("B1.3", "API 클라이언트·상태", "/api/v1 호출·SSE·상태",
                        "/api/v1 호출과 SSE 수신·재연결, 계약 생성 타입(types.gen.ts), 화면 상태·URL 복원",
                        ("api/*.ts", "state/*.ts", "state/*.tsx", "App.tsx", "main.tsx", "styles.css")),
                  Block("B1.4", "표시 로직", "세대·구조·평가 표시 계산",
                        "세대·코호트·구조·평가 값의 표시용 계산과 형식화(아카이브 Pareto 표시 포함)",
                        ("lib/*.ts", "archive/*.ts")),
              ), module_suffixes=(".ts", ".tsx", ".css"),
              scope="owner · public · registration(소스 + 빌드 dist)"),
    Subsystem("SS2", "게이트웨이", "mbnas_server", "mbnas_server/src/mbnas_server", "customer", PUBLIC, "Python · FastAPI",
              "사용자 대면 /api/v1 서버(기본 포트 8710). core 콘솔 API로 중계하고 웹 번들과 아카이브를 제공한다.", (
                  Block("B2.1", "API 중계", "/api/v1 → core 콘솔 중계·SSE",
                        "/api/v1 요청을 core /core/v1/console로 중계(SSE 포함), 검증·run 생성 때 고객 호스트 경로·데이터셋 manifest 측정값 동봉, 오류 코드·request id 중계",
                        ("api/__init__.py", "api/app.py", "api/relay.py", "api/core_client.py", "api/validate_prep.py",
                         "api/gateway_inputs.py", "api/contract_check.py", "api/errors.py", "api/deps.py")),
                  Block("B2.2", "인증·요청 보호", "Bearer 인증·경로 위생·요청 제한",
                        "브라우저 Bearer 토큰 검사(상수 시간 비교), 경로 위생(dot segment 거부·경로 인자 인코딩), "
                        "요청률·고비용 동시성·SSE 연결 수 제한, 감사 로그",
                        ("api/auth.py", "api/limits.py", "api/path_guard.py")),
                  Block("B2.3", "구성 YAML 업로드", "원문 바이트·sha256 보존",
                        "모델 구성 YAML 업로드(≤1 MiB), 원문 바이트와 sha256을 고객 측 저장소에 보존",
                        ("api/uploads.py",)),
                  Block("B2.4", "정적 웹 서빙", "mbnas_web/dist → /",
                        "빌드된 웹 번들(mbnas_web/dist)을 / 경로로 제공(SPA fallback), 고객 서버에 Node 불필요",
                        ("api/static_web.py",)),
                  Block("B2.5", "읽기 전용 아카이브", "고객 호스트 연구 결과 열람",
                        "고객 호스트에 있는 연구 run 결과를 읽기 전용으로 열람(/api/v1/archive/*, GET 13종)",
                        ("archive/*.py",)),
                  Block("B2.6", "설정·진단", "MBNAS_GW_* 설정·doctor",
                        "MBNAS_GW_* 설정 로딩·검증, mbnas-gateway-doctor 진단",
                        ("__init__.py", "config.py", "doctor.py")),
                  Block("B2.7", "artifact 저장소", "내용 주소 저장·서명 URL·다운로드",
                        "고객 측 artifact 저장소(sha256 내용 주소, 원자적 쓰기), 서명 URL, digest 재검증 다운로드, 내보내기 ZIP 조립, 구성 개정 원문 제공",
                        ("artifacts/*.py", "api/artifact_routes.py")),
              ), cols=3),
    Subsystem("SS3", "실행기(runner)", "mbnas_runner", "mbnas_runner/src/mbnas_runner", "customer", PUBLIC, "Python",
              "고객 GPU 호스트에서 학습·평가·벤치마크 작업을 실행한다. core에서 작업을 임대(lease)받는다.", (
                  Block("B3.1", "데몬·원격 프로토콜", "등록·claim·heartbeat·result·journal",
                        "register→claim→start→heartbeat→progress→checkpoint/artifact→result→ack 수행, 로컬 journal로 멱등 재전송, "
                        "TLS 검증, artifact 바이트의 고객 측 저장소 전송",
                        ("__init__.py", "__main__.py", "daemon.py", "client.py", "journal.py", "envelope.py", "reports.py",
                         "config.py", "transfer.py")),
                  Block("B3.2", "작업 프로세스 관리", "자식 프로세스·정지·GPU 대기",
                        "작업별 자식 프로세스 실행·정지 신호·GPU 점유 대기와 장치 할당",
                        ("child.py", "contract.py", "gpu.py", "gpu_guard.py")),
                  Block("B3.3", "학습 핸들러", "단계 학습·checkpoint",
                        "rung 단위 학습, full-state checkpoint 저장·재개, 학습 recipe 해석",
                        ("training/*.py",)),
                  Block("B3.4", "평가·벤치마크 핸들러", "raw 지표 측정·보고",
                        "평가·공식 가중치 평가·벤치마크 작업 실행과 raw 지표 보고(적합도·승급 판정은 하지 않음)",
                        ("evaluation/*.py", "benchmark/*.py")),
              )),
    Subsystem("SS4", "모델 런타임", "mbnas_model_runtime", "mbnas_model_runtime/src/mbnas_model_runtime", "customer", PUBLIC,
              "Python · PyTorch",
              "승인된 모델을 구축·학습·평가하는 공개 백엔드. runner와 gateway가 같은 호스트에서 import한다.", (
                  Block("B4.1", "백엔드 어댑터", "Ultralytics·RF-DETR·ETRI hybrid",
                        "Ultralytics(YOLO11/YOLO26/RT-DETR), RF-DETR, ETRI hybrid 모델 어댑터와 native 구조 관측",
                        ("adapters/*.py", "adapters/etri_hybrid/*.py")),
                  Block("B4.2", "맞춤 연산자", "ETRI mixed 연산자·parse_model",
                        "ETRI mixed 연산자 3종(vendored)과 ultralytics 8.4.173 parse_model 정적 확장(exec 미사용)",
                        ("operators/*.py",)),
                  Block("B4.3", "구조 표현·관측", "genotype·블록 카탈로그",
                        "YAML genotype 해석·선언 검사(모듈 허용목록·인자 한도), 구조 관측, 블록 설계 출처 카탈로그",
                        ("genotype.py", "genotype_audit.py", "block_catalog.py")),
                  Block("B4.4", "데이터셋", "manifest·안전 YAML",
                        "데이터셋 YAML 안전 파싱, split manifest·hash 계산, 부분집합·COCO 변환",
                        ("datasets/*.py",)),
                  Block("B4.5", "평가·벤치마크 절차", "평가기·latency·ONNX/TRT",
                        "평가기(정합·이미지 산출), latency·크기 측정, ONNX/TensorRT 벤치마크 절차",
                        ("evaluation/*.py", "benchmark/*.py")),
                  Block("B4.6", "checkpoint·registry·출처", "checkpoint 형식·모델 명세",
                        "checkpoint 형식·검증, baseline 모델 명세·probe, 실행 환경 lock 기록",
                        ("__init__.py", "checkpoint.py", "registry/*.py", "provenance/*.py")),
              )),
    Subsystem("SS5", "공통 계약", "mbnas_contracts", "mbnas_contracts/src/mbnas_contracts", "shared", PUBLIC, "Python",
              "모든 Python 패키지가 공유하는 DTO·OpenAPI·작업 프로토콜·공통 유틸(외부 의존 pydantic·PyYAML만).", (
                  Block("B5.1", "스키마·OpenAPI", "DTO·openapi.json",
                        "공개 DTO 스키마(pydantic), 공개 API·runner 프로토콜 OpenAPI 문서와 계약 이력",
                        ("schemas/*.py", "openapi/*.py")),
                  Block("B5.2", "작업·원격 프로토콜", "JobEnvelope·runner 프로토콜",
                        "작업 프로토콜 타입, runner 프로토콜 모델, protocol_hash, 구조 문서 상수, 기본 baseline 상수",
                        ("jobs_protocol.py", "runner_protocol.py", "protocol_hash.py", "structure_doc.py", "constants.py")),
                  Block("B5.3", "공통 유틸·오류", "canon·원자적 쓰기·로그 마스킹",
                        "정규 직렬화·원자적 쓰기·경로 검사·로그 마스킹·시간 유틸, 오류 본문, ASGI 미들웨어",
                        ("__init__.py", "util/*.py", "asgi.py", "errors.py")),
              ), cols=3, svg_note=" — 모든 Python 패키지가 의존"),
    Subsystem("SS6", "코어(core)", "mbnas_core", "mbnas_core/src/mbnas_core", "owner", PRIVATE, "Python · FastAPI · SQLite",
              "Owner 호스트에서만 실행되는 비공개 서브시스템(기본 포트 8700). 탐색 정책·판정·권위 상태를 가진다.", (
                  Block("B6.1", "콘솔 API", "/core/v1/console (gateway 전용)",
                        "gateway 전용 콘솔 API(서비스 토큰), 공개 응답 투영 지점",
                        display="api, console"),
                  Block("B6.2", "원격 실행 제어면", "/core/v1/runner · /core/v1/owner",
                        "runner 작업 임대·fencing·job ticket·lease 회수, owner 조회 API",
                        display="runner_api"),
                  Block("B6.3", "탐색 정책 (기여 2)", "후보 제안·세대 진행·RNG 스트림",
                        "후보 제안(GA·IndependentRandom·RandomGeneration), 세대 진행, seed별 RNG 스트림, 계보 기록",
                        display="search (structure 제외), search.mixed_pool"),
                  Block("B6.4", "구조 게이트·검사 격리 (기여 1)", "구조 게이트·수리·격리 실행",
                        "후보·사용자 구조 게이트와 수리(repair), 자원 제한 서브프로세스 격리 실행",
                        display="search.structure, structure"),
                  Block("B6.5", "프로토콜 해석·판정", "검증·적합도·승급 판정",
                        "실험 프로토콜 검증·해석, 적합도·승급 판정(비공개 규칙)",
                        display="protocol"),
                  Block("B6.6", "오케스트레이션·스케줄링", "작업 계획·예산·최종 재학습",
                        "작업 계획·예산 승인·비용 기록, 별도 학습 run, 최종 재학습, artifact 등록, 내보내기",
                        display="orchestration, scheduling, training_runs, artifacts, export"),
                  Block("B6.7", "상태 저장·이벤트", "SQLite 권위 상태·이벤트",
                        "run·후보·평가·작업·비용·checkpoint 권위 상태(SQLite)와 이벤트 버스",
                        display="db, events"),
                  Block("B6.8", "owner worker·운영", "후보 생성 작업·자격 관리",
                        "후보 생성(PROPOSE_GENERATION) 전용 owner worker, 자격(principal) 관리, 진단, registry 상태",
                        display="owner_worker, ops, registry, config"),
              )),
    Subsystem("SS7", "운영·배포 도구", "(저장소 루트)", ".", "ops", PUBLIC, "Bash · Python · Docker",
              "설치·기동·정지·진단·검증 스크립트, 배포 정의, 설정·예제. 패키지가 아닌 배포 보조물이다.", (
                  Block("B7.1", "설치·운영 스크립트", "install·start·stop·doctor·verify",
                        "설치·기동·정지·진단·검증 스크립트",
                        ("scripts/install.sh", "scripts/doctor.sh", "scripts/start.sh", "scripts/stop.sh",
                         "scripts/verify.sh"), scope="owner · public · registration"),
                  Block("B7.2", "배포 정의", "Docker·compose·service 예시",
                        "Docker 이미지·compose·systemd/nginx 예시(public; owner 정의는 owner 배포본 전용)",
                        ("deploy/public/*",), scope="owner · public · registration(deploy/owner는 owner)"),
                  Block("B7.3", "설정·예제", "env 예시·protocol 예시·합성 예제",
                        "역할별 env 예시, protocol·registry 예시, 합성 예제 데이터",
                        ("config/examples/*", "examples/*", "examples/models/*", "examples/synthetic4/data.yaml"),
                        scope="owner · public · registration(core·owner env 예시는 owner)"),
                  Block("B7.4", "공개 보조 도구", "pilot 데이터 부분집합 생성",
                        "pilot 데이터 부분집합 생성 도구(`--source`·`--out` 필수 인자, 호스트 기본 경로 없음)",
                        ("scripts/public/*.py",), scope="owner · public · registration"),
                  Block("B7.5", "owner 보조 도구", "가중치·호스트 GPU 벤치·재개 동등성",
                        "가중치 내려받기, 호스트 GPU 벤치마크(콘솔 큐 대신 쓰는 BENCHMARK 경로), full-state 재개 동등성 비교",
                        ("scripts/runner/*.py",), scope="owner 전용(공개 ZIP 미포함)"),
              ), module_suffixes=(), cols=5, svg_note=" — 패키지가 아닌 배포 보조물", scope="프로필별(블록 행 참조)"),
)

ZONES = {
    "browser": ("사용자 브라우저", "SS1"),
    "customer": ("고객 호스트 — public-runtime 배포(공개)", "SS2, SS3, SS4"),
    "owner": ("Owner 호스트 — owner-private 배포(비공개)", "SS6"),
    "shared": ("공통(모든 Python 패키지가 의존)", "SS5"),
    "ops": ("배포 보조물", "SS7"),
}

# Interfaces drawn as numbered arrows (number, label, from, to). Detailed tables live in INTERFACES.md.
INTERFACES = (
    ("1", "/api/v1 — HTTPS, 브라우저 Bearer 토큰, SSE", "SS1", "SS2"),
    ("2", "/core/v1/console — gateway 서비스 토큰(server scope)", "SS2", "SS6"),
    ("3", "/core/v1/runner — runner 토큰 + job ticket", "SS3", "SS6"),
    ("4", "/core/v1/owner — admin 토큰(원격 배치 시 프록시에서 차단)", "ADMIN", "SS6"),
    ("5", "정적 웹 번들(/) 제공", "SS2", "SS1"),
)
DEPENDENCIES = (  # dashed arrows: same-host, in-process use or local disk
    ("프로세스 내 import(모델 구축·학습·평가)", "SS3", "SS4"),
    ("데이터셋 manifest helper", "SS2", "SS4"),
    ("artifact 바이트(로컬 저장소)", "SS3", "ST1"),
)
STORES = {
    "ST1": "artifact 저장소 · 업로드 원문(고객 디스크)",
    "ST2": "데이터셋 · 가중치 · 학습 산출물(고객 디스크)",
    "ST3": "core DB(SQLite) · 자격 hash · run 기록",
}
ADMIN_LABEL = "owner 관리자(로컬 CLI)"

# ----------------------------------------------------------------------------------------------------------------
# Korean summaries of the contract operations (fallback: the contract's own summary, marked).
# ----------------------------------------------------------------------------------------------------------------
API_SUMMARY_KO = {
    ("GET", "/api/v1/health"): "서비스 상태·버전·허용 경로·capability 조회(gateway 상태 + core 요약)",
    ("POST", "/api/v1/protocols/validate"): "실험 프로토콜 검증·해석(resolved protocol, protocol_hash, validation_id)",
    ("POST", "/api/v1/runs"): "검증된 프로토콜로 탐색 run 생성(Idempotency-Key)",
    ("GET", "/api/v1/runs"): "run 목록(최신순)",
    ("GET", "/api/v1/runs/{run_id}/snapshot"): "run 스냅샷(후보·세대·작업자·비용)",
    ("GET", "/api/v1/runs/{run_id}/events"): "run 이벤트 스트림(SSE, Last-Event-ID/after_seq로 재개)",
    ("POST", "/api/v1/runs/{run_id}/pause"): "일시정지 요청(안전 경계에서 full-state 저장 후 PAUSED)",
    ("POST", "/api/v1/runs/{run_id}/resume"): "재개 요청(full-state 검증 후 재개)",
    ("POST", "/api/v1/runs/{run_id}/cancel"): "취소 요청",
    ("GET", "/api/v1/runs/{run_id}/artifacts/{artifact_id}"): "artifact 바이트 다운로드(Bearer 또는 서명 URL, sha256 대조)",
    ("GET", "/api/v1/runs/{run_id}/export"): "run 내보내기 ZIP",
    ("GET", "/api/v1/protocols/presets"): "서버 preset(DEFAULT_BASELINE14 설계 예시, PILOT_SMOKE)",
    ("GET", "/api/v1/registry"): "baseline 모델 registry와 capability 증거",
    ("GET", "/api/v1/runs/{run_id}"): "run 객체(resolved protocol, 비용)",
    ("GET", "/api/v1/runs/{run_id}/candidates/{candidate_id}"): "후보 상세(계보·블록·평가·checkpoint·비용·게이트)",
    ("POST", "/api/v1/runs/{run_id}/artifacts/{artifact_id}/refresh"): "만료된 artifact 참조의 서명 URL·메타데이터 재발급",
    ("POST", "/api/v1/runs/{run_id}/final-retrain"): "완료된 탐색 run의 최종 재학습(FINAL_RETRAIN) child run 생성",
    ("GET", "/api/v1/archive/runs"): "아카이브 run 목록",
    ("GET", "/api/v1/archive/runs/{run_id}"): "아카이브 run 개요",
    ("GET", "/api/v1/archive/runs/{run_id}/baselines"): "baseline과 탐색 기준점",
    ("GET", "/api/v1/archive/runs/{run_id}/generations"): "세대별 진행·특성",
    ("GET", "/api/v1/archive/runs/{run_id}/generations/{g}"): "세대 상세",
    ("GET", "/api/v1/archive/runs/{run_id}/candidates"): "후보 평가 목록",
    ("GET", "/api/v1/archive/runs/{run_id}/candidates/{cid}"): "후보 상세",
    ("GET", "/api/v1/archive/runs/{run_id}/pareto"): "Pareto archive",
    ("GET", "/api/v1/archive/runs/{run_id}/fidelity"): "fidelity(T1/T2 순위 상관) 산출물",
    ("GET", "/api/v1/archive/runs/{run_id}/confirm"): "3-seed 확인 단계",
    ("GET", "/api/v1/archive/runs/{run_id}/files/{kind}"): "등록된 산출 파일",
    ("GET", "/api/v1/archive/compare"): "run 비교(조건 포함)",
    ("GET", "/api/v1/archive/live"): "진행 중 파이프라인 상태",
    ("POST", "/api/v1/runs/{run_id}/candidates/{candidate_id}/structure-check"): "후보 구조 검사 재실행(CPU, 학습·성능 평가 없음)",
    ("POST", "/api/v1/training-runs"): "baseline·사용자 구성·후보를 선택한 별도 학습 run 생성",
    ("GET", "/api/v1/registry/{model_id}/structure"): "registry 모델의 기록된 구조 증거(빌드 없음)",
    ("POST", "/api/v1/registry/{model_id}/structure-check"): "registry 모델 구조 관측(CPU, 결과 저장)",
    ("POST", "/api/v1/structure-check"): "사용자 detector YAML 구조 검사(결과 저장 안 함)",
    ("GET", "/api/v1/structure/block-catalog"): "블록 설계 출처 표(정적, 모델 빌드 없음)",
    ("GET", "/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions"): "후보 구성의 개정 이력(원본·선택본·실행본·생성본)",
    ("GET", "/api/v1/runs/{run_id}/candidates/{candidate_id}/config-revisions/{revision_id}/yaml"): "개정 하나의 저장 바이트(다운로드·선택본↔실행본 diff용)",
    ("POST", "/api/v1/uploads/model-config"): "모델 구성 YAML 업로드(gateway 저장소, 내용 주소; gateway가 직접 처리)",
    ("POST", "/core/v1/runner/register"): "runner 등록·갱신과 호환성 협상(계약·envelope 버전, 주기, 허용 작업 종류)",
    ("POST", "/core/v1/runner/claim"): "다음 작업 lease와 JobEnvelope 수령",
    ("POST", "/core/v1/runner/jobs/{job_id}/start"): "attempt 시작(과금 시작점)",
    ("POST", "/core/v1/runner/jobs/{job_id}/heartbeat"): "lease 연장과 제어 플래그(pause·cancel) 수신",
    ("POST", "/core/v1/runner/jobs/{job_id}/progress"): "진행 보고(배치)",
    ("POST", "/core/v1/runner/jobs/{job_id}/checkpoints"): "검증된 checkpoint 등록",
    ("POST", "/core/v1/runner/jobs/{job_id}/artifacts"): "평가 artifact 등록(배치)",
    ("POST", "/core/v1/runner/jobs/{job_id}/result"): "작업 결과 commit(멱등)",
    ("POST", "/core/v1/runner/jobs/{job_id}/ack"): "결과 응답 수신 확인",
    ("POST", "/core/v1/runner/deregister"): "정상 종료 등록 해제",
    ("POST", "/core/v1/runner/checkpoints/{checkpoint_id}/pruned"): "보존 정책 삭제 지시의 처리 결과 보고(core가 checkpoint를 PRUNED로 표시)",
}
METHODS = ("get", "post", "put", "patch", "delete")
RUNNER_FLOW = ("/core/v1/runner/register", "/core/v1/runner/claim", "/core/v1/runner/jobs/{job_id}/start",
               "/core/v1/runner/jobs/{job_id}/heartbeat", "/core/v1/runner/jobs/{job_id}/progress",
               "/core/v1/runner/jobs/{job_id}/checkpoints", "/core/v1/runner/jobs/{job_id}/artifacts",
               "/core/v1/runner/jobs/{job_id}/result", "/core/v1/runner/jobs/{job_id}/ack",
               "/core/v1/runner/deregister")  # protocol order of the runner table (unknown paths keep file order, last)


# ----------------------------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------------------------
def md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def text_width(text: str, size: float) -> float:
    """Rough rendered width (px) of ``text`` at ``size`` px: wide (CJK) glyphs 1 em, others ~0.56 em."""
    width = 0.0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            width += size
        elif ch == " ":
            width += size * 0.3
        else:
            width += size * 0.56
    return width


def block_files(root: Path, sub: Subsystem, block: Block) -> list[str]:
    base = root / sub.root
    found: set[str] = set()
    for pattern in block.globs:
        for p in base.glob(pattern):
            if p.is_file() and "__pycache__" not in p.parts:
                found.add(p.relative_to(base).as_posix())
    return sorted(found)


def module_label(sub: Subsystem, rel: str) -> str:
    if sub.zone == "ops" or sub.package == "mbnas_web":
        return rel
    name = rel[:-3] if rel.endswith(".py") else rel
    name = name.replace("/", ".")
    if name.endswith("__init__"):
        name = name[: -len("__init__")].rstrip(".") or "__init__"
    return name


def coverage(root: Path) -> tuple[list[str], list[str]]:
    """(uncovered, notes): every module file of a present subsystem must match one of its block globs."""
    uncovered: list[str] = []
    notes: list[str] = []
    for sub in SUBSYSTEMS:
        if not sub.module_suffixes:
            continue
        base = root / sub.root
        if not base.is_dir():
            notes.append(f"{sub.sid} {sub.package}: {sub.root} 없음(이 트리에 포함되지 않은 패키지) — 검사 생략")
            continue
        if sub.visibility == PRIVATE:
            globs = {
                "B6.1": ("api/*.py", "console/*.py"), "B6.2": ("runner_api/*.py",),
                "B6.3": ("search/__init__.py", "search/ga.py", "search/generation.py", "search/handlers.py",
                         "search/lineage.py", "search/random.py", "search/space.py", "search/streams.py",
                         "search/mixed_pool/*.py"),
                "B6.4": ("search/structure.py", "structure/*.py"), "B6.5": ("protocol/*.py",),
                "B6.6": ("orchestration/*.py", "scheduling/*.py", "training_runs/*.py", "artifacts/*.py",
                         "export/*.py"),
                "B6.7": ("db/*.py", "events/*.py"),
                "B6.8": ("__init__.py", "owner_worker/*.py", "ops/*.py", "registry/*.py", "config.py"),
            }
            covered = {p.relative_to(base).as_posix() for pats in globs.values() for pat in pats
                       for p in base.glob(pat) if p.is_file()}
        else:
            covered = {f for b in sub.blocks for f in block_files(root, sub, b)}
        for p in sorted(base.rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix not in sub.module_suffixes:
                continue
            rel = p.relative_to(base).as_posix()
            if rel not in covered:
                uncovered.append(f"{sub.sid} {sub.package}: {rel}")
    return uncovered, notes


# ----------------------------------------------------------------------------------------------------------------
# SYSTEM_STRUCTURE.md blocks
# ----------------------------------------------------------------------------------------------------------------
def render_structure_summary(root: Path) -> str:
    lines = [
        f"System **{SYSTEM_ID} {SYSTEM_NAME}** ({SYSTEM_NAME_KO})",
        "",
        "| ID | Subsystem | 패키지·위치 | 실행 위치(신뢰 영역) | 배포 범위 | 구현 언어 | Block 수 | Module 수 | 역할 |",
        "|---|---|---|---|---|---|---:|---:|---|",
    ]
    for sub in SUBSYSTEMS:
        if sub.visibility == PRIVATE:
            n_mod = "비기재"
        else:
            n_mod = str(sum(len(block_files(root, sub, b)) for b in sub.blocks))
        zone = ZONES[sub.zone][0]
        lines.append(f"| {sub.sid} | {md_cell(sub.name)} | `{sub.package}` | {md_cell(zone)} | "
                     f"{md_cell(sub.scope or SCOPE_TEXT[sub.visibility])} | {md_cell(sub.lang)} | {len(sub.blocks)} | {n_mod} | "
                     f"{md_cell(sub.role)} |")
    return "\n".join(lines)


def render_structure_table(root: Path) -> str:
    lines = [
        "| Subsystem | Block | Module | 배포 범위 | 설명 |",
        "|---|---|---|---|---|",
    ]
    for sub in SUBSYSTEMS:
        for b in sub.blocks:
            if sub.visibility == PRIVATE:
                modules = f"`{sub.package}`: {b.display} (파일 목록 비기재)"
            else:
                files = block_files(root, sub, b)
                if files:
                    modules = ", ".join(f"`{module_label(sub, f)}`" for f in files)
                else:
                    modules = "(해당 파일 없음)"
            scope = b.scope or sub.scope or SCOPE_TEXT[sub.visibility]
            lines.append(f"| {sub.sid} {md_cell(sub.name)} | {b.bid} {md_cell(b.name)} | {md_cell(modules)} | "
                         f"{md_cell(scope)} | {md_cell(b.desc)} |")
    return "\n".join(lines)


def render_mermaid() -> str:
    def node(b: Block) -> str:
        return b.bid.replace(".", "_")

    out = [f"%% {SYSTEM_ID} {SYSTEM_NAME} — 시스템 구조도",
           "flowchart LR"]
    by_zone: dict[str, list[Subsystem]] = {}
    for sub in SUBSYSTEMS:
        by_zone.setdefault(sub.zone, []).append(sub)

    def emit_sub(sub: Subsystem, indent: str) -> None:
        out.append(f'{indent}subgraph {sub.sid}["{sub.sid} {sub.name} · {sub.package}"]')
        out.append(f"{indent}  direction TB")
        for b in sub.blocks:
            out.append(f'{indent}  {node(b)}["{b.bid} {b.name}"]')
        out.append(f"{indent}end")

    for zone_key, zid in (("browser", "ZA"), ("customer", "ZB"), ("owner", "ZC")):
        title = ZONES[zone_key][0]
        out.append(f'  subgraph {zid}["{title}"]')
        if zone_key == "browser":
            out.append(f'    USER(["{USER_LABEL}"])')
        for sub in by_zone.get(zone_key, []):
            emit_sub(sub, "    ")
        if zone_key == "customer":
            out.append(f'    ST1[("{STORES["ST1"]}")]')
            out.append(f'    ST2[("{STORES["ST2"]}")]')
        if zone_key == "owner":
            out.append(f'    ADMIN["{ADMIN_LABEL}"]')
            out.append(f'    ST3[("{STORES["ST3"]}")]')
        out.append("  end")
    for zone_key in ("shared", "ops"):
        for sub in by_zone.get(zone_key, []):
            emit_sub(sub, "  ")
    circled = {"1": "①", "2": "②", "3": "③", "4": "④", "5": "⑤"}
    for num, label, src, dst in INTERFACES:
        out.append(f'  {src} -- "{circled[num]} {label}" --> {dst}')
    for label, src, dst in DEPENDENCIES:
        out.append(f'  {src} -. "{label}" .-> {dst}')
    out.append("  USER --> SS1")
    out.append("  SS6 --- ST3")
    out.append("  SS3 --- ST2")
    for sub in SUBSYSTEMS:
        style = "fill:#FBEFEA,stroke:#B5562F,stroke-width:2px" if sub.visibility == PRIVATE \
            else "fill:#EEF4FB,stroke:#3C6E9F"
        out.append(f"  style {sub.sid} {style}")
    out.append("  style ZC stroke-dasharray: 6 4")
    out.append("  style ZB stroke-dasharray: 6 4")
    out.append("  style ZA stroke-dasharray: 6 4")
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------------------------------------------------------
# SVG (plain, hand-editable; coordinates in LAYOUT)
# ----------------------------------------------------------------------------------------------------------------
W, H = 1280, 1060
HEADER_H, BLOCK_H, GAP, PAD = 28, 40, 6, 8
LAYOUT = {  # subsystem -> (x, y, width); heights follow from the block count and column count
    "SS1": (28, 146, 212),
    "SS2": (280, 112, 576),
    "SS4": (280, 324, 280),
    "SS3": (576, 324, 280),
    "SS6": (896, 166, 356),
    "SS5": (16, 712, 1248),
    "SS7": (16, 808, 1248),
}
ZONE_BOXES = {  # zone -> (x, y, w, h)
    "browser": (16, 76, 236, 618),
    "customer": (268, 76, 600, 618),
    "owner": (884, 76, 380, 618),
}
STORE_BOXES = {"ST1": (576, 572, 280, 44), "ST2": (576, 628, 280, 44), "ST3": (896, 586, 356, 44)}
ADMIN_BOX = (968, 102, 284, 34)
USER_BOX = (28, 102, 212, 34)
USER_LABEL = "사용자(연구자·운영자)"
COLORS = {
    PUBLIC: ("#EEF4FB", "#3C6E9F", "#C9D9EA"),
    PRIVATE: ("#FBEFEA", "#B5562F", "#EBC9BA"),
    "text": "#1F2328", "muted": "#57606A", "zone": "#8C959F", "store": ("#F3F3F1", "#8A8A85"),
}
FONT = "'Noto Sans KR','Noto Sans CJK KR','Nanum Gothic','Malgun Gothic','Apple SD Gothic Neo',sans-serif"


def sub_height(sub: Subsystem) -> int:
    rows = -(-len(sub.blocks) // sub.cols)
    return HEADER_H + rows * (BLOCK_H + GAP) - GAP + 2 * PAD


def sub_box(sub: Subsystem) -> tuple[int, int, int, int]:
    x, y, w = LAYOUT[sub.sid]
    return x, y, w, sub_height(sub)


class Svg:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.overflow: list[str] = []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x: float, y: float, s: str, size: float, *, weight: str = "normal", fill: str = "#1F2328",
             anchor: str = "start", limit: float | None = None, what: str = "") -> None:
        if limit is not None and text_width(s, size) > limit:
            self.overflow.append(f"{what or s!r}: ~{text_width(s, size):.0f}px > {limit:.0f}px")
        self.add(f'<text x="{x:g}" y="{y:g}" font-size="{size:g}" font-weight="{weight}" fill="{fill}" '
                 f'text-anchor="{anchor}">{xml_escape(s)}</text>')


def badge(svg: Svg, x: float, y: float, num: str) -> None:
    svg.add(f'<g class="badge"><circle cx="{x:g}" cy="{y:g}" r="10" fill="#FFFFFF" stroke="#1F2328" stroke-width="1.4"/>'
            f'<text x="{x:g}" y="{y + 4:g}" font-size="11" font-weight="bold" fill="#1F2328" '
            f'text-anchor="middle">{num}</text></g>')


def arrow(svg: Svg, x1: float, y1: float, x2: float, y2: float, *, dashed: bool = False, aid: str = "") -> None:
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    marker = "url(#arrow-dashed)" if dashed else "url(#arrow)"
    color = COLORS["muted"] if dashed else COLORS["text"]
    svg.add(f'<line id="{aid}" x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" stroke="{color}" stroke-width="1.6"'
            f'{dash} marker-end="{marker}"/>')


def render_svg() -> tuple[str, list[str]]:
    svg = Svg()
    svg.add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'font-family="{FONT}" role="img" aria-labelledby="svg-title svg-desc">')
    svg.add(f'<title id="svg-title">{SYSTEM_ID} {SYSTEM_NAME} 시스템 구조도</title>')
    svg.add('<desc id="svg-desc">System·Subsystem·Block 구조와 신뢰 영역(브라우저, 고객 호스트, Owner 호스트) 및 인터페이스 '
            '①~⑤.</desc>')
    svg.add('<defs>'
            '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
            'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#1F2328"/></marker>'
            '<marker id="arrow-dashed" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#57606A"/></marker>'
            '</defs>')
    svg.add(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')
    svg.text(24, 34, f"{SYSTEM_ID} {SYSTEM_NAME} — 시스템 구조도 (System · Subsystem · Block)", 18, weight="bold")
    svg.text(24, 56, f"{SYSTEM_NAME_KO} · 상세 Module 목록은 SYSTEM_STRUCTURE.md 표", 11,
             fill=COLORS["muted"])

    # zones
    for key, (x, y, w, h) in ZONE_BOXES.items():
        title = ZONES[key][0]
        svg.add(f'<g id="zone-{key}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="none" '
                f'stroke="{COLORS["zone"]}" stroke-width="1.4" stroke-dasharray="7 5"/>')
        svg.text(x + 12, y + 19, title, 12.5, weight="bold", fill=COLORS["muted"], limit=w - 20, what=f"zone {key}")
        svg.add("</g>")

    # subsystems and blocks
    for sub in SUBSYSTEMS:
        x, y, w, h = sub_box(sub)
        fill, stroke, block_stroke = COLORS[sub.visibility]
        width_px = 2.2 if sub.visibility == PRIVATE else 1.4
        svg.add(f'<g id="{sub.sid}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{width_px}"/>')
        tag = " · 비공개" if sub.visibility == PRIVATE else ""
        pkg = sub.package if sub.package.startswith("(") else f"({sub.package})"
        svg.text(x + 10, y + 19, f"{sub.sid} {sub.name} {pkg}{tag}{sub.svg_note}", 12.5, weight="bold", fill=stroke,
                 limit=w - 16, what=f"{sub.sid} title")
        inner_w = w - 2 * PAD
        col_w = (inner_w - (sub.cols - 1) * GAP) / sub.cols
        for i, b in enumerate(sub.blocks):
            r, c = divmod(i, sub.cols)
            bx = x + PAD + c * (col_w + GAP)
            by = y + HEADER_H + PAD + r * (BLOCK_H + GAP)
            svg.add(f'<g id="{b.bid.replace(".", "_")}"><rect x="{bx:g}" y="{by:g}" width="{col_w:g}" '
                    f'height="{BLOCK_H}" rx="5" fill="#FFFFFF" stroke="{block_stroke}" stroke-width="1.2"/>')
            svg.text(bx + 8, by + 16, f"{b.bid} {b.name}", 11.5, weight="bold", limit=col_w - 12, what=b.bid)
            svg.text(bx + 8, by + 32, b.short, 10, fill=COLORS["muted"], limit=col_w - 12, what=f"{b.bid} short")
            svg.add("</g>")
        svg.add("</g>")

    # stores and the owner admin actor
    s_fill, s_stroke = COLORS["store"]
    for sid, (x, y, w, h) in STORE_BOXES.items():
        svg.add(f'<g id="{sid}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{s_fill}" '
                f'stroke="{s_stroke}" stroke-width="1.2"/>')
        svg.text(x + w / 2, y + h / 2 + 4, STORES[sid], 11, anchor="middle", limit=w - 16, what=sid)
        svg.add("</g>")
    ax, ay, aw, ah = ADMIN_BOX
    svg.add(f'<g id="ADMIN"><rect x="{ax}" y="{ay}" width="{aw}" height="{ah}" rx="17" fill="#FFFFFF" '
            f'stroke="{COLORS[PRIVATE][1]}" stroke-width="1.2"/>')
    svg.text(ax + aw / 2, ay + ah / 2 + 4, ADMIN_LABEL, 11, anchor="middle", limit=aw - 16, what="admin")
    svg.add("</g>")
    ux, uy, uw, uh = USER_BOX
    svg.add(f'<g id="USER"><rect x="{ux}" y="{uy}" width="{uw}" height="{uh}" rx="17" fill="#FFFFFF" '
            f'stroke="{COLORS[PUBLIC][1]}" stroke-width="1.2"/>')
    svg.text(ux + uw / 2, uy + uh / 2 + 4, USER_LABEL, 11, anchor="middle", limit=uw - 16, what="user")
    svg.add("</g>")

    # interface arrows (numbered) and dependency arrows (dashed)
    s1 = sub_box(SUBSYSTEMS[0])
    gw = sub_box(next(s for s in SUBSYSTEMS if s.sid == "SS2"))
    rn = sub_box(next(s for s in SUBSYSTEMS if s.sid == "SS3"))
    mr = sub_box(next(s for s in SUBSYSTEMS if s.sid == "SS4"))
    core = sub_box(next(s for s in SUBSYSTEMS if s.sid == "SS6"))
    arrow(svg, ux + uw / 2, uy + uh, ux + uw / 2, s1[1], aid="user-web")
    s1_right, gw_left = s1[0] + s1[2], gw[0]
    arrow(svg, s1_right, 200, gw_left, 200, aid="if-1")
    badge(svg, (s1_right + gw_left) / 2, 186, "1")
    arrow(svg, gw_left, 262, s1_right, 262, aid="if-5")
    badge(svg, (s1_right + gw_left) / 2, 248, "5")
    gw_right, core_left = gw[0] + gw[2], core[0]
    arrow(svg, gw_right, 236, core_left, 236, aid="if-2")
    badge(svg, (gw_right + core_left) / 2, 222, "2")
    rn_right = rn[0] + rn[2]
    arrow(svg, rn_right, 430, core_left, 430, aid="if-3")
    badge(svg, (rn_right + core_left) / 2, 416, "3")
    admin_cx = ax + aw / 2
    arrow(svg, admin_cx, ay + ah, admin_cx, core[1], aid="if-4")
    badge(svg, admin_cx + 18, (ay + ah + core[1]) / 2, "4")
    arrow(svg, rn[0], 372, mr[0] + mr[2], 372, dashed=True, aid="dep-runner-runtime")
    arrow(svg, 420, gw[1] + gw[3], 420, mr[1], dashed=True, aid="dep-gateway-datasets")
    st1 = STORE_BOXES["ST1"]
    arrow(svg, rn[0] + rn[2] / 2, rn[1] + rn[3], rn[0] + rn[2] / 2, st1[1], dashed=True, aid="dep-runner-store")
    st3 = STORE_BOXES["ST3"]
    arrow(svg, core[0] + core[2] / 2, core[1] + core[3], core[0] + core[2] / 2, st3[1], dashed=True, aid="dep-core-db")

    # legend
    ly = 922
    svg.add('<g id="legend">')
    svg.text(24, ly, "범례", 12, weight="bold")
    items = [(PUBLIC, "공개 서브시스템(public·registration 배포)"), (PRIVATE, "비공개 서브시스템(owner 전용)")]
    lx = 70
    for vis, label in items:
        fill, stroke, _ = COLORS[vis]
        svg.add(f'<rect x="{lx}" y="{ly - 12}" width="22" height="14" rx="3" fill="{fill}" stroke="{stroke}"/>')
        svg.text(lx + 28, ly, label, 11)
        lx += 28 + text_width(label, 11) + 24
    svg.add(f'<rect x="{lx}" y="{ly - 12}" width="22" height="14" rx="7" fill="{s_fill}" stroke="{s_stroke}"/>')
    svg.text(lx + 28, ly, "데이터 저장소", 11)
    lx += 28 + text_width("데이터 저장소", 11) + 24
    svg.add(f'<line x1="{lx}" y1="{ly - 5}" x2="{lx + 30}" y2="{ly - 5}" stroke="#57606A" stroke-width="1.6" '
            f'stroke-dasharray="5 4" marker-end="url(#arrow-dashed)"/>')
    svg.text(lx + 38, ly, "같은 호스트의 import·로컬 디스크 사용", 11)
    y = ly + 24
    for num, label, src, dst in INTERFACES:
        badge(svg, 34, y - 4, num)
        names = {"ADMIN": ADMIN_LABEL}
        names.update({s.sid: f"{s.sid} {s.name}" for s in SUBSYSTEMS})
        svg.text(52, y, f"{names[src]} → {names[dst]}: {label}", 11)
        y += 22
    svg.add("</g>")
    svg.add("</svg>")
    return "\n".join(svg.parts) + "\n", svg.overflow


# ----------------------------------------------------------------------------------------------------------------
# INTERFACES.md blocks (from the openapi files)
# ----------------------------------------------------------------------------------------------------------------
def schema_name(content: dict | None) -> str:
    if not content:
        return "—"
    names = []
    for mime, body in content.items():
        ref = (body or {}).get("schema", {}).get("$ref") if isinstance(body, dict) else None
        if ref:
            names.append(f"`{ref.rsplit('/', 1)[-1]}`")
        elif mime.startswith("text/event-stream"):
            names.append("SSE 스트림")
        elif mime.startswith("application/json"):
            names.append("JSON")
        else:
            names.append(f"`{mime}`")
    return ", ".join(dict.fromkeys(names)) or "—"


def render_operations(doc: dict, *, auth_note: str, order: tuple[str, ...] = ()) -> str:
    info = doc.get("info", {})
    paths = doc.get("paths", {})
    ops = []
    for path, item in paths.items():
        for method in METHODS:
            if method in item:
                ops.append((path, method.upper(), item[method]))
    if order:
        rank = {p: i for i, p in enumerate(order)}
        ops = [op for _, op in sorted(enumerate(ops), key=lambda t: (rank.get(t[1][0], len(rank)), t[0]))]
    lines = [
        f"계약 문서: **{md_cell(info.get('title', '?'))}** 버전 `{info.get('version', '?')}` · 경로 {len(paths)}개 · "
        f"연산 {len(ops)}개 · 인증: {auth_note}",
        "",
        "| # | 메서드 | 경로 | 기능 | 요청 본문 | 성공 응답 | 오류 응답(HTTP) |",
        "|---:|---|---|---|---|---|---|",
    ]
    for i, (path, method, op) in enumerate(ops, 1):
        summary = API_SUMMARY_KO.get((method, path))
        if summary is None:
            summary = (op.get("summary") or op.get("operationId") or "").strip() + " (계약 원문 요약)"
        body = schema_name((op.get("requestBody") or {}).get("content"))
        params = [p.get("name") for p in op.get("parameters", []) if isinstance(p, dict) and p.get("in") == "header"]
        if params:
            body = f"{body} · 헤더 {', '.join(f'`{p}`' for p in params)}"
        ok = [(code, r) for code, r in op.get("responses", {}).items() if code.startswith("2")]
        ok_text = ", ".join(f"{code} {schema_name((r or {}).get('content')) if (r or {}).get('content') else ''}".strip()
                            for code, r in ok) or "—"
        errors = ", ".join(code for code in op.get("responses", {}) if code[:1] in ("4", "5")) or "—"
        lines.append(f"| {i} | {method} | `{path}` | {md_cell(summary)} | {md_cell(body)} | {md_cell(ok_text)} | {errors} |")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------
# FILE_LIST.md
# ----------------------------------------------------------------------------------------------------------------
def _first(entry: dict, keys: tuple[str, ...]) -> object:
    for k in keys:
        if k in entry and entry[k] not in (None, ""):
            return entry[k]
    return None


def load_manifest(path: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    problems: list[str] = []
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(f"{n}행: JSON 오류 ({exc.msg})")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{n}행: 객체가 아님")
            continue
        rel = _first(entry, ("path", "relpath", "rel_path", "name"))
        if not isinstance(rel, str) or not rel:
            problems.append(f"{n}행: 경로 키(path/relpath) 없음")
            continue
        if rel.startswith("/") or ".." in Path(rel).parts:
            problems.append(f"{n}행: 허용되지 않는 경로 {rel!r}")
            continue
        sha = _first(entry, ("sha256", "digest"))
        if isinstance(sha, str) and sha.startswith("sha256:"):
            sha = sha[len("sha256:"):]
        rows.append({
            "path": rel,
            "bytes": _first(entry, ("bytes", "size", "size_bytes")),
            "sha256": sha,
            "role": _first(entry, ("role", "kind")),
            "origin": _first(entry, ("origin", "source", "provenance")),
            "license": _first(entry, ("license", "licence", "license_id")),
        })
    return rows, problems


def _dig(data: dict, dotted: str) -> object:
    cur: object = data
    for key in dotted.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return None if isinstance(cur, (dict, list)) else cur


BUILD_INFO_FIELDS = (  # (dotted key, label); nested keys of the mbnas.build_info/1 schema written by package_build.py
    # (the build time is not shown: shipped documents carry no dates; it stays in BUILD_INFO.json)
    ("version", "버전"), ("profile", "프로필"), ("build_id", "build_id"),
    ("platform.tag", "플랫폼"), ("source_tree.sha256", "소스 트리"), ("payload_sha256", "payload"),
)


def render_build_info(info: dict) -> str:
    parts = []
    for key, label in BUILD_INFO_FIELDS:
        value = _dig(info, key)
        if value in (None, ""):
            continue
        text = f"{label} `{value}`"
        if key == "source_tree.sha256" and _dig(info, "source_tree.algorithm"):
            text += f"(`{_dig(info, 'source_tree.algorithm')}`)"
        parts.append(text)
    return " · ".join(parts) if parts else "(알려진 키 없음)"


def _inner(rel: str) -> str:
    return rel[len("mbnas_application/"):] if rel.startswith("mbnas_application/") else rel


def _group(rel: str) -> str:
    parts = _inner(rel).split("/")
    if len(parts) == 1:
        return "(루트 파일)"
    if parts[0] in ("docs", "deploy", "tests", "scripts", "config", "dist") and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}/"
    return f"{parts[0]}/"


def render_file_list(root: Path, manifest: Path | None, problems_out: list[str] | None = None) -> str:
    head = [
        "# FILE_LIST — 등록용 배포본 파일 목록 (registration-runtime ZIP)",
        "",
        "> 이 목록은 등록용 ZIP의 매니페스트(`FILE_MANIFEST.jsonl`)에서 **자동 생성**된다. 손으로 고치지 않는다.",
        "",
    ]
    if manifest is None:
        return "\n".join(head + [
            "## 매니페스트 없음",
            "",
            "이 트리에는 등록용 ZIP의 매니페스트가 없어 파일 목록을 만들지 않았다. 등록용 ZIP에는 그 ZIP의 매니페스트로 만든 "
            "목록이 들어 있다.",
            "",
        ])
    rows, problems = load_manifest(manifest)
    if problems_out is not None:
        problems_out.extend(problems)
    # SELF_EXCLUDED rows exist only in the final manifest; they are never counted (same text from either manifest)
    rows = [r for r in rows if _inner(r["path"]) not in SELF_EXCLUDED]
    rows.sort(key=lambda r: r["path"])
    build_info = None
    bi_path = manifest.parent / "BUILD_INFO.json"
    if bi_path.is_file():
        try:
            build_info = json.loads(bi_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            build_info = None
    try:
        shown = manifest.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        shown = manifest.name
    lines = head + [
        "## 출처",
        "",
        f"- 생성 입력: 이 ZIP의 매니페스트 `{shown}` · 목록 대상 항목 {len(rows)}개",
    ]
    if isinstance(build_info, dict):
        lines.append(f"- 빌드 정보(`BUILD_INFO.json`): {render_build_info(build_info)}")
    else:
        lines.append("- BUILD_INFO.json: 매니페스트 옆에 없음")
    lines.append("- 목록에서 제외: `docs/registration/FILE_LIST.md`(이 파일), `FILE_MANIFEST.jsonl`, `PACKAGE_CONTENTS.md`"
                 "(매니페스트를 사람이 읽는 표로 만든 것)")
    if problems:
        lines.append(f"- **매니페스트 문제 {len(problems)}건**(해당 행은 목록에서 빠짐): " + "; ".join(problems[:20]))
    total = sum(int(r["bytes"]) for r in rows if isinstance(r["bytes"], int))
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(_group(r["path"]), []).append(r)
    lines += ["", "## 요약", "", f"파일 {len(rows)}개 · 합계 {total:,} 바이트", "",
              "| 구분 | 파일 수 | 바이트 |", "|---|---:|---:|"]
    for g in sorted(groups):
        gb = sum(int(r["bytes"]) for r in groups[g] if isinstance(r["bytes"], int))
        lines.append(f"| `{md_cell(g)}` | {len(groups[g])} | {gb:,} |")
    lines += ["", "## 전체 목록", "", "| # | 경로 | 바이트 | sha256 | 역할 | 출처 | 라이선스 |",
              "|---:|---|---:|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        size = f"{r['bytes']:,}" if isinstance(r["bytes"], int) else (str(r["bytes"]) if r["bytes"] is not None else "—")
        sha = f"`{r['sha256']}`" if r["sha256"] else "—"
        cells = [md_cell(str(r[k])) if r[k] is not None else "—" for k in ("role", "origin", "license")]
        lines.append(f"| {i} | `{md_cell(r['path'])}` | {size} | {sha} | {cells[0]} | {cells[1]} | {cells[2]} |")
    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------------------
# marker replacement, driver
# ----------------------------------------------------------------------------------------------------------------
def replace_block(text: str, name: str, body: str, path: Path) -> str:
    begin = f"<!-- BEGIN GENERATED:{name}"
    end = f"<!-- END GENERATED:{name} -->"
    i = text.find(begin)
    j = text.find(end)
    if i < 0 or j < 0 or j < i:
        raise SystemExit(f"error: {path}: marker pair for '{name}' not found ({begin} ... -->, {end})")
    line_end = text.index("-->", i) + 3
    return text[:line_end] + "\n" + body.rstrip("\n") + "\n" + text[j:]


def find_manifest(root: Path, explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise SystemExit(f"error: manifest {p} not found")
        return p
    for cand in MANIFEST_CANDIDATES:
        p = root / cand
        if p.is_file():
            return p
    return None


def build(root: Path, only: str, manifest_arg: str | None) -> tuple[dict[Path, str], list[str], list[str], list[str]]:
    outputs: dict[Path, str] = {}
    warnings: list[str] = []
    notes: list[str] = []
    errors: list[str] = []
    if only in ("all", "structure"):
        md_path = root / STRUCTURE_MD
        if not md_path.is_file():
            raise SystemExit(f"error: {md_path} missing (the hand-written part with the GENERATED markers)")
        text = md_path.read_text(encoding="utf-8")
        text = replace_block(text, "structure-summary", render_structure_summary(root), md_path)
        text = replace_block(text, "structure-table", render_structure_table(root), md_path)
        mermaid = render_mermaid()
        text = replace_block(text, "mermaid", "```mermaid\n" + mermaid + "```", md_path)
        outputs[md_path] = text
        outputs[root / MMD] = mermaid
        svg, overflow = render_svg()
        outputs[root / SVG] = svg
        warnings += [f"SVG 텍스트 폭 초과 추정: {o}" for o in overflow]
        uncovered, cov_notes = coverage(root)
        warnings += [f"블록에 배정되지 않은 모듈: {u}" for u in uncovered]
        notes += cov_notes
    if only in ("all", "interfaces"):
        md_path = root / INTERFACES_MD
        if not md_path.is_file():
            raise SystemExit(f"error: {md_path} missing (the hand-written part with the GENERATED markers)")
        text = md_path.read_text(encoding="utf-8")
        for name, rel, auth, order in (("api-v1", OPENAPI_PUBLIC,
                                        "`Authorization: Bearer <브라우저 토큰>`(artifact 다운로드는 Bearer 또는 서명 URL)", ()),
                                       ("runner-v1", OPENAPI_RUNNER,
                                        "`Authorization: Bearer <runner 토큰>`, 작업 범위 요청은 job ticket·attempt 동반",
                                        RUNNER_FLOW)):
            p = root / rel
            if not p.is_file():
                body = f"(계약 파일 `{rel.as_posix()}` 이 이 트리에 없어 표를 생성하지 않았다.)"
                notes.append(f"{rel.as_posix()} 없음")
            else:
                body = render_operations(json.loads(p.read_text(encoding="utf-8")), auth_note=auth, order=order)
                body += f"\n\n출처: `{rel.as_posix()}` (sha256 `{hashlib.sha256(p.read_bytes()).hexdigest()}`)"
            text = replace_block(text, name, body, md_path)
        outputs[md_path] = text
    if only in ("all", "file-list"):
        manifest = find_manifest(root, manifest_arg)
        problems: list[str] = []
        outputs[root / FILE_LIST_MD] = render_file_list(root, manifest, problems)
        notes.append(f"매니페스트: {manifest if manifest else '없음 → 생성 대기 문구'}")
        errors += [f"매니페스트 행 문제: {pr}" for pr in problems]
    return outputs, warnings, notes, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate FILE_LIST.md, structure diagram (mermaid + SVG) and interface "
                                             "tables of docs/registration.")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="tree holding docs/registration (default: this repository)")
    ap.add_argument("--manifest", default=None, help="FILE_MANIFEST.jsonl of the registration ZIP staging tree")
    ap.add_argument("--only", choices=("all", "structure", "interfaces", "file-list"), default="all")
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 on drift, coverage gaps or overflow")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / REG).is_dir():
        print(f"error: {root / REG} is not a directory", file=sys.stderr)
        return 2
    outputs, warnings, notes, errors = build(root, args.only, args.manifest)
    drift = []
    for path, content in outputs.items():
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current == content:
            status = "unchanged"
        elif args.check:
            status = "DRIFT"
            drift.append(path)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            status = "written"
        try:
            shown = path.relative_to(root).as_posix()
        except ValueError:
            shown = str(path)
        print(f"{status:9s} {shown}")
    for n in notes:
        print(f"note      {n}")
    for w in warnings:
        print(f"warning   {w}")
    for e in errors:
        print(f"error     {e}")
    if errors:
        print(f"FAIL: {len(errors)} manifest problem(s); the file list marks them, fix the manifest")
        return 1
    if args.check and (drift or warnings):
        print(f"check: FAIL (drift {len(drift)}, warnings {len(warnings)})")
        return 1
    if args.check:
        print("check: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

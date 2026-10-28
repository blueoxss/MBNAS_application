# PROGRAM_OVERVIEW — 프로그램 개요

## 1. 기본 정보

| 항목 | 내용 |
|---|---|
| 프로그램 명칭(국문) | MBNAS 모듈형 신경망 구조 탐색·실험 통합관리 프로그램 |
| 프로그램 명칭(영문) | MBNAS Research Console |
| 버전 | v0.1.0 초판(설치 패키지 5종, 웹 패키지, `VERSION` 파일 모두 `0.1.0`) |
| 창작(개발) 연월일 · 공표 여부 | |
| 창작자 · 권리자 | |
| 용도 분류 | 연구·개발용 응용 소프트웨어: 객체 검출 신경망의 구조 탐색(NAS) 실험 설계·실행·관리 |
| 구현 언어 | Python 3.10(서버·실행기·모델 런타임), TypeScript + React 18(웹 화면), SQL(SQLite), Bash(운영 스크립트) |
| 운영 환경 | Linux x86_64. 학습·평가·벤치마크는 NVIDIA GPU(CUDA 12.4 빌드의 PyTorch 2.6.0). 컨테이너 배포 시 Docker(+ NVIDIA Container Toolkit) |
| 주요 외부 구성요소 | PyTorch, Ultralytics 8.4.173, RF-DETR, FastAPI, pydantic, React 등(전체 목록: `THIRD_PARTY_NOTICES.md`, 웹 번들의 npm 패키지: `THIRD_PARTY_NOTICES_WEB.md`). Ultralytics는 AGPL-3.0이며 모델 런타임이 이를 import한다 |
| 라이선스 | |

## 2. 목적

객체 검출(Object Detection) 모델의 신경망 구조를 자동 탐색하는 연구에서 반복되는 작업 — 실험 프로토콜 작성, 후보 구조 생성과 구조 검사, 학습·평가 작업의 배치와 일시정지·재개, 결과 비교와 산출물 관리 — 을 하나의 웹 콘솔과 서버로 관리한다.

설계상 특징은 **배포 분리**다. 사용자(고객)의 데이터와 GPU에서 실행되어야 하는 부분(웹 화면, 사용자 API gateway, 학습·평가 실행기, 모델 런타임)은 공개 배포본으로 제공하고, 탐색 정책·구조 게이트 규칙·판정·권위 상태를 가진 core는 owner 호스트에서만 실행되는 비공개 서비스로 둔다. 두 쪽은 정해진 HTTP 인터페이스로만 통신한다(`INTERFACES.md`).

## 3. 주요 기능

| # | 기능 | 설명 |
|---|---|---|
| 1 | 실험 프로토콜 | 서버 preset 불러오기, 프로토콜 검증·해석(resolved protocol, `protocol_hash`, 검증 id), 경로·데이터셋 split 검사 |
| 2 | 구조 탐색 run | 성능 평가를 하는 탐색(GA, IndependentRandom)과 평가 없이 구조만 생성하는 실행(RandomGeneration)을 구분해 실행 |
| 3 | 구조 검사 | 사용자 모델 YAML·baseline 모델·탐색 후보에 대해 CPU 구조 검사(PASS / FAIL / UNVERIFIED, 10개 검사 항목, 진단 코드·위치) |
| 4 | 별도 학습 run | baseline·사용자 구성·탐색/생성 후보를 골라 처음부터 학습, 사전학습 가중치로 추가 학습, 공식 가중치 평가 전용, 기존 run의 full-state 재개 |
| 5 | 실행 제어 | 일시정지(안전 경계에서 full-state checkpoint 저장 후 PAUSED)·재개(상태 검증 후)·취소, 최종 재학습 run |
| 6 | 모니터링 | 진행 상태·학습 곡선·세대 진행·이벤트 스트림(재연결 복구)·작업자(GPU) 상태 |
| 7 | 후보 상세 | gene 기반 블록 구성도, 블록 표(YAML 행·내부 계층 대응, 행에서 같은 블록 상세로 이동), 구성 YAML·해시 3종, 구조 비교, 계보 |
| 8 | 산출물 | checkpoint, 평가 이미지(GT/TP/FN/FP), sha256 대조 다운로드, run 내보내기 ZIP |
| 9 | 아카이브 | 고객 호스트에 있는 연구 run 결과의 읽기 전용 열람·조건 포함 비교 |
| 10 | 원격 실행 | 고객 GPU 호스트의 runner가 core에서 작업을 임대받아 실행(lease·heartbeat·멱등 결과 commit) |
| 11 | 운영 | 설치·진단·기동·정지·검증 스크립트와 Docker 배포 정의(`docs/public/INSTALL.md`, `docs/public/OPERATIONS.md`) |

기능별 설명은 `docs/public/FEATURES.md`에 있다.

## 4. 구성

System `S0` 아래 7개 Subsystem으로 구성된다. 상세는 `SYSTEM_STRUCTURE.md`(표와 도식), 인터페이스는 `INTERFACES.md`, 파일 목록은 `FILE_LIST.md`(ZIP 매니페스트에서 생성)에 있다.

| ID | Subsystem | 배포 |
|---|---|---|
| SS1 | 웹 콘솔(`mbnas_web`) | 공개 |
| SS2 | 게이트웨이(`mbnas_server`) | 공개 |
| SS3 | 실행기(`mbnas_runner`) | 공개 |
| SS4 | 모델 런타임(`mbnas_model_runtime`) | 공개 |
| SS5 | 공통 계약(`mbnas_contracts`) | 공개 |
| SS6 | 코어(`mbnas_core`) | 비공개(owner 전용) |
| SS7 | 운영·배포 도구(스크립트·배포 정의·설정 예시) | 프로필별 |

## 5. 운영 요구 사항

- **Python**: 3.10만 지원(`requires-python >=3.10,<3.11`).
- **주요 고정 버전**: torch 2.6.0(cu124 빌드), torchvision 0.21.0, ultralytics 8.4.173. ultralytics는 연산자 확장이 설치본의 `parse_model` 소스 hash를 확인하므로 다른 버전에서는 맞춤 연산자 등록이 명시적으로 실패한다.
- **GPU**: 학습·평가·벤치마크용 NVIDIA GPU. CPU 전용 runner는 학습·checkpoint·구조 생성까지만 지원하며, 지연시간 측정이 필요한 평가 완료 판정은 하지 않는다(평가가 필요한 작업은 checkpoint를 남기고 `NOT_EVALUATED`로 기록된다).
- **포트(기본값)**: core 8700, gateway 8710, 웹 개발용 preview 8720.
- **네트워크**: 온라인 설치는 PyPI와 PyTorch 배포 서버(`download.pytorch.org`)에 접속한다. 선택 사항인 오프라인 설치는 wheel 묶음이 필요하다.
- **Node.js**: 웹 화면을 다시 빌드할 때만 필요하다(서버는 빌드된 `mbnas_web/dist/`를 제공).

## 6. 관련 문서

| 문서 | 내용 |
|---|---|
| `SYSTEM_STRUCTURE.md` | System·Subsystem·Block·Module 표와 구조도(SVG·mermaid) |
| `INTERFACES.md` | 공개 API·콘솔 중계·runner 프로토콜·owner API 요약 |
| `FILE_LIST.md` | 등록용 ZIP의 파일 목록(매니페스트에서 생성) |
| `KEY_CONTRIBUTIONS.md` | 핵심 기여 1(구조 게이트·수리), 2(탐색 정책·상태) — 외부 관찰 동작만 |
| `SUBMISSION_SCOPE_TABLE.md` | owner·public·registration 배포 범위표 |
| `../public/` | 설치·기능·운영·문제 해결·보안 모델·호환성·API 문서 |

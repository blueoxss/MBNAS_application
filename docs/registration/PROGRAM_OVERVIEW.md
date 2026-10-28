# PROGRAM_OVERVIEW — 프로그램 개요

문서 상태: 초안 · 2026-10-28 · WP-7C. "확인 필요"로 표시한 항목은 사용자가 `REVIEW_CHECKLIST.md`에 따라 채운다. 이 문서는 법적 판단이나 검증 결과를 대신하지 않는다.

## 1. 기본 정보

| 항목 | 내용 | 상태 |
|---|---|---|
| 프로그램 명칭(국문) | MBNAS 모듈형 신경망 구조 탐색·실험 통합관리 프로그램 | 확정 여부 확인 필요 |
| 프로그램 명칭(영문) | MBNAS Research Console | 확정 여부 확인 필요 |
| 버전 | v0.1.0 초판(설치 패키지 5종, 웹 패키지, 저장소 `VERSION` 파일 모두 `0.1.0`) | 제출처 양식의 버전 표기 방식만 확인 |
| 창작(개발) 연월일 · 공표 여부 | — | **확인 필요** |
| 창작자 · 권리자 | — | **확인 필요** |
| 용도 분류 | 연구·개발용 응용 소프트웨어: 객체 검출 신경망의 구조 탐색(NAS) 실험 설계·실행·관리 | 제출처 분류 체계 확인 필요 |
| 구현 언어 | Python 3.10(서버·실행기·모델 런타임), TypeScript + React 18(웹 화면), SQL(SQLite), Bash(운영 스크립트) | — |
| 운영 환경 | Linux x86_64. 학습·평가·벤치마크는 NVIDIA GPU(CUDA 12.4 빌드의 PyTorch 2.6.0). 컨테이너 배포 시 Docker(+ NVIDIA Container Toolkit) | 검증 플랫폼: 아래 §6 |
| 주요 외부 구성요소 | PyTorch, Ultralytics 8.4.173, RF-DETR, FastAPI, pydantic, React 등(전체 목록: `THIRD_PARTY_NOTICES.md`) | 권리 검토 대기: `RIGHTS_REVIEW.md` |
| 라이선스 | 자체 코드의 라이선스는 정해지지 않았다 | `RIGHTS_REVIEW.md` |

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

기능별 구현·검증 상태는 `docs/public/FEATURES.md`에 있다. 콘솔 run의 조건 포함 후보 비교 화면, 서버 로그 tail 화면은 이번 범위에 없다(후속 과제).

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

## 5. 개발·검증 상태 (2026-10-28 기준)

아래 표의 "실행 확인"은 이 저장소에서 실제로 실행한 결과만 뜻한다. 원본 프로그램에서 보고된 과거 GPU 결과는 이 프로그램의 검증으로 세지 않는다. 상태는 2026-10-28 통합 검증 결과다.

| 검증 항목 | 내용 | 상태 |
|---|---|---|
| 단위·통합 시험(T1) | owner·public 시험 묶음(CPU) | 실행 확인. 분할 직후 기준선: owner 게이트 1116 통과·0 실패, public 564 통과·0 실패(2026-10-26). 최종 회귀 게이트: owner 1569 통과·0 실패, public 1124 통과·0 실패(2026-10-28). 그 뒤의 수정(작업 임대 때 만료 lease 회수, 원격 prune 왕복, 운영 스크립트·패키저·웹 보강)은 영향 받는 시험 묶음을 다시 돌려 0 실패 |
| core 동등성(T2) | 원본 대비 같은 입력의 후보·수리·판정·난수 흐름 비교 | 실행 확인: 10개 시나리오 불일치 0(정확히 같음 3, 문서화된 허용 차이만 있음 7). 마지막 core 변경 뒤의 트리에서 다시 돌려 같은 결과(2026-10-28). GPU 학습 결과도 원본과 bitwise 동일(같은 seed·결정적 설정, 1차 GPU 검증) |
| 분산·보안(T3) | 원격 큐 시험 매트릭스, 입력·인증 음성 시험 | 실행 확인: runner 프로토콜·산출물 시험 97건 통과(보존 정책 원격 삭제 왕복 T3-34 포함), 구조 검사 sandbox(T3-35)와 보안 음성 시험 통과. runner 경로의 응답에 owner 전용 정보가 없음을 표식 시험으로 확인 |
| 실제 다중 프로세스 실행 | core·owner worker·runner·gateway 실제 프로세스(CPU) | 실행 확인(구조 생성 run, CPU 학습, 업로드·구조 검사, 분리 배치의 고객 측 출력 경로 run 완료). 두 호스트 분리 배치 시뮬레이션(CPU)에서 GPU 없는 core 호스트의 장치 목록 대체, validation을 켠 CPU 학습의 `NOT_EVALUATED` 기록과 run 완료, core 쪽 생성 구조 YAML의 gateway 경유 다운로드(sha256 일치)도 확인 |
| GPU 경로(T7) | 실제 GPU 학습·평가·일시정지·재개·장애 복구·산출물 전송 | **실행 확인 — 판정 YES(한계 있음)**: 기준 트리 `9dafe4d0…`에서 baseline 학습, 평가 ON 탐색(후보 32개·실측 지연시간), 최종 재학습, 일시정지→full-state 재개, 취소, runner 강제 종료→lease 회수→다음 시도, 산출물 로컬 전송과 gateway 경유 다운로드(sha256 일치), 장치 기록, 공식 가중치 평가, 0이 아닌 GPU의 runner, owner 스크립트 벤치마크가 모두 통과. 한계: TensorRT는 BLOCKED_BY_ENV, 벤치마크는 owner 스크립트 경로만, 0이 아닌 GPU에서 탐색 후보 학습은 미실행, 그 뒤의 코드 변경은 CPU로만 검증 |
| owner 독립 설치·실행(T4) | owner 배포본만으로 설치·실행 | **PASS**(최종 배포본 재실행, 2026-10-28): GPU 장치가 없는 격리 환경(bwrap)에서 owner ZIP만으로 오프라인 설치·진단·4프로세스 기동·구조 검사 PASS/FAIL·평가 OFF 생성·CPU 학습 일시정지→full-state 재개→완료·산출물과 checkpoint 다운로드(sha256 일치)·내보내기·정지. validation을 켠 CPU 학습은 checkpoint를 남기고 `NOT_EVALUATED`(사유 `EVALUATION_REQUIRES_CUDA`)로 끝나고, 평가 ON 탐색은 검증 단계에서 거부된다(설계대로). 1차 실행(PARTIAL)의 결함은 수정 후 확인 |
| public 독립 설치·실행(T5) | 공개 배포본 + core 연결 | **PASS**(최종 배포본 재실행, 2026-10-28): 공개 ZIP만으로 만든 CPU 이미지(gateway·runner 컨테이너)를 같은 호스트의 core에 연결해, 고객 쪽에만 있는 출력 경로로 업로드→검사→평가 OFF 생성→사용자 YAML CPU 학습(일시정지·재개)→결과→웹→다운로드·내보내기(sha256 일치), core 중단 시 명시 실패(503)를 확인. 이미지·컨테이너 환경에 core·비밀·표식 0, runner id는 무작위 값. 같은 호스트의 기능 분리이며 별도 관리 주체의 호스트 배치는 검증하지 않았다 |
| 재배치(T6) | 새 경로(공백 포함)·외부 작업 디렉터리 | **PASS**(최종 배포본 3종): owner unit 2657/0·integration 17/17·relocation 8/8, public·registration unit 717/0·relocation 8/8, 외부 작업 디렉터리 재호출. 오프라인 설치: public ZIP을 네트워크 차단 상태로 설치 성공(서드파티 wheel 묶음 필요) |
| 누출 검사(T8) | 공개 산출물·응답·로그에 비공개 정보 부재 | **PASS**: 배포 ZIP 3종(표식·비밀 값·호스트 사실 0)과 최종 배포본의 런타임(응답·서비스 로그·gateway와 runner의 프로세스 환경에 비밀·표식·owner 쪽 문자열 0, health는 gateway 자신의 경로만). 1차 실행에서 나온 health 누출은 수정 후 재실행으로 확인. 캡처하지 않은 것: runner 학습 작업의 자식 프로세스 환경 |

최종 배포 ZIP(2026-10-28 07:0x) 이후의 변경(작업 임대 때 만료 lease 회수, 보존 정책의 원격 삭제 왕복, 문서·웹·운영 스크립트·패키저 보강)은 시험으로 확인했고, 배포 ZIP 재빌드와 그 재확인(T4–T8)은 남아 있다.

## 6. 운영 요구 사항

- **Python**: 3.10만 지원(`requires-python >=3.10,<3.11`).
- **주요 고정 버전**: torch 2.6.0(cu124 빌드), torchvision 0.21.0, ultralytics 8.4.173. ultralytics는 연산자 확장이 설치본의 `parse_model` 소스 hash를 확인하므로 다른 버전에서는 맞춤 연산자 등록이 명시적으로 실패한다.
- **GPU**: 학습·평가·벤치마크용 NVIDIA GPU. CPU 전용 runner는 학습·checkpoint·구조 생성까지만 지원하며, 지연시간 측정이 필요한 평가 완료 판정은 하지 않는다(평가가 필요한 작업은 checkpoint를 남기고 `NOT_EVALUATED`로 기록된다).
- **포트(기본값)**: core 8700, gateway 8710, 웹 개발용 preview 8720.
- **네트워크**: 온라인 설치는 PyPI와 PyTorch 배포 서버(`download.pytorch.org`)에 접속한다. 선택 사항인 오프라인 설치는 wheel 묶음이 필요하다.
- **Node.js**: 웹 화면을 다시 빌드할 때만 필요하다(서버는 빌드된 `mbnas_web/dist/`를 제공).

## 7. 관련 문서

| 문서 | 내용 |
|---|---|
| `SYSTEM_STRUCTURE.md` | System·Subsystem·Block·Module 표와 구조도(SVG·mermaid) |
| `INTERFACES.md` | 공개 API·콘솔 중계·runner 프로토콜·owner API 요약 |
| `FILE_LIST.md` | 등록용 ZIP의 파일 목록(매니페스트에서 생성) |
| `KEY_CONTRIBUTIONS.md` | 핵심 기여 1(구조 게이트·수리), 2(탐색 정책·상태) — 외부 관찰 동작만 |
| `SUBMISSION_SCOPE_TABLE.md` | owner·public·registration 배포 범위표 |
| `REVIEW_CHECKLIST.md` | 등록 전에 사용자가 채울 항목 |
| `RIGHTS_REVIEW.md` | 권리·라이선스 검토 질문 목록 |
| `../public/` | 설치·기능·운영·문제 해결·보안 모델·호환성·API 문서 |

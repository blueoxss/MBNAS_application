# FEATURES — 기능 (Features)

이 문서는 MBNAS Research Console 0.1.0의 기능과 지원 범위를 적는다.

## A. 설정과 경로 (Configuration and paths)

| 기능 | 설명 |
|---|---|
| 역할별 설정 | core(`MBNAS_CORE_*`), gateway(`MBNAS_GW_*`), runner(`MBNAS_RUNNER_*`) 설정을 분리. 기본 경로는 모두 `MBNAS_HOME`(기본 `<설치 위치>/runtime`) 아래 |
| 허용 경로 검사 | 데이터셋·출력·모델 YAML 경로는 설정된 허용 루트 안의 절대경로만 받는다. 브라우저 쪽 파일 경로가 아니라 **서버(고객 호스트) 경로**다. 분리 배치에서는 gateway가 고객 호스트에서 검사한다 |
| 프로토콜 경로 검증 | `console.paths`(데이터셋 YAML, train/search_val/final_eval, output_root)의 존재·쓰기·split 중복 검사. gateway가 검사 결과와 데이터셋 manifest를 core로 보내고, run·학습 생성 때도 같은 입력을 다시 측정해 보낸다. 분리 배치의 `output_root`는 core 호스트 밖의 고객 측 경로여도 된다 |

## B. 학습 실행 (Training runs)

| 기능 | 설명 |
|---|---|
| baseline 학습 | registry 모델(YOLO11·YOLO26·RT-DETR·RF-DETR·ETRI hybrid 계열)을 골라 별도 학습 run 생성. capability가 AVAILABLE이어야 한다 |
| 사용자 구성 학습 | 업로드한 YAML(`upload_id`) 또는 서버 경로의 YAML(구조 검사 PASS 필수)로 학습 run 생성 |
| 탐색·생성 후보 학습 | 탐색 run의 후보나 평가 없이 생성한 후보를 골라 학습. 원본 run·후보·구조 hash가 연결된다 |
| 학습 방식 | 처음부터(scratch), 사전학습 가중치로 추가 학습(pretrained), 공식 가중치 평가 전용(evaluate_only), 기존 run의 full-state 재개(resume) |
| 멱등 생성 | `Idempotency-Key`로 같은 요청의 중복 생성을 막는다 |

## C. 평가 가중치와 승급 (Fitness weights and promotion)

| 기능 | 설명 |
|---|---|
| rung 표 | rung마다 누적 epoch 목표, 지표 가중치, 정규화 기준, 승급 조건. 가중치 합은 1이어야 하며 조용히 정규화하지 않는다 |
| 적합도·승급 판정 | core만 계산한다. runner는 측정값만 보고하고, 웹은 서버가 계산한 값만 표시한다(웹 번들에 적합도 공식 없음) |
| 판정 표시 | 후보별 게이트 이력, 기본값 적용 표시, 승급 결정 코드 |

## D. 일시정지·재개·취소 (Pause, resume, cancel)

| 기능 | 설명 |
|---|---|
| 상태 전이 | `PAUSE_REQUESTED → PAUSED`, `RESUMING → RUNNING`, `CANCEL_REQUESTED → CANCELLED`. 같은 요청의 반복은 무해한 no-op |
| 안전 경계 정지 | 학습이 epoch 경계에서 full-state checkpoint를 저장한 뒤 `PAUSED`가 된다 |
| full-state 재개 | 모델·EMA·optimizer·scheduler·AMP·RNG·sampler 상태 복원. 상태가 없거나 맞지 않으면 가중치만으로 대체하지 않고 차단 |
| 장애 복구 | runner가 사라지면 core가 만료 lease를 회수하고, 다음 시도는 최신 full-state에서 이어 간다(같은 runner·작업 디렉터리). 이전 시도가 늦게 보낸 결과는 `409`로 거부된다 |
| 생성 작업 복구 | 평가 없는 생성 작업은 journal로 재시도·장애 뒤에도 재샘플링 없이 이어 간다 |

## E. 모니터링과 비교 (Monitoring and comparison)

| 기능 | 설명 |
|---|---|
| 진행·학습 곡선 | epoch별 lr·loss·처리량, 상태·rung·epoch·step, checkpoint 상태 |
| 세대 진행 | 세대 표와 세대선·대표선(성능 순위나 Pareto front가 아님을 표시) |
| 이벤트 스트림 | SSE 재생(`Last-Event-ID`/`after_seq`), 새로고침·재연결 복구. gateway가 core 스트림을 중계 |
| 작업자 상태 | runner·장치·GPU 사용률, 실행 환경 대조 라벨(`verified`·`mismatch`·`unknown`). runner id는 기본으로 무작위 `runner-<8 hex>` 표시명(호스트 이름을 쓰지 않음), 공개 응답의 작업자·장치 id는 opaque `wrk-…`·`dev-…` |
| 실제 평가 이미지 | 평가기가 만든 GT/TP/FN/FP 고정 표본 이미지, native 학습 이미지. 예시 이미지로 채우지 않음 |
| 아카이브 비교 | 고객 호스트의 연구 run 결과를 조건(데이터셋·split·단계·epoch·protocol_hash·장치·정밀도) 포함 비교 |

## F. 후보 상세와 구조 (Candidate detail and structure)

| 기능 | 설명 |
|---|---|
| gene 블록 구성도 | backbone/neck/head gene, 실제 `from` 연결·분기·skip·Concat/Add, 블록 종류 색상, 선택·확대·접기 |
| 블록 표 | 논리 블록 id·YAML 위치·from·repeats·args·입출력 shape·채널·stride·params. YAML 행과 내부 계층을 1:1로 가정하지 않음. 표의 위치 번호(Pos) 버튼을 누르면 구조 탭으로 바뀌고, 그래프 노드나 gene 목록을 누른 것과 같은 블록 상세가 열린다(URL `?block=`) |
| 비-YAML 백엔드 구조 | RF-DETR·ETRI hybrid는 forward hook 관측으로 공통 구조 표현 |
| 블록 설계 출처 카탈로그 | 블록의 계열·도입 시점·역할·참고 문헌(정적 표) |
| 구조 검사 | 사용자 YAML·registry 모델·후보의 CPU 구조 검사(PASS/FAIL/UNVERIFIED). 사용자 YAML은 빌드 전에 모듈 허용목록·인자 한도 검사, 실제 검사는 core 호스트의 자원 제한 프로세스 |
| 구성 YAML 업로드 | 브라우저에서 YAML 파일 업로드(≤1 MiB), 원문 바이트·sha256 보존 |
| 구성 개정 이력·diff | 원본·선택본·실행본·생성본의 개정 이력, 선택본↔실행본 diff, 개정별 원문 다운로드 |
| 구성 YAML 다운로드·내보내기 | 표시본·원문 다운로드와 hash 대조. 내보내기 ZIP에 실제 YAML 바이트 포함 |
| 평가 ON/OFF 분리 | 평가 OFF(RandomGeneration)는 구조 생성과 CPU 구조 검사만 하고 평가·적합도·승급이 없다(`NOT_EVALUATED`) |
| 생성 후보 표시 | 평가 없는 생성 후보를 "UNTRAINED · UNEVALUATED"로 표시(`training_status`), 계보 화면에 생성 후보 포함 |
| 학습 완료 · 미평가 표시 | validation을 끈 명시 학습의 결과와, CPU runner가 학습만 하고 평가하지 못한 후보(`SEARCH_COMPLETE` + `NOT_EVALUATED`, 사유 `EVALUATION_REQUIRES_CUDA`)를 "학습 완료 · 미평가"로 표시. 지표는 비어 있다. 배지 설명(툴팁)은 사유별로 다르다(CUDA 없는 runner, validation OFF, 그 밖의 미평가) |
| 구조 비교 | 부모·자식 또는 두 모델의 블록 추가·삭제·변경과 연결 차이 |
| 지연시간 출처 | `measured`(실측) 기본, `calibrated_proxy`는 프로토콜이 명시적으로 허용할 때만 |

## G. 콘솔 기능 (Console features)

| 기능 | 설명 |
|---|---|
| 프로토콜 preset·검증·run 생성 | 연결 시 서버 preset 자동 적용(편집기 미수정·run 미로드 상태에서만), 검증·resolved diff·`protocol_hash`, 오래된 검증 거부(`STALE_VALIDATION`) |
| 최종 재학습 | 완료된 탐색 run의 FINAL_RETRAIN child run(평가 OFF run에는 해당 없음) |
| GA·IndependentRandom 탐색 | 평가 ON 탐색, 세대 계획·예산 승인·비용 기록·세대 마감 |
| artifact 보안 다운로드 | id로만 조회(checkpoint 포함), 서명 URL 또는 Bearer, gateway가 고객 측 저장소에서 sha256 대조 후 제공. checkpoint의 `artifact_id`는 gateway가 처음 검증 다운로드한 뒤에 채워지며, 그 전에는 웹이 checkpoint id로 내려받는다 |
| run 내보내기 | protocol·평가·계보·세대·비용·checkpoint·artifact·작업·이벤트·provenance·환경 lock·SHA256SUMS + 구성 YAML 바이트. 공개 투영 적용, `X-Bundle-Sha256` |
| full-state 보존 정책 | `keep_all`, `rung_end_only`, `last_rung_end_only`. 원격 runner에서 실행한 run에도 같은 정책을 적용한다: core가 삭제 지시를 heartbeat(`control.prune`)로 보내고, runner가 지운 뒤 보고하면 checkpoint 행이 `PRUNED`가 된다. 정책과 관계없이 후보의 가장 최근 full-state checkpoint(재개 기점)와 평가 행이 참조하는 checkpoint는 지우지 않는다. gateway 저장소의 사본은 0.1.0에서 항상 남으므로 지운 checkpoint도 digest로 내려받을 수 있다 |
| health·doctor·registry | 상태·capability·registry 진단 |
| 벤치마크 | PyTorch FP32/FP16·ONNX Runtime(CUDA) 호스트 GPU 측정과 정확도 차이. **0.1.0은 콘솔 작업 큐로 벤치마크를 제공하지 않는다** — owner 호스트 스크립트 `scripts/runner/bench_host_gpu.py`로 실행(`OPERATIONS.md` §12). TensorRT는 설치 묶음에 없어 측정하지 않는다 |
| 웹 7화면 | 아카이브·Run 비교·탐색 모니터링·실험 설정·모델 계보·실행 기록·서버 연결, URL 상태 복원, 토큰은 메모리에만 보관 |

## H. 원격 실행과 배포 분리 (Remote execution and split deployment)

| 기능 | 설명 |
|---|---|
| runner 원격 프로토콜 | register·claim·start·heartbeat·progress·checkpoint·artifact·result·ack·deregister, fencing·job ticket·멱등 commit |
| gateway 중계 | `/api/v1` → core 콘솔 API, 오류 코드·request id 중계, core 부재 시 `503 CORE_UNAVAILABLE` |
| 요청률 제한·감사 로그·경로 위생 | 토큰별 요청률·고비용 경로 동시 실행·SSE 수 제한, 민감 조회 감사 로그, dot segment 거부 |
| 공개 응답 투영 | 계약 1.5.0: 공개 계약에 있는 필드만 응답(owner 전용 필드 제거, opaque id, 상대경로). gateway health의 허용 경로는 gateway 자신의 것만 |
| 고객 측 artifact 저장소 | runner 산출물을 같은 호스트의 gateway 저장소로 로컬 전송(하드링크/복사, 내용 주소), gateway가 digest 대조 후 제공 |
| core 쪽 생성 구조 YAML 제공 | 분리 배치에서 owner worker가 core 호스트(`<MBNAS_CORE_OUTPUT_ROOT>/<run id>/architectures/`)에 쓴 생성 구조 YAML을 후보의 artifact로 등록(정해진 위치·일반 파일·1 MiB 이하·기록된 YAML과 바이트 동일할 때만). core가 같은 조건을 다시 확인해 제공하고 gateway가 sha256을 대조해 중계한다. gateway 저장소에는 복사되지 않아 core 연결이 필요하다 |
| CPU·GPU 혼합 fleet의 작업 배정 | CPU runner는 CUDA 측정이 필요한 작업(평가 ON 탐색·최종 재학습, 평가하는 명시 학습, BENCHMARK)을 같은 종류를 받는 GPU runner가 `ONLINE`일 때 건너뛴다(작업은 `QUEUED`로 남고 시도 횟수를 쓰지 않음). CPU runner만 있으면 종전대로 받아 `NOT_EVALUATED`로 끝낸다 |
| 정적 웹 서빙 | gateway가 `mbnas_web/dist`를 `/`에서 제공(Node 불필요) |

## I. 운영과 설치 (Operations and installation)

| 기능 | 설명 |
|---|---|
| 설치 | Docker compose, `pip install -r requirements-<profile>.txt`, 또는 `scripts/install.sh`(래퍼). 배포본에는 프로젝트 wheel만 들어 있으므로 오프라인 설치에는 서드파티 wheel 묶음(`--wheelhouse`)이 필요하다 |
| 진단·기동·정지·검증 | `scripts/doctor.sh`, `start.sh`, `stop.sh`, `verify.sh`. 어느 작업 디렉터리에서 불러도 스크립트 위치로 ROOT를 찾는다 |
| 배포본 내용 목록 | 각 배포본(owner·public·registration)의 최상위 폴더에 파일별 경로·크기·sha256·역할·라이선스를 담은 `FILE_MANIFEST.jsonl`과, 같은 내용을 사람이 읽는 표로 만든 `PACKAGE_CONTENTS.md`가 있다. `verify.sh --suite package`가 매니페스트를 대조한다 |

## 지원 범위 (Scope)

- **CPU 전용 runner**: 학습·checkpoint·구조 검사·평가 없는 생성까지 지원한다. 평가 지연시간은 CUDA에서만 측정하므로, 검증용 장치 목록이 CPU뿐이면 평가 ON 탐색은 검증 단계에서 `EVALUATION_REQUIRES_CUDA`로 거부된다(학습과 평가 OFF 생성은 허용). GPU 없는 core 호스트의 기본 장치 목록(`MBNAS_CORE_DEVICE_SOURCE=local`)은 실행 중인 CPU runner의 `cpu` 항목으로 대신한다(GPU runner가 다른 호스트에 있으면 `runners`). CPU runner가 평가가 필요한 작업을 맡으면(예: validation을 켠 명시 학습) checkpoint를 남기고 `SEARCH_COMPLETE` + 성능 상태 `NOT_EVALUATED`(사유 `EVALUATION_REQUIRES_CUDA`)로 끝나며 가짜 지표를 만들지 않는다. 명시 학습 run은 `COMPLETED`가 되고, 탐색 후보는 평가되지 않은 채 승급·부모 선택에서 빠진다. 실행 장치는 결과(`device_used`)에 기록된다.
- **CPU runner만 있는 fleet의 평가 ON 탐색**: 보통 검증에서 거부되지만, GPU가 있는 장치 목록으로 검증된 run(예: GPU 있는 core 호스트의 `local`)을 CPU runner만 실행하면 모든 후보가 `NOT_EVALUATED`로 끝난다(순위 없음, 계산 낭비). GPU·CPU 혼합 fleet에서도 GPU runner가 멈추면(`ONLINE`이 아니면) 남은 평가 작업을 CPU runner가 가져갈 수 있다(`OPERATIONS.md` §14).
- **분리 배치의 학습 경로**: core와 gateway를 다른 호스트에 둘 때 학습 프로토콜은 `console.paths`에 `train_root`·`search_val_root`를 명시해야 한다. `dataset_yaml`만 주면 split 경로를 core 호스트에서 찾다가 실패한다.
- **1.4.x 시절 DB 비호환**: 0.1.0(계약 1.5.0)에서 검증 hash 정의가 바뀌었다. 이전 판에서 만든 DB의 run은 재개·runner 검사에서 hash가 맞지 않으며, 0.1.0은 마이그레이션 도구를 제공하지 않는다(`COMPATIBILITY.md` §5).
- **벤치마크**: 콘솔 작업 큐로는 제공하지 않는다(owner 호스트 스크립트만). TensorRT는 설치 묶음에 없어 측정하지 않는다.
- **artifact 전송**: runner → gateway는 같은 호스트의 로컬 전송만 지원한다. 네트워크 업로드 방식(`upload`)은 0.1.0에 구현되지 않았다(설정하면 거부). 다른 runner로의 checkpoint 전송 후 재개와 lease 배정 시 checkpoint 위치 고려는 없다.
- **원격 prune 왕복**: 삭제 지시는 그 runner가 작업 lease를 가진 동안의 heartbeat에만 실린다. 다음 작업을 받지 않는 runner의 삭제 요청은 대기로 남고 checkpoint 행은 `PRESENT`로 남는다. runner는 자기 작업 디렉터리의 그 run 아래 일반 파일만, sha256과 크기가 지시와 같을 때만 지운다(심볼릭 링크와 gateway 저장소 안은 지우지 않으며, 맞지 않으면 `already_absent`로 보고하거나 거부한다). 저장소 전송이 끝나지 않은 파일은 미룬다(`PRUNE_TRANSFER_PENDING`). 보고는 보내기 전에 기록해 core가 내려가 있어도 나중에 다시 보낸다. 삭제 요청 목록을 보는 API는 없다(owner DB에만 있다). ultralytics 학습기가 중간 상태 파일을 먼저 지운 경우 runner는 `already_absent`로 보고한다.
- **큰 객체의 변조 탐지**: 64 MiB(`MBNAS_GW_ARTIFACT_VERIFY_FIRST_MAX_BYTES`)를 넘는 객체는 전송하면서 sha256을 검사하므로, 불일치는 응답이 시작된 뒤 본문 중단으로 나타난다. 받은 쪽은 `X-Artifact-Sha256` 헤더와 대조해야 한다.
- **브라우저의 sha256 대조**: 웹은 WebCrypto가 허용되는 HTTPS 또는 localhost에서만 내려받은 파일의 sha256을 계산한다. 평문 LAN 주소로 접속하면 "대조 못 함"으로 표시된다(서버 hash는 표시). id 기반 다운로드는 브라우저 메모리에 담으므로 아주 큰 checkpoint에는 맞지 않는다.
- **GPU 재현성**: ETRI attention 블록을 가진 후보는 CUDA memory-efficient attention의 비결정성 때문에 GPU에서 bitwise 재현되지 않는다.
- **GPU 번호**: 직접 실행하는 GPU 스크립트에는 `CUDA_DEVICE_ORDER=PCI_BUS_ID`를 설정해 CUDA 번호와 `nvidia-smi` 번호를 맞춘다(runner 작업은 자동 설정).
- **RF-DETR native 재개**: 전체 checkpoint를 pickle로 읽어야 하므로 공개 runner에서는 기본으로 꺼져 있다(`MBNAS_RUNNER_ALLOW_PICKLE_RESUME=false`). owner 프로필은 켠다.
- **외부 checkpoint import·과거 체크포인트**: 다른 곳에서 만든 checkpoint를 새 run에 붙여 이어 가는 기능은 없고, 원본 연구 코드의 모듈 경로로 저장된 ultralytics `.pt`는 읽지 못한다(`COMPATIBILITY.md`).
- **관리 경계**: 정책 비공개는 core가 고객과 다른 관리 주체의 호스트에 있을 때만 성립한다(같은 호스트에서 프로세스만 나눈 배치는 관리 경계가 아니다). owner 관리 API는 admin 토큰만 검사하므로 core를 원격에 열 때는 proxy가 `/core/v1/owner/`를 차단해야 한다(`SECURITY_MODEL.md` §4).

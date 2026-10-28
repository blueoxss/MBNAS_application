# 원 지침 원문 (사용자 붙여넣기, 2026-10-23; 세션 transcript에서 10-28 복원)

# MBNAS Application 구현·보호·독립 실행·ZIP 패키징 최종 지침

당신은 MBNAS의 NAS·소프트웨어 설계·보안 기술 책임자다. 서버의 실제 코드를 근거로 분류→분석→계획→구현→검증→평가→수정을 수행하라. 기존 작업을 이어받고, 아래 요구를 통합하여 실제 실행 가능한 프로그램과 검증된 ZIP을 만들어라. 계획서·mock·빈 구현만 남기고 끝내지 마라. 이 지침은 이전 지침 중 작업 경로·프로젝트 명명·추출 위치와 충돌하는 부분을 대체한다.

## 1. 프로그램 이름과 작업 위치

- 표시명: MBNAS Research Console.
- 국문 설명명: MBNAS 모듈형 신경망 구조 탐색·실험 통합관리 프로그램.
- 프로젝트 루트: mbnas_application.
- 원본 etri_nas와 같은 상위 디렉터리의 형제 폴더로 만든다.

실제 위치를 조사한 뒤 SOURCE_ROOT를 기존 etri_nas의 절대경로로, TARGET_ROOT를 SOURCE_ROOT.parent / "mbnas_application"으로 확정하라. 예를 들어 원본이 /home/mgjeong/Desktop/etri_hd/etri_nas이면 대상은 /home/mgjeong/Desktop/etri_hd/mbnas_application이다. 예시 경로를 실제 확인 없이 하드코딩하지 마라.

원본은 읽기 전용 참조로 유지한다. 원본 내부에 새 구현·테스트·문서·venv·빌드물을 만들지 말고, 원본 Git/설정/DB/산출물/실험 프로세스에 쓰지 마라. 필요한 최신 미커밋 기능까지 선별하여 대상에 복사하되 파일별 원본 경로·HEAD·dirty 여부·복사 시점·SHA-256·라이선스·역할을 기록하라. 전체 저장소 복사 후 몇 파일만 지우는 방식을 사용하지 않는다. 이후 구현·문서·검증·패키징은 모두 TARGET_ROOT에서 수행한다.

TARGET_ROOT가 이미 있으면 기존 파일과 TASK_STATE를 먼저 확인하고 이어서 작업한다. 삭제·초기화·무조건 덮어쓰기를 하지 않는다. 원본에서 동시에 진행 중인 작업은 입력 스냅샷과 구분하고, 이후 변경을 원본 훼손이나 본 작업의 회귀로 오판하지 마라.

## 2. 확정 하위 구조와 명명 규칙

다음은 신규 프로젝트의 목표 구조다. 기존 application/MBNAS_server와 application/MBNAS_web를 각각 아래 구성으로 발췌·정리한다. 신규 디렉터리는 소문자 snake_case, Python 배포명은 kebab-case, import 이름은 snake_case를 사용한다. 기존 mbnas_server import는 불필요하게 전면 개명하지 않는다.

| TARGET_ROOT 하위 경로 | Python 배포명 / import | 역할·배포 범위 |
|---|---|---|
| mbnas_web/ | npm: mbnas-web | React/Vite 모니터링·관리 UI, 공개 가능 |
| mbnas_server/ | mbnas-server / mbnas_server | 사용자 API·gateway·공개 조회, 공개 가능 |
| mbnas_runner/ | mbnas-runner / mbnas_runner | 승인 작업의 실제 학습·평가·측정·재개, 공개 가능 |
| mbnas_core/ | mbnas-core / mbnas_core | 구조 gate/repair·검색·승급·정책 상태, 권리자 전용 |
| mbnas_model_runtime/ | mbnas-model-runtime / mbnas_model_runtime | 승인 모델 구축에 필요한 범용 operator·registry, 공개 가능 |
| mbnas_contracts/ | mbnas-contracts / mbnas_contracts | 공개 DTO·API schema·공유 타입, 공개 가능 |
| config/examples/ | 해당 없음 | 자격증명 없는 설정·배포 프로필 예시 |
| examples/ | 해당 없음 | 작은 공개 가능 모델 구성·시험 데이터·사용 예제 |
| tests/public/ | 해당 없음 | 공개 계약·실행·패키징 검증 |
| tests/owner/ | 해당 없음 | 비공개 코어 동등성·정책·장애 검증, 내부 전용 |
| scripts/ | 해당 없음 | 설치·doctor·시작·종료·검증·ZIP 생성 |
| deploy/public/, deploy/owner/ | 해당 없음 | 공개 실행 환경 / 권리자 코어 배포 설정 |
| docs/public/ | 해당 없음 | 설치·기능·운영·문제 해결 설명서 |
| docs/registration/ | 해당 없음 | 등록 항목·구성도·주요 기능·기여·제출 검토 목록 |
| docs/internal/ | 해당 없음 | 분류·계획·위협모델·내부 감사·평가 기록 |
| runtime/ | 해당 없음 | 실행 시 생성하는 DB·로그·임시 산출물, 배포 제외 |
| dist/public/, dist/owner/, dist/registration/ | 해당 없음 | 프로필별 완성 ZIP·외부 checksum |
| build/ | 해당 없음 | 프로필별 allowlist staging·중간 빌드물, 배포 제외 |

루트에 README.md, VERSION, BUILD_INFO.json, PACKAGE_PROFILE.json, pyproject.toml 및 실제 설치 방식에 맞는 lock/constraints, THIRD_PARTY_NOTICES.md, LICENSE 또는 권리 검토 상태를 둔다. VERSION은 기존 정식 버전이 있으면 계승하고, 없으면 0.1.0부터 시작한다. Python 패키지별 pyproject와 웹 package.json/lock을 포함하고, 실제 빌드되는 의존관계로 정리한다. 이름만 있는 빈 패키지나 실행되지 않는 스크립트를 만들지 마라.

## 3. 핵심 보호 구조와 독립 실행의 정의

보호할 핵심 기여는 다음 두 가지다.
1. 학습 전에 블록·from·채널·shape·stride·분기·Concat/Add·검출 head·지원 모듈의 구조 유효성을 확인하고 필요한 repair를 수행하는 gate.
2. 후보 생성·부모 선택·교차·변이·단계별 fitness/승급·예산·종료를 결정하는 검색 정책과 그 상태.

채택 구조는 권리자가 관리하는 private core/control plane + 공개 Web/API + GPU runner다. 기존 서버의 권위 있는 orchestration·정책 DB·검색 RNG·선정 로직은 core에 둔다. 고객 측 저장소는 실행 journal·재전송 큐·공개 조회 캐시로 제한하고 같은 정책을 독자적으로 다시 결정하지 않는다. PROPOSE_GENERATION과 구조 검사는 owner worker에서 실행하며 runner가 받을 JobKind를 허용목록으로 고정한다.

독립 실행 수용조건은 다음 세 가지를 각각 만족해야 한다.
A. 원본 저장소 독립성: etri_nas 및 이전 checkout/build에 접근하지 않고 빌드·설치·실행·테스트한다.
B. 권리자 전체 통합 실행: mbnas_application 내부의 실제 core/server/runner/web와 포함된 시험 자원만으로 gate·검색·학습·평가를 실행한다.
C. 공개 ZIP 실행: ZIP의 server/runner/web만 설치하고 실제 권리자 core endpoint에 연결해 동작한다. 고객 환경에 private core 구현은 없다.

Python/Node/OS/CUDA 및 명시적으로 선언·고정한 외부 라이브러리는 실행 플랫폼이다. 실제 사용자 데이터·가중치의 /data 경로는 입력이다. 자체 소스·operator·빌드 자원·테스트 fixture는 반드시 프로젝트 또는 해당 배포물 안에 있어야 한다. /data 입력을 통해 원본 Python 모듈·실행 스크립트·동적 loader를 우회 참조하지 않는다. 기본 CPU smoke는 작은 포함 데이터로 가능하게 하고, 외부 대형 데이터/가중치 다운로드를 필수로 만들지 마라.

공개 ZIP은 실제 원격 core 연결이 필요한 프로그램으로 설명한다. core를 제외하면서 오프라인 전체 NAS가 된다고 주장하지 마라. owner 전체 ZIP은 private 코드를 포함하므로 공개·고객 전달 대상에서 제외한다. 한 host의 프로세스 분리는 기능 검증이며 고객 root에 대한 보안 경계 증거가 아니다. 실제 보호 운영은 별도 관리자 신뢰영역의 core host를 사용한다.

핵심을 빈 함수·고정 PASS·미리 저장한 후보·mock 서비스·로컬 fallback으로 바꾸지 않는다. 승인 모델의 gene/YAML/가중치는 사용자에게 제공하므로 모델 결과의 비밀과 생성·검증 정책의 비밀을 혼동하지 않는다. 필요한 custom operator 구현은 독립 runtime에 포함한다. operator 자체를 비밀로 하면서 고객 장비에서 실행하겠다는 충돌이 있으면 정확히 보고하라. 첫 구현은 기존 Python/PyTorch를 재사용하고 Rust/TEE/난독화 전체 개조는 후속 ADR로 분리한다.

## 4. 모델별 역할과 작업 운영

사용자 지정 fable5.1 ultra를 분류·분석·주 계획·절차/가이드 수립·설계 결정·검증 충분성 판단·평가·피드백·최종 승인 담당으로 배정하라. 하위 모델은 승인된 모듈 구현·리팩터링·테스트·shell/설치/배포 스크립트 작성·실행·문서 정리를 담당한다. 하위 모델이 연구 조건·알고리즘·신뢰경계를 독단 변경하지 않는다.

실제 requested_model/resolved_model_id/요청 및 적용 모드/지원 여부/확인 근거를 MODEL_ROUTING.md에 기록한다. fable5.1과 ultra는 사용자 별칭일 수 있으므로 존재하지 않는 모델 ID·옵션을 만들지 않는다. 조회 기능 부재와 모델 부재를 구분한다. 확인 불가 시 HIGH_MODEL_REVIEW_PENDING으로 두고 이미 이 지침에서 정한 가역적 구현·조사·증거 수집은 계속한다. 새로운 주요 결정과 최종 승인을 하위 모델이 대신했다고 쓰지 마라.

병렬 작업은 실제 지원할 때 파일 소유권·입력 버전·인터페이스·수용조건을 정해 수행한다. 같은 파일에 동시 쓰지 않는다. 기존 archive API·web archive·운영 스크립트·12-탐색 작업의 결과를 보존하고 인터페이스를 통합하라. 해당 작업을 중지하거나 과거 버전으로 되돌리지 않는다.

## 5. 실제 코드 기준선·환경 분류

참고: blueoxss/ETRI_NAS, 2026-10-23에 확인한 main SHA cc5f3e9191a9739e136302533297689a6de5f117. 이는 참고 스냅샷이며 현재 서버를 되돌리라는 지시가 아니다. 로컬 HEAD/branch/dirty 상태, 적용되는 AGENTS.md/CLAUDE.md/README와 인수인계를 우선 조사하라.

application/MBNAS_server의 search/{genotype,space,ga,random,handlers,streams,lineage,etri_bridge}.py, orchestration/, protocol/{fitness,promotion,validator}.py, scheduling/, worker/, training/, api/, artifacts/, export/bundle.py, 공개 contracts와 MBNAS_web의 CandidateView/fitness/이벤트/아카이브 경로를 실제 호출관계로 추적하라. 이름과 존재 여부는 로컬에서 재확인한다.

각 기능을 구현·실시험 확인 / 구현·통합 미확인 / 부분 구현 / 계획 / 누락 / 증거 부족으로 분류하고 파일·함수·호출자·저장소·실행 주체·공개 여부·검증 근거를 연결한다. 네트워크·패키지·경로 경계를 가로지르는 import closure와 동적 registry까지 조사한다.

기존 sg26=/home/mgjeong/venvs/sg26, /data 가중치·데이터와 실행 중 실험을 보존한다. 참고 앱의 Python 요구 >=3.10,<3.11과 실제 연구 Python/torch/CUDA를 대조하고 앱용 격리 환경을 선택한다. 기존 venv를 덮거나 연구 환경을 일괄 업그레이드하지 않는다. Node/React/Vite 명령은 실제 package.json/lock에 따른다. 새 DB·출력·포트·프로세스 이름을 사용한다.

가역적 구현·격리 설치·로컬 시험·빌드·ZIP 생성은 수행한다. push/저장소 공개/실제 release·생산 배포/기존 DB 이관/실험 중단/파괴적 삭제/장시간 대규모 GPU 실험은 별도 승인 없이 수행하지 않는다. 별도 승인이 필요한 마지막 행동 전까지의 검토 가능한 준비는 완료하라.

## 6. NAS와 상태 의미 보존

기준 코드에서 실제 동작을 확인하고 다음 의미를 보존하라.
- 구조 gate, 실행 준비 검증, 평가 후 승급 gate를 분리한다. 해상도 stage·평가 rung/phase·GA generation을 혼동하지 않는다.
- CPU 모델 구축·참조 forward·hook의 구조 PASS는 확인한 입력/연산의 구조 유효성이다. GPU 학습·loss·정확도를 보증하지 않는다.
- GA/IndependentRandom의 generation 0 공통 frozen prior, 후속 Random의 부모 fitness 독립성, 동일 template family 교차, parent rung과 확정 평가, RNG 호출 순서·elite·tie-break를 보존한다.
- fitness와 승급의 source rung 가중치, 승급 자격과 budget admission, baseline의 별도 의미, final retrain의 split/초기화/비교군을 보존한다. 논문의 설정을 현재 코드 기본값으로 임의 덮지 않는다.
- 무효/미확인 지표를 성공·0점·실측으로 바꾸지 않는다. calibrated proxy와 measured를 구분하고 기존 hard constraint 조건을 보존한다.
- search RNG/내부 ID/정렬 키를 바꾸지 않고 안정적인 공개 opaque ID를 매핑한다. training seed와 search seed의 기존 연계를 기록하며 이미 공개된 seed를 완전 비밀이라고 쓰지 않는다.
- model/EMA/optimizer/scheduler/scaler/훈련 RNG를 포함하는 full-state 복구와 안전한 tensor/container checkpoint 저장을 보존한다. weights-only나 scratch 재시작으로 조용히 대체하지 않는다.

## 7. 원격 계약·트랜잭션·장애

기존 중앙 큐/lease를 가능한 한 재사용하여 claim→heartbeat→progress→result commit→ack를 구현한다. 공개 envelope에는 job/attempt/후보 공개 ID/실행 종류/구성 digest/runtime·schema 버전/공개 recipe/데이터 참조/lease·fencing을 둔다. parent pool·private resolved policy·raw search state 전체를 고객에게 보내지 않는다.

core가 인증 주체/run/job/attempt/stage/config digest/expected revision/lease epoch를 검사한다. 늦은 이전 attempt·취소·재할당 결과가 현재 작업을 덮어쓰지 못하게 한다. 고객의 eligible/rank/fitness/예산 잔액을 정책 결정으로 신뢰하지 않는다. 인증된 결과도 고객 root가 만든 측정의 진실성을 보증하지 않으므로 해당 신뢰 한계를 표시한다.

DB transaction 안에서 네트워크·모델 구축을 기다리지 않는다. 영속 큐·실행 journal·outbox/inbox 또는 동등 구조로 짧은 commit과 외부 I/O를 분리한다. 후보 출력+RNG/결정 상태+policy revision+멱등 응답을 원자적으로 확정한다. 장시간 계산은 불변 snapshot/version CAS/결정 직렬화로 중복 확정을 막는다.

동일 idempotency key의 확정 응답을 revision 검사보다 먼저 조회한다. 같은 digest이면 이전 revision 재시도에도 같은 응답을 재생하고, 다른 payload면 충돌, 처리 중이면 PROCESSING/재조회로 처리한다. 응답 유실/중복/경쟁/재부팅/commit 실패/취소 경쟁에서 후보·RNG·청구가 중복되지 않아야 한다. 네트워크 자체의 exactly-once를 주장하지 않는다.

timeout의 실행 여부가 불명인데 예산을 자동 환급·재청구하지 않는다. 동기화 미완료 작업을 남긴 채 Run을 완료시키지 않는다. core 장애·인증 만료·미지원 연산·손상 checkpoint에는 명시적 대기/차단/실패를 둔다. 로컬 검색/검증 생략/OFF 전환/새 seed/자동 scratch fallback은 금지한다. 승인된 진행 중 학습의 체크포인트 보존과 신규 작업 차단을 구분한다.

## 8. 입력·인증·산출물 보안

브라우저/runner/core 관리자 권한과 자격증명을 분리한다. 운영 TLS 검증과 scope/대상/수명 제한 서비스 인증 또는 필요한 mTLS를 검증된 라이브러리로 구현한다. core 개인키·DB 자격증명·관리 키를 고객에 넣지 않는다. 작업 티켓은 job/attempt/stage/digest/만료·세대에 바인딩하고 암호를 자작하지 않는다.

YAML safe parsing에 모듈 allowlist·인자 타입/문자열·연결/shape/반복·입력 크기·연산량·시간/메모리 제한을 더한다. eval/임의 callable/import/pickle/자동 플러그인 설치/임의 URL을 허용하지 않는다. 고객 operator manifest는 호환성 주장이다. core에 설치·고정한 registry만 사용한다. CPU 구조 검사는 자원 제한된 비특권 subprocess로 격리하고 전역 backend/hook 정리와 동시 요청 간섭을 검증한다.

원격 artifact는 runner_id/artifact ID/namespace/digest/크기/유형으로 참조한다. 고객 /data 경로를 core 로컬 경로로 처리하지 않는다. host별 허용 경로·실제 바이트 검증·보고된 존재를 구분하고 인증된 전송/relay/backend를 실제 구현한다. 다운로드되지 않는 URL만 만들지 마라. checkpoint affinity 또는 승인 전송 후 재개를 구현한다.

원본 YAML 바이트 hash, 정규화 실행 구성 hash, 구축 구조 hash는 의미·알고리즘·버전을 구분한다. 원본을 덮거나 서로 다른 hash가 같아야 한다고 요구하지 않는다. shell은 사용자 입력을 명령 문자열에 결합하지 말고 argv/구조화 인자를 사용한다. 공개 오류에는 안정적 코드·위치·request ID를, owner 로그에는 필요한 상세를 두되 토큰·개인키를 기록하지 않는다.

public DTO/owner audit DTO를 허용목록으로 분리한다. API/SSE/event/snapshot/OpenAPI/TS/browser bundle/source map/CSV/JSON/ZIP/archive/로그 전체에 적용한다. private computeFitness/정책 매개변수·prior·repair trace·search state가 프런트에 남지 않게 한다. 사용자 가중치·공개 연구 수식·결과 설명과 private 정책을 구분한다. 반복 질의의 정책 추정 위험에는 scope·quota·rate limit·감사를 두되 사용 가능한 진단을 유지한다.

## 9. 반드시 유지·완성할 기능

전체 모델 실험 관리 프로그램으로 구현한다. 무인이동체는 데이터 적용 예시다. 프로젝트/데이터셋/모델/가중치/결과 경로, baseline·사용자·검색 모델 학습, 단계별 가중치와 epoch 의미, pause/resume/cancel, 곡선·세대 개선·Pareto·조건별 비교·체크포인트/출처를 유지한다.

그래프/표/목록/모델을 클릭하면 동일 후보의 상세로 연결하고 다음 세 가지를 기본 성능 표와 함께 제공한다.
1. gene 기반 블록 구성도: backbone/neck/head 구분, 실제 from·분기·skip·Concat/Add, CNN/등록 attention 혼합 배치, 선택 블록과 상세 연동.
2. 블록 구성 표: 논리 블록 ID, from, repeats, module, args, 입출력 shape/채널, stride, params와 해당 YAML 행. 확장 전 논리 블록과 반복 확장 후 내부 계층을 혼동하지 않는다.
3. 실제 configure 파일: yolov5_modified.yaml 등 업로드·안전 검증·원본 보존·버전/hash·실행본/diff·다운로드·실제 YAML 바이트를 포함한 아카이브 내보내기.

원본 [from, number, module, args]를 손실 없이 보관하고 gene↔논리 블록↔YAML 행↔구축 계층을 추적한다. 잘못된 예제는 원본을 유지한 채 정확한 실패 위치·진단을 표시하고 정상으로 꾸미지 않는다. 새 구조도는 실제 그래프/등록 operator를 반영하며 고정 스크린샷으로 대체하지 않는다.

evaluation ON은 실제 검색·학습·평가·단계 결정을 수행한다. OFF는 명시적으로 요청한 ‘독립 무작위 생성+구조검사만’으로 구현하고 UNTRAINED/UNEVALUATED로 표시한다. 기존 IndependentRandom의 실제 학습·평가 모드와 구분하고 가짜 metric/fitness/Pareto 자격을 만들지 않는다. core 장애 시 OFF로 자동 전환하지 않는다.

기존 병렬 구현을 재작성하지 말고 합친다. 최소 보안 수직 경로를 먼저 완성하되 위 기능을 후속 필수 작업으로 추적하고, 미완성인데 전체 기능 완료라고 보고하지 않는다.

## 10. 원본 의존성이 없는 설치·실행

새 빈 환경에서 target의 manifest/lock과 빌드된 wheel로 설치한다. 원본 editable install, .pth, 부모 PYTHONPATH, user-site 의존, 원본 symlink/volume, 원본 모듈 re-export를 허용하지 않는다. 자체 패키지의 동적 import/registry/package-data/프런트 asset/템플릿/마이그레이션까지 포함한다.

프로젝트 자체 모듈의 __file__/import origin과 설치 wheel을 추적해 target 또는 해당 ZIP의 빌드물에서 왔음을 입증한다. 선언된 third-party 패키지와 자체 모듈을 구분한다. 버전 조회가 Git 실행·원본 checkout·개발자 home에 의존하지 않도록 BUILD_INFO를 사용한다.

클린 검증은 include-system-site-packages=false, user-site 차단, 외부 PYTHONPATH 제거와 새 컨테이너/격리 환경으로 수행한다. venv·Node 설치물·캐시는 TARGET_ROOT/runtime 또는 해당 압축 해제본 내부에 생성하고 PIP_CACHE_DIR/npm_config_cache/XDG_CACHE_HOME을 격리한다. HOME을 바꾸거나 global install·기존 사용자 캐시에 의존하지 않는다. 생성 환경은 ZIP에서 제외하며 설치·빌드의 자체 코드는 해당 프로필의 압축 해제본에서만 가져온다.

다른 절대경로와 공백이 있는 경로에 재배치하고 cwd를 프로젝트 밖으로 바꿔도 제공 명령으로 동작해야 한다. 스크립트는 자신의 실제 위치를 기준으로 root를 찾고 경로를 올바르게 인용한다. 기본 writable 경로는 프로젝트 runtime 하위로 두고 운영 외부 경로는 명시 설정으로만 허용한다.

참조 저장소를 mount하지 않는 격리 환경에서 원본 접근을 차단한다. 실제 원본을 rename/delete/chmod하거나 실행 중 프로세스를 방해하지 않는다. 검증 도구만 원본과 비교한 뒤 실행 환경에는 원본과 개발 폴더를 노출하지 않는다. 깨끗한 시험에서 원본 설치물·이전 캐시·host 자격증명·데이터를 자동 재사용하지 않는다.

scripts/install.sh, doctor.sh, start.sh, stop.sh, verify.sh, package.sh를 실제 작성하라. 권장 CLI는 --profile owner|public, --suite unit|integration|relocation|package|gpu|all, package의 --profile owner|public|registration이다. 실제 구현과 README를 일치시키고 지원하지 않는 옵션을 문서에 넣지 않는다. start/stop은 자기 프로젝트·프로필의 pid/포트만 관리하며 임의 kill/pkill을 하지 않는다.

registration 실행 ZIP은 public 실행 모드와 동일하게 시작하되 자신의 패키지 식별자를 유지한다. all은 프로필별 수용 시험 전체를 의미한다. public에 포함되지 않은 owner 시험은 NOT_APPLICABLE로, 필요한 실제 core/GPU 부재는 BLOCKED_BY_ENV로 기록하고 통과 수에 넣지 않는다. 테스트 수집·doctor·도움말을 위해 private 모듈을 import해서는 안 된다. public에서 owner 프로필 요청은 명시적으로 거부한다.

## 11. ZIP 프로필·내용·이름

버전은 VERSION, platform은 실제 검증 OS/architecture를 사용한다. source 실행물인지 플랫폼별 wheel 포함 실행물인지 manifest에 명시한다. 모든 ZIP은 최상위 mbnas_application/ 하나로 시작하며 ZIP마다 자신의 PACKAGE_PROFILE.json과 BUILD_INFO.json을 가진다.

| ZIP 이름 | 포함 범위·사용 |
|---|---|
| mbnas_application-<version>-public-runtime-<platform>.zip | Web/server/runner/public contracts/model runtime·설정 예시·공개 시험·문서. 실제 owner core 연결 필요 |
| mbnas_application-<version>-owner-private-<platform>.zip | 실제 core와 전체 통합 실행에 필요한 프로젝트 파일. 권리자 내부 설치/검증 전용, 공개·고객 전달 금지 |
| mbnas_application-<version>-registration-runtime-<platform>.zip | 검증된 public-runtime에 등록 검토용 설명·파일 범위표를 더한 실행 검토 패키지. core 연동 조건·실제 포함 범위를 명시 |

등록 ZIP은 실행 검토용 산출물이다. 기관 제출용 소스/발췌/바이너리 범위는 별도 확인하고 실제 등록 대상 설명과 일치시킨다. 확인 전 제출 승인·등록 수리·핵심 생략 허용을 확정하지 않는다. 실제 제출이나 공개는 수행하지 않는다. owner ZIP을 등록/public ZIP에 넣거나 프로젝트 루트/dist 전체를 다시 압축하지 마라.

프로필별 allowlist를 먼저 만들고 해당 staging만 ZIP/Docker build context로 사용한다. public에 private 파일을 넣었다가 지우는 방식은 금지한다. core 소스·reference backend·정책 fixture·private wheel/image·감사자료·관리 키는 public/registration에서 제외한다. local/owner 환경변수만 바꿔도 코어 구현이 나타나면 실패다.

프로필별 루트 pyproject/workspace·lock/constraints·설치/빌드/타입 생성/검증 설정을 생성·검증하고 그 버전·hash를 기록한다. public/registration의 필수 dependency·workspace member·package discovery·기본 test path·스크립트는 제외된 mbnas_core/tests/owner/deploy/owner 파일을 요구해서는 안 된다. private extras나 임의 pip 설치로 누락을 메우지 말고 public 의존성 closure만 설치한다. 공통 constraints·optional 그룹·동적 import도 private 패키지 자동 설치를 유발하지 않아야 한다.

공통 제외: 실제 .env/토큰/개인키, .git, venv/node_modules/cache/__pycache__, 기존 DB·로그·checkpoint·대형 데이터/가중치, 과거 ZIP·build/dist 재귀, 개발자 절대경로. 설정 예시만 포함하고 비밀은 실행 시 안전하게 주입한다. 공개 런타임에 필요한 웹 production asset과 package-data는 반드시 포함한다. 소스 배포라면 lock·빌드 명령을, 사전 빌드 배포라면 실제 호환 산출물을 포함한다.

ZIP 내부의 절대경로/.. 경로 탈출/외부 symlink/중복 entry/대소문자 충돌/숨은 private 패키지/배포 누출을 검사한다. 자체 파일 manifest에는 상대경로·크기·SHA-256·역할·출처·라이선스를 기록한다. private 원본 경로·코드 mapping은 owner provenance에만 두고 공개 manifest에는 안전한 식별자를 사용한다. ZIP 자체 checksum은 ZIP 바깥의 <archive>.sha256에 쓰며 manifest 자기 자신의 hash 순환을 만들지 않는다.

ZIP의 실행 비트 보존에 의존하지 않도록 bash scripts/install.sh 등 명시적 인터프리터 호출을 기준 명령으로 문서화하고 압축 해제본에서 검증한다. launcher가 필요하면 설치 시 생성·권한 설정하고 지원 shell·LF 줄바꿈·shebang을 검증한다. Git이 없어도 설치/버전 조회/시작이 가능해야 한다. 재현 가능한 빌드가 필요하면 파일 순서·timestamp·권한과 도구 버전을 고정하고 실제 재빌드 hash 비교 후에만 해당 성질을 주장한다.

PACKAGE_PROFILE에는 포함/제외 모듈, Python/Node/OS/CUDA 요구, core 연결 필요 여부, 설정 키, 데이터·가중치 요구, 설치 시 네트워크 의존, offline_install 지원 여부, 시험한 platform과 build ID를 명시한다. 일반 설치와 오프라인 설치를 구분하고 wheelhouse/필요 빌드물 없이 오프라인 지원을 주장하지 않는다.

## 12. 필수 검증과 실행 증거

최소 수직 경로→기존 queue/lease 원격화→학습·평가·재개·artifact→Web 상세→ZIP 순으로 구현하고, 문서를 모두 끝낼 때까지 코딩을 미루지 않는다. 검증은 mock/실제 CPU/실제 GPU/배포 검증을 별도 집계한다.

T1 기준선: 기존 관련 시험과 로컬 실패를 기록한다. 원본 snapshot과 새 코드의 출처·입력 일관성을 확인한다. 테스트 삭제/assert 완화/기대값 교체/자의적 skip·deselect로 통과시키지 않는다. 이전 승인 예외는 출처·범위·영향을 보존하고 동시 작업의 Git 상태 문제는 격리/manifest로 해결한다.
T2 core 동등성: 고정 입력의 유효/무효 gate·위치·repair, 실제 Random/GA 후보·RNG·elite/tie-break, source-rung 승급·예산·종료·baseline/final 조건을 reference와 대조한다. reference 구현은 owner에서만 사용한다.
T3 분산·보안: 응답 유실/멱등 충돌/이전 revision/경쟁/restart/commit 실패/stale lease/cancel, scope·티켓 만료/회전/변조, YAML/operator/경로/자원 초과/민감 오류와 공개 유출을 검증한다.
T4 owner 독립 실행: 원본 접근 불가의 빈 환경에서 target 내부의 실제 core/API/runner/web로 유효/무효 구조 검사, 실제 후보 생성, 학습·평가·최소 한 번의 단계 결정, full-state 재개와 다운로드를 수행한다. 작은 포함 데이터와 제한된 검증 전용 recipe를 사용하고 논문/기존 실험 설정과 분리한다. 가능한 CPU 경로부터 실행하며 지원하지 않는 CPU 작업은 정직하게 표시한다.
T5 public 독립 실행: private 코드를 설치하지 않은 별도 환경에 최종 public ZIP만 풀어 설치한다. 실제 owner core API에 연결해 구성 업로드→검사→후보 생성→승인 YAML→runner 실행→결과 확정→Web 조회/다운로드를 시험한다. mock core로 대체하지 않는다. core 종료 시 명시적 연결 실패·대기와 fallback 부재를 확인한다.
T6 최종 ZIP 재배치: 개발 폴더가 아닌 완성 ZIP의 새 압축 해제본으로 install→doctor→start→실제 API→verify→stop을 반복한다. 새 경로/외부 cwd/공백 경로를 포함하고 원본·개발 폴더·private volume 접근을 차단한다. owner/public/registration 각 프로필을 검증하며 registration 결과를 public 결과로 자동 대체하지 않는다.
owner 시험은 해당 owner ZIP에 포함된 core만 허용하며 외부 owner 패키지로 누락을 보충하지 않는다. public 시험의 core는 별도 서비스 환경에 두고 공개 실행 환경에는 core 파일·DB·비밀을 mount하지 않는다. 네트워크 연동과 파일 의존성 차단을 함께 입증한다.
T7 실제 GPU: 유휴 장치에서 실행 전 고정한 시간·step/epoch 예산으로 실제 core 생성/검사→GPU 학습·평가→단계 결정→full-state 재개→모니터링·checkpoint 다운로드를 검증한다. 기존 연구 GPU 작업을 중단하거나 자원을 무단 점유하지 않는다. 장비가 없으면 CPU/mock을 GPU 통과로 바꾸지 말고 독립 검증을 계속한다.
T8 배포 누출: ZIP 파일·wheel/sdist/package-data·웹 bundle/map·image layer/build context·fixture·문서·archive에서 private 구현/가짜 canary 비밀 유출을 검사한다. 문자열 검색만으로 통과시키지 말고 실제 파일 목록·import closure·분리 실행 근거를 함께 확인한다.

명령/cwd/인터프리터/의존 lock/시각/exit code/로그 경로·hash/시험한 코드·ZIP digest/fixture·구성 hash/실제 API request ID를 연결한다. 단순 테스트 개수나 파일 존재는 실행 증거가 아니다. GPU 오차는 실행 전 정한 허용오차로, CPU 정책은 가능한 정확 비교로 평가한다. 비용/속도/RPC 지연은 측정한 값만 보고한다. 시험 변경 이후 영향받은 최종 ZIP을 재생성하고 다시 검증한다.

## 13. 등록 문서와 권리 검토

docs/registration에는 프로그램 명칭·목적·적용 범위·주요 기능·동작 절차·실행 환경·System/Subsystem/Block/Module 정의·인터페이스·파일 목록·자체/외부 서비스 구분·구현 현황·검증 근거를 작성한다. 주요 기여 1의 사전 구조 gate와 2의 단계별 NAS 정책을 별도 강조하되 private 규칙/소스가 공개 문서에 나오지 않게 한다. 실제로 그린 편집 가능한 구조도·모델 상세 예시·표를 사용하고 스크린샷만으로 설명하지 않는다.

제출 범위표는 full owner 프로젝트/public runtime/등록 검토 ZIP의 기능과 실제 파일을 대응시킨다. 외부 core가 수행하는 기능을 공개 ZIP의 내부 구현이라고 적지 않는다. 확정되지 않은 개발일·창작자/권리자·외부 고지·기관 양식 필드는 추측하지 말고 확인 필요로 표시한다. 법적 수리나 제출 규칙을 자체 확약하지 않는다.

Ultralytics 등 사용·수정·결합·배포 의무, 의존 라이선스와 NOTICE/SBOM, 논문의 공개 약속을 검토 대상으로 남긴다. REST/컴파일/발췌만으로 의무가 없어졌다고 주장하지 않는다. 전달된 원고에는 §4.5 다단계 평가와 §5 각주의 출판 후 소스 공개 약속이 있으므로 제품 비공개 구현/연구 재현 자료의 관계를 기록한다. 서버에 원문이 없으면 전달된 참고 사실로 표시하고 읽었다고 꾸미지 않는다. 이 검토 대기 때문에 독립 구현·시험을 중단하지 않는다.

## 14. iterative-feedback-evaluator: 12개 누적 기준셋

thread는 threshold로 해석한다. n=12, threshold=90/100이다. 각 기준셋은 아래 4개 기준을 25점씩 평가한다. 첫 평가 전 관찰할 코드/명령/산출물 증거와 채점 근거를 고정한다. 평가→피드백→원인 수정→관련 검증→재평가를 반복하고, 90점 초과·미해결 Blocker/Critical=0·해당 필수 시험 통과 시에만 다음으로 이동한다. 이전 최종 수정본을 다음 기준셋의 시작점으로 사용하고 기준을 낮추지 않는다.

설치된 iterative-feedback-evaluator 스킬이 있으면 실제 지침을 읽고 적용한다. 서버에 없거나 호출할 수 없으면 그 사실을 기록하고 이 절의 명시적 평가 계약을 수행하되 스킬을 실행했다고 주장하지 않는다.

01 사실·기준선: 실제 호출관계 / 최신 변경 보존 / 환경 호환 / 복사 provenance.
02 위협·비밀: 고객 root 가정 / core·키·DB 경계 / 결과 공개 한계 / 실제 관리 주체.
03 NAS 의미: 구조 gate / GA·Random·prior / source rung·동률 / baseline·final.
04 분산 상태: 멱등 원자성 / revision·RNG / lease fencing / 예산·취소.
05 입력·인증: scope/TLS / 티켓·만료 / YAML·operator·자원 / 경로·오류.
06 복구·재현: search/train RNG / 공개 ID / full-state / 정책·호환성.
07 UI·기능: gene·연결 / 블록 표·매핑 / YAML 원본·실행본 / 비교·ON/OFF·baseline.
08 원격 산출물: host 경로 / 바이트·hash / 실제 다운로드 / 재개·보존.
09 설치 폐쇄성: 자체 import 출처 / package-data·operator / 빈 환경·원본 차단 / 경로 재배치.
10 ZIP 배포: 확정 명명·allowlist / public-private 분리 / 최종 압축 해제본 실행 / manifest·checksum·누출.
11 실구동·등록: 실제 core/runner / CPU·GPU 증거 구분 / 등록 범위·도식 / 라이선스·공개 약속.
12 운영·최종회귀: 장애·종료·doctor / 모델 라우팅 / 증거 완결 / 이전 11셋 회귀.

docs/internal/REVIEW_LOG.jsonl에 set_id/round_id/실제 모델·모드·확인 수준/입력 hash/세부 점수·합계/결함 등급/수정 파일/실행 근거/잔여 위험/다음 행동을 기록한다. private 사고과정이나 실제 비밀을 기록하지 않는다. 지침 품질 평가/구현 수용/실행환경 경계 검증을 별도 필드로 둔다. 점수는 자체 품질 평가이지 보안 확률·실측 성능이 아니다. 미실행 시험을 통과로 만들거나 점수를 사전 할당하지 않는다.

장비·모델·외부 결정 부족으로 진전이 막히면 blocker와 최소 필요사항을 기록하고 독립 작업을 계속한다. 다른 기준을 검토할 수 있어도 막힌 기준셋을 통과했다고 건너뛰지 않는다. 무한 문구 수정으로 점수를 올리지 않는다. 마지막 변경 후 영향받은 이전 기준셋과 최종 ZIP을 다시 검증한다.

docs/internal 아래 TASK_STATE.md, MODEL_ROUTING.md, IMPLEMENTATION_PLAN.md, DECISIONS.md, FUNCTION_AND_SECRET_MATRIX.md, ACCEPTANCE_MATRIX.md, WORK_PACKAGES/, VALIDATION_REPORT.md, PACKAGE_CONTENTS.md를 지속 갱신한다. 문맥/세션 변경 시 이를 읽고 실제 파일 hash를 확인해 이어서 작업한다. 유효한 고비용 검증·이미 받은 승인을 불필요하게 반복하지 않는다. 내부 문서는 public ZIP에서 제외한다.

## 15. 최종 완료 조건과 보고

상태는 다음을 각각 판정하라. 하나의 PASS로 합치지 않는다.
- IMPLEMENTATION_COMPLETE: 요청 범위 실제 구현과 관련 검증 완료.
- SOURCE_INDEPENDENCE_VALIDATED: 원본 접근 불가·빈 환경에서 자체 파일 출처와 설치·실행 검증.
- OWNER_BUNDLE_VALIDATED: 완성 owner ZIP의 압축 해제본에서 실제 전체 통합 실행.
- PUBLIC_ZIP_VALIDATED: 완성 public ZIP만 설치한 환경과 실제 원격 core의 연동 실행.
- REGISTRATION_ZIP_VALIDATED: 완성 등록 검토 ZIP 자체의 설치·실행·범위 대조 완료. 기관 등록 승인과는 별개.
- GPU_PATH_VALIDATED: 실제 GPU 학습·평가·재개 증거 확보.
- ADMIN_BOUNDARY_VALIDATED: 별도 관리자 신뢰영역의 host에서 code/키/DB 격리 확인.
- OFFLINE_INSTALL_VALIDATED: 해당 플랫폼 설치 자원까지 포함하고 네트워크 차단 설치를 실제 검증한 경우만 사용.
- RELEASE_CANDIDATE_READY: 필요한 기능·프로필별 검증·누출 검사가 완료된 공개 준비물. 실제 release 권한과는 별개.
- HIGH_MODEL_REVIEW_PENDING / RELEASE_REVIEW_PENDING / BLOCKED_BY_ENV: 해당 미충족 조건을 구체적으로 기록.

동일 host를 ADMIN_BOUNDARY_VALIDATED로, CPU/mock을 GPU_PATH_VALIDATED로, core 필요 프로그램을 오프라인 NAS로 보고하지 않는다. 전체 기능/필수 검증이 남으면 포괄 완료를 선언하지 않는다. 별도 host가 없어도 구현·로컬 통합·분리 패키지·배포 재료는 완료하고 그 조건만 남겨라.

최종 보고에는 실제 TARGET_ROOT와 버전, 실제 작업 모델, 보존/신규/미완료 기능, 실행 가능한 설치·doctor·시작·종료·검증·복구 명령, 원본 의존성 차단 근거, 각 ZIP의 절대경로·SHA-256·파일 범위·core 연결 조건, 12기준셋의 회차/점수/수정/시험 근거, 남은 최소 다음 행동을 포함한다. 산출물을 생성했다는 주장과 파일·로그·ZIP 내용이 일치해야 한다.

지금 실제 위치와 기준선·모델 라우팅을 확인하고 형제 mbnas_application을 준비하라. 최소 실제 수직 경로부터 구현하고, 최종 ZIP을 새 위치에서 설치·실행·검증할 때까지 지속하라. 확인되지 않은 실행·배포·등록을 완료했다고 보고하지 마라.

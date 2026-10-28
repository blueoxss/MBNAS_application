# RIGHTS_REVIEW — 권리·라이선스 검토 질문 목록

문서 상태: 초안 · 2026-10-27 작성 · 2026-10-28 갱신(DD-A65: 루트 `LICENSE` 자리표시, npm 목록, 원고의 심사 기간 제공 약속). **이 문서는 결론이 아니라 질문 목록이다.** 각 절의 "확인된 사실"은 패키지 메타데이터·소스 기록에서 읽은 것이고, 그 법적 의미는 판단하지 않았다. 라이선스 메타데이터는 자동 수집값이며 법적 검토 결과가 아니다(`THIRD_PARTY_NOTICES.md`). 검토가 끝나기 전에는 공개·제출을 하지 않는다는 기존 결정(owner 문서 `LICENSE_STATUS.md`의 RELEASE_REVIEW_PENDING)을 따른다.

## 1. Ultralytics (AGPL-3.0)

확인된 사실
- 모델 런타임이 실행 시 `ultralytics==8.4.173`을 import한다. 패키지 메타데이터의 라이선스는 `AGPL-3.0`이며, 함께 설치되는 `ultralytics-thop 2.2.2`도 `AGPL-3.0`이다.
- `mbnas_model_runtime/operators/parse_model_8_4_173.py`는 ultralytics `parse_model` 함수를 정적으로 복제해 맞춤 연산자 3종을 추가한 **수정 사본**이다.
- 공개 배포본은 requirements 파일로 ultralytics를 PyPI에서 설치하게 하며(온라인 설치), 선택 사항인 오프라인 변형과 Docker 이미지는 ultralytics를 함께 담게 된다.
- 비공개 core도 구조 관측·검사를 위해 같은 모델 런타임(따라서 ultralytics)을 owner 호스트에서 import하며, 사용자는 gateway를 거쳐 네트워크로 그 결과를 받는다.
- 기본 baseline 가중치(YOLO11·YOLO26·RT-DETR 계열)는 사용자 또는 보조 도구가 Ultralytics 배포처에서 받는다.

질문
- Q1-1. 모델 런타임과 이를 import하는 패키지(runner·gateway·core)는 AGPL-3.0의 의무가 미치는 결합 저작물인가? 공개 ZIP·Docker 이미지 배포 시 소스 제공 의무의 범위는?
- Q1-2. AGPL-3.0 §13(네트워크 상호작용)은 사용자가 gateway를 통해 core 기능과 상호작용하는 구조에 어떻게 적용되는가? **비공개 core를 유지하려는 설계와 충돌하는지**가 이 검토의 핵심 질문이다.
- Q1-3. `parse_model` 수정 사본에 필요한 고지(원저작권·수정 사실 표시)는 무엇인가?
- Q1-4. Ultralytics의 별도 상용 라이선스가 선택지인지, 그 조건은 무엇인지(확인 못 함).
- Q1-5. Ultralytics 제공 가중치(.pt)의 이용 조건은 코드와 같은가?

## 2. ETRI mixed 연산자 (vendored)

확인된 사실
- 원본 연구 코드의 연산자 모듈 3개를 이 저장소로 옮겼다: 공개 모델 런타임의 `operators/etri_mixed_blocks.py`, 비공개 core의 `search/mixed_pool/seed_variants.py`·`runtime_constants.py`. 원본 파일의 sha256은 owner 내부 provenance에 기록되어 있다.
- 원본 저장소에는 LICENSE·NOTICE 파일이 없고, 해당 파일에 저자 표기가 없다.
- 연산자 코드는 고객 장비에서 승인 모델을 빌드할 때 필요하므로 공개 배포본에 포함되도록 설계되었다(비밀로 둘 수 없음).

질문
- Q2-1. 이 코드의 저작자·권리자는 누구인가(개인, 연구실, 기관)?
- Q2-2. 공개 배포본에 포함해 제3자에게 배포하는 것을 권리자가 허락하는가? 허락 조건(저작자 표시 문구, 라이선스)은?
- Q2-3. 비공개 core 쪽으로 옮긴 부분의 이용 범위에 제한이 있는가?

## 3. ETRI hybrid 모델 어댑터

확인된 사실
- `mbnas_model_runtime/adapters/etri_hybrid/`는 원본 연구 노트북에서 유래한 CNN-Transformer 검출 모델 구현이며, 출처 노트북의 식별 hash가 코드에 기록되어 있다. 저자·라이선스 표기는 없다.

질문
- Q3-1. 노트북과 이 구현의 권리자는 누구이며 공개 배포가 허락되는가?
- Q3-2. 시험 증거 스크린샷에 이 구조 문서가 보이는 경우 그 화면 자료를 등록·공개 자료로 써도 되는가?

## 4. RF-DETR · DINOv2 · 기타 제3자 모델과 데이터

확인된 사실
- `rfdetr 1.11.2`의 메타데이터 라이선스는 Apache-2.0이다. RF-DETR는 DINOv2 계열 ViT 인코더를 쓰며, 일부 구성에서 DINOv2 사전학습 가중치를 hub에서 내려받는다(코드 주석·구조 관측 기록 기준).
- `torch`·`torchvision`은 BSD 계열, `transformers`는 Apache-2.0으로 표기되어 있다.
- NVIDIA CUDA 런타임 wheel(cuBLAS, cuDNN 등)은 메타데이터상 "NVIDIA Proprietary Software"다.
- `faster-coco-eval`, `torch-hungarian`은 라이선스 메타데이터를 확인하지 못했다.
- `certifi`(MPL-2.0), `tqdm`(MPL-2.0 AND MIT)처럼 파일 단위 copyleft가 있는 구성요소가 있다.
- 프로토콜 예시는 공개 데이터셋(VisDrone 등)을 가리키는 placeholder 경로를 쓴다. 예제 데이터는 합성 데이터다: `examples/synthetic4`(4장)와 생성 스크립트 `examples/make_synthetic_dataset.py`가 세 배포본에 들어 있다.

질문
- Q4-1. DINOv2·RF-DETR 사전학습 가중치의 이용 조건은 코드 라이선스와 같은가?
- Q4-2. 오프라인 wheel 묶음과 Docker 이미지에 NVIDIA 런타임을 넣어 재배포하는 것이 NVIDIA 조건에 맞는가?
- Q4-3. 라이선스 미확인 패키지 2종의 조건은 무엇인가?
- Q4-4. 시험·예시·문서에 실제 데이터셋 이미지나 그 파생물이 포함되는가? 포함된다면 데이터셋 이용 조건은?

## 5. 웹 의존성 (npm)

확인된 사실
- 웹 번들에는 React 18.3.1 등 런타임 의존성이 포함된다. Vite·Vitest·Playwright 등은 빌드·시험용 개발 의존성이다.
- npm 의존성의 라이선스 목록은 `THIRD_PARTY_NOTICES_WEB.md`다(2026-10-28, `mbnas_web/package-lock.json`과 설치된 `node_modules/*/package.json`의 license 필드에서 생성). 166개 중 런타임 5개(react, react-dom, scheduler, loose-envify, js-tokens)는 모두 MIT이고, 개발용 161개는 MIT·MPL-2.0·Apache-2.0·ISC 등이며, 라이선스를 확인하지 못한 항목(`UNKNOWN`)은 0개다.
- 최소화된 `dist/` 번들 안에는 라이선스 주석이 없다. 고지는 위 목록 파일로만 함께 배포된다.

질문
- Q5-1. 배포하는 `dist/` 번들에 필요한 라이선스 고지(MIT 등)는 `THIRD_PARTY_NOTICES_WEB.md`로 충분한가, 아니면 각 패키지의 라이선스 전문을 번들 옆에 함께 내야 하는가?
- Q5-2. 개발용 의존성(MPL-2.0 14개 등)은 배포물에 들어가지 않는다. 소스 배포본에 `package-lock.json`만 싣는 현재 방식으로 고지 의무가 생기지 않는가?

## 6. 자체 코드의 라이선스

확인된 사실
- `mbnas_*` 패키지와 `mbnas_web`에 적용할 라이선스·저작권 표기는 정해지지 않았다. 원본 저장소에도 LICENSE가 없다.
- 결정 전까지 루트 `LICENSE`는 자리표시 고지다(DD-A65): 권리 유보(All rights reserved), 권리자 미정, 어떤 라이선스도 부여하지 않으며 제3자 구성요소의 조건을 바꾸지 않는다. 세 배포본(owner·public·registration)에 모두 들어 있고, 결정되면 그 내용으로 교체한다.

질문
- Q6-1. 공개 패키지에 어떤 라이선스를 붙일 것인가? 1절의 결론(AGPL 호환 필요 여부)에 따라 선택지가 달라지는가?
- Q6-2. 비공개 core의 이용 조건(사내 전용, 별도 계약 등)은 무엇인가?
- Q6-3. 저작권 표기 문구와 표기 위치(각 파일 머리말, NOTICE 파일 등)는?

## 7. 연구 원고의 소스 공개 약속

확인된 사실
- 이 프로그램의 기반 연구를 다룬 원고에 맞춤 GA 기반 NAS 알고리즘에 관한 공개 약속이 있다(원문과 위치는 owner 내부 기록 `LICENSE_STATUS.md` 참조. 원고는 미공개일 수 있으므로 이 문서에 인용하지 않는다). 취지는 세 가지다.
  1. 본문: 이 알고리즘을 공개한다고 적는다.
  2. 각주: 심사 기간에는 블라인드 심사 정책과 보안을 이유로, 요청이 있으면 익명·암호화된 링크로 소스 코드를 제공한다.
  3. 각주: 게재되면 소스 코드를 공개한다.
- 원고가 지금 심사 중인지는 이 배포본의 기록으로 확인하지 못했다(확인 못 함).
- 이 배포본은 GA 탐색 정책을 비공개 core(`SS6`)에 둔다.

질문
- Q7-1. 공개 약속의 대상("소스 코드")은 연구 재현용 코드인가, 이 제품의 core 전체인가?
- Q7-2. 약속을 지키는 방법(연구 재현 코드를 별도 공개 등)과 비공개 core 유지가 함께 가능한가?
- Q7-3. 심사 기간 중 요청 시 제공 약속이 지금 유효한가(원고가 심사 중인가)? 유효하다면 무엇을 누구에게 주는가(연구 재현 코드인가, 이 제품의 owner 배포본인가)? owner 배포본을 주면 정책 비공개의 전제(core가 다른 관리 주체의 영역에 있음, `docs/public/SECURITY_MODEL.md` §8)가 깨지는데, 그 범위·조건(열람 기간, 재배포 금지, 링크 만료)은 누가 정하는가?
- (처리됨) `LICENSE_STATUS.md`는 원고 관련 문구를 담고 있어 owner 배포본에만 넣고 공개·등록 ZIP에서는 제외하기로 했다. 공개본의 라이선스 안내는 루트 `LICENSE`(자리표시), `THIRD_PARTY_NOTICES.md`, `THIRD_PARTY_NOTICES_WEB.md`와 `docs/public/INSTALL.md`의 라이선스 절이다.

## 8. 검토 결과 기록 방법

각 질문의 결론은 결정권자·근거·일자와 함께 `LICENSE_STATUS.md`(owner 문서)에 기록하고, 결론에 따라 `THIRD_PARTY_NOTICES.md`, 이 문서, `SUBMISSION_SCOPE_TABLE.md`를 고친다. 결론이 나기 전에는 이 문서의 질문을 지우지 않는다.

# LICENSE_STATUS — 권리 검토 상태 (2026-10-23 작성, 2026-10-28 갱신, 미확정)

이 프로젝트의 자체 코드(`mbnas_*` 패키지, `mbnas_web`)에 적용할 라이선스와 권리자 표기는 **확정되지 않았다**. 아래는 검토 대기 목록이며 법적 결론을 내리지 않는다(지침 §13). 2026-10-28(DD-A65): 결정 전까지 루트 `LICENSE`에 자리표시 고지(권리 유보, 라이선스 부여 없음, 권리자 미정, 결정 시 교체)를 두고 세 배포본 모두에 넣는다.

| 항목 | 사실 | 검토 필요 사항 |
|---|---|---|
| 자체 코드 고지 | 루트 `LICENSE`: 권리 유보 자리표시(DD-A65, 2026-10-28). 라이선스를 부여하지 않고 제3자 조건을 바꾸지 않는다 | 권리자·라이선스 결정 후 교체(REVIEW_CHECKLIST) |
| 원본 ETRI_NAS 저장소 | 루트·application에 LICENSE/NOTICE 없음(스냅샷 manifest 전 파일 "rights review pending") | 창작자·권리자(ETRI/연구팀) 확인, 자체 코드 라이선스 선택 |
| ultralytics 8.4.173 | AGPL-3.0 (wheel METADATA) | 결합·배포 의무(네트워크 서비스 포함), `parse_model` 수정 복제본(`mbnas_model_runtime/operators/parse_model_8_4_173.py`)의 파생물 지위, 공개 ZIP 배포 시 소스 공개 의무 범위 |
| ETRI mixed operators(vendored) | `yolo_ga_runtime/mixed_blocks.py` 원문 sha256 4233dd32…, 저자 표기 없음 | 권리자 확인, 공개 패키지 포함 적정성(고객 장비 실행 필수 → 비밀 불가, DD-A2) |
| ETRI hybrid 모델(adapters/etri_hybrid) | 노트북 `ETRI_NAS_CNN_Transformer_Experiment.ipynb` 유래(NOTEBOOK_BLOB_SHA1 기록) | 권리자 확인 |
| 논문 `_2026_IVC_/main.tex:407` 본문·각주 | 본문: "we develop a custom GA-based NAS algorithm, which is open to public". 각주 원문: "Due to blind review policies and security considerations, the source code will be made available upon request during the review process via an anonymized and encrypted library link, and will be publicly released upon publication." (원본 저장소 읽기 전용 확인, 2026-10-23·10-28) | 두 약속(심사 중 요청 시 익명·암호화 링크 제공, 게재 시 공개)과 제품 비공개 core의 관계 정리. 원고가 지금 심사 중인지(따라서 심사 중 제공 약속이 유효한지)는 확인 못 함. RIGHTS_REVIEW Q7-1~Q7-3 |
| 의존 라이선스 전체 | `THIRD_PARTY_NOTICES.md`(Python 137개, 2026-10-23 `importlib.metadata`에서 자동 생성 + 10-27 수기 추가 절). 생성 명령은 저장소에 남아 있지 않다(C11 평가에서 재도출로 일치 확인) | NOTICE/SBOM 형식 확정, GPL 계열 유무 재확인, 생성 명령 보존 |
| 웹 의존(npm) | `THIRD_PARTY_NOTICES_WEB.md`(2026-10-28, `scripts/dev/gen_npm_notices.py`: package-lock + node_modules/*/package.json의 license 필드). 166개: 런타임 5개 MIT, 개발용 161개, `UNKNOWN` 0 | `dist/` 번들 안에 고지문을 함께 넣을지(RIGHTS_REVIEW Q5-1) |

제출·공개·등록은 이 검토가 끝나기 전에 수행하지 않는다(RELEASE_REVIEW_PENDING).

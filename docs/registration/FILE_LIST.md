# FILE_LIST — 등록용 배포본 파일 목록 (registration-runtime ZIP)

> 이 파일은 `scripts/dev/gen_registration_docs.py`가 ZIP 매니페스트(`FILE_MANIFEST.jsonl`)에서 **자동 생성**한다. 손으로 고치지 않는다.
> 매니페스트를 만드는 쪽은 패키징 스크립트(`scripts/package.sh --profile registration`)다.

## 상태: 매니페스트 없음 — 생성 대기

등록용 ZIP의 매니페스트가 아직 만들어지지 않아 파일 목록을 생성하지 않았다. 추측으로 목록을 채우지 않는다.

생성 절차(패키징 단계에서 수행):

1. `scripts/package.sh --profile registration` 이 배포 파일을 staging 디렉터리에 복사하고 `FILE_MANIFEST.jsonl`(상대경로·크기·sha256·역할·출처·라이선스)을 만든다.
2. `python scripts/dev/gen_registration_docs.py --only file-list --root <staging>/mbnas_application --manifest <staging>/mbnas_application/FILE_MANIFEST.jsonl` 로 이 파일을 다시 만든다.
3. 이 파일이 바뀌었으므로 매니페스트를 다시 계산한다. 이어서 패키저가 `PACKAGE_CONTENTS.md`를 만들고 매니페스트를 마지막으로 계산한다(이 파일은 자기 자신·매니페스트·`PACKAGE_CONTENTS.md`를 목록에 넣지 않으므로 그 뒤에도, 출하된 최종 매니페스트로 다시 만들어도 내용이 변하지 않는다).

포함 범위의 기준은 `SUBMISSION_SCOPE_TABLE.md`, 최종 판정은 패키징 allowlist(`build/allowlists/registration.txt`)다.

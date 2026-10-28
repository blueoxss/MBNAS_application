# CLEAN DISTRIBUTION SPEC (DD-A76) — 2026-10-28 15:3x KST

Owner instruction (user, 15:2x KST, verbatim):
"일단 license와 다른건 다 지워 [the README status paragraph was pasted] ... 테스트한 내용들과 주석들도 모두 지우고
실제로 그냥 깔끔한 프로그램만 돌아가는 형태로 배포하면돼."

Interpretation (designer): the three distribution ZIPs become "the clean program that runs": code + web + ops
scripts + config/deploy examples + user docs + third-party notices + wheels. No tests, no internal records, no
evidence, no dev/packaging/scan tooling, no placeholder licence, no status / verification narrative, no
process-trail annotations (decision numbers, fix rounds, snapshot provenance) in shipped docs or code comments.
The repository keeps everything (tests, docs/internal, tooling); only the ZIP contents and the shipped text change.

ROOT = /home/mgjeong/Desktop/etri_hd/mbnas_application (the only tree you edit).
NEVER touch /home/mgjeong/Desktop/etri_hd/etri_nas (read-only original). NEVER run git commands that modify
state (no add/commit/stash/checkout/reset). NEVER print or copy .env contents. NEVER delete runtime/, dist/,
build/, docs/internal/evidence. NEVER weaken an assertion to make a test pass; when a packaging test encodes the
OLD distribution spec, rewrite it to encode the NEW spec below and say so in your report.
Interpreter for python/pytest: ROOT/runtime/venv/bin/python (never conda base). Run pytest with -p no:cacheprovider.
Before editing a file, read it. Keep edits minimal and local. Do not reformat files.

## 1. What each ZIP ships (allowlist target state)

Common to public / registration / owner:
  KEEP   mbnas_contracts/{pyproject.toml,src/**}  mbnas_model_runtime/{pyproject.toml,src/**}
         mbnas_runner/{pyproject.toml,src/**}  mbnas_server/{pyproject.toml,src/**}
         mbnas_web/{src/**,public/**,index.html,README.md,package.json,package-lock.json,vite.config.ts,tsconfig*.json,
                    scripts/**,dist/**}            (scripts/ = gen-types.mjs + check-bundle.mjs, needed by `npm run build`)
         scripts/install.sh scripts/doctor.sh scripts/start.sh scripts/stop.sh scripts/verify.sh scripts/public/**
         config/examples/** (public set)  deploy/public/**  examples/**  docs/public/*.md (cleaned)
         README.md (cleaned)  VERSION  requirements-public.txt requirements-cpu.txt constraints.txt .dockerignore
         THIRD_PARTY_NOTICES.md THIRD_PARTY_NOTICES_WEB.md
         generated: BUILD_INFO.json PACKAGE_PROFILE.json FILE_MANIFEST.jsonl PACKAGE_CONTENTS.md wheels/*.whl
  DROP   tests/** (all, incl. tests/_shared/**)   docs/internal/**   docs/public/contracts/** (contract history)
         mbnas_web/tests/** mbnas_web/e2e/** mbnas_web/playwright*.config.ts
         LICENSE (placeholder)  LICENSE_STATUS.md   pyproject.toml (root; only pytest/workspace settings)
         scripts/dev/** scripts/owner/t2_compare.py (test tool)  scripts/package.sh  build/** dist/** runtime/**
registration additionally:
  KEEP   docs/registration/{PROGRAM_OVERVIEW.md,SYSTEM_STRUCTURE.md,INTERFACES.md,KEY_CONTRIBUTIONS.md,
                            SUBMISSION_SCOPE_TABLE.md,FILE_LIST.md,diagrams/**}  (cleaned; FILE_LIST regenerated)
  DROP   docs/registration/REVIEW_CHECKLIST.md docs/registration/RIGHTS_REVIEW.md (internal review records)
owner additionally:
  KEEP   mbnas_core/{pyproject.toml,src/**}  requirements-owner.txt  deploy/owner/**  config/examples/** (all)
         scripts/owner/{probe_registry.py,search_dry_run.py,smoke_end_to_end.py}  scripts/runner/**
         scripts/dev/wp3_three_process_smoke.py ONLY (the verify.sh integration driver for the owner profile)
  DROP   everything in the common DROP list, docs/internal/**, tests/owner/**

[required] per profile becomes: VERSION .dockerignore constraints.txt THIRD_PARTY_NOTICES.md THIRD_PARTY_NOTICES_WEB.md
mbnas_*/pyproject.toml (the shipped packages) mbnas_web/package.json.  LICENSE is no longer required or included.
[expected]: drop tests/public/conftest.py and tests/_shared/conftest_common.py; keep the rest that still ships.
[deny] with reasons: add 'tests/** :: test suites are not shipped (clean distribution, DD-A76)',
'docs/public/contracts/** :: contract history stays in the repository (DD-A76)', 'LICENSE* :: no licence file is
shipped until the rights holder decides (DD-A76)', 'pyproject.toml :: workspace pytest settings (DD-A76)'.
Licence column (package_build.py LICENCE_RULES): remove the words placeholder / undetermined / pending /
unconfirmed. Project files -> "project"; ETRI-derived -> "third-party-derived (see THIRD_PARTY_NOTICES.md)";
AGPL-derived -> "third-party-derived (AGPL-3.0; see THIRD_PARTY_NOTICES.md)"; web bundle -> "project + bundled npm
third-party code (see THIRD_PARTY_NOTICES_WEB.md)"; notices -> "notice (third-party licence list)".
PACKAGE_CONTENTS.md header: no "licence undetermined" sentence.
offline_install_evidence in [profile] may keep pointing at the existing record (it is read by the packager only).

## 2. Shipped documents — content rules (README.md, docs/public/*.md, docs/registration/*)

REMOVE entirely: status paragraphs (e.g. README "> 상태(2026-10-28): v0.1.0 초판 ..."), "문서 기준일/문서 상태: 초안"
lines, any sentence whose subject is OUR testing/verification process (시험 통과, 시험 묶음, 회귀 게이트, 실행 확인,
검증 판정, 미검증, BLOCKED_BY_ENV, VALIDATED, T2..T8, 1차 검증, 재실행, 통과 수, 증거, 기준 트리 hash), status
columns in feature tables (FEATURES.md keeps 기능 | 설명 only; delete the 상태 column and the 상태 표기 legend;
FEATURES "지원 범위의 한계" becomes a short factual "지원 범위" list: keep only product facts such as CPU runner
scope, local-only artifact transfer, no console benchmark queue, pickle resume default, no checkpoint import — delete
every item that is about our verification coverage or "다음 판 후보/이번 판 밖"), SECURITY_MODEL §9 "검증 상태 요약"
(delete the section), PROGRAM_OVERVIEW §5 "개발·검증 상태" (delete), KEY_CONTRIBUTIONS §3 "확인 상태" (delete),
INSTALL §8 "라이선스" (replace by two lines: third-party notices live in THIRD_PARTY_NOTICES.md / _WEB.md;
ultralytics is AGPL-3.0 and the model runtime imports it), README "라이선스 상태" section (same two lines) and the
README/INSTALL/OPERATIONS sections about packaging (`scripts/package.sh`, "배포 ZIP 만들기", --canary) — the packager
is no longer shipped. Remove references to docs/internal, to tests, to verify "unit" suites that no longer exist
(OPERATIONS §10 describes verify.sh: rewrite to the suites that remain: integration, relocation, package, gpu;
unit is reported NOT_APPLICABLE in a distribution), to REVIEW_CHECKLIST / RIGHTS_REVIEW, to LICENSE/LICENSE_STATUS.
REMOVE every "DD-A##", "WP-#", "C0#", "fix round", "라운드", "설계자" token and the parenthesis around it; rewrite the
sentence so it still reads naturally (the technical fact stays, the provenance tag goes).
KEEP: everything that tells a user what the program does, how to install/configure/run/operate/troubleshoot it,
requirements (GPU/CUDA/Python/pins), security model facts (trust zones, credentials, transport, input limits,
data exposure rules, threat model), API usage, compatibility facts (ultralytics hash check etc. — stated as product
behaviour, not as "verified on ..."), registration structure/interface/contribution descriptions.
DATES: no document dates. VERSION 0.1.0 may be named.
Links: fix every link that points at a removed file/section. No link may point at docs/internal or tests.
Language: keep the existing Korean/English style of each document.
PROGRAM_OVERVIEW §1 basic-info table: keep fields; where a value is "확인 필요"/"미정" (author, rights holder,
dates) leave the cell EMPTY rather than writing a placeholder word.

## 3. Shipped code — comment/docstring rules (mbnas_contracts, mbnas_model_runtime, mbnas_runner, mbnas_server,
mbnas_core, mbnas_web/src, scripts/install.sh start.sh stop.sh doctor.sh verify.sh, scripts/public, scripts/owner
(kept files), scripts/runner, scripts/dev/wp3_three_process_smoke.py, deploy/**, config/examples/**, examples/**)

Remove process-trail text from comments and docstrings: tokens DD-A##, WP-#(x) / "WP-1 contract §4", C0#/C1#,
"fix round N", "round N", "라운드", "designer"/"설계자", "snapshot HEAD <hex>", "monolithic package", "split per",
"moved per", "Source: ``...`` (...)" provenance lines, "T2-xx"/"T3-xx" test ids, "1.5.1 carry-over"/"이월".
Rewrite the sentence so the technical statement remains (e.g. "``Error.location`` (DD-A20 / WP-3 contract section
2.8): which component produced the error." -> "``Error.location``: which component produced the error.").
Delete a comment line entirely only when nothing technical is left of it.
HARD CONSTRAINTS:
 - Byte-identical OpenAPI: mbnas_contracts/src/mbnas_contracts/openapi/openapi.json and openapi_runner.json must not
   change. Any text that reaches them (pydantic Field(description=...), model/class docstrings of schema models that
   FastAPI/pydantic export, route docstrings/summaries) is OUT OF SCOPE — leave it exactly as is. Verify after
   editing: `runtime/venv/bin/python -B scripts/dev/gen_runner_openapi.py --check` exits 0 and the public contract
   tests pass (tests/public/contracts); sha256sum of both JSON files unchanged (public openapi.json sha 70e0cffa…).
 - No behaviour change: only comment/docstring text. Never touch string literals used at runtime (log messages,
   error messages, HTML, env names), never touch tests (they stay in the repo; owner-only test files are not shipped).
 - python -m compileall over every edited .py must pass; `mbnas_web`: `npx tsc --noEmit -p tsconfig.json` passes.
 - Keep the first line of every module docstring meaningful.

## 4. verify.sh / doctor.sh behaviour in a distribution without tests
 - `--suite unit` (and `gpu`): when ROOT/tests does not exist -> status NOT_APPLICABLE with reason
   "test suites are not shipped in this distribution" (not FAIL, not BLOCKED_BY_ENV), counted in neither pass nor fail.
   Help text updated. In the source tree (tests/ present) behaviour is unchanged.
 - `--suite integration` owner: driver scripts/dev/wp3_three_process_smoke.py still ships -> unchanged.
   public: unchanged (live core checks via MBNAS_CORE_URL).
 - `--suite relocation`, `package`: unchanged.
 - `all` = the suites that apply; the summary prints NOT_APPLICABLE suites separately.
 - doctor.sh: any check that looks for tests/ or pyproject.toml (root) must not WARN/FAIL in a distribution.
 - install.sh: must not need the root pyproject.toml; check scripts/install.sh:272 area and the wheel path.

## 5. Records (designer writes these; implementers do not)
 - docs/internal/DECISIONS.md DD-A76; REVIEW_LOG rows; TASK_STATE; ACCEPTANCE_MATRIX/FINAL_REPORT later.

## 6. Acceptance of this change (what the reviewers check)
 R1 selection dry-run per profile (packager's selection function, no build) contains no path under tests/,
    docs/internal/, docs/public/contracts/, mbnas_web/tests|e2e, no LICENSE*, no pyproject.toml at root, no
    scripts/dev except the owner smoke driver, no scripts/package.sh, no REVIEW_CHECKLIST/RIGHTS_REVIEW.
 R2 grep over the SELECTED files of each profile: zero hits for DD-A[0-9], WP-[0-9], 라운드, fix round, 설계자,
    snapshot HEAD, 시험 통과, 미검증, BLOCKED_BY_ENV, 검증 판정, 문서 기준일, 초안, 자리표시, placeholder notice,
    licence undetermined, 권리자 미정, docs/internal, tests/public, tests/owner, REVIEW_CHECKLIST, RIGHTS_REVIEW,
    LICENSE_STATUS — except openapi JSON files (frozen contract) and THIRD_PARTY_NOTICES*.md (generated notices).
 R3 openapi.json / openapi_runner.json sha unchanged; gen_runner_openapi --check 0; gen_npm_notices --check 0;
    gen_registration_docs --check 0 after FILE_LIST regeneration (or documented how it is regenerated at build).
 R4 tests/public/packaging passes with runtime/venv; tests/public + tests/owner suites: 0 failed (designer runs).
 R5 verify.sh --suite all in a distribution without tests reports unit NOT_APPLICABLE and exits 0 when the rest pass.

# 긴급: 로컬 커밋 3e5bbecb9 push 금지 (2026-10-28 09:1x 설계자)

확인(읽기 전용 `git ls-remote`): 원격 `origin/main`은 여전히 `c9a294085`(README만)이고, 로컬 `main`의 커밋 `3e5bbecb9 "update"`는 **아직 push되지 않았습니다.**

이 로컬 커밋이 추적하는 것(절대 공개 저장소에 올라가면 안 됨):
- `runtime/` 146,689 파일 — 시험 설치 트리의 `config/{owner,public}/*.env` 5개(생성된 토큰·서명키 포함), venv, DB, 로그
- `build/` 11,456 파일 — `build/canary/**`(private core 사본 + `CANARY_MANIFEST.json`), staging, wheel 빌드
- `dist/owner/*-owner-private-*.zip` — private core 포함 ZIP
- `docs/internal/**` — 내부 검토 기록(민감 경로 포함)
- `__pycache__/*.pyc` 다수(계정명 포함 경로)

조치(사용자 수행; 이 세션은 git을 조작하지 않음):
1. push 하지 마십시오.
2. `.gitignore`를 갱신해 두었습니다(runtime/, build/, dist/, docs/internal/canary/, *.env, __pycache__ 등).
3. 추적 해제 후 다시 커밋:
   ```bash
   git rm -r --cached runtime build dist docs/internal/canary
   git rm -r --cached $(git ls-files | grep -E '__pycache__|\.pyc$')
   git commit --amend -m "mbnas_application source (no runtime/build/dist)"
   ```
   또는 `git reset --soft c9a294085` 후 필요한 경로만 `git add`(권장: 패키지 6개, tests, scripts, config/examples, deploy, docs/public, docs/registration, examples, requirements*, constraints.txt, README.md, VERSION, LICENSE*, THIRD_PARTY_NOTICES*.md, pyproject.toml, .dockerignore, .gitignore).
4. 시험 트리의 생성 토큰은 시험용이지만, 혹시 push된 적이 있다면 해당 설치본의 principal을 폐기하십시오(현재 push 안 됨).
5. 다음 과제(별도 저장소 이관)는 이 정리와 무관하게 allowlist 사본으로 진행합니다.

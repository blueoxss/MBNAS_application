# WP-6 WEB 계약 v1 (설계자 claude-fable-5-1, 2026-10-23 22:5x; WP-4 계약 1.5.0 확정 후 착수, WP-5와 병행)

우선순위: 이 문서 > DD-A8, A21, A23 > WEB_PRIVATE_INVENTORY.md §8/§9(R1~R8) > FUNCTION_BASELINE F-그룹.

## 0. 목표
공개 번들에서 private 정책·호스트 사실을 제거하고(DD-A8), 상세 3요소(gene 구성도·블록 표·구성 YAML)를 유지·확장(업로드·개정 이력·diff·다운로드)하며, OFF 후보를 UNTRAINED·UNEVALUATED로 표시하고 1.5.0 타입에 맞춘다.

## 1. 변경 목록(WEB_PRIVATE_INVENTORY §9 R1~R8 + 추가)
1. R1: `lib/fitness.ts`에서 computeFitness 등 제거(weightsSum/weightsSumOk/타입만 유지); `CandidateView` "Client recompute" 행 제거; `tests/fitness_oracle.ts` 신설(픽스처 전용), `fitness.test.ts` 삭제(서버 unit이 수식 고정), `components.test.tsx`·`e2e/weights.spec.ts` 수정.
2. R2/R3: `lib/policy.ts` handoff embed·템플릿 제거; 연결 성공 시 `loadPresets()` 자동 + DEFAULT_BASELINE14 자동 적용(라벨 명시); 연결 전 Settings 안내 배너; mock `fixturePresets()` 확장(DEFAULT_BASELINE14 3-rung + PILOT_SMOKE); e2e 6 spec 조정. 번들 grep 수용 기준: `dist/**`에 handoff 원문 키 문자열(`"value_provenance"`, `illustrative_new_revision`), `/root/detection`, `/data/drone`, `147.46.`, `pc2300` 0건.
3. R4/R5: 경로·포트 정리, `gen-types` 경로(WP-1H 완료), 1.5.0 타입 재생, `types.ts` 수기 보강 제거, `lib/structure.ts` 수기 카탈로그 타입 제거.
4. R6: Monitor 대표 배지 제거; addRung 기본값 유지; validate.ts 유지.
5. UNTRAINED 표시: GeneratedTable·CandidateView 배지 "UNTRAINED · UNEVALUATED"(`training_status`/`evaluation_status` 필드 기반), DD-5 배지 문구 유지.
6. 구성 YAML(F5): Settings/TrainingPanel에 **파일 업로드**(`POST /uploads/model-config`) → 응답 `upload_id`로 structure-check·training-runs 호출; CandidateView "구성" 탭: 개정 이력 표(revision kind/sha256/yaml_hash/architecture_hash/시각), 선택본↔실행본 **diff 뷰**(행 단위 텍스트 diff, 양쪽 sha 표시), 다운로드(원문 바이트 = revision yaml 엔드포인트; 표시본 Blob 다운로드와 구분 표기), export 안내.
7. Lineage 화면 생성 후보 표시(F14): generatedCandidates를 Lineage 목록에 포함(평가 행과 구분 배지).
8. 경로→id: Lineage/CandidateView의 절대경로 표시를 artifact/checkpoint id + sha로 교체; Worker 표 runner_id/device_id.
9. 포트·문서: dev proxy 8710(완료), README 갱신(WP-7), production asset `dist/` 생성·ZIP 포함 확인.
10. 접근성·a11y 기존 e2e 유지(axe).

## 2. 수용조건
`npm run check:types`·`typecheck`·`build`·`vitest`(전체) 통과; e2e(mock) 전부 통과(desktop/mobile); 번들 grep 0건; 실제 2프로세스(core+gateway) 하네스에서 업로드→검사→학습(OFF 생성 run 또는 CPU 학습)→상세(3요소)→diff→다운로드 스크린샷·sha 일치(CPU, 사용자 GUI 조작 없이 Playwright). 변경 파일 목록·테스트 적응 기록.

## 3. 소유
WP-6A 정책 제거·프리셋·UNTRAINED(sonnet) / WP-6B 구성 YAML 업로드·개정·diff·Lineage(opus) / WP-6C 타입·경로·e2e 갱신(sonnet) / WP-6V verify(opus).

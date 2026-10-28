# ETRI_RUNTIME_SCOPE — ETRI mixed pool(yolo_ga_runtime) vendoring 범위 (WP-0 / A7)

작성: 2026-10-23, 분석 에이전트(세션 모델 claude-fable-5-1 상속). 근거는 전부 `build/source_snapshot/`(2026-10-23 21:07 KST 사본, provenance `docs/internal/provenance/SOURCE_SNAPSHOT_MANIFEST.jsonl`)에서 읽었다. 코드 실행은 하지 않았고(GPU·서버·테스트 미실행), 정적 계산은 scratchpad의 `python3 -I` 스크립트 2개로 했다(부록 A). 실행으로만 확인되는 사실은 "확인 못 함"으로 표기한다.

인용 규칙: 서버 = `application/MBNAS_server/src/mbnas_server/<file>:<line>` (아래에서는 `server/<file>:<line>`로 줄임), ETRI = `yolo_ga_runtime/<file>:<line>`, 테스트 = `application/MBNAS_server/tests/<path>:<line>`, ultralytics = `build/wheelhouse/ultralytics-8.4.173-py3-none-any.whl` 안의 `ultralytics/nn/tasks.py:<line>`.

## 0. 결론

1. 서버가 실제로 실행하는 ETRI 코드는 **4개 함수**뿐이다: `mixed_search_space.make_mixed_seed_variants`, `mixed_blocks.register_ultralytics_blocks`, `mixed_blocks.audit_model`, 그리고 핸들만 보관하고 호출하지 않는 `mixed_search_space.audit_config`(`server/search/etri_bridge.py:139,159,162`; `server/search/space.py:307,337`). `mutate_operators`·`_legal_choices`·`symbolic_shape_propagate`는 서버 코드에서 **도달 불가**다(§2).
2. `make_mixed_seed_variants`가 `runtime`에서 쓰는 것은 `R.REPEAT_MODULES` 한 개(`yolo_ga_runtime/mixed_search_space.py:176`)이고, 그 값은 `__init__.py`가 import 시 실행하는 `mixed_hooks.install_runtime_hooks()`에 의해 **17개 이름**으로 확장된 집합이다(§3.3, 정적 계산). 이 17개는 ultralytics 8.4.173 `parse_model`의 `repeat_modules`(15개)에 ETRI 반복 연산자 2개를 더한 집합과 **동일**하다.
3. import 클로저: `yolo_ga_runtime.mixed_search_space`를 import하면 패키지 `__init__`이 `crossover_v22`, `runtime`(PyYAML 필요), `pipeline`, `ga_hooks`, `mixed_hooks`를 모두 실행하고 `runtime`/`crossover_v22`의 클래스·함수를 monkeypatch한다(§3.1). 서버는 이 중 어떤 심볼도 쓰지 않으므로 vendoring 후 **이 5개 파일은 전부 제외**할 수 있고, `/2` 해시 입력 8파일 → `/3` 해시 입력 3파일로 줄어든다.
4. vendoring 권고(Option A, §5): `mixed_blocks.py` 전체(314행)를 `mbnas_model_runtime.operators`로, `mixed_search_space.py`의 L12-17·L25-62·L154-203(92행)과 `REPEAT_MODULES` 유효값 리터럴을 `mbnas_core.search.mixed_pool`로. DD-A2가 열거한 `_legal_choices`/`mutate_operators`는 도달 불가이고 `_legal_choices`는 이미 `space.illegal_reason`으로 이식되어 있으므로 **참조 사본으로만 보존**할 것을 권고한다(DD-A2 문구와의 충돌 플래그, §10).
5. 해시: `space_hash`/`prior_hash`는 `templates[].source.etri_files_sha256`(8파일 content sha256)을 포함하므로 vendoring 즉시 바뀐다. `mbnas-mixed-space/3` 정의와 `/2`→`/3` 입력 매핑 레코드를 §6에 제안한다. 또 `search_space_resolved.etri_root`가 **protocol hash에 들어가는 필드**여서(`server/protocol/validator.py:528`; openapi 1.4.1 "hashed") vendoring은 검색 run의 protocol_hash도 바꾼다 — 결정 필요.
6. 라이선스: etri_nas 루트에 LICENSE 없음(manifest 전 파일 동일 표기), ultralytics 8.4.173은 AGPL-3.0(wheel METADATA 직접 확인). `register_ultralytics_blocks`는 ultralytics `parse_model` **소스를 읽어 수정·재컴파일**하므로(§4.2) DD-A9 검토 항목에 "파생물 여부"를 추가해야 한다.

## 1. 범위와 방법

- 질문: DD-A2 실행을 위해 (1) 서버 경로에서 실행되는 ETRI 심볼, (2) import 부작용 뒤의 유효 상수값, (3) 커스텀 연산자·파서 확장 내용과 ultralytics 결합, (4) 해시 정의 변경, (5) 출처·라이선스, (6) 환경 변수 의미.
- 방법: 스냅샷 파일 정독 + grep으로 호출 그래프 확인 + AST 기반 정적 계산(부록 A). ultralytics 결합은 `build/wheelhouse/ultralytics-8.4.173-py3-none-any.whl`(sha256 `6d75174bb116be353cc02de65f07dbeea951f480d0ad61f294f3227fa385f373`)에서 `tasks.py`만 scratchpad에 추출해 읽었다(설치·import 없음). 살아 있는 원본 저장소에는 읽기 전용 `git log`/`git diff --stat`(커밋 간 비교, 인덱스 미사용) 2회만 실행했다(§7).

## 2. 서버 호출 경로에서 실행되는 ETRI 심볼 (질문 1)

### 2.1 진입점과 호출되는 함수

| 서버 진입점 | 호출 | 실행되는 ETRI 심볼 | 근거 |
|---|---|---|---|
| `SearchSpace.create` → `load_etri_mixed(etri_root)` | import + `mb.register_ultralytics_blocks()` | `mixed_blocks.register_ultralytics_blocks` (및 그 내부 `_extend_parser_source`, `_register_checkpoint_globals`) | `server/search/space.py:280`, `server/search/etri_bridge.py:137-139` |
| `SearchSpace._add_family` | `self.etri.make_seed_variants(copy.deepcopy(base))` | `mixed_search_space.make_mixed_seed_variants` → `_available`(= `register_ultralytics_blocks` 재호출, idempotent 경로) → `_attention`, `_name`, `_annotate`→`audit_config`; `runtime.REPEAT_MODULES` 읽기 | `server/search/space.py:307`; `yolo_ga_runtime/mixed_search_space.py:161-203` |
| `SearchSpace.inspect` (CPU build audit hook) | `self.etri.audit_model(model)` | `mixed_blocks.audit_model` → `_scope_audit` | `server/search/space.py:328-338`; 결과는 `lineage.audit_for`의 `etri_audit_model`로 후보 audit에 저장 `server/search/lineage.py:77` |
| `EtriMixed.audit_config` 핸들 | 보관만, 호출처 없음 | — | `server/search/etri_bridge.py:62,162`; src 전체 grep에서 `audit_config(` 호출 0건(서버는 자체 포트 `audit.config_estimate` 사용 `server/search/audit.py:48-66`, 호출 `server/search/lineage.py:76`) |
| 학습·평가·벤치 child (`register_hook`) | `ensure_mixed_registered()` = `load_etri_mixed(root).provenance()` | `register_ultralytics_blocks`(+import 부작용) | `server/worker/child.py:52,55-68`, `server/training/selection.py:136-139`, `server/adapters/base.py:187-195`, `server/adapters/ultralytics_adapter.py:164-167` |
| 구조 검사(후보/사용자 YAML에 NAS 모듈이 있을 때) | `load_etri_mixed()` | `register_ultralytics_blocks` | `server/api/model_services.py:276-284`, `:507-515` |
| `/health`, 프로토콜 validator | `probe_mixed_pool()` | **ETRI 코드 미실행**(파일 존재 + `importlib.util.find_spec("ultralytics")`) | `server/api/routes_meta.py:40-46`, `server/protocol/validator.py:499-532`, `server/search/etri_bridge.py:197-222` |

### 2.2 `mutate_operators`·`symbolic_shape_propagate` 도달 불가 증명

- 서버 src 전체 grep(`CompatibilityAwareSampler|TorchExecutor|architecture_fingerprint|mixed_cli|mutate_operators|_legal_choices|symbolic_shape_propagate|install_runtime_hooks|bootstrap_training_process|extension_revision`) 결과: 코드 참조 0건. 일치는 `server/search/space.py:22,53`의 **문자열/주석**("_legal_choices port")과 `server/archive/analysis.py:64`의 **dict key 문자열** `"architecture_fingerprint"`뿐이다.
- ETRI 내부에서 `mutate_operators`에 도달하는 경로는 두 개뿐이다: (a) `mixed_hooks.install_runtime_hooks`가 감싼 `runtime.CompatibilityAwareSampler.sample`(`yolo_ga_runtime/mixed_hooks.py:154-163`) — 서버는 `CompatibilityAwareSampler`를 생성하지 않는다; (b) `mixed_hooks.install_ga_hooks`(`:186-207`) — `yolo_ga_full_runtime` 전용이며 서버는 import하지 않는다(스냅샷 role_group `etri-full-runtime(reference)`).
- `symbolic_shape_propagate`의 ETRI 내 호출자는 `mutate_operators`(`mixed_search_space.py:116`), `pipeline`/`runtime.evaluate_candidate`(`runtime.py:932`), `mixed_cli`(`:146,253`)뿐이다 — 모두 서버 비도달.
- 따라서 **DD-A2가 "mutate_operators 전용"으로 적은 `symbolic_shape_propagate` 이식은 서버 기능에 필요하지 않다.** 서버의 "mutation"은 `SearchSpace.mutate`(`server/search/space.py:477`)가 자체 prior 테이블(`alternatives`, `:398`)로 수행한다.

### 2.3 `_legal_choices`와 `space.illegal_reason`의 정적 등가성

`yolo_ga_runtime/mixed_search_space.py:78-98`과 `server/search/space.py:176-196`을 조건별로 대조했다.

| 연산자 | ETRI `_legal_choices` 거부 조건 | 서버 `illegal_reason` | 일치 |
|---|---|---|---|
| C2PSA | `c1 != c2 or c2 < 128 or tokens > 1024 or hidden % heads`, `heads = max(hidden//64,1)`, `hidden=int(c2*.5)` | 동일 4조건(L186-194 + L195) | 예 |
| C3TR | `tokens > 1024 or hidden % 4` | 동일 | 예 |
| A2C2f | `c2 < 64 or hidden % 32 or tokens > 1024` | 동일 | 예 |
| NASGlobalTransformer | `tokens > 1024` | 동일(`TOKEN_BUDGET = 1024`, `:62`) | 예 |
| C2f, C3k2, NASWindowTransformer | 항상 허용 | 항상 허용(+반복 규칙은 서버 추가) | 예 |
| `available` 필터(파서에 등록된 클래스만) | `_available()` | `ops_feature` 선택(`FEATURE_OPS`/`NATIVE_FEATURE_OPS`, `:54-55,290-292`) | 의미 동등 |

→ `_legal_choices`를 별도 vendoring할 기능적 이유는 없다. 권고: 원문을 참조 사본으로 두고 등가성 단위 테스트(두 함수에 같은 (c1,c2,h,w) 격자를 넣어 비교)를 추가한다.

## 3. import 클로저 증명과 유효 상수값 (질문 2)

### 3.1 import 체인(정적)

`server/search/etri_bridge.py:137` `importlib.import_module("yolo_ga_runtime.mixed_search_space")` →

1. 패키지 `yolo_ga_runtime/__init__.py` 실행: `:10` `from .crossover_v22 import NASUtils, Crossover`(stdlib만: `crossover_v22.py:17-18`) → `:11-21` `from .runtime import ...`(`runtime.py:41` `import yaml` = PyYAML 의존, `:43` crossover 재import) → `:22-25` `from .pipeline import ...`(`pipeline.py:21-26` runtime 심볼만) → `:26` `from . import ga_hooks`(dataclass/Protocol 정의만) → `:44-47` `from .mixed_hooks import install_runtime_hooks; _install_mixed_runtime_hooks()`.
2. `install_runtime_hooks()`(`mixed_hooks.py:63-172`)의 **영구 부작용**: `runtime.FEATURE_MODULES.update(...)`(`:69`), `runtime.REPEAT_MODULES.update(...)`(`:70`), `runtime.MODULE_FAMILY.update(...)`(`:71-75`), `runtime._module_out_hw` 교체(`:77-86`, NASPatchMerge 출력 크기 ceil(H/stride)), `NASUtils._conv_stride`/`_is_down_event`(`:88-108`), `Crossover._is_feature_module`/`_guess_out_channels`(`:110-125`), `TorchExecutor.__init__`/`build`(`:127-143`), `CompatibilityAwareSampler.__init__`/`sample`(`:145-163`), `runtime.architecture_fingerprint` 교체(`:164-171`), 플래그 `:172`.
3. `mixed_search_space` 모듈 본문: `copy, math, os, Counter`만(`:6-9`); runtime은 함수 내부에서 지연 import(`:113,161`).
4. `server/search/etri_bridge.py:138` `yolo_ga_runtime.mixed_blocks`: `torch`(`mixed_blocks.py:15-17`), 모듈 본문에 ultralytics import 없음(`register_ultralytics_blocks` 내부 `:218-219`).
5. `register_ultralytics_blocks()`: `import ultralytics; from ultralytics.nn import modules, tasks`(`:218-219`).

`sys.modules`에 남는 `yolo_ga_runtime*` 모듈 = **8개**: `__init__`, `crossover_v22`, `runtime`, `pipeline`, `ga_hooks`, `mixed_hooks`, `mixed_search_space`, `mixed_blocks`. `mixed_cli`는 어떤 모듈도 import하지 않는다(스냅샷 전체 grep: `mixed_hooks.py:22`의 **파일 바이트 읽기**만). 이 8개가 `_loaded_runtime_files()`(`etri_bridge.py:175-188`)로 수집되어 `files_sha256`가 되고(`:149-150`), ACCEPTANCE_RESULTS §10 S1-F4 행도 같은 8개를 열거한다(`application/MBNAS_server/docs/ACCEPTANCE_RESULTS.md:853`).

프로세스 전역 부작용(vendoring 시 재현/제거 판단 대상):

| 부작용 | 위치 | 서버 기능에 필요한가 |
|---|---|---|
| `os.environ["ETRI_NAS_MIXED"]="1"` 영구 설정(복원 없음) | `server/search/etri_bridge.py:130` | 아니오(§8) |
| `sys.path` 삽입/제거, `sys.dont_write_bytecode` | `:131-142,76-84` | 아니오(패키지 import로 대체, DD-A2) |
| runtime/crossover monkeypatch 전부 | `mixed_hooks.py:69-172` | **REPEAT_MODULES 값만** 필요(§3.3) |
| `torch.serialization.add_safe_globals([...])` | `mixed_blocks.py:167-185` | 예(커스텀 모듈 포함 ultralytics 체크포인트를 `weights_only=True`로 읽을 때) |
| `ultralytics.nn.tasks.parse_model` 교체, `tasks`/`modules` 네임스페이스에 3클래스 추가, `modules.__all__` 확장 | `mixed_blocks.py:243-249,263` | 예(핵심) |

### 3.2 `make_mixed_seed_variants`가 runtime에서 읽는 것

- `from . import runtime as R`(`mixed_search_space.py:161`) 뒤 사용은 `R.REPEAT_MODULES` **한 곳**(`:176`)이다. `FEATURE_MODULES`, `MODULE_FAMILY`, `_module_out_hw`, `symbolic_shape_propagate`는 쓰지 않는다.
- `repeat_ops = set(R.REPEAT_MODULES) | REPEAT_CHOICES`(`:176`)는 transformer_backbone 변형에서 "어떤 backbone 행을 `NASWindowTransformer [c_out]`로 바꿀지"(`:193-194`, 추가로 `{'SPP','SPPF','PSA'}`)만 결정한다.

### 3.3 유효 상수값(정적 계산, 부록 A 스크립트 출력)

`runtime.REPEAT_MODULES` 기본값(`runtime.py:60-63`, 14개) ∪ `mixed_hooks.py:70`의 `CUSTOM_REPEATS | {"A2C2f"}`(3개) = **유효 REPEAT_MODULES 17개**:

```
A2C2f, BottleneckCSP, C1, C2, C2PSA, C2f, C2fAttn, C2fCIB, C2fPSA, C3, C3Ghost, C3TR, C3k2, C3x,
NASGlobalTransformer, NASWindowTransformer, RepC3
```

- `REPEAT_CHOICES`(`mixed_search_space.py:14` = `set(CHOICES)`, 7개)는 전부 위 집합의 부분집합이므로 `repeat_ops == 유효 REPEAT_MODULES`(스크립트가 `True` 확인).
- transformer_backbone 변형에서 `NASWindowTransformer`로 치환되는 이름 = 위 17개 ∪ `{SPP, SPPF, PSA}` = 20개.
- 교차 검증: ultralytics 8.4.173 `parse_model`의 `repeat_modules`(`tasks.py:2108-2126`)는 15개(`BottleneckCSP, C1, C2, C2f, C3k2, C2fAttn, C3, C3TR, C3Ghost, C3x, RepC3, C2fPSA, C2fCIB, C2PSA, A2C2f`)이고 ETRI 패치가 `NASWindowTransformer, NASGlobalTransformer`를 더해 17개가 된다(§4.2 정적 패치 결과) — **유효 REPEAT_MODULES와 집합이 같다.** 즉 vendoring 시 "ultralytics 파서의 repeat 집합 + ETRI 반복 연산자 2개"라는 의미로 리터럴을 고정해도 된다.
- 참고(서버 비사용): 유효 `FEATURE_MODULES` = 기본 36개(`runtime.py:50-58`) + `{NASPatchMerge, NASWindowTransformer, NASGlobalTransformer, A2C2f}` = 40개; `MODULE_FAMILY` 추가 4건(`mixed_hooks.py:71-75`: NASWindowTransformer→`custom_window_transformer`, NASGlobalTransformer→`custom_global_transformer`, NASPatchMerge→`custom_linear_patch_merge`, A2C2f→`v12`); `DETECT_MODULES`(`:64`) `{Detect, v10Detect, Segment, Pose, OBB, WorldDetect}`; `CONCAT_MODULES` `{Concat}`; `UPSAMPLE_MODULES`(`:68`) `{nn.Upsample, nn.ConvTranspose2d, ConvTranspose}`; `FIXED_DOWN2_MODULES`(`:70`) `{ADown, SCDown, AConv}`. Option B(§5.3)에서만 필요.

### 3.4 서버 템플릿(yolo11n/yolo26n)에 대한 `make_mixed_seed_variants` 정적 유도

기준 YAML은 wheel 안의 `ultralytics/cfg/models/11/yolo11.yaml`(sha256 `43d8a7c8…3784c`)·`26/yolo26.yaml`(`b1d1fa0c…7ef0`)이며 서버는 `load_base_yaml`(`server/search/space.py:208-219`)로 같은 파일을 읽는다(`FAMILY_RE`가 yolo11|yolo26만 허용 `:67`). 두 템플릿의 backbone 11행은 구조가 같다(yolo26은 SPPF 인자 `[1024, 5, 3, True]`만 다름).

| 행 | 기본 | `cnn_backbone`(`:171-175`) | `transformer_backbone`(`:177-199`) |
|---|---|---|---|
| 0,1,3,5,7 `Conv [c,3,2]` | — | 불변(`_attention` False) | `NASPatchMerge [c, 2]`(k=3 홀수, s=2, padding None 통과 `:184-192`) |
| 2,4,6,8 `C3k2 [c, False, 0.25]`/`[c, True]` | — | 불변(`_attention`: 인자 길이 ≤3 → False `:32-33`) | `NASWindowTransformer [c]`(C3k2 ∈ repeat_ops `:193`) |
| 9 `SPPF [1024, 5(,3,True)]` | — | 불변 | `NASWindowTransformer [1024]`(SPPF ∈ 추가 집합 `:193`) |
| 10 `C2PSA [1024]` | — | `C2f [1024]`(`:175`) | `NASWindowTransformer [1024]` |
| head 전부, `nc/scale/scales/end2end/reg_max` | 그대로 | 그대로 | 그대로(deepcopy `:168-170`) |

- 각 변형에는 `__mixed_seed_variant__`, `__mixed_audit__` 키가 붙지만(`:200-202,53-55`) 서버 `Genotype.from_yaml_dict`→`canonical_yaml`이 `YAML_KEYS`(`server/search/genotype.py:41-42`) 외 키를 버린다(`:68-76`) → `yaml_hash`·템플릿 provenance에 들어가지 않는다. 따라서 vendoring 시 `_annotate`/`audit_config`는 **생략해도 서버 출력이 동일**하다(단, `EtriMixed.provenance()["functions"]` 목록 문자열은 바뀜 `etri_bridge.py:69-70`).
- 위 유도는 `tests/search/test_space_genotype.py:121-123`(transformer_backbone의 `NASPatchMerge == 5`)·`:125-126`(yolo26 head 행 22 attn은 변형 대상 아님)과 부합한다. 실행 확인은 못 했다.

## 4. 커스텀 연산자 3종과 `register_ultralytics_blocks` (질문 3)

### 4.1 연산자(`yolo_ga_runtime/mixed_blocks.py`, 전체 314행, sha256 `4233dd324e755440d1bc…`)

| 클래스 | YAML 인자 / 생성자 | 동작 요약 | 행 |
|---|---|---|---|
| `NASPatchMerge(c1, c2, patch=2)` | `[-1, 1, NASPatchMerge, [c2, patch]]`; 반복 금지 | 하단/우측 zero-pad → `(B, c1·p·p, ⌈H/p⌉, ⌈W/p⌉)` reshape → `nn.Linear(c1·p·p, c2)` → `LayerNorm(c2)`; Conv 없음 | `:39-62` |
| `_PreNormAttention(channels, heads, mlp_ratio)` | 내부 블록 | `LayerNorm → nn.MultiheadAttention(batch_first, dropout 0) + 잔차 → LayerNorm → Linear-GELU-Linear + 잔차`; `key_padding_mask` 지원 | `:65-81` |
| `NASWindowTransformer(c1, c2, n=1, heads=4, window=7, mlp_ratio=2.0)` | `[-1, repeats, NASWindowTransformer, [c2, heads, window, mlp_ratio]]`(파서가 c1·n 삽입) | `Linear(c1→c2)`(c1≠c2일 때) → window 패딩·분할(비이동 window) → 2D sin/cos 위치(`_position_2d` `:28-36`) 가산 → `n`개 블록(패딩 토큰 mask) → crop. `actual_heads = gcd(c2, heads)`(`:96`) | `:84-131` |
| `NASGlobalTransformer(c1, c2, n=1, heads=4, max_tokens=1024, mlp_ratio=2.0)` | `[-1, repeats, NASGlobalTransformer, [c2, heads, max_tokens, mlp_ratio]]` | 전역 attention; `H·W > max_tokens`면 `ValueError`(`:154-155`, 서버 repair가 이 실패를 처리 `tests/search/test_space_genotype.py:92-101`) | `:134-160` |

보조: `_positive_integer`(`:22-25`), `IMPLEMENTATION_VERSION = "1.0.0"`(`:19`, `audit_model`·capabilities에 기록), `_CUSTOM`/`_REPEATED`(`:163-164`), `_scope_audit`/`audit_model`(`:267-314`; 출력 키 `conv_modules, attention_modules, params, backbone_family, feature_family, full_model_family, terminal_detect, scopes, implementation_version`). 모듈 import 의존은 `torch`뿐.

### 4.2 `register_ultralytics_blocks()`(`:216-264`)의 동작과 ultralytics 8.4.173 결합

동작 순서:
1. `tasks.parse_model`에 `_nas_mixed_installed` 플래그가 있으면 idempotent 경로(`:221-227`): 저장된 capabilities 복사 + `_register_checkpoint_globals()` 재실행 + `already_installed=True`.
2. `inspect.getsource(tasks.parse_model)`(`:228-232`) — **소스 파일이 설치돼 있어야 한다**(wheel 설치는 충족; pyc 전용 배포는 실패).
3. `_extend_parser_source`(`:188-213`): AST에서 `parse_model` 내부의 `base_modules`/`repeat_modules` 대입을 찾고, 값이 `frozenset({...})`/`set({...})`/리터럴 집합이면 원소 이름 추가(`base_modules`에 3개, `repeat_modules`에 2개). 두 이름이 모두 없으면 `RuntimeError`(`:210-212`).
4. `exec(compile(tree, "<ETRI_NAS additive parse_model extension>", "exec"), tasks.__dict__, namespace)`(`:236`) — 패치된 함수의 `__globals__`가 `tasks.__dict__`이므로 `LOGGER`, `make_divisible`, `_SafeLoad`, 모듈 클래스들이 호출 시점에 해석된다.
5. `setattr(tasks, name, cls)`, `setattr(modules, name, cls)`, `modules.__all__` 확장(`:243-249`) — 패치된 `parse_model`의 `globals()[m]`(`tasks.py:2133`) 조회가 YAML 문자열 `NASPatchMerge` 등을 찾게 하는 부분.
6. capabilities 기록(`:250-257`: `installed, already_installed, implementation_version, ultralytics_version, custom_modules, repeat_modules, parser_source_sha256, native_modules{C2f,C3TR,C3k2,C2PSA,A2C2f,SPPF,Detect}` + safe-globals 정보), 함수 속성 `_nas_mixed_original/_nas_mixed_installed/_nas_mixed_capabilities` 설정 후 `tasks.parse_model = patched`(`:258-263`).

8.4.173 결합 검증(정적, 부록 A `a7_parser_patch_proof.py`):

| 항목 | 확인 결과 | 근거 |
|---|---|---|
| `parse_model` 정의 | `tasks.py:2030-2243`, 모듈 최상위 함수 1개 | AST |
| `base_modules` | `:2070-2107` `frozenset({...})` 34개 → 패치 후 37개(끝에 `NASPatchMerge, NASWindowTransformer, NASGlobalTransformer`) | 패치 함수 원문 적용 |
| `repeat_modules` | `:2108-2126` `frozenset({...})` 15개 → 17개 | 동일 |
| 컴파일 | 패치 AST `compile()` 성공 | 동일 |
| YAML 이름 해석 | `:2133` `globals()[m]` → 5단계 `setattr(tasks, …)` 필수 | 읽기 |
| restricted 로딩 | `:2057` `restricted = _SafeLoad.restricted()`, `:2135-2137` nn.Module 서브클래스만 허용 → 3클래스 모두 `nn.Module`이므로 통과 | 읽기 |
| `C2f, C3k2, C3TR, C2PSA, A2C2f`가 `tasks` 네임스페이스에 존재(`_available` 조건) | `tasks.py:18-60` import 목록에 전부 포함 | 읽기 |
| `parser_source_sha256` | 원문 L2030-2243 기준 `7da6bb14102890302fd9c8708a9f7d0fbb38e94e8f511c0434fc41c0fcff5f6d` — `inspect.getsource`가 정확히 이 범위를 돌려준다는 가정 하의 재계산; 설치본에서 재확인 필요(확인 못 함) | 스크립트 |

결합의 성격: 이 메커니즘은 ultralytics의 **지역 변수 이름 두 개와 리터럴 형태**에 묶여 있다. 8.4.173에서는 성립하지만, 상위 버전에서 집합이 모듈 상수로 빠지거나 이름이 바뀌면 `RuntimeError`로 명시적으로 실패한다(조용한 오동작은 아님). vendoring 후에도 `ultralytics==8.4.173` 고정(`application/MBNAS_server/pyproject.toml:34`, `requirements.lock.txt:131`)을 유지해야 하고, 버전 상승 시 이 정적 증명 스크립트를 다시 돌려야 한다.

### 4.3 체크포인트 safe-globals와 ultralytics `_SafeLoad`

- `_register_checkpoint_globals`(`:167-185`)는 3클래스 + `_PreNormAttention` + `nn.Linear/LayerNorm/MultiheadAttention/Identity/ModuleList/Sequential/GELU` + `NonDynamicallyQuantizableLinear`를 `torch.serialization.add_safe_globals`에 **프로세스 전역**으로 등록하고, 등록 이름을 `f"{cls.__module__}.{cls.__qualname__}"`로 보고한다 → vendoring으로 `__module__`이 `yolo_ga_runtime.mixed_blocks` → `mbnas_model_runtime.operators.<module>`로 바뀐다.
- ultralytics 8.4.173 `_SafeLoad.loading`(`tasks.py:1681-1750`)은 체크포인트가 요구하는 global 중 `torch.nn.modules`, `ultralytics.nn.modules`, `ultralytics.nn.tasks` 계열만 자동 허용한다(`:1704-1710`). ETRI 클래스는 이 자동 허용 밖이므로 ETRI 자체 등록에 의존하며, ultralytics 문서(`:1687-1688`)대로 등록은 프로세스 수명 동안 유지된다 → 호환.
- **영향**: MBNAS 자체 full-state/weights-only 포맷은 텐서·단순 컨테이너만 저장하므로(`server/training/checkpoint.py:11,159`) 모듈 경로 변경에 영향 없음. 반면 ultralytics 포맷 `.pt`(피클된 모델)를 `load_checkpoint`로 읽는 경로(`server/adapters/ultralytics_adapter.py:180-182,290-292`)에서 **`yolo_ga_runtime.mixed_blocks.*`로 피클된 과거 산출물**(예: ETRI `mixed_cli` 캠페인 체크포인트)은 vendoring 후 해당 모듈이 없어 unpickle이 실패한다. 그런 파일이 registry/weights 경로에 존재하는지는 스냅샷으로 확인 못 함.

## 5. vendoring 표 (질문 1·2·3 종합)

### 5.1 Option A — 권고(서버 도달 심볼만)

| 원본(스냅샷) | 행 | 심볼 | 대상 모듈(제안) | 공개 여부 | 변경 사항 |
|---|---|---|---|---|---|
| `yolo_ga_runtime/mixed_blocks.py` (sha256 `4233dd324e755440d1bc…`, 아래 §7 전체값) | 1-314 전체 | `IMPLEMENTATION_VERSION, _positive_integer, _position_2d, NASPatchMerge, _PreNormAttention, NASWindowTransformer, NASGlobalTransformer, _CUSTOM, _REPEATED, _register_checkpoint_globals, _extend_parser_source, register_ultralytics_blocks, _scope_audit, audit_model` | `mbnas_model_runtime/operators/etri_mixed_blocks.py` | 공개(DD-A1 b) | 알고리즘 무변경. 모듈 docstring에 출처·sha256 추가, `VENDORED_FROM` 상수 추가. `__module__` 경로 변경의 결과(§4.3) 기록 |
| `server/search/etri_bridge.py` | 38, 225-233 | `MIXED_MODULES`, `ensure_mixed_registered`(→ `register_ultralytics_blocks()` 직접 호출로 단순화), `yaml_uses_mixed_modules` | `mbnas_model_runtime/operators/__init__.py` | 공개 | runner의 `register_hook` 대상이 된다(§9) |
| `yolo_ga_runtime/mixed_search_space.py` | 12-17 | `CHOICES, REPEAT_CHOICES, ATTENTION_NAMES, PURE_NAMES` | `mbnas_core/search/mixed_pool/seed_variants.py` | 비공개(DD-A1 a) | 그대로 |
| 〃 | 25-55 | `_name, _attention, audit_config, _annotate` | 〃 | 비공개 | `audit_config`는 호출처 없음(§2.1) — provenance 함수 목록 유지용으로만 보존하거나 삭제(결정 D4) |
| 〃 | 58-62 | `_available` | 〃 | 비공개 | `from .mixed_blocks import register_ultralytics_blocks` → `from mbnas_model_runtime.operators import register_ultralytics_blocks` |
| 〃 | 154-203 | `make_mixed_seed_variants` | 〃 | 비공개 | `from . import runtime as R` → `runtime_constants.REPEAT_MODULES`; 나머지 바이트 동일 |
| `yolo_ga_runtime/runtime.py` + `mixed_hooks.py` | `runtime.py:60-63`, `mixed_hooks.py:14,70` | 유효 `REPEAT_MODULES`(17개, §3.3 리터럴) | `mbnas_core/search/mixed_pool/runtime_constants.py` | 비공개 | 새 리터럴 `frozenset`; 주석에 "runtime.py L60-63 ∪ mixed_hooks L70; == ultralytics 8.4.173 repeat_modules ∪ {NASWindowTransformer, NASGlobalTransformer}" |
| `server/search/etri_bridge.py` | 42-70, 112-164, 197-222 | `EtriUnavailable, SearchSpaceUnavailable, EtriMixed(provenance), load_etri_mixed, probe_mixed_pool` | `mbnas_core/search/mixed_pool/bridge.py`(이름 유지 가능) | 비공개 | `sys.path`/root/`.git HEAD`/`ETRI_NAS_MIXED` 제거; `files` → vendored 모듈 바이트 sha256; `probe` → "ultralytics findable + 패키지 import 가능" |

제외(0 심볼 필요): `crossover_v22.py`, `pipeline.py`, `ga_hooks.py`, `mixed_hooks.py`(상수값만 흡수), `mixed_cli.py`, `runtime.py`의 나머지 전부, `runtime.py.before_smoke_nc_…bak`, `yolo_ga_full_runtime/*`.

분량: operators 314행 + seed_variants 약 92행 + 상수 약 20행 + bridge(재작성) ≈ 430행.

### 5.2 Option A에서 반드시 이식할 "최소 runtime 상수"(리터럴)

```python
# mbnas_core/search/mixed_pool/runtime_constants.py
# yolo_ga_runtime/runtime.py L60-63 (14) ∪ yolo_ga_runtime/mixed_hooks.py L70 CUSTOM_REPEATS|{"A2C2f"} (3)
REPEAT_MODULES = frozenset({
    "BottleneckCSP", "C1", "C2", "C2f", "C3k2", "C2fAttn", "C3", "C3TR",
    "C3Ghost", "C3x", "RepC3", "C2fPSA", "C2fCIB", "C2PSA",
    "A2C2f", "NASWindowTransformer", "NASGlobalTransformer",
})
```

이 외의 runtime 상수는 서버 경로에서 읽히지 않는다(§3.2).

### 5.3 Option B — DD-A2 문구 그대로(`mutate_operators`까지) 이식할 때의 추가 클로저

| 원본 | 행 | 심볼 | 비고 |
|---|---|---|---|
| `mixed_search_space.py` | 65-75, 78-98, 101-151 | `_input_shape, _legal_choices, mutate_operators` | `mixed_enabled()` 게이트(`:111`)와 env 읽기(`:115`) 제거/인자화 필요 |
| `runtime.py` | 85-87 | `make_divisible` | |
| 〃 | 94-130 | `TensorSignature, NodeShape, ValidationFailure` | dataclass |
| 〃 | 50-58, 64-70 | `FEATURE_MODULES`(유효 40개), `DETECT/CONCAT/UPSAMPLE/FIXED_DOWN2_MODULES` | 유효값은 §3.3 |
| 〃 | 290-362 | `_resolve_scale_and_depth_width, _conv_stride_from_args, _autopad, _conv_hw, _module_out_hw` | |
| 〃 | 365-470 | `symbolic_shape_propagate` | |
| `mixed_hooks.py` | 77-86 | `_module_out_hw`의 NASPatchMerge 래퍼(출력 = ⌈HW/stride⌉) | 없으면 NASPatchMerge 행 뒤 shape 추적이 틀림 |

약 310행 추가, 서버 호출자 0. 서버 `SearchSpace.mutate`가 별도 설계(slot/prior)로 동작하므로 두 "mutation"이 공존하면 정책 혼동 위험. **권고: 지금은 이식하지 않고, 원문 사본을 `docs/internal/provenance/vendored_reference/`(또는 동등 위치)에 sha256과 함께 보존.**

## 6. 해시 영향과 `mbnas-mixed-space/3` 제안 (질문 4)

### 6.1 현재 `/2`에 무엇이 들어가는가

- `describe()`의 `body`(`server/search/space.py:510-518`)를 `canonical_sha256`(정렬 키 JSON, `server/util/canon.py:41-56`)한 것이 `space_hash`, `{"space": body, **prior_def}`가 `prior_hash`(`:524`).
- `body.templates[].source`에 `origin = {"kind": "etri_mixed_seed_variant", "function": "mixed_search_space.make_mixed_seed_variants", "base": src, "etri_files_sha256": {8파일: sha256}}` + `variant`, `yaml_hash`가 들어간다(`:308-309,323-324`). `src`에는 ultralytics YAML 경로·sha256·`ultralytics_version`이 포함(`:219-220`).
- `describe()["etri"]`(`root, git_head, files_sha256, registration, env, functions`; `etri_bridge.py:65-70`)는 **body 밖**(`:528`) → 해시에 들어가지 않는다. `registration.parser_source_sha256`도 해시 밖.
- 소비: `lineage_for`가 `version/variant/space_hash/prior_hash`만 후보 lineage에 저장(`server/search/lineage.py:65-66`); `describe()` 전체는 API/DB에 저장되지 않는다(grep: `handlers.py:220`, `generation.py:145`에서 lineage 생성에만 사용).

### 6.2 vendoring이 바꾸는 것

1. `etri_files_sha256`의 키(파일 경로)와 값(바이트)이 모두 달라진다 → 같은 nc/imgsz/families에서 `space_hash`/`prior_hash`가 바뀐다. 이는 설계상 의도된 민감도(S1-F1/S1-F4)이므로 "/2 값 유지"는 불가능하고 바람직하지도 않다.
2. `search_space_resolved = {"variant": ..., "etri_root": probe["root"]}`(`server/protocol/validator.py:528`)는 resolved protocol에 기록되고 openapi 1.4.1이 "hashed"로 명시한다(`application/MBNAS_server/contracts/openapi.json`, `search_space_resolved` description; `etri_root`는 `["string","null"]`, required는 `variant`만). vendoring 후 `etri_root` 값이 바뀌면 **동일 사용자 프로토콜의 protocol_hash가 바뀐다.**
3. `/health capabilities.search_space = {variant, root, reason, checked, fallback_policy}`(`server/api/routes_meta.py:43-46`) — Health 스키마는 open object(description에 키 열거)이고 테스트 pin은 `variant == "etri_mixed"`와 `fallback_policy` 접두사뿐(`tests/api/test_set3_health.py:24-27`). 웹 `SearchSpaceState`가 `root`를 어떻게 표시하는지는 확인 못 함(`MBNAS_web/src/screens/Server.tsx:85`만 확인).

### 6.3 제안: `mbnas-mixed-space/3`

```python
SPACE_VERSION = "mbnas-mixed-space/3"
SPACE_HASH_DEFINITION = (
    "mbnas-mixed-space/3: canonical sha256 of describe() body; mixed-pool identity = content sha256 of the "
    "vendored mixed-pool modules executed for seed variants and parser registration (mixed_pool_files_sha256: "
    "mbnas_model_runtime/operators/etri_mixed_blocks.py, mbnas_core/search/mixed_pool/seed_variants.py, "
    "mbnas_core/search/mixed_pool/runtime_constants.py), read from the installed files at load time. "
    "Supersedes mbnas-mixed-space/2 (content sha256 of every executed yolo_ga_runtime module of an external "
    "ETRI_NAS checkout); the /2 inputs and their sha256 are recorded as vendor_origin (provenance, not hashed).")
```

- `templates[].source` = `{"kind": "vendored_mixed_seed_variant", "function": "mbnas_core.search.mixed_pool.seed_variants.make_mixed_seed_variants", "base": src, "mixed_pool_files_sha256": {...3파일...}}` (해시 대상).
- `describe()["etri"]` → 키 이름은 호환을 위해 유지하거나 `mixed_pool`로 개명(결정 D6). 내용: `{"root": None, "git_head": None, "files_sha256": <3파일>, "vendor_origin": {<8파일: /2 sha256>, "etri_nas_commit": "d5028e8 (== 18c4815 for yolo_ga_runtime/)", "space_version_before": "mbnas-mixed-space/2"}, "registration": caps, "implementation_version": "1.0.0", "gate": None, "functions": [...]}`.
- 해시 대상 파일을 3개로 한정하는 이유: `/2`의 규칙이 "실행된 ETRI 코드의 내용"이었고, 이제 실행되는 ETRI 유래 코드가 이 3개뿐이기 때문이다. bridge(MBNAS 코드)는 `/2`에서도 해시 밖이었으므로 제외.
- 바이트는 **설치된 파일을 읽어** 계산한다(`Path(module.__file__).read_bytes()`); 패키지가 zip/pyc로 배포되면 실패하므로 `__file__`이 없을 때 `SearchSpaceUnavailable`로 명시 실패(묵시적 생략 금지).

`/2`→`/3` 매핑 레코드(제안: `docs/internal/provenance/MIXED_SPACE_HASH_MAP.json`):

| 항목 | /2 (현재 스냅샷) | /3 (vendoring 후) |
|---|---|---|
| 입력 파일 | 8개(§3.1) + 그 sha256(§7 표) | 3개 vendored 모듈 + sha256(WP-1에서 산출) |
| 기록된 값 | nc=1, imgsz=640, 기본 families, ETRI d5028e8: `space_hash sha256:03e316a81b2f…`, `prior_hash sha256:fa984229a8aa…`(`application/MBNAS_server/docs/ACCEPTANCE_RESULTS.md:852`, 문서에 앞자리만 기록 — 전체값 확인 못 함) | WP-1 격리 venv에서 같은 인자로 산출해 VALIDATION_REPORT에 기록 |
| 저장 후보 행 | `/1`(`91c2e66d3c88…`) 또는 `/2` 값 보유, 재기록 없음(`space.py:15-17`) | 재기록 없음; lineage의 `search_space.version`으로 구분 |

### 6.4 정의를 고정하는 테스트(수정 대상)

| 테스트 | pin 내용 | 조치 |
|---|---|---|
| `tests/search/test_space_hash_content.py:84-90` | `files_sha256` 키 집합 ⊇ 6개 `yolo_ga_runtime/*`; `version == "mbnas-mixed-space/2"`; `"content" in definition`; `:43-54` ETRI 루트 복사·`runtime.py` 변조로 해시 변화 확인; `:94-104` 두 번째 루트 거부 | 재작성: 키 집합을 3개 vendored 경로로, 버전 `/3`; "내용 변화→해시 변화"는 서브프로세스에서 vendored 모듈 사본을 `sys.path` 앞에 두고 변조해 검증; "두 번째 루트 거부"는 삭제 |
| `tests/search/test_space_genotype.py:22-25` | `registration.installed is True`; `files_sha256 ⊇ {mixed_blocks, mixed_search_space, __init__, runtime}` | 키 집합 갱신 |
| `tests/search/test_space_fallback_optin.py:31-34,50-55,59-69,84-107,125-134` | `etri_root="/nonexistent"`·`MBNAS_ETRI_NAS_ROOT=/nonexistent`로 unavailable 유도 | "unavailable" 유도 수단 교체(예: `monkeypatch`로 `register_ultralytics_blocks`가 `RuntimeError`를 내게 하거나 ultralytics spec 부재 시뮬레이션) |
| `tests/search/test_etri_bridge_permissions.py` 전체 | 읽기 불가 루트(DD-26) | 폐기(루트 개념 소멸); DD-26의 "진단은 500을 내지 않는다"는 의미는 bridge 예외 처리 테스트로 승계 |
| `tests/search/test_structure_observation.py:22,375-383` | `ETRI_ROOT` 없으면 skip, `load_etri_mixed(ETRI_ROOT)` | fixture를 무조건 등록으로 변경 |
| `tests/search/conftest.py:34-35`, `tests/api/test_candidate_detail_lineage.py:39-40`, `tests/conftest.py:45-52,109`, `tests/worker/harness.py:33`, `tests/worker/test_generation_crash.py:41`, `scripts/run_tests_host.sh:9` | `MBNAS_TEST_ETRI_ROOT`/`MBNAS_ETRI_NAS_ROOT`/`etri_nas_root` 배관 | 제거(또는 D3에 따라 allow-root 용도로만 유지) |
| `tests/search/test_repair_evidence_seed.py:45-61` | seed 116, 세대 0 후보 1의 C2PSA repair(행 9 C3TR→SPPF) | **변경 없이 통과해야 하는 충실도 게이트**(템플릿·prior 스트림이 동일하면 유지됨) |
| `tests/unit/test_config.py:11,21` | `etri_nas_root` 기본값 `/root/detection/ETRI_NAS` | D3 결과에 따름 |
| `tests/api/test_registry_structure.py:220-233` | `etri_nas_root`가 사용자 YAML allow-root | D3 결과에 따름 |

## 7. 출처·라이선스·무결성 (질문 5)

스냅샷 sha256은 스크립트로 재계산해 manifest와 **전부 일치**했다(`manifest_match: true`). git 메타는 manifest 값(HEAD `18c481517d4393f5d7818aba5b37d721e15628c5`, 전 파일 `tracked_clean`). 라이선스 필드는 전 파일 동일: `"no LICENSE file in etri_nas root; ETRI_NAS research code, rights review pending"`.

| 파일 | bytes | sha256 | git blob sha1 | vendoring |
|---|---|---|---|---|
| `yolo_ga_runtime/__init__.py` | 2087 | `ef0224c2bab7101623cd7a5c0919aa7ea00615e613ef67ca7cf529224314b63a` | `ede01908…` | 제외(/2 해시 입력) |
| `yolo_ga_runtime/mixed_blocks.py` | 16617 | `4233dd324e755440d1bc223241fe2e0c95ca37f71e4c3aac8513778491496e70` | `eb728503…` | **전체** |
| `yolo_ga_runtime/mixed_search_space.py` | 9331 | `adb6f41302a8ee8b526533edff9b52c916479c93ed08e63173b43241012e5da7` | `41db2484…` | 부분(L12-17, 25-62, 154-203) |
| `yolo_ga_runtime/mixed_hooks.py` | 11063 | `297511c927e1c6e192d9ebebcebce2b7977d33c00cc39aaf7dacce580cb9deb7` | `98bb4e20…` | 상수 2줄만(L14, L70) |
| `yolo_ga_runtime/runtime.py` | 46260 | `f538c50f415cf0626d70a8be5b3d25550077983451202e2244a8d7423fd045e1` | `c4216fb5…` | 상수 1개(L60-63) |
| `yolo_ga_runtime/crossover_v22.py` | 26348 | `3222dfbdd816c67888ff9a49fe38cb8e054cf5bc3810620e6914c0db88e4056e` | `ba7c29d1…` | 제외 |
| `yolo_ga_runtime/pipeline.py` | 14490 | `0551ae0b25b76980c1a834c36f371384ff67801da34eb8d65b07a232a1115593` | `bcb87738…` | 제외 |
| `yolo_ga_runtime/ga_hooks.py` | 1819 | `3ce290d9772c9855ae741629ce9d1f2d42f90f9e2a7be2f7d0fc1819a7b01f57` | `25f03208…` | 제외 |
| `yolo_ga_runtime/mixed_cli.py` | 29954 | `80e55c6c71f9d2870eb8c01f0c1b24e9a5f76eb1fb0b6ab07472397c16e944ef` | `e977f8b9…` | 제외(import되지 않음) |
| `yolo_ga_runtime/runtime.py.before_smoke_nc_20261006_043702_dd7da4de.bak` | 46272 | `47b6ff8bfae0d2af2d5e05d51d710fe7aebc4953a6aba25cc42bac5a53527efa` | `a58911e2…` | 제외(runtime.py와 L726 1줄 차이: `train_smoke`의 nc 조회) |

(mixed_blocks.py의 sha256 전체값: `4233dd324e755440d1bc223241fe2e0c95ca37f71e4c3aac8513778491496e70` — 본문 표의 줄임 표기 `4233dd324e755440d1bc…`는 이 값이다.)

추가 사실:
- 읽기 전용 `git --no-pager diff --stat d5028e8 18c4815 -- yolo_ga_runtime/` 결과 **빈 출력** → 스냅샷의 `yolo_ga_runtime/*`는 ACCEPTANCE_RESULTS가 `/2` 해시를 기록한 커밋 d5028e8과 바이트 동일. `git log -- yolo_ga_runtime/`: d5028e8(2026-10-07), 1d3a459(2026-10-06), cbe6831(2026-09-29), 226e5e1(2026-09-29). `git ls-files`에 LICENSE/COPYING/NOTICE 없음(루트 디렉터리 목록에서도 미발견).
- `mixed_hooks.extension_revision()`(4파일 content identity, `mixed_hooks.py:18-27`)를 정적으로 재계산한 값: `7824daaeeb9eae3c74d5ee565490465771226fc7951c83da5b7af069af4b4546` — 서버는 쓰지 않지만 ETRI 캠페인 산출물(`mixed_sampler_state.json`)과의 대조용 기록.
- 출처 표기: `crossover_v22.py:1-15`는 "VERBATIM REUSE … etri_nas/YOLO_GA_MUTATION_v2.2.ipynb"; `runtime.py:1-22`는 "Author: MBNAS phase-1 harness"; `mixed_blocks.py:1-5`는 "Custom window attention is not an implementation of a named published model"(저자 표기 없음). 논문 각주 `_2026_IVC_/main.tex:407` "publicly released upon publication"(DD-A9 기록과 일치).
- ultralytics 8.4.173 wheel METADATA: `License: AGPL-3.0`, `License-File: LICENSE`. `register_ultralytics_blocks`는 설치본 `parse_model` 소스를 읽어 수정·재컴파일해 교체하므로(§4.2) "ultralytics 코드와의 결합/파생" 판단이 필요한 지점이다 — DD-A9 검토 범위에 명시 추가 권고(여기서 법적 결론은 내리지 않는다).

## 8. 환경 변수 의미 (질문 6)

| 변수 | 읽는 곳 | 효과 | 서버 경로 도달 |
|---|---|---|---|
| `ETRI_NAS_MIXED` | `mixed_enabled()`(`mixed_search_space.py:20-22`): 문자열 `"1"`일 때만 True | `mutate_operators`(`:111-112`, False면 deep copy·repair 없음), `mixed_hooks._enabled`(`:30-32`) → `bootstrap_training_process`(`:45-49`), `_revision_identity` fingerprint 접미(`:56-58`), `TorchExecutor.build` audit(`:138-141`), sampler 카탈로그 필터(`:149-151`), sampler mutate(`:158-161`), `install_ga_hooks` mutate/checkpoint/resume(`:202,213,231`) | **없음.** `make_mixed_seed_variants`/`audit_config`/`register_ultralytics_blocks`/`audit_model`은 이 변수를 읽지 않는다. 서버가 `"1"`로 설정하는 것(`etri_bridge.py:130`)은 기능적으로 무효하며, 설정한 프로세스의 자식에게 상속된다(`server/worker/main.py:288-289`). provenance `env`에 기록됨(`etri_bridge.py:68`) |
| `ETRI_NAS_MIXED_IMAGE_SIZE` | `mutate_operators`(`:115`) 기본 `'640'` | `symbolic_shape_propagate`의 입력 크기 | 없음(서버는 protocol `imgsz`를 `SearchSpace.create(imgsz=…)`로 전달) |
| `ETRI_NAS_OPERATOR_RATE` | `mixed_hooks._operator_rate`(`:35-42`) 기본 0.35, [0,1] 검증 | sampler hook의 변이율 | 없음 |
| `MBNAS_ETRI_NAS_ROOT` / `Settings.etri_nas_root` | `etri_bridge.resolve_root`(`:107-109`), `server/config.py:66`(기본 `/root/detection/ETRI_NAS`), child env(`worker/main.py:73`) | import 루트 **및** 사용자 YAML allow-root(`server/api/model_services.py:481-483`, `server/training/selection.py:71-72`) | vendoring 후 import 용도 소멸; allow-root 용도만 남음(결정 D3) |

권고: vendored 코드에는 환경 게이트를 두지 않는다(항상 활성). provenance의 `env` 항목은 `{"gate": null}` 또는 삭제.

## 9. 소비자별 교체 지점

| 파일:행 | 현재 | 교체(제안) |
|---|---|---|
| `server/search/space.py:44,280,307,337,528` | `etri_bridge.load_etri_mixed`, `etri.make_seed_variants`, `etri.audit_model`, `etri.provenance()` | `mbnas_core.search.mixed_pool.bridge.load_mixed_pool()`; 동일 핸들 인터페이스 유지 |
| `server/search/handlers.py:59-72,205-215`, `server/search/generation.py:135-137` | `etri_root` 캐시 키·파라미터, `SearchSpaceUnavailable` | `etri_root` 인자 제거(캐시 키에서 삭제) 또는 무시; 예외 클래스 유지 |
| `server/protocol/validator.py:499-532` | `probe_mixed_pool()`; `search_space_resolved.etri_root = probe["root"]` | probe는 "ultralytics findable + operators import 가능"; `etri_root` 값 정책은 D2 |
| `server/api/routes_meta.py:40-46` | `probe` 필드 `root/reason/checked` | 키 유지, `root: null`, `checked: "vendored operators importable + ultralytics installed"` |
| `server/api/model_services.py:276-284,507-515` | `load_etri_mixed()` | `mbnas_model_runtime.operators.register_ultralytics_blocks()`(또는 `ensure_registered()`) |
| `server/worker/child.py:52,55-68`, `server/training/selection.py:136-139` | `MIXED_HOOK = "mbnas_server.search.etri_bridge:ensure_mixed_registered"` | `"mbnas_model_runtime.operators:ensure_registered"` — **단, `selection.py:308`이 이 문자열을 `resolved.console.extra.training.arch`에 영구 저장**하므로 기존 run의 재개/재시작 시 구 문자열이 등장한다(결정 D5) |
| `server/adapters/base.py:187-195` | `mbnas_server.` 접두사만 허용 | 허용 목록을 `mbnas_model_runtime.operators:` 정확 일치(또는 구/신 두 문자열의 명시적 매핑)로 교체 — 임의 모듈 import 금지 원칙 유지 |
| `server/search/genotype.py:233-236` | `tasks.parse_model._nas_mixed_installed/_nas_mixed_capabilities.parser_source_sha256` | 속성 이름 유지(vendored 모듈이 같은 속성을 설정) |
| `server/search/block_catalog.py:36,39,193-207` | `ETRI_MIXED_SOURCE = "yolo_ga_runtime/mixed_blocks.py"` | `"mbnas_model_runtime/operators/etri_mixed_blocks.py (vendored from yolo_ga_runtime/mixed_blocks.py sha256 4233dd32…)"`; `source_path` 해석은 설치 패키지라 더 이상 null이 아님(`:5-8` 규칙에 따라 lazy 해석 가능) |
| `server/search/structure.py:34-37`, `server/search/audit.py:20-24` | NAS 이름 상수 | 변경 없음 |
| `server/config.py:66`, `server/worker/main.py:73`, `config/server.example.yaml:16`, `deploy/*.service.example`, `README.md`, `scripts/run_tests_host.sh:9` | ETRI 루트 배관 | D3에 따라 제거/축소(WP-7) |
| `MBNAS_web/src/api/types.gen.ts:1061-1066` | `search_space_resolved.etri_root?: string|null` | 계약 유지 가능(nullable). 값 정책은 D2 |

## 10. DD 충돌·보완 플래그 (재논의 아님, 증거 제시)

- **DD-A2 ↔ §2.2/§2.3**: DD-A2는 `_legal_choices`·`mutate_operators`를 core로 vendoring한다고 적었으나, 둘 다 서버 비도달이고 `_legal_choices`는 이미 `space.illegal_reason`으로 이식돼 있다(`RULES_VERSION` 문자열이 그 사실을 기록 `server/search/space.py:53`). `symbolic_shape_propagate` 이식도 불필요. 실행 가능한 최소 범위는 Option A다. 보존 가치(향후 ETRI 호환 mutation 재현)가 있다면 Option B를 별도 WP로 미룰 것을 권고.
- **DD-A3/A4/A5 ↔ §4.3/§9**: 모델 구축·등록(`register_ultralytics_blocks`)은 core 호스트(PROPOSE_GENERATION CPU build, `SearchSpace.inspect`)와 runner 호스트(학습·평가·벤치) **양쪽**에서 실행된다. 즉 private `mbnas_core`가 public `mbnas_model_runtime.operators`에 의존하며(허용 방향), 두 호스트의 operators 바이트가 다르면 파서 의미가 달라진다. runner가 `registration.parser_source_sha256`·`implementation_version`·operators 파일 sha256을 결과 envelope에 보고하고 core가 space의 `mixed_pool_files_sha256`와 대조하는 검사를 WP-2/WP-5 계약에 추가할 것을 권고.
- **DD-A9 ↔ §7**: AGPL 검토 항목에 "`parse_model` 소스 재컴파일 결합" 추가.
- **DD-26(이전 통합 결정, PLAN_20261021 §DD-26) ↔ §6.4**: 읽기 불가 루트 진단은 루트 개념과 함께 소멸한다. "진단 경로가 500을 내지 않는다"는 원칙만 승계.

## 11. 결정 필요 사항

- **D1. vendoring 범위**: Option A(권고) vs Option B(DD-A2 문구 그대로, +310행, 서버 호출자 0). A 선택 시 `_legal_choices`/`mutate_operators`/`symbolic_shape_propagate` 원문은 provenance 사본으로만 보존.
- **D2. `search_space_resolved.etri_root` 값**(protocol hash에 포함): (a) `null`로 고정(계약상 nullable, 기존 run과 hash 불일치는 버전 경계로 수용), (b) `"vendored:mbnas_model_runtime==<ver>"` 같은 식별자(패키지 버전마다 hash가 바뀜), (c) 필드 자체를 `search_space_resolved`에서 제거하고 contract 1.5.0으로 bump. 권고 (a).
- **D3. `Settings.etri_nas_root`/`MBNAS_ETRI_NAS_ROOT`**: import 용도는 소멸. 사용자 YAML allow-root 용도(`model_services.py:481-483`, `selection.py:71-72`, 테스트 `test_registry_structure.py:220-233`)를 유지할지, 범용 `model_yaml_roots` 설정으로 개명할지, 삭제할지.
- **D4. `audit_config`·`_annotate` 보존 여부**: 서버 출력에 영향 없음(§3.4). provenance `functions` 목록 호환만 걸림.
- **D5. `register_hook` 문자열의 하위 호환**: 기존 run의 `resolved.console.extra.training.arch.register_hook == "mbnas_server.search.etri_bridge:ensure_mixed_registered"`를 (a) `resolve_register_hook`에서 신 대상으로 **명시 매핑**(허용 목록 2개), (b) 구 run 재개 불가로 선언. 권고 (a) + 매핑 테이블을 contracts에 기록.
- **D6. `describe()["etri"]` 키 이름**: 유지(테스트·문서 최소 변경) vs `mixed_pool`로 개명(의미 정확). 어느 쪽이든 `files_sha256`/`registration` 하위 키는 유지 권고.
- **D7. `/3` 해시 대상 파일 집합**: 3개(권고) vs bridge 포함 4개 vs `mbnas_model_runtime/operators` 디렉터리 전체. 또한 설치 파일 바이트가 아니라 **패키지 빌드 시 고정한 상수 해시**를 쓸지(zip/pyc 배포 내성 ↑, "실행 코드 = 해시 대상" 보장 ↓).
- **D8. 과거 ultralytics 포맷 `.pt`(`yolo_ga_runtime.mixed_blocks.*` 피클) 호환**: 미지원 선언(권고; 해당 파일 존재 여부 확인 못 함) vs 호환 alias 모듈 제공(공개 패키지에 `yolo_ga_runtime` 이름을 되살리는 셈이라 비권고).
- **D9. ultralytics 버전 상승 정책**: §4.2 정적 증명 스크립트를 CI 게이트로 채택할지(버전 변경 PR에서 `base_modules`/`repeat_modules` 패치 가능성 검증).

## 12. 확인 못 함 (실행 없이는 확정 불가)

- `inspect.getsource(parse_model)`가 정확히 `tasks.py:2030-2243`을 돌려주는지(→ `parser_source_sha256 = 7da6bb14…`의 실측 일치).
- 스냅샷 콘텐츠에서의 실제 `/2` `space_hash`/`prior_hash` 전체값(문서에 앞 12자리만 있음).
- `make_mixed_seed_variants`의 실행 결과가 §3.4 정적 유도와 동일한지(단, 기존 테스트 `test_space_genotype.py:121-126`이 통과했다는 ACCEPTANCE 기록과 부합).
- registry/weights 경로에 `yolo_ga_runtime.mixed_blocks.*`를 피클한 ultralytics 포맷 `.pt`가 존재하는지.
- 웹 `SearchSpaceState`가 `root: null`을 올바르게 표시하는지.
- `describe()` 전체가 다른 경로(예: 아카이브, 디버그 엔드포인트)로 노출되는지(grep 범위에서는 lineage 생성에만 사용).

## 부록 A. 정적 계산 스크립트(실행 완료, 출력은 본문에 반영)

- `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/a7_static_sets.py` — `runtime.py`/`mixed_hooks.py`/`mixed_search_space.py`의 집합 리터럴을 `ast.literal_eval`로 읽고 `install_runtime_hooks`의 `update` 호출(L69-75)을 그대로 적용해 유효 집합을 계산; 스냅샷 10파일 sha256을 manifest와 대조; `extension_revision` 재계산; 행 범위별 sha256 앞 16자리(vendoring 후 "원문 동일" 검증용: `mixed_search_space.py L154-203 = f8dc3c3f624c6ad1`, `L25-55 = ef1bad3b0ef41ffd`, `L12-17 = 275cebfa31cb8c47`, `L58-62 = 2b37a64afbbea361`).
- `/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/a7_parser_patch_proof.py` — wheel에서 추출한 `tasks.py`에 `_extend_parser_source` 원문(이름 치환만)을 적용해 패치 가능성·결과 집합 크기·컴파일을 확인.
- wheel 추출물(읽기 전용 사본): `.../scratchpad/ul_wheel/ultralytics__nn__tasks.py`(sha256 `c2465c97a13a1e3bb0ebd5ec7c531aa912122d59281e61677af376411c7d448a`), `ultralytics____init__.py`(`__version__ = "8.4.173"`), `yolo11.yaml`, `yolo26.yaml`.

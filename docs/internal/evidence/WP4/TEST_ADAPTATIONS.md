# WP-4 test adaptations (recorded in fix round 1, 2026-10-28 KST)

Rule (as in WP-1/WP-3): no assertion weakened, no silent skip, no policy stub. Every change names file:line, the reason
and the approval. Part A is the WP-4B/4C change that had no record (C03 EVAL_1 D1); part B lists the test changes of fix
round 1 (`docs/internal/FIX_ROUND_1.md`), whose evidence is in `docs/internal/evidence/WP7/FIX1/`.

## A. Contract 1.5.0 adaptation, approved post hoc (DD-A57)

| file | line | change | reason | approval |
|---|---|---|---|---|
| `tests/owner/search/test_repair_evidence_seed.py` | 18 (import), 61-66 (snapshot original: line 58) | The original `jsonschema.validate(blk, block_schema)` (the STORED repaired block validated against the contract `Block`) became `public = project_public("Block", fold_block(blk, row["parents"]))` + `jsonschema.validate(public, block_schema)`, plus two NEW assertions: the public block's `source_kind` is folded into `parent_a`/`parent_b`/`random` with `name == "SPPF"`, and none of `before`/`after`/`old_name`/`repair_reason` is in the public block. | Contract 1.5.0 closed `Block` (`additionalProperties: false`): the repair trace is owner-only (DD-A19 D1, DD-A42) and the stored row keeps it, so the stored row no longer validates against the public `Block` (failure recorded in `docs/internal/evidence/WP5/5B/owner_gate.txt:12769,23546`). WP-4B/4C made the edit on 2026-10-27 16:17 KST without a record. | DD-A57 |

Unchanged in that file (the seed-116 repair evidence it exists for): repair event row 9 / violation row 10 /
"C2PSA requires c1 == c2", before `C3TR` -> after `SPPF`, exactly one stored block with `source_kind == "repair"` at
position 9 named `SPPF` with a C2PSA `repair_reason` and before != after, no `repair_events` in the lineage. The
behaviour itself is also checked independently: T2-4 (seed 116, GA and IR, row 9 C3TR -> SPPF) EXACT_MATCH against the
original package (`docs/internal/evidence/WP4/4C/t2/T2_REPORT.md`; re-run in C03 EVAL_1).

## B. Fix round 1 (C03 / C04 / C06 EVAL_1)

New test files (no existing assertion touched):

| file | item | asserts |
|---|---|---|
| `tests/owner/search/test_generation_recovery_independent_journal.py` | C04-D1 | `mbnas_core/search/generation.py` journal guards: a journal of another `protocol_hash`, another `run_id`, or a corrupt journal -> `JobFailure GENERATION_STATE_INVALID` (not retryable), no notification, journal bytes unchanged, no space instantiated; a held `generation-<seed>.lock` -> `GENERATION_BUSY` with `retryable is True`, no journal created; the same request succeeds once the lock is released (control). Line trace: generation.py 107-109 and 113-118 executed. |
| `tests/owner/api/test_generation_real_cpu_training_status.py` | C03-D3 | real evaluation-OFF run (gateway relay + real owner worker on CPU): every `generated_candidates[].training_status == "UNTRAINED"` (the string `"TRAINED"` occurs nowhere in the served rows), every candidate detail `training_status == "UNTRAINED"` with `evaluations == []`, Snapshot/CandidateDetail contract-valid, jobs = `[PROPOSE_GENERATION]`, 0 evaluations. |
| `tests/owner/search/test_ga_same_family_crossover.py` | C03-D4 | `mbnas_core/search/ga.py` 126 / 141-142 on the real shared space: in a mixed yolo11n + yolo26n pool every crossover pairs two parents of one family (A != B, child family = parent family, parent-B contenders only same-family rows) and both families occur as parent A; with a lone-family row, a drawn crossover degrades to a clone of A with the note "crossover drawn but no same-family partner in the pool; degraded to clone" and no parent B, while the other family still crosses over. |
| `tests/owner/orchestration/test_semantics_baseline_ungated.py` | C03-D5 | under a gate that rejects every search candidate (`score_ge=0.99`, control: all GA candidates `REJECTED_BY_GATE`) the baseline is still trained and evaluated at both stages, every Baseline evaluation row has `gate` None (stored; served: absent/null while the GA rows of the same snapshot carry their gate) and a fitness value, the candidate ends `EVALUATED` (no promotion/gate state), Baseline ledger rows `billed_arm` None. Line trace: the gate branch of `commit_evaluation` (`gate = None`; only `SEARCH_ARMS` call `evaluate_gate`) and the Baseline branch of `_advance` executed. These were `engine.py` 577-578 and 700-704 when this row was written; later engine edits (DD-A62/A63 NOT_EVALUATED commit, DD-A66 prune round trip) moved them to `engine.py:636-638` and `:872-877` (source tree `01c7d785…`, 2026-10-28 round 4, C03 EVAL_2 D7). |

Changed test files:

| file | line | change | reason |
|---|---|---|---|
| `tests/owner/projection/test_value_rules.py` | 6-8 (docstring), 96-103 | Worker id rule strengthened: was `fullmatch((wrk-[0-9a-f]{8,64}\|[A-Za-z0-9._-]+))`, now every public worker id must fullmatch `wrk-[0-9a-f]{8,64}\|runner-[0-9a-f]{8,}` (the designer's `^runner-[0-9a-f]{8,}$\|^wrk-` with the existing `wrk-` hex rule kept), every non-null `runner_id` must fullmatch `runner-[0-9a-f]{8,}`, and the world's default-id runner must be among both sets (no vacuous pass). | C06-D1: the old alternative accepted `runner-<hostname>`, and `HOST_RE`'s lookbehind does not match a host name after `runner-`. Stronger only. |
| `tests/owner/projection/projection_world.py` | 18-21 (docstring item 5), 440 (`World.runner_id`), 911-939 (`default_runner_id`, `_attach_default_runner`), 975-978, 1000, 1021 | The runner principal of the world is bound to the DEFAULT runner id of `mbnas_runner.config.RunnerSettings` (resolved with `MBNAS_HOME=<world>/runner_runtime`; was the fixed id `canary-runner`), and that runner registers through the real E1 route and is published busy on one of the run's training jobs by `runner_api.routes._publish` (workers row + `worker.updated` event), before `last_seq` is read. | C06-D1: the world never contained a runner row, so a host-derived default id could not be caught. With the old default the strengthened rule FAILS on `runner-dev1` (this host's name) on both sides (`WP7/FIX1/c06d1_repro_before_fix.txt`); with the fix it passes (`WP7/FIX1/c06d1_after_fix.txt`). |
| `tests/owner/unit/test_settings_split.py` | 5 (`import re`), 52-56 | `assert s.runner_id == f"runner-{socket.gethostname()}"` -> the default is the generated form `runner-[0-9a-f]{8}` and not `runner-<hostname>`, the same id is stored in `MBNAS_HOME/runner/runner_id`, and a second `RunnerSettings()` returns it. | C06-D1 changed the default under test (FIX_ROUND_1: random 8 hex persisted in `runtime/runner/runner_id`); the new assertions are stricter (format, persistence, stability). |

# T4 re-run on the FINAL owner ZIP (DD-A59…A63 fixes) — agent P2, 2026-10-28 07:4x–07:5x KST

**Overall: PASS.** The strict sandbox (no GPU device nodes at all, loopback only, original tree invisible) now runs the
whole owner flow through the gateway: install → doctor → start (core, owner worker, gateway, runner) → web console →
structure checks → OFF generation, including a download of the OFF candidate's architecture artifact → CPU training with
pause → full-state resume → downloads with sha256 match → export → explicit training with validation ON, which ends
`NOT_EVALUATED` / `EVALUATION_REQUIRES_CUDA` and keeps its checkpoint → the evaluation-ON search is refused at validate with
`EVALUATION_REQUIRES_CUDA` → stop. Two device sources were run: the default `local`, which falls back to the CPU runner
entry, and `runners`. Request ids now link one gateway access line to one core access line, for 153 requests.

All checks here were run on the final ZIP. The first-run evidence (`README.md`, `run/`, `logs/`, `debug/`) belongs to the
earlier ZIP `ba8ed91f…` and is kept unchanged for history. `<ROOT>` = TARGET_ROOT and `<T>` = `<ROOT>/runtime/test_root/t4t5`.
Host paths in this folder are replaced by placeholders, and the folder was leak-scanned clean (`rerun/evidence_scan.txt`).

## Inputs

| item | value |
|---|---|
| owner ZIP | `dist/owner/mbnas_application-0.1.0-owner-private-runtime-linux_x86_64.zip`, sha256 **`79cce02a8d36eec17a24a5d43228b02fa32832ff7ca9499a9035c3c0f56d9c20`** (mtime 07:06:56 KST), copied to `<T>/final/zips` (`rerun/zips_sha256_used.txt`) |
| extraction | fresh `<T>/final/t4`: only this ZIP plus a copy of `examples/synthetic4` (byte-identical to the ZIP's copy) |
| sandbox | `rerun/harness/sbx.sh` (same launcher as run 1, `SBX_NVML=0`). `rerun/isolation_proof.txt`: network `lo` only, outbound https fails (curl exit 6). Not visible inside: `<ROOT>/pyproject.toml`, `<ROOT>/build`, the run-1 tree `<T>/t4f`, `etri_nas`, `/data`, `~/.ssh`. No `/dev/nvidia*` |
| install | `bash mbnas_application/scripts/install.sh --profile owner --offline --wheelhouse /opt/wheelhouse --python <PY310_ENV>/bin/python3.10` → **exit 0**, 162 s. The runner principal is issued for the random id **`runner-d3c27c94`**, stored in `runtime/runner/runner_id`; `runner.env` has no `MBNAS_RUNNER_RUNNER_ID` line (DD-A61 b) |
| per variant | `rerun/harness/t4f_inside.sh`: `doctor.sh` → `start.sh --profile owner --wait-s 120` → `t45f.py` → `stop.sh`. Overrides: `MBNAS_RUNNER_ARTIFACT_TRANSFER=local`, store = gateway store, `MBNAS_CORE_HEARTBEAT_S=2`, protocol `console.benchmark.device=cpu`. The second variant adds `MBNAS_CORE_DEVICE_SOURCE=runners` |

| step | variant `local` (default) | variant `runners` |
|---|---|---|
| doctor | exit 0 | exit 0 |
| start (core :8700, owner worker, gateway :8710, runner) | exit 0 | exit 0 |
| harness `t45f.py` | **exit 0**, 11/11 items | **exit 0**, 6/6 items |
| stop | exit 0; afterwards only the bwrap init process remains | exit 0; same |

## Results (request ids in `rerun/run/t4f_*_steps.jsonl`)

| check | local | runners | key facts |
|---|---|---|---|
| health: no token → 401; token → 200, core reachable | PASS | PASS | owner all-in-one: gateway allowlists `{dataset_roots: [], output_roots: [], archive_roots: []}`, its own (empty) roots |
| device inventory on a host with no NVIDIA device nodes | PASS (`[cpu]` already present) | PASS (`[cpu]` after 24.5 s, within the documented 30 s cache) | DD-A62 b / A63 b fallback |
| web `/`: index sha `08772e74…`; `/assets/index-CY9ctObG.js` 529 623 B sha `6c3f29a2…` (= final dist); css; SPA fallback | PASS | — | byte-identical to the ZIP's `mbnas_web/dist` |
| structure check: `yolo11n_user.yaml` → PASS; `yolo11n_user_broken.yaml` → FAIL (`SHAPE_MISMATCH`, `MERGE_INPUT_MISMATCH`, row 12) | PASS | — | `t4f_local-3ead9f05…`, `t4f_local-686ce2c1…` |
| OFF generation (CPU) → COMPLETED, 2 candidates UNTRAINED / NOT_EVALUATED | PASS `run-4593fab66b6e` | PASS `run-04cc64e706fd` | — |
| OFF candidate architecture artifact through the gateway: 985 B, sha `6999dc2b…` (body == header == listed), `X-Artifact-Verification: core` | PASS `art-1df03be8…` | PASS `art-965049a3…` | — |
| explicit CPU training, 64 px, 2 epochs, validation off, pause during epoch 1 → `PAUSED` (`PAUSED_AT_EPOCH`, full state e1) → resume → COMPLETED. Attempt 2 has `resumed_from=full_e0001.pt`, `device_used=cpu`, lease owner `runner-d3c27c94` | PASS `train-315bd567a05f` | — | pause `t4f_local-75d35aae…`, resume `t4f_local-6ee91f4d…` |
| result rows through the gateway: detail `training_status` TRAINED, 4 checkpoints | PASS | — | — |
| downloads, body sha == `X-Artifact-Sha256` == listed (runner bytes are in the gateway store) | PASS | — | runner artifact 73 072 B `b7bae762…` (NOT_READY → READY); core architecture 975 B `0e47ec6b…`; `training_full` e2 31 600 417 B `baa32171…`; `weights_only` e2 5 360 396 B `14f7f122…` |
| export zip | PASS | — | 23 211 B, sha `92727ba9…` == `X-Bundle-Sha256`, 21 members, `SHA256SUMS` verified (`t4f_local-85058480…`) |
| **explicit CPU training with validation ON** → run COMPLETED, job SUCCEEDED. The row is `SEARCH_COMPLETE` + `performance_status NOT_EVALUATED` + `reason_code EVALUATION_REQUIRES_CUDA`; ap/fitness null, latency `pending`, `device_used=cpu`; checkpoint kept (`full_e0002.pt`, 4 checkpoints). Checkpoint download 31 600 801 B sha `e73bfb65…` matches | PASS `train-5537eea8be71` | PASS `train-e04ffc38bb5f` | — |
| **decision step**: evaluation-ON search → `POST /protocols/validate` returns `valid: false`, error `EVALUATION_REQUIRES_CUDA` on `console.search.evaluation_enabled` ("…the registered device inventory is CPU-only; set evaluation_enabled=false…, use explicit training, or register a GPU runner") | **PASS-by-design** (`t4f_local-908b6e96…`) | **PASS-by-design** (`t4f_runners-0c044969…`) | No promotion or final retrain on CPU, by design (DD-A32/A60/A62) |
| request ids: 171 harness ids, 171/171 echoed | PASS | PASS | `rerun/run/t4f_request_id_linkage.json` |
| access-log linkage: harness ids with **exactly one** `mbnas_server.access` line and one `mbnas_core.access` line carrying the same `request_id` | **153** | (included) | Example: `GET /api/v1/runs/train-315bd567a05f … request_id=t4f_local-0091c025…` ↔ `GET /core/v1/console/runs/train-315bd567a05f … request_id=t4f_local-0091c025…`. 9 ids have 1 gateway line and 2 core lines: artifact and export downloads, where the gateway makes two core calls with the same id (`…/record` + bytes). 9 ids are gateway-only (static web, no-token health, uploads: no core call) |

## Compared with run 1

| run-1 finding | final ZIP |
|---|---|
| F1: strict sandbox → training/search `422 DEVICE_UNAVAILABLE` | fixed: the CPU runner `cpu` entry is used (`local` fallback and `runners`) |
| F2/F3: CPU evaluation crashes; no decision step | fixed by design: validation-ON training gives an honest `NOT_EVALUATED` / `EVALUATION_REQUIRES_CUDA` with the checkpoint kept; the evaluation-ON search is refused at validate |
| F4: no request id shared by gateway and core logs | fixed: one access line per request on each side, same id (153 ids) |

Not re-run on the final ZIP: the NVML-node variant B of run 1. It was not needed: strict variant A now passes, and leaving it out keeps the sandbox free of GPU device nodes.

## Files (`rerun/`)

- `install_owner_offline.txt`, `isolation_proof.txt`, `t4f_local_console.txt`, `t4f_runners_console.txt`, `zip*_sha256*`
- `run/`: harness output per variant — steps, http logs, request ids, gen / train / trainval candidate details and snapshot rows, doctor/start/stop output, export sha, and `t4f_request_id_linkage.json`
- `logs/`: the sandbox's core / gateway / owner-worker / runner / install logs (access lines included) and `audit.jsonl`
- `harness/`: `sbx.sh`, `t4f_inside.sh`, `t45f.py`, `reqid_link2.py`, `copy_redacted.py`

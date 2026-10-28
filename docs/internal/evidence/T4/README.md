# T4 — owner independent run (WP-8 §1 row T4, C11 `real_core_runner`, DD-A31/A38/A46) — agent P2, 2026-10-28 KST

**Overall: PARTIAL.** In an isolated bwrap sandbox built from the owner ZIP alone, install → doctor → start (core, owner
worker, gateway, runner) → web console, valid/invalid structure checks, OFF generation (UNTRAINED candidates), CPU
training with pause → full-state resume → finish, artifact + checkpoint downloads with sha256 match, export zip and stop all
**PASS** — but only in variant B, where the sandbox exposes the NVML device nodes (no `/dev/nvidia-uvm`, CUDA hidden)
so that the core's protocol validator sees a device inventory. In the strict variant A (no GPU device nodes at all) every
explicit training / search request is refused with `422 DEVICE_UNAVAILABLE` (**FAIL**: a GPU-less owner host cannot run
the DD-A32 CPU training path). The required decision step (promotion or final retrain) is **FAIL** on CPU in both
variants. Request ids are echoed and propagated to the core, but no gateway log record and core log record share an id
(**FAIL** for log linkage).

`<ROOT>` = TARGET_ROOT, `<T>` = `<ROOT>/runtime/test_root/t4t5` (scratch), all evidence text has host paths replaced
(`harness/copy_redacted.py`) and was scanned clean (`evidence_scan.txt`: canary + 14 secret needles + host facts, 0 hits).

## Inputs

| item | value |
|---|---|
| owner ZIP | `dist/owner/mbnas_application-0.1.0-owner-private-runtime-linux_x86_64.zip`, sha256 `ba8ed91f601330e1272bc89fd217bba81fbc5ee943cfb8f45f353795da787df3` (mtime 2026-10-28 03:04:23 KST, after this run's start 03:02:23; copied to `<T>/zips` at 03:05, `zips_sha256_used.txt`; still the current `dist/owner` file at report time) |
| public / registration ZIPs at the same snapshot | `4174060dde93ce8a…` / `d5748c136086081…` (T5 uses the public one) |
| extraction | `unzip` into a fresh `<T>/t4f` (only the ZIP + a copy of `examples/synthetic4` in `<T>/t4f/data`) |
| wheelhouse | `<ROOT>/build/wheelhouse` mounted read-only at `/opt/wheelhouse` (offline mirror, DD-A31 `--offline`) |
| Python | base interpreter `<PY310_ENV>/bin/python3.10` (3.10.22) mounted read-only (venv base) |

## Isolation (`harness/sbx.sh`, proof `isolation_proof.txt`)

`env -i bwrap --die-with-parent --unshare-net --unshare-pid --unshare-ipc --unshare-uts`, read-only `/usr` `/etc`, `--dev
/dev`, tmpfs `/tmp`; only `<T>/t4f` (rw, same absolute path), `/opt/wheelhouse` and `/opt/harness` (ro) are visible.
Measured inside: network = `lo` only (outbound https → curl exit 6), `<ROOT>/pyproject.toml`, `<ROOT>/build`, the original
`etri_nas`, `/data`, `~/.ssh` **not visible**; `<ROOT>` contains only `runtime/test_root/t4t5`. Environment is cleared
(`CUDA_VISIBLE_DEVICES=""`, `PIP_CONFIG_FILE=/dev/null`, `PYTHONDONTWRITEBYTECODE=1`, `YOLO_OFFLINE=1`, `HF_HUB_OFFLINE=1`).
Variant A (`SBX_NVML=0`): no `/dev/nvidia*`. Variant B (`SBX_NVML=1`): `/dev/nvidiactl`, `/dev/nvidia0`, `/dev/nvidia1`
only (NVML inventory reads); `/dev/nvidia-uvm` is **not** bound, so a CUDA context cannot be created (torch reports
`CUDA unknown error`, `cuda.is_available() False`) — no GPU compute was possible or used.

## Commands and exit codes

| # | command (inside the sandbox unless noted) | exit |
|---|---|---|
| 1 | host: `unzip -q <T>/zips/…owner…zip` in `<T>/t4f`; `sha256sum` (`zip_owner_used.sha256`) | 0 |
| 2 | `bash mbnas_application/scripts/install.sh --profile owner --offline --wheelhouse /opt/wheelhouse --python <PY310_ENV>/bin/python3.10` (`install_owner_offline.txt`, 105 s; writes `config/owner/*.env` 0600, issues server + runner principals) | 0 |
| 3 | `harness/t4_inside.sh <items> --pause` = `doctor.sh --profile owner` → `start.sh --profile owner --wait-s 120` → `t45.py` → `stop.sh --profile owner` (consoles `t4_strict_console.txt`, `t4_nvml_console.txt`) | see below |
| 3a | doctor (A / B) — `run/t4_*_doctor_owner.txt`: PASS=25 WARN=2 FAIL=0 (WARN = core not yet running) | 0 / 0 |
| 3b | start (A / B) — core :8700, owner worker, gateway :8710 (core reachable, web_dist true), runner `runner-t4-sandbox` (CPU) | 0 / 0 |
| 3c | harness (A / B) — `run/t4_strict_steps.jsonl`, `run/t4_nvml_steps.jsonl` | 1 / 1 (FAIL items below) |
| 3d | stop (A / B) — runner, gateway, owner worker, core stopped by SIGTERM; process list after stop = only the bwrap init | 0 / 0 |

Environment overrides given to `start.sh` (operator settings, logged in the consoles): `MBNAS_RUNNER_ARTIFACT_TRANSFER=local`,
`MBNAS_RUNNER_ARTIFACT_STORE_DIR=<ROOT>/runtime/gateway/artifacts` (= the gateway store), `MBNAS_CORE_HEARTBEAT_S=2` (so a
pause reaches the runner within one CPU epoch); variant A also `MBNAS_CORE_DEVICE_SOURCE=runners` + protocol
`console.benchmark.device=runner-t4-sandbox` (documented option for a core without GPU access; still refused, see F1).
Everything else is the install default (`config/owner/*.env` as written by install.sh). Dataset:
`runtime/data/synthetic4` (copy of `examples/synthetic4`, 4 images per split, 1 class).

## Results

| check | A strict (no GPU nodes) | B NVML nodes only | evidence (request ids in `run/*_steps.jsonl`) |
|---|---|---|---|
| health: no token 401, token 200, core reachable | PASS | PASS | `t4_nvml-99cbc971…`, `t4_nvml-3b2f54fe…` |
| web console at `/` (index 733 B sha `9bdba151…` == `mbnas_web/dist/index.html`), `/assets/index-DlYSES30.js` (sha `074a3d8b…`) + css byte-identical, `immutable` cache header, SPA fallback | PASS | PASS | `t4_nvml-995c8b35…` |
| valid YAML `examples/models/yolo11n_user.yaml` (sha `ecb6c44b…`): upload 200 (sha match) → structure-check PASS | PASS | PASS | `t4_nvml-b0439b07…`, `t4_nvml-2db5a9c1…` |
| invalid YAML `yolo11n_user_broken.yaml` (sha `5e29b7d9…`): structure-check FAIL with `SHAPE_MISMATCH` + `MERGE_INPUT_MISMATCH` at row 12 `head[1]` | PASS | PASS | `t4_nvml-a0251c73…`, `t4_nvml-0c532f64…` |
| OFF generation-only run (CPU, `examples/protocol.cpu_generation_only.json`, output_root `<ROOT>/runtime/core/runs/t4`) → COMPLETED, 2 candidates, `training_status` UNTRAINED ×2, `evaluation_status` NOT_EVALUATED, candidate detail 200 | PASS (`run-42a97dc60284`) | PASS (`run-e71e06ac84b6`) | `t4_nvml-232c1891…` (create) |
| explicit CPU training of the uploaded YAML, 64 px, 2 epochs, validation off | **FAIL**: `422 TRAINING_PROTOCOL_INVALID` / `DEVICE_UNAVAILABLE` (`t4_strict-4ddfacc4…`) | PASS `train-72922aaec5b8` | `t4_nvml-b764dc48…` |
| pause during epoch 1 → `PAUSED` (`PAUSED_AT_EPOCH`, full state e1) → resume → COMPLETED, job attempt 2 `resumed_from=full_e0001.pt`, epochs_done 2, `device_used=cpu` | not reachable | PASS | pause `t4_nvml-a6e725b7…`, resume `t4_nvml-37f0fe37…` |
| result rows through the gateway (run, snapshot, candidate detail: `training_status` TRAINED, 9 artifacts, 4 checkpoints) | not reachable | PASS | `t4_nvml-e57b5c24…`, `t4_nvml-02abcb3e…`, `t4_nvml-f5341f6b…` |
| runner-produced artifact download (local transfer into the gateway store): 73 072 B, sha256 `b7bae762…` == `X-Artifact-Sha256` == listed, bytes in store, state NOT_READY → READY | not reachable | PASS | `t4_nvml-35b4eead…`, `t4_nvml-09f295e1…` |
| core artifact (architecture YAML) download: 975 B sha `0e47ec6b…` matches | not reachable | PASS | `t4_nvml-069ba003…` |
| checkpoint downloads: `training_full` e2 31 600 417 B sha `52cebde6…`; `weights_only` e2 5 360 396 B sha `14f7f122…` (header == listed == body, in store) | not reachable | PASS | `t4_nvml-98d1c2c8…`, `t4_nvml-e6707e0c…` |
| export zip of the training run: 23 314 B, sha256 `ace399d3…` == `X-Bundle-Sha256`, 21 members, `SHA256SUMS` verified | not reachable | PASS | `t4_nvml-ffd47866…` |
| decision step (promotion or final retrain): CPU search run (GA, pop 2, 1 rung, evaluation ON) → run FAILED `NO_SUCCESSFUL_EVALUATION`, both candidates `EVAL_FAILED`; `POST /final-retrain` → `409 INVALID_STATE` | **FAIL** (validate `DEVICE_UNAVAILABLE`) | **FAIL** (see F3) | `t4_nvml-b7d06560…`, `t4_nvml-f2abd37a…` |
| request ids: every response echoed `X-Request-ID` (84/84); core error bodies carry the same id (`t4_strict-4ddfacc4…`, `t4_nvml-f2abd37a…`); gateway audit log 5 ids; core DB run-log events 4 ids (run creation); **0 ids shared by a gateway log record and a core log record** (core/gateway/owner/runner logs contain no request id) | — | **FAIL** (linkage) | `run/t4_request_id_linkage.json` |
| stop: `stop.sh` exit 0, no process left | PASS | PASS | consoles |

## Findings

- **F1 (FAIL, product)** — explicit training and search validation need a live device: `validator.device()` resolves
  `console.benchmark.device` against NVML (`MBNAS_CORE_DEVICE_SOURCE=local`) or against registered runners with a
  `device_uuid` (`runners`); a CPU runner registers `device_uuid = NULL` and is excluded (`devices_from_runners`), so on a
  host without NVIDIA devices `POST /training-runs` and search validation always answer `DEVICE_UNAVAILABLE` (available:
  []). DD-A32's CPU path (train/checkpoint) is therefore unusable on a GPU-less owner host. Both default and `runners`
  device source were tried (`debug/t4_try2.txt`, `debug/t4_try3.txt`, `t4_strict_console.txt`).
- **F2 (sandbox artifact, not a product verdict)** — in variant B the evaluation step of CANDIDATE_TRAIN fails with
  `RuntimeError: CUDA unknown error` because NVML nodes exist without `/dev/nvidia-uvm`. The clean CPU behaviour of the
  same probe was measured in T5 (`AssertionError: Torch not compiled with CUDA enabled`, also EVAL_FAILED).
- **F3 (FAIL, by design DD-A32)** — no promotion / final-retrain decision can be produced on CPU: evaluation latency needs
  CUDA, the fitness is invalid, the gate never runs, the search run ends `FAILED NO_SUCCESSFUL_EVALUATION`, and
  final retraining requires a COMPLETED search run with measured latency. Requires the GPU track (T7).
- **F4 (FAIL, observability)** — the core logs no request id for successful requests and the gateway logs only failures;
  the audit log records sensitive reads only. End-to-end linkage exists only through the core DB run-log events of
  run-creating requests and error bodies (`run/t4_request_id_linkage.json`).

## Files

`install_owner_offline.txt`, `isolation_proof.txt`, `t4_strict_console.txt`, `t4_nvml_console.txt`, `zip*_sha256*`;
`run/` = harness output per variant (`*_steps.jsonl` one JSON per step with UTC/request ids/status, `*_http.log` every
request, `*_request_ids.jsonl`, candidate details, decision snapshot, doctor/start/stop output, export sha);
`logs/` = the sandbox's core/gateway/owner-worker/runner/install logs and gateway `audit.jsonl` (both variants appended);
`harness/` = `sbx.sh`, `t4_inside.sh`, `t45.py` (stdlib client, `--pause`), `reqid_link.py`, `copy_redacted.py`;
`debug/` = the earlier attempts on a first extraction of the same ZIP (`<T>/t4`): try1 gen candidates read from a 404
archive route (harness bug, fixed to the snapshot), try2/try3 `DEVICE_UNAVAILABLE` (F1), try4 env not passed into the
sandbox (harness bug), try5 first variant-B pass; `evidence_scan.txt` = leak scan of this folder.

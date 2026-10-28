# T5 re-run on the FINAL public ZIP (DD-A59…A63 fixes) — agent P2, 2026-10-28 07:5x–08:0x KST

**Overall: PASS.** Setup: a CPU public image rebuilt from the final public ZIP alone (`--no-cache`, online); gateway and
runner containers (`--rm --init --network host`); an owner core on the host from the final owner tree; and a
**customer-only output root** (`/srv/mbnas/runtime/outputs`, which exists only inside the containers). Every step
passes: upload → structure check → OFF generation COMPLETED, with the OFF architecture artifact served `core_local`
through the gateway → user-YAML CPU training (pause → full-state resume) → result rows → web `/` → downloads and export
with sha256 match → validation-ON training `NOT_EVALUATED` / `EVALUATION_REQUIRES_CUDA` with the checkpoint kept →
evaluation-ON search refused at validate (PASS-by-design) → core stopped ⇒ `degraded` + `503 CORE_UNAVAILABLE`.
`mbnas_core` is absent from both containers, and there are 0 secret / canary / host-fact hits in env, image config,
history or the 14 layers. The runner id is a random `runner-0a6ab6db`, the gateway health shows only its own roots, and
49 requests link one gateway access line to one core access line.

All results here are from the final ZIPs. The first-run evidence (`README.md`, `run/`, `logs/`, `leakscan/`) belongs to
the earlier ZIP `4174060d…` and is kept unchanged. `<T>` = `<ROOT>/runtime/test_root/t4t5`; host paths are replaced by
placeholders, and this folder was leak-scanned clean (`rerun/leakscan/evidence_scan.txt`).

## Inputs and topology

| item | value |
|---|---|
| public ZIP (the only input on the customer side) | sha256 **`ce34b821806a27cd2b2ff461d8020e85e0350d846d9fae6188c15f0d09bc2a2b`** (mtime 07:05:54 KST), copied to `<T>/final/zips`, extracted fresh into `<T>/final/t5` |
| image | `DOCKER_BUILDKIT=0 docker build --no-cache -f deploy/public/Dockerfile.cpu -t mbnas-public-cpu:final .` in the extracted root → **exit 0, 247 s, 0 cache hits**, online (PyPI + download.pytorch.org/whl/cpu). **id `sha256:403a1b435390fa2951a36bef01d33b7e6657461376bc36eecfde49cf1c98d8d1`, 2 972 583 681 B**, 14 layers, user 10001:10001 (`rerun/docker_build_nocache.txt`, `rerun/image_info.txt`). A first cached build of the same context (9 s, `6700da22…`, third-party layers from run 1; `rerun/docker_build.txt`) was superseded by this one and was not used |
| owner core (host) | final owner tree `<T>/final/t4` (its venv). `mbnas-core-api --host 127.0.0.1 --port 18750` + `mbnas-core-owner-worker --owner`, own `MBNAS_HOME=<T>/final/t5/core_home` (own DB, generated keys, 0600). `MBNAS_CORE_DEVICE_SOURCE=runners` (split deployment, runners on the customer side; no NVML read on this host). Core-only dataset root `<CH>/core_only_data`; `MBNAS_CORE_OUTPUT_ROOTS` unset (default = core-local runs dir); `MBNAS_CORE_HEARTBEAT_S=2`; `CUDA_VISIBLE_DEVICES=""` |
| runner id (DD-A61 b) | `MBNAS_RUNNER_RUNNER_ID` is not set anywhere. The image created it in the mounted runtime: `docker run --rm … --entrypoint python mbnas-public-cpu:final -c "from mbnas_runner.config import persistent_runner_id; print(persistent_runner_id())"` → **`runner-0a6ab6db`** (`runtime/runner/runner_id`). The core operator then issued `mbnas-core-principal create --kind server` and `--kind runner --runner-id runner-0a6ab6db`; tokens went file-to-file and were never printed |
| containers | `t5f-gw`: `docker run -d --rm --init --network host --user <uid>:<gid> --env-file gateway.env -e MBNAS_HOME=/srv/mbnas/runtime -e MBNAS_GW_HOST=127.0.0.1 -e MBNAS_GW_PORT=18760 -e MBNAS_GW_WEB_DIST=/opt/mbnas/web/dist -e MBNAS_GW_DATASET_ROOTS=/srv/mbnas/data -e MBNAS_GW_ARTIFACT_STORE=/srv/mbnas/runtime/gateway/artifacts -v run/runtime:/srv/mbnas/runtime -v run/data:/srv/mbnas/data:ro mbnas-public-cpu:final gateway`, with `gateway.env` `MBNAS_GW_OUTPUT_ROOTS=["/srv/mbnas/runtime/outputs"]`. `t5f-runner`: same image, `runner`, `--shm-size 2g`, `runner.env` (core URL + token only), local transfer into the gateway store, `CUDA_VISIBLE_DEVICES=` |
| client | `rerun/harness/t45f.py` (stdlib) with `--output-root /srv/mbnas/runtime/outputs --split-paths --bench-device cpu`; dataset = the public ZIP's `examples/synthetic4` at `/srv/mbnas/data/synthetic4` |

Phases (`rerun/t5f_console.txt`), all exit 0: setup → core_start → runner_id → principals → containers_start → harness
(10 items, exit 0) → container checks / env leak check → core_stop → harness `coredown` (exit 0) → logs → containers_stop
(`docker stop` exit 0; 0 containers left; core + owner worker stopped by SIGTERM; no process left).

## Results (request ids in `rerun/run/t5f_steps.jsonl`)

| check | verdict | key facts |
|---|---|---|
| health: no token → 401; token → 200 with `core {status ok, reachable}`. **(f)** `allowlists` == `{dataset_roots: ["/srv/mbnas/data"], output_roots: ["/srv/mbnas/runtime/outputs"], archive_roots: []}` (exactly the gateway's own); the core-only root and the core home path appear 0 times; devices `[cpu]` | PASS | `t5f-581854ba…`, `t5f-5ee2f71b…`; the core-down health shows the same own roots |
| web `/` from the image: index sha `08772e74…`; `/assets/index-CY9ctObG.js` 529 623 B sha `6c3f29a2…`; css; SPA fallback | PASS | byte-identical to the ZIP's `mbnas_web/dist` |
| upload + structure check: valid → PASS; broken → FAIL (`SHAPE_MISMATCH`, `MERGE_INPUT_MISMATCH`, row 12) | PASS | `t5f-337d61fe…`, `t5f-37b9d14e…` |
| **(b)** OFF generation with the **customer-only output root**: validate (`validation_host: gateway`) → `run-81d06544bf80` **COMPLETED** in 6.4 s; 2 candidates UNTRAINED / NOT_EVALUATED; owner-worker job SUCCEEDED (owner-worker log: no error) | PASS | create `t5f-535edd93…` |
| **(b)** OFF candidate architecture artifact through the gateway: `art-5ce4b47b…`, 985 B, sha `6999dc2b…` (body == header == listed), `X-Artifact-Verification: core` (core-local serving; not in the gateway store) | PASS | `t5f-da33f527…` |
| user-YAML CPU training, 64 px, 2 epochs, pause during epoch 1 → PAUSED (`PAUSED_AT_EPOCH`, full state e1) → resume → `train-aa1d88e939f5` COMPLETED. Attempt 2 has `resumed_from=full_e0001.pt`, `device_used=cpu`, lease owner `runner-0a6ab6db` | PASS | create `t5f-49add8cc…`, pause `t5f-67b2bd94…`, resume `t5f-ffb18f3d…` |
| result rows: evaluation row `SEARCH_COMPLETE`, TRAIN rung, 2 epochs, `latency_source pending`; candidate detail `training_status` TRAINED | PASS | `t5f-f6f41494…`, `t5f-2779f418…`, `t5f-08db4133…` |
| downloads, body sha == `X-Artifact-Sha256` == listed | PASS | runner artifact 73 072 B `b7bae762…` (in store, NOT_READY → READY); architecture of the training run 1 318 B `e1d49bbe…`; `training_full` e2 31 600 353 B `4c23d70e…`; `weights_only` e2 5 360 396 B `14f7f122…` |
| export zip | PASS | 22 378 B, sha `8bafb306…` == `X-Bundle-Sha256`, 20 members, `SHA256SUMS` verified (`t5f-7d7793b4…`) |
| **(c)** validation-ON CPU training `train-da239b90980c` → run COMPLETED, job SUCCEEDED. Row `SEARCH_COMPLETE`, `performance_status NOT_EVALUATED`, `reason_code EVALUATION_REQUIRES_CUDA`; ap/fitness null, latency `pending`, `device_used=cpu`; checkpoint kept (`full_e0002.pt`), download 31 600 801 B sha `35bd210b…` matches | PASS | create `t5f-4e7cbf14…`, ckpt `t5f-ba43f76e…` |
| **(c) decision step**: evaluation-ON search → validate `valid: false`, `EVALUATION_REQUIRES_CUDA` ("…the registered device inventory is CPU-only…") | **PASS-by-design** | `t5f-e156d2a5…` |
| core stopped → health 200 `degraded`, `core: unreachable`, warning `core: CORE_UNAVAILABLE`; `POST /protocols/validate` **503 `CORE_UNAVAILABLE`** (`location: core`, `Retry-After: 5`, no `valid` key = no fallback); `GET /runs` 503 | PASS | `t5f-225fea6a…`, `t5f-2ea32973…`, `t5f-a640f73d…` |
| `mbnas_core` in both containers: `import` → `ModuleNotFoundError` (exit 1), `find_spec` None, 0 `*mbnas_core*` files; mbnas distributions = contracts, model-runtime, runner, server | PASS | `rerun/run/t5f_container_checks.txt` |
| **(e)** runner id: `runner-0a6ab6db` matches `^runner-[0-9a-f]{8}$`; registered ONLINE as `cpu`; the only runner lease owner; contains neither the host name nor the container host name; no `MBNAS_RUNNER_RUNNER_ID` in the runner env. Public responses (health, runs, 3 snapshots, candidate detail): 0 host-name hits; snapshot `workers` = `wrk-4b6bb8e7…` (opaque owner worker) + `runner-0a6ab6db` | PASS | `rerun/run/t5f_container_checks.txt`, `rerun/run/t5f_public_hostname_check.txt` |
| container env (`docker inspect` + `docker exec env`) vs T5 core secrets (3), final owner-install secrets (7), canaries (9 + prefix) | PASS (0 unexpected hits; positive control: gateway holds its own console token and server principal, runner its runner token) | `rerun/run/t5f_env_leak_check.txt` |
| image: 14 layers unpacked (52 005 files, 3 007 902 685 B; 565 symlinks not followed) + image config + `docker history --no-trunc` + image env. `canary_scan --profile public --secrets-env <14 needles: T5 core/gateway/runner + final owner install>`, host facts on | PASS (0 hits, 104 needles); positive control on the env files: 14 hits | `rerun/leakscan/` |
| **(d)** request ids: 66 harness ids, 66/66 echoed. **49 ids have exactly one `mbnas_server.access` line (container log) and one `mbnas_core.access` line (core log)** with the same `request_id` | PASS | e.g. `POST /api/v1/protocols/validate … request_id=t5f-0210d37a…` ↔ `POST /core/v1/console/protocols/validate … request_id=t5f-0210d37a…`. 7 ids have 1 gateway line + 2 core lines (recounted from the raw access logs, C11 EVAL_1 D9): 5 store-served downloads (3 checkpoints, 2 artifacts: `GET …/artifacts/<id>/record`, then `POST /core/v1/console/artifacts/<id>/locations`), 1 core-local architecture download (`GET …/artifacts/<id>/record`, then the bytes `GET …/artifacts/<id>` from the core) and 1 resume (`GET /core/v1/console/runs/<id>`, then `POST …/resume`). 10 are gateway-only: static web, uploads, no-token health, and the 3 requests made while the core was down. Audit log 5 ids, core DB run-log events 3; the core-down error body carries the caller's id (`rerun/run/t5f_request_id_linkage.json`) |

## Notes added after the C11 / C12 evaluations (2026-10-28, round 4)

- **Host-name line of `rerun/run/t5f_container_checks.txt:35` (C11 EVAL_1 D9).** The line reads "public responses saved by
  the harness containing the host name: 1 files", but that file is `run/t5f_steps.jsonl`: the harness log, which also holds
  rows the harness read from the owner DB. The hit is the `lease_owner` of the PROPOSE_GENERATION job, i.e. the owner
  worker's id `<host name>:<pid>` on the core host (`rerun/harness/t45f.py:161,315`). It is not a public response. The
  public responses themselves (health, runs, 3 snapshots, candidate detail) have 0 host-name hits
  (`run/t5f_public_hostname_check.txt`), as the (e) row says.
- **Runner side while the core is down (C12 EVAL_1 D6).** The T5 logs show only the gateway side: the runner container
  log ends at 23:00:33Z, the core was stopped at 23:01:40Z and the logs were collected at 23:01:54Z, before the runner's
  first claim retry cycle (6 attempts, about 15 s) ended. Runner-side evidence with the same runner code (the
  `mbnas_runner` wheel is byte-identical in the three final ZIPs):
  - the final owner ZIP's T6 integration smoke (owner ZIP `79cce02a…`, verify exit 0, integration 17/17):
    `runtime/test_root/t6/final/mbnas owner final/mbnas_application/runtime/logs/verify/20261027T221827Z-owner/integration_smoke.txt`
    lines 76-80 (core stopped, gateway `503`) and lines 129-130: `runner smoke-runner-1: claim failed: POST /claim:
    ConnectError: [Errno 111] Connection refused (after 6 attempts)` 15 s after the core stop, then `deregister failed …
    (after 2 attempts)`. The runner waits and fails explicitly; there is no fallback;
  - run 1 on the earlier public ZIP: `run/t5c_runner_container.log:7-9`, the same `claim failed … (after 6 attempts)`
    line three times while the core was down;
  - unit level: `tests/public/runner/test_runner_daemon_unit.py` (the client retries, then raises `CoreUnavailable`).
  Not covered: a core that goes down while a job is running (heartbeat loss during a job) was not exercised in T5 or T6.

## Compared with run 1

| run-1 finding | final ZIP |
|---|---|
| S1: customer-only output root → owner worker crashed, OFF run stuck QUEUED | fixed: run COMPLETED; files written core-local; architecture served `core_local` via the gateway |
| S2: evaluation-ON PROPOSE_GENERATION read the dataset YAML on the core host | fixed path not exercised: the CPU-only fleet now refuses the evaluation-ON search at validate (확인 못 함 for the gateway dataset-summary path itself; it needs a GPU fleet) |
| S3: CPU evaluation crashed (`AssertionError`), no decision | fixed by design: `NOT_EVALUATED` / `EVALUATION_REQUIRES_CUDA` with checkpoint; evaluation-ON search refused at validate |
| S4: no request-id linkage between gateway and core logs | fixed: 49 ids with one access line on each side |
| (T8 note) health relayed core allowlists | fixed: own roots only, core-only roots absent |
| runner id configured by hand (`t5-cpu-runner`) | now the random default `runner-0a6ab6db`, generated by the image |

## Files (`rerun/`)

- `docker_build.txt` (cached, superseded), `docker_build_nocache.txt` (the image used), `image_info.txt`, `zips_sha256_used.txt`, `runner_id_generated.txt`
- Consoles: `t5f_console.txt`, `t5f_harness.txt`, `t5f_coredown.txt`
- `run/`: steps, http log, request ids, candidate details, trainval rows, container logs (`t5f_gateway_container.log` = gateway access log, `t5f_runner_container.log`), container / env / host-name checks, linkage json
- `logs/`: host core log (core access log) and owner-worker log, gateway `audit.jsonl`
- `leakscan/`: image scan, positive control, docker history, image env/config, evidence scan
- `harness/`: `t5f_run.sh`, `t45f.py`, `env_leak_check.py`, `reqid_link2.py`, `copy_redacted.py`

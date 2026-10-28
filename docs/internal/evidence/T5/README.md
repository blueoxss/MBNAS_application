# T5 — public independent run (WP-8 §1 row T5, C11 `real_core_runner`, DD-A31/A38/A46) — agent P2, 2026-10-28 KST

**Overall: PARTIAL.** The CPU public image built from the public ZIP alone, run as gateway + runner containers
(`--network host`, `--rm --init`) against an owner core on the host, passes: upload → structure check (valid/invalid) →
OFF generation → user-YAML CPU training (incl. pause → full-state resume) → result rows → web console at `/` →
artifact/checkpoint/export downloads with sha256 match → core stopped ⇒ health `degraded` + `503 CORE_UNAVAILABLE` (no
fallback); `mbnas_core` is not importable in either container; no core secret / owner-install secret / canary / host fact
in the containers' env, image config, `docker history` or any of the 14 image layers (52 005 files, 3.0 GB).
**FAIL**: (1) with a customer-side `output_root` that exists only on the gateway host, the OFF generation run never
starts — the owner worker crashes on `mkdir` of that path on the core host (training runs with the same root COMPLETE);
(2) no decision step (promotion / final retrain) is possible on a CPU runner; (3) no request id is shared by a gateway log
record and a core log record (core logs carry no request ids). The generation / search part of the pass therefore relies
on the documented workaround "same absolute output path on both hosts" (`INSTALL.md` §4).

`<ROOT>` = TARGET_ROOT, `<T>` = `<ROOT>/runtime/test_root/t4t5`. Host paths in this folder are replaced by placeholders
(`harness/copy_redacted.py`); the folder was leak-scanned clean (`leakscan/evidence_scan.txt`).

## Inputs and topology

| item | value |
|---|---|
| public ZIP (only this ZIP was extracted for the customer side) | sha256 `4174060dde93ce8aa941f2cbd36fcb8b0230f5f184e68f8ccbc71d49bcd9ae86` (mtime 03:03:34 KST > run start 03:02:23; copy in `<T>/zips`). `dist/public` was **rebuilt again at 03:14:39** (sha `529243cf…`): only docs/BUILD_INFO/PACKAGE_PROFILE/FILE_MANIFEST/README/one e2e spec differ; every Dockerfile.cpu input (requirements-cpu.txt, 4 wheels, entrypoint, `mbnas_web/dist`) and `examples/` are byte-identical (`public_zip_rebuild_diff.txt`), so the image content is unaffected — the current file itself was 확인 못 함 by a separate build |
| image | `DOCKER_BUILDKIT=0 docker build -f deploy/public/Dockerfile.cpu -t mbnas-public-cpu:t5 .` in the extracted root → exit 0, 227 s, **online** (PyPI + download.pytorch.org/whl/cpu; base/apt/venv steps 3–11 from the local layer cache of earlier builds, the third-party layer was installed fresh: torch 2.6.0+cpu); id `sha256:be21b5a88c7e4219c1258c37c682044b3fbc697877767cf28d9eef2e968f751f`, 2 972 536 141 B, 14 layers, user 10001:10001, base `python:3.10-slim@sha256:6ff50646…` (`docker_build.txt`, `image_info.txt`) |
| owner core (host) | the extracted **owner** tree of T4 (`<T>/t4f`, its venv): `mbnas-core-api --host 127.0.0.1 --port 18750` + `mbnas-core-owner-worker --owner`, separate `MBNAS_HOME=<T>/t5/core_home` (own DB, generated keys in a 0600 env file), `CUDA_VISIBLE_DEVICES=""`, `MBNAS_CORE_HEARTBEAT_S=2`, default device source (host NVML inventory read; no GPU compute) |
| principals | `mbnas-core-principal create --kind server` and `--kind runner --runner-id t5-cpu-runner`; tokens written file-to-file (0600), never printed |
| gateway container | `docker run -d --rm --init --name t5-gw --network host --user <uid>:<gid> --env-file run/config/public/gateway.env -e MBNAS_HOME=/srv/mbnas/runtime -e MBNAS_GW_HOST=127.0.0.1 -e MBNAS_GW_PORT=18760 -e MBNAS_GW_WEB_DIST=/opt/mbnas/web/dist -e MBNAS_GW_DATASET_ROOTS=… -e MBNAS_GW_ARTIFACT_STORE=/srv/mbnas/runtime/gateway/artifacts -v run/runtime:/srv/mbnas/runtime -v run/data:/srv/mbnas/data:ro [-v <shared_out>:<shared_out>] mbnas-public-cpu:t5 gateway` (mirrors `deploy/public/docker-compose.yml`) |
| runner container | same image, `runner`, `--shm-size 2g`, `--env-file runner.env`, `MBNAS_RUNNER_ARTIFACT_TRANSFER=local`, store dir = the gateway store (shared volume), `CUDA_VISIBLE_DEVICES=` (registered ONLINE as `cpu`, `device_uuid` NULL) |
| dataset | `examples/synthetic4` of the public ZIP at `/srv/mbnas/data/synthetic4` (container path); protocols name `train_root`/`search_val_root`/`final_eval_root` explicitly (DD-A53 g) |

Phases (`t5_console.txt`; `harness/t5_run.sh` subcommands): **A** customer-side `output_root=/srv/mbnas/runtime/outputs`
(exists only in the containers) — 18:27–18:32Z; **B** same-path output root `<T>/t5/run/shared_out` mounted at the
identical path in both containers and allowed on both sides (core + gateway `OUTPUT_ROOTS`) — 18:32–18:35Z; **C**
additionally the dataset at an identical path (decision probe) — 18:35–18:37Z (first container start of C failed with
exit 125: harness quoting bug, fixed and restarted); **D** core stopped → degraded checks — 18:37:52Z; **E** containers
stopped 18:39:02Z (`--rm`, none left). Between A and B the stuck run was cancelled through the gateway (200), container
logs were captured, containers + core were stopped and the two `OUTPUT_ROOTS` lines edited (operator transcript; the
cancel and DB views are in `run/t5_split_output_root_finding.txt`).

## Results

| check | verdict | evidence (request ids in `run/t5*_steps.jsonl`) |
|---|---|---|
| health: no token 401, token 200, `core: {status ok, reachable}` | PASS | `t5-d460d9bd…`, `t5-e5ea864b…` |
| web console served at `/` from the image: index 733 B sha `9bdba151…` == ZIP `mbnas_web/dist/index.html`; `/assets/index-DlYSES30.js` 528 433 B sha `074a3d8b…` + css identical, `immutable`; SPA fallback 200 | PASS | `t5-a8357f71…` |
| upload `yolo11n_user.yaml` (sha match) → structure-check PASS; upload `yolo11n_user_broken.yaml` → FAIL `SHAPE_MISMATCH`, `MERGE_INPUT_MISMATCH` (row 12) | PASS | `t5-8905be54…`, `t5-eddde2b3…`, `t5-d8f43af2…`, `t5-20a58766…` |
| validate OFF policy through the gateway (`capabilities.validation_host = gateway`) | PASS (A and B) | `t5-24731365…`, `t5-ca70dd3a…` |
| **A**: OFF generation run with customer-side output_root | **FAIL** — run `run-8c5d97da5cc8` stays QUEUED; PROPOSE_GENERATION leased, owner worker dies with `PermissionError: '/srv/mbnas'` in `owner_worker/main.py:_paths` (mkdir of `<output_root>/<run>/control` on the core host), lease expires, job re-queued; cancelled via gateway 200 | `run/t5_split_output_root_finding.txt` |
| **A**: explicit CPU training of the uploaded YAML with the same customer-side output_root (DD-A46 acceptance for training) → COMPLETED `train-7b84db94fc05`, 2 epochs, `device_used=cpu`; downloads + export (20 members, `SHA256SUMS` ok, sha `91675f2e…`) | PASS | `run/t5a_steps.jsonl` (`t5a-f79a7c08…` create) |
| **B**: OFF generation run → COMPLETED `run-e6a505c69145`, 2 candidates, `training_status` UNTRAINED ×2, NOT_EVALUATED, candidate detail 200 | PASS | `t5-63c280d4…` |
| **B**: user-YAML CPU training 64 px 2 epochs, pause in epoch 1 → PAUSED (`PAUSED_AT_EPOCH`, full state e1) → resume → COMPLETED `train-10c7a96bf696`, attempt 2 `resumed_from=full_e0001.pt`, `device_used=cpu` | PASS | create `t5-433827d8…`, pause `t5-c87d2c4c…`, resume `t5-66d0cd94…` |
| **B**: result rows (run, snapshot evaluation row: TRAIN rung, 2 epochs, `latency_source=pending`; candidate detail `training_status` TRAINED) | PASS | `t5-c2c6c92b…`, `t5-9d2949b4…`, `t5-d8ba22d3…` |
| **B**: downloads through the gateway, sha256(body) == `X-Artifact-Sha256` == listed: runner artifact 73 072 B `b7bae762…` (in the gateway store via local transfer, NOT_READY → READY), architecture 975 B `0e47ec6b…`, checkpoint `training_full` e2 31 600 353 B `c129f38d…`, `weights_only` e2 5 360 396 B `14f7f122…` | PASS | `t5-84fb767d…`, `t5-03ea09cc…`, `t5-af4339c9…`, `t5-45648e62…`, `t5-17bbd6f9…` |
| **B**: export zip 23 279 B sha `76da56a2…` == `X-Bundle-Sha256`, 21 members, `SHA256SUMS` verified | PASS | `t5-35b65b13…` |
| decision step: **B** search probe → `PROPOSE_GENERATION FAILED DATASET_YAML_INVALID` (the owner worker reads the container dataset path on the core host); **C** (dataset at an identical path) → generation ok, both CANDIDATE_TRAIN `EVAL_FAILED` (`AssertionError: Torch not compiled with CUDA enabled`, `device_used=cpu`), run FAILED `NO_SUCCESSFUL_EVALUATION`; final retrain → `409 INVALID_STATE` | **FAIL** (CPU, DD-A32; plus split gap) | `t5-bd57fa24…`, `t5dec-1e25fdae…`, `t5dec-ab636e6c…` |
| core stopped (SIGTERM core + owner worker) → `GET /health` 200 `status: degraded`, `core: unreachable`, warning `core: CORE_UNAVAILABLE`; `POST /protocols/validate` **503 `CORE_UNAVAILABLE`** (`location: core`, `Retry-After: 5`, no `valid` key = no local fallback); `GET /runs` 503 | PASS | `t5-56babe84…`, `t5-04831935…`, `t5-ec89c522…` |
| `import mbnas_core` in t5-gw and t5-runner → `ModuleNotFoundError` (exit 1); `find_spec` None; installed mbnas distributions = contracts, model-runtime, runner, server; 0 files named `*mbnas_core*` on the container root fs | PASS | `run/t5_container_checks.txt` |
| container env (`docker inspect .Config.Env` + `docker exec env`) vs needles: T5 core secrets (3), owner-install core/gateway/runner secrets (7), canary values (9) + prefix → 0 hits; the gateway holds only its own console token/signing key and server-principal token, the runner only its runner token (expected, positive control) | PASS | `run/t5_env_leak_check.txt` |
| image: all 14 layers unpacked (52 005 files, 3 007 833 879 B, 46 nested containers; 565 symlinks not followed) + image config + `docker history --no-trunc` + image env, `scripts/dev/canary_scan.py --manifest … --profile public --secrets-env <14 secret needles: T5 core/gateway/runner + owner install>` (host facts on) → 0 hits, PASS; positive control on the env files → 14 hits | PASS | `leakscan/image_scan.{txt,json}`, `leakscan/positive_control.txt` |
| request ids: 95 harness ids, 94 echoed (the 95th got no response: gateway not yet up in phase C); gateway audit log 7 ids, gateway container log 3 ids (core-down `core call … failed (request_id=…)`), core DB run-log events 5 ids (each run-creating `POST /runs` / `/training-runs`), core error bodies carry the caller's id (`INVALID_STATE` ×2, `CORE_UNAVAILABLE`); core log 0, owner-worker log 0; **ids present in both a gateway log record and a core log record: 0** | **FAIL** (linkage) | `run/t5_request_id_linkage.json` |
| everything stopped: core + owner worker SIGTERM (stopped), containers `docker stop` (exit 0, `--rm`, none left) | PASS | `t5_console.txt` |

## Findings / requests

- **S1 (FAIL, product, DD-A46 not complete)**: `owner_worker/main.py:_paths` creates `<output_root>/<run>/control` on the
  core host; with a customer-only output root the owner worker process **exits** on `PermissionError` (unhandled), the
  job loops through lease expiry, the run never leaves QUEUED. Needs: write owner-worker files under a core-local run
  root (or the core output root) and fail the job with a reason instead of crashing the worker.
- **S2 (FAIL, product)**: a search with evaluation reads `console.paths.dataset_yaml` on the core host in
  PROPOSE_GENERATION (`DATASET_YAML_INVALID` for a customer-side path) — the gateway-measured dataset summary
  (DD-A34/A37) is not used there.
- **S3 (FAIL, by design DD-A32)**: CPU runners cannot produce evaluations (latency needs CUDA; the CPU torch build
  raises in the evaluation step), hence no gate / promotion / final retrain on CPU — requires the GPU track.
- **S4 (FAIL, observability)**: no per-request request-id logging in core and gateway (only audit of sensitive reads and
  failure warnings) → C11 "T5 로그(request id 연결)" can only be shown through DB run-log events and error bodies.
- Note: health of the gateway lists its own `allowlists` (gateway dataset/output roots); the T8 row covers that.

## Files

`docker_build.txt`, `image_info.txt`, `public_zip_rebuild_diff.txt`, `zips_sha256_used.txt`; consoles
`t5_console.txt` (phases), `t5_console_attempt1_envquote.txt` (first bring-up: harness env-file quoting bug, core never
started), `t5a_train_customer_root.txt`, `t5b_harness.txt`, `t5c_decision.txt` (gateway not up), `t5dec_decision.txt`,
`t5_coredown.txt`; `run/` = harness output (`t5_steps.jsonl` = phase A lines 1–5 [health … gen-validate, then stopped],
phase B lines 6–28, core-down lines 29–30; `t5a_*` phase A training; `t5dec_*` decision probe; `*_http.log`,
`*_request_ids.jsonl`, candidate details, snapshots), container logs per phase (`t5{a,b,c}_*_container.log`), raw core
logs of phase A, `t5_split_output_root_finding.txt`, `t5_container_checks.txt`, `t5_env_leak_check.txt`,
`t5_request_id_linkage.json`; `logs/` = core / owner-worker log of the host core (all phases) and the gateway
`audit.jsonl`; `leakscan/` = image scan, positive control, docker history, image env/config, evidence scan;
`harness/` = `t5_run.sh`, `t45.py`, `env_leak_check.py`, `reqid_link.py`, `copy_redacted.py`.

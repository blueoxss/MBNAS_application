# T7 / WP-G2 GPU validation track — evidence (2026-10-27, agent G2)

TREE_HASH `sha256:164292c6e775a52a3da02f98282257b67a282f9e84b236ded3940e592a568f2f` (`scripts/dev/source_tree_hash.py`,
mbnas-source-tree/2, 213 files of the five packages as INSTALLED in the frozen copy's venv; `tree_hash.json`).
Frozen copy `runtime/test_root/g2_tree` = rsync of the live tree at 2026-10-27T06:03:28Z (excludes runtime, build,
node_modules, dist, .git) + `mbnas_web/dist` copied; installed offline with
`bash scripts/install.sh --profile owner --offline --wheelhouse <live>/build/wheelhouse --python <mbnas310 python3.10>`
(exit 0, 1m40s, `logs/install_owner_offline.txt`). Installed packages == copy sources (diff -r, only the unpackaged
openapi `drafts/`). Every process/test ran from the copy; GPUs: physical GPU 0 (T7 stack) and GPU 1 (G2-2/G2-3/G2-4),
both RTX A6000, verified idle before (`nvidia_smi_initial.txt`: no compute process) and after (`nvidia_smi_final.txt`).
Network guard for every GPU process: `HTTPS_PROXY=http://127.0.0.1:9` (+`NO_PROXY` loopback), `YOLO_OFFLINE=1`,
`HF_HUB_OFFLINE=1` → nothing was downloaded (ultralytics' AMP check, which would fetch `yolo26n.pt`, reports
"checks skipped" and keeps AMP on).

## Result table

| Item | Verdict | Evidence (steps.jsonl item / files) | Key facts |
|---|---|---|---|
| G2-1 (a1) BASELINE_TRAIN yolo11n (user YAML nc=1), 2 ep, 128 px, cuda:0, via gateway | **PASS** | item `a`; `dbviews/a_train-a296ce62247f.json`, `snapshots/a_*` | job SUCCEEDED, claimed/committed by `t7-gpu0` (GPU-e8fe2321…, RTX A6000), EVALUATED row, latency_source=measured 7.95 ms (stack-check run: 7.98 ms), fitness 0.699, eval `device_uuid` == runner GPU |
| G2-1 (a2) `device_used=cuda:0` recorded | **FAIL** | step `a-device-used-recorded` | runner never sets `outputs.device_used` (neither GPU nor the DD-A32 CPU mapping) → `raw_metrics.device_used` null. Device is only traceable via eval `device/device_uuid`, cost_ledger `device_uuid`, runner row |
| G2-1 (b) search run, evaluation ON (L0=1/L1=2 ep, pop 2, gen 2, seeds 101/202, GA+Random) | **PASS** | item `b`; `dbviews/b_run-7b3f1da6d222.json` | run COMPLETED in 7.2 min: 32 CANDIDATE_TRAIN SUCCEEDED (+8 PROPOSE_GENERATION on the owner worker), 32 EVALUATED rows, all latency_source=measured (7.5–12.0 ms), real fitness 0.397–0.700; promotion decisions L0 16×PROMOTION_ELIGIBLE, L1 16×SEARCH_RUNG_FINAL; AP=0 on this tiny synthetic set |
| G2-1 (c) pause during GPU training → resume → full-state resume | **PASS** | item `c`; `dbviews/c_paused_*`, `dbviews/c_*` | pause after full state e2 → child stopped at epoch 3 boundary, job QUEUED with reason **`PAUSED_AT_EPOCH`** (contract PAUSE_REASONS member; the generic name PAUSED_AT_SAFE_BOUNDARY was not used by the training handler), run PAUSED; resume → same job attempt 2 `resumed_from=…/full_e0003.pt`, 8/8 epochs, 480 steps / 3840 samples (continuous counters = no weights-only fallback), EVALUATED |
| G2-1 (d) cancel | **PASS** | item `d`; `dbviews/d_*` | run CANCELLED (CANCELLED_BY_USER), job CANCELLED/RUN_CANCELLED (discarded child result kept), child pid gone, GPU memory released |
| G2-1 (e) SIGKILL runner → lease expiry → sweeper requeue → attempt+1 → idempotent commit | **PASS** | item `e`; `dbviews/e_*`, `logs/*runner.log` | kill at 06:31:46Z (child died via PDEATHSIG), lease expired + LEASE_EXPIRED requeue after 59 s, runner LOST; restarted runner replayed its journal: stale attempt-1 commit → **409 LEASE_LOST, discarded**; attempt 2 resumed from full_e0002 → SUCCEEDED; explicit replays: attempt-1 result.json → 409 LEASE_LOST (stored), attempt-2 result.json → 200 identical stored response, job unchanged |
| G2-1 (f1) artifact relay into the gateway store (`MBNAS_RUNNER_ARTIFACT_TRANSFER=local`) | **PASS** | step `f-store-contents`; `dbviews/f_store_listing.json` | 201 store objects, all `<sha[:2]>/<sha>` + meta JSON, digests re-verified; bytes of all 137 checkpoints and of all 397 runner-registered artifacts present (85 distinct artifact digests: identical panels de-duplicate, DD-A39 no-op re-registration) |
| G2-1 (f2) download of runner-produced artifacts through the gateway with digest match | **FAIL** | step `f-download-via-gateway` | gateway route still relays to core (WP-5B wiring absent in the frozen tree): evaluation.json / gt / tp panels and a training_full checkpoint → **404 MISSING** (core cannot see runner bytes; as a side effect core marked those 3 artifact rows MISSING); core-local `architecture` artifact and the export bundle download OK with digest match |
| G2-1 (g1) EVALUATE official yolo11n | **PASS** (see note) | item `g` (run 06:38Z); `dbviews/g_evaluate_train-a880bd4f9902.json`, `logs/*probe_registry.log` | weights not under runtime/weights: a byte-identical local copy (`~/Desktop/etri_hd/yolo11n.pt`, sha256 0ebbc80d… == registry example `weights_sha256`) was COPIED (not downloaded) into the copy's weights dir; CPU registry probe → yolo11n AVAILABLE; evaluate_only via gateway on coco128 (local real COCO images) at 640: EVALUATE SUCCEEDED, AP50-95 0.503, AP50 0.671, recall 0.570, measured 9.9 ms, cost row device = GPU 0. The first g run (06:35Z) shows FAIL only because the harness required `device_uuid` inside the official-evaluation payload, which that payload does not carry (criterion corrected, rerun) |
| G2-1 (g2) BENCHMARK official yolo11n | **FAIL** | steps `g-benchmark-child`, `g-benchmark-diagnostic-shim`; `logs/*benchmark_*.log`, `dbviews/g_benchmark_diag_record.json` | core never creates BENCHMARK jobs (runner_api/envelope.py) → run as the runner child `python -m mbnas_runner --spec-file`: **INTERNAL_ERROR `'Manifest' object has no attribute 'root'`** (`mbnas_model_runtime/benchmark/pipeline.py:124`; same line in the original snapshot and the live tree). DIAGNOSTIC only (in-process shim root→roots[0]): pytorch FP32/FP16 and ONNX Runtime CUDA FP32 MEASURED (AP deltas ≤6e-4), TensorRT BLOCKED (`tensorrt` not installed — excluded from the wheelhouse) |
| G2-1 (h) FINAL_RETRAIN short run | **PASS** | item `h`; `dbviews/h_run-b6b1b4851829.json` | child run (kind FINAL_RETRAIN, 4 finalists = top-1 per arm×seed) COMPLETED; 4 FINAL_RETRAIN jobs SUCCEEDED (2 ep scratch), evaluated on final_eval with measured latency |
| G2-2 T2-GPU equivalence vs original monolith | **PASS** (training exact; latency see note) | `T2_GPU/T2_GPU_REPORT.md`, `T2_GPU/{ref,new,new_2}.json`, `run_b/`, `run_c_quiet/` | original `mbnas_server.training.handlers` (snapshot, isolated) vs new runner handler, same JobSpec/seed/recipe, deterministic=True: eval-EMA/model/optimizer tensor digests, losses, AP, evaluation_hash **bitwise identical** in all 3 configs (incl. AP50-95 0.824 in B/C); latency: quiet config C |Δmean| 0.0075 ms ≤ spread 0.045–0.108 ms; config B (host shared with the T7 run) exceeded its spread (ref +1.4 ms), config A outlier was a NEW run. Handler-level drive (not `worker.child --job-id`, which needs the original DB) |
| G2-3 resume equivalence (yolo11n, horizon 6, cuts 2,4) | **PASS** | `resume_equivalence.json`, `logs/resume_equivalence_*` | 2→4→6 in fresh processes vs continuous 6 on GPU 1: counters/LR/optimizer/RNG/loader exact, model+EMA bitwise (max\|d\| 0.0); negative cases blocked (stripped optimizer → MISSING_STATE, wrong arch hash → HASH_MISMATCH, native last.pt → FORMAT_UNKNOWN). Script parameterised (no host paths) |
| G2-4 sandbox RLIMIT_AS on the GPU host | **PASS** (measured) | `G2_4/rlimit_gpu_host.{json,txt,py}` | same thresholds as the CPU measurement: yolo11n@640 passes at 5 GiB; yolo11x@1280 RESOURCE_LIMIT at 5 GiB, PASS at 6 GiB; rt-detr-x@1280 PASS at 5 GiB (3A: failed at 5); 8 GiB default holds. Diagnostic variant with the GPU visible inside the worker also passes at 6/8 GiB (production worker always gets `CUDA_VISIBLE_DEVICES=""`) |
| G2-4 Docker CUDA image smoke | **PASS** | `G2_4/docker_build.txt`, `G2_4/docker_runner.txt` | `docker build` of deploy/public/Dockerfile from the frozen copy (legacy builder, local wheelhouse over 127.0.0.1:8731, base/apt/third-party layers cached) → `mbnas-public:g2t7` (39c4c0da4b7d); `docker run --gpus device=1`: torch 2.6.0+cu124 `cuda.is_available() True`, RTX A6000, matmul ok, `mbnas_core` not importable, `mbnas-runner --help` ok |

GPU time: every GPU-holding process ran ≈ 20 min in total (T7 runner windows 06:19:17–06:19:41, 06:21:02–06:33:48
incl. a 61 s no-GPU lease gap, 06:35:32–06:35:53, 06:38:5x–06:39:46; G2-3 46 s; G2-2 ≈ 4.8 min over 4 orchestrations;
docker smoke ≈ 5 s) — far below the 6 h cap.

**GPU_PATH_VALIDATED: PARTIAL** — recommend accepting the GPU job-execution path (train, search with measured latency/fitness/promotion, pause→full-state resume, cancel, SIGKILL→lease reclaim→attempt+1 with fencing/idempotency, final retrain, official evaluate, local artifact transfer) as validated on TREE_HASH 164292c6…, and NOT the device_used recording (a2), the gateway download of runner artifacts (f2, WP-5B) or the BENCHMARK kind (g2, pipeline defect): re-run items a/f/g after those fixes and the whole track once W4 changes the tree hash.

Note on item e: the "new runner" is the same runner id restarted on the same work root (journal replay path); a different runner on another work root cannot resume the attempt-1 full state (cross-runner transfer is a documented DD-A38 limitation) and was not exercised.

## Files
- `steps.jsonl` — one JSON line per step (UTC, item, ok, duration, request ids, pids, exit codes, nvidia-smi
  snapshots); `t7_summary_<stamp>.json` per harness run (items, gpu windows, pids/exit codes, nvidia-smi before/after):
  `20261027T061910Z` = stack check (item a), `20261027T062034Z` = validation-only dry check (item v),
  **`20261027T062055Z` = main run a,b,h,c,d,e,f**, `20261027T063522Z` / **`20261027T063849Z`** = item g (first / corrected).
- `snapshots/` — gateway `/api/v1/runs/{id}/snapshot` per run (bulky `per_sample` / latency `samples` arrays trimmed,
  counts kept); `dbviews/` — read-only core-DB views (jobs, evaluations, checkpoints, artifacts, locations, cost
  ledger, idempotent commits, runners), store listing, benchmark records.
- `logs/` — harness transcripts and stdout, copies of core/owner/gateway/runner logs and every job child's stderr log
  (secrets redacted; none found), install log, probe/benchmark logs, resume-equivalence log.
- `harness/t7_gpu_path.py` (driver; reuses the copy's `scripts/dev/wp3_three_process_smoke.py`), `harness/bench_diag.py`
  (diagnostic shim), `T2_GPU/harness/` (G2-2 orchestrator, side runner, report renderer), `G2_4/rlimit_gpu_host.py`.
- `T2_GPU/failed_attempt1/` — first G2-2 attempt (validation refused the disabled PILOT baselines: BASELINE_UNKNOWN with
  an empty registry; fixed by `baselines: []`).

## Other observations
- Search candidates with ETRI attention blocks log `Memory Efficient attention defaults to a non-deterministic
  algorithm` (CUDA SDPA backward, warn_only determinism): such candidates are not bitwise reproducible on GPU.
- `capability.py` declares `host-gpu-0` as "NVIDIA A100 80GB PCIe" (original host); health/validation show that name
  on this RTX A6000 host.
- The official-evaluation payload has no `device_uuid` / latency device manifest (training-evaluation payloads do).
- `tests/owner/t2/reference_runner.py` assumes editable installs (it removes `<pkg>/src` from sys.path); with the wheel
  install of the copy the new packages stay importable and it refuses to run — G2-2 used a meta-path blocker instead.

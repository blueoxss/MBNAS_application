# T7 GPU re-run (DD-A49 f, DD-A52) — evidence (2026-10-27 18:02–18:10Z)

TREE_HASH `sha256:9dafe4d021ab572058c306e596b686d3f19a1203cab7504c4c2662973fb670ea` (`scripts/dev/source_tree_hash.py`,
mbnas-source-tree/2, 220 files of the five packages as INSTALLED in the new frozen copy's venv; `tree_hash_rerun.json`).
Runner/runtime/contracts sub-hash (G2FIX command) `sha256:24c2214807a62b29aa8c717c3edb77d1f3743f0efb458dca261b89d722bd65b9`
— differs from the G2FIX round-2 value `4bfb6e51…`, i.e. those three packages changed again after G2FIX; this re-run
tests the tree as it was at 18:02:20Z, not the G2FIX snapshot.

Frozen copy `runtime/test_root/g2_tree2` = rsync of the live tree at 2026-10-27T18:02:20Z (excluding `/runtime`, `/build`,
`node_modules`, `dist`, `.git`) + `mbnas_web/dist` copied; installed with
`bash scripts/install.sh --profile owner --offline --wheelhouse <live>/build/wheelhouse --python <mbnas310 python3.10>`
(exit 0, 1m42s, `logs/rerun_install_owner_offline.txt`). Installed packages == copy sources (`diff -r`, only the
unpackaged openapi `drafts/`). Every process ran from the copy with the same network guard as the first run
(`HTTPS_PROXY=http://127.0.0.1:9`, `YOLO_OFFLINE=1`, `HF_HUB_OFFLINE=1`). Harness: `harness/t7_rerun.py` (subclass of
`harness/t7_gpu_path.py`, unchanged plumbing; strict device checks, store-served download check, GPU-1 runner item).
GPUs 0 and 1 (RTX A6000) were handed over by the designer; nvidia-smi showed no compute process before
(`steps_rerun.jsonl` `nvidia-smi-before`) and after (`nvidia_smi_final_rerun.txt`, step `nvidia-smi-final`).

## Result table

| Item | Verdict | Evidence | Key facts |
|---|---|---|---|
| (a) BASELINE_TRAIN via gateway on cuda:0 (was FAIL a2) | **PASS** | step `a-device-used-strict`; `dbviews/rerun_a_train-649a9fcc9220.json` | run COMPLETED, job claimed by `t7-gpu0`; `outputs.device_used = "cuda:0"`, `outputs.device_uuid = GPU-e8fe2321…` == runner registered uuid == nvidia-smi GPU 0; evaluation `raw_metrics.device_used = "cuda:0"`; training-eval payload + `latency.manifest.uuid` = same uuid, `nvml_lookup = "uuid"`; latency measured 8.37 ms |
| (b) f2 download of runner artifacts through the gateway (was FAIL) | **PASS** | step `f2-download-via-gateway-store`; `dbviews/rerun_f_states_after.json` | `MBNAS_RUNNER_ARTIFACT_TRANSFER=local`, runner store dir = gateway `artifact_store`. 7 downloads, all HTTP 200, `X-Artifact-Verification: pre-send`, sha256(body) == `X-Artifact-Sha256` == DB record: 1 `training_full` checkpoint (e2, 42.1 MB), training `evaluation.json`, architecture YAML, 1 fn panel (item a), 3 fn/fp panels of the official evaluation (item g). DB afterwards: 0 artifacts / 0 checkpoints MISSING, no state changed by the downloads; each downloaded object gained a `PRESENT@server_store verified_by=store` location next to its `runner_local` one |
| (c) official EVALUATE yolo11n (local byte-identical weights) | **PASS** | steps `g-official-weights-and-probe`, `g-evaluate-payload-device`; `dbviews/rerun_g_evaluate_train-ef745127ed1c.json` | weights sha256 0ebbc80d… == registry example (copied, not downloaded), CPU probe → AVAILABLE; EVALUATE SUCCEEDED on `t7-gpu0`, AP50-95 0.5034 / AP50 0.6711 (identical to the first run), measured 8.48 ms; payload `device_uuid` == runner uuid, `latency.manifest` present with `nvml_lookup = "uuid"` and `uuid` == runner uuid, `outputs.device_used = "cuda:0"` |
| (d) g2 BENCHMARK via `scripts/runner/bench_host_gpu.py` on GPU 1 (was FAIL) | **PASS** (TensorRT **BLOCKED_BY_ENV**) | step `g2-bench-host-gpu-gpu1`; `G2_RERUN_bench/` | `CUDA_VISIBLE_DEVICES=1`, `--data-root <work>/runner_data/coco128` (byte-identical to `~/Desktop/etri_hd/datasets/coco128` images, the set used in the first run) `--val-dir images/train2017 --weights yolo11n.pt`: exit 0, job SUCCEEDED, 27 s. MEASURED forward+postprocess p50: PyTorch FP32 6.38 ms, PyTorch FP16 8.39 ms, ORT CUDA FP32 2.84 ms; AP deltas ≤ 6.1e-4. TensorRT FP16/FP32 engines BLOCKED (`tensorrt` not importable), record `tensorrt.status = BLOCKED_BY_ENV`, no TensorRT number. Record's device uuid = GPU 1 (`GPU-a27ee4cd…`, `nvml_lookup = "uuid"`), GPU-0 uuid absent |
| (e) runner registered on GPU 1 — DD-A52 b | **PASS** | steps `i-gpu1-runner-registered`, `i-train-gpu1-device`, `i-evaluate-gpu1-device`; `dbviews/rerun_i_*` | GPU-0 runner stopped (SIGTERM, exit 0); `t7-gpu1` (`CUDA_VISIBLE_DEVICES=1`, gpu_index 1) registered `host-gpu-1`, uuid `GPU-a27ee4cd…` == nvidia-smi GPU 1. Short BASELINE_TRAIN (2 ep, training evaluation) and official EVALUATE both claimed by `t7-gpu1`, COMPLETED: outputs/payload `device_uuid`, `latency.manifest.uuid` and cost-ledger uuid are all GPU 1's, `nvml_lookup = "uuid"`, and GPU 0's uuid occurs nowhere in the recorded facts. EVALUATE on GPU 1: AP 0.5034 (same as GPU 0), 8.58 ms |

GPU time: stack 18:07:38–18:08:55Z (GPU-0 runner 18:07:38–18:08:19, GPU-1 runner 18:08:21–18:08:55), benchmark
18:09:18–18:09:45Z — about 2 min of the 45-min budget. Every started process (core, owner worker, gateway, 2 runners,
benchmark) was stopped; nvidia-smi final: no compute process on either GPU.

Scope: CANDIDATE_TRAIN was not run on GPU 1. Item (e) used BASELINE_TRAIN (same training + evaluation handler path) plus
EVALUATE, because a CANDIDATE_TRAIN needs a whole search run. Whether CANDIDATE_TRAIN records GPU 1 was not checked
on GPU (확인 못 함). The first run's remaining items were re-run afterwards on the same copy (second table).

## Second table — first-run items a,b,h,c,d,e,f on the same frozen copy (DD-A54, 2026-10-27 18:13–18:27Z)

Same TREE_HASH `9dafe4d0…`, same copy `g2_tree2`. The items are those of `harness/t7_gpu_path.py`, UNCHANGED
(criteria as in the first run). They were run through `harness/t7_rerun.py --mode full --timeout 600 --search-timeout 900`,
which changes only where the evidence goes: steps are appended to `steps_rerun.jsonl`, saved views are named
`rerun_full_*`, and the summary is `t7_rerun_full_summary_20261027T181305Z.json`. The first run's files are never
overwritten. Work root: `g2_tree2/runtime/t7/20261027T181305Z`. GPU 0 only, runner `t7-gpu0`
(`GPU-e8fe2321…`). Harness exit 0. GPU-holding windows were 18:13:12–18:24:54Z and 18:25:54–18:27:09Z, about 13 min
in total, with a 60 s lease gap without any GPU process. All four processes were stopped (`stop-own-processes`:
still_alive []). Afterwards nvidia-smi showed no compute process (step `nvidia-smi-final-after-full-run`).

| Item | Verdict | Evidence (steps_rerun.jsonl / files) | Key facts |
|---|---|---|---|
| (a) BASELINE_TRAIN yolo11n (user YAML nc=1), 2 ep, 128 px, cuda:0, via gateway | **PASS** | `a-baseline-train-gpu`, `a-device-used-recorded`; `dbviews/rerun_full_a_train-424692530435.json` | run COMPLETED on `t7-gpu0`; eval `device_uuid` == runner GPU; `outputs.device_used` = cuda:0 and `raw_metrics.device_used` = cuda:0. The first run's a2 FAIL does not recur on this tree |
| (b) search run, evaluation ON (L0=1/L1=2 ep, pop 2, gen 2, seeds 101/202, GA+Random) | **PASS** | `b-validate`, `b-create`, `b-search-eval-on`; `dbviews/rerun_full_b_run-6b46da7fd4a3.json` | COMPLETED in 7.97 min: 32 CANDIDATE_TRAIN SUCCEEDED + 8 PROPOSE_GENERATION. 32 EVALUATED rows, all latency_source=measured (7.9–15.3 ms), fitness 0.397–0.700, `raw_metrics.device_used` = cuda:0 in every row. Promotion: 16 PROMOTION_ELIGIBLE (L0), 16 SEARCH_RUNG_FINAL (L1) |
| (h) FINAL_RETRAIN short run | **PASS** | `h-create`, `h-final-retrain`; `dbviews/rerun_full_h_run-9f31f93d3de4.json` | child run COMPLETED, finalist_count 4, 4 FINAL_RETRAIN SUCCEEDED, 4 EVALUATED |
| (c) pause during GPU training → resume → full-state resume | **PASS** | `c-pause-request`, `c-paused`, `c-resume-request`, `c-full-state-resume`; `dbviews/rerun_full_c_*` | paused at the epoch-3 boundary (`PAUSED_AT_EPOCH`, run PAUSED, no child left). Resume: attempt 2 `resumed_from=…/full_e0003.pt`, 8/8 epochs, 480 steps / 3840 samples (continuous counters), EVALUATED |
| (d) cancel | **PASS** | `d-cancel-request`, `d-cancelled`; `dbviews/rerun_full_d_*` | run CANCELLED, job CANCELLED / RUN_CANCELLED, child pid gone |
| (e) SIGKILL runner → lease expiry → sweeper requeue → attempt+1 → idempotent commit | **PASS** | `e-sigkill-runner`, `e-lease-expired-requeued`, `e-runner-restart`, `e-reclaimed-attempt-plus-1`, `e-replay-idempotent` | runner killed (exit -9), child died; requeued after 58.1 s. The restarted runner's journal replay of the stale attempt-1 result got LEASE_LOST and the result was discarded. Attempt 2 resumed from `full_e0002` and SUCCEEDED (8 epochs, 480 steps / 3840 samples). Explicit replays: attempt 1 → 409 LEASE_LOST; attempt 2 → 200, identical to the stored response, job unchanged |
| (f) local transfer + downloads through the gateway | **PASS** | `f-store-contents`, `f-download-via-gateway`; `dbviews/rerun_full_f_store_listing.json` | 201 store objects, all digests and layout verified, 0 of 137 checkpoints missing from the store, 85 runner artifacts in the store. Downloads: evaluation.json (`other`), gt and tp panels, and a `training_full` checkpoint (42.1 MB) → all 200 with digest match. The first run's f2 FAIL does not recur. The core-written architecture YAML (not in the store) and the export bundle also download with digest match |

## GPU_PATH_VALIDATED recommendation

**GPU_PATH_VALIDATED: YES (with stated limits)** for TREE_HASH 9dafe4d0…. On this one tree, every T7 item of the first
run (a, b, c, d, e, f, h) passes again. The three first-run failures are closed: device_used/device_uuid recording
(a2), store-served gateway downloads with no false MISSING (f2), and BENCHMARK through the owner script (g2). The
DD-A52 uuid-based NVML lookup is shown correct on a non-zero GPU.
Limits to state in the release notes:
1. TensorRT is BLOCKED_BY_ENV (not in the wheelhouse).
2. The console queue does not run BENCHMARK; it is an owner script only (DD-A49 c).
3. CANDIDATE_TRAIN on a non-zero GPU was not run (see Scope). The search run in the second table used GPU 0 only.
4. The first run's G2-2, G2-3 and G2-4 results (T2-GPU equivalence, resume equivalence, RLIMIT, Docker) were not
   repeated on this tree.

## Observations (not counted as failures)
- `bench_host_gpu.py` labels the target `host-gpu-0` even when run with `CUDA_VISIBLE_DEVICES=1`: it is the logical
  index of the only visible device. The uuid in the record is GPU 1's, so the hardware is identified correctly. Only the
  label can mislead a reader.
- In the benchmark job outputs, `runtime_status` gives TensorRT as `FAILED`, while the record's top-level
  `tensorrt.status` is `BLOCKED_BY_ENV` and the engines are `BLOCKED`. The two vocabularies differ for the same cause.
- Item a: AP 0.0 on the tiny synthetic set after 2 epochs, the same as the first run. This is not an accuracy claim.

## Files
- `steps_rerun.jsonl` — one line per step: UTC, item, ok, duration, request ids, checks, facts, nvidia-smi.
- `t7_rerun_summary_20261027T180728Z.json` — item verdicts, GPU windows, pids/exit codes (first table).
- `t7_rerun_full_summary_20261027T181305Z.json` — the same for the second table; its logs are
  `logs/20261027T181305Z_*` and `logs/t7_transcript_20261027T181305Z.log`, its views `dbviews/rerun_full_*` and
  `snapshots/rerun_full_*`.
- `tree_hash_rerun.json`, `nvidia_smi_final_rerun.txt`.
- `logs/20261027T180728Z_*` — core/owner/gateway/runner logs (both runners append to `runner.log`), job-child stderr
  of GPU 0 and of GPU 1 (`*_child_gpu1_*`), probe log, transcript `logs/t7_transcript_20261027T180728Z.log`.
- `logs/rerun_install_owner_offline.txt`.
- `dbviews/rerun_*`, `snapshots/rerun_*`.
- `G2_RERUN_bench/` — benchmark record, `benchmark.json` and stdout.
- `harness/t7_rerun.py`.

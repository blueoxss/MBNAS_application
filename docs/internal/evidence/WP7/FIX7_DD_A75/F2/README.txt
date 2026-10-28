F2 (DD-A75, fix round 7): owner integration smoke on a CPU-only fleet - evidence index. Not a summary file of the round.

RESULT
  (a) bwrap sandbox WITHOUT /dev/nvidia* (P2's T4 flags), fresh offline install of the owner ZIP 53e6a1f0..., only
      scripts/dev/wp3_three_process_smoke.py replaced: verify.sh --profile owner --suite integration -> PASS 17/17,
      fleet mode CPU-only (a_cpu_only_sandbox/).
  (b) the same with the host's /dev/nvidia* nodes bound into the sandbox, CUDA_VISIBLE_DEVICES="": PASS 17/17, fleet
      mode GPU (b_gpu_nodes_visible_sandbox/); no sandbox process ever appeared among the GPU compute processes
      (gpu_compute_apps_monitor.txt, 49 samples).
  baseline: the ORIGINAL smoke in the (a) sandbox conditions -> FAIL 14/16, the same two steps and codes as T4
      (baseline_original_smoke_cpu_only_sandbox/). Side by side with T4 and T6: step_table.txt.
  17 vs 16: the original smoke returns early when runner-training-via-gateway cannot create its run, so step
      artifact-download-from-gateway-store never runs on a CPU-only fleet (T4: 16 steps). With the fix the run is
      created on the CPU entry and the step runs: 17 = T6's count, same names and order.

WHY THE HOST PASSES TODAY (T6 attempt3, 17/17 with CUDA_VISIBLE_DEVICES="")
  The core's validation inventory with the default MBNAS_CORE_DEVICE_SOURCE=local is NVML of the core host
  (mbnas_model_runtime.provenance.env_lock.validation_devices -> pynvml). NVML ignores CUDA_VISIBLE_DEVICES, so on the
  host it lists host-gpu-0/1 (2 x RTX A6000) and the preset console.benchmark.device host-gpu-0 resolves; the
  evaluation-ON protocol of step validate-dataset-manifests-via-gateway is valid (validation only, no run), and the
  explicit training validates on host-gpu-0 while the job is executed by the smoke's CPU-only runner (registered
  device "cpu", job result device_used=cpu: core_db_jobs.txt). In the sandbox nvmlInit fails (no /dev/nvidia*, rc 9),
  the local list is empty and DD-A62 (b) falls back to the registered CPU runner's "cpu" entry: host-gpu-0 is then
  DEVICE_UNAVAILABLE and, the fleet being CPU-only, the evaluation-ON protocol is also EVALUATION_REQUIRES_CUDA.
  nvidia-smi plays no part (pynvml only); the owner worker does not take part in validation.

CHANGE (owner-only members; scripts/dev/** and tests/owner/** are not in the public/registration ZIPs)
  scripts/dev/wp3_three_process_smoke.py (diff: F2.diff) - the smoke asks the running stack for the device inventory
  (GET /api/v1/health through the gateway, before the first device-dependent step; not a step itself) and prints the
  mode and why in the evidence header ("# fleet mode:", "# device discovery:") and the step log. GPU mode = every step
  exactly as before (preset device, unchanged assertions). CPU-only mode = the same steps with the CPU entry: step
  validate-dataset-manifests-via-gateway asserts 9 named checks (cpu_checks in the step line): refused at validate with
  exactly EVALUATION_REQUIRES_CUDA on console.search.evaluation_enabled, the CPU entry resolved, no resolution claim, no
  validation row stored, validation_host = gateway, core used the three per-split manifests (3/2/2 images) and the
  gateway computed all three split hashes + one class-mapping hash (read back from its manifest cache);
  runner-training-via-gateway runs on the CPU entry with the GPU-mode assertions. UNKNOWN (no or empty inventory) = the
  GPU-mode steps unchanged (they fail). No step skipped, no BLOCKED_BY_ENV needed; verify.sh unchanged.
  tests/owner/qa/test_wp3_smoke_fleet_mode.py (new, 40 tests) - classification of real health answers, the CPU verdict
  passes only for the by-design answer, real core app: the production health projection classifies like the validator
  and the validator answers the smoke's protocol on the CPU entry with exactly the expected refusal.

FILES
  COMMANDS.txt                 exact commands, order, timestamps, exit codes
  F2.diff                      the change set
  step_table.txt               T4 / F2 baseline / (a) / T6 / (b), all 17 step names
  a_cpu_only_sandbox/, b_gpu_nodes_visible_sandbox/, baseline_original_smoke_cpu_only_sandbox/:
    verify_console.txt, verify.json, integration.log, integration_smoke.txt (verify's evidence), device_discovery.txt
    (the mode lines), core_db_jobs.txt (job device_used, resolved benchmark device; GPU uuids not copied),
    install_owner_offline.txt, swap.txt (sha256 before/after of the only replaced file)
  harness/                     sbx_f2.sh (bwrap launcher) + sbx2_vs_sbx_f2.diff, nvml_probe.py + nvml_probe_output.txt,
                               gpu_monitor.sh, mutation_control.sh, step_table.py, assemble_evidence.py
  pytest/                      P1 new tests 40/40, P2 tests/public/ops 65 passed 2 deselected (incl. test_ops_role_env.py
                               2/2: the brief's path tests/public/packaging/ does not exist, the file is in tests/public/ops/),
                               P3 DD-A60/A62 CPU semantics 21/21, P4 tests/owner/qa 313 passed 1 skipped, mutation control
  source_tree_hash/            before 1b4bd1a4..., after 26e02259...: the only changed hashed file is
                               mbnas_core/console/projection.py, written 04:55:10Z by the concurrent F1 work, not by F2
  canary_scan.txt, canary_positive_controls.txt   leak scan of this directory (PASS) and its positive controls (FAIL as expected)
  Placeholders: <ROOT> TARGET_ROOT, <SP> scratchpad, <W> = <SP>/f2, <PY310_ENV> conda env mbnas310, <HOME>/<user>.

NOT VERIFIED HERE
  - A run directly on the host without any sandbox (T6 style): (b) reproduces the host's NVML view inside bwrap.
  - --device-source runners (CPU-only by construction with this CPU runner) and a real mixed GPU+CPU runner fleet:
    covered by the unit tests' classification only, not by a process run.
  - The unit suite of the installed trees (only the integration suite was run there; the new test is not in the ZIP).

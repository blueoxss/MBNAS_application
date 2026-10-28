# WP-2V round 1 evidence (2026-10-26, CPU, CUDA_VISIBLE_DEVICES="")
cwd /home/mgjeong/Desktop/etri_hd/mbnas_application, interpreter runtime/venv/bin/python -B
| step | file | exit | result |
|---|---|---|---|
| 1 install (-e --no-deps x5) + imports + 6 console --help | install.txt | 0 | all exit 0 |
| 2 import closure | import_closure.json | 0 | violations 0, allowlisted 0, allowlist [], excepted 2 (DD-A26) |
| 3 public | pytest_public.txt/.meta | 0 | 692 passed, 31 skipped (host data), 1 xfailed, 117 s |
| 4 owner gate | owner_gate.txt/.meta, run_owner_gate.sh | 0 | 1158 passed, 1 skipped, 0 failed, 585 s |
| 5 T3 matrix | pytest_runner_protocol.txt/.meta | 0 | 40 passed |
| 6 two-process smoke | smoke_console.txt, smoke.meta, two_process_smoke.txt | 0 | all steps PASS; (b) real yolo11n CPU: run COMPLETED, BASELINE_TRAIN SUCCEEDED, real checkpoints; EVALUATED row only in SYNTHETIC b2 |
| 7 web | web.txt, web_*.txt | 0/0/0 | check:types ok, tsc ok, vitest 504 passed |

Code changes (root-cause fixes):
- scripts/dev/check_import_closure.py: TRANSITIONAL_ALLOW emptied (DD-A25/A27 expiry).
- mbnas_core/runner_api/routes.py (E5): log text read from ProgressMessage.message (payload.message fallback) -> T3-23.
- mbnas_runner/daemon.py: S7-F1 `_gpu_ignored` + ancestor exclusion restored in wait_gpu_clear; D6 `_externalise_samples` moves per_sample/latency.samples to a registered evaluation_detail JSON artifact when the commit exceeds max_body_bytes-8 KiB -> T3-33; `_assigned_device` maps assigned `cuda:0` to `cpu` on a runner registered as CPU (interpretation, designer to confirm) -> smoke (b).
- Test adaptations: see ../TEST_ADAPTATIONS.md "WP-2V round 1".

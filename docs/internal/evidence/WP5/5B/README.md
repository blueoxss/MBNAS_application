# WP-5B evidence — artifact wiring (gateway store downloads, core records/locations, export, DD-A46/DD-A47 (d), T7 f2)

Date 2026-10-27 (UTC in the files). All commands: cwd TARGET_ROOT, `runtime/venv/bin/python -B`, `CUDA_VISIBLE_DEVICES=''`,
`PYTHONDONTWRITEBYTECODE=1`, `PIP_CONFIG_FILE=/dev/null`, `MBNAS_HOME=<ROOT>/runtime`, `MBNAS_TEST_ROOT=<ROOT>/runtime/test_root`.
No GPU, no `/data`, ephemeral 127.0.0.1 ports only. WP-4B / WP-4C (core projection, contract 1.5.0, schema v5) and
another agent editing `mbnas_runner` were working at the same time; their effect on the runs is classified in
`owner_gate_classification.txt`.

| file | what | result |
|---|---|---|
| `targeted_final.txt` | tests/public/server; the owner artifact/export set; the new WP-5B files; runner suites after the concurrent runner edits | 527 passed / 1 skipped / 1 xfailed; 166 passed; 41 passed; 54 passed (0 failed in the settled tree) |
| `run_public.sh` → `pytest_public.txt/.meta` | full public suite (WP-3C marker filter), final code | 1038 passed, 1 skipped, 1 xfailed, 0 failed (exit 0, 261 s) |
| `run_owner_gate.sh` → `owner_gate.txt/.meta` | full owner gate through the combined harness, final code | 1458 passed, 42 failed, 8 skipped, 1 xfailed — see `owner_gate_classification.txt` (21 = concurrent runner edit window, pass after it; 1 = test stub signature after DD-A46; 20 = WP-4B/4C in progress) |
| `wp5b_gateway_download_smoke.py` → `gateway_download_smoke.txt` | real processes: uvicorn core + owner worker + `mbnas-runner` (local transfer) + uvicorn gateway; REAL yolo11n CPU training created through the gateway; all 9 registered objects (6 artifacts, 3 checkpoints up to 31.6 MB) downloaded through the gateway with digest match and the NOT_READY → READY / server_store PRESENT transition; core-signed view URLs (core key ≠ gateway key) work without bearer, forged → 403; refresh; export with both architecture YAMLs | PASS, exit 0 (22 s) |
| `mutation_check_f2.txt` | the T7 f2 regression test against the pre-fix core behaviour (runner-object guard disabled) | mutant caught (core answered 404 MISSING) |
| `import_closure.json` | `scripts/dev/check_import_closure.py` | ok, 0 violations, 0 unresolved, 0 forbidden strings |

Scratch roots left for the designer's cleanup: `runtime/test_root/wp5b_smoke/2026102*` (2 × 49 MB). Test runs through the
shared harness also wrote the DD-A46 inputs cache under `runtime/gateway/gateway_inputs/` (1.1 MB, ≤256 entries; a
cache, safe to delete) because `harness.make_gateway_settings` does not set `manifest_cache_root`.

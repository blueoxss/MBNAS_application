# Round 4 evidence (2026-10-28 08:3x–10:2x KST)

FIX_ROUND_1 "최종 문서/주석 잔여" plus the coordinator's C09/C10/C11/DD-A65/DD-A66 additions. Implementation agent
claude-opus-5-5 with four forks (same model). CPU only; no GPU, `/data`, `etri_nas` or `build/source_snapshot` writes.
Paths in the files: `<ROOT>` = the project tree, `<SCRATCH>` = the session scratchpad, `<HOME>` = the home directory.
Summary and results table: `docs/internal/VALIDATION_REPORT.md`, section "Round 4".

| folder / file | content |
|---|---|
| `final_suites/` | the parent's re-runs on source tree `01c7d785…`: one `.txt` (tail) + `.meta` (command, UTC start/end, exit, start/end tree hash) per suite |
| `checks/docs_static_gates.txt` | check_docs, gen_registration_docs `--check`, import closure, placement coverage, env examples, npm notices `--check` |
| `checks/nginx_example_check.txt` | `nginx -t` and the routing probes of the owner-host core proxy example |
| `checks/web_recheck.txt` | the parent's independent re-check of the web track (gates, dist hashes, fresh build == shipped dist) |
| `t2/` | `t2_compare.py` on the current tree: report, snapshot guard, console, meta (raw outputs stay in the scratchpad) |
| `t7_post_gpu_tree_diff.txt` | package files changed since the GPU evidence tree `9dafe4d0…` (38 modified, 1 added, 1 removed) |
| `tests/` | tests track: the four new Python test files, per-file and suite runs, negative controls |
| `ops/` | ops-scripts track: start/stop/doctor/verify changes, three new ops tests, negative controls |
| `packaging/` | packaging track: packager/allowlist/generator changes, 21 new tests, determinism and package-data gate proofs, scratch builds |
| `../../WP6/ROUND4_WEB/` | web track: gates, mock e2e 210, dist hashes |
| `../../WP6/V4B_FINAL/` | the C07 evaluator's real-stack browser evidence for the previous final dist, copied and leak-scanned |

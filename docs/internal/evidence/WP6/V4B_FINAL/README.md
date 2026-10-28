# V4B_FINAL — browser e2e on the final release dist (copied from the C07 evaluator run)

Source: the independent C07 evaluation (`docs/internal/evidence/CRITERIA/C07/EVAL_1.md`, evaluator claude-opus-5-5,
2026-10-28 07:44–08:20 KST). The evaluator kept these outputs in the session scratchpad (`<SCRATCH>/wp3/c07/`); round 4
(C07 EVAL_1 D3) copied them here so that the browser evidence of the final dist no longer depends on the scratchpad.

**Which dist this covers.** The web dist inside the final ZIPs of 2026-10-28 07:0x: `mbnas_web/dist/assets/index-CY9ctObG.js`
(sha256 `6c3f29a2…`), `assets/index-CG8HuodM.css` (`1e0f94d5…`), `index.html` (`08772e74…`), taken out of the public ZIP
`ce34b821…` (byte-identical to the tree's dist at that time). It does **not** cover the round-4 dist
(`index-7f__3l0I.js`, `df0d6b2d…`: block-table link to the block detail, generalised badge tooltip); that one has the web
unit tests and the mock e2e only (`../ROUND4_WEB/`). A real-stack run on the new dist is part of P-final-2.

| run | what | result | files |
|---|---|---|---|
| web gates on the final sources | `check:types`, `typecheck`, `vitest run --no-cache`, `check:bundle` | exit 0 each; vitest 45 files / 593 passed; bundle hygiene 0 hits | `gates_mock/web_gates.txt`, `gates_mock/vitest_c07_verbose.txt`, `gates_mock/run_web_gates.sh` |
| mock e2e on the ZIP dist | project Playwright config imported by a scratch config (test dir, output dir, port 4199 and web server changed; no `npm run build`) | 208 passed (1.0 m), exit 0 (desktop-1280 and mobile-375) | `gates_mock/mock_e2e_full.txt`, `gates_mock/pw_mock.config.ts`, `gates_mock/run_mock_e2e.sh` |
| real stack V4 on the ZIP dist | `scripts/dev/web_real_stack.py` (core + owner worker + CPU runner + gateway serving the ZIP dist), Playwright `playwright.real.config.ts` | V4-01..11: 9 passed (1.3 m), exit 0; V4-12: 1 passed, exit 0; 42 screenshots; axe serious/critical 0 | `real_stack/e2e_real_v4_part1.txt`, `real_stack/e2e_real_v4_part2.txt`, `real_stack/e2e_steps.jsonl`, `real_stack/v4_state.json`, `real_stack/stack_log.txt`, `real_stack/screenshots/`, `real_stack/run_v4_e2e.sh`, `real_stack/stack_wrapper.py` |
| structure probe on the real stack | graph node click vs gene button click on a trained `yolo11n_user` candidate; edge list vs YAML `from` | same selected block and `?block=block-0012`; 24 genes = 24 nodes; 30 edges match `from` | `probe/structure_probe.json`, `probe/structure_probe.cjs`, `probe/*.png` |
| upload → revisions → export chain | V4-01..09 re-run plus an export check | 7 passed (48.9 s), exit 0; export ZIP architecture members == revision bytes, `SHA256SUMS` and `X-Bundle-Sha256` match | `export_chain/` |

Notes:
- `real_stack/stack_log.txt` starts with "# WP-3 three-process smoke … command: … wp3_three_process_smoke.py": that header was
  hard-coded in `scripts/dev/wp3_three_process_smoke.py` even when `web_real_stack.py` drove the stack (C07 EVAL_1 D4). Round 4
  changed the header to name the program actually run (`sys.argv[0]`); this copy keeps the original text.
- Redaction and scans (2026-10-28, round 4): the stack's runtime root (DB, keys, `secrets/runner.token`) and the stack's
  `stack.out` (it carries the per-start bearer line) were **not copied**. Host paths in the text files were replaced by
  `<ROOT>`, `<SCRATCH>` and `<HOME>`, and the needle names printed by the bundle-hygiene checker in `gates_mock/web_gates.txt`
  were replaced by `<needle N redacted>` (all 28 had count 0). `scripts/dev/canary_scan.py --profile public` over this folder,
  with the stack's runner token as an extra needle: 64 files, 0 hits, PASS. No bearer value or token-like string is present
  (the run scripts read the bearer at run time; `<redacted>` is their placeholder).
- Not copied: the evaluator's pytest temp folders, Playwright `test-results/`, the mock run's 137 screenshots (45 MB) and the
  extracted ZIP dist (its three files are byte-identical to the tree dist of that time, sha above).

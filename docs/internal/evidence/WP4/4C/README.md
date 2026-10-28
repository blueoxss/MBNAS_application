# WP-4C evidence (projection tests, contract 1.5.0 tests, folded V3 items) — 2026-10-27

Agent: WP-4C (opus). Interpreter `runtime/venv/bin/python`, `CUDA_VISIBLE_DEVICES=""`, `PYTHONDONTWRITEBYTECODE=1`,
`-p no:cacheprovider`. No GPU, nothing under /data, only ephemeral 127.0.0.1 ports (in-process uvicorn threads).
Canary token values never appear in this directory (the owner ZIP ships `docs/internal/**`; `canary_scan.py` over
this directory and `tests/owner/projection`: PASS, 0 hits).

| file | what |
|---|---|
| `run_gates.sh` | public suite + owner gate (same commands as WP-3C `run_public.sh` / `run_owner_gate.sh`, `--timeout=180`) |
| `pytest_public_run*.txt/.meta`, `owner_gate_run*.txt/.meta` | outputs of `run_gates.sh <tag>` (see the final report for the counts) |
| `t8/t8_canary_scan_{public,pattern,owner}.json` | `scripts/dev/canary_scan.py` reports on the captured responses (written by `tests/owner/projection/test_t8_canary_scan.py` with `MBNAS_TEST_EVIDENCE_DIR`) |
| `t8/t8_canary_manifest.json` | the manifest used, token values redacted |
| `negative_control_sse_export.txt` | the suite with the projection replaced by the identity for SSE + export (`MBNAS_TEST_WP4C_MUTATION=sse,export`): the expected checks fail |
| `dda43b_public_bundle_sim.txt` | DD-A43(b): affected public tests in a simulated public bundle without the owner-only tools |
| `t3_auth_flake_fix.txt` | DD-A47(e): `test_expired_or_revoked_token_is_401` alone 3x after the fix + the whole file |
| `ops_flaky_reruns.txt` | V3 item 10: the two ops tests 3x each alone + their file 3x (not reproducible, no change) |
| `wp2_two_process_smoke.txt`, `.cmd.txt` | V3 item 7: `scripts/dev/wp2_two_process_smoke.py` exit 0 after enabling the owner debug mount |

## Suite layout (`tests/owner/projection/`)

* `projection_world.py` — the canary-seeded world (session fixture `world`): real core + gateway apps in one process
  (`harness.combined_app`), a GA + IndependentRandom + baseline run created through the gateway and drained by the
  in-process owner worker (stub handlers), then SQL seeding of every owner-only member (64 seeds: 51 schema paths =
  58 concrete OWNER_ONLY paths minus 7 Archive*, 10 owner-only non-schema rows, 3 value-mapped members 1.5.0 removes)
  and public markers; every public response captured once (gateway `/api/v1` with the user bearer and
  `/core/v1/console` with the server and admin principals; SSE replay from seq 0 over real HTTP; export zips), plus
  the owner routes (admin / server / runner / no token) and a recording tap on the projection entry points.
* `test_canary_projection.py` — 0 markers per route group x side (snapshot, candidate detail, SSE replay, export
  default + `?profile=public`, health, registry, validate, runs, presets, explicit structure check), generations,
  admin console mount, 1.5.0 contract validation of every public body / event, owner-only member names absent.
  Note: the structure-check response carries no owner data even before projection (4B's check path does not copy
  lineage / block provenance), so for that route the check is a plain 0-marker check, not a positive canary path.
* `test_public_golden.py` — PUBLIC members survive with their values (lineage, audit, blocks, structure, raw_metrics,
  checkpoints, run budget, full resolved protocol, generations, workers, generated candidates, log payload, health,
  registry evidence, validate, export).
* `test_owner_routes.py` — owner audit / export / registry: raw markers for admin only; 403 server and runner tokens,
  401 without a token, 404 through the gateway; tap positive control (every schema-level seed reached the projection).
* `test_t8_canary_scan.py` — T8 with the real scanner on the captured bodies.
* `test_config_revisions.py` + `revision_handlers.py` — upload -> training -> executed revisions, exact YAML bytes.
* `test_contract_150.py` — 1.5.0 schema rules (closed projected components, mirrors forbid extra keys, new
  components/paths, document-level exclusion of all 58 OWNER_ONLY paths, golden members declared).
* `test_value_rules.py` — scrub / ids / paths / urls / labels of `x-public-projection.value_rules`.

## Results (2026-10-27, final tree as of ~16:30 KST; WP-4B was still editing core files during the session)

| check | command / file | result |
|---|---|---|
| projection suite | `pytest tests/owner/projection` | 259 passed (47 canary, 16 golden, 14 owner routes, 4 T8, 9 revisions, 157 contract 1.5.0, 12 value rules), ~19 s, stable over repeated runs |
| negative control | `negative_control_sse_export.txt` | 21 failed / 233 passed with SSE + export projection disabled (the expected checks) |
| contract tests | `pytest tests/owner/unit/test_schemas_contract.py tests/public/contracts/test_contract_history.py` | 193 + 37 passed |
| T8 scanner | `t8/*.json` | public bodies 151 files / 5.2 MB: 0 hits (manifest + pattern); owner control 43/43 areas, 417 hits |
| public suite run2 (16:22) | `pytest_public_run2.*` | exit 0: 1038 passed, 1 skipped, 1 xfailed (pre-existing DD-A29 strict xfail), 44 deselected |
| public suite run3 (final, 16:54) | `pytest_public_run3.*` | exit 0: 1070 passed, 1 skipped, 1 xfailed (same DD-A29 row), 44 deselected |
| owner gate run1 (15:59, tree in flux) | `owner_gate_run1.*` | 1468 passed, 21 failed: owner tests pinning 1.4.x behaviour that 1.5.0 changed (updated by WP-4C, see the final report) + 1 transient 4B state |
| owner gate run2 (16:27) | `owner_gate_run2.*` | exit 0: 1500 passed, 0 failed, 8 skipped, 1 xfailed (the old SECURITY_BASELINE 4-9 strict xfail, collected before its update) |
| owner gate run3 (final) | `owner_gate_run3.*`, `pytest_public_run3.*` | see the meta files / final report |
| harness isolation (coordinator item 2) | tests run from a scratch cwd | 1260 passed; 0 files written outside runtime/test_root (manifest cache, gateway inputs and audit log pinned to tmp) |
| DD-A43(b) bundle simulation | `dda43b_public_bundle_sim.txt` | exit 0: 399 passed, 216 skipped (215 "owner-only tool not in this bundle" + 1 pre-existing), 0 failed |
| DD-A47(e) flake | `t3_auth_flake_fix.txt` | alone: before 0/3, after 3/3; file 6 passed |
| V3 item 10 ops | `ops_flaky_reruns.txt` | 3/3 + 3/3 alone, file 11 passed x3: not reproducible, no change |
| WP-2 smoke | `wp2_two_process_smoke.txt` | exit 0, 11/11 steps PASS (re-run on the final tree) |
| WP-3 smoke | `wp3_three_process_smoke.txt` | exit 0, 17/17 steps PASS (step 8 export zip digest; step 9 a runner-registered artifact downloaded from the gateway store: body = header = registry sha256, NOT_READY -> READY, PRESENT@server_store) |
| import closure | `scripts/dev/check_import_closure.py` | exit 0: 0 violations / unresolved / forbidden strings (1 documented exception left: base.py legacy hook) |

## Session 2 (10-28 03:0x KST, after the session restart)

| check | file | result |
|---|---|---|
| 5B test order (DD-A53) | `tests/owner/artifacts/test_artifact_wiring.py` | download + verify first (NOT_READY/bearer_fetch before, READY/signed_url after); tests/owner/artifacts 12 passed |
| T2 comparator rule (DD-A53 c) | `scripts/owner/t2_compare.py` PROTOCOL_HASH rules | before: T2-7/8/10 MISMATCH (227/175/18, all protocol-hash derived); after: 10/10 EXACT_MATCH/ALLOWED_DIFF, 0 MISMATCH (`t2/`, `t2_compare.log`); comparator tests 22 passed (7 new negative/positive rows) |
| T2 pytest | `pytest_t2.txt` (`MBNAS_TEST_T2=1 pytest tests/owner/t2`) | 32 passed |
| final gates (10-28 03:08-03:28, nobody else editing) | `pytest_public_run4.*`, `owner_gate_run4.*` | public exit 0: 1070 passed, 1 skipped, 1 xfailed (DD-A29), 0 failed; owner exit 0: 1510 passed, 8 skipped, 0 failed, 0 xfailed |
| smokes (final tree) | `wp2_two_process_smoke.txt`, `wp3_three_process_smoke.txt` | WP-2 exit 0 (11/11); WP-3 exit 0 (17/17, step 9 store download) |

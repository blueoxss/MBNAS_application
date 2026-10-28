# WP-7D evidence — package.sh / allowlists / FILE_MANIFEST / canary (T8 tools)

Run: 2026-10-27 14:05–14:08 KST (final run, after the DD-A41/DD-A43 allowlist changes), TARGET_ROOT tree as it was at that moment (other waves were still editing; the final
ZIPs are to be rebuilt after all waves land). Interpreter `runtime/venv/bin/python` (3.10.22), `CUDA_VISIBLE_DEVICES=""`,
`PIP_CONFIG_FILE=/dev/null`, node v24.20.0 (only for the WP-6A bundle gate). No network, no GPU.
All files were produced by `run_evidence.sh` (copied here; the relocation and negative-control trees live in the session
scratchpad). Every `$` line in the files is the exact command, followed by its output and `exit=<code>`.

| file | what | result |
|---|---|---|
| `00_env.txt` | interpreter/node/VERSION, source-tree sha256 of the installed packages, `mbnas_web/dist` listing | VERSION 0.1.0 (DD-A41), dist present (index.html + 2 assets) |
| `10_package_<profile>.txt` + `.report.json` | `bash scripts/package.sh --profile <p> --reproducible` for public, owner, registration | all three exit 0; two builds IDENTICAL for each profile; 0 violations, 0 warnings; WP-6A bundle gate pass |
| `11_dist_listing.txt` | `dist/<profile>/` listing and `sha256sum -c` of each `.sha256` | one ZIP + `.sha256` per profile, all three `sha256sum -c` OK |
| `20_scan_release_<profile>.txt/.json` | `canary_scan.py` on each release ZIP (generic, and with the manifest + `--profile`) | PASS ×5: public/registration 0 hits (528 / 538 files incl. nested wheels); owner (1178 files) 0 canary hits, host facts reported as informational (owner-only docs) |
| `30_canary_<profile>.txt` + `.scan.json` | `bash scripts/package.sh --profile <p> --canary` (copy → plant → build → scan) | public/registration: 0 hits, PASS; owner: all 8 planted areas found (positive control, nested core wheel included), no `MANIFEST_SELF`, PASS |
| `31_canary_manifest_summary.txt` | areas, `expected_in` / `must_not_in`, planted locations (no token values) | 8 areas + `MANIFEST_SELF` |
| `40_relocation_public.txt` | public ZIP unzipped into a path with spaces | no path naming the private package; no owner-only/excluded material; exactly 4 public wheels; the only `import mbnas_core` text = the two Dockerfile negative assertions (`! python -c "import mbnas_core"`); FILE_MANIFEST 382 entries (+ itself) = files on disk, 0 digest mismatches; scan PASS |
| `45_owner_extracted_packaging_tests.txt` | `pytest tests/public/packaging` inside the extracted owner bundle | 21 passed, 21 skipped (source-tree-only tests skip: no `build/allowlists` in a bundle) |
| `50_negative_controls.txt` | scratch copy of the real tree with deliberately broken allowlists | (a) host facts in three public test files → 10 violations, exit 1, nothing published; (b) owner tests + internal docs + core env example let through → canary build exit 1 with OWNER_TEST, OWNER_DOC, OWNER_SECRET hits |
| `60_pytest_packaging.txt` | `pytest tests/public/packaging -v` (synthetic tree, real allowlists) | 42 passed |
| `70_import_closure.txt` | `scripts/dev/check_import_closure.py` + `pytest tests/public/infra` | 0 violations / 0 unresolved; 32 passed |

Earlier runs of the same session (not re-recorded here) also showed: the reproducibility check reports DIFFER when the
tree changes between the two builds (an agent edited `mbnas_runner/.../transfer.py` mid-build) — after that the second
pass re-stages the first pass's source snapshot; the first public build flagged 12 contract migrate scripts with an
original-host path (WP-7C has since scrubbed them; the temporary deny was lifted per DD-A41).

Scope applied (WP-7D task, DD-A41, DD-A43): public = 4 public packages + wheels, web sources + prebuilt dist (minus
`mbnas_web/tests/fitness_oracle.ts` and the 46 web test/e2e files that import it, directly or transitively — computed
by the `[deny-importers]` rule and listed in PACKAGE_PROFILE.json), tests/public (minus noted files) + the core-free
part of tests/_shared, the root pyproject.toml (pytest configuration), public scripts/config examples/deploy/docs/
examples, README, VERSION, THIRD_PARTY_NOTICES; registration = public + docs/registration (+ generated FILE_LIST.md); owner = public + core package
and wheel, owner tests, all of tests/_shared, all scripts, deploy/owner, requirements-owner, LICENSE_STATUS.md,
docs/internal (minus canary manifest and SOURCE_SNAPSHOT_* manifests). Noted exclusions are listed with reasons in each
ZIP's PACKAGE_PROFILE.json (`exclusions_with_reason`).

확인 못 함 (not verified here): installing and running the unzipped bundles (WP-7V T6/T4/T5); offline install; that the
public test suite passes inside a public install (several public tests need `tests/_shared/harness.py` or `scripts/dev`
tools, which are not shipped — see the WP-7D report); runtime T8 targets (API responses, logs, process environments).

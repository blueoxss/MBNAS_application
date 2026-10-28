# T8 leak — P1-final runtime re-run (final ZIPs, 2026-10-28 07:1x KST)

**Verdict: PASS.** These checks all held:
- The public gateway health shows only its own roots.
- There are 0 secret, 0 canary and 0 owner-side hits in any response, service log or process environment.
- After the two test-controlled strings are normalized (described below), responses and service logs have 0
  host-fact hits.

Two kinds of host-fact hits remain, and neither is product data. The process environments carry `OLDPWD`, the cwd
of the shell that ran `start.sh`. The install logs echo the paths that the operator passed to `install.sh`.

Run: the final ZIPs (`../SHA256SUMS_final.txt`) were installed into `runtime/test_root/t6/final/{mbnas owner final,
mbnas public final, mbnas registration final}/mbnas_application`. The owner core was on 18800. The public gateway
(18810) and the registration gateway (18812), each with its runner, were connected to it. Every service was
started through `start.sh` with a service-like environment: PATH, LANG, `HOME=<tree>/runtime/home`,
`CUDA_VISIBLE_DEVICES=''`. A systemd unit would provide the same, and it keeps the operator's shell variables out
of the scan. The start ran from the external cwd `<scratchpad>/t6final`. Scripts: `../scripts/final_driver.py`
(phase `up`), `../scripts/t8_scan_final.py` and `../scripts/t8_scan_reinvoke.py`.

## Captured
- **public gateway responses** (`responses_public/`):
  - health with and without a token, presets, registry, web `/`;
  - two uploads and two structure-checks (PASS, and FAIL with `SHAPE_MISMATCH`);
  - validate and run create;
  - from the generation-only run `run-3b4cc8f33a39` (COMPLETED, 2 candidates): run, snapshot, candidate detail,
    config-revisions, the export zip (11,637 B, members scanned), and the runs list.
- **registration gateway responses** (`responses_registration/`): health and presets.
- **service logs** (`logs_public/`, `logs_registration/`): `gateway.log`, `runner.log` and the gateway `audit.jsonl`.
  Install logs were scanned separately.
- **`/proc/<pid>/environ` and `cmdline`** of both gateways and both runners, plus the gateway re-started from cwd `/`.
  These stayed in the scratchpad (mode 0600); only the key names were recorded.

## Needles (120)
- canary tokens;
- host facts #1–#6, including the original host's GPU UUID;
- the secret-like values of the owner `core.env`, `gateway.env` and `runner.env`, and of the public and registration
  `gateway.env` and `runner.env`. The /proc scans use all of them too: the services read `--env-file`, so no secret
  value belongs in an environment;
- 7 owner-side strings: the owner tree root, the core DB directory, the owner config directory, the core log
  directory, the core output root, the core dataset root, and `mbnas.sqlite3`.

## Normalization (the verdict uses the normalized scans; raw results are kept)
Only two strings are replaced:
1. the scanned tree's own root, which is the customer's install path, named by its own logs and environment;
2. the `output_root` that the test client put in its protocol, `<owner core runs>/t8_supplied`, echoed by validate,
   run, snapshot and the export protocol files.

The owner core's own output root is the parent of the supplied path and is not replaced. A relay of the core's
allowlists, the defect from the first pass, would therefore still fail the scan.

## Results (`*.json` / `*.txt` here)
| scan | files | raw hits | normalized hits | what is left |
|---|---|---|---|---|
| public responses + service logs | 57 | 15 (6 account name = the public tree root in `gateway.log`/`runner.log`; 9 owner-runs needle = the client-supplied output_root echoed) | **0** | — |
| registration responses + service logs | 6 | 6 (the tree root in the logs) | **0** | — |
| public /proc (gateway, runner) | 4 | 14 | 2 | `OLDPWD` = the invoking shell's cwd (`<scratchpad>/t6final`) |
| registration /proc | 4 | 14 | 2 | the same `OLDPWD` |
| public gateway /proc after re-invocation from cwd `/` | 2 | 6 | **0** | `OLDPWD=/` |
| install logs (public / registration) | 1 / 1 | 20 / 20 (capped at 20 per needle) | 20 / 20 | operator-supplied arguments: `--wheelhouse <source>/build/wheelhouse` and `--python <conda>/python3.10` |

These results also hold:
- Secrets: 0 hits in every scan. No `MBNAS_*` key other than `MBNAS_HOME` is in any environment. The environment
  keys are CUDA_VISIBLE_DEVICES, HOME, LANG, MBNAS_HOME, OLDPWD, PATH, PWD, PYTHONDONTWRITEBYTECODE,
  PYTHONNOUSERSITE, PYTHONUNBUFFERED and SHLVL.
- Canary tokens: 0. Owner-side strings: 0 after normalization.
- Health `allowlists` is `{"dataset_roots": [], "output_roots": [], "archive_roots": []}` on the public gateway, on
  the registration gateway and after re-invocation. These are the gateway's own (unset) roots, as DD-A59 (a)
  requires. The first pass relayed the core's roots.
- Private package: `pkgutil.find_loader` and `find_spec` return None for `mbnas_core`, and it is not in
  `sys.modules` after the public apps are imported, in both the public and registration venvs
  (`../T6/public_no_mbnas_core_loader.txt`, `../T6/reg_no_mbnas_core_loader.txt`). No `mbnas_core` appears in the
  `/proc/<pid>/maps` of any public process.
- Evidence self-check: `canary_scan.py --no-host-facts --secrets-env <every env file of the final trees>` over
  `WP7/7V_FINAL/` and `VALIDATION_REPORT.md` found 0 hits in 217 files (`evidence_self_scan_secrets.txt`).

## Minor note (not a leak; owner of `scripts/start.sh`)
`start.sh` changes into ROOT with `cd`, so the service inherits `OLDPWD`, which names the operator's working
directory. That is a fact of the customer's own host. It is not owner data, and systemd units do not set it.
`unset OLDPWD` before the `exec` would remove it.

## Not covered (확인 못 함)
The capture includes no training job, so the runner checkpoint/artifact responses and signed-URL downloads were
not scanned here. WP-8 covered those in T4/T5. The SSE stream was not captured either.

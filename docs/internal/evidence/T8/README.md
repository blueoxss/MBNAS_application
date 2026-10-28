# T8 leak test — evidence (WP-7V, 2026-10-28 KST)

Verdict: **FAIL**. One real leak: the public gateway's `GET /api/v1/health` relays the core host's
absolute `dataset_roots` and `output_roots` when the gateway has no roots of its own, which is the default after
`install.sh --profile public`. The ZIP scans, secret values, the private-package checks and the other captured
responses, logs and environments are clean (details below).

Environment: CPU only (`CUDA_VISIBLE_DEVICES=""`), no GPU work. Scanner: `scripts/dev/canary_scan.py` from the source tree
(DD-A43 c), manifest `docs/internal/canary/CANARY_MANIFEST.json`. Secret values are needles only: they were never
printed and never copied here. `/proc/<pid>/environ` stayed in the session scratchpad (mode 0600) and only the key names
were recorded.

## 1. Release ZIPs (build-time leak)
`python -I scripts/dev/canary_scan.py --manifest docs/internal/canary/CANARY_MANIFEST.json --profile <p> --json <out> <zip>`
(outputs in `../WP7/7V/canary_scan_<p>.{txt,json}`)

| ZIP | files scanned (nested wheels included) | canary / secret hits | host facts | status |
|---|---|---|---|---|
| public `529243cf…` (rebuilt with offline flag) | 602 | 0 | 0 | PASS |
| public `4174060d…` (the tested build, T6/offline) | 602 | 0 | 0 | PASS |
| registration `d5748c13…` | 612 | 0 | 0 | PASS |
| owner `ba8ed91f…` | 1596 | 0 canary | 2294, not failing for the owner profile (owner-only docs/tests) | PASS |

The packager's own gate also passed for all three builds: 0 violations and 0 warnings (`../WP7/7V/package_<p>.log`). In the extracted public
tree, `verify.sh --profile public --suite package` with `MBNAS_CANARY_SCAN` set to the source scanner checked 453/453
manifest entries (size and sha256) and found 0 hits (`../T6/verify_public_package.json`).

## 2. Runtime captures (T6 stacks running, public gateway on 18710 → owner core on 18700)
Captured by `../T6/t6_t8_driver.py` (phase `up`) and scanned by `../T6/t8_scan.py`:
- responses (`responses/`): health (with and without a token), presets, `/` (web index), upload, structure-check (valid
  and broken YAML), protocols/validate, run create, run, **snapshot**, **candidate detail**, config-revisions,
  **export zip**, runs list. These come from a generation-only run `run-e15ff83bc3b0` (COMPLETED, 2 candidates).
- public logs (`logs/`): gateway.log, runner.log, gateway audit.jsonl, plus the public install log.
- the public gateway and runner processes: `/proc/<pid>/environ` and `cmdline`, kept in the scratchpad only.

Needles: canary tokens, the 5 fixed host facts, the secret-like values of the owner `core.env`, `gateway.env` and `runner.env`
together with the public `gateway.env` and `runner.env` (for /proc only the owner files), and 4 owner-side strings: the owner core DB directory,
the owner config directory, the owner core log directory, and `mbnas.sqlite3`.

| scan | files | canary | secret values / owner strings | host-fact hits | evidence |
|---|---|---|---|---|---|
| responses + logs, raw | 56 | 0 | **0** | 37 (account name) | `t8_runtime_raw_responses_logs.*` |
| /proc environ + cmdline, raw | 4 | 0 | **0** | 56 | `t8_runtime_raw_proc.*` |
| responses + logs, normalized¹ | 56 | 0 | 0 | 24 | `t8_runtime_normalized_responses_logs.*` |
| /proc, normalized¹ | 4 | 0 | 0 | 50 | `t8_runtime_normalized_proc.*` |

¹ The normalized copy replaces only the public tree's own root and the `output_root` that the test itself put in the
protocol. The remaining hits fall into three groups:

| where | count | classification |
|---|---|---|
| `responses/health.json` → `allowlists.dataset_roots` / `allowlists.output_roots` | 1 line, 2 paths | **LEAK**: the owner core's `MBNAS_CORE_DATASET_ROOTS` / output roots (absolute paths of the core host). `mbnas_server/api/relay.py` `health()` replaces the core's allowlists only when the gateway has `dataset_roots` or `output_roots` itself. Without them it passes the core's allowlists through. The 1.5.0 contract states "Health.allowlists customer-host roots only". |
| `responses/export.zip` → `protocol/requested.json`, `protocol/resolved.json` | 3 | not a leak: the `output_root` that the client sent in its own protocol, echoed back |
| public install log | 20 | not a leak: customer-side facts (the `--wheelhouse` path and the interpreter given to install.sh) |
| /proc environ | 50 | not a leak of the product: the operator session environment inherited through `start.sh` (HOME, PATH, CONDA_*, HF_* cache dirs, SSH_CONNECTION, …) and `MBNAS_HOME` (the public tree). No `MBNAS_*` secret key is present in any environ; the services read their env files with `--env-file`. |

Also confirmed:
- No owner token, signing key, ticket key, DB path or config path appears in any public response, log or process
  environment. The secret-value needles came from the owner and public env files: 105 needles for responses and
  logs, 97 for /proc.
- Private package. `pkgutil.find_loader("mbnas_core")` and `importlib.util.find_spec("mbnas_core")` both return
  None in the public venv. After `import mbnas_server.api.app, mbnas_runner.daemon` no `mbnas_core*` module is in
  `sys.modules` (`../T6/00_public_no_mbnas_core_loader_rerun.txt`, run from an external cwd). The public gateway's
  and runner's `/proc/<pid>/maps` contain no `mbnas_core` (a weak check, because maps lists native libraries only).
- The same holds in the offline-installed public tree under `unshare -rn`: `find_spec("mbnas_core")` is None
  (`../WP7/7V/unshare_network_proof.txt`).
- Evidence self-check: `canary_scan.py --no-host-facts --secrets-env <owner core/gateway/runner + public, registration and offline-tree env files>`
  over `T6/`, `T8/`, `WP7/7V/` and `VALIDATION_REPORT.md` found 0 hits in 141 files (`evidence_self_scan_secrets.txt`).

## 3. Not covered (확인 못 함)
- A run with a real training job (runner-produced checkpoints and artifacts) was not part of this capture. The
  generation-only run has no checkpoint artifacts, so the signed-URL and artifact-download responses were not scanned.
- The SSE `/runs/{id}/events` stream was not captured.
- The owner gateway's responses were not scanned. That gateway is owner-side, so it is out of scope.

## Requests
- mbnas_server (WP-3/5B owner): `health()` must never pass the core's `allowlists` through. It should always report
  the gateway's own (possibly empty) `dataset_roots` and `output_roots`, plus `archive_roots`. A test for the split
  configuration should pin this. Re-run T8 §2 afterwards with
  `python -I t8_scan.py` after `t6_t8_driver.py up`.

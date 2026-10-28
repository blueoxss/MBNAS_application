#!/usr/bin/env python3
"""WP-7B: assemble config/examples/owner-all-in-one.env.example from the three role examples (bodies verbatim)."""
import sys
from pathlib import Path

EX = Path(sys.argv[1]) / "config" / "examples"
HEADER = """\
# MBNAS Research Console 0.1.0 - OWNER ALL-IN-ONE settings: core + owner worker + gateway + runner (+ web) on ONE owner
# host. PRIVATE (owner ZIP only). One file holds the three prefixes MBNAS_CORE_ (mbnas_core.config.CoreSettings),
# MBNAS_GW_ (mbnas_server.config.GatewaySettings) and MBNAS_RUNNER_ (mbnas_runner.config.RunnerSettings); each
# process reads only its own prefix. The three sections below repeat core.env.example, gateway.env.example and
# runner.env.example (same keys, same defaults) - see those files for split (public) deployments.
#
# LOAD THIS FILE ONLY AS A DOTENV FILE, per process:
#   mbnas-core-api --env-file <file>      mbnas-core-owner-worker --owner --env-file <file>
#   mbnas-gateway --env-file <file>       mbnas-runner run --env-file <file>
# Dotenv values stay inside each settings object and never enter the process environment. Do NOT use this file as
# systemd EnvironmentFile=, docker compose env_file: or `set -a; . file`: that would put the core secrets
# (MBNAS_CORE_TICKET_KEY, MBNAS_CORE_API_TOKEN, ...) into the gateway and runner processes. For systemd/compose use
# one file per role (core.env, gateway.env, runner.env).
# MBNAS_HOME is read from the process environment only (scripts/start.sh sets <install root>/runtime).
#
# All-in-one values (everything else: the role sections below):
#   * gateway and runner reach the core on loopback: MBNAS_GW_CORE_URL / MBNAS_RUNNER_CORE_URL keep the default
#     http://127.0.0.1:8700 (plain http is accepted for loopback only);
#   * leave MBNAS_GW_DATASET_ROOTS empty: the gateway forwards validation unchanged and the core checks the dataset
#     paths itself (same host); the runner reads the same paths (MBNAS_RUNNER_DATASET_ROOTS = MBNAS_CORE_DATASET_ROOTS,
#     no MBNAS_RUNNER_DATASET_MOUNTS);
#   * secrets: MBNAS_CORE_API_TOKEN, MBNAS_CORE_ARTIFACT_SIGNING_KEY, MBNAS_CORE_TICKET_KEY, MBNAS_GW_API_TOKEN and
#     MBNAS_GW_ARTIFACT_SIGNING_KEY are GENERATED at install time; MBNAS_GW_CORE_TOKEN and MBNAS_RUNNER_TOKEN are
#     issued by this host's core once its DB exists:
#       mbnas-core-principal --env-file <file> create --kind server
#       mbnas-core-principal --env-file <file> create --kind runner --runner-id <MBNAS_RUNNER_RUNNER_ID>
#     (the token is printed once as JSON; store it here, file mode 0600);
#   * artifacts: MBNAS_RUNNER_ARTIFACT_TRANSFER=local with MBNAS_RUNNER_ARTIFACT_STORE_DIR = MBNAS_GW_ARTIFACT_STORE
#     (default $MBNAS_HOME/gateway/artifacts) lets the gateway serve the runner's artifacts (same host); keep
#     MBNAS_RUNNER_ARTIFACT_STORE_MODE=same-uid when gateway and runner run as one user, else link-0644;
#   * RF-DETR native resume is enabled for the owner's own checkpoints (DD-A20 owner profile).
MBNAS_RUNNER_ALLOW_PICKLE_RESUME=true
"""

def body(name: str) -> str:
    text = (EX / name).read_text()
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("# ---- "))
    out = lines[start:]
    if name == "runner.env.example":  # set explicitly in the header above
        i = next(i for i, l in enumerate(out) if l.startswith("# MBNAS_RUNNER_ALLOW_PICKLE_RESUME="))
        out[i] = "# MBNAS_RUNNER_ALLOW_PICKLE_RESUME=false   (set to true at the top of this file)"
    return "\n".join(out)

BANNER = "#" + "=" * 119
parts = [HEADER]
for title, name in (("CORE  (MBNAS_CORE_*: mbnas-core-api, mbnas-core-owner-worker, mbnas-core-principal, mbnas-core-doctor)", "core.env.example"),
                    ("GATEWAY  (MBNAS_GW_*: mbnas-gateway, mbnas-gateway-doctor)", "gateway.env.example"),
                    ("RUNNER  (MBNAS_RUNNER_*: mbnas-runner)", "runner.env.example")):
    parts.append(f"{BANNER}\n# {title}\n{BANNER}\n{body(name)}\n")
(EX / "owner-all-in-one.env.example").write_text("\n".join(parts))
print("written", EX / "owner-all-in-one.env.example")

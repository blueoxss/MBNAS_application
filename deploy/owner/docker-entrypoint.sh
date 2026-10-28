#!/bin/sh
# MBNAS Research Console - entrypoint of the OWNER image (deploy/owner/Dockerfile). PRIVATE: owner host only.
# The first argument selects the role; the remaining arguments go to that program unchanged (exec: the program
# receives the stop signals directly; run the container with --init / compose `init: true`).
#   core          [args] -> mbnas-core-api [args]                 core API (/core/v1/console|runner|owner), port 8700
#   owner-worker  [args] -> mbnas-core-owner-worker --owner [args] PROPOSE_GENERATION jobs (CPU)
#   principal     [args] -> mbnas-core-principal [args]           issue/rotate/revoke/list tokens (printed once)
#   core-doctor   [args] -> mbnas-core-doctor [args]
#   gateway       [args] -> mbnas-gateway [args]                  all-in-one: user API + web console, port 8710
#   runner        [args] -> mbnas-runner run [args]               all-in-one: job daemon (CPU when CUDA_VISIBLE_DEVICES="")
#   register      [args] -> mbnas-runner register [args]
#   version              -> installed MBNAS distributions
# Settings come from the container environment (compose env_file per role) or from --env-file <file>.
set -eu
role="${1:-}"
if [ "$#" -gt 0 ]; then
    shift
fi
case "$role" in
    core) exec mbnas-core-api "$@" ;;
    owner-worker) exec mbnas-core-owner-worker --owner "$@" ;;
    principal) exec mbnas-core-principal "$@" ;;
    core-doctor) exec mbnas-core-doctor "$@" ;;
    gateway) exec mbnas-gateway "$@" ;;
    runner) exec mbnas-runner run "$@" ;;
    register) exec mbnas-runner register "$@" ;;
    version)
        exec python -c 'from importlib import metadata as m
for d in ("mbnas-contracts", "mbnas-model-runtime", "mbnas-runner", "mbnas-server", "mbnas-core", "torch", "ultralytics"):
    try:
        print(d, m.version(d))
    except m.PackageNotFoundError:
        print(d, "not installed")' ;;
    *)
        echo "usage: <image> core|owner-worker|principal|core-doctor|gateway|runner|register|version [args...]" >&2
        exit 64 ;;
esac

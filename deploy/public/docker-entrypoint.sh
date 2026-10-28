#!/bin/sh
# MBNAS Research Console - entrypoint of the PUBLIC images (deploy/public/Dockerfile, Dockerfile.cpu).
# The first argument selects the role; the remaining arguments go to that program unchanged (exec: the program
# receives the stop signals directly; run the container with --init / compose `init: true`).
#   gateway  [args] -> mbnas-gateway [args]          user API /api/v1 + web console (MBNAS_GW_* settings)
#   runner   [args] -> mbnas-runner run [args]       job daemon (MBNAS_RUNNER_* settings; CPU when CUDA_VISIBLE_DEVICES="")
#   register [args] -> mbnas-runner register [args]  register once with the core and print the negotiated configuration
#   doctor   [args] -> mbnas-gateway-doctor [args]   gateway configuration check
#   version         -> installed MBNAS distributions
# Settings come from the container environment (docker --env-file / compose env_file) or from --env-file <file>.
set -eu
role="${1:-}"
if [ "$#" -gt 0 ]; then
    shift
fi
case "$role" in
    gateway) exec mbnas-gateway "$@" ;;
    runner) exec mbnas-runner run "$@" ;;
    register) exec mbnas-runner register "$@" ;;
    doctor) exec mbnas-gateway-doctor "$@" ;;
    version)
        exec python -c 'from importlib import metadata as m
for d in ("mbnas-contracts", "mbnas-model-runtime", "mbnas-runner", "mbnas-server", "torch", "ultralytics"):
    try:
        print(d, m.version(d))
    except m.PackageNotFoundError:
        print(d, "not installed")' ;;
    *)
        echo "usage: <image> gateway|runner|register|doctor|version [args...]" >&2
        exit 64 ;;
esac

#!/bin/bash
# C07 evaluator: V4 real-stack Playwright steps (same env as the project's run_e2e.sh), cwd = scratch (../examples/models = copy)
# usage: run_v4_e2e.sh <stack.out> <log> [playwright args...]
STACK=$1; L=$2; shift 2
ROOT=<ROOT>
S=<SCRATCH>/wp3/c07
J=$(grep -m1 '^MBNAS_REAL_STACK ' "$STACK" | sed 's/^MBNAS_REAL_STACK //')
g() { echo "$J" | $ROOT/runtime/venv/bin/python -I -c "import json,sys;print(json.load(sys.stdin)['$1'])"; }
cd $S/v4/cwd
export CUDA_VISIBLE_DEVICES="" npm_config_cache=$ROOT/build/npm-cache npm_config_logs_max=0 npm_config_update_notifier=false TMPDIR=$S/tmp
export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64 PLAYWRIGHT_BROWSERS_PATH=$ROOT/runtime/pw-browsers
export MBNAS_E2E_REAL=1 MBNAS_E2E_V4=1 MBNAS_E2E_EVIDENCE_DIR=$S/v4c/screenshots
export MBNAS_E2E_TOKEN=$(g bearer) MBNAS_E2E_BASE_URL=$(g url) MBNAS_E2E_DATASET_YAML=$(g dataset_yaml) MBNAS_E2E_OUTPUT_ROOT=$(g output_root) MBNAS_E2E_STACK_PID=$(g pid)
echo "# $(date -Iseconds) cwd=$PWD base_url=$MBNAS_E2E_BASE_URL args: $*" > "$L"
$ROOT/mbnas_web/node_modules/.bin/playwright test -c $ROOT/mbnas_web/playwright.real.config.ts --output $S/v4c/test-results --reporter=list "$@" 2>&1 | sed "s/$MBNAS_E2E_TOKEN/<redacted>/g" >> "$L"
echo "exit ${PIPESTATUS[0]}" >> "$L"

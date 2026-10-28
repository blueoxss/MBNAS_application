#!/usr/bin/env bash
# C07 evaluator: full mock e2e (desktop-1280 + mobile-375) against the public-ZIP dist; cwd = scratch so evidence/ lands there
set -u
ROOT=<ROOT>
S=<SCRATCH>/wp3/c07
export CUDA_VISIBLE_DEVICES="" npm_config_cache=$ROOT/build/npm-cache npm_config_logs_max=0 npm_config_update_notifier=false TMPDIR=$S/tmp
export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64 PLAYWRIGHT_BROWSERS_PATH=$ROOT/runtime/pw-browsers
cd $S/e2e_mock/cwd
echo "# C07 mock e2e $(date -Iseconds) cwd=$PWD dist=$S/zipdist/dist (from dist/public ZIP) args: $*"
$ROOT/mbnas_web/node_modules/.bin/playwright test -c $S/pw_mock.config.ts --reporter=list "$@" 2>&1
echo "exit=$?"

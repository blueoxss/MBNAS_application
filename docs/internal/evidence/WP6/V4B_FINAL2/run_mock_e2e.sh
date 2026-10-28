#!/usr/bin/env bash
# C07 evaluator: full mock e2e (desktop-1280 + mobile-375) against the public-ZIP dist; cwd = scratch so evidence/ lands there
set -u
ROOT=/home/mgjeong/Desktop/etri_hd/mbnas_application
S=/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c07r2
export CUDA_VISIBLE_DEVICES="" npm_config_cache=$ROOT/build/npm-cache npm_config_logs_max=0 npm_config_update_notifier=false TMPDIR=$S/tmp
export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu22.04-x64 PLAYWRIGHT_BROWSERS_PATH=$ROOT/runtime/pw-browsers
cd $S/e2e_mock/cwd
echo "# C07 mock e2e $(date -Iseconds) cwd=$PWD dist=$S/build/dist (scratch build == mbnas_web/dist) args: $*"
$ROOT/mbnas_web/node_modules/.bin/playwright test -c $S/pw_mock.config.ts --reporter=list "$@" 2>&1
echo "exit=$?"

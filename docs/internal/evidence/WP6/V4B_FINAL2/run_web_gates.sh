#!/usr/bin/env bash
# C07 evaluator: re-run web gates (CPU only, no writes to the project tree except npm's cache dir given by the caller)
set -u
ROOT=/home/mgjeong/Desktop/etri_hd/mbnas_application
S=/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp3/c07r2
export CUDA_VISIBLE_DEVICES="" npm_config_cache=$ROOT/build/npm-cache npm_config_logs_max=0 npm_config_update_notifier=false TMPDIR=$S/tmp
cd $ROOT/mbnas_web
echo "# C07 web gates $(date -Iseconds) node $(node --version) cwd=mbnas_web"
for cmd in "npm run check:types" "npm run typecheck" "npx vitest run --no-cache" "npm run check:bundle"; do
  echo "=== \$ $cmd   [$(date -Iseconds)]"
  $cmd 2>&1
  echo "exit=$?"
done
echo "# dist sha256 after"
sha256sum dist/index.html dist/assets/*

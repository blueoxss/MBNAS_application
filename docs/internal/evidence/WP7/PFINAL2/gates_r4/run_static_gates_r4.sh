#!/bin/bash
# WP-7V P-final-2 step 2a (r4, DD-A75 F1b tree): static gates on the current tree. Every log gets a .meta with the tree hash (step 1).
cd /home/mgjeong/Desktop/etri_hd/mbnas_application
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null npm_config_cache=build/npm-cache
E=docs/internal/evidence/WP7/PFINAL2/gates_r4
PY=runtime/venv/bin/python
TREE=$(python3 -c "import json;print(json.load(open('docs/internal/evidence/WP7/PFINAL2/gates_r4/01_tree_hash.json'))['sha256'])")
TREE4=$(python3 -c "import json;print(json.load(open('docs/internal/evidence/WP7/PFINAL2/gates_r4/01_tree_hash_public4.json'))['sha256'])")
gate() {  # gate <name> <cmd...>
  local name=$1; shift; local s=$(date +%s)
  echo "start $(date -u +%FT%TZ) cwd=$PWD interpreter=$PY tree=$TREE tree_public4=$TREE4" > $E/$name.meta
  echo "cmd: $*" >> $E/$name.meta
  "$@" > $E/$name.txt 2>&1; local rc=$?
  echo "exit=$rc dur=$(( $(date +%s)-s ))s end=$(date -u +%FT%TZ)" >> $E/$name.meta
  echo "$name exit=$rc"
}
gate import_closure $PY -B scripts/dev/check_import_closure.py
gate placement_coverage $PY -B scripts/dev/placement_coverage.py --format text
gate gen_runner_openapi_check $PY -B scripts/dev/gen_runner_openapi.py --check
gate public_openapi_sha sha256sum mbnas_contracts/src/mbnas_contracts/openapi/openapi.json mbnas_contracts/src/mbnas_contracts/openapi/openapi_runner.json mbnas_contracts/src/mbnas_contracts/openapi/HISTORY.json
gate web_types_check bash -c 'cd mbnas_web && node scripts/gen-types.mjs --check'
gate gen_registration_docs_check $PY -B scripts/dev/gen_registration_docs.py --check
gate gen_npm_notices_check $PY -B scripts/dev/gen_npm_notices.py --check
gate snapshot_compare $PY -B scripts/dev/snapshot_compare.py --json-out $E/snapshot_compare.json
gate t2_compare $PY -B scripts/owner/t2_compare.py --work-root /tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/pfinal2/t2_work_r4 --out-dir docs/internal/evidence/WP7/PFINAL2/gates_r4/t2
gate pytest_t2 env MBNAS_TEST_T2=1 $PY -B -m pytest tests/owner/t2 -p no:cacheprovider -q -rfE --timeout=900

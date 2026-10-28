#!/bin/bash
# WP-7V P-final-2 step 2b (r2, DD-A73 tree): full public suite + owner gate, same marker expression and timeout as the P-final run
# (docs/internal/evidence/WP7/7V_FINAL/run_gates_final.sh = WP-4C run_gates.sh). Tree hash in every .meta.
cd /home/mgjeong/Desktop/etri_hd/mbnas_application
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null MBNAS_HOME=/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime MBNAS_TEST_ROOT=/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime/test_root
E=docs/internal/evidence/WP7/PFINAL2/gates_r2
TREE=$(python3 -c "import json;print(json.load(open('docs/internal/evidence/WP7/PFINAL2/gates_r2/01_tree_hash.json'))['sha256'])")
s=$(date +%s); echo "public start $(date -u +%FT%TZ) cwd=$PWD interpreter=runtime/venv/bin/python tree=$TREE" > $E/gate_public.meta
timeout 1800 runtime/venv/bin/python -B -m pytest tests/public -m "not gpu and not live and not legacy_records and not host_data" \
  -p no:cacheprovider -q -rfE --timeout=180 --durations=15 > $E/gate_public.txt 2>&1
echo "exit=$? dur=$(( $(date +%s)-s ))s end=$(date -u +%FT%TZ)" >> $E/gate_public.meta
s=$(date +%s); echo "owner start $(date -u +%FT%TZ) cwd=$PWD interpreter=runtime/venv/bin/python tree=$TREE" > $E/gate_owner.meta
timeout 3000 runtime/venv/bin/python -B -m pytest tests/owner -m "not gpu and not live and not legacy_records and not host_data" \
  -p no:cacheprovider -q -rfE --timeout=180 --durations=30 > $E/gate_owner.txt 2>&1
echo "exit=$? dur=$(( $(date +%s)-s ))s end=$(date -u +%FT%TZ)" >> $E/gate_owner.meta
TREE_AFTER=$(runtime/venv/bin/python -B scripts/dev/source_tree_hash.py | python3 -c "import json,sys;print(json.load(sys.stdin)['sha256'])")
echo "tree after the gates: $TREE_AFTER" >> $E/gate_owner.meta

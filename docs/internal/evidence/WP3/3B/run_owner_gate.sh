#!/bin/bash
# WP-3B fix round: the WP-3C owner gate command (docs/internal/evidence/WP3/3C/run_owner_gate.sh), evidence written here.
cd /home/mgjeong/Desktop/etri_hd/mbnas_application
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 MBNAS_HOME=/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime MBNAS_TEST_ROOT=/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime/test_root
E=docs/internal/evidence/WP3/3B
s=$(date +%s); echo "start $(date -u +%FT%TZ) cwd=$PWD interpreter=runtime/venv/bin/python" > $E/owner_gate.meta
timeout 3000 runtime/venv/bin/python -B -m pytest tests/owner -m "not gpu and not live and not legacy_records and not host_data" \
  --ignore=tests/owner/api/test_zz_wp3a_scratch.py -p no:cacheprovider -q -rfE --timeout=180 --durations=30 > $E/owner_gate.txt 2>&1
echo "exit=$? dur=$(( $(date +%s)-s ))s end=$(date -u +%FT%TZ)" >> $E/owner_gate.meta

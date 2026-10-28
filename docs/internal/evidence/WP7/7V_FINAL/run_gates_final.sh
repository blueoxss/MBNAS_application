#!/bin/bash
# WP-7V P1-final regression gates: the same commands as docs/internal/evidence/WP4/4C/run_gates.sh (= WP-3C
# run_public.sh / run_owner_gate.sh: marker filter, --timeout=180), output to docs/internal/evidence/WP7/7V_FINAL/.
cd /home/mgjeong/Desktop/etri_hd/mbnas_application
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null MBNAS_HOME=/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime MBNAS_TEST_ROOT=/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime/test_root
E=docs/internal/evidence/WP7/7V_FINAL
s=$(date +%s); echo "public start $(date -u +%FT%TZ) cwd=$PWD interpreter=runtime/venv/bin/python" > $E/gate_public.meta
timeout 1800 runtime/venv/bin/python -B -m pytest tests/public -m "not gpu and not live and not legacy_records and not host_data" \
  -p no:cacheprovider -q -rfE --timeout=180 --durations=15 > $E/gate_public.txt 2>&1
echo "exit=$? dur=$(( $(date +%s)-s ))s end=$(date -u +%FT%TZ)" >> $E/gate_public.meta
s=$(date +%s); echo "owner start $(date -u +%FT%TZ) cwd=$PWD interpreter=runtime/venv/bin/python" > $E/gate_owner.meta
timeout 3000 runtime/venv/bin/python -B -m pytest tests/owner -m "not gpu and not live and not legacy_records and not host_data" \
  -p no:cacheprovider -q -rfE --timeout=180 --durations=30 > $E/gate_owner.txt 2>&1
echo "exit=$? dur=$(( $(date +%s)-s ))s end=$(date -u +%FT%TZ)" >> $E/gate_owner.meta

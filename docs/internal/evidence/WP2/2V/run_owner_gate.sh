#!/bin/bash
cd /home/mgjeong/Desktop/etri_hd/mbnas_application
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 MBNAS_HOME=/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime MBNAS_TEST_ROOT=/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime/test_root
s=$(date +%s); echo "start $(date -u +%FT%TZ) cwd=$PWD interpreter=/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime/venv/bin/python" > /home/mgjeong/Desktop/etri_hd/mbnas_application/docs/internal/evidence/WP2/2V/owner_gate.meta
/home/mgjeong/Desktop/etri_hd/mbnas_application/runtime/venv/bin/python -B -m pytest tests/owner -m "not gpu and not live and not legacy_records and not host_data" -p no:cacheprovider -q -rfE > /home/mgjeong/Desktop/etri_hd/mbnas_application/docs/internal/evidence/WP2/2V/owner_gate.txt 2>&1
echo "exit=$? dur=$(( $(date +%s)-s ))s end=$(date -u +%FT%TZ)" >> /home/mgjeong/Desktop/etri_hd/mbnas_application/docs/internal/evidence/WP2/2V/owner_gate.meta

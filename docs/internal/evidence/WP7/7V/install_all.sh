#!/usr/bin/env bash
set -u
A=/home/mgjeong/Desktop/etri_hd/mbnas_application; T=$A/runtime/test_root/t6; S="$(dirname "$0")"
PYI=/home/mgjeong/miniconda3/envs/mbnas310/bin/python3.10
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null
cd "$S"
( bash "$T/mbnas public test/mbnas_application/scripts/install.sh" --profile public --offline --wheelhouse "$A/build/wheelhouse" --python "$PYI" > "$S/install_public.txt" 2>&1; echo "exit=$?" >> "$S/install_public.txt" ) &
( bash "$T/mbnas owner test/mbnas_application/scripts/install.sh" --profile owner --offline --wheelhouse "$A/build/wheelhouse" --python "$PYI" > "$S/install_owner.txt" 2>&1; echo "exit=$?" >> "$S/install_owner.txt" ) &
( unshare -rn bash "$T/mbnas offline test/mbnas_application/scripts/install.sh" --profile public --offline --wheelhouse "$A/build/wheelhouse" --python "$PYI" > "$S/install_offline_unshare.txt" 2>&1; echo "exit=$?" >> "$S/install_offline_unshare.txt" ) &
wait

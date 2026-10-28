#!/usr/bin/env bash
# WP-7V P1-final installs of the FINAL ZIPs, from the external cwd (this scratch folder), in parallel
set -u
A=/home/mgjeong/Desktop/etri_hd/mbnas_application; F="$A/runtime/test_root/t6/final"; S="$(cd "$(dirname "$0")" && pwd)"
E=$A/docs/internal/evidence/WP7/7V_FINAL; PYI=/home/mgjeong/miniconda3/envs/mbnas310/bin/python3.10
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null
cd "$S"
inst() { local out="$1"; shift; { echo "\$ (cwd=<scratchpad>/t6final) $*"; "$@"; echo "exit=$?"; } > "$out" 2>&1; }
inst "$E/40_install_public.txt" bash "$F/mbnas public final/mbnas_application/scripts/install.sh" --profile public --offline --wheelhouse "$A/build/wheelhouse" --python "$PYI" &
inst "$E/40_install_registration.txt" bash "$F/mbnas registration final/mbnas_application/scripts/install.sh" --profile public --offline --wheelhouse "$A/build/wheelhouse" --python "$PYI" &
inst "$E/40_install_owner.txt" bash "$F/mbnas owner final/mbnas_application/scripts/install.sh" --profile owner --offline --wheelhouse "$A/build/wheelhouse" --python "$PYI" &
inst "$E/22_offline_install_public_final_unshare.txt" unshare -rn bash "$F/mbnas offline final/mbnas_application/scripts/install.sh" --profile public --offline --wheelhouse "$A/build/wheelhouse" --python "$PYI" &
wait

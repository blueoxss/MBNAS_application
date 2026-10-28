#!/bin/bash
# WP-7V P-final-2 step 5: T6 relocation rerun2 on the NEW ZIPs (dist/ after step 3).
#   owner        : host, fresh path with spaces, external cwd (the core also serves the two sandboxes)
#   public       : bwrap sandbox (sbx_t6r2.sh): isolated offline install from the ZIP (own network namespace), then
#   registration   doctor/start/API/verify/stop inside the sandbox on the host network (gateway -> owner core)
# Phases (argument): extract | install | configure | owner_up | sandboxes | owner_verify | owner_down | owner_reinvoke
set -u
SRC=/home/mgjeong/Desktop/etri_hd/mbnas_application
RT=$SRC/runtime/test_root/t6/rerun2
H=/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/pfinal2/t6r2
SBX=$H/harness/sbx_t6r2.sh
OWN="$RT/mbnas owner r2/mbnas_application"
OEV="$RT/mbnas owner r2/evidence"
PY310=/home/mgjeong/miniconda3/envs/mbnas310/bin/python3.10
CORE_PORT=18900; OWN_GW=18911; PUB_GW=18910; REG_GW=18912
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null
log() { echo "[t6r2 $(date -u +%FT%TZ)] $*"; }
svc() { mkdir -p "$OWN/runtime/home"; env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 HOME="$OWN/runtime/home" CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 "$@"; }  # service-like environment
ostep() { local name=$1; shift; mkdir -p "$OEV"; { echo "\$ (cwd=$(pwd)) $*"; "$@"; echo "exit=$?"; } > "$OEV/$name.txt" 2>&1; log "owner $name: $(tail -n 1 "$OEV/$name.txt")"; }
cd "$H"
case "$1" in
extract)
  for t in "mbnas owner r2:owner" "mbnas public r2:public" "mbnas registration r2:registration"; do
    d="${t%%:*}"; p="${t##*:}"; z=$(ls $SRC/dist/$p/*.zip)
    [ -e "$RT/$d" ] && { log "refusing: $RT/$d exists"; exit 1; }
    mkdir -p "$RT/$d" && (cd "$RT/$d" && unzip -q "$z") && log "$d <- $(basename "$z") sha256 $(sha256sum "$z" | cut -c1-64)"
  done ;;
install)
  ( ostep install_owner bash "$OWN/scripts/install.sh" --profile owner --offline --wheelhouse "$SRC/build/wheelhouse" --python "$PY310" ) &
  for L in public registration; do
    ( mkdir -p "$RT/mbnas $L r2/evidence"; SBX_NET=none "$SBX" "$L" "$RT/mbnas $L r2" bash /opt/harness/install_inside.sh \
        > "$RT/mbnas $L r2/evidence/install_public_sandbox.txt" 2>&1
      log "$L sandbox install: $(tail -n 1 "$RT/mbnas $L r2/evidence/install_public_sandbox.txt")" ) &
  done
  wait ;;
configure)
  "$SRC/runtime/venv/bin/python" -I "$H/harness/t6r2_configure.py" ;;
owner_up)
  ostep start_owner svc bash "$OWN/scripts/start.sh" --profile owner --wait-s 120
  ostep doctor_owner_strict bash "$OWN/scripts/doctor.sh" --profile owner --strict --json "$OEV/doctor_owner.json" ;;
sandboxes)
  for L in public registration; do
    port=$PUB_GW; [ $L = registration ] && port=$REG_GW
    ( "$SBX" "$L" "$RT/mbnas $L r2" bash /opt/harness/t6r2_inside.sh $port > "$RT/mbnas $L r2/evidence/sandbox_console.txt" 2>&1
      log "$L sandbox run exit=$?" ) &
  done
  wait ;;
owner_verify)
  mkdir -p "$OEV"; { echo "\$ env | grep -E '^(PYTHON|OLDPWD|VIRTUAL_ENV)'   (the shell that invokes verify.sh; cwd $(pwd))"; env | grep -E '^(PYTHON|OLDPWD|VIRTUAL_ENV)'; echo "exit=$? (1 = no match)"; } > "$OEV/verify_caller_env.txt" 2>&1
  ostep verify_owner bash "$OWN/scripts/verify.sh" --profile owner --suite unit,integration,relocation --json "$OEV/verify_owner.json"
  for d in "$OWN"/runtime/logs/verify/*; do [ -f "$d/relocation.json" ] && cp "$d/relocation.json" "$OEV/relocation_$(basename "$d").json"; done ;;
owner_down)
  ostep stop_owner bash "$OWN/scripts/stop.sh" --profile owner ;;
owner_reinvoke)
  cd /
  ostep reinvoke_start_core_from_root svc bash "$OWN/scripts/start.sh" --profile owner --role core --wait-s 120
  ostep reinvoke_doctor_from_root bash "$OWN/scripts/doctor.sh" --profile owner
  ostep reinvoke_stop_from_root bash "$OWN/scripts/stop.sh" --profile owner
  log "leftover service processes: $(pgrep -f 'test_root/t6/rerun2/.*/runtime/venv/bin/' | wc -l)" ;;
esac

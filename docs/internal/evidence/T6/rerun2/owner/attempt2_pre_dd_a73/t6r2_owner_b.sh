#!/bin/bash
# WP-7V P-final-2 (DD-A72 follow-up): T6 owner rerun2 on the rebuilt owner ZIP 867cc577…, full: fresh extraction into a
# new path with spaces, install from an external cwd, ports, start (service-like env), doctor --strict, verify
# unit,integration,relocation + package, stop, re-invocation from cwd /. Phases: extract install configure up verify down reinvoke
set -u
SRC=/home/mgjeong/Desktop/etri_hd/mbnas_application
RT=$SRC/runtime/test_root/t6/rerun2
H=/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/pfinal2/t6r2
OWN="$RT/mbnas owner r2b/mbnas_application"
OEV="$RT/mbnas owner r2b/evidence"
PY310=/home/mgjeong/miniconda3/envs/mbnas310/bin/python3.10
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null
log() { echo "[t6r2-owner-b $(date -u +%FT%TZ)] $*"; }
svc() { mkdir -p "$OWN/runtime/home"; env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 HOME="$OWN/runtime/home" CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 "$@"; }
ostep() { local name=$1; shift; mkdir -p "$OEV"; { echo "\$ (cwd=$(pwd)) $*"; "$@"; echo "exit=$?"; } > "$OEV/$name.txt" 2>&1; log "$name: $(tail -n 1 "$OEV/$name.txt")"; }
cd "$H"
case "$1" in
extract)
  z=$SRC/dist/owner/mbnas_application-0.1.0-owner-private-runtime-linux_x86_64.zip
  [ -e "$RT/mbnas owner r2b" ] && { log "refusing: exists"; exit 1; }
  mkdir -p "$RT/mbnas owner r2b" && (cd "$RT/mbnas owner r2b" && unzip -q "$z") && log "mbnas owner r2b <- $(sha256sum "$z" | cut -c1-64)" ;;
install)
  ostep install_owner bash "$OWN/scripts/install.sh" --profile owner --offline --wheelhouse "$SRC/build/wheelhouse" --python "$PY310" ;;
configure)
  "$SRC/runtime/venv/bin/python" -I - "$OWN" <<'PYEOF'
import os, sys
from pathlib import Path
own = Path(sys.argv[1])
def setkeys(path, kv):
    lines = path.read_text().splitlines()
    keep = [l for l in lines if not any(l.startswith(f"{k}=") or l.startswith(f"# {k}=") for k in kv)]
    keep += [f"{k}={v}" for k, v in kv.items()]
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC); os.write(fd, ("\n".join(keep) + "\n").encode()); os.close(fd)
    print(f"configured {path.name}: {sorted(kv)} (values not printed)")
setkeys(own / "config/owner/core.env", {"MBNAS_CORE_PORT": 18900})
setkeys(own / "config/owner/gateway.env", {"MBNAS_GW_PORT": 18911, "MBNAS_GW_CORE_URL": "http://127.0.0.1:18900"})
setkeys(own / "config/owner/runner.env", {"MBNAS_RUNNER_CORE_URL": "http://127.0.0.1:18900"})
PYEOF
  ;;
up)
  ostep start_owner svc bash "$OWN/scripts/start.sh" --profile owner --wait-s 120
  ostep doctor_owner_strict bash "$OWN/scripts/doctor.sh" --profile owner --strict --json "$OEV/doctor_owner.json" ;;
verify)
  mkdir -p "$OEV"; { echo "\$ env | grep -E '^(PYTHON|OLDPWD|VIRTUAL_ENV)'   (the shell that invokes verify.sh; cwd $(pwd))"; env | grep -E '^(PYTHON|OLDPWD|VIRTUAL_ENV)'; echo "exit=$? (1 = no match)"; } > "$OEV/verify_caller_env.txt" 2>&1
  ostep verify_owner bash "$OWN/scripts/verify.sh" --profile owner --suite unit,integration,relocation --json "$OEV/verify_owner.json"
  ostep verify_owner_package bash "$OWN/scripts/verify.sh" --profile owner --suite package --json "$OEV/verify_owner_package.json"
  for d in "$OWN"/runtime/logs/verify/*; do [ -f "$d/relocation.json" ] && cp "$d/relocation.json" "$OEV/relocation_$(basename "$d").json"; done ;;
down)
  ostep stop_owner bash "$OWN/scripts/stop.sh" --profile owner ;;
reinvoke)
  cd /
  ostep reinvoke_start_core_from_root svc bash "$OWN/scripts/start.sh" --profile owner --role core --wait-s 120
  ostep reinvoke_doctor_from_root bash "$OWN/scripts/doctor.sh" --profile owner
  ostep reinvoke_stop_from_root bash "$OWN/scripts/stop.sh" --profile owner
  n=$(pgrep -f 'test_root/t6/rerun2/mbnas owner r2b/.*/runtime/venv/bin/' | wc -l); log "leftover service processes: $n"; echo "leftover service processes after the re-invocation: $n" > "$OEV/leftover_check.txt" ;;
esac

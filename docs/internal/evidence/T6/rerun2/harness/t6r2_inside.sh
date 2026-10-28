#!/bin/bash
# T6 rerun2, inside the sandbox (public profile tree of the public or registration ZIP). cwd = /tmp (external to ROOT).
# Args: <gateway port>. Steps: isolation facts -> doctor --strict -> start -> doctor --strict --json -> API probe +
# T8 capture (responses, logs, /proc environ/cmdline/maps of the gateway and runner) -> mbnas_core loader check ->
# verify unit,relocation -> verify package (scanner bound read-only) -> stop -> leftover check.
set -u
PORT=$1
L=$T6_LABEL
SB="/srv/mbnas $L r2"
R="$SB/mbnas_application"
EV="$SB/evidence"; mkdir -p "$EV/proc" "$EV/logs"
log() { echo "[t6r2-$L $(date -u +%FT%TZ)] $*"; }
step() { local name=$1; shift; "$@" > "$EV/$name.txt" 2>&1; local rc=$?; echo "exit=$rc" >> "$EV/$name.txt"; log "$name exit=$rc"; return $rc; }
log "isolation: /home visible? $(test -e /home && echo YES || echo no); project tree visible? $(test -e /home/mgjeong/Desktop/etri_hd/mbnas_application && echo YES || echo no); etri_nas visible? $(test -e /home/mgjeong/Desktop/etri_hd/etri_nas && echo YES || echo no); /data visible? $(test -e /data && echo YES || echo no); interfaces: $(awk 'NR>2{print $1}' /proc/net/dev | tr '\n' ' '); /opt: $(ls /opt | tr '\n' ' '); cwd $(pwd)"
log "python: $(readlink -f "$R/runtime/venv/bin/python"); PYTHONPATH=${PYTHONPATH:-<unset>}; editable finders: $(ls "$R"/runtime/venv/lib/python3.10/site-packages | grep -ci '__editable__\|\.egg-link' ) ; mbnas dists: $(ls "$R"/runtime/venv/lib/python3.10/site-packages | grep -i '^mbnas' | tr '\n' ' ')"
log "runner id file: $(cat "$R/runtime/runner/runner_id" 2>/dev/null || echo '<none>'); runner.env RUNNER_ID lines: $(grep -c '^MBNAS_RUNNER_RUNNER_ID=' "$R/config/public/runner.env")"
step doctor_strict_prestart bash "$R/scripts/doctor.sh" --profile public --strict
step start bash "$R/scripts/start.sh" --profile public --wait-s 120; START=$?
step doctor_strict bash "$R/scripts/doctor.sh" --profile public --strict --json "$EV/doctor.json"
if [ $START = 0 ]; then
  step api_probe "$R/runtime/venv/bin/python" -I /opt/harness/t6r2_api.py --gw "http://127.0.0.1:$PORT" \
    --token-env "$R/config/public/gateway.env" --models "$R/examples/models" --output-root /srv/outputs \
    --evidence "$EV" --full "$([ "$L" = public ] && echo 1 || echo 0)"
  for role in gateway runner; do
    pid=$(cat "$R/runtime/pids/$role.pid" 2>/dev/null) || { log "no $role pid"; continue; }
    tr '\0' '\n' < /proc/$pid/environ > "$EV/proc/$role.environ"; tr '\0' ' ' < /proc/$pid/cmdline > "$EV/proc/$role.cmdline"
    chmod 600 "$EV/proc/$role.environ" "$EV/proc/$role.cmdline"
    log "$role pid $pid environ keys: $(cut -d= -f1 "$EV/proc/$role.environ" | sort | tr '\n' ' ')"
    log "$role /proc/$pid/maps lines naming mbnas_core: $(grep -c mbnas_core /proc/$pid/maps)"
  done
fi
step loader_check "$R/runtime/venv/bin/python" -c "import sys, pkgutil; assert not pkgutil.find_loader('mbnas_core'); import importlib.util as u; assert u.find_spec('mbnas_core') is None; import mbnas_server, mbnas_runner, mbnas_model_runtime, mbnas_contracts; import mbnas_server.api.app, mbnas_runner.daemon; bad=[m for m in sys.modules if m.split('.')[0]=='mbnas_core']; assert not bad, bad; print('mbnas_core: no loader, no spec, not in sys.modules after importing the public apps; mbnas_server from', mbnas_server.__file__)"
{ echo "\$ env | grep -E '^(PYTHON|OLDPWD|VIRTUAL_ENV)'   (the shell that invokes verify.sh; cwd $(pwd))"; env | grep -E '^(PYTHON|OLDPWD|VIRTUAL_ENV)'; echo "exit=$? (1 = no match)"; } > "$EV/verify_caller_env.txt" 2>&1
log "verify caller env (PYTHON*/OLDPWD/VIRTUAL_ENV): $(env | grep -E '^(PYTHON|OLDPWD|VIRTUAL_ENV)' | tr '\n' ' ')"
step verify_unit_relocation bash "$R/scripts/verify.sh" --profile public --suite unit,relocation --json "$EV/verify_unit_relocation.json"
for d in "$R"/runtime/logs/verify/*; do [ -f "$d/relocation.json" ] && cp "$d/relocation.json" "$EV/relocation_$(basename "$d").json"; done
step verify_package env MBNAS_CANARY_SCAN=/opt/scanner/canary_scan.py bash "$R/scripts/verify.sh" --profile public --suite package --json "$EV/verify_package.json"
for f in "$R"/runtime/logs/*.log; do [ -f "$f" ] && cp "$f" "$EV/logs/"; done
for d in "$R/runtime/gateway/logs" "$R/runtime/runner/logs"; do [ -d "$d" ] && for f in "$d"/*; do [ -f "$f" ] && cp "$f" "$EV/logs/$(basename "$(dirname "$d")")__$(basename "$f")"; done; done
step stop bash "$R/scripts/stop.sh" --profile public
sleep 2
log "processes after stop (pid namespace of the sandbox): $(ps -eo pid,args | grep -E 'mbnas|uvicorn' | grep -v grep | wc -l)"
log end

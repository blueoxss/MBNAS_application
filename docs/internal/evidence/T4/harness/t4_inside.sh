#!/bin/bash
# Runs INSIDE the T4 bwrap sandbox. Args: <items> [extra harness args...]
set -u
ITEMS="$1"; shift
T4=<T>/${T4_SUB:-t4}
R=$T4/mbnas_application
EV=$T4/evidence; mkdir -p $EV
log() { echo "[t4 $(date -u +%FT%TZ)] $*"; }
mkdir -p $R/runtime/data $R/runtime/core/runs/t4
[ -d $R/runtime/data/synthetic4 ] || cp -r $T4/data/synthetic4 $R/runtime/data/synthetic4
export MBNAS_RUNNER_ARTIFACT_TRANSFER=local MBNAS_RUNNER_ARTIFACT_STORE_DIR=$R/runtime/gateway/artifacts MBNAS_CORE_HEARTBEAT_S=2
VARIANT=${T4_VARIANT:-strict}; LABEL=t4${T4_LABEL_SUFFIX:-}
BENCH=()
if [ "$VARIANT" = strict ]; then export MBNAS_CORE_DEVICE_SOURCE=runners; BENCH=(--bench-device runner-t4-sandbox); fi
export ${T4_EXTRA_ENV:-T4_NOOP=1}
log "variant=$VARIANT label=$LABEL env overrides: MBNAS_RUNNER_ARTIFACT_TRANSFER=local MBNAS_RUNNER_ARTIFACT_STORE_DIR=<ROOT>/runtime/gateway/artifacts MBNAS_CORE_HEARTBEAT_S=2 MBNAS_CORE_DEVICE_SOURCE=${MBNAS_CORE_DEVICE_SOURCE:-<default local>} ${T4_EXTRA_ENV:-}; /dev nvidia nodes: $(ls /dev | grep nvidia | tr '\n' ' ')"
log "isolation: original tree visible? $(test -e <ROOT>/pyproject.toml && echo YES || echo no); etri_nas visible? $(test -e <HOME>/Desktop/etri_hd/etri_nas && echo YES || echo no); /data visible? $(test -e /data && echo YES || echo no); network interfaces: $(ls /sys/class/net | tr '\n' ' ')"
cd /tmp
log "doctor"; bash $R/scripts/doctor.sh --profile owner > $EV/${LABEL}_doctor_owner.txt 2>&1; log "doctor exit=$?"
log "start"; bash $R/scripts/start.sh --profile owner --wait-s 120 > $EV/${LABEL}_start_owner.txt 2>&1; RC=$?; log "start exit=$RC"; cat $EV/${LABEL}_start_owner.txt
if [ $RC = 0 ]; then
  log "harness items=$ITEMS"
  $R/runtime/venv/bin/python -I /opt/harness/t45.py --gw http://127.0.0.1:8710 --token-env $R/config/owner/gateway.env \
    --redact-env $R/config/owner/gateway.env:MBNAS_GW_CORE_TOKEN --redact-env $R/config/owner/runner.env:MBNAS_RUNNER_TOKEN \
    --redact-env $R/config/owner/core.env:MBNAS_CORE_API_TOKEN --redact-env $R/config/owner/core.env:MBNAS_CORE_ARTIFACT_SIGNING_KEY \
    --redact-env $R/config/owner/core.env:MBNAS_CORE_TICKET_KEY --redact-env $R/config/owner/gateway.env:MBNAS_GW_ARTIFACT_SIGNING_KEY \
    --label $LABEL --evidence-dir $EV --items "$ITEMS" --models $R/examples/models --gen-protocol $R/examples/protocol.cpu_generation_only.json \
    --dataset-yaml $R/runtime/data/synthetic4/data.yaml --output-root $R/runtime/core/runs/t4 \
    --db $R/runtime/core/db/mbnas.sqlite3 --store-dir $R/runtime/gateway/artifacts --web-dist $R/mbnas_web/dist \
    --audit-log $R/runtime/gateway/logs/audit.jsonl --gw-log $R/runtime/logs/gateway.log --core-log $R/runtime/logs/core.log "${BENCH[@]}" "$@"
  log "harness exit=$?"
fi
log "processes before stop:"; ps -eo pid,ppid,args | grep -E "mbnas|uvicorn" | grep -v grep | cut -c1-200
log "stop"; bash $R/scripts/stop.sh --profile owner > $EV/${LABEL}_stop_owner.txt 2>&1; log "stop exit=$?"; cat $EV/${LABEL}_stop_owner.txt
sleep 2; log "processes after stop:"; ps -eo pid,args | grep -E "mbnas|uvicorn|python" | grep -v grep | cut -c1-200; log "end"

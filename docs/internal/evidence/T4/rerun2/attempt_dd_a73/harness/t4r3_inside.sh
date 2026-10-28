#!/bin/bash
# DD-A73 attempt, inside the T4 sandbox (sbx2.sh). Args: <items> [extra harness args...]
# Env: WORK (bound work dir), T4_VARIANT local|runners, T4_LABEL_SUFFIX
set -u
ITEMS="$1"; shift
R=$WORK/zip/mbnas_application
EV=$WORK/evidence; mkdir -p $EV
VARIANT=${T4_VARIANT:-local}; LABEL=t4a73${T4_LABEL_SUFFIX:-}
log() { echo "[t4a73 $(date -u +%FT%TZ)] $*"; }
mkdir -p $R/runtime/data $R/runtime/core/runs/t4
[ -d $R/runtime/data/synthetic4 ] || cp -r $R/examples/synthetic4 $R/runtime/data/synthetic4
export MBNAS_RUNNER_ARTIFACT_TRANSFER=local MBNAS_RUNNER_ARTIFACT_STORE_DIR=$R/runtime/gateway/artifacts MBNAS_CORE_HEARTBEAT_S=2
[ "$VARIANT" = runners ] && export MBNAS_CORE_DEVICE_SOURCE=runners
log "variant=$VARIANT label=$LABEL overrides: MBNAS_RUNNER_ARTIFACT_TRANSFER=local MBNAS_RUNNER_ARTIFACT_STORE_DIR=<R>/runtime/gateway/artifacts MBNAS_CORE_HEARTBEAT_S=2 MBNAS_CORE_DEVICE_SOURCE=${MBNAS_CORE_DEVICE_SOURCE:-<unset: default local>}; /dev nvidia nodes: [$(ls /dev | grep nvidia | tr '\n' ' ')]"
log "isolation: original tree visible? $(test -e <ROOT>/pyproject.toml && echo YES || echo no); etri_nas visible? $(test -e <HOME>/Desktop/etri_hd/etri_nas && echo YES || echo no); /data visible? $(test -e /data && echo YES || echo no); interfaces: $(ip -brief addr 2>/dev/null | awk '{print $1}' | tr '\n' ' ')"
cd /tmp
log "doctor"; bash $R/scripts/doctor.sh --profile owner > $EV/${LABEL}_doctor_owner.txt 2>&1; log "doctor exit=$?"
log "start"; bash $R/scripts/start.sh --profile owner --wait-s 120 > $EV/${LABEL}_start_owner.txt 2>&1; RC=$?; log "start exit=$RC"; cat $EV/${LABEL}_start_owner.txt
log "schema_version of the fresh core DB: $($R/runtime/venv/bin/python -I -c "import sqlite3; c=sqlite3.connect('file:$R/runtime/core/db/mbnas.sqlite3?mode=ro', uri=True); print(c.execute(\"select value from meta where key='schema_version'\").fetchone()[0], 'prune_requests table:', c.execute(\"select count(*) from sqlite_master where name='prune_requests'\").fetchone()[0])")"
log "runner id file: $(cat $R/runtime/runner/runner_id 2>/dev/null || echo '<none>') ; runner.env RUNNER_ID line: $(grep -c '^MBNAS_RUNNER_RUNNER_ID=' $R/config/owner/runner.env)"
if [ $RC = 0 ]; then
  log "harness items=$ITEMS"
  $R/runtime/venv/bin/python -I /opt/harness/t45r2.py --gw http://127.0.0.1:8710 --token-env $R/config/owner/gateway.env \
    --redact-env $R/config/owner/gateway.env:MBNAS_GW_CORE_TOKEN --redact-env $R/config/owner/runner.env:MBNAS_RUNNER_TOKEN \
    --redact-env $R/config/owner/core.env:MBNAS_CORE_API_TOKEN --redact-env $R/config/owner/core.env:MBNAS_CORE_ARTIFACT_SIGNING_KEY \
    --redact-env $R/config/owner/core.env:MBNAS_CORE_TICKET_KEY --redact-env $R/config/owner/gateway.env:MBNAS_GW_ARTIFACT_SIGNING_KEY \
    --label $LABEL --evidence-dir $EV --items "$ITEMS" --models $R/examples/models --gen-protocol $R/examples/protocol.cpu_generation_only.json \
    --dataset-yaml $R/runtime/data/synthetic4/data.yaml --output-root $R/runtime/core/runs/t4 \
    --db $R/runtime/core/db/mbnas.sqlite3 --store-dir $R/runtime/gateway/artifacts --web-dist $R/mbnas_web/dist \
    --audit-log $R/runtime/gateway/logs/audit.jsonl --gw-log $R/runtime/logs/gateway.log --core-log $R/runtime/logs/core.log \
    --bench-device cpu --dump-dir $WORK/dump --during-train-cmd "bash /opt/harness/t4r3_capture.sh ${LABEL}_during_train3" "$@"
  log "harness exit=$?"
  bash /opt/harness/t4r3_capture.sh ${LABEL}_idle
fi
log "processes before stop:"; ps -eo pid,ppid,args | grep -E "mbnas|uvicorn" | grep -v grep | cut -c1-160
log "stop"; bash $R/scripts/stop.sh --profile owner > $EV/${LABEL}_stop_owner.txt 2>&1; log "stop exit=$?"; cat $EV/${LABEL}_stop_owner.txt
sleep 2; log "processes after stop:"; ps -eo pid,args | grep -E "mbnas|uvicorn|python" | grep -v grep | cut -c1-160; log "end"

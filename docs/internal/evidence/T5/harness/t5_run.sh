#!/bin/bash
# T5 public independent run (WP-8). Host side orchestration:
#  - owner core + owner worker started on the HOST from the extracted OWNER tree (t4f, its installed venv), separate
#    MBNAS_HOME / DB / port 18750, loopback only;
#  - gateway + CPU runner containers from the image built from the extracted PUBLIC ZIP (mbnas-public-cpu:t5),
#    docker run --rm --init --network host, gateway on 127.0.0.1:18760;
#  - client harness t45.py (stdlib only) drives the gateway; then core stop -> degraded/503 checks; leak checks.
# Secrets are generated here into 0600 files and never printed.
set -u
T=<T>
O=$T/t4f/mbnas_application            # owner tree (core host)
P=$T/t5/mbnas_application             # public tree (customer host)
RUN=$T/t5/run
CH=$T/t5/core_home
EV=$T/t5/evidence
IMG=mbnas-public-cpu:t5
CPORT=18750; GPORT=18760
PY=<PY310_ENV>/bin/python3.10
UIDGID="$(id -u):$(id -g)"
mkdir -p $RUN/runtime/outputs $RUN/data $RUN/config/public $CH/logs $EV
chmod 700 $RUN/config/public $CH
log() { echo "[t5 $(date -u +%FT%TZ)] $*"; }
gen() { $PY -I -c 'import secrets; print(secrets.token_urlsafe(48))'; }

phase_setup() {
  [ -d $RUN/data/synthetic4 ] || cp -r $P/examples/synthetic4 $RUN/data/synthetic4
  umask 077
  if [ ! -f $CH/core.env ]; then
    {
      echo "MBNAS_HOME=$CH"
      echo "MBNAS_CORE_HOST=127.0.0.1"; echo "MBNAS_CORE_PORT=$CPORT"
      echo "MBNAS_CORE_API_TOKEN=$(gen)"; echo "MBNAS_CORE_ARTIFACT_SIGNING_KEY=$(gen)"; echo "MBNAS_CORE_TICKET_KEY=$(gen)"
      echo "MBNAS_CORE_DATASET_ROOTS='[\"/srv/mbnas/data\"]'"
      echo "MBNAS_CORE_OUTPUT_ROOTS='[\"/srv/mbnas/runtime/outputs\", \"$CH/core/runs\"]'"
      echo "MBNAS_CORE_HEARTBEAT_S=2"
    } > $CH/core.env
  fi
  umask 022
  log "core.env written (0600; keys: $(sed 's/=.*//' $CH/core.env | tr '\n' ' '))"
}

core_env() { set -a; . $CH/core.env; set +a; export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null; }

phase_core_start() {
  ( core_env; cd $CH && exec setsid nohup $O/runtime/venv/bin/mbnas-core-api --host 127.0.0.1 --port $CPORT >> $CH/logs/core.log 2>&1 ) &
  sleep 0.5
  for i in $(seq 1 60); do
    c=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$CPORT/core/v1/console/health)
    [ "$c" = 401 ] && break; sleep 1
  done
  pgrep -f "mbnas-core-api --host 127.0.0.1 --port $CPORT" > $CH/core.pid
  log "core listening on 127.0.0.1:$CPORT (unauthenticated health -> $c) pid=$(cat $CH/core.pid | tr '\n' ' ')"
  [ "$c" = 401 ] || { log "core did not start"; exit 1; }
  ( core_env; cd $CH && exec setsid nohup $O/runtime/venv/bin/mbnas-core-owner-worker --owner >> $CH/logs/owner-worker.log 2>&1 ) &
  sleep 3
  pgrep -f "mbnas-core-owner-worker --owner" | while read p; do
    if [ "$(tr '\0' '\n' < /proc/$p/environ | grep -c "^MBNAS_HOME=$CH$")" = 1 ]; then echo $p; fi
  done > $CH/owner.pid
  log "owner worker pid=$(cat $CH/owner.pid | tr '\n' ' ')"
}

phase_principals() {
  umask 077
  if [ ! -f $RUN/config/public/gateway.env ]; then
    S=$( (core_env; cd $CH && $O/runtime/venv/bin/mbnas-core-principal create --kind server) | $PY -I -c 'import json,sys; d=json.load(sys.stdin); print(d["token"])')
    R=$( (core_env; cd $CH && $O/runtime/venv/bin/mbnas-core-principal create --kind runner --runner-id t5-cpu-runner) | $PY -I -c 'import json,sys; d=json.load(sys.stdin); print(d["token"])')
    if [ -z "$S" ] || [ -z "$R" ]; then log "principal issuance FAILED"; exit 1; fi
    {
      echo "MBNAS_GW_API_TOKEN=$(gen)"; echo "MBNAS_GW_ARTIFACT_SIGNING_KEY=$(gen)"
      echo "MBNAS_GW_CORE_URL=http://127.0.0.1:$CPORT"; echo "MBNAS_GW_CORE_TOKEN=$S"
      echo 'MBNAS_GW_OUTPUT_ROOTS=["/srv/mbnas/runtime/outputs"]'
    } > $RUN/config/public/gateway.env
    {
      echo "MBNAS_RUNNER_CORE_URL=http://127.0.0.1:$CPORT"; echo "MBNAS_RUNNER_TOKEN=$R"; echo "MBNAS_RUNNER_RUNNER_ID=t5-cpu-runner"
    } > $RUN/config/public/runner.env
    unset S R
  fi
  umask 022
  log "principals issued (server, runner t5-cpu-runner); env files 0600: $(stat -c '%a %n' $RUN/config/public/*.env | tr '\n' ' ')"
}

phase_containers_start() {
  docker run -d --rm --init --name t5-gw --network host --user $UIDGID \
    --env-file $RUN/config/public/gateway.env \
    -e MBNAS_HOME=/srv/mbnas/runtime -e MBNAS_GW_HOST=127.0.0.1 -e MBNAS_GW_PORT=$GPORT -e MBNAS_GW_WEB_DIST=/opt/mbnas/web/dist \
    -e "MBNAS_GW_DATASET_ROOTS=${GW_DS_ROOTS:-/srv/mbnas/data}" -e MBNAS_GW_ARTIFACT_STORE=/srv/mbnas/runtime/gateway/artifacts \
    -v $RUN/runtime:/srv/mbnas/runtime -v $RUN/data:/srv/mbnas/data:ro -v $RUN/shared_out:$RUN/shared_out ${EXTRA_MOUNT:-} $IMG gateway
  log "gateway container start exit=$?"
  for i in $(seq 1 60); do c=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$GPORT/api/v1/health); [ "$c" = 401 ] && break; sleep 1; done
  log "gateway answers (no token) -> $c"
  docker run -d --rm --init --name t5-runner --network host --user $UIDGID --shm-size 2g \
    --env-file $RUN/config/public/runner.env \
    -e MBNAS_HOME=/srv/mbnas/runtime -e "MBNAS_RUNNER_DATASET_ROOTS=${GW_DS_ROOTS:-/srv/mbnas/data}" -e MBNAS_RUNNER_ARTIFACT_TRANSFER=local \
    -e MBNAS_RUNNER_ARTIFACT_STORE_DIR=/srv/mbnas/runtime/gateway/artifacts -e MBNAS_RUNNER_ARTIFACT_STORE_MODE=same-uid \
    -e CUDA_VISIBLE_DEVICES= \
    -v $RUN/runtime:/srv/mbnas/runtime -v $RUN/data:/srv/mbnas/data:ro -v $RUN/shared_out:$RUN/shared_out ${EXTRA_MOUNT:-} $IMG runner
  log "runner container start exit=$?"
  sleep 8
  docker ps --filter name=t5- --format '{{.Names}} {{.Image}} {{.Status}}'
  $PY -I - <<EOF
import json, sqlite3
con = sqlite3.connect("file:$CH/core/db/mbnas.sqlite3?mode=ro", uri=True)
print("runners:", con.execute("select id, status, device, device_uuid, device_name from runners").fetchall())
EOF
}

harness() {
  $PY -I $T/harness/t45.py --gw http://127.0.0.1:$GPORT --token-env $RUN/config/public/gateway.env \
    --redact-env $RUN/config/public/gateway.env:MBNAS_GW_CORE_TOKEN --redact-env $RUN/config/public/gateway.env:MBNAS_GW_ARTIFACT_SIGNING_KEY \
    --redact-env $RUN/config/public/runner.env:MBNAS_RUNNER_TOKEN --redact-env $CH/core.env:MBNAS_CORE_API_TOKEN \
    --redact-env $CH/core.env:MBNAS_CORE_ARTIFACT_SIGNING_KEY --redact-env $CH/core.env:MBNAS_CORE_TICKET_KEY \
    --label ${T5_LABEL:-t5} --evidence-dir $EV --models $P/examples/models --gen-protocol $P/examples/protocol.cpu_generation_only.json \
    --dataset-yaml /srv/mbnas/data/synthetic4/data.yaml --output-root ${T5_OUT:-/srv/mbnas/runtime/outputs} --split-paths \
    --db $CH/core/db/mbnas.sqlite3 --store-dir $RUN/runtime/gateway/artifacts --web-dist $P/mbnas_web/dist \
    --audit-log $RUN/runtime/gateway/logs/audit.jsonl --gw-log $EV/t5_gateway_container.log --core-log $CH/logs/core.log "$@"
}

phase_core_stop() {
  for f in $CH/owner.pid $CH/core.pid; do
    for p in $(cat $f 2>/dev/null); do kill -TERM $p 2>/dev/null && log "SIGTERM $p ($f)"; done
  done
  sleep 4
  for f in $CH/owner.pid $CH/core.pid; do for p in $(cat $f 2>/dev/null); do [ -d /proc/$p ] && log "STILL ALIVE $p" || log "stopped $p"; done; done
}

phase_containers_logs() {
  docker logs t5-gw > $EV/t5_gateway_container.log 2>&1
  docker logs t5-runner > $EV/t5_runner_container.log 2>&1
  log "container logs captured ($(wc -l < $EV/t5_gateway_container.log) / $(wc -l < $EV/t5_runner_container.log) lines)"
}

phase_containers_stop() {
  docker stop -t 60 t5-gw t5-runner; log "docker stop exit=$?"; sleep 2
  log "containers left: $(docker ps -a --filter name=t5- --format '{{.Names}}' | tr '\n' ' ')"
}

case "${1:-}" in
  setup) phase_setup ;;
  core_start) phase_core_start ;;
  principals) phase_principals ;;
  containers_start) phase_containers_start ;;
  harness) shift; harness "$@" ;;
  core_stop) phase_core_stop ;;
  logs) phase_containers_logs ;;
  containers_stop) phase_containers_stop ;;
  *) echo "usage: $0 setup|core_start|principals|containers_start|harness <args>|core_stop|logs|containers_stop"; exit 2 ;;
esac

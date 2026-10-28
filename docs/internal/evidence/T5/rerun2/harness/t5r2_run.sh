#!/bin/bash
# T5 RERUN2 (WP-8, P2-FINAL-2 brief). Same method as the FINAL re-run (t5f_run.sh); host core from the CURRENT owner ZIP
# a43a8a2e... install (<T>/final2/ownercur), image mbnas-public-cpu:final2 from the final public ZIP bc0500bd....
# Former header: Host side orchestration, same method as run 1 (harness/t5_run.sh) with the final ZIPs:
#  - owner core + owner worker on the HOST from the extracted FINAL owner tree (<T>/final/t4, its venv), separate
#    MBNAS_HOME/DB, port 18750, loopback only, MBNAS_CORE_DEVICE_SOURCE=runners (split deployment: the runners are on the
#    customer side; no NVML read on this host), a core-only dataset root (must never appear in the gateway health);
#  - gateway + CPU runner containers from mbnas-public-cpu:final (built from the extracted FINAL public ZIP),
#    docker run --rm --init --network host, gateway 127.0.0.1:18760; customer-only output root /srv/mbnas/runtime/outputs;
#  - the runner id is NOT configured: the image generates runner-<8 hex> into the mounted runtime (DD-A61 b), the core
#    operator issues the runner principal for that id.
# Secrets are generated here into 0600 files and never printed.
set -u
T=<T>
O=$T/final2/ownercur/mbnas_application  # owner tree (core host): current owner ZIP a43a8a2e... install
P=$T/final2/t5/mbnas_application       # public tree (customer host): final public ZIP bc0500bd...
RUN=$T/final2/t5/run
CH=$T/final2/t5/core_home
EV=$T/final2/t5/evidence
DUMP=$T/final2/t5/dump          # T8: every public response body (scratch, not copied to evidence)
CAP=$T/final2/t5/proc_capture    # T8: /proc/<pid>/environ of the container processes (0600, scratch only)
IMG=mbnas-public-cpu:final2
CPORT=18750; GPORT=18760
PY=<PY310_ENV>/bin/python3.10
UIDGID="$(id -u):$(id -g)"
mkdir -p $RUN/runtime/outputs $RUN/data $RUN/config/public $CH/logs $CH/core_only_data $EV $DUMP $CAP
chmod 700 $CAP
chmod 700 $RUN/config/public $CH
log() { echo "[t5r2 $(date -u +%FT%TZ)] $*"; }
gen() { $PY -I -c 'import secrets; print(secrets.token_urlsafe(48))'; }

phase_setup() {
  [ -d $RUN/data/synthetic4 ] || cp -r $P/examples/synthetic4 $RUN/data/synthetic4
  umask 077
  if [ ! -f $CH/core.env ]; then
    {
      echo "MBNAS_HOME=$CH"
      echo "MBNAS_CORE_HOST=127.0.0.1"; echo "MBNAS_CORE_PORT=$CPORT"
      echo "MBNAS_CORE_API_TOKEN=$(gen)"; echo "MBNAS_CORE_ARTIFACT_SIGNING_KEY=$(gen)"; echo "MBNAS_CORE_TICKET_KEY=$(gen)"
      echo "MBNAS_CORE_DATASET_ROOTS='[\"$CH/core_only_data\"]'"
      echo "MBNAS_CORE_HEARTBEAT_S=2"
      echo "MBNAS_CORE_DEVICE_SOURCE=runners"
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

phase_runner_id() {
  # the public image creates the runner id file in the mounted runtime on first use (no MBNAS_RUNNER_RUNNER_ID set)
  docker run --rm --user $UIDGID -e MBNAS_HOME=/srv/mbnas/runtime -v $RUN/runtime:/srv/mbnas/runtime --entrypoint python $IMG \
    -c "from mbnas_runner.config import persistent_runner_id; print(persistent_runner_id())" > $RUN/runner_id.txt
  log "runner id generation exit=$? id=$(cat $RUN/runner_id.txt) file=$(cat $RUN/runtime/runner/runner_id) host=$(hostname)"
}

phase_principals() {
  umask 077
  RID=$(cat $RUN/runtime/runner/runner_id)
  if [ ! -f $RUN/config/public/gateway.env ]; then
    S=$( (core_env; cd $CH && $O/runtime/venv/bin/mbnas-core-principal create --kind server) | $PY -I -c 'import json,sys; d=json.load(sys.stdin); print(d["token"])')
    R=$( (core_env; cd $CH && $O/runtime/venv/bin/mbnas-core-principal create --kind runner --runner-id "$RID") | $PY -I -c 'import json,sys; d=json.load(sys.stdin); print(d["token"])')
    if [ -z "$S" ] || [ -z "$R" ]; then log "principal issuance FAILED"; exit 1; fi
    {
      echo "MBNAS_GW_API_TOKEN=$(gen)"; echo "MBNAS_GW_ARTIFACT_SIGNING_KEY=$(gen)"
      echo "MBNAS_GW_CORE_URL=http://127.0.0.1:$CPORT"; echo "MBNAS_GW_CORE_TOKEN=$S"
      echo 'MBNAS_GW_OUTPUT_ROOTS=["/srv/mbnas/runtime/outputs"]'
    } > $RUN/config/public/gateway.env
    {
      echo "MBNAS_RUNNER_CORE_URL=http://127.0.0.1:$CPORT"; echo "MBNAS_RUNNER_TOKEN=$R"
    } > $RUN/config/public/runner.env
    unset S R
  fi
  umask 022
  log "principals issued (server; runner bound to $RID); env files 0600: $(stat -c '%a' $RUN/config/public/*.env | tr '\n' ' '); runner.env has RUNNER_ID line: $(grep -c '^MBNAS_RUNNER_RUNNER_ID=' $RUN/config/public/runner.env)"
}

phase_containers_start() {
  docker run -d --rm --init --name t5r2-gw --network host --user $UIDGID \
    --env-file $RUN/config/public/gateway.env \
    -e MBNAS_HOME=/srv/mbnas/runtime -e MBNAS_GW_HOST=127.0.0.1 -e MBNAS_GW_PORT=$GPORT -e MBNAS_GW_WEB_DIST=/opt/mbnas/web/dist \
    -e MBNAS_GW_DATASET_ROOTS=/srv/mbnas/data -e MBNAS_GW_ARTIFACT_STORE=/srv/mbnas/runtime/gateway/artifacts \
    -v $RUN/runtime:/srv/mbnas/runtime -v $RUN/data:/srv/mbnas/data:ro $IMG gateway
  log "gateway container start exit=$?"
  for i in $(seq 1 60); do c=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$GPORT/api/v1/health); [ "$c" = 401 ] && break; sleep 1; done
  log "gateway answers (no token) -> $c"
  docker run -d --rm --init --name t5r2-runner --network host --user $UIDGID --shm-size 2g \
    --env-file $RUN/config/public/runner.env \
    -e MBNAS_HOME=/srv/mbnas/runtime -e MBNAS_RUNNER_DATASET_ROOTS=/srv/mbnas/data -e MBNAS_RUNNER_ARTIFACT_TRANSFER=local \
    -e MBNAS_RUNNER_ARTIFACT_STORE_DIR=/srv/mbnas/runtime/gateway/artifacts -e MBNAS_RUNNER_ARTIFACT_STORE_MODE=same-uid \
    -e CUDA_VISIBLE_DEVICES= \
    -v $RUN/runtime:/srv/mbnas/runtime -v $RUN/data:/srv/mbnas/data:ro $IMG runner
  log "runner container start exit=$?"
  sleep 8
  docker ps --filter name=t5r2- --format '{{.Names}} {{.Image}} {{.Status}}'
  $PY -I - <<EOF
import sqlite3
con = sqlite3.connect("file:$CH/core/db/mbnas.sqlite3?mode=ro", uri=True)
print("runners:", con.execute("select id, status, device, device_uuid, device_name from runners").fetchall())
EOF
}

harness() {
  $PY -I $T/harness/t45r2.py --gw http://127.0.0.1:$GPORT --token-env $RUN/config/public/gateway.env \
    --redact-env $RUN/config/public/gateway.env:MBNAS_GW_CORE_TOKEN --redact-env $RUN/config/public/gateway.env:MBNAS_GW_ARTIFACT_SIGNING_KEY \
    --redact-env $RUN/config/public/runner.env:MBNAS_RUNNER_TOKEN --redact-env $CH/core.env:MBNAS_CORE_API_TOKEN \
    --redact-env $CH/core.env:MBNAS_CORE_ARTIFACT_SIGNING_KEY --redact-env $CH/core.env:MBNAS_CORE_TICKET_KEY \
    --label ${T5_LABEL:-t5r2} --evidence-dir $EV --models $P/examples/models --gen-protocol $P/examples/protocol.cpu_generation_only.json \
    --dataset-yaml /srv/mbnas/data/synthetic4/data.yaml --output-root /srv/mbnas/runtime/outputs --split-paths \
    --db $CH/core/db/mbnas.sqlite3 --store-dir $RUN/runtime/gateway/artifacts --web-dist $P/mbnas_web/dist \
    --audit-log $RUN/runtime/gateway/logs/audit.jsonl --gw-log $EV/t5r2_gateway_container.log --core-log $CH/logs/core.log \
    --bench-device cpu --forbid-health "$CH" --forbid-health "core_only_data" \
    --expect-health-allowlists '{"dataset_roots": ["/srv/mbnas/data"], "output_roots": ["/srv/mbnas/runtime/outputs"], "archive_roots": []}' \
    --dump-dir $DUMP --during-train-cmd "bash $T/harness/t5r2_run.sh capture_env during_train3" "$@"
}

phase_core_stop() {
  for f in $CH/owner.pid $CH/core.pid; do
    for p in $(cat $f 2>/dev/null); do kill -TERM $p 2>/dev/null && log "SIGTERM $p ($(basename $f))"; done
  done
  sleep 4
  for f in $CH/owner.pid $CH/core.pid; do for p in $(cat $f 2>/dev/null); do [ -d /proc/$p ] && log "STILL ALIVE $p" || log "stopped $p"; done; done
}

phase_containers_logs() {
  docker logs t5r2-gw > $EV/t5r2_gateway_container.log 2>&1
  docker logs t5r2-runner > $EV/t5r2_runner_container.log 2>&1
  log "container logs captured ($(wc -l < $EV/t5r2_gateway_container.log) / $(wc -l < $EV/t5r2_runner_container.log) lines)"
}

phase_containers_stop() {
  docker stop -t 60 t5r2-gw t5r2-runner; log "docker stop exit=$?"; sleep 2
  log "containers left: [$(docker ps -a --filter name=t5r2- --format '{{.Names}}' | tr '\n' ' ')]"
}

phase_capture_env() {
  # /proc/<pid>/environ + cmdline of every process in both containers (runner job child included when a job runs)
  tag=${1:-snapshot}
  umask 077
  for c in t5r2-gw t5r2-runner; do
    docker exec $c sh -c 'for p in /proc/[0-9]*; do if [ -r $p/environ ]; then echo "### pid=${p#/proc/} cmd=$(tr "\0" " " < $p/cmdline | cut -c1-160)"; tr "\0" "\n" < $p/environ; echo; fi; done' > $CAP/${c}_${tag}.txt 2>&1
    echo "$c $tag exit=$? processes=$(grep -c '^### pid=' $CAP/${c}_${tag}.txt)"
  done
  umask 022
}

case "${1:-}" in
  capture_env) shift; phase_capture_env "$@" ;;
  setup) phase_setup ;;
  core_start) phase_core_start ;;
  runner_id) phase_runner_id ;;
  principals) phase_principals ;;
  containers_start) phase_containers_start ;;
  harness) shift; harness "$@" ;;
  core_stop) phase_core_stop ;;
  logs) phase_containers_logs ;;
  containers_stop) phase_containers_stop ;;
  *) echo "usage: $0 setup|core_start|runner_id|principals|containers_start|harness <args>|capture_env <tag>|core_stop|logs|containers_stop"; exit 2 ;;
esac

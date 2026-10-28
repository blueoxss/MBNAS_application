#!/bin/bash
# T5 DD-A75 attempt (WP-8). Host owner core from the DD-A75 owner ZIP 9f03c484... install of the T4 attempt (its ROOT
# has a blank and a unicode word); own core home with a blank + unicode word; public containers from
# mbnas-public-cpu:final2 (public ZIP bc0500bd..., unchanged); the runner work root inside the container is planted
# ("/srv/mbnas/runtime/rw 작업 Zq9mT4"). Core processes read their secrets with --env-file (only MBNAS_HOME exported).
# Secrets are generated into 0600 files and never printed. Every path is quoted (blanks / unicode).
set -u
T=<T>   # harness location
W2=<SP>/p2a75
O="$W2/t4work/t4 공백 rootX7kQ2/mbnas_application"     # owner tree (core host) = DD-A75 install
RUN="$W2/t5work/run"                                   # customer side: runtime, data, config (bind-mounted)
CH="$W2/t5work/t5 공백 coreX8mR5"                       # core home (owner host), blank + unicode + token
EV="$W2/t5work/evidence"; DUMP="$W2/t5work/dump"; CAP="$W2/t5work/proc_capture"
IMG=mbnas-public-cpu:final2
RUNNER_WR="/srv/mbnas/runtime/rw 작업 Zq9mT4"
CPORT=18750; GPORT=18760
PY=<PY310_ENV>/bin/python3.10
UIDGID="$(id -u):$(id -g)"
mkdir -p "$RUN/runtime/outputs" "$RUN/data" "$RUN/config/public" "$CH/logs" "$CH/core_only_data" "$EV" "$DUMP" "$CAP"
chmod 700 "$RUN/config/public" "$CH" "$CAP"
log() { echo "[t5a75 $(date -u +%FT%TZ)] $*"; }
gen() { "$PY" -I -c 'import secrets; print(secrets.token_urlsafe(48))'; }
core_env() { export MBNAS_HOME="$CH" CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null; }

phase_setup() {
  [ -d "$RUN/data/synthetic4" ] || cp -r "$O/examples/synthetic4" "$RUN/data/synthetic4"
  umask 077
  if [ ! -f "$CH/core.env" ]; then
    {
      echo "MBNAS_CORE_HOST=127.0.0.1"; echo "MBNAS_CORE_PORT=$CPORT"
      echo "MBNAS_CORE_API_TOKEN=$(gen)"; echo "MBNAS_CORE_ARTIFACT_SIGNING_KEY=$(gen)"; echo "MBNAS_CORE_TICKET_KEY=$(gen)"
      "$PY" -I -c 'import json,sys; print("MBNAS_CORE_DATASET_ROOTS=\x27" + json.dumps([sys.argv[1]], ensure_ascii=False) + "\x27")' "$CH/core_only_data"
      echo "MBNAS_CORE_HEARTBEAT_S=2"
      echo "MBNAS_CORE_DEVICE_SOURCE=runners"
    } > "$CH/core.env"
  fi
  umask 022
  log "core.env written (0600; keys: $(sed 's/=.*//' "$CH/core.env" | tr '\n' ' '))"
}

phase_core_start() {
  ( core_env; cd "$CH" && exec setsid nohup "$O/runtime/venv/bin/mbnas-core-api" --env-file "$CH/core.env" --host 127.0.0.1 --port $CPORT >> "$CH/logs/core.log" 2>&1 ) &
  sleep 0.5
  for i in $(seq 1 60); do
    c=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$CPORT/core/v1/console/health)
    [ "$c" = 401 ] && break; sleep 1
  done
  pgrep -f "mbnas-core-api --env-file $CH/core.env" > "$CH/core.pid"
  log "core listening on 127.0.0.1:$CPORT (unauthenticated health -> $c) pid=$(tr '\n' ' ' < "$CH/core.pid")"
  [ "$c" = 401 ] || { log "core did not start"; exit 1; }
  ( core_env; cd "$CH" && exec setsid nohup "$O/runtime/venv/bin/mbnas-core-owner-worker" --owner --env-file "$CH/core.env" >> "$CH/logs/owner-worker.log" 2>&1 ) &
  sleep 3
  pgrep -f "mbnas-core-owner-worker --owner --env-file $CH/core.env" > "$CH/owner.pid"
  log "owner worker pid=$(tr '\n' ' ' < "$CH/owner.pid")"
}

phase_runner_id() {
  docker run --rm --user $UIDGID -e MBNAS_HOME=/srv/mbnas/runtime -v "$RUN/runtime:/srv/mbnas/runtime" --entrypoint python $IMG \
    -c "from mbnas_runner.config import persistent_runner_id; print(persistent_runner_id())" > "$RUN/runner_id.txt"
  log "runner id generation exit=$? id=$(cat "$RUN/runner_id.txt") host=$(hostname)"
}

phase_principals() {
  umask 077
  RID=$(cat "$RUN/runtime/runner/runner_id")
  if [ ! -f "$RUN/config/public/gateway.env" ]; then
    S=$( (core_env; cd "$CH" && "$O/runtime/venv/bin/mbnas-core-principal" --env-file "$CH/core.env" create --kind server) | "$PY" -I -c 'import json,sys; print(json.load(sys.stdin)["token"])')
    R=$( (core_env; cd "$CH" && "$O/runtime/venv/bin/mbnas-core-principal" --env-file "$CH/core.env" create --kind runner --runner-id "$RID") | "$PY" -I -c 'import json,sys; print(json.load(sys.stdin)["token"])')
    if [ -z "$S" ] || [ -z "$R" ]; then log "principal issuance FAILED"; exit 1; fi
    {
      echo "MBNAS_GW_API_TOKEN=$(gen)"; echo "MBNAS_GW_ARTIFACT_SIGNING_KEY=$(gen)"
      echo "MBNAS_GW_CORE_URL=http://127.0.0.1:$CPORT"; echo "MBNAS_GW_CORE_TOKEN=$S"
      echo 'MBNAS_GW_OUTPUT_ROOTS=["/srv/mbnas/runtime/outputs"]'
    } > "$RUN/config/public/gateway.env"
    { echo "MBNAS_RUNNER_CORE_URL=http://127.0.0.1:$CPORT"; echo "MBNAS_RUNNER_TOKEN=$R"; } > "$RUN/config/public/runner.env"
    unset S R
  fi
  umask 022
  log "principals issued (server; runner bound to $RID); runner.env has RUNNER_ID line: $(grep -c '^MBNAS_RUNNER_RUNNER_ID=' "$RUN/config/public/runner.env")"
}

phase_containers_start() {
  docker run -d --rm --init --name t5a75-gw --network host --user $UIDGID --env-file "$RUN/config/public/gateway.env" \
    -e MBNAS_HOME=/srv/mbnas/runtime -e MBNAS_GW_HOST=127.0.0.1 -e MBNAS_GW_PORT=$GPORT -e MBNAS_GW_WEB_DIST=/opt/mbnas/web/dist \
    -e MBNAS_GW_DATASET_ROOTS=/srv/mbnas/data -e MBNAS_GW_ARTIFACT_STORE=/srv/mbnas/runtime/gateway/artifacts \
    -v "$RUN/runtime:/srv/mbnas/runtime" -v "$RUN/data:/srv/mbnas/data:ro" $IMG gateway
  log "gateway container start exit=$?"
  for i in $(seq 1 60); do c=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$GPORT/api/v1/health); [ "$c" = 401 ] && break; sleep 1; done
  log "gateway answers (no token) -> $c"
  docker run -d --rm --init --name t5a75-runner --network host --user $UIDGID --shm-size 2g --env-file "$RUN/config/public/runner.env" \
    -e MBNAS_HOME=/srv/mbnas/runtime -e MBNAS_RUNNER_DATASET_ROOTS=/srv/mbnas/data -e MBNAS_RUNNER_ARTIFACT_TRANSFER=local \
    -e MBNAS_RUNNER_ARTIFACT_STORE_DIR=/srv/mbnas/runtime/gateway/artifacts -e MBNAS_RUNNER_ARTIFACT_STORE_MODE=same-uid \
    -e "MBNAS_RUNNER_WORK_ROOT=$RUNNER_WR" -e CUDA_VISIBLE_DEVICES= \
    -v "$RUN/runtime:/srv/mbnas/runtime" -v "$RUN/data:/srv/mbnas/data:ro" $IMG runner
  log "runner container start exit=$?"
  sleep 8
  docker ps --filter name=t5a75- --format '{{.Names}} {{.Image}} {{.Status}}'
  "$PY" -I -c 'import sqlite3,sys; con=sqlite3.connect("file:"+sys.argv[1]+"?mode=ro", uri=True); print("runners:", con.execute("select id, status, device from runners").fetchall()); print("schema_version:", con.execute("select value from meta where key=\x27schema_version\x27").fetchone()[0])' "$CH/core/db/mbnas.sqlite3"
}

harness() {
  "$O/runtime/venv/bin/python" -I "$T/harness/t45r3.py" --gw http://127.0.0.1:$GPORT --token-env "$RUN/config/public/gateway.env" \
    --redact-env "$RUN/config/public/gateway.env:MBNAS_GW_CORE_TOKEN" --redact-env "$RUN/config/public/gateway.env:MBNAS_GW_ARTIFACT_SIGNING_KEY" \
    --redact-env "$RUN/config/public/runner.env:MBNAS_RUNNER_TOKEN" --redact-env "$CH/core.env:MBNAS_CORE_API_TOKEN" \
    --redact-env "$CH/core.env:MBNAS_CORE_ARTIFACT_SIGNING_KEY" --redact-env "$CH/core.env:MBNAS_CORE_TICKET_KEY" \
    --label "${T5_LABEL:-t5a75}" --evidence-dir "$EV" --models "$O/examples/models" --gen-protocol "$O/examples/protocol.cpu_generation_only.json" \
    --dataset-yaml /srv/mbnas/data/synthetic4/data.yaml --output-root /srv/mbnas/runtime/outputs --split-paths \
    --db "$CH/core/db/mbnas.sqlite3" --store-dir "$RUN/runtime/gateway/artifacts" --web-dist "$O/mbnas_web/dist" \
    --audit-log "$RUN/runtime/gateway/logs/audit.jsonl" --gw-log "$EV/t5a75_gateway_container.log" --core-log "$CH/logs/core.log" \
    --bench-device cpu --forbid-health "$CH" --forbid-health "core_only_data" --forbid-health "coreX8mR5" --forbid-health "rootX7kQ2" \
    --expect-health-allowlists '{"dataset_roots": ["/srv/mbnas/data"], "output_roots": ["/srv/mbnas/runtime/outputs"], "archive_roots": []}' \
    --dump-dir "$DUMP" --during-train-cmd "bash $T/harness/t5a75_run.sh capture_env during_train3" \
    --plant-fragment "owner_install_token=rootX7kQ2" --plant-fragment "core_home_token=coreX8mR5" --plant-fragment "owner_unicode=공백" \
    --plant-fragment "runner_wr_token=Zq9mT4" --plant-fragment "runner_wr_unicode=작업" "$@"
}

phase_capture_env() {
  tag=${1:-snapshot}
  umask 077
  for c in t5a75-gw t5a75-runner; do
    docker exec $c sh -c 'for p in /proc/[0-9]*; do if [ -r $p/environ ]; then echo "### pid=${p#/proc/} cmd=$(tr "\0" " " < $p/cmdline | cut -c1-160)"; tr "\0" "\n" < $p/environ; echo; fi; done' > "$CAP/${c}_${tag}.txt" 2>&1
    echo "$c $tag exit=$? processes=$(grep -c '^### pid=' "$CAP/${c}_${tag}.txt")"
  done
  for p in $(cat "$CH/core.pid" "$CH/owner.pid" 2>/dev/null); do
    [ -r /proc/$p/environ ] && { echo "### pid=$p cmd=$(tr '\0' ' ' < /proc/$p/cmdline | cut -c1-160)"; tr '\0' '\n' < /proc/$p/environ; echo; }
  done > "$CAP/core_host_${tag}.txt"
  echo "core host $tag processes=$(grep -c '^### pid=' "$CAP/core_host_${tag}.txt")"
  umask 022
}

phase_core_stop() {
  for f in "$CH/owner.pid" "$CH/core.pid"; do
    for p in $(cat "$f" 2>/dev/null); do kill -TERM $p 2>/dev/null && log "SIGTERM $p ($(basename "$f"))"; done
  done
  sleep 4
  for f in "$CH/owner.pid" "$CH/core.pid"; do for p in $(cat "$f" 2>/dev/null); do [ -d /proc/$p ] && log "STILL ALIVE $p" || log "stopped $p"; done; done
}

phase_containers_logs() {
  docker logs t5a75-gw > "$EV/t5a75_gateway_container.log" 2>&1
  docker logs t5a75-runner > "$EV/t5a75_runner_container.log" 2>&1
  log "container logs captured ($(wc -l < "$EV/t5a75_gateway_container.log") / $(wc -l < "$EV/t5a75_runner_container.log") lines)"
}

phase_containers_stop() {
  docker stop -t 60 t5a75-gw t5a75-runner; log "docker stop exit=$?"; sleep 2
  log "containers left: [$(docker ps -a --filter name=t5a75- --format '{{.Names}}' | tr '\n' ' ')]"
}

case "${1:-}" in
  setup) phase_setup ;;
  core_start) phase_core_start ;;
  runner_id) phase_runner_id ;;
  principals) phase_principals ;;
  containers_start) phase_containers_start ;;
  harness) shift; harness "$@" ;;
  capture_env) shift; phase_capture_env "$@" ;;
  core_stop) phase_core_stop ;;
  logs) phase_containers_logs ;;
  containers_stop) phase_containers_stop ;;
  *) echo "usage: $0 setup|core_start|runner_id|principals|containers_start|harness <args>|capture_env <tag>|core_stop|logs|containers_stop"; exit 2 ;;
esac

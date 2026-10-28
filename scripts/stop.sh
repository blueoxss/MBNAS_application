#!/usr/bin/env bash
# stop.sh - stop the processes that scripts/start.sh started for one profile (own pid files only).
# WP-7A; contract docs/internal/WORK_PACKAGES/WP-7_OPS_PACKAGING.md section 1.
set -u -o pipefail
unset CDPATH
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage: bash scripts/stop.sh --profile owner|public [--role core|gateway|runner|web|all] [--timeout-s <seconds>]

Stops only processes recorded in this tree's pid files (runtime/pids/<name>.pid); never pkill/killall. A pid is
signalled only when it still is this tree's process: one of its argv entries is the role's program name
(mbnas-core-api, mbnas-core-owner-worker, mbnas-gateway, mbnas-runner, vite.js) and an argv entry lies under ROOT
or its working directory is ROOT. Otherwise (process gone, pid reused) the stale pid file is removed and nothing is
signalled.

Options:
  --profile owner|public  (required) public: gateway, runner, web; owner: + core API and owner worker.
  --role <role>           core (core API + owner worker; owner only), gateway, runner, web, or all (default: every
                          process of the profile, stopped in the order web, runner, gateway, owner worker, core).
  --timeout-s <seconds>   wait after SIGTERM before SIGKILL (default 30). SIGKILL goes to the process group the
                          process leads (start.sh runs it in its own session) and to its remaining descendants;
                          each of them is re-checked right before the signal (same start time as when it was
                          collected), so a pid that was reused meanwhile by another process is not signalled.
  -h, --help              show this help and exit.

Exit codes: 0 every requested process is stopped (or was not running); 1 a process survived SIGKILL; 2 usage error.
EOF
}

die_usage() { echo "stop.sh: $*" >&2; echo "Try: bash scripts/stop.sh --help" >&2; exit 2; }
need_value() { [ $# -ge 2 ] && [ -n "$2" ] || die_usage "option $1 needs a value"; }

PROFILE=""
ROLE="all"
TIMEOUT_S=30
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) need_value "$@"; PROFILE="$2"; shift 2 ;;
    --profile=*) PROFILE="${1#*=}"; shift ;;
    --role) need_value "$@"; ROLE="$2"; shift 2 ;;
    --role=*) ROLE="${1#*=}"; shift ;;
    --timeout-s) need_value "$@"; TIMEOUT_S="$2"; shift 2 ;;
    --timeout-s=*) TIMEOUT_S="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die_usage "unknown option: $1" ;;
  esac
done
case "$PROFILE" in
  owner|public) ;;
  "") die_usage "--profile owner|public is required" ;;
  *) die_usage "--profile must be owner or public, got '$PROFILE'" ;;
esac
case "$ROLE" in core|gateway|runner|web|all) ;; *) die_usage "--role must be core, gateway, runner, web or all" ;; esac
case "$TIMEOUT_S" in ''|*[!0-9]*) die_usage "--timeout-s needs a whole number of seconds" ;; esac

ROOT_REAL="$(cd "$ROOT" && pwd -P)"
PIDS="$ROOT/runtime/pids"

if [ "$PROFILE" = "public" ] && [ "$ROLE" = "core" ]; then
  echo "[stop] the public profile runs no core: nothing to stop"
  exit 0
fi
case "$ROLE" in
  core) PROCS=(owner-worker core) ;;
  all) if [ "$PROFILE" = "owner" ]; then PROCS=(web runner gateway owner-worker core); else PROCS=(web runner gateway); fi ;;
  *) PROCS=("$ROLE") ;;
esac

# ---- process identity (same block in start.sh and stop.sh) -----------------------------------------------------------
proc_marker() {
  case "$1" in
    core) echo "mbnas-core-api" ;;
    owner-worker) echo "mbnas-core-owner-worker" ;;
    gateway) echo "mbnas-gateway" ;;
    runner) echo "mbnas-runner" ;;
    web) echo "vite.js" ;;
    *) echo "" ;;
  esac
}
proc_state() {  # pid -> one-letter process state, empty when the process is gone
  local stat="" rest=""
  stat="$(cat "/proc/$1/stat" 2>/dev/null)" || { echo ""; return 0; }
  rest="${stat##*) }"
  echo "${rest%% *}"
}
is_ours() {  # name pid -> 0 when pid is a live process of this tree's <name>: the marker is one of its argv basenames
  local name="$1" pid="$2" marker="" arg="" st="" cwd="" found=1 rooted=1  # and an argv entry lies under ROOT or
  # its cwd is ROOT. Locals start set: a process that exits mid-check has an empty cmdline (read assigns nothing)
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  st="$(proc_state "$pid")"
  case "$st" in ''|Z|X) return 1 ;; esac
  [ -r "/proc/$pid/cmdline" ] || return 1
  marker="$(proc_marker "$name")"
  while IFS= read -r -d '' arg || [ -n "$arg" ]; do
    [ "${arg##*/}" = "$marker" ] && found=0
    case "$arg" in "$ROOT"/*|"$ROOT_REAL"/*) rooted=0 ;; esac
  done <"/proc/$pid/cmdline"
  cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
  [ "$cwd" = "$ROOT_REAL" ] && rooted=0
  [ "$found" -eq 0 ] && [ "$rooted" -eq 0 ]
}
read_pid() {  # pid file -> pid, empty when missing or not a number
  local pid=""
  [ -f "$1" ] && read -r pid _ <"$1" 2>/dev/null
  case "$pid" in ''|*[!0-9]*) echo "" ;; *) echo "$pid" ;; esac
}
# ---- end of the shared block -------------------------------------------------------------------------------------------

proc_starttime() {  # pid -> start time of the process (clock ticks after boot, /proc/<pid>/stat field 22), empty when
  # the process is gone. (pid, start time) names one process incarnation: a pid reused later has another start time.
  local stat="" rest="" f=()
  stat="$(cat "/proc/$1/stat" 2>/dev/null)" || { echo ""; return 0; }
  rest="${stat##*) }"
  read -r -a f <<<"$rest"  # f[0] = field 3 (state) ... f[19] = field 22 (starttime)
  echo "${f[19]:-}"
}

kill_verified() {  # pid starttime -> SIGKILL the pid only while it is still the process collected earlier (same start
  # time). Prints killed | gone (no such process any more) | replaced (the pid now names another process: not signalled)
  local now=""
  now="$(proc_starttime "$1")"
  if [ -z "$now" ]; then
    echo "gone"
  elif [ -z "$2" ] || [ "$now" != "$2" ]; then
    echo "replaced"
  else
    kill -KILL "$1" 2>/dev/null
    echo "killed"
  fi
}

descendants() {  # pid -> "<pid> <start time>" of each descendant, read in one pass over /proc before a SIGKILL
  # (children are re-parented once their parent dies; the start time lets kill_verified skip a reused pid)
  local top="$1" d="" stat="" rest="" p="" ppid="" cur="" f=()
  declare -A kids=() started=()
  for d in /proc/[0-9]*; do
    p="${d#/proc/}"
    stat="$(cat "$d/stat" 2>/dev/null)" || continue
    rest="${stat##*) }"
    read -r -a f <<<"$rest"
    ppid="${f[1]:-}"
    case "$ppid" in ''|*[!0-9]*) continue ;; esac
    kids[$ppid]="${kids[$ppid]:-} $p"
    started[$p]="${f[19]:-}"
  done
  local queue=("$top")
  while [ ${#queue[@]} -gt 0 ]; do
    cur="${queue[0]}"
    queue=("${queue[@]:1}")
    for p in ${kids[$cur]:-}; do
      echo "$p ${started[$p]:-}"
      queue+=("$p")
    done
  done
}

pgid_of() {
  local stat="" rest="" pg=""
  stat="$(cat "/proc/$1/stat" 2>/dev/null)" || return 1
  rest="${stat##*) }"
  read -r _ _ pg _ <<<"$rest"  # fields after the command name: state ppid pgrp ...
  echo "$pg"
}

group_still_ours() {  # leader leader_start "<pid> <start>" lines -> 0 while process group <leader> is still the group
  # start.sh created: its leader is the same process (same start time), or a collected descendant that is still the
  # same process is still in that group (a process group id is not reused while the group has a member)
  local lead="$1" lead_t="$2" p="" t=""
  if [ -n "$lead_t" ] && [ "$(proc_starttime "$lead")" = "$lead_t" ]; then
    return 0
  fi
  while read -r p t; do
    [ -n "$p" ] && [ -n "$t" ] || continue
    if [ "$(proc_starttime "$p")" = "$t" ] && [ "$(pgid_of "$p" 2>/dev/null)" = "$lead" ]; then
      return 0
    fi
  done <<<"$3"
  return 1
}

wait_gone() {  # name pid seconds -> 0 once the pid is no longer this tree's live process
  local name="$1" pid="$2" end=$((SECONDS + $3))
  while is_ours "$name" "$pid"; do
    [ "$SECONDS" -ge "$end" ] && return 1
    sleep 0.25
  done
  return 0
}

stop_one() {
  local name="$1" pidf="$PIDS/$1.pid" pid="" pg="" kids="" kid_pids="" lead_t="" p="" t="" verdict=""
  if [ ! -e "$pidf" ]; then
    echo "[stop] $name: not running (no pid file)"
    return 0
  fi
  pid="$(read_pid "$pidf")"
  if ! is_ours "$name" "$pid"; then
    if [ -n "$pid" ] && [ -n "$(proc_state "$pid")" ]; then
      echo "[stop] $name: pid $pid is not this tree's $name (pid reused?): not signalled; stale pid file removed"
    else
      echo "[stop] $name: process ${pid:-?} is gone; stale pid file removed"
    fi
    rm -f "$pidf"
    return 0
  fi
  kill -TERM "$pid" 2>/dev/null
  if wait_gone "$name" "$pid" "$TIMEOUT_S"; then
    rm -f "$pidf"
    echo "[stop] $name: stopped (pid $pid, SIGTERM)"
    return 0
  fi
  lead_t="$(proc_starttime "$pid")"
  kids="$(descendants "$pid")"  # "<pid> <start time>" per line, collected while the process still runs
  while read -r p t; do [ -n "$p" ] && kid_pids+="$p "; done <<<"$kids"
  pg="$(pgid_of "$pid" || true)"
  echo "[stop] $name: pid $pid still running after ${TIMEOUT_S} s: SIGKILL${kid_pids:+ (descendants: $kid_pids)}"
  # every SIGKILL target is re-verified right before the signal (C12 D8: no stale pid is signalled)
  if [ "$pg" = "$pid" ] && group_still_ours "$pid" "$lead_t" "$kids"; then
    kill -KILL -- "-$pid" 2>/dev/null
  elif [ -n "$lead_t" ] && [ "$(proc_starttime "$pid")" = "$lead_t" ]; then
    kill -KILL "$pid" 2>/dev/null
  fi
  while read -r p t; do
    [ -n "$p" ] || continue
    verdict="$(kill_verified "$p" "$t")"
    if [ "$verdict" = "replaced" ]; then
      echo "[stop] $name: descendant pid $p now belongs to another process (start time changed): not signalled"
    fi
  done <<<"$kids"
  if wait_gone "$name" "$pid" 10; then
    rm -f "$pidf"
    echo "[stop] $name: stopped (pid $pid, SIGKILL)"
    return 0
  fi
  echo "stop.sh: $name (pid $pid) survived SIGKILL" >&2
  return 1
}

rc=0
for name in "${PROCS[@]}"; do
  stop_one "$name" || rc=1
done
exit "$rc"

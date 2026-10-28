#!/bin/bash
# Read-only GPU compute-process monitor (host side) for the F2 (b) run: every second, the compute processes NVML reports
# (nvidia-smi --query-compute-apps) and whether any of them belongs to the F2 scratch trees (cmdline contains /f2/).
# Stops when $1 (a stop file) exists. Output: one line per sample.
STOP=$1
n=0; ours=0
while [ ! -e "$STOP" ]; do
  ts=$(date -u +%FT%TZ)
  apps=$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>&1)
  line="$ts"
  while IFS=, read -r pid mem; do
    pid=$(echo "$pid" | tr -d ' '); [ -z "$pid" ] && continue
    cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null | cut -c1-60)
    case "$cmd" in *"/f2/"*) tag=F2; ours=$((ours+1));; *) tag=other;; esac
    line="$line pid=$pid($tag,${mem// /})"
  done <<< "$apps"
  echo "$line"; n=$((n+1)); sleep 1
done
echo "# samples=$n F2_process_entries_on_a_GPU=$ours"

#!/bin/bash
# Inside the T4 sandbox: /proc/<pid>/environ + cmdline of every process of this pid namespace (0600 file, scratch only).
tag=${1:-snapshot}; out=$WORK/proc_capture/t4_${tag}.txt
umask 077
for p in /proc/[0-9]*; do
  [ -r $p/environ ] || continue
  echo "### pid=${p#/proc/} cmd=$(tr '\0' ' ' < $p/cmdline | cut -c1-160)"; tr '\0' '\n' < $p/environ; echo
done > $out 2>/dev/null
echo "t4 $tag processes=$(grep -c '^### pid=' $out)"

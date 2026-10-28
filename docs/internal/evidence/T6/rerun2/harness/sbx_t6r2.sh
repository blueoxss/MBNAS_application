#!/bin/bash
# T6 rerun2 sandbox launcher (public / registration): bwrap with only the extracted ZIP folder (bound at a new path
# with spaces: /srv/mbnas <label> r2), the read-only wheelhouse (/opt/wheelhouse), the Python 3.10 base interpreter
# (/opt/py310), the harness (/opt/harness) and the scanner file (/opt/scanner) visible. /home, the project tree,
# etri_nas and /data are not mounted; own pid/ipc/uts namespaces; fresh /tmp. SBX_NET=none -> own network namespace
# (loopback only, used for the install); otherwise the host network is shared so the gateway reaches the owner core
# on 127.0.0.1. Usage: sbx_t6r2.sh <label> <host folder> <cmd...>
set -u
L=$1; HOSTDIR=$2; shift 2
WH=/home/mgjeong/Desktop/etri_hd/mbnas_application/build/wheelhouse
PYBASE=/home/mgjeong/miniconda3/envs/mbnas310
HARNESS=/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/pfinal2/t6r2/harness
SCANNER=/home/mgjeong/Desktop/etri_hd/mbnas_application/scripts/dev/canary_scan.py
NET=(); [ "${SBX_NET:-host}" = none ] && NET=(--unshare-net)
SB="/srv/mbnas $L r2"
mkdir -p "$HOSTDIR/outputs" "$HOSTDIR/data" "$HOSTDIR/home"
exec env -i PATH=/usr/bin:/bin bwrap --die-with-parent "${NET[@]}" --unshare-pid --unshare-ipc --unshare-uts --hostname "t6r2-$L" \
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \
  --ro-bind /etc /etc --proc /proc --dev /dev --tmpfs /tmp \
  --ro-bind "$PYBASE" /opt/py310 --ro-bind "$WH" /opt/wheelhouse --ro-bind "$HARNESS" /opt/harness \
  --ro-bind "$SCANNER" /opt/scanner/canary_scan.py \
  --bind "$HOSTDIR" "$SB" --bind "$HOSTDIR/outputs" /srv/outputs --bind "$HOSTDIR/data" /srv/data \
  --setenv HOME "$SB/home" --setenv PATH /usr/local/bin:/usr/bin:/bin --setenv LANG C.UTF-8 \
  --setenv CUDA_VISIBLE_DEVICES "" --setenv PYTHONDONTWRITEBYTECODE 1 --setenv PIP_CONFIG_FILE /dev/null \
  --setenv YOLO_OFFLINE 1 --setenv HF_HUB_OFFLINE 1 --setenv TMPDIR /tmp --setenv T6_LABEL "$L" \
  --chdir /tmp "$@"

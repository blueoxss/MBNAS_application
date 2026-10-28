#!/bin/bash
# Short-path sandbox (DD-A75 relocation check): SHORTSRC (a scratch dir) is bound at /r, so the install ROOT is short
# (< 100 characters). Otherwise identical to sbx2.sh (no GPU nodes, loopback only, original tree invisible).
: "${SHORTSRC:?SHORTSRC must be set}"
WH=<ROOT>/build/wheelhouse
PYBASE=<PY310_ENV>
exec env -i PATH=/usr/bin:/bin bwrap --die-with-parent --unshare-net --unshare-pid --unshare-ipc --unshare-uts --hostname t4-short \
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \
  --ro-bind /etc /etc --proc /proc --dev /dev --tmpfs /tmp \
  --ro-bind "$PYBASE" "$PYBASE" --ro-bind "$WH" /opt/wheelhouse --bind "$SHORTSRC" /r \
  --setenv HOME /r/home --setenv PATH /usr/local/bin:/usr/bin:/bin --setenv LANG C.UTF-8 \
  --setenv CUDA_VISIBLE_DEVICES "" --setenv PYTHONDONTWRITEBYTECODE 1 --setenv PIP_CONFIG_FILE /dev/null \
  --setenv YOLO_OFFLINE 1 --setenv HF_HUB_OFFLINE 1 --setenv TMPDIR /tmp --chdir /r "$@"

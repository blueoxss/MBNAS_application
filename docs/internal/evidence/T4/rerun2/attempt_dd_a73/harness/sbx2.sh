#!/bin/bash
# T4 sandbox launcher v2 (DD-A73 attempt): like sbx.sh but the work directory is given by WORK (absolute path, bound
# read-write at the same path). Only WORK, the read-only wheelhouse (/opt/wheelhouse), the harness (/opt/harness) and the
# python3.10 base interpreter are visible; own network namespace (loopback only), own pid namespace, no GPU device nodes.
: "${WORK:?WORK must be set}"
WH=<ROOT>/build/wheelhouse
PYBASE=<PY310_ENV>
HARNESS=<T>/harness
exec env -i PATH=/usr/bin:/bin bwrap --die-with-parent --unshare-net --unshare-pid --unshare-ipc --unshare-uts --hostname t4-sandbox \
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \
  --ro-bind /etc /etc --proc /proc --dev /dev --tmpfs /tmp \
  --ro-bind "$PYBASE" "$PYBASE" \
  --ro-bind "$WH" /opt/wheelhouse \
  --bind "$WORK" "$WORK" \
  --ro-bind "$HARNESS" /opt/harness \
  --setenv HOME "$WORK/home" --setenv PATH /usr/local/bin:/usr/bin:/bin --setenv LANG C.UTF-8 \
  --setenv CUDA_VISIBLE_DEVICES "" --setenv PYTHONDONTWRITEBYTECODE 1 --setenv PIP_CONFIG_FILE /dev/null \
  --setenv YOLO_OFFLINE 1 --setenv HF_HUB_OFFLINE 1 --setenv TMPDIR /tmp \
  --setenv WORK "$WORK" --setenv T4_VARIANT "${T4_VARIANT:-local}" --setenv T4_LABEL_SUFFIX "${T4_LABEL_SUFFIX:-}" \
  --chdir "$WORK" "$@"

#!/bin/bash
# F2 (DD-A75) sandbox launcher: a copy of P2's T4 launcher sbx2.sh (runtime/test_root/t4t5/harness/sbx2.sh, DD-A73 attempt)
# with the same flags. Only WORK (rw, same path), the read-only wheelhouse (/opt/wheelhouse), this harness (/opt/harness)
# and the python3.10 base interpreter are visible; own network namespace (loopback only), own pid namespace; the project
# tree and /home are invisible. GPU_NODES=0 (default, variant a): no /dev/nvidia* (bwrap --dev = minimal /dev).
# GPU_NODES=1 (variant b): the host's /dev/nvidia* character devices are added with --dev-bind (NVML can enumerate the
# GPUs, like the T6 host run); CUDA_VISIBLE_DEVICES="" in both variants.
: "${WORK:?WORK must be set}"
WH=<ROOT>/build/wheelhouse
PYBASE=<PY310_ENV>
HARNESS=<W>/harness
GPU_ARGS=()
if [ "${GPU_NODES:-0}" = "1" ]; then
  for n in /dev/nvidia0 /dev/nvidia1 /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools /dev/nvidia-modeset; do
    [ -e "$n" ] && GPU_ARGS+=(--dev-bind "$n" "$n")
  done
fi
exec env -i PATH=/usr/bin:/bin bwrap --die-with-parent --unshare-net --unshare-pid --unshare-ipc --unshare-uts --hostname f2-sandbox \
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \
  --ro-bind /etc /etc --proc /proc --dev /dev ${GPU_ARGS[@]+"${GPU_ARGS[@]}"} --tmpfs /tmp \
  --ro-bind "$PYBASE" "$PYBASE" \
  --ro-bind "$WH" /opt/wheelhouse \
  --bind "$WORK" "$WORK" \
  --ro-bind "$HARNESS" /opt/harness \
  --setenv HOME "$WORK/home" --setenv PATH /usr/local/bin:/usr/bin:/bin --setenv LANG C.UTF-8 \
  --setenv CUDA_VISIBLE_DEVICES "" --setenv PYTHONDONTWRITEBYTECODE 1 --setenv PIP_CONFIG_FILE /dev/null \
  --setenv YOLO_OFFLINE 1 --setenv HF_HUB_OFFLINE 1 --setenv TMPDIR /tmp \
  --setenv WORK "$WORK" \
  --chdir "$WORK" "$@"

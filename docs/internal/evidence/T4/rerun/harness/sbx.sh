#!/bin/bash
# T4 sandbox launcher: bwrap with only the extracted owner tree (+ work dirs), the read-only wheelhouse mirror and the
# python3.10 base interpreter visible; own network namespace (loopback only), own pid namespace.
T=<T>
WH=<ROOT>/build/wheelhouse
PYBASE=<PY310_ENV>
# SBX_NVML=1: expose ONLY the NVML control/device nodes (no /dev/nvidia-uvm => CUDA compute cannot start) so the core's
# protocol validator can read the device inventory (DEVICE_UNAVAILABLE otherwise); CUDA_VISIBLE_DEVICES stays "".
NV=()
[ "${SBX_NVML:-0}" = 1 ] && NV=(--dev-bind /dev/nvidiactl /dev/nvidiactl --dev-bind /dev/nvidia0 /dev/nvidia0 --dev-bind /dev/nvidia1 /dev/nvidia1)
exec env -i PATH=/usr/bin:/bin bwrap --die-with-parent --unshare-net --unshare-pid --unshare-ipc --unshare-uts --hostname t4-sandbox \
  --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --symlink usr/sbin /sbin \
  --ro-bind /etc /etc --proc /proc --dev /dev "${NV[@]}" --tmpfs /tmp \
  --ro-bind "$PYBASE" "$PYBASE" \
  --ro-bind "$WH" /opt/wheelhouse \
  --bind "$T/${T4_SUB:-t4}" "$T/${T4_SUB:-t4}" \
  --ro-bind "$T/harness" /opt/harness \
  --setenv HOME "$T/${T4_SUB:-t4}/home" --setenv PATH /usr/local/bin:/usr/bin:/bin --setenv LANG C.UTF-8 \
  --setenv CUDA_VISIBLE_DEVICES "" --setenv PYTHONDONTWRITEBYTECODE 1 --setenv PIP_CONFIG_FILE /dev/null \
  --setenv YOLO_OFFLINE 1 --setenv HF_HUB_OFFLINE 1 --setenv TMPDIR /tmp \
  --setenv T4_VARIANT "${T4_VARIANT:-strict}" --setenv T4_LABEL_SUFFIX "${T4_LABEL_SUFFIX:-}" \
  --setenv T4_SUB "${T4_SUB:-t4}" --chdir "$T/${T4_SUB:-t4}" "$@"

"""Read-only NVML probe (nvmlInit + device count + names; no CUDA context, no GPU work)."""
import ctypes, os, sys
print("CUDA_VISIBLE_DEVICES=%r" % os.environ.get("CUDA_VISIBLE_DEVICES"))
print("dev nvidia nodes:", sorted(n for n in os.listdir("/dev") if n.startswith("nvidia")))
try:
    lib = ctypes.CDLL("libnvidia-ml.so.1")
except OSError as exc:
    print("NVML library: not loadable:", exc); sys.exit(0)
rc = lib.nvmlInit_v2()
print("nvmlInit_v2 rc", rc)
if rc == 0:
    n = ctypes.c_uint(0)
    print("nvmlDeviceGetCount_v2 rc", lib.nvmlDeviceGetCount_v2(ctypes.byref(n)), "count", n.value)
    for i in range(n.value):
        h = ctypes.c_void_p()
        lib.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(h))
        buf = ctypes.create_string_buffer(96)
        lib.nvmlDeviceGetName(h, buf, 96)
        print(" device", i, buf.value.decode())
    lib.nvmlShutdown()

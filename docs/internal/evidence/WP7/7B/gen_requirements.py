#!/usr/bin/env python3
"""WP-7B generator: requirements-{public,owner,cpu}.txt from constraints.txt (the wheelhouse closure).

Third-party lines = every constraints.txt pin except the installer tools (pip, setuptools, wheel), which are not runtime
dependencies of any package in the closure (checked separately from installed metadata). The CPU variant swaps
torch/torchvision to the +cpu builds, drops the CUDA runtime wheels (nvidia-*-cu12; nvidia-ml-py and triton stay, see the header text) and
replaces onnxruntime-gpu by onnxruntime of the same version. Project wheels are appended by relative path.
"""
import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1])
VERSION = "0.1.0"
INSTALLER = {"pip", "setuptools", "wheel"}
PUBLIC_WHEELS = ["mbnas_contracts", "mbnas_model_runtime", "mbnas_runner", "mbnas_server"]
OWNER_WHEELS = PUBLIC_WHEELS + ["mbnas_core"]


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


pins = []
for raw in (ROOT / "constraints.txt").read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    name, ver = line.split("==")
    pins.append((name, ver))
third = [(n, v) for n, v in pins if canon(n) not in INSTALLER]


def cpu_pins():
    out = []
    for n, v in third:
        c = canon(n)
        if c.startswith("nvidia-") and c.endswith("-cu12"):  # CUDA runtime wheels: needed by torch+cu124 only
            continue
        if c in ("torch", "torchvision"):
            v = v.replace("+cu124", "+cpu")
        if c == "onnxruntime-gpu":
            n = "onnxruntime"
        out.append((n, v))
    return out


COMMON_USAGE = """\
# Standard install path (DD-A31). Run pip FROM THE DIRECTORY THAT HOLDS THIS FILE AND wheels/ (the unpacked ZIP
# root): pip resolves the ./wheels/... lines at the end against the current working directory, not against this file.
#   python3.10 -m venv .venv
#   .venv/bin/python -m pip install -r {fname}
# Online install = network access to PyPI and {index}.
# Offline install: add `--no-index --find-links <wheelhouse dir>` (the index line above is then ignored);
# {offline}
"""

HEADERS = {
    "requirements-public.txt": ("https://download.pytorch.org/whl/cu124", """\
# MBNAS Research Console {ver} - PUBLIC profile: gateway (mbnas_server) + runner (mbnas_runner) + mbnas_model_runtime +
# mbnas_contracts. The core (search policy, job queue) is not part of this profile: a public install always needs a
# reachable core on the owner host.
{usage}\
# Verified platform: Linux x86_64, CPython 3.10, CUDA 12.4 wheels (torch 2.6.0+cu124; GPU jobs need an NVIDIA
# driver that supports CUDA 12.4). CPU-only hosts: requirements-cpu.txt (not the verified platform). Owner host:
# requirements-owner.txt.
# Pins: every third-party line below equals constraints.txt (the wheelhouse closure: {n} runtime distributions;
# the installer tools pip/setuptools/wheel are pinned in constraints.txt only). The project wheels come last.
"""),
    "requirements-owner.txt": ("https://download.pytorch.org/whl/cu124", """\
# MBNAS Research Console {ver} - OWNER profile (PRIVATE): the public set plus mbnas_core (core API, owner worker,
# search policy). Never hand this file or wheels/mbnas_core-*.whl to a customer.
{usage}\
# Verified platform: Linux x86_64, CPython 3.10, CUDA 12.4 wheels (torch 2.6.0+cu124). The core and the owner worker
# need no GPU; the GPU is used by runners (an all-in-one owner host runs one). CPU-only owner host (CPU torch):
#   .venv/bin/python -m pip install -r requirements-cpu.txt ./wheels/mbnas_core-{ver}-py3-none-any.whl
# Pins: every third-party line below equals constraints.txt (the wheelhouse closure: {n} runtime distributions;
# the installer tools pip/setuptools/wheel are pinned in constraints.txt only). The project wheels come last.
"""),
    "requirements-cpu.txt": ("https://download.pytorch.org/whl/cpu", """\
# MBNAS Research Console {ver} - PUBLIC profile, CPU-ONLY variant (CPU smoke, hosts without an NVIDIA GPU; Docker:
# deploy/public/Dockerfile.cpu). The VERIFIED platform is cu124 (requirements-public.txt). Differences from
# constraints.txt: torch/torchvision are the +cpu builds, the CUDA runtime wheels (nvidia-*-cu12) are not installed
# and onnxruntime replaces onnxruntime-gpu (same version); triton stays pinned (torch-hungarian, an RF-DETR training
# dependency, requires it on Linux and would otherwise pull the newest release). A CPU runner trains, writes
# checkpoints, checks structures and runs OFF generation; evaluation latency needs CUDA, so CPU evaluations stay
# latency_source=pending (DD-A32).
{usage}\
"""),
}


def write(fname, rows, wheels):
    index, head = HEADERS[fname]
    offline = ("that wheelhouse must hold the +cpu builds (build/wheelhouse is the cu124 closure and does not)."
               if fname == "requirements-cpu.txt" else
               "a wheelhouse of the constraints.txt closure (build/wheelhouse in the source tree) covers this file.")
    usage = COMMON_USAGE.format(fname=fname, index=index, offline=offline)
    text = [f"--extra-index-url {index}", "--only-binary=:all:",
            head.format(ver=VERSION, usage=usage, n=len(rows)).rstrip("\n"), "#", "# third-party (pinned)"]
    text += [f"{n}=={v}" for n, v in rows]
    text += ["# project wheels (relative to the current working directory; built by scripts/package.sh)"]
    text += [f"./wheels/{w}-{VERSION}-py3-none-any.whl" for w in wheels]
    (ROOT / fname).write_text("\n".join(text) + "\n")
    print(f"{fname}: {len(rows)} third-party pins + {len(wheels)} wheels")


write("requirements-public.txt", third, PUBLIC_WHEELS)
write("requirements-owner.txt", third, OWNER_WHEELS)
write("requirements-cpu.txt", cpu_pins(), PUBLIC_WHEELS)

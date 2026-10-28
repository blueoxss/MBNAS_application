#!/usr/bin/env python3
"""Download official Ultralytics release checkpoints into the MBNAS weights cache and record provenance.

Only pinned asset names are accepted. Each file is recorded with source URL, bytes, sha256 and download time in
<weights_dir>/weights_manifest.json. YOLO26 digests are cross-checked against the release digests pinned in
ETRI_NAS/drone_bench/drone_bench/adapters/yolo26.py (read-only reference) when that file is present.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys, time
from pathlib import Path

ASSETS = {
    # name: release tag of https://github.com/ultralytics/assets/releases
    "yolo11n.pt": "v8.3.0", "yolo11s.pt": "v8.3.0", "yolo11m.pt": "v8.3.0", "yolo11l.pt": "v8.3.0", "yolo11x.pt": "v8.3.0",
    "yolo26n.pt": "v8.4.0", "yolo26s.pt": "v8.4.0", "yolo26m.pt": "v8.4.0", "yolo26l.pt": "v8.4.0", "yolo26x.pt": "v8.4.0",
    "rtdetr-l.pt": "v8.3.0", "rtdetr-x.pt": "v8.3.0",
}
BASE = "https://github.com/ultralytics/assets/releases/download/{tag}/{name}"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def etri_yolo26_pins(etri_root: Path) -> dict:
    src = etri_root / "drone_bench/drone_bench/adapters/yolo26.py"
    pins = {}
    if src.is_file():
        for m in re.finditer(r'"([nslmx])":\s*\("(yolo26[nslmx]\.pt)",\s*(\d+),\s*"([0-9a-f]{64})"\)', src.read_text()):
            pins[m.group(2)] = {"bytes": int(m.group(3)), "sha256": m.group(4), "source": str(src)}
    return pins


def download(url: str, dst: Path) -> None:
    import urllib.request
    tmp = dst.with_suffix(dst.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "mbnas-server/0.1"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights-dir", default=os.environ.get("MBNAS_WEIGHTS_DIR", "/root/detection/MBNAS_data/weights"))
    ap.add_argument("--etri-root", default="/root/detection/ETRI_NAS")
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    wd = Path(a.weights_dir); wd.mkdir(parents=True, exist_ok=True)
    man_path = wd / "weights_manifest.json"
    manifest = json.loads(man_path.read_text()) if man_path.exists() else {}
    pins = etri_yolo26_pins(Path(a.etri_root))
    names = a.only or list(ASSETS)
    rc = 0
    for name in names:
        tag = ASSETS[name]; url = BASE.format(tag=tag, name=name); dst = wd / name
        rec = manifest.get(name, {})
        if dst.exists() and rec.get("sha256") == sha256_file(dst):
            print(f"[skip] {name} present sha256={rec['sha256'][:16]}"); continue
        t0 = time.time()
        try:
            download(url, dst)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: {e}", file=sys.stderr); rc = 2
            manifest[name] = {"source_url": url, "status": "DOWNLOAD_FAILED", "error": str(e)}
            continue
        sha = sha256_file(dst); nbytes = dst.stat().st_size
        rec = {"source_url": url, "release_tag": tag, "bytes": nbytes, "sha256": sha,
               "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": round(time.time() - t0, 1),
               "status": "DOWNLOADED", "digest_verification": "observed_at_download_only"}
        if name in pins:
            ok = pins[name]["sha256"] == sha and pins[name]["bytes"] == nbytes
            rec["digest_verification"] = ("matches_ETRI_NAS_pinned_release_digest" if ok else "MISMATCH_vs_ETRI_NAS_pin")
            rec["pin_reference"] = pins[name]
            if not ok:
                rc = 3
        manifest[name] = rec
        print(f"[ok] {name} {nbytes} bytes sha256={sha[:16]} {rec['digest_verification']}")
        man_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    man_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    print("manifest:", man_path)
    return rc


if __name__ == "__main__":
    sys.exit(main())

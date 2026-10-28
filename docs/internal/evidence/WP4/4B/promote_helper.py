#!/usr/bin/env python3
"""WP-4B promotion helper (evidence copy of the scratch tool used for the promotion; not shipped): migrate 1.4.3 -> 1.5.0, pin the sha, write the live contract,
copy snapshot + script into docs/public/contracts/history and move HISTORY drafts[0] into versions (both indexes)."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]  # repository root (docs/internal/evidence/WP4/4B/<this>)
SCRIPT = ROOT / "scripts/dev/migrate_openapi_1.4.3_to_1.5.0.py"
H143 = ROOT / "docs/public/contracts/history/openapi-1.4.3.json"
LIVE = ROOT / "mbnas_contracts/src/mbnas_contracts/openapi/openapi.json"
PKG_HISTORY = ROOT / "mbnas_contracts/src/mbnas_contracts/openapi/HISTORY.json"
DOC_DIR = ROOT / "docs/public/contracts/history"
DOC_HISTORY = DOC_DIR / "HISTORY.json"
PY = str(ROOT / "runtime/venv/bin/python")

ENTRY_NOTE = ("Written for this repository by WP-4A (draft, sha256 70edb8ef...) and completed at promotion by WP-4B "
              "(DD-A42 Block user-model keys, DD-A44 upload 422 / gateway path hygiene, DD-A46 final-retrain "
              "STALE_VALIDATION); standard library only, no host path, no commander transcript. It asserts the sha256 "
              "above when replayed on openapi-1.4.3.json. Unlike the note above says for the older links, nothing in "
              "it is verbatim from the original commander.")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "in.json"
        out = Path(tmp) / "out.json"
        shutil.copy(H143, work)
        text = SCRIPT.read_text()
        text = re.sub(r"EXPECTED_1_5_0_SHA256 = '[^']*'", "EXPECTED_1_5_0_SHA256 = '__FILL__'", text)
        SCRIPT.write_text(text)
        subprocess.run([PY, "-I", "-B", str(SCRIPT), str(work), str(out)], check=True)
        digest = sha(out.read_bytes())
    text = re.sub(r"EXPECTED_1_5_0_SHA256 = '[^']*'", f"EXPECTED_1_5_0_SHA256 = '{digest}'", SCRIPT.read_text())
    SCRIPT.write_text(text)
    subprocess.run([PY, "-I", "-B", str(SCRIPT), str(H143), str(LIVE), "--promote"], check=True)
    assert sha(LIVE.read_bytes()) == digest
    shutil.copyfile(LIVE, DOC_DIR / "openapi-1.5.0.json")
    shutil.copyfile(SCRIPT, DOC_DIR / "migrate_1.4.3_to_1.5.0.py")
    entry = {"version": "1.5.0", "file": "openapi-1.5.0.json", "sha256": digest,
             "author": "WP-4A draft + WP-4B promotion 2026-10-27 (design: claude-fable-5-1; implementation: claude-opus-5-5)",
             "created": "2026-10-27", "model": "claude-opus-5-5 (under claude-fable-5-1 design)",
             "migration": "migrate_1.4.3_to_1.5.0.py",
             "predecessor": {"version": "1.4.3", "file": "openapi-1.4.3.json",
                             "sha256": "f3fe8ea672ee106d41c5c92723f7002136c7181892933aff7653a31c83f8c738"},
             "script_note": ENTRY_NOTE}
    doc = json.loads(PKG_HISTORY.read_text(encoding="utf-8"))  # the package index carries the DD-A41 note correction
    doc.pop("drafts", None)
    doc["versions"] = [v for v in doc["versions"] if v["version"] != "1.5.0"] + [entry]
    data = json.dumps(doc, indent=1, ensure_ascii=False)
    for path in (PKG_HISTORY, DOC_HISTORY):  # one index, two copies (package data + history directory)
        path.write_text(data, encoding="utf-8")
    print("promoted 1.5.0 sha256", digest)


if __name__ == "__main__":
    main()

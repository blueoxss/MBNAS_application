#!/usr/bin/env python3
"""Copy evidence files into docs/internal/evidence/T4|T5 with host paths replaced by placeholders (text files only;
<T> = <ROOT>/runtime/test_root/t4t5). Secrets are never in these files (verified by canary_scan afterwards)."""
import shutil, sys
from pathlib import Path
REPL = [("<W>", "<W>"),
        ("<SP>", "<SP>"),
        ("<T>", "<T>"),
        ("<ROOT>", "<ROOT>"),
        ("<PY310_ENV>", "<PY310_ENV>"),
        ("<CONDA>", "<CONDA>"),
        ("<HOME>", "<HOME>"), ("<user>", "<user>")]
def main():
    dst = Path(sys.argv[1]); dst.mkdir(parents=True, exist_ok=True)
    for spec in sys.argv[2:]:
        src, _, name = spec.partition("=")
        s = Path(src); d = dst / (name or s.name)
        d.parent.mkdir(parents=True, exist_ok=True)
        data = s.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            shutil.copyfile(s, d); print("binary", d); continue
        for a, b in REPL:
            text = text.replace(a, b)
        d.write_text(text, encoding="utf-8")
main()

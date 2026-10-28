"""Markdown table shape check: every row of a pipe table has the header's cell count (escaped \\| and pipes inside
`code` spans are not separators). Usage: md_tables.py <root> <file>..."""
import re
import sys
from pathlib import Path

def cells(line: str) -> int:
    s = line.strip()
    out, cur, code, i = [], "", False, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur += "|"; i += 2; continue
        if ch == "`":
            code = not code
        if ch == "|" and not code:
            out.append(cur); cur = ""
        else:
            cur += ch
        i += 1
    out.append(cur)
    if out and out[0].strip() == "": out = out[1:]
    if out and out[-1].strip() == "": out = out[:-1]
    return len(out)

root = Path(sys.argv[1]); files = sys.argv[2:]
tables = bad = 0
for rel in files:
    lines = (root / rel).read_text().splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            tables += 1; n = cells(lines[i]); j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                if cells(lines[j]) != n:
                    bad += 1; print(f"MISMATCH {rel}:{j + 1}: {cells(lines[j])} cells, header {n}")
                j += 1
            i = j
        else:
            i += 1
print(f"files: {len(files)} tables: {tables} row mismatches: {bad}")
sys.exit(1 if bad else 0)

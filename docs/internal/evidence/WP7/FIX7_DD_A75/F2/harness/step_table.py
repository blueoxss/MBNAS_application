"""Side-by-side step table of integration_smoke.txt files (verify.sh's own parse rule: a line starting with '{' that
contains '"step"'), plus the '# fleet mode' header of each."""
import json, sys
cols = []
for spec in sys.argv[1:]:
    label, _, path = spec.partition("=")
    steps, mode = [], "(no fleet-mode line: pre-DD-A75 script)"
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if s.startswith("# fleet mode:"):
            mode = s[len("# fleet mode: "):].split(" - ", 1)[0]
        if s.startswith("{") and '"step"' in s:
            try:
                d = json.loads(s)
            except ValueError:
                continue
            steps.append((d.get("step"), "PASS" if d.get("ok") else "FAIL"))
    cols.append((label, mode, steps))
names = []  # run order of the longest column first, then any other step names
for _, _, steps in sorted(cols, key=lambda c: -len(c[2])):
    for n, _ in steps:
        if n not in names:
            names.append(n)
w = max(len(n) for n in names) + 5
print("step".ljust(w) + "".join(c[0].ljust(16) for c in cols))
print("(fleet mode)".ljust(w) + "".join(c[1][:15].ljust(16) for c in cols))
for i, n in enumerate(names, 1):
    row = f"{i:2d} {n}".ljust(w)
    for _, _, steps in cols:
        r = dict(steps).get(n, "-- (absent)")
        row += r.ljust(16)
    print(row)
print("counts".ljust(w) + "".join(f"{sum(1 for _, r in s if r == 'PASS')}/{len(s)}".ljust(16) for _, _, s in cols))

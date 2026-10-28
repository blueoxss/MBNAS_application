#!/bin/bash
# Negative control (inside the sandbox): apply one mutation to the dev tree's smoke, run the new unit tests, restore.
set -u
T=$WORK/zip/mbnas_application
S=$T/scripts/dev/wp3_three_process_smoke.py
cp -p "$S" /tmp/smoke.keep
run() {
  local name=$1 old=$2 new=$3
  /usr/bin/python3 -I - "$S" "$old" "$new" <<'PY'
import sys
p, old, new = sys.argv[1:4]
t = open(p).read()
assert t.count(old) == 1, ("anchor", t.count(old), old)
open(p, "w").write(t.replace(old, new))
PY
  (cd "$T" && PYTHONDONTWRITEBYTECODE=1 runtime/venv/bin/python -B -m pytest -p no:cacheprovider -q tests/owner/qa/test_wp3_smoke_fleet_mode.py 2>&1 | tail -1 | sed "s/^/[$name] /")
  cp -p /tmp/smoke.keep "$S"
}
run M1_contains_instead_of_exact 'tuple((e.get("code"), e.get("field")) for e in errors) == CPU_EVALUATION_REFUSAL' 'CPU_EVALUATION_REFUSAL[0] in tuple((e.get("code"), e.get("field")) for e in errors)'
run M2_ignore_device_id '        not entry.get("device_id") and str(entry.get("id") or "").lower() == "cpu"))' '        str(entry.get("id") or "").lower() == "cpu"))'
run M3_empty_inventory_is_cpu '    if cpu:
        device = cpu[0].get("id")' '    if cpu or not inventory:
        device = (cpu or [{"id": "cpu"}])[0].get("id")'
run M4_drop_gateway_hash_check '        "gateway_split_hashes_set": gm.get("all_split_hashes_set") is True and gm.get("images") == expected,' '        "gateway_split_hashes_set": True,'
sha256sum "$S"

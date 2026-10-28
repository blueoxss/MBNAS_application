#!/usr/bin/env python3
"""Positive control of the DD-A75 path detector (t45r3.H._scan_doc/_raw_counts): run it on PRE-FIX responses (DD-A73
attempt, owner ZIP 53e6a1f0, where raw_metrics.resolved_recipe still carried runner absolute paths) with that
install's directory-name fragments; the detector must report 'other' hits and NOT REPLACED recipe values."""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("t45r3", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
class A: plant_fragment = ["install_root_token=p2dd73", "runner_wr_dirs=runtime/runner/runs"]
h = m.H.__new__(m.H); h.a = A()
out = {}
for f in sys.argv[2:]:
    blob = open(f, "rb").read()
    acc = h._new_acc(); h._raw_counts(blob, acc); h._scan_doc(json.loads(blob), "", acc)
    acc["recipe_values"] = {k: sorted(v) for k, v in acc["recipe_values"].items()}
    out[f.rsplit("/", 1)[-1]] = {"fragments": acc["fragments"], "abs_path_regex": acc["abs_path_regex"],
                                 "recipe_values": acc["recipe_values"], "raw_body_grep": acc["raw_body_grep"]}
print(json.dumps(out, indent=1))
ok = all(v["fragments"].get("runner_wr_dirs", {}).get("other", 0) > 0 and v["abs_path_regex"]["other"] > 0 for v in out.values())
print("detector positive control:", "PASS (pre-fix leak detected)" if ok else "FAIL")

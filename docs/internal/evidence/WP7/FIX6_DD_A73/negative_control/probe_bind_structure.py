"""DD-A73 negative control / branch probe (evidence script, not a test). Fresh interpreter: run with python -I -B.

argv: <candidate yaml> <mode>; mode fix = the patched code as is; noop = load_etri_mixed replaced by a no-op (the
pre-DD-A73 behaviour: bind_structure builds without registering the ETRI mixed blocks); raise = load_etri_mixed raises
(mixed backend unavailable). Prints one RESULT json line."""
import json, sys
yaml_path, mode = sys.argv[1], sys.argv[2]
text = open(yaml_path, encoding="utf-8").read()
from mbnas_model_runtime.genotype import Genotype
from mbnas_model_runtime.operators import yaml_uses_mixed_modules
import mbnas_core.search.mixed_pool.bridge as bridge
if mode == "noop":
    bridge.load_etri_mixed = lambda *a, **k: None          # the pre-DD-A73 behaviour: nothing registered
elif mode == "raise":
    def boom(*a, **k):
        raise bridge.EtriUnavailable("simulated: mixed-block backend missing")
    bridge.load_etri_mixed = boom
from ultralytics.nn import tasks
before = isinstance(getattr(tasks, "NASGlobalTransformer", None), type)
from mbnas_core.training_runs.selection import TrainingSelectionError, bind_structure
genotype = Genotype.from_yaml_text(text, family="NAS:hybrid_backbone", template_id="run-x:RAND:s7:g00:c000")
source = {"kind": "generated", "run_id": "run-x", "candidate_id": "run-x:RAND:s7:g00:c000", "name": "RAN-G00-C000",
          "family": "NAS:hybrid_backbone", "architecture_yaml": genotype.yaml_text(), "architecture_hash": None,
          "yaml_hash": genotype.yaml_hash(), "blocks": [], "genotype": genotype.to_json(), "dataset_class_count": 1,
          "arch": {"model_id": "RAN-G00-C000", "family": "YOLO11", "backend": "ultralytics", "variant": "detect",
                   "config": genotype.yaml,
                   "register_hook": "mbnas_model_runtime.operators:ensure_registered" if yaml_uses_mixed_modules(genotype.yaml) else None}}
resolved = {"console": {"paths": {}, "extra": {"training": {"arch": source["arch"]}}}, "training": {"requested_input_size": [64, 64]}}
out = {"mode": mode, "registered_before": before}
try:
    bind_structure(source, resolved, "scratch")
    st = source.get("structure") or {}
    out.update(raised=None, status=st.get("status"), codes=[d.get("code") for d in st.get("diagnostics") or []],
               causes=[d.get("cause") for d in st.get("diagnostics") or []][:3], n_blocks=len(st.get("blocks") or []),
               architecture_hash=source.get("architecture_hash"), resolved_arch_hash=resolved["console"]["extra"]["training"].get("architecture_hash"))
except TrainingSelectionError as exc:
    out.update(raised=exc.code, errors=[{k: e.get(k) for k in ("code", "path", "cause")} for e in (exc.errors or [])][:3])
out["registered_after"] = isinstance(getattr(tasks, "NASGlobalTransformer", None), type)
print("RESULT " + json.dumps(out))

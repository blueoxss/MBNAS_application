"""WP-4B evidence: does adding resolution.paths to RESOLUTION_HASH_EXCLUDED (and dropping etri_root / realpath from the
validator's resolution) change protocol_hash? Computed for both presets (requested and validated) on a synthetic
dataset (tests/_shared/datasets.py) with the synthetic all-AVAILABLE example registry and the A100 stub device of the
owner tests. 1.4.3 definition = canonical sha256 without (console.label, resolution.validated_at, console.retention).

Run: runtime/venv/bin/python -B <this> <out.json>
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]  # repository root (docs/internal/evidence/WP4/4B/<this>)
sys.path.append(str(ROOT / "tests" / "_shared"))

from mbnas_contracts.protocol_hash import RESOLUTION_HASH_EXCLUDED, protocol_hash  # noqa: E402
from mbnas_contracts.util.canon import canonical_sha256  # noqa: E402

OLD_EXCLUDED = ("console.label", "resolution.validated_at", "console.retention")


def hash_with(doc, excluded):
    d = copy.deepcopy(doc)
    for dotted in excluded:
        *parents, leaf = dotted.split(".")
        node = d
        for p in parents:
            node = node.get(p) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(leaf, None)
    return canonical_sha256(d)


def main(out: str) -> None:
    from datasets import build_dataset  # tests/_shared/datasets.py (synthetic)

    from mbnas_core.config import CoreSettings
    from mbnas_core.protocol.presets import presets
    from mbnas_core.protocol.validator import ValidationContext, validate
    from mbnas_model_runtime.datasets.manifest import DatasetService

    assert RESOLUTION_HASH_EXCLUDED == OLD_EXCLUDED + ("resolution.paths",), RESOLUTION_HASH_EXCLUDED
    reg = json.loads((ROOT / "config/examples/model_registry.example.json").read_text())
    entries = {}
    for e in reg["registry"]:
        e = copy.deepcopy(e)
        e["status"], e["reason"] = "AVAILABLE", "synthetic"
        entries[e["id"]] = e
    a100 = {"id": "host-gpu-0", "uuid": "GPU-00000000-0000-0000-0000-000000000000", "name": "NVIDIA A100 80GB PCIe",
            "aliases": ["GPU-00000000-0000-0000-0000-000000000000", "NVIDIA A100 80GB PCIe", "cuda:0"],
            "precisions": ["FP32", "FP16"]}
    result: dict = {"note": __doc__, "requested": {}, "validated": {}}
    with tempfile.TemporaryDirectory() as tmp:
        data, runs = Path(tmp) / "data", Path(tmp) / "runs"
        runs.mkdir()
        ds = build_dataset(data, "ds")
        settings = CoreSettings(db_path=str(runs / "db.sqlite3"), output_root=str(runs), output_roots=[str(runs)],
                                dataset_roots=[str(data)], weights_dir=str(runs / "weights"), log_dir=str(runs / "logs"),
                                min_free_disk_gb=0.0)
        orin = {"id": "orin-nx", "uuid": "GPU-11111111-1111-1111-1111-111111111111", "name": "Orin NX",
                "aliases": ["GPU-11111111-1111-1111-1111-111111111111", "Orin NX"], "precisions": ["FP32", "FP16"]}
        ctx = ValidationContext(settings=settings, registry_lookup=entries.get, devices=[a100, orin],
                                dataset_service=DatasetService.from_settings(settings), now=lambda: "2026-10-27T00:00:00Z")
        paths = {"dataset_yaml": ds["dataset_yaml"], "train_root": ds["train_root"],
                 "search_val_root": ds["search_val_root"], "final_eval_root": ds["final_eval_root"],
                 "output_root": str(runs / "out")}
        for preset in presets():
            pid, requested = preset["id"], preset["protocol"]
            result["requested"][pid] = {"old_definition": hash_with(requested, OLD_EXCLUDED),
                                        "new_definition": protocol_hash(requested)}
            proto = copy.deepcopy(requested)
            proto["console"]["paths"] = dict(paths)
            if str(proto.get("status", "")).startswith("DESIGN_EXAMPLE"):
                proto["status"] = "PRESET_" + pid
            r = validate(proto, ctx)
            entry = {"valid": r.valid, "errors": [e["code"] for e in r.errors][:10]}
            if r.valid:
                new_doc = r.resolved
                # what a 1.4.3 validator recorded for the same request: etri_root null + realpath per checked path
                old_doc = copy.deepcopy(new_doc)
                ssr = old_doc["console"]["search"].get("search_space_resolved")
                if isinstance(ssr, dict):
                    ssr["etri_root"] = None
                old_doc["resolution"]["hash_excluded"] = list(OLD_EXCLUDED)
                for key, rec in old_doc["resolution"]["paths"].items():
                    rec["realpath"] = str(Path(paths[key]).resolve())
                entry.update({
                    "hash_1_5_0": r.protocol_hash,
                    "hash_1_4_3_same_request": hash_with(old_doc, OLD_EXCLUDED),
                    "stored_1_4_3_doc_rehashed_by_1_5_0_definition": protocol_hash(old_doc),
                    "fixed_point_1_5_0": validate(new_doc, ctx).protocol_hash == r.protocol_hash,
                    "hash_independent_of_realpath": protocol_hash({**new_doc, "resolution": {
                        **new_doc["resolution"], "paths": {k: {"requested": "/elsewhere"} for k in
                                                           new_doc["resolution"]["paths"]}}}) == r.protocol_hash,
                })
                entry["changed_vs_1_4_3"] = entry["hash_1_5_0"] != entry["hash_1_4_3_same_request"]
            result["validated"][pid] = entry
    Path(out).write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "note"}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])

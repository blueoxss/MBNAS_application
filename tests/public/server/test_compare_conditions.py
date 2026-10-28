"""WP-S5 (DD-33): ``conditions`` of every compared run is read from the run artifacts or null."""
from __future__ import annotations

import json

import archive_synth as synth
from mbnas_server.api.contract_check import contract_errors

RUN = "synthetic-day"

KEYS = {"dataset", "split", "stage", "epochs_total", "protocol_hash", "device", "precision", "checkpoint"}


def test_conditions_filled_from_artifacts(client, synth_root):
    run = synth_root / RUN
    synth.write_json(run / "protocol/protocol_v2.json", {"eval": {"split": "val"}, "latency": {"device": "cuda:0", "fp16": True}})
    fs = json.loads((run / "final_summary.json").read_text())
    fs["roles"]["BEST_ACCURACY"]["candidate_id"] = "g001_c0005"
    (run / "final_summary.json").write_text(json.dumps(fs))
    body = client.get(f"/api/v1/archive/compare?runs={RUN}").json()
    assert contract_errors("ArchiveCompare", body) == []
    c = body["runs"][0]["conditions"]
    assert set(c) == KEYS
    assert c == {"dataset": "nps_drones", "split": "val", "stage": "search T@5e; final@18e", "epochs_total": 18,
                 "protocol_hash": "train0001", "device": "cuda:0", "precision": "fp16", "checkpoint": "/nonexistent/final/best.pt"}


def test_conditions_null_when_not_recorded(client, synth_root):
    run = synth_root / "synthetic-bare"
    synth.make_run(synth_root, "synthetic-bare", rows=synth.DESIGN[:5], finished=False)
    body = client.get("/api/v1/archive/compare?runs=synthetic-bare").json()
    assert contract_errors("ArchiveCompare", body) == []
    c = body["runs"][0]["conditions"]
    assert set(c) == KEYS
    assert c["split"] is None and c["device"] is None and c["precision"] is None and c["checkpoint"] is None
    assert c["epochs_total"] is None and c["dataset"] is None
    assert c["stage"] == "search T@5e" and c["protocol_hash"] == "train0001"  # row-recorded hash (existing summary fallback)
    assert not (run / "protocol/protocol_v2.json").exists()

"""DD-26: WEIGHTS_UNVERIFIED for official weights without ``weights_manifest.json`` names the command that creates it."""
from __future__ import annotations

import json

import pytest

from mbnas_model_runtime.benchmark.subjects import SubjectError, verify_official_weights


def test_missing_manifest_message_names_download_weights_script(tmp_path):
    w = tmp_path / "yolo11n.pt"
    w.write_bytes(b"x")
    with pytest.raises(SubjectError) as err:
        verify_official_weights(w, tmp_path)
    assert err.value.code == "WEIGHTS_UNVERIFIED"
    msg = str(err.value)
    assert "weights_manifest.json" in msg and "does not exist" in msg
    assert "scripts/download_weights.py" in msg and f"--weights-dir {tmp_path}" in msg


def test_manifest_present_but_entry_missing_message_names_the_record_command(tmp_path):
    w = tmp_path / "yolo11n.pt"
    w.write_bytes(b"x")
    (tmp_path / "weights_manifest.json").write_text(json.dumps({"other.pt": {"sha256": "0" * 64, "bytes": 1}}))
    with pytest.raises(SubjectError) as err:
        verify_official_weights(w, tmp_path)
    assert err.value.code == "WEIGHTS_UNVERIFIED"
    assert "scripts/download_weights.py" in str(err.value) and "--only yolo11n.pt" in str(err.value)
    assert "does not exist" not in str(err.value)

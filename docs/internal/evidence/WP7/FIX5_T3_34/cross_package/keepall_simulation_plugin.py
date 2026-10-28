"""Scratch simulation of the PROPOSED test-side change (not applied to the repository): the transfer / artifact test
protocols pin console.retention.full_state = keep_all (their subject is not retention; DD-A66 now applies the PILOT_SMOKE
default last_rung_end_only to remote runs)."""
import sys

sys.path.append("/home/mgjeong/Desktop/etri_hd/mbnas_application/tests/_shared")
import harness  # noqa: E402

_orig = harness.small_protocol


def small_protocol(*a, **k):
    proto = _orig(*a, **k)
    proto["console"]["retention"] = {"full_state": "keep_all"}
    return proto


harness.small_protocol = small_protocol
print("PROPOSAL SIMULATION: small_protocol pins keep_all", flush=True)

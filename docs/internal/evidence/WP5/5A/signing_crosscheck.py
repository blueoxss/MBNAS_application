"""Self-check: mbnas_server.artifacts.signing (ported) == mbnas_core.artifacts.registry (original copy) on many inputs."""
import itertools, random
from mbnas_core.artifacts import registry as core
from mbnas_server.artifacts import signing as gw

rng = random.Random(20261027)
keys = [b"k", b"unit-test-signing-key-0123456789", bytes(rng.randrange(256) for _ in range(48))]
ids = ["art-1", "art-" + "f" * 20, "aé", "x.y", ""]
exps = [0, 1, 2000, 1700000000, 2**40]
n = mism = 0
for key, aid, exp in itertools.product(keys, ids, exps):
    s1, s2 = core.sign(key, aid, exp), gw.sign(key, aid, exp)
    n += 1; mism += s1 != s2
    for q_exp, q_sig, now in ((str(exp), s1, exp - 1.0), (str(exp), s1, exp + 1.0), (str(exp + 1), s1, 0.0),
                              (str(exp), "0" * 64, 0.0), ("٣", s1, 0.0), (None, s1, 0.0), (str(exp), "é", 0.0)):
        n += 1; mism += core.verify_signature(key, aid, q_exp, q_sig, now=now) != gw.verify_signature(key, aid, q_exp, q_sig, now=now)
row = {"id": "art-x", "run_id": "run-u1", "kind": "tp", "state": "READY", "sha256": "a" * 64, "mime_type": "image/png",
       "sample_id": "s1", "split_hash": None, "evaluation_id": "ev1",
       "meta": {"matching": {"sample_id": "s1"}, "origin": "evaluation", "secret_path": "/x", "epoch": 3}}
for key, state in itertools.product((None, b"k"), ("READY", "NOT_READY")):
    r = {**row, "state": state}
    n += 1; mism += core.artifact_view(r, base_url="http://h:1/", key=key, ttl_s=60, now=1000.0) != \
        gw.artifact_view(r, base_url="http://h:1/", key=key, ttl_s=60, now=1000.0)
print(f"cross-check comparisons={n} mismatches={mism}")
raise SystemExit(1 if mism else 0)

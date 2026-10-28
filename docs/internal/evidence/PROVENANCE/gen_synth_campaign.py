"""Build a synthetic 12-arm campaign root (the layout of the real visdrone_12search campaign) with the helpers of
tests/public/server/test_campaign_adapter.py, for running the read-only smoke without touching /data."""
import importlib.util, sys, copy
from pathlib import Path
T = Path("/home/mgjeong/Desktop/etri_hd/mbnas_application/tests/public/server")
out = Path(sys.argv[1])
def load(alias, path):
    spec = importlib.util.spec_from_file_location(alias, path); mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod; spec.loader.exec_module(mod); return mod
load("archive_synth", T / "synth.py")
sys.path.append(str(T.parents[1] / "_shared"))
m = load("cadapter", T / "test_campaign_adapter.py")
croot = m.make_campaign(out / "visdrone_12search", "vd12_20261021_2240")  # S2/ga/42 done, S2/random/42 blocked, S1/ga/42 partial
n = 3
for seed in (42, 2024):
    for space in ("S1", "S2", "S3"):
        for method in ("ga", "random"):
            arm = croot / "arms" / space / method / f"seed_{seed}"
            if arm.exists():
                continue
            res = copy.deepcopy(m.COMPLETED); res["arm"] = f"{space}/{method}/seed_{seed}"
            m.make_arm(croot, space, method, seed, result=res); n += 1
print(croot, "arms", n)

"""T8 §2 add-on: the public gateway environ when start.sh is invoked from the external cwd "/" (re-invocation)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import final_driver as d          # noqa: E402
import t8_scan_final as t         # noqa: E402

t.EV8.mkdir(parents=True, exist_ok=True)
norm = d.HERE / "capture_normalized/reinvoke"
n = t.normalize(d.CAP / "reinvoke/proc", norm / "proc", d.PUB)
print("normalized replacements:", n)
t.scan("reinvoke_cwd_root_raw_proc", [d.CAP / "reinvoke/proc"], d.HERE / "secrets_all.env", d.HERE / "needles_owner.txt")
t.scan("reinvoke_cwd_root_normalized_proc", [norm / "proc"], d.HERE / "secrets_all.env", d.HERE / "needles_owner.txt")

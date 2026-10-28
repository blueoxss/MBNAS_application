"""WP-7V T6 for the registration ZIP (same runtime as public + docs/registration): doctor -> start -> API -> verify ->
stop, all from an external working directory; against the owner core started by t6_t8_driver.py (port 18700)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import t6_t8_driver as d  # noqa: E402

REG = d.T6 / "mbnas registration test/mbnas_application"
d.GW = "http://127.0.0.1:18712"


def main() -> int:
    d.EV6.mkdir(parents=True, exist_ok=True)
    (d.CAP / "responses_registration").mkdir(parents=True, exist_ok=True)
    cwd, rb = d.HERE, REG / "scripts"
    out: dict = {"registration_root": str(REG), "external_cwd": str(cwd)}
    out["doctor_pre"] = d.run("reg_doctor_strict_prestart", ["bash", str(rb / "doctor.sh"), "--profile", "public",
                                                             "--strict"], cwd)
    out["start"] = d.run("reg_start", ["bash", str(rb / "start.sh"), "--profile", "public"], cwd)
    out["doctor"] = d.run("reg_doctor_strict", ["bash", str(rb / "doctor.sh"), "--profile", "public", "--strict",
                                                "--json", str(d.EV6 / "doctor_registration.json")], cwd)
    d.TOKEN = d.envval(REG / "config/public/gateway.env", "MBNAS_GW_API_TOKEN")
    api: dict = {}
    c, b = d.gw("GET", "/api/v1/health")
    api["health"] = {"http": c, "status": d.j(b).get("status"), "core": d.j(b).get("core")}
    c, b = d.gw("GET", "/api/v1/protocols/presets")
    api["presets_http"] = c
    c, b = d.gw("GET", "/", auth=False)
    api["web_index_http"] = c
    c, b = d.gw("POST", "/api/v1/uploads/model-config?filename=yolo11n_user.yaml",
                raw=(REG / "examples/models/yolo11n_user.yaml").read_bytes(), ctype="application/x-yaml")
    up = d.j(b).get("upload_id")
    api["upload_http"] = c
    c, b = d.gw("POST", "/api/v1/structure-check", {"upload_id": up, "input_size": 64})
    api["structure_check"] = {"http": c, "status": d.j(b).get("status")}
    out["api"] = api
    out["verify"] = d.run("reg_verify_unit_relocation", ["bash", str(rb / "verify.sh"), "--profile", "public",
                                                         "--suite", "unit,relocation", "--json",
                                                         str(d.EV6 / "verify_registration.json")], cwd, timeout=5400)
    out["stop"] = d.run("reg_stop", ["bash", str(rb / "stop.sh"), "--profile", "public"], cwd)
    (d.EV6 / "summary_registration.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    d.log(f"registration summary -> {(d.EV6 / 'summary_registration.json').relative_to(d.SRC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

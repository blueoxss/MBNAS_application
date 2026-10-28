"""T6 rerun2 API probe through the public gateway (stdlib only; runs inside the sandbox with the bundle's venv, -I).

Saves every response body (+ status/headers) under <evidence>/responses for the T8 runtime scan and prints a summary
JSON. The console token is read from the gateway env file and never printed.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gw", required=True)
    ap.add_argument("--token-env", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--output-root", required=True)
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--full", default="1")
    a = ap.parse_args()
    token = next(l.split("=", 1)[1].strip().strip('"').strip("'") for l in Path(a.token_env).read_text().splitlines()
                 if l.startswith("MBNAS_GW_API_TOKEN="))
    out_dir = Path(a.evidence) / "responses"
    out_dir.mkdir(parents=True, exist_ok=True)
    steps: list[dict] = []

    def call(method: str, path: str, body=None, *, raw: bytes | None = None, ctype: str | None = None,
             name: str | None = None, auth: bool = True) -> tuple[int, bytes]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(a.gw + path, data=data, method=method)
        if auth:
            req.add_header("Authorization", f"Bearer {token}")
        if data is not None:
            req.add_header("Content-Type", ctype or "application/json")
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                code, payload, headers = r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            code, payload, headers = e.code, e.read(), dict(e.headers)
        if name:
            (out_dir / name).write_bytes(payload)
            (out_dir / (name + ".headers.json")).write_text(json.dumps({"status": code, "headers": headers}, indent=1))
            steps.append({"call": f"{method} {path.split('?')[0]}", "http": code, "bytes": len(payload),
                          "request_id": headers.get("X-Request-Id") or headers.get("x-request-id")})
        return code, payload

    def j(b: bytes) -> dict:
        try:
            v = json.loads(b)
            return v if isinstance(v, dict) else {"_list": v}
        except ValueError:
            return {}

    s: dict = {}
    c, b = call("GET", "/api/v1/health", name="health.json")
    h = j(b)
    s["health"] = {"http": c, "status": h.get("status"), "core": h.get("core"), "allowlists": h.get("allowlists"),
                   "devices": [d.get("kind") or d.get("device_kind") or d.get("id") for d in h.get("devices", [])
                               if isinstance(d, dict)]}
    c, _ = call("GET", "/api/v1/health", name="health_noauth.json", auth=False)
    s["health_noauth_http"] = c
    c, _ = call("GET", "/api/v1/protocols/presets", name="presets.json")
    s["presets_http"] = c
    c, b = call("GET", "/", name="web_index.html", auth=False)
    s["web_index_http"] = c
    c, b = call("POST", "/api/v1/uploads/model-config?filename=yolo11n_user.yaml",
                raw=(Path(a.models) / "yolo11n_user.yaml").read_bytes(), ctype="application/x-yaml", name="upload.json")
    s["upload_http"] = c
    c, b = call("POST", "/api/v1/structure-check", {"upload_id": j(b).get("upload_id"), "input_size": 64},
                name="structure_check.json")
    s["structure_check"] = {"http": c, "status": j(b).get("status")}
    if a.full == "1":
        c, _ = call("GET", "/api/v1/registry", name="registry.json")
        s["registry_http"] = c
        c, b = call("POST", "/api/v1/uploads/model-config?filename=yolo11n_user_broken.yaml",
                    raw=(Path(a.models) / "yolo11n_user_broken.yaml").read_bytes(), ctype="application/x-yaml",
                    name="upload_broken.json")
        c, b = call("POST", "/api/v1/structure-check", {"upload_id": j(b).get("upload_id"), "input_size": 64},
                    name="structure_check_broken.json")
        diags = j(b).get("diagnostics") if isinstance(j(b).get("diagnostics"), list) else []
        s["structure_check_broken"] = {"http": c, "status": j(b).get("status"),
                                       "codes": sorted({d.get("code") for d in diags if isinstance(d, dict)})}
        policy = {"schema_version": "nas-policy/1",
                  "console": {"paths": {"output_root": a.output_root},
                              "search": {"evaluation_enabled": False, "nc": 1, "input_size": 64,
                                         "families": ["yolo11n"], "seeds": [7], "candidate_count": 2,
                                         "max_attempts": 12, "allow_duplicates": False, "arms": ["GA"]}}}
        c, b = call("POST", "/api/v1/protocols/validate", {"protocol": policy}, name="validate.json")
        v = j(b)
        s["validate"] = {"http": c, "valid": v.get("valid"), "errors": (v.get("errors") or [])[:3]}
        if c == 200 and v.get("valid"):
            c, b = call("POST", "/api/v1/runs", {"protocol": v["resolved"], "protocol_hash": v["protocol_hash"],
                                                 "validation_id": v["validation_id"],
                                                 "idempotency_key": f"t6r2-{int(time.time())}"},
                        name="run_create.json")
            run_id = j(b).get("run_id")
            s["run_create_http"] = c
            status = None
            for _ in range(300):
                c, b = call("GET", f"/api/v1/runs/{run_id}")
                status = j(b).get("status")
                if status in ("COMPLETED", "FAILED", "CANCELLED"):
                    break
                time.sleep(2)
            s["run"] = {"run_id": run_id, "status": status}
            call("GET", f"/api/v1/runs/{run_id}", name="run.json")
            c, b = call("GET", f"/api/v1/runs/{run_id}/snapshot", name="snapshot.json")
            snap = j(b)
            cands = snap.get("generated_candidates") or snap.get("candidates") or []
            s["snapshot"] = {"http": c, "candidates": len(cands)}
            cid = (cands[0].get("candidate_id") or cands[0].get("id")) if cands else None
            if cid:
                c, _ = call("GET", f"/api/v1/runs/{run_id}/candidates/{cid}", name="candidate_detail.json")
                s["candidate_detail_http"] = c
                c, _ = call("GET", f"/api/v1/runs/{run_id}/candidates/{cid}/config-revisions",
                            name="config_revisions.json")
                s["config_revisions_http"] = c
            c, b = call("GET", f"/api/v1/runs/{run_id}/export", name="export.zip")
            s["export"] = {"http": c, "bytes": len(b)}
            c, _ = call("GET", "/api/v1/runs", name="runs_list.json")
            s["runs_list_http"] = c
    s["calls"] = steps
    print(json.dumps(s, indent=1, ensure_ascii=False))
    ok = (s["health"]["http"] == 200 and s["health_noauth_http"] == 401 and s["upload_http"] == 200
          and s["structure_check"]["status"] == "PASS")
    if a.full == "1":
        ok = ok and s.get("run", {}).get("status") == "COMPLETED" and s.get("export", {}).get("http") == 200
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

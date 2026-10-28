#!/usr/bin/env python3
"""Generate ``mbnas_contracts/openapi/openapi_runner.json`` (OpenAPI 3.1, ``/core/v1/runner/*``) from the pydantic models of
``mbnas_contracts.runner_protocol``. Deterministic: sorted keys, two-space indent, trailing newline.

Usage: gen_runner_openapi.py [--check] [--out PATH]    (--check exits 1 if the committed file differs)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from mbnas_contracts import runner_protocol as rp

DEFAULT_OUT = Path(rp.__file__).resolve().parent / "openapi" / "openapi_runner.json"
REF = "#/components/schemas/"


def build() -> dict[str, Any]:
    schemas: dict[str, Any] = {}
    for model in rp.MODELS:
        sch = model.model_json_schema(ref_template=REF + "{model}")
        for name, sub in sch.pop("$defs", {}).items():
            schemas.setdefault(name, sub)
        schemas[model.__name__] = sch
    schemas["RunnerProtocolError"] = {"type": "string", "enum": [e.value for e in rp.RunnerProtocolError],
                                      "description": "Machine codes of Error.code specific to the runner protocol."}
    schemas["RunnerAuxCode"] = {"type": "string", "enum": [e.value for e in rp.RunnerAuxCode],
                                "description": "Further codes named by the design (not part of the mandated enum)."}

    def body(model: Any, desc: str) -> dict[str, Any]:
        out: dict[str, Any] = {"description": desc}
        if model is not None:
            out["content"] = {"application/json": {"schema": {"$ref": REF + model.__name__}}}
        return out

    paths: dict[str, Any] = {}
    for ep in rp.ENDPOINTS:
        op: dict[str, Any] = {
            "operationId": ep["op"], "summary": ep["summary"], "tags": ["runner"], "x-endpoint": ep["id"],
            "security": [{"BearerAuth": []}],
            "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": REF + ep["request"].__name__}}}},
            "responses": {code: body(m, d) for code, (m, d) in sorted(ep["responses"].items())},
        }
        op["responses"]["401"] = body(rp.ErrorBody, "AUTH_REQUIRED")
        op["responses"]["403"] = body(rp.ErrorBody, "ACCESS_DENIED, RUNNER_MISMATCH or TICKET_INVALID")
        names = re.findall(r"\{(\w+)\}", ep["path"])  # every path template variable is a declared parameter
        if names:
            op["parameters"] = [{"name": n, "in": "path", "required": True, "schema": {"type": "string", "minLength": 1}}
                                for n in names]
        paths[rp.RUNNER_BASE_PATH + ep["path"]] = {ep["method"]: op}

    return {
        "openapi": "3.1.0",
        "info": {"title": "MBNAS Runner Protocol", "version": rp.RUNNER_API_VERSION,
                 "description": "Remote queue protocol between the MBNAS core and a runner (envelope_version 1). "
                                "Generated from mbnas_contracts.runner_protocol; do not edit by hand."},
        "servers": [{"url": "/"}],
        "tags": [{"name": "runner"}],
        "paths": dict(sorted(paths.items())),
        "components": {
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer",
                                               "description": "Runner principal token (scope runner), bound to one runner_id"}},
            "schemas": dict(sorted(schemas.items())),
        },
    }


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    text = render()
    if args.check:
        if not args.out.is_file() or args.out.read_text(encoding="utf-8") != text:
            print(f"{args.out} is stale; run gen_runner_openapi.py", file=sys.stderr)
            return 1
        return 0
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

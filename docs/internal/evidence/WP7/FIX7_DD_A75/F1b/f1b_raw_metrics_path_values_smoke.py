#!/usr/bin/env python3
"""FIX7 / DD-A75 F1b real-process check with a SPACED + UNICODE root (evidence script, not a repository script).

Wraps ``../F1/f1_raw_metrics_paths_smoke.py`` (v2; itself the unchanged FIX5/WP3 smoke classes: core + owner worker +
REAL ultralytics CPU runner + gateway, 127.0.0.1 ephemeral ports, ``CUDA_VISIBLE_DEVICES=''``, ``--device-source
runners``, ``benchmark.device = cpu``) and changes two things:

* ``--root`` must lie below a directory whose name holds a blank and a unicode word (e.g. ``.../f1b 공백 루트/<label>``):
  every path of the run (core, gateway, runner) contains that ROOT fragment; the client-provided protocol paths (snapshot
  ``run.resolved_protocol``, export ``protocol/*.json``) therefore echo it too (out of F1/F1b scope, reported by pointer);
* the runner's work root is moved to ``<root>/runner 작업 dir/work`` (``MBNAS_RUNNER_WORK_ROOT`` /
  ``MBNAS_RUNNER_OUTPUT_ROOTS``): that RUNNER fragment occurs only in runner-side paths, i.e. in the stored
  ``raw_metrics.resolved_recipe.{data, model, project, save_dir}``.

The scan is NON-circular: the fragments are searched literally and as JSON ``\\u`` escapes in every raw body (snapshot,
candidate detail, SSE replay from seq 0 to last_seq, every export member) and located by JSON pointer (``raw_metrics``
subtree or elsewhere); the ``_ABS_PATH`` regex is reported alongside, never used as the detector.

``--expect leak`` (F1 code) passes when a fragment shows up inside a public raw_metrics; ``--expect clean`` (F1b code)
passes when the RUNNER fragment occurs nowhere in any body and neither fragment occurs inside any raw_metrics.

usage: runtime/venv/bin/python -B <this file> --root "DIR/<name with blank + unicode>/<label>" --label L --expect E
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("f1_raw_metrics_paths_smoke", HERE.parent / "F1" / "f1_raw_metrics_paths_smoke.py")
f1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(f1)  # type: ignore[union-attr]
ROOT, PY, now, text_matches = f1.ROOT, f1.PY, f1.now, f1.text_matches
RUNNER_DIR = "runner 작업 dir"


def escaped(fragment: str) -> str:
    return json.dumps(fragment)[1:-1]


def located(doc: Any, fragments: dict[str, str], where: str = "$") -> list[tuple[str, str, str]]:
    """(fragment label, JSON pointer, 80-char excerpt) of every string or key of ``doc`` that holds a fragment."""
    out: list[tuple[str, str, str]] = []

    def hit(text: str, path: str) -> None:
        for label, frag in fragments.items():
            i = text.find(frag)
            if i >= 0:
                out.append((label, path, text[max(0, i - 30):i + len(frag) + 30]))

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                hit(str(k), f"{path}.<key>")
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            hit(node, path)

    walk(doc, where)
    return out


class F1bSmoke(f1.F1Smoke):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)
        base = Path(args.root).resolve().parent.name
        if " " not in base or base.isascii():
            raise SystemExit(f"--root must lie below a directory named with a blank and a unicode word, got {base!r}")
        # the part after the first blank survives a regex that stops at that blank, so the tails are searched too
        self.fragments = {"ROOT": base, "ROOT_TAIL": base.split(" ", 1)[1], "RUNNER": RUNNER_DIR,
                          "RUNNER_TAIL": RUNNER_DIR.split(" ", 1)[1]}

    def runner_env(self, name: str = "runner", runner_id: str = "smoke-runner-1") -> dict[str, str]:
        env = super().runner_env(name, runner_id)
        work = self.root / RUNNER_DIR / "work"
        env.update({"MBNAS_RUNNER_WORK_ROOT": str(work), "MBNAS_RUNNER_OUTPUT_ROOTS": json.dumps([str(work)])})
        return env

    def step_fetch_and_scan(self, run_id: str, cid: str, recipe_abs: dict[str, str]) -> bool:
        t0 = time.monotonic()
        bodies: dict[str, tuple[int, str, Any]] = {}
        code, snap, rid_s, _ = self.gw("GET", f"/api/v1/runs/{run_id}/snapshot", raw=True)
        text = snap.decode("utf-8", "replace")
        bodies["snapshot"] = (code, text, json.loads(text) if code == 200 else None)
        code, det, rid_d, _ = self.gw("GET", f"/api/v1/runs/{run_id}/candidates/{cid}", raw=True)
        text = det.decode("utf-8", "replace")
        bodies["candidate_detail"] = (code, text, json.loads(text) if code == 200 else None)
        last_seq = int((bodies["snapshot"][2] or {}).get("last_seq") or 0)
        sse_code, sse_text, seen = self.read_sse(run_id, last_seq)
        events = [json.loads(ln[len("data: "):]) for ln in sse_text.splitlines() if ln.startswith("data: ")]
        bodies["sse_replay"] = (sse_code, sse_text, events)
        export_code, zbytes, rid_z, _ = self.gw("GET", f"/api/v1/runs/{run_id}/export", raw=True)
        if export_code == 200:
            with zipfile.ZipFile(io.BytesIO(zbytes)) as zf:
                for name in zf.namelist():
                    data = zf.read(name).decode("utf-8", "replace")
                    doc: Any = None
                    if name.endswith(".json"):
                        doc = json.loads(data)
                    elif name.endswith(".jsonl"):
                        doc = [json.loads(x) for x in data.splitlines() if x.strip()]
                    bodies[f"export!{name}"] = (export_code, data, doc)
        report: dict[str, Any] = {}
        totals: dict[str, int] = {f"{lb}_{k}": 0 for lb in self.fragments for k in ("anywhere", "in_raw_metrics")}
        totals["regex_in_raw_metrics"] = 0
        for name, (code_, text_, doc_) in bodies.items():
            texts = [text_] + ([json.dumps(doc_, ensure_ascii=False)] if doc_ is not None else [])
            in_text = {label: sum(t.count(frag) + t.count(escaped(frag)) for t in texts[:1])
                       for label, frag in self.fragments.items()}  # occurrences in the raw body as sent
            loc = located(doc_, self.fragments) if doc_ is not None else []
            in_raw = [x for x in loc if ".raw_metrics" in x[1]]
            other = [x for x in loc if ".raw_metrics" not in x[1]]
            regex_raw = [m for x in f1.json_matches(doc_) if ".raw_metrics" in x[0] for m in [x[1]]] if doc_ is not None else []
            values: dict[str, int] = {}
            for raw in f1.raw_metrics_objects(doc_):
                rr = raw.get("resolved_recipe")
                if isinstance(rr, dict):
                    key = json.dumps({k: rr.get(k, "<absent>") for k in sorted(recipe_abs)}, sort_keys=True,
                                     ensure_ascii=False)
                    values[key] = values.get(key, 0) + 1
            groups: dict[str, dict[str, Any]] = {}
            for label, path, excerpt in other:
                g = groups.setdefault(f"{label} {f1.pattern(path)}", {"count": 0, "sample": excerpt})
                g["count"] += 1
            report[name] = {"http": code_, "bytes": len(text_),
                            "fragment_occurrences_in_raw_body": in_text,
                            "fragment_in_raw_metrics": [{"fragment": lb, "pointer": f1.pattern(p), "excerpt": ex}
                                                        for lb, p, ex in in_raw],
                            "fragment_elsewhere_by_pointer": dict(sorted(groups.items())),
                            "regex_matches_in_raw_metrics": len(regex_raw),
                            "projected_recipe_values": [{"count": n, "values": json.loads(k)} for k, n in values.items()]}
            for lb in self.fragments:
                totals[f"{lb}_anywhere"] += in_text[lb]
                totals[f"{lb}_in_raw_metrics"] += sum(1 for x in in_raw if x[0] == lb)
            totals["regex_in_raw_metrics"] += len(regex_raw)
        carried = sum(1 for e in events if isinstance((e.get("payload") or {}).get("raw_metrics"), dict))
        fetched = (bodies["snapshot"][0] == 200 and bodies["candidate_detail"][0] == 200 and sse_code == 200
                   and seen >= last_seq > 0 and carried > 0)
        in_raw_total = sum(totals[f"{lb}_in_raw_metrics"] for lb in self.fragments)
        if self.args.expect == "clean":
            verdict = (fetched and totals["RUNNER_anywhere"] == 0 and totals["RUNNER_TAIL_anywhere"] == 0
                       and in_raw_total == 0)
        else:
            verdict = fetched and in_raw_total > 0
        self.scan = {"fragments": self.fragments, "totals": totals, "bodies": report,
                     "sse": {"last_seq": last_seq, "replayed_to": seen, "events": len(events),
                             "events_with_raw_metrics": carried}}
        return self.step(f"fetch-and-fragment-scan(expect={self.args.expect})", bool(verdict), t0, totals=totals,
                         fragments=self.fragments,
                         http={"snapshot": bodies["snapshot"][0], "candidate_detail": bodies["candidate_detail"][0],
                               "sse_replay": sse_code, "export": export_code},
                         sse_events=len(events), sse_events_with_raw_metrics=carried, last_seq=last_seq,
                         request_ids=[rid_s, rid_d, rid_z])

    def write_evidence(self, ok: bool, started: str, dur: float) -> None:
        super().write_evidence(ok, started, dur)
        out = Path(self.args.evidence)
        text = out.read_text(encoding="utf-8").replace("# FIX7 DD-A75 F1 real-process raw_metrics path check",
                                       "# FIX7 DD-A75 F1b real-process raw_metrics path-value check (spaced + unicode root)", 1)
        text = text.replace(f"# command: {PY} -B {Path(f1.__file__).relative_to(ROOT)}",
                            f"# command: {PY} -B {Path(__file__).relative_to(ROOT)}", 1)
        out.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--gw-port", type=int, default=0)
    p.add_argument("--root", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--expect", choices=("leak", "clean"), required=True)
    p.add_argument("--timeout", type=float, default=900)
    p.add_argument("--web-dist", default=None)
    p.add_argument("--device-source", choices=("local", "runners"), default="runners")
    p.add_argument("--evidence", default=None)
    args = p.parse_args(argv)
    if not args.evidence:
        args.evidence = str(HERE / f"30_real_process_{args.label}.txt")
    return F1bSmoke(args).run()


if __name__ == "__main__":
    raise SystemExit(main())

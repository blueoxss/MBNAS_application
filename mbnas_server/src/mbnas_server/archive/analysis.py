"""Pure computations over candidate rows: Pareto fronts, generation deltas, lineage, module histograms, curves.

Formulas (identical to ``docs/ARCHIVE_API.md``; nothing here is a runtime value except where stated):

* eligible candidate = ``status == "ok"`` and every objective of the front finite.
* 2D front objectives: ``map50_95`` max, ``latency_median_ms`` min. 4D: ``map50_95`` max, ``small_target_recall`` max,
  ``latency_median_ms`` min, ``params_deploy`` min (``params`` when ``params_deploy`` is absent).
* ``a`` dominates ``b`` iff ``a`` is no worse in every objective and strictly better in at least one; equal vectors do
  not dominate each other, so duplicates (cache-hit twins) are all kept on the front.
* the front of generation ``g`` is computed over all eligible rows with ``generation <= g`` (cumulative).
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from typing import Any, Callable, Iterable

from mbnas_server.archive.parsers import finite, parse_notes, parse_ts

ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

OBJECTIVES: tuple[tuple[str, str, str], ...] = (
    ("map50_95", "max", "mAP50-95"),
    ("small_target_recall", "max", "small-target recall"),
    ("latency_median_ms", "min", "latency median (ms)"),
    ("params_deploy", "min", "params (deploy)"),
)
SENSE = {"map50_95": 1, "small_target_recall": 1, "latency_median_ms": -1, "params_deploy": -1}
KEYS_2D = ("map50_95", "latency_median_ms")
KEYS_4D = ("map50_95", "small_target_recall", "latency_median_ms", "params_deploy")


def valid_id(value: Any) -> bool:
    """A run id / candidate id usable as a single path component: ``[A-Za-z0-9._-]{1,128}``, not starting with ``.``."""
    return isinstance(value, str) and bool(ID_RE.fullmatch(value)) and not value.startswith(".")


def metric(cand: dict[str, Any], key: str) -> float | None:
    """Finite metric of a candidate; ``params_deploy`` falls back to ``params``."""
    m = cand["metrics"]
    v = finite(m.get(key))
    if v is None and key == "params_deploy":
        v = finite(m.get("params"))
    return v


# ----------------------------------------------------------------------------------------------------- rows

def normalize_rows(raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """jsonl objects -> candidate dicts in file order. Rows without a valid ``candidate_id`` or an integer
    ``generation`` are skipped (reported); a repeated (candidate, tier, attempt) keeps the last occurrence."""
    warnings: list[str] = []
    out: list[dict[str, Any]] = []
    seen: dict[tuple[Any, ...], int] = {}
    skipped = 0
    for r in raw:
        cid, gen = r.get("candidate_id"), r.get("generation")
        if not valid_id(cid) or isinstance(gen, bool) or not isinstance(gen, int):
            skipped += 1
            continue
        metrics = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
        cand = {"candidate_id": cid, "generation": gen, "fingerprint": r.get("architecture_fingerprint") or None,
                "tier": r.get("fidelity_tier"), "status": r.get("status") if isinstance(r.get("status"), str) else "unknown",
                "seed": r.get("seed") if isinstance(r.get("seed"), int) else None,
                "epochs": r.get("epochs") if isinstance(r.get("epochs"), int) else None,
                "metrics": metrics, "notes": parse_notes(r.get("notes")), "raw": r}
        key = (cid, cand["tier"], r.get("attempt"))
        if key in seen:
            out[seen[key]] = cand
        else:
            seen[key] = len(out)
            out.append(cand)
    if skipped:
        warnings.append(f"{skipped} jsonl row(s) without a valid candidate_id/generation were skipped")
    for i, c in enumerate(out):
        c["idx"] = i
    return out, warnings


def is_ok(cand: dict[str, Any]) -> bool:
    return cand["status"] == "ok"


# ----------------------------------------------------------------------------------------------------- fronts

def _vec(cand: dict[str, Any], keys: tuple[str, ...]) -> tuple[float, ...] | None:
    if not is_ok(cand):
        return None
    out = []
    for k in keys:
        v = metric(cand, k)
        if v is None:
            return None
        out.append(-v if SENSE[k] > 0 else v)  # minimisation form
    return tuple(out)


def dominates(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


class FrontTracker:
    """Incrementally maintained non-dominated set (minimisation vectors). Adding a point costs O(front size)."""

    def __init__(self) -> None:
        self.members: list[tuple[int, tuple[float, ...]]] = []  # (row idx, vector)

    def add(self, idx: int, vec: tuple[float, ...]) -> None:
        for _, v in self.members:
            if dominates(v, vec):
                return
        self.members = [(i, v) for i, v in self.members if not dominates(vec, v)]
        self.members.append((idx, vec))

    def indices(self) -> list[int]:
        return sorted(i for i, _ in self.members)


def front_series(cands: list[dict[str, Any]], generations: list[int]) -> dict[str, dict[int, list[int]]]:
    """Cumulative fronts per generation: ``{"2d": {g: [row idx]}, "4d": {...}}`` for every g in ``generations``."""
    by_gen: dict[int, list[dict[str, Any]]] = {}
    for c in cands:
        by_gen.setdefault(c["generation"], []).append(c)
    t2, t4 = FrontTracker(), FrontTracker()
    out: dict[str, dict[int, list[int]]] = {"2d": {}, "4d": {}}
    gens = sorted(set(generations) | set(by_gen))
    for g in gens:
        for c in by_gen.get(g, []):
            v2, v4 = _vec(c, KEYS_2D), _vec(c, KEYS_4D)
            if v2 is not None:
                t2.add(c["idx"], v2)
            if v4 is not None:
                t4.add(c["idx"], v4)
        out["2d"][g] = t2.indices()
        out["4d"][g] = t4.indices()
    return out


def ids_of(cands: list[dict[str, Any]], idxs: Iterable[int], sort_key: str = "map50_95") -> list[str]:
    """Unique candidate ids of the given row indices, best ``sort_key`` first (ties: file order)."""
    rows = [cands[i] for i in idxs]
    rows.sort(key=lambda c: (-(metric(c, sort_key) if metric(c, sort_key) is not None else -math.inf), c["idx"]))
    seen: list[str] = []
    for c in rows:
        if c["candidate_id"] not in seen:
            seen.append(c["candidate_id"])
    return seen


# ----------------------------------------------------------------------------------------------------- per generation

def status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(r["status"] for r in rows).items()))


def new_fingerprints(cands: list[dict[str, Any]]) -> dict[int, tuple[int, list[str]]]:
    """Per generation: (number of fingerprints not seen in any earlier generation, first candidate id of each).
    Rows without a fingerprint are ignored; every status counts (a failed candidate still has an architecture)."""
    seen: set[str] = set()
    out: dict[int, tuple[int, list[str]]] = {}
    for g in sorted({c["generation"] for c in cands}):
        fresh: dict[str, str] = {}
        for c in cands:
            fp = c["fingerprint"]
            if c["generation"] == g and fp and fp not in seen and fp not in fresh:
                fresh[fp] = c["candidate_id"]
        seen |= set(fresh)
        out[g] = (len(fresh), list(fresh.values()))
    return out


def lineage_modes(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter((r["notes"]["mode"] or "unknown") for r in rows).items()))


def best_of(rows: list[dict[str, Any]], key: str) -> tuple[float, str] | None:
    """Best (value, candidate id) of ``key`` over eligible rows (status ok, finite); ties keep the first row."""
    best: tuple[float, str] | None = None
    for r in rows:
        if not is_ok(r):
            continue
        v = metric(r, key)
        if v is None:
            continue
        if best is None or (v - best[0]) * SENSE[key] > 0:
            best = (v, r["candidate_id"])
    return best


def improvements(cands: list[dict[str, Any]], g: int) -> list[dict[str, Any]]:
    """Per objective: the best of generation ``g`` against the best of all earlier generations (cumulative).
    ``kind`` = ``initial`` (no earlier eligible row), ``improved`` (strictly better than the earlier best)."""
    prior = [c for c in cands if c["generation"] < g]
    here = [c for c in cands if c["generation"] == g]
    out = []
    for key, _direction, _label in OBJECTIVES:
        new = best_of(here, key)
        if new is None:
            continue
        prev = best_of(prior, key)
        if prev is None:
            out.append({"objective": key, "prev_best": None, "new_best": new[0], "candidate_id": new[1], "delta": None,
                        "kind": "initial"})
        elif (new[0] - prev[0]) * SENSE[key] > 0:
            out.append({"objective": key, "prev_best": prev[0], "new_best": new[0], "candidate_id": new[1],
                        "delta": new[0] - prev[0], "kind": "improved"})
    return out


def cumulative_best(cands: list[dict[str, Any]], g: int) -> dict[str, float | None]:
    rows = [c for c in cands if c["generation"] <= g]
    return {key: (lambda b: b[0] if b else None)(best_of(rows, key)) for key, _d, _l in OBJECTIVES}


def sub(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


# ----------------------------------------------------------------------------------------------------- architectures

CONV_LIKE = {"Conv", "DWConv", "GhostConv", "Conv2", "LightConv", "RepConv"}
POOLS = {"nn.MaxPool2d", "nn.AvgPool2d", "MaxPool2d", "AvgPool2d"}
ALWAYS_DOWN = {"ADown", "SCDown"}


def cfg_rows(cfg: Any, part: str) -> list[list[Any]]:
    rows = cfg.get(part) if isinstance(cfg, dict) else None
    return [r for r in rows if isinstance(r, list) and len(r) >= 3 and isinstance(r[2], str)] if isinstance(rows, list) else []


def module_names(cfg: Any) -> list[str]:
    """Module name of every ``backbone`` + ``head`` row (``[from, repeats, module, args]``); repeats are not expanded."""
    return [r[2] for r in cfg_rows(cfg, "backbone") + cfg_rows(cfg, "head")]


def module_histogram(cfgs: Iterable[Any]) -> dict[str, int]:
    """Sum over configs of the per-row module-name counts, sorted by count desc then name."""
    c: Counter[str] = Counter()
    for cfg in cfgs:
        c.update(module_names(cfg))
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))


def _is_down(module: str, args: Any) -> bool:
    if module in POOLS or module in ALWAYS_DOWN:
        return True
    return module in CONV_LIKE and isinstance(args, list) and len(args) > 2 and args[2] == 2


def derive_stages(cfg: Any) -> list[dict[str, Any]]:
    """Backbone stages: a new stage starts at the first row and at every down-sampling row (stride-2 conv-like module,
    pooling, ADown/SCDown). Agrees with the runtime genotype stages on all 33 archive entries of the NPS day run that
    have a cfg file (checked in tests/archive)."""
    stages: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for i, r in enumerate(cfg_rows(cfg, "backbone")):
        down = _is_down(r[2], r[3] if len(r) > 3 else None)
        if down or cur is None:
            cur = {"stage_index": len(stages), "global_start": i, "modules": [], "downsamples": 0}
            stages.append(cur)
        cur["modules"].append(r[2])
        if down:
            cur["downsamples"] += 1
    return stages


def spread(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "med": None, "max": None}
    med = statistics.median(values)
    return {"min": min(values), "med": med, "max": max(values)}


def characteristics(rows: list[dict[str, Any]], prev_rows: list[dict[str, Any]], cfg_of: Callable[[str], Any]) -> dict[str, Any]:
    """Features of the ``status == ok`` candidates of one generation (see module docstring of views / ARCHIVE_API.md)."""
    ok = [r for r in rows if is_ok(r)]
    prev_ok = [r for r in prev_rows if is_ok(r)]
    cfgs = [(r, cfg_of(r["candidate_id"])) for r in ok]
    have = [c for _, c in cfgs if c is not None]
    prev_cfgs = [c for c in (cfg_of(r["candidate_id"]) for r in prev_ok) if c is not None]
    hist, prev_hist = module_histogram(have), module_histogram(prev_cfgs)
    present: Counter[str] = Counter()
    for c in have:
        present.update(set(module_names(c)))
    delta = []
    for mod in set(hist) | set(prev_hist):
        d = hist.get(mod, 0) - prev_hist.get(mod, 0)
        if d == 0:
            continue
        per = None
        if have and prev_cfgs:
            per = hist.get(mod, 0) / len(have) - prev_hist.get(mod, 0) / len(prev_cfgs)
        delta.append({"module": mod, "count": hist.get(mod, 0), "count_prev": prev_hist.get(mod, 0), "delta": d,
                      "delta_per_candidate": per})
    delta.sort(key=lambda e: (-abs(e["delta"]), e["module"]))
    head_mode = Counter((r["metrics"].get("head_mode") or "unknown") for r in ok)
    return {
        "n": len(ok), "n_cfg": len(have), "n_cfg_missing": len(ok) - len(have),
        "n_prev": len(prev_ok), "n_prev_cfg": len(prev_cfgs),
        "module_histogram": hist, "module_histogram_prev": prev_hist,
        "module_candidates": dict(sorted(present.items(), key=lambda kv: (-kv[1], kv[0]))),
        "head_mode": {"e2e": head_mode.get("e2e", 0), "nms": head_mode.get("nms", 0),
                      **{k: v for k, v in sorted(head_mode.items()) if k not in ("e2e", "nms")}},
        "depth": {"n_backbone": spread([float(len(cfg_rows(c, "backbone"))) for c in have]),
                  "n_head": spread([float(len(cfg_rows(c, "head"))) for c in have]),
                  "stage_count": spread([float(len(derive_stages(c))) for c in have])},
        "params_deploy": spread([v for v in (metric(r, "params_deploy") for r in ok) if v is not None]),
        "latency_median_ms": spread([v for v in (metric(r, "latency_median_ms") for r in ok) if v is not None]),
        "top_modules_delta": delta[:8],
    }


def parent_diff(parent_id: str, parent_cfg: Any, child_cfg: Any) -> dict[str, Any]:
    """Module / stage differences child vs one parent (both cfg dicts)."""
    pc, cc = Counter(module_names(parent_cfg)), Counter(module_names(child_cfg))
    added = sorted(m for m in cc if cc[m] > pc.get(m, 0))
    removed = sorted(m for m in pc if pc[m] > cc.get(m, 0))
    ps, cs = derive_stages(parent_cfg), derive_stages(child_cfg)
    stage_diff = []
    for i in range(max(len(ps), len(cs))):
        a = ps[i]["modules"] if i < len(ps) else []
        b = cs[i]["modules"] if i < len(cs) else []
        if a != b:
            stage_diff.append({"stage_index": i, "parent_modules": a, "child_modules": b})
    return {"parent": parent_id, "modules_added": added, "modules_removed": removed,
            "module_delta": {m: cc.get(m, 0) - pc.get(m, 0) for m in sorted(set(cc) | set(pc)) if cc.get(m, 0) != pc.get(m, 0)},
            "stage_diff": stage_diff, "n_rows_parent": len(module_names(parent_cfg)), "n_rows_child": len(module_names(child_cfg))}


# ----------------------------------------------------------------------------------------------------- curves

def best_so_far_curve(cands: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """(points, rows without train_time_s, cache-hit rows). Rows are walked in jsonl order; ``cum_train_time_s`` adds
    ``metrics.train_time_s`` of every row (a missing value is skipped, not counted as 0). A point is emitted whenever the
    best ``map50_95`` of eligible rows improves strictly, and a final point repeats the best at the last row so the
    curve spans the whole run. ``cum_train_time_s_no_cache`` leaves out cache-hit rows (their train_time_s is a copy of
    the source candidate's)."""
    pts: list[dict[str, Any]] = []
    cum = cum_nc = 0.0
    skipped = hits = 0
    best: tuple[float, str] | None = None
    last: dict[str, Any] | None = None
    for c in cands:
        t = finite(c["metrics"].get("train_time_s"))
        cache = c["notes"]["cache_hit"] is not None
        hits += 1 if cache else 0
        if t is None:
            skipped += 1
        else:
            cum += t
            if not cache:
                cum_nc += t
        v = metric(c, "map50_95") if is_ok(c) else None
        last = c
        if v is not None and (best is None or v > best[0]):
            best = (v, c["candidate_id"])
            pts.append({"generation": c["generation"], "cum_train_time_s": round(cum, 3), "value": v,
                        "candidate_id": c["candidate_id"], "cum_train_time_s_no_cache": round(cum_nc, 3)})
    if best is not None and last is not None and (not pts or pts[-1]["cum_train_time_s"] != round(cum, 3)):
        pts.append({"generation": last["generation"], "cum_train_time_s": round(cum, 3), "value": best[0],
                    "candidate_id": best[1], "cum_train_time_s_no_cache": round(cum_nc, 3)})
    return pts, skipped, hits


# ----------------------------------------------------------------------------------------------------- gpu guard

def gpu_guard_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Guard records are the JSON lines with an ``action`` key. An episode per GPU = consecutive ``wait`` records up to the
    terminating ``proceed`` / ``gpu_busy_giveup`` (or the last wait when still open)."""
    recs = [r for r in records if isinstance(r, dict) and isinstance(r.get("action"), str)]
    recs.sort(key=lambda r: finite(r.get("timestamp")) or 0.0)
    open_ep: dict[str, dict[str, Any]] = {}
    episodes: list[dict[str, Any]] = []
    giveups = 0
    from mbnas_server.archive.parsers import iso_from_epoch

    def ts(r: dict[str, Any]) -> str | None:
        t = finite(r.get("timestamp"))
        return iso_from_epoch(t) if t is not None else None

    def bump(ep: dict[str, Any], key: str, value: float | None) -> None:
        if value is not None:
            ep[key] = value if ep[key] is None else max(ep[key], value)

    def close(gpu: str, outcome: str, r: dict[str, Any] | None) -> None:
        ep = open_ep.pop(gpu, None)
        if ep is None:
            return
        if r is not None:
            ep["end"] = ts(r)
            bump(ep, "waited_s", finite(r.get("waited_s")))
        ep["outcome"] = outcome
        episodes.append(ep)

    def new_ep(gpu: str, r: dict[str, Any]) -> dict[str, Any]:
        return {"gpu": gpu, "waited_s": None, "external_mib_max": None, "start": ts(r), "end": ts(r), "outcome": "open"}

    for r in recs:
        gpu = str(r.get("gpu"))
        act = r["action"]
        if act in ("wait", "gpu_busy_giveup"):
            ep = open_ep.setdefault(gpu, new_ep(gpu, r))
            bump(ep, "external_mib_max", finite(r.get("external_mib")))
            bump(ep, "waited_s", finite(r.get("waited_s")))
            if act == "wait":
                ep["end"] = ts(r)
            else:
                giveups += 1
                close(gpu, "giveup", r)
        elif act == "proceed":
            close(gpu, "proceed", r)
    for gpu in list(open_ep):
        close(gpu, "open", None)
    episodes.sort(key=lambda e: (e["start"] or "", e["gpu"]))
    return {"records": len(recs), "episodes": episodes, "giveups": giveups}


def duration_s(start: str | None, end: str | None) -> float | None:
    a, b = parse_ts(start), parse_ts(end)
    return round(b - a, 3) if a is not None and b is not None and b >= a else None

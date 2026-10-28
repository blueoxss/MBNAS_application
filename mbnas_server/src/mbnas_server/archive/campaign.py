"""Read-only adapter that serves the arms of a 12-search VisDrone campaign as archive runs (design decision DD-39).

A campaign root ``CROOT`` is written by ``scripts/campaign12`` and looks like::

    CROOT/campaign.json                       frozen config (campaign_id, contract.config.{preset,budget}, runtime_metadata.paths.prep_run_dir)
    CROOT/queue.json                          arms[{arm, k, state, ...}], pair_done[k]
    CROOT/prep/<...>/<prep run>/              prep_manifest.json (BASELINES, DATASET_MANIFEST_HASH), preset.json (dataset_id), protocol/
    CROOT/arms/<space S1|S2>/<method ga|random>/<seed_N>/
        arm_manifest.json                     written first (attempt, pid, t_start, gpu)
        arm_result.json                       only once the arm ended (ARM_COMPLETED | NO_TERMINAL_RESULT | ARM_INTERRUPTED | BLOCKED_<reason>)
        search/{candidates/candidate_evaluations.jsonl, candidates/train_runs/, generations/gen_NNN_summary.json,
                pareto/{pareto_archive.csv, objective_champions.json}, exclusive_latency.json, ...}
        ledger/{cost_ledger.json, eval_timing.jsonl}   budget ledger; the progress shown for a running arm comes from here
        final/, driver/

``search/`` is produced by the same ``yolo_ga_full_runtime`` as a plain archive run, so the existing :class:`scanner.RunData` parsers
and :mod:`views` are reused unchanged: an arm directory is handed to them as a run directory (``RunRef.real`` = the arm directory).
:class:`CampaignRunData` only replaces what a campaign arm does not have in the plain-run layout (status, start/end, baselines, preset,
final results) by values mapped from the campaign files, and returns ``None`` for everything that does not exist.

Run id (URL-safe, one path segment)::

    <campaign_id>__<space>__<method>__<seed_dir>      e.g. vd12_20261021_2240__S2__ga__seed_42

``__`` separates the four parts; each part is checked against ``[A-Za-z0-9._-]`` and must not itself contain ``__`` (the id is never
parsed back: the store looks it up in the discovered arms), so the id passes ``analysis.valid_id`` and needs no URL escaping.

Read-only guarantees: nothing under a campaign root is ever opened for writing or created; only the files named above are read, always
through the stat-keyed caches of :class:`scanner.ArchiveStore` / :class:`scanner.RunData`; a symlink that leaves the campaign root is
ignored. ``candidate_evaluations.jsonl`` is rewritten in place by the runtime (non-atomic): the tolerant jsonl parser skips a torn last
line and reports it as a warning; every other source is a JSON file or ``ledger/eval_timing.jsonl`` (append-only).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from mbnas_server.archive import analysis as an
from mbnas_server.archive.live import pid_alive
from mbnas_server.archive.parsers import finite, iso_from_epoch, read_jsonl
from mbnas_server.archive.scanner import RootInfo, RunData, RunRef, _stat_sig, within

FAMILY = "visdrone-campaign"
KIND = "campaign"
SEP = "__"
TERMINAL = ("ARM_COMPLETED", "NO_TERMINAL_RESULT")  # arm ended normally (campaign_arm.py TERMINAL_STATUSES)
SEG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
SEED_RE = re.compile(r"^seed_(\d+)$")
ARM_MARKERS = ("arm_manifest.json", "arm_result.json", "search")
CAMPAIGN_FILES = ("campaign.json", "queue.json")
ARM_FILES = ("arm_manifest.json", "arm_result.json", "ledger/cost_ledger.json", "ledger/eval_timing.jsonl", "search/validate_counters.json",
             "search/pareto/objective_champions.json", "search/pareto/pareto_archive.csv", "search/exclusive_latency.json",
             "search/budget_stop.json", "driver/checklist.json")
MAX_ARMS_PER_CAMPAIGN = 256


@dataclass(frozen=True)
class ArmRef:
    """One arm directory of a campaign (``RunRef.campaign``)."""

    campaign_id: str
    croot: str  # as configured / joined (display)
    croot_real: str
    space: str
    method: str
    seed_name: str
    seed: int | None

    @property
    def arm(self) -> str:
        return f"{self.space}/{self.method}/{self.seed_name}"


def run_id_of(campaign_id: str, space: str, method: str, seed_name: str) -> str:
    return SEP.join((campaign_id, space, method, seed_name))


def _seg_ok(seg: str) -> bool:
    return bool(SEG_RE.fullmatch(seg)) and SEP not in seg and not seg.endswith("_")


def is_croot(real: str) -> bool:
    return os.path.isfile(os.path.join(real, "campaign.json")) or os.path.isdir(os.path.join(real, "arms"))


def _subdirs(real: str) -> list[str]:
    try:
        return sorted(n for n in os.listdir(real) if not n.startswith(".") and os.path.isdir(os.path.join(real, n)))
    except OSError:
        return []


def discover(store: Any, paths: list[str]) -> tuple[list[RootInfo], list[RunRef], list[str]]:
    """(root entries with ``kind='campaign'``, one :class:`RunRef` per arm directory, warnings) for the configured campaign roots.

    A configured path may be a campaign root itself or a directory whose direct sub-directories are campaign roots."""
    roots: list[RootInfo] = []
    refs: list[RunRef] = []
    warnings: list[str] = []
    seen_campaigns: dict[str, str] = {}
    for path in paths:
        real = os.path.realpath(path)
        if not os.path.isdir(real):
            roots.append(RootInfo(path=path, real=real, family=FAMILY, exists=False, kind=KIND))
            warnings.append(f"campaign root not found: {path}")
            continue
        croots = [(path, real)] if is_croot(real) else []
        if not croots:
            for name in _subdirs(real):
                r = os.path.realpath(os.path.join(real, name))
                if within(r, real) and r != real and is_croot(r):
                    croots.append((os.path.join(path, name), r))
        if not croots:
            roots.append(RootInfo(path=path, real=real, family=FAMILY, exists=True, kind=KIND))
            warnings.append(f"no campaign root (campaign.json / arms/) found under {path}")
            continue
        for cpath, creal in croots:
            roots.append(RootInfo(path=cpath, real=creal, family=FAMILY, exists=True, kind=KIND))
            cid = _campaign_id(store, creal, warnings)
            if cid is None:
                continue
            if cid in seen_campaigns:
                warnings.append(f"campaign id {cid} exists in more than one root: using {seen_campaigns[cid]}")
                continue
            seen_campaigns[cid] = cpath
            n0 = len(refs)
            refs.extend(_arm_refs(cid, cpath, creal, warnings))
            if len(refs) == n0:
                warnings.append(f"campaign {cid}: no arm directory found under arms/")
    return roots, refs, warnings


def _campaign_id(store: Any, creal: str, warnings: list[str]) -> str | None:
    doc = store.cfg_cached(os.path.join(creal, "campaign.json"))
    cid = doc.get("campaign_id") if isinstance(doc, dict) else None
    if not isinstance(cid, str) or not _seg_ok(cid):
        if cid is not None or doc is None:
            warnings.append(f"{creal}: campaign.json {'unreadable' if doc is None else 'has no usable campaign_id'}: using the directory name")
        cid = os.path.basename(creal.rstrip(os.sep))
    if not _seg_ok(cid) or len(cid) > 60:
        warnings.append(f"campaign id {cid!r} cannot form a run id (allowed [A-Za-z0-9._-], no '__', <= 60 chars): campaign skipped")
        return None
    return cid


def _arm_refs(cid: str, cpath: str, creal: str, warnings: list[str]) -> list[RunRef]:
    out: list[RunRef] = []
    adir = os.path.join(creal, "arms")
    for space in _subdirs(adir):
        for method in _subdirs(os.path.join(adir, space)):
            for seed_name in _subdirs(os.path.join(adir, space, method)):
                if len(out) >= MAX_ARMS_PER_CAMPAIGN:
                    warnings.append(f"campaign {cid}: more than {MAX_ARMS_PER_CAMPAIGN} arms, the rest is not listed")
                    return out
                if not (_seg_ok(space) and _seg_ok(method) and _seg_ok(seed_name)):
                    warnings.append(f"campaign {cid}: arm {space}/{method}/{seed_name} has a name that cannot form a run id: skipped")
                    continue
                d = os.path.join(adir, space, method, seed_name)
                r = os.path.realpath(d)
                if not within(r, creal) or r == creal:
                    warnings.append(f"campaign {cid}: arm {space}/{method}/{seed_name} escapes its campaign root via a symlink: skipped")
                    continue
                if not any(os.path.exists(os.path.join(r, m)) for m in ARM_MARKERS):
                    continue
                rid = run_id_of(cid, space, method, seed_name)
                if not an.valid_id(rid):
                    warnings.append(f"campaign {cid}: run id {rid!r} is not valid: arm skipped")
                    continue
                m = SEED_RE.match(seed_name)
                arm = ArmRef(campaign_id=cid, croot=cpath, croot_real=creal, space=space, method=method, seed_name=seed_name,
                             seed=int(m.group(1)) if m else None)
                out.append(RunRef(id=rid, family=FAMILY, root=cpath, dir=d, real=r, campaign=arm))
    return out


# ----------------------------------------------------------------------------------------------------- run data

class CampaignRunData(RunData):
    """:class:`RunData` of one campaign arm (see the module docstring)."""

    def __init__(self, store: Any, ref: RunRef) -> None:
        super().__init__(store, ref)
        self.arm: ArmRef = ref.campaign

    # -------------------------------------------------------------- campaign-level files (stat-keyed cache of the store)
    def _croot_file(self, rel: str) -> str:
        return os.path.join(self.arm.croot_real, rel)

    def _campaign(self) -> dict[str, Any] | None:
        def load() -> dict[str, Any] | None:
            doc = self.store.cfg_cached(self._croot_file("campaign.json"))
            if doc is None:
                self._warn("campaign.json: missing or unreadable (preset, budget and prep location unavailable)")
            return doc
        return self.once("campaign", load)

    def _queue(self) -> dict[str, Any] | None:
        return self.once("queue", lambda: self.store.cfg_cached(self._croot_file("queue.json")))

    def _queue_arm(self) -> dict[str, Any] | None:
        q = self._queue() or {}
        for e in q.get("arms") if isinstance(q.get("arms"), list) else []:
            if isinstance(e, dict) and e.get("arm") == self.arm.arm:
                return e
        return None

    def _prep_dir(self) -> str | None:
        def find() -> str | None:
            camp = self._campaign() or {}
            paths = (camp.get("runtime_metadata") or {}).get("paths") if isinstance(camp.get("runtime_metadata"), dict) else None
            cand = paths.get("prep_run_dir") if isinstance(paths, dict) else None
            if isinstance(cand, str) and cand:
                real = os.path.realpath(cand)
                if within(real, self.arm.croot_real) and os.path.isfile(os.path.join(real, "prep_manifest.json")):
                    return real
            pdir = os.path.join(self.arm.croot_real, "prep")
            for a in _subdirs(pdir):
                for b in _subdirs(os.path.join(pdir, a)):
                    real = os.path.realpath(os.path.join(pdir, a, b))
                    if within(real, self.arm.croot_real) and os.path.isfile(os.path.join(real, "prep_manifest.json")):
                        return real
            return None
        return self.once("prep_dir", find)

    def _prep_file(self, rel: str) -> dict[str, Any] | None:
        d = self._prep_dir()
        return self.store.cfg_cached(os.path.join(d, rel)) if d else None

    def _prep_manifest(self) -> dict[str, Any] | None:
        def load() -> dict[str, Any] | None:
            m = self._prep_file("prep_manifest.json")
            if m is None:
                self._warn("prep_manifest.json not found or unreadable under prep/: baselines unavailable")
            return m
        return self.once("prep_manifest", load)

    # -------------------------------------------------------------- arm files
    def _manifest(self) -> dict[str, Any] | None:
        return self.jdict("arm_manifest.json")

    def _result(self) -> dict[str, Any] | None:
        return self.jdict("arm_result.json")

    # -------------------------------------------------------------- signature
    def compute_signature(self) -> tuple[Any, ...]:
        base = self.ref.real
        sig: list[Any] = list(super().compute_signature())
        sig.extend((rel, _stat_sig(os.path.join(base, rel))) for rel in ARM_FILES)
        sig.extend((rel, _stat_sig(self._croot_file(rel))) for rel in CAMPAIGN_FILES)
        pd = self._prep_dir()
        sig.append(("prep", _stat_sig(os.path.join(pd, "prep_manifest.json")) if pd else None))
        return tuple(sig)

    # -------------------------------------------------------------- virtual JSON documents (campaign files mapped to run-layout names)
    def json(self, rel: str) -> Any:
        if rel == "final_summary.json":
            return self.once("json:" + rel, self._final_summary)
        if rel == "budget.json":
            return self.once("json:" + rel, self._budget)
        if rel == "checklist.json":
            return self.once("json:" + rel, lambda: super(CampaignRunData, self).json("driver/checklist.json"))
        if rel == "protocol/protocol_v2.json":
            return self.once("json:" + rel, lambda: self._prep_file("protocol/protocol_v2.json"))
        return super().json(rel)

    def _budget(self) -> dict[str, Any] | None:
        cfg = ((self._campaign() or {}).get("contract") or {}).get("config")
        b = cfg.get("budget") if isinstance(cfg, dict) else None
        return b if isinstance(b, dict) else None

    def _final_summary(self) -> dict[str, Any] | None:
        """Shape of the plain-run ``final_summary.json`` built from ``arm_result.json`` (terminal = final-tier results of the arm).
        None while the arm has no result. ``roles`` carries the runtime's own ``best_terminal`` under the name BEST_TERMINAL."""
        res = self._result()
        if res is None:
            return None
        fv: list[dict[str, Any]] = []
        for t in res.get("terminal") if isinstance(res.get("terminal"), list) else []:
            if not isinstance(t, dict) or not an.valid_id(t.get("cid")):
                continue
            fv.append({"cid": t["cid"], "status": "ok", "val_map50": t.get("val_map50"), "val_map50_95": t.get("val_map50_95"),
                       "small_target_recall": t.get("small_recall"), "latency_median_ms": t.get("latency_v2"),
                       "params_deploy": t.get("params_deploy"), "params_train": t.get("params_train"), "head_mode": t.get("head_mode"),
                       "forward_only_median_ms": t.get("forward_only_median_ms"), "t_post_ms": t.get("t_post_ms"),
                       "peak_mem_mb": t.get("peak_mem_mb"), "best_ckpt": t.get("best_ckpt"),
                       "checkpoint_sha256": t.get("checkpoint_sha256"), "train_s": None})
        roles: dict[str, Any] = {}
        best = res.get("best_terminal")
        if isinstance(best, dict) and an.valid_id(best.get("cid")):
            roles["BEST_TERMINAL"] = {"candidate_id": best["cid"], "val_map50_95": best.get("val_map50_95")}
        pm = self._prep_manifest() or {}
        ds: dict[str, Any] = {}
        if isinstance(pm.get("DATASET_MANIFEST_HASH"), str):
            ds["manifest_hash"] = pm["DATASET_MANIFEST_HASH"]
        gens = res.get("generations")
        return {"dataset": ds, "roles": roles, "final_val": fv,
                "search": {"generations": gens if isinstance(gens, int) and not isinstance(gens, bool) else None},
                "source": "arm_result.json"}

    # -------------------------------------------------------------- preset / baselines (campaign + prep files)
    def preset_doc(self) -> tuple[dict[str, Any] | None, str | None]:
        def build() -> tuple[dict[str, Any] | None, str | None]:
            camp = self._campaign()
            cfg = ((camp or {}).get("contract") or {}).get("config") if isinstance((camp or {}).get("contract"), dict) else None
            preset = cfg.get("preset") if isinstance(cfg, dict) and isinstance(cfg.get("preset"), dict) else None
            if preset is None:
                return None, None
            pp = dict(preset)
            ds = (self._prep_file("preset.json") or {}).get("dataset_id")
            if isinstance(ds, str) and ds:
                pp["dataset_id"] = ds
            return {"scale": pp.get("name") if isinstance(pp.get("name"), str) else None, "preset": pp}, "campaign.json"
        return self.once("preset_doc", build)

    def baselines(self) -> tuple[dict[str, dict[str, Any]], str | None]:
        def build() -> tuple[dict[str, dict[str, Any]], str | None]:
            b = (self._prep_manifest() or {}).get("BASELINES")
            if isinstance(b, dict):
                return {k: v for k, v in b.items() if isinstance(v, dict)}, "prep_manifest.json"
            return {}, None
        return self.once("baselines", build)

    # -------------------------------------------------------------- status / timing
    def _liveness(self) -> tuple[bool | None, int | None]:
        man = self._manifest() or {}
        pid = man.get("pid") if isinstance(man.get("pid"), int) and not isinstance(man.get("pid"), bool) else (self.pids() or {}).get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            return None, None
        return pid_alive(pid, None, finite(man.get("t_start"))), pid

    def pid_states(self) -> dict[str, bool]:
        def build() -> dict[str, bool]:
            alive, _pid = self._liveness()
            return {} if alive is None else {"nb_pid": alive}  # "nb_pid" = the process running the unit (here: the arm process)
        return self.once("pid_states", build)

    def status(self) -> tuple[str, str]:
        def build() -> tuple[str, str]:
            man = self._manifest() or {}
            res = self._result()
            has_file = self.exists("arm_result.json")
            st = res.get("status") if isinstance(res, dict) else None
            if isinstance(st, str):
                if st in TERMINAL:
                    bits = [st]
                    reason = " / ".join(str(res[k]) for k in ("stop_reason", "final_stop_reason") if res.get(k))
                    if reason:
                        bits.append(f"stop {reason}")
                    if isinstance(res.get("n_eval"), int):
                        bits.append(f"{res['n_eval']} evaluations")
                    return "done", "; ".join(bits)
                if st.startswith("BLOCKED_"):
                    return "failed", f"{st}: arm blocked (re-approval needed before it can resume)"
                if st == "ARM_INTERRUPTED":
                    a_res, a_man = res.get("attempt"), man.get("attempt")
                    resumed = isinstance(a_res, int) and isinstance(a_man, int) and a_man > a_res
                    if not resumed:
                        return "failed", f"ARM_INTERRUPTED (attempt {a_res}); resumable with --resume"
                else:
                    return "unknown", f"arm_result.json status {st!r} is not in the campaign vocabulary"
            elif has_file:
                return "unknown", "arm_result.json present but unreadable or without a status"
            alive, pid = self._liveness()
            attempt = man.get("attempt")
            what = f"attempt {attempt}" if attempt is not None else "attempt ?"
            stage = man.get("stage")
            what += f"; stage {stage}" if stage else ""
            if st == "ARM_INTERRUPTED":
                what = f"resumed after ARM_INTERRUPTED ({what})"
            if alive is False:
                return "unknown", f"no arm_result.json and arm process {pid} is not alive ({what})"
            if alive is None:
                return "running", f"no arm_result.json yet ({what}); process liveness unknown (no pid recorded)"
            return "running", f"arm process {pid} alive ({what})"
        return self.once("status", build)

    def started_ended(self) -> tuple[str | None, str | None]:
        led = self._ledger()
        man = self._manifest() or {}
        first = led["first_t"] if led else None
        for cand in (first, finite(man.get("start_epoch")), finite(man.get("t_start"))):
            if cand is not None:
                started = iso_from_epoch(cand)
                break
        else:
            started = None
        ended = None
        if self.status()[0] in ("done", "failed") and self.exists("arm_result.json"):
            # queue.json t_end (set by the launcher when the arm exited) > last ledger event > arm_result.json mtime (last resort; on the real campaign the
            # two arms of pair 0 carry the same mtime although they ended 35 min apart, so the mtime is not the arm end)
            t_end = finite((self._queue_arm() or {}).get("t_end"))
            if t_end is None and led:
                t_end = led["last_t"]
            if t_end is None:
                try:
                    t_end = os.stat(self.path("arm_result.json") or "").st_mtime
                except OSError:
                    t_end = None
            ended = iso_from_epoch(t_end) if t_end is not None else None
        return started, ended

    # -------------------------------------------------------------- ledger-derived progress
    def _ledger(self) -> dict[str, Any] | None:
        def build() -> dict[str, Any] | None:
            p = self.path("ledger/eval_timing.jsonl")
            if p is None:
                return None
            rows, bad, err = read_jsonl(p)
            if err:
                self._warn(f"ledger/eval_timing.jsonl: {err}")
                return None
            if bad:
                self._warn(f"ledger/eval_timing.jsonl: {bad} unparsable line(s) skipped (a line being appended); retried on the next poll")
            gens: set[int] = set()
            kinds: dict[str, int] = {}
            by_status: dict[str, int] = {}
            n_cand = n_hit = 0
            first_t = last_t = last_eval_t = None
            for r in rows:
                k = r.get("kind") if isinstance(r.get("kind"), str) else "unknown"
                kinds[k] = kinds.get(k, 0) + 1
                g = r.get("gen")
                if isinstance(g, int) and not isinstance(g, bool):
                    gens.add(g)
                t0, t1 = finite(r.get("t_start")), finite(r.get("t_end"))
                if t0 is not None and (first_t is None or t0 < first_t):
                    first_t = t0
                if t1 is not None and (last_t is None or t1 > last_t):
                    last_t = t1
                if k == "cand":
                    n_cand += 1
                    s = r.get("status") if isinstance(r.get("status"), str) else "unknown"
                    by_status[s] = by_status.get(s, 0) + 1
                    n_hit += 1 if r.get("cache_hit") is True else 0
                    if t1 is not None and (last_eval_t is None or t1 > last_eval_t):
                        last_eval_t = t1
            return {"rows": len(rows), "bad_lines": bad, "kinds": dict(sorted(kinds.items())), "gens": sorted(gens),
                    "n_cand": n_cand, "n_hit": n_hit, "by_status": dict(sorted(by_status.items())), "first_t": first_t,
                    "last_t": last_t, "last_eval_t": last_eval_t}
        return self.once("ledger", build)

    def progress(self) -> dict[str, Any]:
        """Ledger-derived progress (all values null when the ledger is missing): generations seen, candidate counts by status,
        last evaluation time and the cost counters of ``ledger/cost_ledger.json`` (or ``arm_result.json`` once the arm ended)."""
        def build() -> dict[str, Any]:
            led = self._ledger()
            cost = self.jdict("ledger/cost_ledger.json") or {}
            res = self._result() or {}
            src = res if isinstance(res.get("cost_s"), (int, float)) else cost
            n_by = led["by_status"] if led else {}
            gens = led["gens"] if led else []
            return {
                "generations_seen": len(gens) if led else None, "last_generation": gens[-1] if gens else None,
                "candidates": ({"total": led["n_cand"], "ok": n_by.get("ok", 0), "eval_fail": n_by.get("eval_fail", 0),
                                "degenerate": n_by.get("degenerate", 0), "cache_hit": led["n_hit"], "by_status": n_by} if led else None),
                "last_eval_at": iso_from_epoch(led["last_eval_t"]) if led and led["last_eval_t"] is not None else None,
                "last_event_at": iso_from_epoch(led["last_t"]) if led and led["last_t"] is not None else None,
                "ledger_rows": led["rows"] if led else None,
                "cost_s": finite(src.get("cost_s")), "cost_search_s": finite(src.get("cost_search_s")),
                "search_budget_s": finite(cost.get("search_s")), "arm_budget_s": finite(cost.get("total_s")),
                "source": "ledger/eval_timing.jsonl" if led else None,
            }
        return self.once("progress", build)

    # -------------------------------------------------------------- additive summary fields
    def summary_extras(self, base: dict[str, Any]) -> dict[str, Any]:
        a = self.arm
        q = self._queue() or {}
        qa = self._queue_arm() or {}
        k = qa.get("k")
        pair_done = q.get("pair_done")
        res = self._result()
        out: dict[str, Any] = {
            "campaign_id": a.campaign_id, "campaign_arm": a.arm, "space": a.space, "method": a.method, "seed": a.seed,
            "pair": k if isinstance(k, int) and not isinstance(k, bool) else None,
            "pair_done": (k in pair_done) if isinstance(k, int) and isinstance(pair_done, list) else None,
            "queue_state": qa.get("state") if isinstance(qa.get("state"), str) else None,
            "campaign_arms_total": len(q["arms"]) if isinstance(q.get("arms"), list) else None,
            "arm_status": res.get("status") if isinstance(res, dict) and isinstance(res.get("status"), str) else None,
            "progress": self.progress(),
            "arm": {"space": a.space, "method": a.method, "seed": a.seed},
        }
        if isinstance(res, dict):
            bt = res.get("best_terminal") if isinstance(res.get("best_terminal"), dict) else None
            out["result"] = {
                "stop_reason": res.get("stop_reason"), "final_stop_reason": res.get("final_stop_reason"), "n_eval": res.get("n_eval"),
                "terminal_count": res.get("terminal_count"), "flags": res.get("flags") if isinstance(res.get("flags"), list) else None,
                "cost_s": finite(res.get("cost_s")),
                "best_terminal": ({"candidate_id": bt.get("cid"), "val_map50_95": finite(bt.get("val_map50_95")),
                                   "small_recall": finite(bt.get("small_recall")), "latency_v2": finite(bt.get("latency_v2")),
                                   "params_deploy": finite(bt.get("params_deploy"))} if bt else None)}
        # best: the search-tier best of the candidate rows (base); objective_champions.json fills what the rows could not give
        champs = self.json("search/pareto/objective_champions.json")
        if isinstance(champs, list):
            by_obj = {c.get("objective"): finite(c.get("value")) for c in champs if isinstance(c, dict)}
            best = dict(base.get("best") or {})
            fill = {"map50_95": by_obj.get("map50_95"), "small_target_recall": by_obj.get("small_target_recall"),
                    "latency_min_ms": by_obj.get("latency_median_ms")}
            changed = False
            for key, val in fill.items():
                if best.get(key) is None and val is not None:
                    best[key] = val
                    changed = True
            if changed:
                out["best"] = best
        return out

"""Archive roots -> runs -> parsed run data, with mtime-keyed caching (design D3/D4).

* A run is a direct sub-directory of a configured root whose name is a valid id and which contains one of the run
  markers (``search``, ``run.log``, ``final_summary.json``, ``protocol``). Other directories (e.g. ``*_cpugate_*`` with only
  ``g3``) are not runs.
* Every file is reached as ``<run real dir>/<fixed relative name>``; a resolved path outside the run's real directory
  (symlink escape) is treated as missing and reported in ``warnings``.
* :class:`RunData` is rebuilt only when the signature (mtime_ns + size of the key files and the generation summaries) changes;
  the signature is re-checked at most once per ``ttl_s`` (default 5 s).
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from mbnas_server.archive import analysis as an
from mbnas_server.archive.live import pid_alive
from mbnas_server.archive.parsers import (finite, iso_from_epoch, notebook_span, notebook_stages, read_csv_rows, read_json, read_jsonl, tail_lines)

MARKERS = ("search", "run.log", "final_summary.json", "protocol")
KEY_FILES = (
    "search/candidates/candidate_evaluations.jsonl", "search/archive.json", "final_summary.json", "checklist.json",
    "protocol/baselines_v2.json", "run.log", "driver/run.pids.json", "driver/stop_rule.json", "driver/transition.json",
    "driver/orphan_check.json", "driver/watchdog.log", "search/relative_to_anchor.json", "search/exclusive_latency.json",
    "three_seed/three_seed_summary.json", "smoke_verdict.json", "budget.json", "preset.json", "protocol_manifest.json",
    "search/accuracy_floor.json", "fidelity/rank_correlation.json", "fidelity/noise.json", "search/gpu_guard.log",
    "gpu_guard.log", "search/pareto/pareto_archive.csv", "three_seed/final_report.md", "arm.json",
)
GEN_DIR = "search/generations"
CFG_DIR = "search/candidates/train_runs"
GEN_RE = re.compile(r"^gen_(\d{3,})_summary\.json$")
NB_EXIT_RE = re.compile(r"NBCONVERT_EXIT=(-?\d+)")
MAX_CFG_CACHE = 8192


class ArchiveError(Exception):
    """Raised for client-visible archive failures; the route layer maps it to an ``Error`` body."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass(frozen=True)
class RootInfo:
    path: str
    real: str
    family: str
    exists: bool
    kind: str = "runs"  # "campaign" for MBNAS_GW_ARCHIVE_CAMPAIGN_ROOTS entries (archive/campaign.py)


@dataclass(frozen=True)
class RunRef:
    id: str
    family: str
    root: str
    dir: str
    real: str
    campaign: Any = None  # campaign.ArmRef for a 12-search campaign arm (served by campaign.CampaignRunData), else None


def within(real: str, base: str) -> bool:
    return real == base or real.startswith(base.rstrip(os.sep) + os.sep)


def family_of(root: str) -> str:
    name = os.path.basename(root.rstrip(os.sep))
    return name[len("yolo_ga_full_"):] if name.startswith("yolo_ga_full_") and len(name) > len("yolo_ga_full_") else name


def _stat_sig(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class ArchiveStore:
    """Thread-safe cache of discovered runs and parsed :class:`RunData` (one instance per app)."""

    def __init__(self, roots: list[str], driver_dirs: list[str] | None = None, ttl_s: float = 5.0,
                 clock: Callable[[], float] = time.monotonic, max_runs: int = 16,
                 campaign_roots: list[str] | None = None) -> None:
        self.root_paths = [r for r in roots]
        self.campaign_roots = list(campaign_roots or [])
        self.driver_dirs = list(driver_dirs or [])
        self.ttl_s = ttl_s
        self.clock = clock
        self.max_runs = max_runs
        self._lock = threading.RLock()
        self._disc: tuple[float, list[RootInfo], list[RunRef], list[str]] | None = None
        self._runs: "OrderedDict[str, RunData]" = OrderedDict()
        self._run_locks: dict[str, threading.Lock] = {}
        self._cfg: "OrderedDict[str, tuple[tuple[int, int], Any]]" = OrderedDict()
        self._nb: dict[str, tuple[tuple[int, int], Any]] = {}
        self.memo: dict[str, tuple[float, Any]] = {}
        self.stats = {"discover": 0, "run_builds": 0, "cfg_parses": 0}

    # ------------------------------------------------------------------ discovery
    def discover(self) -> tuple[list[RootInfo], list[RunRef], list[str]]:
        with self._lock:
            now = self.clock()
            if self._disc is not None and now - self._disc[0] < self.ttl_s:
                return self._disc[1], self._disc[2], self._disc[3]
            self.stats["discover"] += 1
            roots: list[RootInfo] = []
            refs: dict[str, RunRef] = {}
            warnings: list[str] = []
            for path in self.root_paths:
                real = os.path.realpath(path)
                exists = os.path.isdir(real)
                roots.append(RootInfo(path=path, real=real, family=family_of(path), exists=exists))
                if not exists:
                    warnings.append(f"archive root not found: {path}")
                    continue
                try:
                    names = sorted(os.listdir(real))
                except OSError as exc:
                    warnings.append(f"archive root unreadable: {path} ({type(exc).__name__})")
                    continue
                for name in names:
                    if not an.valid_id(name):
                        continue
                    d = os.path.join(real, name)
                    if not os.path.isdir(d):
                        continue
                    r = os.path.realpath(d)
                    if not within(r, real) or r == real:
                        warnings.append(f"run directory {name} escapes its root via a symlink: skipped")
                        continue
                    if not any(os.path.exists(os.path.join(r, m)) for m in MARKERS):
                        continue
                    if name in refs:
                        warnings.append(f"run id {name} exists in more than one root: using {refs[name].root}")
                        continue
                    refs[name] = RunRef(id=name, family=family_of(path), root=path, dir=d, real=r)
            if self.campaign_roots:  # read-only 12-search campaigns: every arm directory is one run (archive/campaign.py)
                from mbnas_server.archive import campaign as cp
                c_roots, c_refs, c_warn = cp.discover(self, self.campaign_roots)
                roots.extend(c_roots)
                warnings.extend(c_warn)
                for ref in c_refs:
                    if ref.id in refs:
                        warnings.append(f"run id {ref.id} exists in more than one root: using {refs[ref.id].root}")
                        continue
                    refs[ref.id] = ref
            self._disc = (now, roots, list(refs.values()), warnings)
            return roots, list(refs.values()), warnings

    def has_available_root(self) -> bool:
        return any(r.exists for r in self.discover()[0])

    def ref(self, run_id: str) -> RunRef:
        if not an.valid_id(run_id):
            raise ArchiveError(422, "ARCHIVE_BAD_ID", "run id must match [A-Za-z0-9._-]{1,128} and not start with '.'")
        roots, refs, _ = self.discover()
        for r in refs:
            if r.id == run_id:
                return r
        if not any(r.exists for r in roots):
            raise ArchiveError(503, "ARCHIVE_ROOT_UNAVAILABLE", "no configured archive root is available")
        raise ArchiveError(404, "ARCHIVE_RUN_NOT_FOUND", f"run {run_id} not found")

    # ------------------------------------------------------------------ run data
    def run(self, run_id: str) -> "RunData":
        ref = self.ref(run_id)
        with self._lock:
            lock = self._run_locks.setdefault(run_id, threading.Lock())
        with lock:
            now = self.clock()
            with self._lock:
                rd = self._runs.get(run_id)
            if rd is not None and rd.ref.real == ref.real:
                if now - rd.checked < self.ttl_s:
                    return rd
                sig = rd.compute_signature()
                if sig == rd.sig:
                    rd.checked = now
                    rd.refresh_volatile()
                    return rd
            self.stats["run_builds"] += 1
            if ref.campaign is not None:
                from mbnas_server.archive.campaign import CampaignRunData
                rd = CampaignRunData(self, ref)
            else:
                rd = RunData(self, ref)
            rd.sig = rd.compute_signature()
            rd.checked = now
            with self._lock:
                self._runs[run_id] = rd
                self._runs.move_to_end(run_id)
                while len(self._runs) > self.max_runs:
                    self._runs.popitem(last=False)
            return rd

    def ttl_memo(self, key: str, build: Callable[[], Any]) -> Any:
        """Store-level memo for multi-run responses (list / compare): valid for ``ttl_s``."""
        with self._lock:
            hit = self.memo.get(key)
            now = self.clock()
            if hit is not None and now - hit[0] < self.ttl_s:
                return hit[1]
        value = build()
        with self._lock:
            self.memo[key] = (self.clock(), value)
            if len(self.memo) > 64:
                self.memo.pop(next(iter(self.memo)))
        return value

    # ------------------------------------------------------------------ small per-file caches (keyed by stat)
    def cfg_cached(self, real_path: str) -> Any:
        sig = _stat_sig(real_path)
        if sig is None:
            return None
        with self._lock:
            hit = self._cfg.get(real_path)
            if hit is not None and hit[0] == sig:
                self._cfg.move_to_end(real_path)
                return hit[1]
        obj, err = read_json(real_path)
        self.stats["cfg_parses"] += 1
        value = obj if err is None and isinstance(obj, dict) else None
        with self._lock:
            self._cfg[real_path] = (sig, value)
            while len(self._cfg) > MAX_CFG_CACHE:
                self._cfg.popitem(last=False)
        return value

    def notebook_cached(self, real_path: str) -> dict[str, Any] | None:
        sig = _stat_sig(real_path)
        if sig is None:
            return None
        with self._lock:
            hit = self._nb.get(real_path)
            if hit is not None and hit[0] == sig:
                return hit[1]
        nb, err = read_json(real_path)
        value = None if err else {"stages": notebook_stages(nb), "span": notebook_span(nb)}
        with self._lock:
            self._nb[real_path] = (sig, value)
            if len(self._nb) > 32:
                self._nb.pop(next(iter(self._nb)))
        return value


class RunData:
    """Parsed view of one run directory (immutable after the signature check; computed pieces are memoised)."""

    def __init__(self, store: ArchiveStore, ref: RunRef) -> None:
        self.store, self.ref = store, ref
        self.id, self.family = ref.id, ref.family
        self.sig: Any = None
        self.checked = 0.0
        self.memo: dict[str, Any] = {}
        self.view_cache: dict[str, Any] = {}
        self._lock = threading.RLock()
        self.warnings: list[str] = []

    def summary_extras(self, base: dict[str, Any]) -> dict[str, Any]:
        """Additive keys merged into :func:`views.run_summary` (campaign arms add grouping fields; plain runs add none)."""
        return {}

    def refresh_volatile(self) -> None:
        """Drop what depends on process liveness (not on file contents): status, pid states and the ``vol:`` views."""
        with self._lock:
            for k in ("status", "pid_states"):
                self.memo.pop(k, None)
            for k in [k for k in self.view_cache if k.startswith("vol:")]:
                del self.view_cache[k]

    # ------------------------------------------------------------------ paths
    def path(self, rel: str) -> str | None:
        """Real path of ``<run dir>/<rel>`` when it exists inside the run's real directory, else None."""
        full = os.path.join(self.ref.real, rel)
        real = os.path.realpath(full)
        if not within(real, self.ref.real):
            self._warn(f"{rel}: symlink leaves the run directory, ignored")
            return None
        return real if os.path.exists(real) else None

    def exists(self, rel: str) -> bool:
        return self.path(rel) is not None

    def _warn(self, msg: str) -> None:
        with self._lock:
            if msg not in self.warnings:
                self.warnings.append(msg)

    def once(self, key: str, fn: Callable[[], Any]) -> Any:
        with self._lock:
            if key not in self.memo:
                self.memo[key] = fn()
            return self.memo[key]

    def cached_view(self, key: str, fn: Callable[[], Any]) -> Any:
        with self._lock:
            if key not in self.view_cache:
                self.view_cache[key] = fn()
            return self.view_cache[key]

    def sources(self, *rels: str) -> list[str]:
        """The relative names among ``rels`` that exist (for the response ``source.files``)."""
        return [r for r in rels if self.exists(r)]

    # ------------------------------------------------------------------ signature
    def compute_signature(self) -> tuple[Any, ...]:
        base = self.ref.real
        sig: list[Any] = [(rel, _stat_sig(os.path.join(base, rel))) for rel in KEY_FILES]
        gdir = os.path.join(base, GEN_DIR)
        try:
            with os.scandir(gdir) as it:
                sig.append(tuple(sorted((e.name, _stat_sig(e.path)) for e in it if GEN_RE.match(e.name))))
        except OSError:
            sig.append(None)
        sig.append(_stat_sig(os.path.join(base, CFG_DIR)))
        sig.append(_stat_sig(os.path.join(base, f"{self.id}.executed.ipynb")))
        return tuple(sig)

    # ------------------------------------------------------------------ generic loaders
    def json(self, rel: str) -> Any:
        def load() -> Any:
            p = self.path(rel)
            if p is None:
                return None
            obj, err = read_json(p)
            if err:
                self._warn(f"{rel}: {err}")
                return None
            return obj
        return self.once("json:" + rel, load)

    def jdict(self, rel: str) -> dict[str, Any] | None:
        v = self.json(rel)
        return v if isinstance(v, dict) else None

    # ------------------------------------------------------------------ candidates
    @property
    def cands(self) -> list[dict[str, Any]]:
        return self.once("cands", self._load_cands)

    def _load_cands(self) -> list[dict[str, Any]]:
        p = self.path("search/candidates/candidate_evaluations.jsonl")
        if p is None:
            self.memo["jsonl_bad"] = 0
            return []
        raw, bad, err = read_jsonl(p)
        if err:
            self._warn(f"candidate_evaluations.jsonl: {err}")
        rows, warns = an.normalize_rows(raw)
        for w in warns:
            self._warn(w)
        if bad:
            self._warn(f"candidate_evaluations.jsonl: {bad} unparsable line(s) skipped (file is rewritten in place by the runtime; retry on next poll)")
        self.memo["jsonl_bad"] = bad
        tiers = {c["tier"] for c in rows if an.is_ok(c) and c["tier"]}
        if len(tiers) > 1:
            self._warn(f"multiple fidelity tiers among ok rows ({', '.join(sorted(tiers))}); fronts and bests pool all of them")
        return rows

    @property
    def bad_lines(self) -> int:
        self.cands  # noqa: B018 - populates memo
        return int(self.memo.get("jsonl_bad", 0))

    def by_id(self) -> dict[str, list[dict[str, Any]]]:
        def build() -> dict[str, list[dict[str, Any]]]:
            out: dict[str, list[dict[str, Any]]] = {}
            for c in self.cands:
                out.setdefault(c["candidate_id"], []).append(c)
            return out
        return self.once("by_id", build)

    def latest(self, cid: str) -> dict[str, Any] | None:
        rows = self.by_id().get(cid)
        return rows[-1] if rows else None

    # ------------------------------------------------------------------ generations
    def summaries(self) -> dict[int, dict[str, Any]]:
        def build() -> dict[int, dict[str, Any]]:
            out: dict[int, dict[str, Any]] = {}
            gdir = self.path(GEN_DIR)
            if gdir is None or not os.path.isdir(gdir):
                return out
            for name in sorted(os.listdir(gdir)):
                m = GEN_RE.match(name)
                if not m:
                    continue
                obj = self.jdict(f"{GEN_DIR}/{name}")
                if obj is not None:
                    g = obj.get("generation")
                    out[g if isinstance(g, int) and not isinstance(g, bool) else int(m.group(1))] = obj
            return out
        return self.once("summaries", build)

    def generation_numbers(self) -> list[int]:
        return sorted(set(self.summaries()) | {c["generation"] for c in self.cands})

    def fronts(self) -> dict[str, dict[int, list[int]]]:
        return self.once("fronts", lambda: an.front_series(self.cands, self.generation_numbers()))

    def final_front_ids(self, kind: str) -> list[str]:
        gens = self.generation_numbers()
        if not gens:
            return []
        return an.ids_of(self.cands, self.fronts()[kind][gens[-1]])

    def front_flags(self) -> tuple[set[int], set[int]]:
        gens = self.generation_numbers()
        if not gens:
            return set(), set()
        f = self.fronts()
        return set(f["2d"][gens[-1]]), set(f["4d"][gens[-1]])

    def new_fp(self) -> dict[int, tuple[int, list[str]]]:
        return self.once("new_fp", lambda: an.new_fingerprints(self.cands))

    # ------------------------------------------------------------------ cfg / yaml
    def cfg(self, cid: str) -> dict[str, Any] | None:
        """Parsed ``train_runs/<cid>.cfg.json`` of exactly this candidate (None when absent / unreadable)."""
        if not an.valid_id(cid):
            return None
        p = self.path(f"{CFG_DIR}/{cid}.cfg.json")
        return self.store.cfg_cached(p) if p else None

    def cfg_or_twin(self, cid: str) -> tuple[dict[str, Any] | None, str | None]:
        """(cfg, source candidate id). When the candidate has no cfg file (cache-hit rows reuse a trained twin) the cfg of
        a candidate with the same architecture fingerprint is used and ``source`` names it; otherwise ``source == cid``."""
        cfg = self.cfg(cid)
        if cfg is not None:
            return cfg, cid
        row = self.latest(cid)
        if row is None:
            return None, None
        hit = row["notes"]["cache_hit"]
        order = ([hit] if hit and an.valid_id(hit) else []) + [c["candidate_id"] for c in self.cands
                                                                if c["fingerprint"] and c["fingerprint"] == row["fingerprint"]]
        for other in order:
            if other != cid:
                cfg = self.cfg(other)
                if cfg is not None:
                    return cfg, other
        return None, None

    # ------------------------------------------------------------------ run level facts
    def nb_exit(self) -> int | None:
        def load() -> int | None:
            p = self.path("run.log")
            if p is None:
                return None
            lines, err = tail_lines(p, 4000, max_chars=2000)  # the marker is written near the end; logs can be large
            if err:
                if err != "missing":
                    self._warn(f"run.log: {err}")
                return None
            found = NB_EXIT_RE.findall("\n".join(lines))
            return int(found[-1]) if found else None
        return self.once("nb_exit", load)

    def pids(self) -> dict[str, Any] | None:
        return self.jdict("driver/run.pids.json")

    def pid_states(self) -> dict[str, bool]:
        """Liveness of the pids named in ``driver/run.pids.json`` (``nb_pid``, ``kernel_pid``, ``confirm_pid``)."""
        def build() -> dict[str, bool]:
            d = self.pids() or {}
            started = finite(d.get("launcher_start_epoch"))
            written = None
            if isinstance(d.get("updated"), str):
                try:
                    written = time.mktime(time.strptime(d["updated"][:19], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    written = None
            return {k: pid_alive(d.get(k), started, written) for k in ("nb_pid", "kernel_pid", "confirm_pid")
                    if k in d}
        return self.once("pid_states", build)

    def status(self) -> tuple[str, str]:
        """(running|done|failed|unknown, detail) by the design rules (section 3.1), first match wins."""
        def build() -> tuple[str, str]:
            stop = self.jdict("driver/stop_rule.json")
            if self.exists("driver/stop_rule.json"):
                rule = None
                if stop:
                    for k in ("rule", "reason", "stop_rule", "name"):
                        if isinstance(stop.get(k), str):
                            rule = stop[k]
                            break
                return "failed", f"stop_rule: {rule}" if rule else "stop_rule.json present"
            code = self.nb_exit()
            fs = self.jdict("final_summary.json")
            if code == 0 and fs is not None:
                detail = f"NBCONVERT_EXIT=0; notebook verdict {fs.get('verdict')}"
                if self.pid_states().get("confirm_pid"):
                    detail += "; confirm stage still running"
                return "done", detail
            if code is not None and code != 0:
                return "failed", f"NBCONVERT_EXIT={code}"
            ps = self.pid_states()
            if ps.get("nb_pid"):
                stage = (self.pids() or {}).get("stage")
                return "running", f"nb_pid alive" + (f"; stage {stage}" if stage else "")
            if self.id.startswith("FAILED_"):
                return "failed", "directory name starts with FAILED_"
            if code == 0:
                return "unknown", "NBCONVERT_EXIT=0 but final_summary.json is missing or unreadable"
            return "unknown", "no stop rule, no NBCONVERT_EXIT, nb_pid not alive"
        return self.once("status", build)

    # ------------------------------------------------------------------ notebook timing
    def notebook(self) -> dict[str, Any] | None:
        p = self.path(f"{self.id}.executed.ipynb")
        return self.store.notebook_cached(p) if p else None

    def started_ended(self) -> tuple[str | None, str | None]:
        nb = self.notebook()
        started = ended = None
        if nb:
            started, ended = nb["span"]
        if started is None:
            ep = finite((self.pids() or {}).get("launcher_start_epoch"))
            started = iso_from_epoch(ep) if ep is not None else None
        status = self.status()[0]
        if ended is None and status == "done":
            fp = self.path("final_summary.json")
            if fp:
                try:
                    ended = iso_from_epoch(os.stat(fp).st_mtime)
                except OSError:
                    ended = None
        return started, ended

    # ------------------------------------------------------------------ baselines / presets
    def baselines(self) -> tuple[dict[str, dict[str, Any]], str | None]:
        """({name: entry} from protocol/baselines_v2.json, else final_summary.baselines_v2; source rel path)."""
        d = self.jdict("protocol/baselines_v2.json")
        if d:
            return {k: v for k, v in d.items() if isinstance(v, dict)}, "protocol/baselines_v2.json"
        fs = self.jdict("final_summary.json")
        if fs and isinstance(fs.get("baselines_v2"), dict):
            return {k: v for k, v in fs["baselines_v2"].items() if isinstance(v, dict)}, "final_summary.json"
        return {}, None

    def judge_name(self) -> str | None:
        b, _ = self.baselines()
        if "baseline_yolo26n" in b:
            return "baseline_yolo26n"
        return next(iter(b), None)

    def preset_doc(self) -> tuple[dict[str, Any] | None, str | None]:
        for rel in ("preset.json", "protocol_manifest.json"):
            d = self.jdict(rel)
            if d is not None:
                return d, rel
        return None, None

    def preset(self) -> dict[str, Any]:
        d, _ = self.preset_doc()
        p = (d or {}).get("preset")
        return p if isinstance(p, dict) else {}

    def final_epochs(self) -> int | None:
        v = self.preset().get("final_epochs")
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        ts = self.jdict("three_seed/three_seed_summary.json")
        ep = ((ts or {}).get("protocol") or {}).get("epochs")
        return ep if isinstance(ep, int) and not isinstance(ep, bool) else None

    def final_tier_label(self) -> str:
        e = self.final_epochs()
        return f"final@{e}e" if e is not None else "final"

    def dataset_id(self) -> str | None:
        p = self.preset().get("dataset_id") or (self.jdict("preset.json") or {}).get("dataset_id")
        if isinstance(p, str) and p:
            return p
        ds = (self.jdict("final_summary.json") or {}).get("dataset")
        if isinstance(ds, dict):
            if isinstance(ds.get("dataset_id"), str):
                return ds["dataset_id"]
            if "nps_root" in ds:
                return "nps_drones"  # derived: the NPS runs record only nps_root / aot_root
        return None

    # ------------------------------------------------------------------ logs
    def gpu_guard_records(self) -> list[dict[str, Any]]:
        def build() -> list[dict[str, Any]]:
            out, seen = [], set()
            for rel in ("gpu_guard.log", "search/gpu_guard.log"):
                p = self.path(rel)
                if p is None:
                    continue
                rows, _bad, _err = read_jsonl(p)
                for r in rows:
                    key = (r.get("timestamp"), r.get("gpu"), r.get("action"), r.get("waited_s"))
                    if "action" in r and key not in seen:
                        seen.add(key)
                        out.append(r)
            return out
        return self.once("gpu_guard", build)

    def watchdog_tail(self, n: int = 40) -> list[str]:
        p = self.path("driver/watchdog.log")
        return tail_lines(p, n, max_chars=400)[0] if p else []

    def pareto_csv_rows(self) -> list[dict[str, str]]:
        p = self.path("search/pareto/pareto_archive.csv")
        return read_csv_rows(p)[0] if p else []

"""ONE shared operator space over ultralytics YAML genotypes, used identically by the GA and IndependentRandom arms.

Contract:

* **Seed templates** — for each family (default ``yolo11n``, ``yolo26n``) the installed ultralytics YAML (file path and
  SHA-256 recorded) with ``nc`` set to the dataset class count, plus the ETRI mixed seed variants ``native`` /
  ``cnn_backbone`` / ``transformer_backbone`` produced by ``mixed_search_space.make_mixed_seed_variants`` through
  :mod:`~mbnas_core.search.mixed_pool.bridge`. If the ETRI pool cannot be registered on the installed ultralytics,
  ``create`` raises :class:`~mbnas_core.search.mixed_pool.bridge.SearchSpaceUnavailable` (SEARCH_SPACE_UNAVAILABLE)
  unless the caller opts in with ``allow_native_fallback=True`` (protocol ``console.search.allow_native_fallback``)
  or disables the pool explicitly (``use_etri=False``); only then does the space fall back to native operators
  (``variant="native_fallback"``, reason recorded; no convolution-free NAS operator port in that mode).
* **Hash definition** (:data:`SPACE_HASH_DEFINITION`, ``mbnas-mixed-space/3``): ``space_hash``/``prior_hash`` are the
  canonical SHA-256 of the ``describe()`` body; the mixed-pool identity inside it is the content SHA-256 of the
  vendored mixed-pool modules executed for seed variants and parser registration (``mixed_pool_files_sha256``, read
  from the installed files at load time). No git HEAD is hashed; provenance fields (``vendor_origin``) are recorded
  only. History: ``mbnas-mixed-space/2`` hashed the content of every executed ETRI ``yolo_ga_runtime`` file of an
  external checkout (``etri_files_sha256``), and ``mbnas-mixed-space/1`` (candidates stored before that, e.g.
  space_hash ``91c2e66d3c88...``) hashed ``etri_git_head`` plus only ``mixed_blocks.py``/``mixed_search_space.py``;
  stored rows of either version are not rewritten.
* **Slots** come from the family's native template: backbone stride-2 ``Conv`` rows are ``down`` slots
  (``Conv`` | ``NASPatchMerge``); repeat-module rows and the ``SPPF`` row are ``feature`` slots (C2f, C3k2, C3TR,
  C2PSA, A2C2f, NASWindowTransformer, NASGlobalTransformer; SPPF only at its own row) with declared depth
  (YAML repeats) and width (multiplier of the native ``c2``) choices. Concat/Upsample/head-Conv/Detect rows are fixed.
* **Legal-shape rules** (ported from ETRI ``mixed_search_space._legal_choices``) are evaluated on effective channels
  and the slot's input spatial size at the protocol ``imgsz``; :func:`illegal_reason` returns the reason or None.
* **Frozen prior** (identical for both arms, never conditioned on fitness): template ~ uniform over seed templates;
  number of slot draws ~ uniform over :data:`PRIOR_NUM_DRAWS`; slots uniform without replacement; per slot an
  operator uniform over legal alternatives, then (depth, width) uniform over that operator's legal combinations.
* **Repair**: every child is built on CPU and checked against the rules; on failure the offending row (or, when that
  row already equals the reference, the nearest changed upstream row) is reverted to the reference row and a repair
  event ``{row, reason, before, after, reverted_to}`` is recorded. Reverting everything yields the valid reference.
"""
from __future__ import annotations

import copy
import math
import random as _random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mbnas_model_runtime import genotype_audit as audit_mod
from mbnas_core.search.mixed_pool.bridge import EtriMixed, EtriUnavailable, SearchSpaceUnavailable, load_etri_mixed
from mbnas_model_runtime.genotype import BuildReport, Genotype, inspect_genotype
from mbnas_contracts.util.canon import canonical_sha256, sha256_hex

SPACE_VERSION = "mbnas-mixed-space/3"
SPACE_HASH_DEFINITION = (
    "mbnas-mixed-space/3: canonical sha256 of describe() body; mixed-pool identity = content sha256 of the "
    "vendored mixed-pool modules executed for seed variants and parser registration (mixed_pool_files_sha256: "
    "mbnas_model_runtime/operators/etri_mixed_blocks.py, mbnas_core/search/mixed_pool/seed_variants.py, "
    "mbnas_core/search/mixed_pool/runtime_constants.py), read from the installed files at load time. "
    "Supersedes mbnas-mixed-space/2 (content sha256 of every executed yolo_ga_runtime module of an external "
    "ETRI_NAS checkout); the /2 inputs and their sha256 are recorded as vendor_origin (provenance, not hashed).")
RULES_VERSION = "mbnas-legal-shape/1 (ETRI mixed_search_space._legal_choices port)"
FEATURE_OPS: tuple[str, ...] = ("C2f", "C3k2", "C3TR", "C2PSA", "A2C2f", "NASWindowTransformer", "NASGlobalTransformer")
NATIVE_FEATURE_OPS: tuple[str, ...] = ("C2f", "C3k2", "C3TR", "C2PSA", "A2C2f")
DOWN_OPS: tuple[str, ...] = ("Conv", "NASPatchMerge")
NON_REPEAT_OPS = frozenset({"SPPF", "Conv", "NASPatchMerge"})
SLOT_NATIVE_FEATURE = frozenset({"C2f", "C3k2", "C3TR", "C2PSA", "A2C2f", "C3", "NASWindowTransformer",
                                 "NASGlobalTransformer"})
DEPTH_CHOICES: tuple[int, ...] = (1, 2, 3)
WIDTH_CHOICES: tuple[float, ...] = (0.75, 1.0, 1.25)
TOKEN_BUDGET = 1024
PRIOR_NUM_DRAWS: tuple[int, ...] = (0, 1, 2, 3)
DEFAULT_FAMILIES: tuple[str, ...] = ("yolo11n", "yolo26n")
BASE_YAML_REL = {"yolo11": "cfg/models/11/yolo11.yaml", "yolo26": "cfg/models/26/yolo26.yaml"}
ETRI_VARIANTS: tuple[str, ...] = ("native", "cnn_backbone", "transformer_backbone")
FAMILY_RE = re.compile(r"^(yolo11|yolo26)([nslmx])$")


class RepairFailed(RuntimeError):
    """No sequence of reverts produced a buildable, rule-conforming genotype (reference itself invalid)."""


@dataclass(frozen=True)
class Choice:
    """One gene value for a slot: operator, YAML repeats (pre depth-scaling) and width multiplier of native c2."""

    op: str
    repeats: int = 1
    width: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {"op": self.op, "repeats": self.repeats, "width": self.width}


@dataclass(frozen=True)
class Slot:
    """A searchable row position of a family (identical for every template of that family)."""

    row: int
    scope: str
    kind: str
    ops: tuple[str, ...]
    native_row: tuple[Any, ...] = field(compare=False)

    def choices(self) -> list[Choice]:
        """Declared (op, repeats, width) grid of this slot before legality filtering."""
        out = []
        for op in self.ops:
            depths = (1,) if op in NON_REPEAT_OPS else DEPTH_CHOICES
            widths = (1.0,) if self.kind == "down" else WIDTH_CHOICES
            out.extend(Choice(op, d, w) for d in depths for w in widths)
        return out

    def render(self, choice: Choice) -> list[Any]:
        """YAML row for ``choice``; the native operator keeps its native extra args (exact native row at width 1)."""
        frm, _, nat_op, nat_args = self.native_row
        c2 = int(round(nat_args[0] * choice.width))
        if self.kind == "down":
            args = [c2, *nat_args[1:]] if choice.op == nat_op else ([c2, 3, 2] if choice.op == "Conv" else [c2, 2])
            return [copy.deepcopy(frm), 1, choice.op, args]
        args = [c2, *copy.deepcopy(nat_args[1:])] if choice.op == nat_op else [c2]
        return [copy.deepcopy(frm), 1 if choice.op in NON_REPEAT_OPS else choice.repeats, choice.op, args]

    def gene_of(self, row: list[Any]) -> Choice | None:
        """Inverse of :meth:`render`; None when the row is not a rendering of a declared choice."""
        op, rep, args = str(row[2]), row[1], row[3]
        if op not in self.ops or not args or not isinstance(args[0], (int, float)):
            return None
        width = args[0] / self.native_row[3][0]
        match = next((w for w in WIDTH_CHOICES if abs(w - width) < 1e-9), None)
        if match is None:
            return None
        choice = Choice(op, 1 if op in NON_REPEAT_OPS else rep, match)
        return choice if self.render(choice) == list(row) else None


@dataclass(frozen=True)
class Template:
    """A seed template (``<family>:<variant>``) with its genotype and source provenance."""

    id: str
    family: str
    variant: str
    genotype: Genotype
    source: dict[str, Any]


@dataclass
class RowOrigin:
    """Provenance of one row of a proposal (becomes one contract ``Block``)."""

    kind: str
    source: str
    operation: str
    before: list[Any] | None = None
    after: list[Any] | None = None
    reason: str | None = None


@dataclass
class Proposal:
    """A built, rule-conforming child with full lineage (consumed by lineage.py / handlers.py)."""

    method: str
    operation: str
    genotype: Genotype
    report: BuildReport
    origins: list[RowOrigin]
    parents: list[Any] = field(default_factory=list)
    elite: bool = False
    crossover: dict[str, Any] | None = None
    mutation_events: list[dict[str, Any]] = field(default_factory=list)
    repair_events: list[dict[str, Any]] = field(default_factory=list)
    prior_draw: dict[str, Any] | None = None
    selection: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)


def effective_channels(c2_yaml: float, scale: list[float]) -> int:
    """ultralytics width scaling: ``make_divisible(min(c2, max_channels) * width, 8)``."""
    _, width, max_ch = scale
    return int(math.ceil(min(c2_yaml, max_ch) * width / 8) * 8)


def illegal_reason(choice: Choice, *, kind: str, c1: int | None, c2: int, h: int | None, w: int | None) -> str | None:
    """Legal-shape rule check for one slot choice; returns the violated rule or None."""
    if kind == "down":
        return None if choice.repeats == 1 else "down operators do not repeat"
    if choice.op in NON_REPEAT_OPS and choice.repeats != 1:
        return f"{choice.op} does not repeat"
    if c1 is None or h is None or w is None:
        return "input shape unknown"
    tokens, hidden = h * w, int(c2 * 0.5)
    if choice.op == "C2PSA":
        heads = max(hidden // 64, 1)
        if c1 != c2:
            return f"C2PSA requires c1 == c2 (c1={c1}, c2={c2})"
        if c2 < 128:
            return f"C2PSA requires c2 >= 128 (c2={c2})"
        if hidden % heads:
            return f"C2PSA hidden {hidden} not divisible by heads {heads}"
    if choice.op == "C3TR" and hidden % 4:
        return f"C3TR hidden {hidden} not divisible by 4 heads"
    if choice.op == "A2C2f" and (c2 < 64 or hidden % 32):
        return f"A2C2f requires c2 >= 64 and hidden % 32 == 0 (c2={c2})"
    if choice.op in {"C2PSA", "C3TR", "A2C2f", "NASGlobalTransformer"} and tokens > TOKEN_BUDGET:
        return f"{choice.op} token budget exceeded ({h}x{w}={tokens} > {TOKEN_BUDGET})"
    return None


def _ultralytics_root() -> Path:
    import ultralytics

    return Path(ultralytics.__file__).resolve().parent


def load_base_yaml(family: str, nc: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Installed ultralytics YAML for ``family`` (e.g. yolo11n) with ``nc``/``scale`` set, plus its provenance."""
    m = FAMILY_RE.match(family)
    if not m:
        raise ValueError(f"unsupported template family {family!r}; expected yolo11<scale> or yolo26<scale>")
    import ultralytics

    path = _ultralytics_root() / BASE_YAML_REL[m.group(1)]
    raw = path.read_bytes()
    data = yaml.safe_load(raw.decode("utf-8"))
    data["nc"], data["scale"] = int(nc), m.group(2)
    return data, {"kind": "ultralytics_cfg", "path": str(path), "sha256": sha256_hex(raw), "scale": m.group(2),
                  "ultralytics_version": ultralytics.__version__}


def family_slots(native: Genotype, *, ops_feature: tuple[str, ...], ops_down: tuple[str, ...]) -> list[Slot]:
    """Slots of a family derived from its native template rows."""
    slots = []
    for i, row in enumerate(native.rows()):
        name, scope = str(row[2]).split(".")[-1], ("backbone" if i < native.n_backbone else "head")
        args = row[3]
        if scope == "backbone" and name == "Conv" and row[1] == 1 and len(args) > 2 and args[2] == 2:
            slots.append(Slot(i, scope, "down", ops_down, tuple(row)))
        elif name in SLOT_NATIVE_FEATURE:
            slots.append(Slot(i, scope, "feature", ops_feature, tuple(row)))
        elif name == "SPPF":
            slots.append(Slot(i, scope, "feature", ops_feature + ("SPPF",), tuple(row)))
    return slots


def _native_cnn_variant(base: dict[str, Any]) -> dict[str, Any]:
    """Fallback-mode port of the ETRI cnn_backbone variant: attention backbone rows -> C2f [c2]."""
    cnn = copy.deepcopy(base)
    for row in cnn["backbone"]:
        if audit_mod.row_has_attention(row):
            row[2], row[3] = "C2f", [row[3][0]]
    return cnn


class SearchSpace:
    """The shared space; construct with :meth:`create`. Instances cache CPU build reports by YAML hash."""

    def __init__(self, *, nc: int, imgsz: int, variant: str, ops_feature: tuple[str, ...], ops_down: tuple[str, ...],
                 etri: EtriMixed | None, fallback_reason: str | None, allow_native_fallback: bool = False) -> None:
        self.nc, self.imgsz, self.variant = nc, imgsz, variant
        self.allow_native_fallback = allow_native_fallback
        self.ops_feature, self.ops_down = ops_feature, ops_down
        self.etri, self.fallback_reason = etri, fallback_reason
        self.templates: dict[str, Template] = {}
        self.rejected_templates: list[dict[str, Any]] = []
        self.slots: dict[str, list[Slot]] = {}
        self.native_head: dict[str, list[list[Any]]] = {}
        self.native_reports: dict[str, BuildReport] = {}
        self.prior: dict[str, dict[int, dict[str, list[Choice]]]] = {}
        self._cache: dict[str, BuildReport] = {}

    # -- construction -------------------------------------------------------------------------------------------
    @classmethod
    def create(cls, *, nc: int, imgsz: int = 640, families: tuple[str, ...] = DEFAULT_FAMILIES,
               etri_root: str | None = None, use_etri: bool = True,
               allow_native_fallback: bool = False) -> "SearchSpace":
        """Load templates, register the ETRI pool, build every template and freeze the prior.

        An unloadable ETRI pool raises :class:`SearchSpaceUnavailable` unless ``allow_native_fallback`` (the
        protocol's ``console.search.allow_native_fallback``) is true; ``use_etri=False`` is itself an explicit
        opt-out of the pool (tests / ablations).
        """
        if imgsz % 32:
            raise ValueError("imgsz must be a multiple of 32")
        etri, reason = None, None
        if use_etri:
            try:
                etri = load_etri_mixed(etri_root)
            except EtriUnavailable as exc:
                reason = str(exc)
                if not allow_native_fallback:
                    raise SearchSpaceUnavailable(
                        f"ETRI mixed operator pool unavailable ({reason}); the native_fallback space (no "
                        "convolution-free operators) is used only with console.search.allow_native_fallback=true"
                    ) from exc
        else:
            reason = "ETRI mixed pool disabled by caller"
        space = cls(nc=nc, imgsz=imgsz, variant="etri_mixed" if etri else "native_fallback",
                    ops_feature=FEATURE_OPS if etri else NATIVE_FEATURE_OPS,
                    ops_down=DOWN_OPS if etri else ("Conv",), etri=etri, fallback_reason=reason,
                    allow_native_fallback=bool(allow_native_fallback))
        for family in families:
            space._add_family(family)
        if not space.templates:
            raise RepairFailed(f"no seed template builds: {space.rejected_templates}")
        return space

    def _add_family(self, family: str) -> None:
        base, src = load_base_yaml(family, self.nc)
        native = Genotype.from_yaml_dict(base, family=family, template_id=f"{family}:native")
        self.slots[family] = family_slots(native, ops_feature=self.ops_feature, ops_down=self.ops_down)
        self.native_head[family] = native.yaml["head"]
        self.native_reports[family] = self.inspect(native)
        if self.etri is not None:
            variants = self.etri.make_seed_variants(copy.deepcopy(base))
            origin = {"kind": "vendored_mixed_seed_variant",
                      "function": "mbnas_core.search.mixed_pool.seed_variants.make_mixed_seed_variants",
                      "base": src, "mixed_pool_files_sha256": dict(sorted(self.etri.files.items()))}
        else:
            variants = {"native": base, "cnn_backbone": _native_cnn_variant(base)}
            origin = {"kind": "native_fallback_variant", "base": src}
        for name in ETRI_VARIANTS:
            if name not in variants:
                continue
            tid = f"{family}:{name}"
            g = Genotype.from_yaml_dict(variants[name], family=family, template_id=tid)
            rep = self.inspect(g)
            bad = self.violations(g, rep)
            if not rep.ok or bad:
                self.rejected_templates.append({"id": tid, "error": rep.error, "violations": bad})
                continue
            self.templates[tid] = Template(tid, family, name, g, {**origin, "variant": name,
                                                                   "yaml_hash": g.yaml_hash()})
            self.prior[tid] = self.alternatives(g, rep)

    # -- inspection / rules -------------------------------------------------------------------------------------
    def inspect(self, genotype: Genotype) -> BuildReport:
        """Cached CPU build + reference forward + built audit (see genotype.inspect_genotype)."""
        key = genotype.yaml_hash()
        if key not in self._cache:
            nb = genotype.n_backbone

            def hook(model: Any, rep: BuildReport) -> dict[str, Any]:
                out = {"built": audit_mod.built_audit(model, nb, flops=rep.flops, imgsz=self.imgsz)}
                if self.etri is not None:
                    out["etri_audit_model"] = self.etri.audit_model(model)
                return out

            self._cache[key] = inspect_genotype(genotype, self.imgsz, audit_hook=hook)
        return self._cache[key]

    def _slot_ctx(self, rep: BuildReport, slot: Slot) -> dict[str, Any]:
        li = rep.layers[slot.row]
        return {"c1": li.c_in, "h": li.h_in, "w": li.w_in}

    def _scale(self, genotype: Genotype) -> list[float]:
        return genotype.yaml["scales"][genotype.yaml["scale"]]

    def symbolic_channels(self, genotype: Genotype) -> list[tuple[int | None, int | None]]:
        """(c_in, c_out) per row from the YAML alone (width scaling, Concat sums); spatial sizes are fixed per family."""
        scale, outs, result = self._scale(genotype), [], []
        for i, (frm, _, module, args) in enumerate(genotype.rows()):
            ins = [3 if (s == -1 and i == 0) else outs[i + s if s < 0 else s] for s in (frm if isinstance(frm, list) else [frm])]
            c_in = sum(ins) if all(x is not None for x in ins) else None
            name = str(module).split(".")[-1]
            if name in ("Concat", "Upsample"):
                c_out = c_in
            elif name != "Detect" and args and isinstance(args[0], (int, float)):
                c_out = effective_channels(args[0], scale)
            else:
                c_out = None
            outs.append(c_out)
            result.append((c_in, c_out))
        return result

    def illegal_reason_for(self, genotype: Genotype, row: int) -> str | None:
        """Symbolic rule check of one slot row (YAML channels + the family's fixed spatial sizes); no build."""
        slot = next((s for s in self.slots[genotype.family] if s.row == row), None)
        gene = slot.gene_of(genotype.rows()[row]) if slot else None
        if slot is None or gene is None:
            return None if slot is None else "row is not a declared choice of this slot"
        c_in, c_out = self.symbolic_channels(genotype)[row]
        ref = self.native_reports[genotype.family].layers[row]
        return illegal_reason(gene, kind=slot.kind, c1=c_in, c2=c_out or 0, h=ref.h_in, w=ref.w_in)

    def violations(self, genotype: Genotype, rep: BuildReport) -> list[dict[str, Any]]:
        """Rule violations in row order (empty when legal). A failed build reports the first symbolic rule
        violation when one explains it, else the build failure with its row."""
        if not rep.ok:
            for slot in self.slots[genotype.family]:
                reason = self.illegal_reason_for(genotype, slot.row)
                if reason:
                    return [{"row": slot.row, "reason": f"{reason}; CPU {rep.error}"}]
            return [{"row": rep.error_row, "reason": rep.error}]
        out, rows = [], genotype.rows()
        for slot in self.slots[genotype.family]:
            gene = slot.gene_of(rows[slot.row])
            if gene is None:
                out.append({"row": slot.row, "reason": f"row {rows[slot.row]} is not a declared choice of this slot"})
                continue
            c2 = rep.layers[slot.row].c_out
            reason = illegal_reason(gene, kind=slot.kind, c2=c2 if c2 is not None else 0, **self._slot_ctx(rep, slot))
            if reason:
                out.append({"row": slot.row, "reason": reason})
        return out

    def alternatives(self, genotype: Genotype, rep: BuildReport) -> dict[int, dict[str, list[Choice]]]:
        """Per slot row: legal alternative choices (current gene excluded) grouped by operator."""
        rows, scale, table = genotype.rows(), self._scale(genotype), {}
        for slot in self.slots[genotype.family]:
            current = slot.gene_of(rows[slot.row])
            ctx = self._slot_ctx(rep, slot)
            by_op: dict[str, list[Choice]] = {}
            for ch in slot.choices():
                if ch == current:
                    continue
                c2 = effective_channels(slot.native_row[3][0] * ch.width, scale)
                if illegal_reason(ch, kind=slot.kind, c2=c2, **ctx) is None:
                    by_op.setdefault(ch.op, []).append(ch)
            table[slot.row] = by_op
        return table

    @staticmethod
    def draw(rng: _random.Random, by_op: dict[str, list[Choice]]) -> Choice:
        """Hierarchical uniform draw: operator uniform over legal operators, then (depth, width) uniform."""
        op = rng.choice(sorted(by_op))
        return rng.choice(by_op[op])

    # -- repair ---------------------------------------------------------------------------------------------------
    def repair(self, genotype: Genotype, reference: Genotype, reference_label: str, origins: list[RowOrigin],
               reference_sources: list[str]) -> tuple[Genotype, BuildReport, list[dict[str, Any]]]:
        """Revert rows toward ``reference`` until the child builds and conforms; returns (child, report, events).

        ``reference_sources[i]`` names where the reference's row ``i`` came from; a reverted row's origin becomes
        ``repair`` with that source.
        """
        rows, ref_rows, events = genotype.rows(), reference.rows(), []
        for _ in range(len(rows) + 1):
            g = genotype.with_rows(rows)
            rep = self.inspect(g)
            bad = self.violations(g, rep)
            if not bad:
                return g, rep, events
            changed = [i for i in range(len(rows)) if rows[i] != ref_rows[i]]
            if not changed:
                raise RepairFailed(f"reference {reference_label} violates rules: {bad}")
            row = bad[0]["row"]
            if row is not None and rows[row] != ref_rows[row]:
                target = row
            else:
                upstream = [i for i in changed if row is None or i < row]
                target = max(upstream) if upstream else max(changed)
            before, after = copy.deepcopy(rows[target]), copy.deepcopy(ref_rows[target])
            events.append({"row": target, "violation_row": row, "reason": bad[0]["reason"], "before": before,
                           "after": after, "reverted_to": reference_label})
            origins[target] = RowOrigin("repair", reference_sources[target], "repair", before, after, bad[0]["reason"])
            rows[target] = after
        raise RepairFailed("repair did not converge")

    # -- proposal operators -----------------------------------------------------------------------------------------
    def template_ids(self) -> list[str]:
        return sorted(self.templates)

    def sample_prior(self, rng: _random.Random, *, method: str) -> Proposal:
        """One proposal from the frozen prior (both arms; generation 0 of GA)."""
        tid = rng.choice(self.template_ids())
        tpl = self.templates[tid]
        k = rng.choice(PRIOR_NUM_DRAWS)
        table = self.prior[tid]
        eligible = sorted(r for r, by_op in table.items() if by_op)
        chosen = sorted(rng.sample(eligible, min(k, len(eligible))))
        rows = tpl.genotype.rows()
        origins = [RowOrigin("random", tid, "inherit") for _ in rows]
        slots = {s.row: s for s in self.slots[tpl.family]}
        draws = []
        for r in chosen:
            choice = self.draw(rng, table[r])
            before, rows[r] = rows[r], slots[r].render(choice)
            origins[r] = RowOrigin("random", tid, "sample", before, copy.deepcopy(rows[r]))
            draws.append({"row": r, "choice": choice.as_dict()})
        child, rep, repairs = self.repair(tpl.genotype.with_rows(rows), tpl.genotype, f"template:{tid}", origins,
                                          [tid] * len(rows))
        return Proposal(method=method, operation="random", genotype=child, report=rep, origins=origins,
                        repair_events=repairs, prior_draw={"template": tid, "num_draws": k, "draws": draws})

    def mutate(self, genotype: Genotype, rng: _random.Random,
               origins: list[RowOrigin]) -> tuple[Genotype, dict[str, Any] | None]:
        """Operator swap at one uniformly chosen slot that has a legal alternative (shared prior draw rule)."""
        rep = self.inspect(genotype)
        table = self.alternatives(genotype, rep)
        eligible = sorted(r for r, by_op in table.items() if by_op)
        if not eligible:
            return genotype, None
        r = rng.choice(eligible)
        choice = self.draw(rng, table[r])
        slot = next(s for s in self.slots[genotype.family] if s.row == r)
        rows = genotype.rows()
        before, rows[r] = rows[r], slot.render(choice)
        origins[r] = RowOrigin("mutation", origins[r].source, "mutate", before, copy.deepcopy(rows[r]))
        event = {"row": r, "scope": slot.scope, "before": before, "after": copy.deepcopy(rows[r]),
                 "choice": choice.as_dict(), "old_name": str(before[2]), "new_name": choice.op,
                 "pre_mutation_architecture_hash": rep.architecture_hash}
        return genotype.with_rows(rows), event

    @staticmethod
    def crossover_rows(a: Genotype, b: Genotype, rng: _random.Random) -> tuple[list[list[Any]], int]:
        """One-point row crossover: rows [0, cut) from A, [cut, n) from B; cut in [1, n-2]."""
        if a.family != b.family:
            raise ValueError(f"crossover requires one template family ({a.family} != {b.family})")
        ra, rb = a.rows(), b.rows()
        cut = rng.randrange(1, len(ra) - 1)
        return ra[:cut] + rb[cut:], cut

    # -- description ----------------------------------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        """JSON description (identical for both arms); ``space_hash``/``prior_hash`` are hashes of its parts."""
        prior = {tid: {str(r): {op: [c.as_dict() for c in cs] for op, cs in sorted(by_op.items())}
                       for r, by_op in sorted(t.items())} for tid, t in sorted(self.prior.items())}
        body = {
            "version": SPACE_VERSION, "rules": RULES_VERSION, "variant": self.variant, "nc": self.nc,
            "imgsz": self.imgsz, "ops_feature": list(self.ops_feature), "ops_down": list(self.ops_down),
            "depth_choices": list(DEPTH_CHOICES), "width_choices": list(WIDTH_CHOICES), "token_budget": TOKEN_BUDGET,
            "templates": [{"id": t.id, "family": t.family, "variant": t.variant, "source": t.source}
                          for t in sorted(self.templates.values(), key=lambda t: t.id)],
            "slots": {f: [{"row": s.row, "scope": s.scope, "kind": s.kind, "ops": list(s.ops)} for s in sl]
                      for f, sl in sorted(self.slots.items())},
        }
        prior_def = {"template": "uniform", "num_slot_draws": list(PRIOR_NUM_DRAWS),
                     "slot": "uniform without replacement over slots with a legal alternative",
                     "choice": "operator uniform over legal operators, then (repeats, width) uniform",
                     "conditioned_on_fitness": False, "tables": prior}
        return {**body, "prior": {k: v for k, v in prior_def.items() if k != "tables"},
                "space_hash": canonical_sha256(body), "prior_hash": canonical_sha256({"space": body, **prior_def}),
                "space_hash_definition": SPACE_HASH_DEFINITION,
                "fallback_reason": self.fallback_reason, "allow_native_fallback": self.allow_native_fallback,
                "rejected_templates": self.rejected_templates,
                "etri": self.etri.provenance() if self.etri else None}

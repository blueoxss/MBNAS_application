"""Public, data-only structural inspection boundary over the existing genotype builder (``mbnas-structure/2``).

This service never trains, reads a dataset, benchmarks latency, or computes fitness.  A
PASS means that the existing CPU DetectionModel parser and reference forward both ran
*and* that the individual rules below held on the shapes observed at that forward.
Missing runtimes are UNVERIFIED.  YAML rows remain genes; their expanded modules are
listed separately instead of inventing a one-to-one internal-layer correspondence.

Declared (D), derived (R) and observed (O) values live under separate keys: ``repeats_detail`` carries the
declared count, the depth-scaled formula value and the count read from the built module; ``effective.repeats`` is
the observed count and is ``None`` whenever nothing was observed.  Individual rules (CONTRACTS_v2 section 1.3):

* ``channels_spatial_stride`` - every tensor delivered to a layer equals the output of its source row.
* ``merge_conditions`` - Concat sources agree on every axis but the concat axis; Add/Sum sources agree on c, h, w.
* ``head_connection`` - a detection head receives ``nl`` sources whose strides strictly increase and match
  ``Detect.stride``; the head ``nc`` equals the model ``nc``.

A check without a rule of its own that passes only because the forward succeeded is marked ``derived_from_forward``.
"""
from __future__ import annotations

import math
import re
from typing import Any

from mbnas_contracts.structure_doc import (  # noqa: F401 - StructureReport document constants (contract §4)
    BACKEND, CHECK_EVIDENCE, CHECK_NAMES, EVIDENCE_LEVELS, REPRESENTATION, SCHEMA,
)
from mbnas_model_runtime import block_catalog
from mbnas_model_runtime.genotype import BuildReport, Genotype, LayerInfo, inspect_genotype, observation_environment

DETECTION_MODULES = frozenset({"Detect", "v10Detect", "WorldDetect", "YOLOEDetect", "RTDETRDecoder"})
MERGE_MODULES = frozenset({"Concat", "Add", "Sum"})
CHANNEL_MODULES = frozenset({"Conv", "Conv2", "DWConv", "GhostConv", "LightConv", "RepConv", "ConvTranspose",
                             "DWConvTranspose2d", "Bottleneck", "BottleneckCSP", "C1", "C2", "C2f", "C3", "C3x",
                             "C3TR", "C3Ghost", "C3k2", "C2PSA", "C2fPSA", "C2fAttn", "A2C2f", "SPP", "SPPF",
                             "Focus", "NASPatchMerge", "NASWindowTransformer", "NASGlobalTransformer"})


def _diagnostic(code: str, cause: str, suggestion: str, position: int | None = None,
                path: str | None = None, evidence_level: str | None = None) -> dict[str, Any]:
    out = {"code": code, "position": position, "path": path, "cause": cause, "suggestion": suggestion}
    if evidence_level is not None:
        out["evidence_level"] = evidence_level
    return out


def _name(module: Any) -> str:
    return str(module).rsplit(".", 1)[-1]


def _finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _runtime_message(message: str | None) -> str:
    """Keep useful shape/parser diagnostics without exposing traceback or local source paths."""
    text = str(message or "model construction or reference forward did not complete")
    text = text.split("Traceback (most recent call last)", 1)[0]
    text = re.sub(r"(?:[A-Za-z]:)?/[^\s'\";:,()]+", "<server-path>", text)
    return " ".join(text.split())[:600]


def _failure_code(message: str | None) -> str:
    value = str(message or "").lower()
    if "keyerror" in value or "not defined" in value or "no attribute" in value:
        return "BLOCK_UNSUPPORTED"
    if any(word in value for word in ("size", "dimension", "channel", "shape", "stride", "concat")):
        return "SHAPE_MISMATCH"
    if "head" in value or "nc" in value:
        return "HEAD_INVALID"
    return "STRUCTURE_BUILD_FAILED"


def _environment_failure(message: str | None) -> bool:
    value = str(message or "").lower()
    return any(word in value for word in ("modulenotfounderror", "no module named", "importerror",
                                          "shared object file", "operator torchvision::", "not compiled"))


def _scale(genotype: Genotype) -> tuple[float, float, float] | None:
    config = genotype.yaml
    if config.get("scales") is not None:
        if not isinstance(config["scales"], dict) or not isinstance(config.get("scale"), str):
            return None
        values = config["scales"].get(config.get("scale"))
        if isinstance(values, (list, tuple)) and len(values) == 3 and all(
                _finite_number(v) and v > 0 for v in values):
            return tuple(float(v) for v in values)
        return None
    depth, width = config.get("depth_multiple", 1.0), config.get("width_multiple", 1.0)
    if all(_finite_number(v) and v > 0 for v in (depth, width)):
        return float(depth), float(width), float("inf")
    return None


def _section_basis(index: int, genotype: Genotype, module: Any) -> tuple[str, str]:
    """``(section, section_basis)`` per DD-15; the section value keeps the v1 rule, the basis states why."""
    if index < genotype.n_backbone:
        return "backbone", "yaml:backbone"
    if _name(module) in DETECTION_MODULES:
        return "head", "yaml:head+detection_module"
    return "neck", "derived:non-detection head row -> neck"


def describe_genotype(genotype: Genotype, *, report: BuildReport | None = None,
                      lineage: dict[str, Any] | None = None,
                      observation: dict[str, Any] | None = None,
                      resolve_source: bool = False) -> dict[str, Any]:
    """Render exact declared rows and any *recorded* runtime metadata; no model is executed.

    Every block carries ``catalog`` (static design-origin entry, DD-38) and the document ``catalog_summary``.
    ``resolve_source`` (only the explicit checks pass True) additionally imports the module classes to fill
    ``catalog.source_path``; the default keeps a declared-only document import-light with ``source_path`` null.

    ``effective.repeats`` is the repeat count read from the built module (``None`` when nothing was observed);
    ``effective.repeats_scaled`` is the parser's depth formula and ``repeats_detail`` lists declared / scaled /
    observed side by side.  ``internal_layers`` contains the actually built module paths when available.
    """
    rows = genotype.rows()
    scale = _scale(genotype)
    runtime = report is not None and report.ok
    layer_objs: dict[int, LayerInfo] = {li.index: li for li in report.layers} if runtime else {}
    signature = (report.signature or {}) if runtime else {}
    modules = signature.get("modules") or []
    yaml_hash = genotype.yaml_hash()
    tag = yaml_hash[7:19]
    blocks, edges = [], []
    for index, (frm, repeats, module, args) in enumerate(rows):
        yaml_section = "backbone" if index < genotype.n_backbone else "head"
        yaml_index = index if yaml_section == "backbone" else index - genotype.n_backbone
        yaml_path = f"{yaml_section}[{yaml_index}]"
        refs = frm if isinstance(frm, list) else [frm]
        resolved = [(-1 if index == 0 and ref == -1 else index + ref if isinstance(ref, int) and ref < 0 else ref)
                    for ref in refs]
        for raw, source in zip(refs, resolved):
            edges.append({"source": source, "target": index, "from_ref": raw, "connection_source": "yaml_from"})
        layer = layer_objs.get(index)
        scaled = max(round(repeats * scale[0]), 1) if scale and repeats > 1 else repeats if scale else None
        effective = layer.as_dict() if layer is not None else {}
        for key in ("index", "module", "from"):
            effective.pop(key, None)
        observed = layer.observed_repeats if layer is not None else None
        effective["repeats"] = observed
        effective["repeats_scaled"] = scaled
        if layer is not None:
            effective["repeats_source"] = layer.repeats_source
        if layer is not None and layer.internal is not None:
            internal = copy_records(layer.internal)
        else:
            internal = [{"path": item["path"], "module": item.get("cls"), "parameters": item.get("params", []),
                         "attributes": item.get("attrs", {}), "shared": None} for item in modules
                        if str(item.get("path", "")).startswith(f"{index}.")]
        section, basis = _section_basis(index, genotype, module)
        block = {
            "id": f"block-{index:04d}", "block_id": f"block-{index:04d}", "stable_id": f"ul:{yaml_path}@{tag}",
            "index": index, "position": index, "gene_index": index, "name": str(module), "module": str(module),
            "module_class": layer.module if layer is not None else None,
            "wrapper": layer.wrapper if layer is not None else None,
            "wrapped_module": layer.wrapped_module if layer is not None else None,
            "native_path": f"model.{index}",
            "section": section, "section_basis": basis, "connection_source": "yaml_from",
            "yaml_section": yaml_section, "yaml_index": yaml_index, "yaml_path": yaml_path,
            "from": frm, "from_resolved": resolved, "repeats": repeats, "args": args,
            "declared": {"repeats": repeats, "channels": args[0] if args and _finite_number(args[0]) and _name(module) in CHANNEL_MODULES else None},
            "repeats_detail": {"declared": repeats, "scaled": scaled, "observed": observed},
            "effective": effective, "internal_layers": internal, "checks_local": [],
            "internal_mapping_status": "RECORDED" if runtime else "UNVERIFIED",
        }
        blocks.append(block)
    flops = report.flops if runtime else None
    catalog_summary = block_catalog.attach_yaml_catalog(blocks, resolve_source=resolve_source)
    return {"schema": SCHEMA, "backend": BACKEND, "representation": REPRESENTATION,
            "family": genotype.family, "template_id": genotype.template_id,
            "class_count": genotype.yaml.get("nc"), "scale": genotype.yaml.get("scale"),
            "yaml_hash": yaml_hash,
            "architecture_hash": report.architecture_hash if runtime else None,
            "signature_hash": report.signature_hash if runtime else None,
            "parameters": report.params if runtime else None,
            "flops": flops, "flops_status": "MEASURED" if flops is not None else "NOT_MEASURED",
            "flops_source": "proposer_flop_counter" if flops is not None else None,
            "observation": observation,
            "blocks": blocks, "catalog_summary": catalog_summary, "edges": edges, "edges_unobserved": [], "lineage": lineage,
            "lineage_status": "RECORDED" if lineage else "NOT_APPLICABLE"}


def copy_records(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Independent copy of recorded internal-layer rows (the cached report must not be mutated by consumers)."""
    return [{**item, "parameters": [list(p) for p in item.get("parameters", [])],
             "attributes": dict(item.get("attributes", {}))} for item in items]


# -- individual rules over observed shapes ---------------------------------------------------------------------------
def _fmt(shape: tuple[Any, Any, Any] | None) -> str:
    return "unknown" if shape is None else "x".join("?" if v is None else str(v) for v in shape)


def _src_label(source: Any) -> str:
    return "input image" if source == -1 else "unrecorded source" if source is None else f"row {source}"


def _input_triples(inputs: list[dict[str, Any]]) -> list[tuple[Any, tuple[Any, Any, Any]]]:
    return [(i.get("source"), (i.get("c"), i.get("h"), i.get("w"))) for i in inputs]


def _shape_rule(layers: list[LayerInfo], imgsz: int) -> tuple[list[tuple[int, str]], int]:
    """Continuity: every delivered tensor equals the output of its source row (violations, number compared)."""
    by_index = {li.index: li for li in layers}
    problems, compared = [], 0
    for li in layers:
        sources = li.frm if isinstance(li.frm, (list, tuple)) else [li.frm]
        if li.inputs and len(li.inputs) != len(sources):
            problems.append((li.index, f"row {li.index} received {len(li.inputs)} tensor(s) but its from lists {len(sources)} source(s)"))
            continue
        for item in li.inputs:
            src = item.get("source")
            if src == -1:
                expected = (3, imgsz, imgsz)
            elif isinstance(src, int) and src in by_index:
                expected = (by_index[src].c_out, by_index[src].h_out, by_index[src].w_out)
            else:
                continue
            got = (item.get("c"), item.get("h"), item.get("w"))
            if None in expected or None in got:
                continue
            compared += 1
            if got != expected:
                problems.append((li.index, f"row {li.index} received {_fmt(got)} from {_src_label(src)} which produced {_fmt(expected)}"))
    return problems, compared


def _merge_cause(name: str, args: list[Any], inputs: list[dict[str, Any]]) -> str | None:
    """Why the sources of a Concat/Add/Sum row cannot be merged, or None when they can (or cannot be judged)."""
    triples = [(s, t) for s, t in _input_triples(inputs) if None not in t]
    if len(triples) < 2:
        return None
    listing = ", ".join(f"{_src_label(s)}={_fmt(t)}" for s, t in triples)
    if name == "Concat":
        dim = args[0] if args and isinstance(args[0], int) and not isinstance(args[0], bool) and 1 <= args[0] <= 3 else 1
        axes = [a for a in range(3) if a != dim - 1]
        label = {0: "channels", 1: "height", 2: "width"}
        bad = [label[a] for a in axes if len({t[a] for _, t in triples}) > 1]
        if bad:
            return f"Concat(dim={dim}) sources differ in {' and '.join(bad)}: {listing}"
        return None
    bad = [label for a, label in enumerate(("channels", "height", "width")) if len({t[a] for _, t in triples}) > 1]
    if bad:
        return f"{name} sources must have identical c, h, w but differ in {' and '.join(bad)}: {listing}"
    return None


def _merge_rule(rows: list[list[Any]], layers: list[LayerInfo]) -> tuple[list[tuple[int, str]], int]:
    problems, checked = [], 0
    for li in layers:
        if li.index >= len(rows):
            continue
        name = _name(rows[li.index][2])
        if name not in MERGE_MODULES and li.wrapped_module not in MERGE_MODULES and li.module not in MERGE_MODULES:
            continue
        checked += 1
        cause = _merge_cause(name if name in MERGE_MODULES else li.wrapped_module or li.module, rows[li.index][3], li.inputs)
        if cause:
            problems.append((li.index, cause))
        elif li.c_out is not None and name == "Concat":
            known = [i["c"] for i in li.inputs if i.get("c") is not None]
            dim = rows[li.index][3][0] if rows[li.index][3] and isinstance(rows[li.index][3][0], int) else 1
            if dim == 1 and len(known) == len(li.inputs) and sum(known) != li.c_out:
                problems.append((li.index, f"Concat output has {li.c_out} channels but its sources sum to {sum(known)}"))
    return problems, checked


def _head_cause(inputs: list[dict[str, Any]], head: dict[str, Any] | None, imgsz: int, nc: Any) -> str | None:
    """Rule for a detection head: ``nl`` sources, strictly increasing strides equal to ``Detect.stride``, matching nc."""
    if not head:
        return None
    nl = head.get("nl")
    if isinstance(nl, int) and len(inputs) != nl:
        return f"head expects nl={nl} input source(s) but received {len(inputs)}"
    strides = [imgsz / i["h"] for i in inputs if isinstance(i.get("h"), int) and i["h"] > 0]
    if len(strides) == len(inputs) and len(strides) > 1 and any(b <= a for a, b in zip(strides, strides[1:])):
        return f"head input strides {[round(s, 3) for s in strides]} are not strictly increasing (sources {[i.get('source') for i in inputs]})"
    declared = head.get("head_strides")
    if declared and len(strides) == len(inputs):
        if len(declared) != len(strides) or any(not math.isclose(a, b, rel_tol=1e-3) for a, b in zip(declared, strides)):
            return f"head_strides {declared} differ from the observed input strides {[round(s, 3) for s in strides]}"
    head_nc = head.get("nc")
    if isinstance(nc, int) and isinstance(head_nc, int) and head_nc != nc:
        return f"head nc={head_nc} differs from model nc={nc}"
    return None


def _head_rule(rows: list[list[Any]], layers: list[LayerInfo], imgsz: int, nc: Any) -> tuple[list[tuple[int, str]], int]:
    problems, checked = [], 0
    for li in layers:
        if not li.head:
            continue
        checked += 1
        cause = _head_cause(li.inputs, li.head, imgsz, nc)
        if cause:
            problems.append((li.index, cause))
    return problems, checked


def _checks_doc(status: dict[str, str], level: dict[str, str | None], extra: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = []
    for name in CHECK_NAMES:
        item = {"name": name, "status": status.get(name, "NOT_CHECKED"),
                "evidence_level": level.get(name) if status.get(name, "NOT_CHECKED") != "NOT_CHECKED" else None}
        item.update((extra or {}).get(name, {}))
        out.append(item)
    return out


def _path_of(genotype: Genotype, index: int) -> str:
    return f"backbone[{index}]" if index < genotype.n_backbone else f"head[{index - genotype.n_backbone}]"


def _attach_local(result: dict[str, Any], diagnostics: list[dict[str, Any]]) -> None:
    """Mirror positioned rule diagnostics onto their block (``checks_local``) so the block detail can show them."""
    for diag in diagnostics:
        pos = diag.get("position")
        if isinstance(pos, int) and 0 <= pos < len(result["blocks"]) and diag.get("code") in {
                "SHAPE_MISMATCH", "MERGE_INPUT_MISMATCH", "HEAD_INPUT_INVALID"}:
            result["blocks"][pos]["checks_local"].append(
                {"code": diag["code"], "status": "FAIL", "cause": diag["cause"],
                 "evidence_level": diag.get("evidence_level")})


def check_structure(genotype: Genotype, *, input_size: int = 640, class_count: int | None = None,
                    report: BuildReport | None = None) -> dict[str, Any]:
    """Check declared contracts, then reuse ``inspect_genotype`` CPU build + forward and apply the observed-shape rules.

    A proposal's report may be reused only for the same input size and complete
    identity/shape metadata.  A supplied failed report is never upgraded to PASS.
    Performance fields are deliberately absent; FLOPs are not requested here (``flops_status`` NOT_MEASURED unless a
    supplied proposer report carries a counter value).  ``observation`` records the CPU environment of the run.
    """
    diagnostics: list[dict[str, Any]] = []
    status = {name: "NOT_CHECKED" for name in CHECK_NAMES}
    level: dict[str, str | None] = {name: CHECK_EVIDENCE[name] for name in CHECK_NAMES}
    extra: dict[str, dict[str, Any]] = {}
    if isinstance(input_size, bool) or not isinstance(input_size, int) or input_size < 32 or input_size % 32:
        diagnostics.append(_diagnostic("INPUT_SIZE_INVALID", "input size must be a positive multiple of 32",
                                       "Choose the model family's supported input size.", path="input_size",
                                       evidence_level="declared"))
    rows = genotype.rows()
    if not rows:
        diagnostics.append(_diagnostic("MODEL_EMPTY", "model has no blocks", "Provide backbone and detection head rows.",
                                       evidence_level="declared"))
    for index, (frm, repeats, module, args) in enumerate(rows):
        refs = frm if isinstance(frm, list) else [frm]
        path = _path_of(genotype, index)
        if not refs or any(isinstance(ref, bool) or not isinstance(ref, int) for ref in refs):
            diagnostics.append(_diagnostic("FROM_INVALID", "from must be an integer or nonempty list of integers",
                                           "Use an earlier row index or a valid negative relative reference.", index, path,
                                           "declared"))
            continue
        for ref in refs:
            source = index + ref if ref < 0 else ref
            if not (index == 0 and ref == -1) and not 0 <= source < index:
                diagnostics.append(_diagnostic("FROM_OUT_OF_RANGE", f"from {ref} resolves to row {source}, outside preceding rows",
                                               "Connect this block to an earlier valid output.", index, path, "declared"))
        if not isinstance(module, str) or not re.fullmatch(r"(?:torch\.)?(?:nn\.)?[A-Za-z_][A-Za-z0-9_]*", module):
            diagnostics.append(_diagnostic("BLOCK_NAME_INVALID", "module name is not a supported parser identifier",
                                           "Choose a registered block name.", index, path, "declared"))
        if _name(module) in DETECTION_MODULES and args and isinstance(args[0], int) and args[0] != genotype.yaml.get("nc"):
            diagnostics.append(_diagnostic("HEAD_CLASS_MISMATCH", "head class count differs from model nc",
                                           "Use nc or the same class count as the model.", index, path, "declared"))
    status["references"] = "FAIL" if any(d["code"].startswith("FROM_") for d in diagnostics) else "PASS"
    nc = genotype.yaml.get("nc")
    if isinstance(nc, bool) or not isinstance(nc, int) or nc < 1 or (class_count is not None and nc != class_count):
        diagnostics.append(_diagnostic("CLASS_COUNT_INVALID", "model nc must be positive and equal the requested class count",
                                       "Set the model and request to the same positive number of classes.", path="nc",
                                       evidence_level="declared"))
    status["class_count"] = "FAIL" if any(d["code"] in {"CLASS_COUNT_INVALID", "HEAD_CLASS_MISMATCH"} for d in diagnostics) else "PASS"
    if _scale(genotype) is None:
        diagnostics.append(_diagnostic("SCALE_INVALID", "selected scale is missing or has invalid depth/width/channel bounds",
                                       "Select an existing scale with three positive values.", path="scale",
                                       evidence_level="declared"))
    status["scales"] = "FAIL" if any(d["code"] == "SCALE_INVALID" for d in diagnostics) else "PASS"
    if not any(_name(row[2]) in DETECTION_MODULES for row in rows):
        diagnostics.append(_diagnostic("DETECTION_HEAD_MISSING", "no supported detection head is declared",
                                       "Connect a supported Detect or RTDETRDecoder head to feature outputs.",
                                       evidence_level="declared"))
    if any(d["code"] in {"DETECTION_HEAD_MISSING", "HEAD_CLASS_MISMATCH"} for d in diagnostics):
        status["head_connection"], level["head_connection"] = "FAIL", "declared"
    # Ultralytics uses eval(activation); untrusted YAML must never execute arbitrary Python.
    activation = genotype.yaml.get("activation")
    if activation is not None and not re.fullmatch(r"(?:torch\.)?nn\.(?:SiLU|ReLU|ReLU6|LeakyReLU|Hardswish|Identity)\((?:\s*(?:True|False|[0-9.]+)\s*(?:,\s*)?)*\)", str(activation)):
        diagnostics.append(_diagnostic("ACTIVATION_REFUSED", "activation expression is outside the safe supported constructors",
                                       "Use a supported torch.nn activation constructor.", path="activation",
                                       evidence_level="declared"))
    # yaml_rows covers the per-row identifier/emptiness rules and the activation expression; it is derived from the
    # diagnostics like references/class_count/scales (INPUT_SIZE_INVALID stays attributed to the request, not to the YAML).
    status["yaml_rows"] = "FAIL" if any(d["code"] in {"BLOCK_NAME_INVALID", "MODEL_EMPTY", "ACTIVATION_REFUSED"} for d in diagnostics) else "PASS"
    if diagnostics:
        return {**describe_genotype(genotype, resolve_source=True), "input_size": input_size, "status": "FAIL", "diagnostics": diagnostics,
                "checks": _checks_doc(status, level)}
    # A BuildReport from the existing proposer records the actual reference forward.
    origin = "supplied"
    if report is None or report.imgsz != input_size or report.yaml_hash != genotype.yaml_hash() or (report.ok and (not report.layers or not report.architecture_hash or not report.signature_hash)):
        origin = "executed"
        try:
            report = inspect_genotype(genotype, input_size, count_flops=False)
        except (ImportError, ModuleNotFoundError, OSError) as exc:
            diagnostics.append(_diagnostic("STRUCTURE_RUNTIME_UNAVAILABLE", f"CPU structure runtime unavailable ({type(exc).__name__})",
                                           "Install the configured model backend, then rerun the structure check.",
                                           evidence_level="built"))
            return {**describe_genotype(genotype, resolve_source=True), "input_size": input_size, "status": "UNVERIFIED", "diagnostics": diagnostics,
                    "checks": _checks_doc(status, level)}
        except Exception as exc:  # service boundary: failures are data, never source tracebacks
            diagnostics.append(_diagnostic("STRUCTURE_CHECK_FAILED", f"structure check did not complete ({type(exc).__name__})",
                                           "Review the model configuration and configured runtime.",
                                           evidence_level="built"))
            return {**describe_genotype(genotype, resolve_source=True), "input_size": input_size, "status": "UNVERIFIED", "diagnostics": diagnostics,
                    "checks": _checks_doc(status, level)}
    observation = {**(report.observation or {"method": "ultralytics_parse_model+forward_hook", "device": "cpu",
                                              "imgsz": report.imgsz, "environment_recorded": False}),
                   "report_origin": origin}
    if not report.ok:
        return _failed_report(genotype, rows, report, input_size, status, level, extra, observation)
    layers = report.layers
    observed = any(li.inputs for li in layers)
    status["module_support"] = "PASS" if len(layers) == len(rows) else "FAIL"
    extra["module_support"] = {"rows_checked": len(rows)}
    if len(layers) != len(rows):
        diagnostics.append(_diagnostic("BLOCK_UNSUPPORTED", f"the parser built {len(layers)} module(s) for {len(rows)} declared row(s)",
                                       "Use only blocks the installed parser registers.", evidence_level="built"))
    status["cpu_build"], status["reference_forward"] = "PASS", "PASS"
    findings: dict[str, tuple[list[tuple[int, str]], int]] = {}
    if observed:
        findings["channels_spatial_stride"] = _shape_rule(layers, report.imgsz)
        findings["merge_conditions"] = _merge_rule(rows, layers)
        findings["head_connection"] = _head_rule(rows, layers, report.imgsz, nc)
    code_of = {"channels_spatial_stride": "SHAPE_MISMATCH", "merge_conditions": "MERGE_INPUT_MISMATCH",
               "head_connection": "HEAD_INPUT_INVALID"}
    advice = {"channels_spatial_stride": "Connect the block to a source whose output matches its input shape.",
              "merge_conditions": "Give every merged source the same spatial size (and channels for Add/Sum).",
              "head_connection": "Feed the detection head one source per scale with strictly increasing strides."}
    for name in ("channels_spatial_stride", "merge_conditions", "head_connection"):
        if name not in findings:  # legacy report without per-source inputs: only the forward success is known
            status[name], level[name] = "PASS", "derived_from_forward"
            continue
        problems, checked = findings[name]
        status[name] = "FAIL" if problems else "PASS"
        extra[name] = {"rows_checked": checked}
        for index, cause in problems:
            diagnostics.append(_diagnostic(code_of[name], cause, advice[name], index, _path_of(genotype, index), "forward"))
    overall = "FAIL" if diagnostics else "PASS"
    result = {**describe_genotype(genotype, report=report, observation=observation, resolve_source=True), "input_size": input_size,
              "status": overall, "diagnostics": diagnostics, "checks": _checks_doc(status, level, extra)}
    _attach_local(result, diagnostics)
    return result


def _failed_report(genotype: Genotype, rows: list[list[Any]], report: BuildReport, input_size: int,
                   status: dict[str, str], level: dict[str, str | None], extra: dict[str, dict[str, Any]],
                   observation: dict[str, Any]) -> dict[str, Any]:
    """FAIL/UNVERIFIED result of a report that did not complete; attributes the failing row to the rule it violates."""
    unavailable = _environment_failure(report.error)
    code = "STRUCTURE_RUNTIME_UNAVAILABLE" if unavailable else _failure_code(report.error)
    stage_level = "forward" if report.stage == "forward" or report.failure_kind == "forward" else "built"
    raw_row = report.error_row if isinstance(report.error_row, int) and 0 <= report.error_row < len(rows) else None
    diagnostics = [_diagnostic(code, _runtime_message(report.error),
                               "Restore the configured runtime and rerun." if unavailable else "Correct this block's module, routing, channels, or merge inputs and rerun.",
                               report.error_row, _path_of(genotype, raw_row) if raw_row is not None else None,
                               evidence_level=stage_level)]
    if unavailable:
        status["cpu_build"] = "NOT_CHECKED"
        return {**describe_genotype(genotype, resolve_source=True), "input_size": input_size, "status": "UNVERIFIED",
                "diagnostics": diagnostics, "checks": _checks_doc(status, level)}
    kind, ctx, row = report.failure_kind, report.error_context, report.error_row
    extra_effective: dict[str, Any] | None = None
    if kind == "forward":
        status["module_support"] = "PASS"  # the parser produced every row; the failure is in the forward loop
        extra["module_support"] = {"rows_checked": len(rows)}
        status["cpu_build"] = "PASS" if report.stage == "forward" else "FAIL"
        # A constructor (stride probe) failure means the reference-size forward never ran: it is not claimed as failed.
        status["reference_forward"] = "FAIL" if report.stage == "forward" else "NOT_CHECKED"
        if isinstance(row, int) and 0 <= row < len(rows):
            name, path = _name(rows[row][2]), _path_of(genotype, row)
            inputs = (ctx or {}).get("inputs") or []
            if ctx and ctx.get("row") == row:
                extra_effective = {"inputs": inputs, "observed_at_failure": True}
            if name in MERGE_MODULES:
                seen = _merge_cause(name, rows[row][3], inputs)
                cause = (f"{seen} (shapes of the failing forward pass, which ran at "
                         f"{'the model constructor stride probe' if report.stage == 'build' else 'the reference input size'})"
                         if seen else f"{name} could not merge its sources in the reference forward: {_runtime_message(report.error)}")
                diagnostics.append(_diagnostic("MERGE_INPUT_MISMATCH", cause,
                                               "Give every merged source the same spatial size (and channels for Add/Sum).",
                                               row, path, "forward"))
                status["merge_conditions"] = "FAIL"
                extra["merge_conditions"] = {"rows_checked": 1}
            elif name in DETECTION_MODULES:
                wrapped = (ctx or {}).get("module_class") == "Sequential"
                cause = ("a detection head row with repeats > 1 is built as nn.Sequential and cannot take its list of sources; "
                         if wrapped else "the detection head could not process its input sources; ") + _runtime_message(report.error)
                diagnostics.append(_diagnostic("HEAD_INPUT_INVALID", cause[:600],
                                               "Declare the head once (repeats 1) and feed it one source per scale.",
                                               row, path, "forward"))
                status["head_connection"] = "FAIL"
                extra["head_connection"] = {"rows_checked": 1}
            elif code == "SHAPE_MISMATCH":
                status["channels_spatial_stride"] = "FAIL"
                extra["channels_spatial_stride"] = {"rows_checked": 1}
    elif kind == "parse":
        status["module_support"] = "FAIL"
        status["cpu_build"] = "FAIL"
    else:  # unknown origin (e.g. a report supplied by another producer): v1 behaviour
        status["cpu_build"] = "FAIL"
    result = {**describe_genotype(genotype, observation=observation, resolve_source=True), "input_size": input_size, "status": "FAIL",
              "diagnostics": diagnostics, "checks": _checks_doc(status, level, extra)}
    if extra_effective is not None and isinstance(row, int) and 0 <= row < len(result["blocks"]):
        result["blocks"][row]["effective"].update(extra_effective)
    _attach_local(result, diagnostics)
    return result


def compare_structures(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """Exact positional structure diff; it makes no unsupported gene ancestry claim."""
    a, b = left.get("blocks", []), right.get("blocks", [])
    changed, added, removed = [], [], []
    for index in range(max(len(a), len(b))):
        if index >= len(a):
            added.append(b[index]["id"])
        elif index >= len(b):
            removed.append(a[index]["id"])
        else:
            keys = ("module", "from", "repeats", "args")
            fields = [key for key in keys if a[index].get(key) != b[index].get(key)]
            if fields:
                changed.append({"position": index, "left_id": a[index]["id"], "right_id": b[index]["id"], "fields": fields})
    left_edges = {(str(e["source"]), str(e["target"]), str(e.get("from_ref"))) for e in left.get("edges", [])}
    right_edges = {(str(e["source"]), str(e["target"]), str(e.get("from_ref"))) for e in right.get("edges", [])}
    return {"comparison": "structure", "alignment": "yaml_position", "gene_ancestry_inferred": False,
            "added": added, "removed": removed, "changed": changed,
            "edges_added": [list(edge) for edge in sorted(right_edges - left_edges)],
            "edges_removed": [list(edge) for edge in sorted(left_edges - right_edges)]}

"""One capability-gated NAS pool; architecture labels are audits, never quotas.

Runtime imports are lazy to avoid registration/runtime circular imports. The installer
must extend runtime's module taxonomy/spatial rules before calling this module.
"""
import copy
import math
import os
from collections import Counter


CHOICES = ('C2f', 'C3k2', 'C3TR', 'C2PSA', 'A2C2f',
           'NASWindowTransformer', 'NASGlobalTransformer')
REPEAT_CHOICES = set(CHOICES)
ATTENTION_NAMES = {'PSA', 'C2PSA', 'C2fPSA', 'C2fAttn', 'C3TR',
                   'NASWindowTransformer', 'NASGlobalTransformer', 'TransformerBlock'}
PURE_NAMES = {'NASWindowTransformer', 'NASGlobalTransformer', 'NASPatchMerge'}


def mixed_enabled():
    """Only the explicit environment value 1 enables mixed operator mutation."""
    return os.environ.get('ETRI_NAS_MIXED', '0') == '1'


def _name(line):
    return str(line[2]).split('.')[-1]


def _attention(line):
    name = _name(line)
    args = line[3] if len(line) > 3 else []
    if name == 'C3k2':
        return bool(args[3]) if len(args) > 3 else False  # c_out,c3k,e,attn
    if name == 'A2C2f':
        return bool(args[1]) if len(args) > 1 else True
    return name in ATTENTION_NAMES


def audit_config(cfg):
    """Config estimate of backbone composition; actual built-model audit is separate."""
    counts = Counter(_name(line) for line in cfg.get('backbone', []))
    rows = cfg.get('backbone', [])
    has_attention = any(_attention(line) for line in rows)
    all_pure = bool(rows) and all(_name(line) in PURE_NAMES for line in rows)
    family = 'transformer_backbone' if all_pure else ('hybrid_backbone' if has_attention else 'cnn_backbone')
    return {'scope': 'backbone_config_estimate', 'family': family,
            'operator_counts': dict(sorted(counts.items())),
            'attention_rows': sum(_attention(line) for line in rows),
            'head_preserved': True, 'proof_of_model_family': False,
            'selection_policy': 'single mixed pool; no family quotas'}


def _annotate(cfg):
    cfg['__mixed_audit__'] = audit_config(cfg)
    return cfg


def _available():
    from ultralytics.nn import tasks
    from .mixed_blocks import register_ultralytics_blocks
    register_ultralytics_blocks()
    return {name for name in CHOICES if isinstance(getattr(tasks, name, None), type)}


def _input_shape(line, index, nodes, image_size):
    route = line[0]
    if not isinstance(route, int):
        return None  # These repeat feature operators consume one feature tensor.
    source = index + route if route < 0 else route
    if source == -1 and index == 0:
        return 3, image_size, image_size
    if source < 0 or source >= index or source >= len(nodes):
        return None
    node = nodes[source]
    return node.sym_channels, node.sym_h, node.sym_w


def _legal_choices(available, c1, c2, h, w):
    if any(value is None for value in (c1, c2, h, w)):
        return []
    tokens = int(h) * int(w)
    hidden = int(c2 * .5)
    choices = []
    for name in CHOICES:
        if name not in available:
            continue
        if name == 'C2PSA':
            heads = max(hidden // 64, 1)
            if c1 != c2 or c2 < 128 or tokens > 1024 or hidden % heads:
                continue
        if name == 'C3TR' and (tokens > 1024 or hidden % 4):
            continue
        if name == 'A2C2f' and (c2 < 64 or hidden % 32 or tokens > 1024):
            continue  # default a2=True,area=1,residual=False
        if name == 'NASGlobalTransformer' and tokens > 1024:
            continue
        choices.append(name)
    return choices


def mutate_operators(cfg, rng, rate=.35, all_slots=False):
    """Return (deep copy, repair records), preserving routes, sizes and head globals.

    all_slots=False: with probability rate mutate one randomly selected eligible
    slot. all_slots=True: independently attempt each eligible slot with probability
    rate; rate=1 resamples every slot. Choices share one pool, without family quotas.
    """
    if not math.isfinite(rate) or not 0 <= rate <= 1:
        raise ValueError('mutation rate must be finite and lie in [0,1]')
    result = copy.deepcopy(cfg)
    if not mixed_enabled():
        return result, []
    from . import runtime as R
    available = _available()
    image_size = int(os.environ.get('ETRI_NAS_MIXED_IMAGE_SIZE', '640'))
    nodes, failures, _ = R.symbolic_shape_propagate(result, 'mixed_mutation', image_size)
    if failures:
        return result, [{'rule': 'mixed_mutation_skipped', 'reason': 'symbolic_trace_failed',
                         'failures': [getattr(f, 'code', str(f)) for f in failures]}]
    backbone = result.get('backbone', [])
    rows = backbone + result.get('head', [])
    eligible = []
    repeat_ops = set(R.REPEAT_MODULES) | REPEAT_CHOICES
    for index, line in enumerate(rows):
        if not isinstance(line, list) or len(line) < 4 or _name(line) not in repeat_ops:
            continue
        if not isinstance(line[3], list) or not line[3] or not isinstance(line[3][0], (int, float)):
            continue
        source_shape = _input_shape(line, index, nodes, image_size)
        if source_shape is None:
            continue
        c1, h, w = source_shape
        legal = _legal_choices(available, c1, nodes[index].sym_channels, h, w)
        legal = [name for name in legal if name != _name(line)]
        if legal:
            eligible.append((index, legal, c1, nodes[index].sym_channels, h, w))
    chosen = eligible if all_slots else ([rng.choice(eligible)] if eligible else [])
    repairs = []
    for index, legal, c1, c2, h, w in chosen:
        if rng.random() >= rate:
            continue
        line = rows[index]
        before = copy.deepcopy(line)
        line[2] = str(rng.choice(legal))
        line[3] = [before[3][0]]  # native/custom safe defaults; no guide tensor extras
        repairs.append({'rule': 'mixed_operator_mutation', 'stage': index,
                        'scope': 'backbone' if index < len(backbone) else 'neck_head',
                        'before': before, 'after': copy.deepcopy(line),
                        'effective_input_channels': c1, 'effective_output_channels': c2,
                        'input_hw': [h, w], 'eligible_operators': legal})
    return _annotate(result), repairs


def make_mixed_seed_variants(cfg):
    """Native, CNN-backbone, and transformer-backbone copies enter the SAME pool.

    Transformer-backbone variants preserve the native neck/head, nc, scale and all
    routes. Therefore this is not a claim of a convolution-free entire detector.
    Unsupported spatial/backbone operators cause an explicit rejection.
    """
    from . import runtime as R
    available = _available()
    from ultralytics.nn import tasks
    if 'NASWindowTransformer' not in available or not isinstance(getattr(tasks, 'NASPatchMerge', None), type):
        raise RuntimeError('Mixed seed variants require registered NASWindowTransformer/NASPatchMerge')
    if 'C2f' not in available:
        raise RuntimeError('Installed Ultralytics parser does not expose C2f')
    native = copy.deepcopy(cfg)
    cnn = copy.deepcopy(cfg)
    pure = copy.deepcopy(cfg)
    for index, line in enumerate(cnn.get('backbone', [])):
        if _attention(line):
            if len(line) < 4 or not line[3]:
                raise ValueError(f'Attention backbone row {index} lacks c_out')
            line[2], line[3] = 'C2f', [line[3][0]]
    repeat_ops = set(R.REPEAT_MODULES) | REPEAT_CHOICES
    for index, line in enumerate(pure.get('backbone', [])):
        if not isinstance(line, list) or len(line) != 4 or not isinstance(line[0], int):
            raise ValueError(f'Unsupported transformer-backbone route/row at {index}: {line}')
        name, args = _name(line), line[3]
        if not isinstance(args, list) or not args or not isinstance(args[0], (int, float)):
            raise ValueError(f'Unsupported transformer-backbone arguments at {index}: {line}')
        c_out = args[0]
        if name == 'Conv':
            kernel = args[1] if len(args) > 1 else 1
            stride = args[2] if len(args) > 2 else 1
            padding = args[3] if len(args) > 3 else None
            if stride not in (1, 2) or line[1] != 1 or not isinstance(kernel, int) or kernel % 2 == 0:
                raise ValueError(f'Nonstandard Conv cannot preserve transformer-backbone geometry at {index}')
            if padding is not None and padding != kernel // 2:
                raise ValueError(f'Explicit Conv padding is not equivalent to patch merge at {index}')
            line[2], line[3] = 'NASPatchMerge', [c_out, stride]
        elif name in repeat_ops or name in {'SPP', 'SPPF', 'PSA'}:
            line[2], line[3] = 'NASWindowTransformer', [c_out]
        elif name == 'NASPatchMerge':
            if len(args) < 2 or args[1] not in (1, 2) or line[1] != 1:
                raise ValueError(f'Unsupported existing NASPatchMerge at {index}')
        else:
            raise ValueError(f'Unsupported transformer-backbone operator {name} at {index}; seed rejected')
    for name, variant in [('native', native), ('cnn_backbone', cnn), ('transformer_backbone', pure)]:
        variant['__mixed_seed_variant__'] = name
        _annotate(variant)
    return {'native': native, 'cnn_backbone': cnn, 'transformer_backbone': pure}

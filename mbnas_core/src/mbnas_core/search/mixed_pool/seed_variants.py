"""ETRI mixed seed variants (vendored, DD-A12; WP-1 contract section 6.4): one mixed pool, labels are audits.

VENDORED_FROM: ``etri_nas/yolo_ga_runtime/mixed_search_space.py`` (sha256
adb6f41302a8ee8b526533edff9b52c916479c93ed08e63173b43241012e5da7, HEAD 18c481517d4393f5d7818aba5b37d721e15628c5),
lines 12-17 (``CHOICES``, ``REPEAT_CHOICES``, ``ATTENTION_NAMES``, ``PURE_NAMES``), 25-62 (``_name``, ``_attention``,
``audit_config``, ``_annotate``, ``_available``) and 154-203 (``make_mixed_seed_variants``), byte for byte except two
import lines: the parser extension comes from :mod:`mbnas_model_runtime.operators` (was ``.mixed_blocks``) and
``R.REPEAT_MODULES`` reads :mod:`mbnas_core.search.mixed_pool.runtime_constants` (the effective set the original
``yolo_ga_runtime.runtime`` held after its import-time hooks). ``mixed_enabled``, ``_input_shape``, ``_legal_choices``
and ``mutate_operators`` are not ported (unreachable from the server; ``_legal_choices`` lives on as
``search.space.illegal_reason``); the originals are kept under ``docs/internal/provenance/vendored_reference/``.
The sha256 of THIS file is part of the search-space identity (``mbnas-mixed-space/3``), so edit it only on purpose.
"""
import copy
from collections import Counter


CHOICES = ('C2f', 'C3k2', 'C3TR', 'C2PSA', 'A2C2f',
           'NASWindowTransformer', 'NASGlobalTransformer')
REPEAT_CHOICES = set(CHOICES)
ATTENTION_NAMES = {'PSA', 'C2PSA', 'C2fPSA', 'C2fAttn', 'C3TR',
                   'NASWindowTransformer', 'NASGlobalTransformer', 'TransformerBlock'}
PURE_NAMES = {'NASWindowTransformer', 'NASGlobalTransformer', 'NASPatchMerge'}


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
    from mbnas_model_runtime.operators import register_ultralytics_blocks
    register_ultralytics_blocks()
    return {name for name in CHOICES if isinstance(getattr(tasks, name, None), type)}


def make_mixed_seed_variants(cfg):
    """Native, CNN-backbone, and transformer-backbone copies enter the SAME pool.

    Transformer-backbone variants preserve the native neck/head, nc, scale and all
    routes. Therefore this is not a claim of a convolution-free entire detector.
    Unsupported spatial/backbone operators cause an explicit rejection.
    """
    from mbnas_core.search.mixed_pool import runtime_constants as R
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

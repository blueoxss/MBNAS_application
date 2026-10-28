"""Effective ``REPEAT_MODULES`` of the ETRI mixed pool (vendored literal, DD-A12; WP-1 contract section 6.4).

The only ``yolo_ga_runtime.runtime`` value the seed variants read (``make_mixed_seed_variants`` uses
``R.REPEAT_MODULES``) is the repeat-module set AFTER the import-time hook ``mixed_hooks.install_runtime_hooks()``
extended it. Source (etri_nas HEAD 18c481517d4393f5d7818aba5b37d721e15628c5, statically recomputed in
docs/internal/analysis/ETRI_RUNTIME_SCOPE.md section 3.3 / 5.2):

* ``yolo_ga_runtime/runtime.py`` L60-63 (14 names; sha256 f538c50f415cf0626d70a8be5b3d25550077983451202e2244a8d7423fd045e1)
* union ``yolo_ga_runtime/mixed_hooks.py`` L70 ``CUSTOM_REPEATS | {"A2C2f"}`` (L14; 3 names;
  sha256 297511c927e1c6e192d9ebebcebce2b7977d33c00cc39aaf7dacce580cb9deb7)

The 17 names equal ultralytics 8.4.173 ``parse_model`` ``repeat_modules`` (15) plus the two ETRI repeat operators.
"""
from __future__ import annotations

# yolo_ga_runtime/runtime.py L60-63 (14) | yolo_ga_runtime/mixed_hooks.py L70 CUSTOM_REPEATS | {"A2C2f"} (3)
REPEAT_MODULES = frozenset({
    "BottleneckCSP", "C1", "C2", "C2f", "C3k2", "C2fAttn", "C3", "C3TR",
    "C3Ghost", "C3x", "RepC3", "C2fPSA", "C2fCIB", "C2PSA",
    "A2C2f", "NASWindowTransformer", "NASGlobalTransformer",
})

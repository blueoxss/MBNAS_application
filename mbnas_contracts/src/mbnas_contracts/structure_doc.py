"""Document constants of the structural inspection report (contract component ``StructureReport``,
``mbnas-structure/2``).

``SCHEMA``/``BACKEND``/``REPRESENTATION`` identify the report document; ``CHECK_NAMES`` are its checks in report
order; ``EVIDENCE_LEVELS`` is the evidence vocabulary and ``CHECK_EVIDENCE`` the evidence level of each check when it
has a rule of its own. The rules themselves (the structure gate) are not here; both the YAML-genotype inspector and the
native-backend observer emit reports with these constants.

Source: ``mbnas_server.search.structure`` (L29-31, L38-44; monolithic package, snapshot HEAD 18c4815), moved per WP-1
contract §4 / DD-A10; values unchanged.
"""

SCHEMA = "mbnas-structure/2"
BACKEND = "ultralytics"
REPRESENTATION = "yaml_genes"
CHECK_NAMES = ("yaml_rows", "references", "class_count", "scales", "module_support", "cpu_build",
               "reference_forward", "channels_spatial_stride", "merge_conditions", "head_connection")
EVIDENCE_LEVELS = ("declared", "built", "forward", "derived_from_forward")
# evidence level of each check when it has a rule of its own (None while NOT_CHECKED)
CHECK_EVIDENCE = {"yaml_rows": "declared", "references": "declared", "class_count": "declared", "scales": "declared",
                  "module_support": "built", "cpu_build": "built", "reference_forward": "forward",
                  "channels_spatial_stride": "forward", "merge_conditions": "forward", "head_connection": "forward"}

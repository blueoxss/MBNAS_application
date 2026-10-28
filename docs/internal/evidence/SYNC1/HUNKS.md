# SYNC-1 port record (delta 3 = SOURCE_SNAPSHOT_DELTA_20261026T194059.jsonl, live HEAD 18c48151, 46 files)

Pre-delta snapshot bytes kept in the session scratchpad; pre->live diffs: live_diffs/*.diff. Target edits: target_python_ported.diff.
sha256 before/after per file: delta_list.tsv.

| live file | target | result |
|---|---|---|
| search/structure.py | mbnas_core/src/mbnas_core/search/structure.py | 6/6 hunks applied (patch, offset -7, no fuzz) |
| api/app.py hunk 1 (version 1.4.1->1.4.3) | mbnas_core/src/mbnas_core/api/app.py | applied as API_VERSION = "1.4.3" (DD-A17 constant; live literal pre-delta was 1.4.1 vs target 1.4.2) |
| api/app.py hunk 2 (ArchiveStore campaign_roots) | mbnas_server/src/mbnas_server/api/app.py | already present (DD-A23), no change |
| search/block_catalog.py (5 hunks, lazy _tables()/__getattr__ refactor) | mbnas_model_runtime/src/mbnas_model_runtime/block_catalog.py | NOT ported: structural refactor only; live table data == pre-delta data (executed, block_catalog_table_equality.txt); target keeps the designer decision _build_tables() at import (WP-1 SUMMARY designer round). Target-vs-live data differences are only the pre-existing WP-1C reference-path edits |
| tests/search/test_structure_{adversarial,cpu,observation,runtime_cpu}.py | tests/owner/search/same | all hunks applied |
| tests/unit/test_schemas_contract.py | tests/owner/unit/test_schemas_contract.py | 2/2 hunks applied (+ relocated drift guard test) |
| tests/unit/test_contract_history.py | tests/public/contracts/test_contract_history.py | applied; drift guard over owner source relocated to the owner test (WP2/TEST_ADAPTATIONS.md) |
| tests/archive/test_campaign_adapter.py | tests/public/server/test_campaign_adapter.py | patch context conflict (WP-1 A5 edits); ported by hand: skip guard removed, docstring |
| tests/api/test_registry_structure.py | tests/owner/api/test_registry_structure.py | NOT ported: MBNAS_TEST_ETRI_ROOT does not exist in the split layout (DD-A12/A13) |
| tests/api/test_error_trace_id.py (new) | tests/owner/api/test_error_trace_id.py | relocated, logger name adapted |
| contracts/openapi.json, history/HISTORY.json, openapi-1.4.3.json, migrate_1.4.2_to_1.4.3.py (new) | mbnas_contracts openapi/{openapi.json,HISTORY.json}, docs/public/contracts/history/{HISTORY.json,openapi-1.4.3.json,migrate_1.4.2_to_1.4.3.py} | live bytes |
| history/openapi-1.4.2.json (new in manifest) | docs/public/contracts/history/openapi-1.4.2.json | already present, identical sha256 9a17ae5d... |
| web: 16 changed files + 2 new tests | mbnas_web/same | straight copy; every target file equalled the pre-delta bytes; none of the 5 WP-1H/V files changed live -> 0 conflicts |
| schemas/*.py | mbnas_contracts/schemas | no live change in delta 3 -> nothing to port |
| application/docs/** (13 files) | snapshot only | no port |

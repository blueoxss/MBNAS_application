# WP-3A core evidence (resume pass 2026-10-27)

Files: `sandbox_rlimit_as.txt` (RLIMIT_AS measurement, owner host, sandboxed worker), `test_wp3a_scratch.py` (23 scratch
self-checks), `scratch_conftest.py` (loader: exec()s tests/owner/conftest.py + tests/owner/api/conftest.py; copy both to
a scratch dir as conftest.py + the test file), `scratch_run.txt` (last run: 23 passed).

RLIMIT_AS default 8 GiB: smallest passing limit measured was 5 GiB (yolo11n 640, rf-detr-large) and 6 GiB (yolo11x and
rt-detr-x at 1280 = structure_check_max_input); 4-5 GiB fails at build/forward. 8 GiB = 6 GiB + headroom.

Deviations from the sub-task text (designer decision requested):
1. `validation_host: "gateway"` is recorded in `capabilities`, not `resolution`: `protocol_hash` covers `resolution`,
   so a host marker there would hash the same protocol differently on gateway vs core-local validation.
2. Activation guard: kept in `validate_declared` (runs on the declared dict before anything is built, in all three
   check entry points and bind_structure) and in `check_structure`, not as an exception in `Genotype` construction:
   raising there would break tests/owner/search/test_structure_adversarial.py::test_activation_expression_never_reaches_parser
   and tests/owner/api/test_training_selection.py::test_unsafe_user_activation_is_rejected, which require an
   ACTIVATION_REFUSED diagnostic (rule 4: no assertion weakening).

Resume fixes: worker preloads torch/ultralytics under the limits so a dlopen refused under RLIMIT_AS ("failed to map
segment") maps to STRUCTURE_CHECK_RESOURCE_LIMIT instead of STRUCTURE_RUNTIME_UNAVAILABLE; scratch SSE test moved to a
real uvicorn thread (TestClient blocked on the endless stream: the >20 min stall); scratch copy removed from tests/;
PLACEMENT_MANIFEST rows for the 5 new core files; DD-A37 wire format documented in protocol/gateway_inputs.py.

# WP-2B contracts evidence
cwd /home/mgjeong/Desktop/etri_hd/mbnas_application; interpreter runtime/venv/bin/python -B; CUDA_VISIBLE_DEVICES="" MBNAS_HOME=runtime MBNAS_TEST_ROOT=runtime/test_root
- `scripts/dev/gen_runner_openapi.py` exit 0 (wrote openapi_runner.json); `--check` exit 0 via test.
- `pytest tests/public/contracts/test_runner_protocol.py -q`: 103 passed, 0.52 s, exit 0.
- `pytest tests/public/contracts tests/public/infra -m "not gpu and not live and not legacy_records and not host_data"`: 219 passed, 13.3 s.
- `scripts/dev/check_import_closure.py`: exit status recorded below.
- check_import_closure.py: exit 0, violations 0, unresolved 0, forbidden_strings 0

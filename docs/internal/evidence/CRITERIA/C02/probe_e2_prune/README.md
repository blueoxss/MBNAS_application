# C02 EVAL_2 probe — runner-route canary coverage for runner protocol 1.1.0 paths

Preserved copy of the evaluator's probe (2026-10-28 10:3x KST, tree f00987be…): heartbeat `control.prune` orders carry exactly the 8 PruneOrder keys and E11 responses carry 0 owner-only markers. C02 D8 (Minor): the project test tests/owner/projection/test_runner_route_canary.py does not yet list the 1.1.0 paths in its required-path set — port this probe in 1.5.1 (RELEASE_1_5_1_CARRYOVER.md). Owner-only evidence; token-pattern files were excluded from the copy.

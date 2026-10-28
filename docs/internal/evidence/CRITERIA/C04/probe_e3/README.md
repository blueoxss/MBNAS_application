# C04 EVAL_3 evaluator probes (preserved copy)

Copied from the evaluator's scratchpad on 2026-10-28 10:2x KST. These 6 probes (planning failure isolation, delivery failure keeps PENDING, E11 rollback + single retry, cancelled-run order cancellation, stale-attempt heartbeat 409, late report after cancel) passed against tree `f00987be…` but are NOT part of the project test suite. DD-A70: porting them into `tests/owner/runner_protocol/` is a 1.5.1 item (C04 M1); `ROOT` is hard-coded to the evaluator's checkout and must be replaced by the repo-relative fixture when ported. Owner-only evidence.

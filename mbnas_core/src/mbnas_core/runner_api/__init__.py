"""Remote runner control plane of the core (``/core/v1/runner/*``).

Modules: :mod:`.routes` (E1-E10), :mod:`.envelope` (DB rows -> JobEnvelope v1), :mod:`.fence` (lease fencing),
:mod:`.settle` (attempt settlement shared with the owner worker), :mod:`.sweeper` (expired-lease reclaim thread),
:mod:`.auth` (scoped principals), :mod:`.tickets` (HMAC job tickets). Private to the core; the wire
models are public in :mod:`mbnas_contracts.runner_protocol`.
"""

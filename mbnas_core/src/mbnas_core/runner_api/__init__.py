"""Remote runner control plane of the core (``/core/v1/runner/*``, WP-2 contract section 1.3; design A3).

Modules: :mod:`.routes` (E1-E10), :mod:`.envelope` (DB rows -> JobEnvelope v1), :mod:`.fence` (lease fencing),
:mod:`.settle` (attempt settlement shared with the owner worker), :mod:`.sweeper` (expired-lease reclaim thread),
:mod:`.auth` (scoped principals), :mod:`.tickets` (HMAC job tickets). Private to the core (DD-A1(a)); the wire
models are public in :mod:`mbnas_contracts.runner_protocol`.
"""

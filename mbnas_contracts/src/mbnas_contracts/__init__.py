"""MBNAS Research Console shared contracts (public): DTO schemas, OpenAPI contract data, job-protocol types and stdlib
helpers. Third-party imports are limited to pydantic and PyYAML, and no other MBNAS package is imported (DD-A10).

Module provenance (monolithic package ``mbnas_server``, snapshot HEAD 18c4815; per-file sha256 in the placement
manifest). Moved: ``schemas.*`` <- ``schemas.*``; ``util.{atomic,canon,log_redact,paths,time}`` <- ``util.*``.
Split or assembled (each module's docstring names its source): ``jobs_protocol`` <- ``worker.contract`` (types only);
``errors`` <- ``api.errors`` (framework-free part); ``asgi`` <- ``api.limits`` + ``api.auth.RequestIdMiddleware``;
``openapi`` <- ``api.contract`` (package data ``openapi.json``/``HISTORY.json`` <- ``contracts/``);
``util.source_hash`` <- ``provenance.source_hash``; ``protocol_hash`` <- ``protocol.validator``; ``constants`` <-
``protocol.presets``, ``protocol.validator``, ``benchmark.capability``; ``structure_doc`` <- ``search.structure``.
New: ``util.home``.
"""

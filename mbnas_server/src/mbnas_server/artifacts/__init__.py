"""Gateway artifact store, signed URLs and download helper (WP-5A; DD-A6, DD-A11, DD-A38, DD-A39).

* :mod:`mbnas_server.artifacts.store` - content-addressed store ``<root>/<sha256[:2]>/<sha256>`` (+ ``<sha256>.json``
  meta), verified atomic writes, idempotent re-put, size cap, key-only addressing;
* :mod:`mbnas_server.artifacts.signing` - HMAC signed URLs and the contract ``Artifact`` view (ported, scheme unchanged);
* :mod:`mbnas_server.artifacts.download` - ``serve_artifact(store, record) -> (headers, body)`` with the digest re-check.

No web framework is imported here. :class:`StoreError` and :class:`SignatureError` are
:class:`mbnas_contracts.errors.ApiError` subclasses (``location`` store / gateway), so routes can let them reach the
gateway's Error handler unchanged.
"""
from __future__ import annotations

from mbnas_server.artifacts.download import DEFAULT_VERIFY_FIRST_MAX_BYTES, ArtifactBody, serve_artifact
from mbnas_server.artifacts.signing import (
    SignatureError, artifact_path, artifact_view, require_valid_signature, sign, signing_key, verify_signature,
)
from mbnas_server.artifacts.store import (
    DEFAULT_MAX_BYTES, META_KEYS, ArtifactStore, PendingPut, StoredArtifact, StoreError, is_sha256,
)

__all__ = [
    "DEFAULT_MAX_BYTES", "DEFAULT_VERIFY_FIRST_MAX_BYTES", "META_KEYS", "ArtifactBody", "ArtifactStore", "PendingPut",
    "SignatureError", "StoreError", "StoredArtifact", "artifact_path", "artifact_view", "is_sha256",
    "require_valid_signature", "serve_artifact", "sign", "signing_key", "verify_signature",
]

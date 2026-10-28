"""Artifact registry: run-relative files, registry-mediated access and HMAC-signed URLs.

재구현(2026-10-15), 원본 누락(리포 루트 .gitignore의 `artifacts/` 규칙으로 커밋에서 빠짐), 근거: 호출부
(api/routes_files.py, api/views.py, orchestration/engine.py, worker/main.py) 와 tests/api/test_artifacts_export.py.
"""

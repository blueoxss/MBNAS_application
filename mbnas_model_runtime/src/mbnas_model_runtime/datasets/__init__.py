"""Dataset handling: safe data.yaml parsing, split manifests (hashes, label validation), COCO conversion, subsets.

재구현(2026-10-15), 원본 누락(리포 루트 .gitignore의 `datasets/` 규칙으로 커밋에서 빠짐), 근거: docs/orchestration/PLAN.md
(datasets/ 항목), 호출부(protocol/validator.py, training/*, evaluation/*, adapters/rfdetr_adapter.py)와
tests/unit/test_datasets.py, tests/adapters/test_coco_convert.py.
"""

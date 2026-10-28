"""Backend adapters (PLAN §3): ultralytics (YOLO11/YOLO26/RT-DETR), RF-DETR, ETRI controlled hybrid.

Use :func:`mbnas_model_runtime.registry.adapter_for` to get the adapter of a registry entry; heavy frameworks are
imported lazily inside the adapter methods.
"""

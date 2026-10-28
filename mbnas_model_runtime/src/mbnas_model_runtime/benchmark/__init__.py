"""Deployment benchmark: ONNX export per backend, ONNX Runtime (CUDA EP) and TensorRT engines on the host GPU,
latency per scope (same scope definitions as :mod:`mbnas_server.evaluation.latency`) and export accuracy deltas with
the common evaluator. Targets other than the host GPU are reported BLOCKED, never filled with proxy numbers.
"""

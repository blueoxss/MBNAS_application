"""MBNAS server: training API, GPU worker, protocol validator and NAS orchestration.

The API process and the worker process are separate OS processes that share one SQLite (WAL) database;
see docs/orchestration/PLAN.md for the binding module contracts.
"""
__version__ = "0.1.0"

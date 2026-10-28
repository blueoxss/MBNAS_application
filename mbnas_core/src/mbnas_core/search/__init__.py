"""Shared mixed operator search space, GA / IndependentRandom proposers, lineage and the generation handler.

Both arms use one :class:`~mbnas_core.search.space.SearchSpace` (same templates, operator pool, frozen prior and
repair); they differ only in the proposal method. Heavy dependencies (torch, ultralytics, the read-only ETRI mixed
blocks) are imported lazily inside functions so the API process can import this package without them.
"""

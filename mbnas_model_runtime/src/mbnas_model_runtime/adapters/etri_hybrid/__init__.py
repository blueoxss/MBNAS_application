"""ETRI controlled CNN+Transformer hybrid detector (not SOTA, not pure Transformer).

Model/loss/data/postprocess code is extracted verbatim from the ETRI notebook (see each module's provenance header);
:mod:`.adapter` adds MBNAS's own deterministic training loop with full-state checkpoints and exact resume.
"""
FAMILY_LABEL = "ETRI controlled CNN+Transformer hybrid (not SOTA, not pure Transformer)"
NOTEBOOK_BLOB_SHA1 = "629ce856b4c6ea337edc8943b9e65ed91ef60e27"
#: ETRI_NAS commit at which the notebook cells were audited and extracted (see each module's provenance header).
ETRI_AUDITED_HEAD = "1d3a459aa6356e11b43c0e58bc778030ba78f950"
NOTEBOOK_CELLS = {"data": 6, "boxes": 8, "model": 10, "loss": 12, "postprocess": 14}

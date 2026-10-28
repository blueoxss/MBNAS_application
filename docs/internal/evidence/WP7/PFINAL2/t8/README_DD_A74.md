# T8 owner static-scan dumps (DD-A74)

`zip_leak_scan_owner.json` / `zip_leak_scan_owner.txt` in this directory are the full match listings of the T8 static
scan of an owner ZIP. They are tens of MB (owner_c: 30,314,242 + 18,821,104 bytes, about 98k host-fact matches each)
because the owner ZIP carries `docs/internal/**`, so every scan re-matches the dumps of the previous cycle and the files
grow with each build (2.8 → 9.9 → 30 MB). They are kept here on the build host but are **excluded from the owner ZIP**
by the `[deny]` rule `docs/internal/evidence/**/t8/zip_leak_scan_owner.*` in `build/allowlists/owner.txt` (DD-A74,
raised by C10 EVAL_2 D8). The scan results themselves are summarised in `docs/internal/evidence/WP7/PFINAL2/SUMMARY.md`
(owner: 0 canary hits; 38 owner-only legacy host-fact hits in 12 files outside `docs/internal`, DD-A72; 25 static-rescan
items) and in the REVIEW_LOG rows for P-final-2. The per-match line-number computation in `scripts/dev/canary_scan.py`
that makes scanning such files quadratic is a 1.5.1 carry-over, not changed in 0.1.0.

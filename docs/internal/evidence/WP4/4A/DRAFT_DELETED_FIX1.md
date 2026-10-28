# WP-4A draft contract deleted (fix round 1, item 4C-4) - 2026-10-28 KST

Deleted from the working tree (no git command was run; dropping the path from the git index is the user's step):

| path | bytes | sha256 | mtime |
|---|---|---|---|
| `mbnas_contracts/src/mbnas_contracts/openapi/drafts/openapi-1.5.0.draft.json` | 350696 | `70edb8ef0628f7e49bc96ac67000c520036b5e29d9e2b76439cc6e8f1d7f9d17` | 2026-10-27 13:36:29 KST |
| `mbnas_contracts/src/mbnas_contracts/openapi/drafts/` (directory, empty after the file) | - | - | - |

Why: this was the WP-4A draft of contract 1.5.0 (checked by `check_draft_1_5_0.py` in this directory). WP-4B promoted
1.5.0 (DD-A53), so the draft is superseded:

- the promoted contract is `mbnas_contracts/src/mbnas_contracts/openapi/openapi.json`, sha256
  `70e0cffaab8fafd9c7f230af9169dbbe8812b3eccf7ad7838ca3f46fa9eebc2a` (the DD-A53 value), byte-identical to the history
  copy `docs/public/contracts/history/openapi-1.5.0.json`;
- the package `HISTORY.json` lists 1.4.2, 1.4.3, 1.5.0 under `versions` and has no `drafts` entry (4B moved `drafts[0]`
  into `versions`);
- the draft was never package data (`mbnas_contracts/pyproject.toml`: `openapi.json`, `HISTORY.json`,
  `openapi_runner.json`).

The draft is not identical to the promoted contract (the parsed JSON differs: 4B changed it while promoting). Its exact
bytes stay recoverable from this repository's git history: the path is tracked at HEAD `3e5bbecb9`
(`git show 3e5bbecb9:mbnas_contracts/src/mbnas_contracts/openapi/drafts/openapi-1.5.0.draft.json`).

References at deletion time (grep over the tree, excluding `build/source_snapshot`, `runtime/`, `node_modules`):

- this directory: `check_draft_1_5_0.py` (default input path), `check_draft_1_5_0.result.json` (records the path),
  `ts_lint/validate_ts.sh` (default input path) - historical 4A evidence; re-running them needs the draft restored from
  git first;
- `tests/owner/projection/projection_world.py` `DRAFT_CONTRACT`: read only by `contract_150()` when the live contract is
  not 1.5.0, which is never the case after the promotion (left unchanged);
- no package source, build allowlist, script or other test reads it.

Checks after the deletion: see `docs/internal/evidence/WP7/FIX1/README.md` (projection suite with the live 1.5.0
contract, import-closure checker, install smoke).

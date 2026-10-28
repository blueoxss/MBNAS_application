#!/usr/bin/env bash
# WP-4A: openapi-typescript generation, Redocly structural lint and a strict tsc probe against the 1.5.0 draft.
# Writes only into a fresh temporary directory (never into mbnas_web). Usage: bash validate_ts.sh [contract.json]
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../../../../.." && pwd)"
DRAFT="${1:-$ROOT/mbnas_contracts/src/mbnas_contracts/openapi/drafts/openapi-1.5.0.draft.json}"
WEB="$ROOT/mbnas_web/node_modules"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
export npm_config_cache="$ROOT/build/npm-cache"
cp "$HERE/use_types.ts" "$TMP/use_types.ts"
cd "$TMP"
"$WEB/.bin/openapi-typescript" "$DRAFT" --empty-objects-unknown -o "$TMP/types.gen.ts"; gen=$?
echo "openapi-typescript exit=$gen sha256=$(sha256sum "$TMP/types.gen.ts" 2>/dev/null | cut -d' ' -f1)"
node "$HERE/redocly_lint.mjs" "$WEB/openapi-typescript" "$DRAFT"; lint=$?
echo "redocly lint exit=$lint"
"$WEB/.bin/tsc" --noEmit --strict --target es2022 --module esnext --moduleResolution bundler --skipLibCheck types.gen.ts use_types.ts; tsc=$?
echo "tsc --strict probe exit=$tsc"
exit $(( gen || lint || tsc ))

#!/usr/bin/env bash
# WP-7D end-to-end evidence on the current tree (writes docs/internal/evidence/WP7/7D/).
set -u -o pipefail
ROOT=/home/mgjeong/Desktop/etri_hd/mbnas_application
EV="$ROOT/docs/internal/evidence/WP7/7D"
S=/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/wp7d
PY="$ROOT/runtime/venv/bin/python"
M="$ROOT/docs/internal/canary/CANARY_MANIFEST.json"
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null PYTHONNOUSERSITE=1
mkdir -p "$EV"
cd "$ROOT" || exit 2
run() {  # run <evidence file> <cmd...>: echo the command, run it, append exit code
  local out="$1"; shift
  { printf '$'; printf ' %q' "$@"; printf '\n'; "$@"; echo "exit=$?"; } >> "$out" 2>&1
}
stage=${1:-all}

if [ "$stage" = all ] || [ "$stage" = env ]; then
  f="$EV/00_env.txt"; : > "$f"
  { echo "date: $(date -Is)"; echo "host python: $("$PY" -V 2>&1)"; echo "node: $(node --version 2>&1)";
    echo "VERSION: $(cat VERSION)"; } >> "$f"
  run "$f" "$PY" -B scripts/dev/source_tree_hash.py --package mbnas_contracts --package mbnas_model_runtime --package mbnas_runner --package mbnas_server --digest-only
  run "$f" "$PY" -B scripts/dev/source_tree_hash.py --digest-only
  run "$f" ls -la mbnas_web/dist mbnas_web/dist/assets
fi

if [ "$stage" = all ] || [ "$stage" = release ]; then
  for p in public owner registration; do
    f="$EV/10_package_${p}.txt"; : > "$f"
    run "$f" bash scripts/package.sh --profile "$p" --reproducible
    cp "build/package_reports/$p.json" "$EV/10_package_${p}.report.json"
  done
  f="$EV/11_dist_listing.txt"; : > "$f"
  run "$f" ls -la dist/public dist/owner dist/registration
  for p in public owner registration; do
    ( cd "dist/$p" && run "$f" sha256sum -c ./*.sha256 )
  done
fi

if [ "$stage" = all ] || [ "$stage" = scan ]; then
  for p in public registration; do
    f="$EV/20_scan_release_${p}.txt"; : > "$f"
    z=$(ls dist/$p/*.zip)
    run "$f" "$PY" -I -B scripts/dev/canary_scan.py "$z"
    run "$f" "$PY" -I -B scripts/dev/canary_scan.py --manifest "$M" --profile "$p" --min-files 100 --json "$EV/20_scan_release_${p}.json" "$z"
  done
  f="$EV/20_scan_release_owner.txt"; : > "$f"
  z=$(ls dist/owner/*.zip)
  run "$f" "$PY" -I -B scripts/dev/canary_scan.py --manifest "$M" --profile owner --min-files 100 --json "$EV/20_scan_release_owner.json" "$z"
fi

if [ "$stage" = all ] || [ "$stage" = canary ]; then
  for p in public owner registration; do
    f="$EV/30_canary_${p}.txt"; : > "$f"
    run "$f" bash scripts/package.sh --profile "$p" --canary
    cp build/canary/$p/tree/dist/$p/*.canary.json "$EV/30_canary_${p}.scan.json"
  done
  f="$EV/31_canary_manifest_summary.txt"; : > "$f"
  run "$f" "$PY" -I -B -c "import json; d=json.load(open('$M')); [print(a['area'], 'expected_in=', a['expected_in'], 'must_not_in=', a['must_not_in'], 'planted_in=', [p['path'] + (' (created)' if p.get('created') else '') for p in a['planted_in']]) for a in d['areas']]"
  run "$f" "$PY" -I -B scripts/dev/canary_scan.py --manifest "$M" --profile owner --expect-present build/canary/owner/tree/dist/owner/
fi

if [ "$stage" = all ] || [ "$stage" = relocate ]; then
  f="$EV/40_relocation_public.txt"; : > "$f"
  D="$S/relocation test/unzip public (spaces)"
  rm -rf "$S/relocation test"; mkdir -p "$D"
  z=$(ls "$ROOT"/dist/public/*.zip)
  run "$f" unzip -q "$z" -d "$D"
  run "$f" ls -la "$D" "$D/mbnas_application"
  echo "--- files whose path names the private package (expect none):" >> "$f"
  run "$f" find "$D" -iname '*mbnas_core*' -o -iname '*mbnas-core*'
  echo "--- owner-only or excluded material present in the unzip (expect none; tests/public and mbnas_web sources ship by DD-A41):" >> "$f"
  run "$f" find "$D/mbnas_application" \( -path '*/tests/owner' -o -path '*/docs/internal' -o -path '*/deploy/owner' -o -name requirements-owner.txt -o -name 'core.env.example' -o -name 'owner-all-in-one.env.example' -o -name '.env' -o -name '*.env' -o -path '*/mbnas_application/runtime' -o -path '*/mbnas_application/build' -o -name LICENSE_STATUS.md -o -name harness.py -o -name test_no_host_facts.py -o -name node_modules -o -name __pycache__ -o -name '*.pyc' -o -name '*.map' -o -path '*/tests/public/packaging' \)
  echo "--- test and web source trees that do ship (DD-A41):" >> "$f"
  run "$f" find "$D/mbnas_application/tests" "$D/mbnas_application/mbnas_web" -maxdepth 1
  echo "--- wheels (expect the four public wheels only):" >> "$f"
  run "$f" ls "$D/mbnas_application/wheels"
  echo "--- import statements of the private package in unzipped files (expect only the Dockerfile negative assertions):" >> "$f"
  run "$f" grep -rnI -E '(^|[^A-Za-z_])(import|from)[[:space:]]+mbnas_core' "$D/mbnas_application"
  echo "--- FILE_MANIFEST.jsonl vs extracted bytes:" >> "$f"
  run "$f" "$PY" -I -B -c "
import hashlib, json, os, sys
top = sys.argv[1]
rows = [json.loads(l) for l in open(os.path.join(top, 'FILE_MANIFEST.jsonl'), encoding='utf-8')]
bad = [r['relpath'] for r in rows if hashlib.sha256(open(os.path.join(top, r['relpath']), 'rb').read()).hexdigest() != r['sha256']]
on_disk = sorted(os.path.relpath(os.path.join(d, n), top) for d, _, ns in os.walk(top) for n in ns)
listed = sorted([r['relpath'] for r in rows] + ['FILE_MANIFEST.jsonl'])
print('manifest entries', len(rows), 'files on disk', len(on_disk), 'digest mismatches', len(bad), 'unlisted', sorted(set(on_disk) - set(listed)), 'missing', sorted(set(listed) - set(on_disk)))
sys.exit(1 if bad or set(on_disk) != set(listed) else 0)
" "$D/mbnas_application"
  echo "--- scan of the unzipped tree (generic tokens + host facts):" >> "$f"
  run "$f" "$PY" -I -B scripts/dev/canary_scan.py --manifest "$M" --profile public --min-files 100 "$D/mbnas_application"
  echo "--- the relocated BUILD_INFO / PACKAGE_PROFILE (excerpt):" >> "$f"
  run "$f" "$PY" -I -B -c "
import json, sys
b = json.load(open(sys.argv[1] + '/BUILD_INFO.json')); p = json.load(open(sys.argv[1] + '/PACKAGE_PROFILE.json'))
print(json.dumps({k: b[k] for k in ('version', 'profile', 'build_id', 'source_tree', 'payload_sha256', 'web_dist', 'reproducibility')}, indent=1))
print(json.dumps({k: p[k] for k in ('zip', 'packages', 'core_connection_required', 'install')}, indent=1))
" "$D/mbnas_application"
fi

if [ "$stage" = all ] || [ "$stage" = negative ]; then
  f="$EV/50_negative_controls.txt"; : > "$f"
  N="$S/negative_real_copy"
  rm -rf "$N"
  echo "# scratch copy of the real tree (canary_plant.make_tree filter: no runtime/ dist/ build/ except allowlists, no dotenv)" >> "$f"
  run "$f" "$PY" -I -B -c "import importlib.util,sys; s=importlib.util.spec_from_file_location('cp', sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(m.make_tree(__import__('pathlib').Path(sys.argv[2]), source=__import__('pathlib').Path(sys.argv[3])))" scripts/dev/canary_plant.py "$N" "$ROOT"
  echo "# (a) without the noted denies of three public test files that hold host facts: the public build must fail" >> "$f"
  "$PY" -I -B -c "
import sys; p=sys.argv[1]; lines=open(p).read().splitlines(True)
drop=('tests/public/tools/smoke_api_only.py ::', 'tests/public/contracts/test_contract_history.py ::', 'tests/public/infra/test_no_host_facts.py ::')
open(p,'w').write(''.join(l for l in lines if not l.startswith(drop)))" "$N/build/allowlists/public.txt"
  run "$f" "$PY" -I -B scripts/dev/package_build.py --root "$N" --profile public
  cp "$N/build/allowlists/public.txt" "$N/build/allowlists/public.txt.a"
  cp "$ROOT/build/allowlists/public.txt" "$N/build/allowlists/public.txt"
  echo "# (b) a public allowlist that lets owner tests, internal docs and the core env example through: the canary test build must fail" >> "$f"
  "$PY" -I -B -c "
import sys; p=sys.argv[1]; s=open(p).read()
for line in ('tests/owner/**\n', 'docs/internal/**\n', 'config/examples/core*.env.example\n'):
    s = s.replace('\n' + line, '\n', 1)
s = s.replace('[include]\n', '[include]\ntests/owner/**\ndocs/internal/DECISIONS.md\n', 1)
open(p, 'w').write(s)" "$N/build/allowlists/public.txt"
  run "$f" "$PY" -I -B scripts/dev/package_build.py --root "$N" --profile public --canary
  run "$f" grep -c '"check": "canary_token"' "$N/build/canary/public/tree/build/package_reports/public.json"
fi

if [ "$stage" = all ] || [ "$stage" = owner_extracted ]; then
  f="$EV/45_owner_extracted_packaging_tests.txt"; : > "$f"
  O="$S/relocation test/unzip owner (spaces)"
  rm -rf "$O"; mkdir -p "$O"
  run "$f" unzip -q "$(ls "$ROOT"/dist/owner/*.zip)" -d "$O"
  echo "# inside an extracted owner bundle (no build/allowlists): source-tree packaging tests skip, scanner unit tests run" >> "$f"
  ( cd "$O/mbnas_application" && run "$f" "$PY" -B -m pytest tests/public/packaging -q -p no:cacheprovider -rs )
fi

if [ "$stage" = all ] || [ "$stage" = tests ]; then
  f="$EV/60_pytest_packaging.txt"; : > "$f"
  run "$f" "$PY" -B -m pytest tests/public/packaging -v -p no:cacheprovider
  f="$EV/70_import_closure.txt"; : > "$f"
  run "$f" "$PY" -B scripts/dev/check_import_closure.py --format text
  run "$f" "$PY" -B -m pytest tests/public/infra -q -p no:cacheprovider
fi
echo "evidence stage '$stage' done"

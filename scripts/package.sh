#!/usr/bin/env bash
# MBNAS Research Console — build a distribution ZIP (WP-7 contract §1 package.sh row; implementation:
# scripts/dev/package_build.py, allowlists: build/allowlists/<profile>.txt).
#
# Usage: bash scripts/package.sh --profile owner|public|registration [--platform auto|<tag>] [--reproducible]
#                                [--allow-missing-web] [--canary]
#
#   --profile             owner (private, with core) | public (customer runtime) | registration (public + docs)
#   --platform            platform tag of the ZIP name; default auto (this host, e.g. linux_x86_64)
#   --reproducible        fixed timestamps/order/modes, build twice and record the digest comparison in BUILD_INFO
#   --allow-missing-web   build public/registration even when mbnas_web/dist is missing (recorded in BUILD_INFO)
#   --canary              T8 test build: copy the tree to build/canary/<profile>/tree, plant canary tokens there,
#                         build and scan the ZIP (output stays in that tree; dist/ is not touched)
#
# Output: dist/<profile>/mbnas_application-<version>-<profile>-runtime-<platform>.zip (owner:
#         ...-owner-private-runtime-...) + <zip>.sha256; staging build/staging/<profile>/mbnas_application/;
#         wheels build/wheels/; report build/package_reports/<profile>.json (every check with pass/warn/fail/not run,
#         all findings) and build/package_reports/<profile>_wheels.txt (wheel member listing and package-data gate);
#         the check table is printed ("[package] check <id> <STATUS> ..."). Every ZIP carries FILE_MANIFEST.jsonl,
#         PACKAGE_CONTENTS.md (human-readable table of the manifest), BUILD_INFO.json and PACKAGE_PROFILE.json (the
#         wheel listing stays in build/package_reports/; no bundle ships tests, docs/internal or a LICENSE file).
# Exit: 0 built · 1 checks failed (nothing published) · 2 usage/configuration error · 3 build tool failure.
# Needs the build venv runtime/venv (Python 3.10 with pip, setuptools, wheel and the mbnas packages); no network.
set -u -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/runtime/venv/bin/python"

usage() {
  sed -n '5,13p' "$ROOT/scripts/package.sh" | sed 's/^# \{0,1\}//'
}

args=()
profile=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      [ "$#" -ge 2 ] || { echo "package.sh: --profile needs a value" >&2; exit 2; }
      profile="$2"; args+=("--profile" "$2"); shift 2 ;;
    --platform)
      [ "$#" -ge 2 ] || { echo "package.sh: --platform needs a value" >&2; exit 2; }
      args+=("--platform" "$2"); shift 2 ;;
    --reproducible|--allow-missing-web|--canary)
      args+=("$1"); shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "package.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$profile" in
  owner|public|registration) ;;
  "") echo "package.sh: --profile is required" >&2; usage >&2; exit 2 ;;
  *) echo "package.sh: --profile must be owner, public or registration (got: $profile)" >&2; exit 2 ;;
esac

if [ ! -x "$PY" ]; then
  echo "package.sh: build interpreter not found: $PY (create the build venv first)" >&2
  exit 2
fi

export PIP_CONFIG_FILE=/dev/null PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 CUDA_VISIBLE_DEVICES=""
exec "$PY" -I -B "$ROOT/scripts/dev/package_build.py" --root "$ROOT" "${args[@]}"

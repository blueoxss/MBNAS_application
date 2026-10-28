#!/bin/bash
# WP-7V P-final-2 step 7: offline install recheck of the RELEASE public ZIP with the network removed (unshare -rn) and
# the external wheelhouse; from an external cwd; fresh extraction into a new path with spaces. Records pip exit codes.
#  (1) bash scripts/install.sh --profile public --offline --wheelhouse <wh>      (wrapper: venv + pip -c constraints -r requirements-public.txt)
#  (2) standard pip path (DD-A31) offline: python3.10 -m venv <v>; cd <ZIP root>; <v>/bin/python -m pip install --no-index --find-links <wh> -r requirements-public.txt
#  (3) requirements-cpu.txt offline, --dry-run only (the wheelhouse holds the cu124 torch builds, not +cpu)
set -u
SRC=/home/mgjeong/Desktop/etri_hd/mbnas_application
RT=$SRC/runtime/test_root/t6/rerun2
P=/tmp/claude-1001/-home-mgjeong-Desktop-etri-hd/8da2e29b-07e2-48e6-bf6a-07622ebcfa6c/scratchpad/pfinal2
E=$SRC/docs/internal/evidence/WP7/PFINAL2/offline
WH=$SRC/build/wheelhouse
PY=/home/mgjeong/miniconda3/envs/mbnas310/bin/python3.10
Z=$SRC/dist/public/mbnas_application-0.1.0-public-runtime-linux_x86_64.zip
D="$RT/mbnas offline r2"
export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1 PIP_CONFIG_FILE=/dev/null
[ -e "$D" ] && { echo "refusing: $D exists"; exit 1; }
mkdir -p "$D" && (cd "$D" && unzip -q "$Z")
cd "$P"
{ echo "# release public ZIP sha256 $(sha256sum "$Z" | cut -c1-64), extracted fresh into <RT>/mbnas offline r2; cwd <scratchpad>/pfinal2"
  echo "\$ unshare -rn bash \"<RT>/mbnas offline r2/mbnas_application/scripts/install.sh\" --profile public --offline --wheelhouse <SRC>/build/wheelhouse --python <conda mbnas310>/bin/python3.10"
  unshare -rn bash "$D/mbnas_application/scripts/install.sh" --profile public --offline --wheelhouse "$WH" --python "$PY"; echo "exit=$?"
  echo "\$ unshare -rn <tree>/runtime/venv/bin/python -c 'import the 4 public packages; find_spec(mbnas_core) is None'"
  unshare -rn "$D/mbnas_application/runtime/venv/bin/python" -c "import mbnas_server, mbnas_runner, mbnas_model_runtime, mbnas_contracts, importlib.util as u; assert u.find_spec('mbnas_core') is None; print('imports ok without network; mbnas_core spec None')"; echo "exit=$?"
} > "$E/20_install_sh_unshare_release.txt" 2>&1
{ echo "\$ unshare -rn $PY -m venv \"<tree>/reqcheck-public-venv\""
  unshare -rn "$PY" -m venv "$D/mbnas_application/reqcheck-public-venv"; echo "exit=$?"
  echo "\$ (cd <tree>) unshare -rn reqcheck-public-venv/bin/python -m pip install --no-index --find-links <SRC>/build/wheelhouse -r requirements-public.txt"
  (cd "$D/mbnas_application" && unshare -rn reqcheck-public-venv/bin/python -m pip install --disable-pip-version-check --no-index --find-links "$WH" -r requirements-public.txt); echo "exit=$?"
  echo "\$ unshare -rn reqcheck-public-venv/bin/python -m pip check"
  (cd "$D/mbnas_application" && unshare -rn reqcheck-public-venv/bin/python -m pip check); echo "exit=$?"
  echo "\$ unshare -rn reqcheck-public-venv/bin/python -c 'import the 4 public packages; find_spec(mbnas_core) is None'"
  (cd /tmp && unshare -rn "$D/mbnas_application/reqcheck-public-venv/bin/python" -c "import mbnas_server, mbnas_runner, mbnas_model_runtime, mbnas_contracts, importlib.util as u; assert u.find_spec('mbnas_core') is None; print('imports ok without network; mbnas_core spec None')"); echo "exit=$?"
} > "$E/21_pip_requirements_public_unshare.txt" 2>&1
{ echo "\$ (cd <tree>) unshare -rn reqcheck-public-venv/bin/python -m pip install --dry-run --no-index --find-links <SRC>/build/wheelhouse -r requirements-cpu.txt   (dry run: the CPU variant pins torch/torchvision +cpu)"
  (cd "$D/mbnas_application" && unshare -rn reqcheck-public-venv/bin/python -m pip install --disable-pip-version-check --dry-run --no-index --find-links "$WH" -r requirements-cpu.txt); echo "exit=$?"
} > "$E/22_pip_requirements_cpu_unshare_dryrun.txt" 2>&1
for f in 20_install_sh_unshare_release 21_pip_requirements_public_unshare 22_pip_requirements_cpu_unshare_dryrun; do
  sed -i "s#$SRC#<SRC>#g; s#$RT#<RT>#g; s#$P#<scratchpad>/pfinal2#g; s#/home/mgjeong/miniconda3/envs/mbnas310#<conda mbnas310>#g" "$E/$f.txt"
  echo "$f: $(grep -E '^exit=' "$E/$f.txt" | tr '\n' ' ')"
done

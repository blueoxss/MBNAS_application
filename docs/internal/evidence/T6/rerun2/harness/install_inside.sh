#!/bin/bash
# T6 rerun2: offline install of the public-profile tree inside the sandbox (run with SBX_NET=none). cwd = /tmp.
set -u
L=$T6_LABEL
R="/srv/mbnas $L r2/mbnas_application"
echo "\$ (cwd=$(pwd); sandbox network interfaces: $(awk 'NR>2{print $1}' /proc/net/dev | tr '\n' ' '); /home visible? $(test -e /home && echo YES || echo no)) bash \"$R/scripts/install.sh\" --profile public --offline --wheelhouse /opt/wheelhouse --python /opt/py310/bin/python3.10"
bash "$R/scripts/install.sh" --profile public --offline --wheelhouse /opt/wheelhouse --python /opt/py310/bin/python3.10
rc=$?
echo "exit=$rc"
echo "runner id file after install: $(cat "$R/runtime/runner/runner_id" 2>/dev/null || echo '<none>')"
exit $rc

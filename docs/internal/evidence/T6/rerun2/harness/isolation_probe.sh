#!/bin/bash
# T6 rerun2 isolation probe (inside the sandbox).
L=$T6_LABEL
echo "label: $L; uid $(id -u); hostname $(cat /proc/sys/kernel/hostname)"
echo "network interfaces (/proc/net/dev): $(awk 'NR>2{print $1}' /proc/net/dev | tr '\n' ' ')"
for p in /home /data /home/mgjeong/Desktop/etri_hd/mbnas_application /home/mgjeong/Desktop/etri_hd/etri_nas /root; do echo "visible? $p: $(test -e "$p" && echo YES || echo no)"; done
echo "/srv: $(ls /srv | tr '\n' ' ')"; echo "/opt: $(ls /opt | tr '\n' ' ')"
echo "nvidia device nodes: [$(ls /dev | grep -i nvidia | tr '\n' ' ')]"
/opt/py310/bin/python3.10 -c "
import socket
try:
    socket.create_connection(('1.1.1.1', 443), 5); print('outbound 1.1.1.1:443: CONNECTED')
except OSError as e:
    print('outbound 1.1.1.1:443: failed ->', type(e).__name__, e)
try:
    socket.getaddrinfo('pypi.org', 443); print('DNS pypi.org: resolved')
except OSError as e:
    print('DNS pypi.org: failed ->', type(e).__name__, e)
"

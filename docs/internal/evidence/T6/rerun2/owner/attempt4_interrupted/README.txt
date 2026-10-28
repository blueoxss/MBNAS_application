T6 owner rerun2 attempt4, INTERRUPTED (kept for honesty). Owner ZIP 9f03c484… (tree 8fc93f2a…), tree <RT>/mbnas owner r2d.
Done: fresh extraction, install --offline (exit 0), configure, start (exit 0), doctor --strict (exit 0, 32 PASS).
verify unit,integration,relocation started 15:18:49 KST; at 15:22:06 (unit 6 %) WP-7V stopped the verify after an
older HOLD message (TaskStop), so it has no result. The service processes were stopped at 15:22:10-12 KST in stop.sh
order (runner deregister -> gateway -> owner worker -> core) by something other than WP-7V: WP-7V's own stop.sh ran at
15:22:27 and found no pid files (stop_owner_on_hold.txt). The re-run from a fresh extraction is attempt4_dd_a75/.

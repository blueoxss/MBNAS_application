ROUND 4 / OPS track - scripts/start.sh, stop.sh, doctor.sh, verify.sh + tests/public/ops (C09 EVAL_1 D1/D2, C12 EVAL_1 D7/D8)
Time: 2026-10-27T23:57Z - 2026-10-28T00:12Z (2026-10-28 08:57-09:12 KST). CPU only (CUDA_VISIBLE_DEVICES=""), interpreter
runtime/venv/bin/python, PYTHONDONTWRITEBYTECODE=1, PIP_CONFIG_FILE=/dev/null, -B -p no:cacheprovider, MBNAS_TEST_ROOT /
MBNAS_HOME / --basetemp in the session scratchpad. No GPU (doctor's GPU check only saw the tests' fake nvidia-smi), no
/data, no etri_nas, no build/source_snapshot. Every process the tests started was stopped (0 left afterwards).
Paths: <ROOT> = the project tree, <SCRATCH> = the session scratchpad.

CHANGES (line numbers after the change; sha256 before/after in files_sha256.txt)
  scripts/start.sh  :339-346 before any role starts: unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE OLDPWD and
                    export PYTHONNOUSERSITE=1 (kept with PYTHONUNBUFFERED / PYTHONDONTWRITEBYTECODE); :356,:360 the same
                    unset again inside the launch subshell right after its cd (bash exports OLDPWD after any cd even when
                    the caller had none - verified: `env -u OLDPWD bash -c '( cd /usr && exec env )'` prints OLDPWD=<cwd>;
                    a top-level unset before the cd prevents it). --help (:40-43) states the scrub. Options unchanged.
  scripts/stop.sh   :111 proc_starttime (field 22 of /proc/<pid>/stat), :120 kill_verified (SIGKILL only while the pid has
                    the start time recorded at collection; prints killed | gone | replaced), :134 descendants now emits
                    "<pid> <start time>" from the same /proc pass, :167 group_still_ours (group SIGKILL only while the leader
                    is the same incarnation or a verified descendant is still in that group), :214-231 stop_one: leader
                    start time captured, group / leader / every descendant re-verified right before SIGKILL, a replaced
                    descendant is reported "not signalled". --help (:22-25) updated. Options unchanged. The shared
                    identity block of start.sh/stop.sh is unchanged (test_identity_block_is_shared_by_start_and_stop).
  scripts/doctor.sh :35-38 help, :350-370 new check env:<role> for every RUNNING Python role (core, owner-worker, gateway,
                    runner; web = node is skipped): reads /proc/<pid>/environ, key names only; FAIL when PYTHONPATH or
                    PYTHONHOME is set (blocking under --strict), PASS otherwise, WARN when unreadable; PYTHONSTARTUP /
                    PYTHONUSERBASE / OLDPWD are noted in the detail ("also set (not blocking)"). Options unchanged.
  scripts/verify.sh :32-36 help, :370-414 ORIGIN_CODE walks every submodule of the profile's mbnas packages with
                    pkgutil.iter_modules + the path-entry finder's find_spec (no package is imported), lists sys.path,
                    the venv site dirs and their *.pth files (path lines resolved; import lines recorded as text);
                    :466-485 where()/shown(); :503-522 module check now covers every recorded module (PASS rule as before:
                    every origin under ROOT; a top-level package not found = FAIL); :573-595 relocation.json gains
                    module_counts, module_origins [{module, location ROOT|OUTSIDE|MISSING, origin relative to ROOT or
                    absolute}], sys_path [{path, location ROOT|BASE|OUTSIDE}], site_dirs, pth_files [{file, location,
                    lines [{kind import|path, ...}]}], caller_env_python_vars (key names only); plus relocation_origins.tsv
                    (module, location, origin) in the evidence list and one summary line. Options unchanged.

TESTS (tests/public/ops; no edit of an existing file)
  test_ops_role_env.py           2  port of the C09 probe (shadowing mbnas_server + marker module on the caller's
                                    PYTHONPATH, PYTHONHOME = a valid base prefix, PYTHONSTARTUP, PYTHONUSERBASE, OLDPWD):
                                    gateway and runner /proc/<pid>/environ have none of the five keys, PYTHONNOUSERSITE=1,
                                    the caller dir is not on sys.path, the marker is not importable, doctor env:<role> PASS;
                                    a gateway started by hand with PYTHONPATH/PYTHONHOME -> doctor env:gateway FAIL, blocking,
                                    --strict exit 1, the shadow path never printed.
  test_ops_stop_identity.py      3  kill_verified/proc_starttime taken verbatim from stop.sh against a live sleep
                                    (other start time / none -> "replaced", not signalled; right one -> "killed"; then
                                    "gone"); static: every SIGKILL of stop_one goes through the re-verification; behaviour:
                                    a SIGTERM-ignoring runner whose child is in its own session (outside the process group)
                                    -> the verified descendant SIGKILL stops that child.
  test_ops_relocation_origins.py 1  real throw-away venv (python -m venv --without-pip) with 4 stub packages x 4 modules and a
                                    .pth (comment, import, path inside ROOT, path outside ROOT): 16 rows all ROOT with
                                    relative origins, counts per package, .pth lines classified/flagged, sys.path ROOT+BASE+
                                    OUTSIDE, TSV, caller PYTHONPATH recorded by name only; then mbnas_runner moved outside
                                    ROOT -> module check FAIL, rows OUTSIDE with absolute origin, suite FAIL exit 1.

RESULTS
  tests/public/ops before (git HEAD scripts):  59 passed, 2 deselected, exit 0   pytest_ops_before.{txt,meta}
  tests/public/ops after:                      65 passed, 2 deselected, exit 0   pytest_ops_after.{txt,meta}
  negative controls (scratch copies, 8 runs):  all exit 1 for the intended reason   negative_control.txt (+ driver)
    head (pre-change scripts): the gateway environment held OLDPWD, PYTHONHOME, PYTHONPATH, PYTHONSTARTUP, PYTHONUSERBASE;
    no env:gateway check; no proc_starttime in stop.sh; no relocation_origins.tsv. mut_start (both unset lines removed):
    same 5 keys leak. mut_stop (no start-time comparison): 'killed' instead of 'replaced'. mut_doctor (no shadow keys):
    env:gateway PASS with PYTHONPATH set. mut_verify (where() -> ROOT): .pth OUTSIDE flag lost. mut_verify_rule (PASS rule
    off): module check ok with mbnas_runner outside ROOT.
  check_docs.py: exit 0, RESULT PASS (0 problems) after the --help text changes   check_docs.txt
  bash -n: start/stop/doctor/verify exit 0; shellcheck not installed (확인 못 함)    bash_syntax.txt
  new origin probe on real installs (read-only): T6 final public 126/126 and owner 215/215 module origins under ROOT,
    .pth coloredlogs + distutils-precedence (import lines) - same as the C09 evaluator's own walk   origin_probe_real_installs.txt

NOT VERIFIED (확인 못 함)
  - verify.sh --suite relocation end-to-end on a real installed tree with the NEW script (the T6 trees carry the old
    scripts; their venvs were only probed read-only with the new ORIGIN_CODE). Needs the next ZIP build + T6 run.
  - doctor env:<role> against real role processes (tested with the stub roles of the ops harness only).
  - A real pid reuse between collection and SIGKILL (cannot be produced on demand; the decision function is tested).
  - shellcheck (not installed).

EXTRA CHECKS
  option sets: parsed (case patterns) and documented (--help) options of start/stop/doctor/verify are identical before
    (git HEAD) and after - no option added or renamed (also test_documented_options_equal_implemented_options PASS).
  canary_scan.py --manifest docs/internal/canary/CANARY_MANIFEST.json --profile public (host facts on): the 3 new tests
    + 4 scripts: 7 files, 0 hits, exit 0; this evidence folder: 0 hits, exit 0.

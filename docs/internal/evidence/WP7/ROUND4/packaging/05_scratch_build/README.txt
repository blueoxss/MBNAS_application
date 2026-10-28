ONE scratch build campaign of the round-4 packager on the REAL tree's allowlisted files (2026-10-28 2026-10-28T00:27:54Z - 2026-10-28T00:29:08Z).
Script: <SCRATCH>/wp3/r4pkg/tools/final_scratch_build.sh (copy of make_scratch_root.py selection: exactly what the three
allowlists select from <ROOT>, plus build/allowlists, the canary manifest (scan needles) and the WP-7V offline record).
Nothing was written to <ROOT>/dist or <ROOT>/build: every --root was a scratch copy.
  root A = allowlisted copy WITH the source placeholder docs/registration/FILE_LIST.md; root B = the same WITHOUT it.
  THIRD_PARTY_NOTICES_WEB.md did not exist in <ROOT> yet (parent still generating it): standin_note.txt.
Results (exit codes in each log's last line):
  registration --reproducible  A: two builds IDENTICAL, zip e385bf6fb6faee5b...  B: two builds IDENTICAL, zip e385bf6fb6faee5b...  => A == B (C10 D5)
  public  (A, single pass): exit 0, 19 checks table, zip 21e3df0a540ed103...
  owner   (A, single pass): exit 0, wheel gate 5/5 PASS (core 92 members, 3/3 package-data), zip 2956e2a6a1d16849...
  C11 D7: gen_registration_docs.py --only file-list --check on the unpacked registration ZIP of A: exit 0 (file_list_check_on_registration_zip.txt)
  member_lists/<profile>.txt: sorted member paths below mbnas_application/ (public 476, registration 486, owner 2459)
These ZIPs are evidence only (stand-in notices file; not release candidates).

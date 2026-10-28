#!/usr/bin/env bash
# Functional self-test of scripts/dev/snapshot_{copy,delta,compare}.py on a synthetic git repo (never the real snapshot).
set -u
T=/home/mgjeong/Desktop/etri_hd/mbnas_application
PY="$T/runtime/venv/bin/python -I -B"
W=$1; rm -rf "$W"; mkdir -p "$W"; S=$W/src; G=$W/tgt
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
mkdir -p $S/application/MBNAS_server/src/pkg/__pycache__ $S/application/MBNAS_web/src $S/application/docs $S/yolo_ga_runtime $S/application/MBNAS_server/src/pkg/evidence_x
echo 'a = 1' > $S/application/MBNAS_server/src/pkg/a.py
echo 'b = 1' > $S/application/MBNAS_server/src/pkg/b.py
echo 'x' > $S/application/MBNAS_server/src/pkg/__pycache__/a.cpython-310.pyc
echo 'skip' > $S/application/MBNAS_server/src/pkg/evidence_x/e.txt
echo 'export const c = 1;' > $S/application/MBNAS_web/src/c.ts
echo '# doc' > $S/application/docs/d.md
echo 'r = 1' > $S/yolo_ga_runtime/r.py
ln -s /nonexistent/target $S/application/MBNAS_server/src/pkg/link.py
git -C $S init -q && git -C $S add -A && git -C $S commit -qm init
echo 'a = 2' > $S/application/MBNAS_server/src/pkg/a.py          # tracked_modified
echo 'u = 1' > $S/application/MBNAS_server/src/pkg/u.py          # untracked
idx0=$(stat -c %Y.%s $S/.git/index)
echo "== 1 copy (write)"; $PY $T/scripts/dev/snapshot_copy.py --source-root $S --target-root $G --snapshot-dir $G/build/source_snapshot --provenance-dir $G/prov --write | grep -E '"files"|"mode"|tracked|untracked|skipped_count'; echo "exit=$?"
echo "== 2 copy again (must refuse, exit 2)"; $PY $T/scripts/dev/snapshot_copy.py --source-root $S --target-root $G --snapshot-dir $G/build/source_snapshot --provenance-dir $G/prov --write >/dev/null 2>&1; echo "exit=$?"
echo "== 3 copy into the source (must refuse, exit 2)"; $PY $T/scripts/dev/snapshot_copy.py --source-root $S --snapshot-dir $S/snap --provenance-dir $S/prov --write >/dev/null 2>&1; echo "exit=$? (src/snap exists: $(test -e $S/snap && echo yes || echo no))"
echo "== 4 compare (in sync)"; $PY $T/scripts/dev/snapshot_compare.py --source-root $S --target-root $G --snapshot-dir $G/build/source_snapshot --provenance-dir $G/prov | tail -6; echo "exit=${PIPESTATUS[0]}"
echo 'b = 2' > $S/application/MBNAS_server/src/pkg/b.py; echo 'n = 1' > $S/application/MBNAS_web/src/n.ts; echo '# new' > $S/application/docs/new.md
echo "== 5 compare (source advanced: expect differ 1, new code 1, new docs 1, exit 1)"; $PY $T/scripts/dev/snapshot_compare.py --source-root $S --target-root $G --snapshot-dir $G/build/source_snapshot --provenance-dir $G/prov | grep -E "live sha256|selected live|^ok"; echo "exit=${PIPESTATUS[0]}"
echo "== 6 delta plan"; $PY $T/scripts/dev/snapshot_delta.py --source-root $S --target-root $G --snapshot-dir $G/build/source_snapshot --provenance-dir $G/prov --plan-out $W/plan.json; echo "exit=$?"
echo "== 7 apply without --write (must refuse, exit 2)"; $PY $T/scripts/dev/snapshot_delta.py --source-root $S --target-root $G --snapshot-dir $G/build/source_snapshot --provenance-dir $G/prov --apply $W/plan.json --reason t >/dev/null 2>&1; echo "exit=$?"
echo "== 8 apply"; $PY $T/scripts/dev/snapshot_delta.py --source-root $S --target-root $G --snapshot-dir $G/build/source_snapshot --provenance-dir $G/prov --apply $W/plan.json --reason "selftest" --write | tr -d '\n' | cut -c1-300; echo; echo "exit=$?"
echo "== 9 compare after apply (expect ok True apart from the new doc, exit 0)"; $PY $T/scripts/dev/snapshot_compare.py --source-root $S --target-root $G --snapshot-dir $G/build/source_snapshot --provenance-dir $G/prov | tail -9; echo "exit=${PIPESTATUS[0]}"
echo 'b = 3' > $S/application/MBNAS_server/src/pkg/b.py
echo "== 10 stale plan apply (must refuse)"; $PY $T/scripts/dev/snapshot_delta.py --source-root $S --target-root $G --snapshot-dir $G/build/source_snapshot --provenance-dir $G/prov --apply $W/plan.json --reason x --write 2>$W/err.txt; echo "exit=$? ($(tail -1 $W/err.txt))"
echo "== source .git/index unchanged by the tools: $( [ "$idx0" = "$(stat -c %Y.%s $S/.git/index)" ] && echo yes || echo NO )"

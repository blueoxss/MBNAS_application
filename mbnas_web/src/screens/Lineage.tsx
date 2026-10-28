/**
 * 모델 계보 (Lineage): pick a candidate (an evaluated row or, F14, a generated candidate that was never trained or
 * evaluated), see parents A/B, operation (2/1/0 parents), the public block source kinds, the architecture / checkpoint
 * files by id + sha256 (contract 1.5.0: no host path) and the ancestry chain. Generated candidates carry the
 * "UNTRAINED · UNEVALUATED" badge so they are never read as measured rows.
 */
import { useMemo, useState } from 'react';
import { CandidateView, lineageOperation, parentRefs } from '../components/CandidateView';
import { DataTable, type Column } from '../components/DataTable';
import { Badge, Banner, Card } from '../components/ui';
import { rec, type Candidate } from '../api/types';
import { isTrainedNotEvaluated, isUntrainedUnevaluated, TRAINED_NOT_EVALUATED, UNTRAINED_UNEVALUATED } from '../lib/evaluation';
import { DASH, fmtNum } from '../lib/format';
import { generatedAsCandidate, generationOnlyRow } from '../lib/generation';
import { candidateList } from '../state/reducer';
import { useApp } from '../state/context';

/** Ancestors by following `parents` (candidate or evaluation ids); cycle-safe, depth-limited. */
export function ancestry(c: Candidate, all: readonly Candidate[], maxDepth = 6): { depth: number; id: string; name: string | null; known: boolean }[] {
  const out: { depth: number; id: string; name: string | null; known: boolean }[] = [];
  const seen = new Set<string>([c.candidate_id]);
  let frontier: Candidate[] = [c];
  for (let depth = 1; depth <= maxDepth && frontier.length; depth++) {
    const next: Candidate[] = [];
    for (const f of frontier) {
      for (const p of parentRefs(f, all)) {
        if (seen.has(p.id)) continue;
        seen.add(p.id);
        const hit = all.find((x) => x.candidate_id === p.id || x.id === p.id);
        out.push({ depth, id: p.id, name: hit?.name ?? null, known: !!hit });
        if (hit) next.push(hit);
      }
    }
    frontier = next;
  }
  return out;
}

/** Row key of the lineage list: the evaluation id of an evaluated row, `generated:<candidate_id>` for a generated candidate. */
export function lineageKey(c: Candidate): string {
  return generationOnlyRow(c) ? `generated:${c.candidate_id}` : c.id;
}

/**
 * Lineage rows: every evaluation row of the loaded run, then the generated candidates (snapshot `generated_candidates`) whose
 * candidate id has no evaluation row, in the server's order. A generated candidate is shown once, never as an evaluation.
 */
export function lineageRows(evaluated: readonly Candidate[], generated: readonly Parameters<typeof generatedAsCandidate>[0][]): Candidate[] {
  const known = new Set(evaluated.map((c) => c.candidate_id));
  return [...evaluated, ...generated.filter((g) => !known.has(g.candidate_id)).map(generatedAsCandidate)];
}

/** Status cell: "UNTRAINED · UNEVALUATED" for a generated candidate (DD-A23), the DD-5 badge, else the evaluation row status. */
export function LineageStatus({ c, run }: { c: Candidate; run: Parameters<typeof isTrainedNotEvaluated>[1] }): JSX.Element {
  if (isUntrainedUnevaluated(c)) {
    const r = rec(c);
    return <Badge tone="warn" className="b-nowrap" title={`generated candidate · training_status ${String(r.training_status ?? 'UNTRAINED')} · evaluation_status ${String(r.evaluation_status ?? 'NOT_EVALUATED')}: never trained, never evaluated`}><span data-testid="lineage-untrained">{UNTRAINED_UNEVALUATED}</span></Badge>;
  }
  if (isTrainedNotEvaluated(c, run)) return <Badge tone="info"><span data-testid="lineage-trained-not-evaluated">{TRAINED_NOT_EVALUATED}</span></Badge>;
  return <Badge tone={c.status === 'EVALUATED' ? 'ok' : 'neutral'}><span data-testid="lineage-status">{c.status}</span></Badge>;
}

export function Lineage(): JSX.Element {
  const app = useApp();
  const all = useMemo(() => lineageRows(candidateList(app.rs), app.rs.generatedCandidates), [app.rs]);
  const [sel, setSel] = useState<string | null>(null);
  if (!app.rs.runId) {
    return (
      <div>
        <div className="page-head"><h1>모델 계보</h1></div>
        <Banner tone="info">No run loaded. Lineage comes from server rows only (parents, blocks, architecture and checkpoint ids with their sha256), including the generated candidates of an evaluation-OFF run.</Banner>
      </div>
    );
  }
  const cur = all.find((c) => lineageKey(c) === sel) ?? all[0] ?? null;
  const generatedCount = all.filter(generationOnlyRow).length;
  const cols: Column<Candidate>[] = [
    { key: 'name', header: 'Candidate', cell: (c) => <button className="btn btn-sm" type="button" aria-pressed={!!cur && lineageKey(cur) === lineageKey(c)} onClick={() => setSel(lineageKey(c))}>{c.name}</button>, sort: (c) => c.name },
    { key: 'status', header: 'Status', cell: (c) => <LineageStatus c={c} run={app.rs.run} /> },
    { key: 'arm', header: 'Arm', cell: (c) => c.arm, sort: (c) => c.arm },
    { key: 'gen', header: 'Gen', cell: (c) => c.generation ?? DASH, sort: (c) => c.generation ?? null, num: true },
    { key: 'rung', header: 'Rung', cell: (c) => c.rung ?? DASH },
    { key: 'op', header: 'Operation', cell: (c) => lineageOperation(c).text },
    { key: 'par', header: 'Parents', cell: (c) => ((c.parents ?? []).length ? (c.parents ?? []).map((p, i) => <Badge key={p} className={i === 0 ? 'b-parent-a' : i === 1 ? 'b-parent-b' : ''}>{lineageOperation(c).op === 'final' ? 'source' : i === 0 ? 'A' : i === 1 ? 'B' : `#${i + 1}`}</Badge>) : DASH) },
    { key: 'fit', header: 'Fitness', cell: (c) => (generationOnlyRow(c) ? <span className="muted">미평가</span> : fmtNum(c.fitness, 3)), sort: (c) => (generationOnlyRow(c) ? null : c.fitness ?? null), num: true },
  ];
  const anc = cur ? ancestry(cur, all) : [];
  return (
    <div>
      <div className="page-head"><h1>모델 계보</h1></div>
      <Card title="Candidates">
        {generatedCount > 0 && <p className="help" data-testid="lineage-generated-count">생성 후보 {generatedCount}개 포함: 학습·평가하지 않은 후보이며 “{UNTRAINED_UNEVALUATED}”로 표시합니다(성능 값 없음).</p>}
        <DataTable caption="Select a candidate (evaluated rows and generated candidates)" columns={cols} rows={all} rowKey={lineageKey} selectedKey={cur ? lineageKey(cur) : null} maxHeight empty="No candidates yet." testId="lineage-table" />
      </Card>
      {cur && (
        <>
          <Card title="Ancestry">
            {anc.length === 0 ? (
              <p className="muted">No ancestors ({lineageOperation(cur).text}).</p>
            ) : (
              <ul data-testid="ancestry" style={{ margin: 0, paddingLeft: 18 }}>
                {anc.map((a) => (
                  <li key={a.id} style={{ marginLeft: (a.depth - 1) * 16 }}>
                    generation -{a.depth}: <span className="mono wrap-any">{a.name ?? a.id}</span> {!a.known && <Badge tone="warn">not in loaded rows</Badge>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title={cur.name}>
            <CandidateView key={lineageKey(cur)} c={cur} run={app.rs.run} runId={app.rs.runId} all={all} client={app.client} />
          </Card>
        </>
      )}
    </div>
  );
}

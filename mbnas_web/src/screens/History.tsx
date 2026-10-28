/**
 * 실행 기록 (History): the event list (type, seq, time) and the facts derived from it: run status
 * transitions with reason codes, per-candidate progress (attempt, epoch, LR, step), failures, budget
 * admission results, recoveries (snapshot.required, gap resync, reconnects) and versions.
 */
import { useMemo, useState } from 'react';
import { DataTable, type Column } from '../components/DataTable';
import { Badge, Banner, Card, KV, SelectField } from '../components/ui';
import type { ApiEvent, Candidate } from '../api/types';
import { rec } from '../api/types';
import { DASH, fmtAny, fmtTime } from '../lib/format';
import { relatedRuns, type RelatedRun } from '../lib/finalretrain';
import { candidateList } from '../state/reducer';
import { useApp } from '../state/context';

const FAILURE = new Set(['INVALID', 'TRAIN_FAILED', 'EVAL_FAILED', 'RESUME_BLOCKED', 'REJECTED_BY_GATE', 'CANCELLED']);
const BUDGET = new Set(['PROMOTION_ELIGIBLE', 'PROMOTION_ADMITTED', 'ELIGIBLE_BUDGET_EXHAUSTED']);

function when(e: ApiEvent): string {
  const p = rec(e.payload);
  const t = p.at ?? p.time ?? p.timestamp ?? p.updated_at ?? p.created_at;
  return typeof t === 'string' ? fmtTime(t) : DASH;
}

function summary(e: ApiEvent): string {
  const p = rec(e.payload);
  switch (e.type) {
    case 'run.updated':
      return `status ${fmtAny(p.status)}${p.reason_code ? ` reason ${p.reason_code}` : ''}${p.kind ? ` kind ${fmtAny(p.kind)}` : ''}${p.parent_run_id ? ` parent ${fmtAny(p.parent_run_id)}` : ''}`;
    case 'candidate.updated':
    case 'candidate.evaluated':
      return `${fmtAny(p.name ?? p.candidate_id)} ${fmtAny(p.status)}${p.candidate_status ? `/${p.candidate_status}` : ''}`;
    case 'generation.updated':
    case 'generation.completed':
      return `${fmtAny(p.arm)} gen ${fmtAny(p.generation)} ${fmtAny(p.rung)} complete=${fmtAny(p.complete)} planned=${fmtAny(p.planned_count)} evaluated=${fmtAny(p.evaluated_count)} pending=${fmtAny(p.pending_count)}`;
    case 'worker.updated':
      return `${fmtAny(p.id)} ${fmtAny(p.status)}`;
    case 'log':
      return fmtAny(p.message);
    default:
      return JSON.stringify(p).slice(0, 120);
  }
}

export function History(): JSX.Element {
  const app = useApp();
  const { rs } = app;
  const [type, setType] = useState('');
  const events = rs.events;
  const cands = useMemo(() => candidateList(rs), [rs]);
  if (!rs.runId) {
    return (
      <div>
        <div className="page-head"><h1>실행 기록</h1></div>
        <Banner tone="info">No run loaded. History lists the events of the loaded run.</Banner>
      </div>
    );
  }
  const shown = type ? events.filter((e) => e.type === type) : events;
  const evCols: Column<ApiEvent>[] = [
    { key: 'seq', header: 'Seq', cell: (e) => e.seq, sort: (e) => e.seq, num: true },
    { key: 'type', header: 'Type', cell: (e) => <Badge>{e.type}</Badge>, sort: (e) => e.type },
    { key: 'time', header: 'Time', cell: when },
    { key: 'sum', header: 'Summary', cell: (e) => <span className="wrap-any">{summary(e)}</span> },
  ];
  const transitions = events.filter((e) => e.type === 'run.updated');
  const progress = events.filter((e) => e.type === 'candidate.updated' && rec(rec(e.payload).progress).epoch !== undefined);
  const failures = cands.filter((c) => FAILURE.has(c.candidate_status ?? '') || FAILURE.has(c.status) || c.reason_code);
  const budget = cands.filter((c) => BUDGET.has(c.candidate_status ?? '') || BUDGET.has(c.status) || rec(c.gate).admission !== undefined);
  const related = relatedRuns({ run: rs.run, events, runs: app.runs.data, createdChildId: app.finalState.parentRunId === rs.runId && app.finalState.status === 'created' ? app.finalState.childRunId : null });
  const recoveries = events.filter((e) => e.type === 'snapshot.required');
  const run = rs.run;
  const candCols = (extra: Column<Candidate>[]): Column<Candidate>[] => [
    { key: 'name', header: 'Candidate', cell: (c) => c.name, sort: (c) => c.name },
    { key: 'rung', header: 'Rung', cell: (c) => c.rung },
    ...extra,
  ];
  return (
    <div>
      <div className="page-head"><h1>실행 기록</h1></div>
      <Card title="Versions · provenance">
        <KV
          rows={[
            ['run status', run ? <Badge tone="info" key="s">{run.status}</Badge> : DASH],
            ['reason_code', run?.reason_code ?? DASH],
            ['run kind', run?.kind ?? DASH],
            ['protocol_hash', <span className="mono wrap-any" key="h">{run?.protocol_hash ?? DASH}</span>],
            ['protocol canonicalization', run?.protocol_canonicalization ?? DASH],
            ['source commit', <span className="mono" key="c">{run?.source_commit ?? DASH}</span>],
            ['server versions', <span className="mono wrap-any" key="v">{app.health.data ? JSON.stringify(app.health.data.versions) : DASH}</span>],
            ['last seq', String(rs.lastSeq)],
          ]}
        />
      </Card>
      <Card title="상위 · 하위 Run · parent / child runs">
        <p className="help">Only explicit server facts are listed: Run.parent_run_id, runs whose parent_run_id is this run (use List runs on the Server screen), run.updated events carrying a parent or child id, and a child created from this page.</p>
        <DataTable
          caption="Related runs"
          columns={[
            { key: 'rel', header: 'Relation', cell: (r: RelatedRun) => <Badge tone={r.relation === 'parent' ? 'info' : 'warn'}>{r.relation}</Badge>, sort: (r: RelatedRun) => r.relation },
            { key: 'id', header: 'Run', cell: (r: RelatedRun) => <span className="mono wrap-any">{r.id}</span> },
            { key: 'st', header: 'Status', cell: (r: RelatedRun) => r.status ?? DASH },
            { key: 'src', header: 'Source', cell: (r: RelatedRun) => <span className="wrap-any">{r.source}</span> },
            { key: 'act', header: '', cell: (r: RelatedRun) => <button className="btn btn-sm" type="button" onClick={() => void app.loadRun(r.id)}>Load {r.relation} run</button> },
          ]}
          rows={related}
          rowKey={(r) => `${r.relation}:${r.id}`}
          empty="No parent or child run is known for the loaded run."
          testId="related-runs"
        />
      </Card>
      <Card title="Events">
        <div style={{ maxWidth: 320, marginBottom: 8 }}>
          <SelectField label="Filter by type" value={type} allowEmpty="All types" options={['candidate.updated', 'candidate.evaluated', 'generation.updated', 'generation.completed', 'run.updated', 'worker.updated', 'log', 'snapshot.required']} onChange={setType} />
        </div>
        <DataTable caption={`Events (${shown.length})`} columns={evCols} rows={shown} rowKey={(e) => String(e.seq)} maxHeight initialSort={{ key: 'seq', dir: 'desc' }} empty="No events in the loaded window." testId="event-table" />
      </Card>
      <div className="grid-2">
        <Card title="Run 상태 전이 · status transitions">
          <DataTable
            caption="run.updated events"
            columns={[
              { key: 'seq', header: 'Seq', cell: (e: ApiEvent) => e.seq, num: true },
              { key: 'st', header: 'Status', cell: (e: ApiEvent) => <Badge>{fmtAny(rec(e.payload).status)}</Badge> },
              { key: 'rs', header: 'Reason', cell: (e: ApiEvent) => fmtAny(rec(e.payload).reason_code) },
            ]}
            rows={transitions}
            rowKey={(e) => String(e.seq)}
            empty="No status transition events in the loaded window."
          />
        </Card>
        <Card title="Epoch / LR / step (candidate.updated)">
          <DataTable
            caption="Training progress events"
            columns={[
              { key: 'seq', header: 'Seq', cell: (e: ApiEvent) => e.seq, num: true },
              { key: 'c', header: 'Candidate', cell: (e: ApiEvent) => <span className="wrap-any">{fmtAny(rec(e.payload).name ?? rec(e.payload).candidate_id)}</span> },
              { key: 'a', header: 'Attempt', cell: (e: ApiEvent) => fmtAny(rec(e.payload).attempt), num: true },
              { key: 'ep', header: 'Epoch', cell: (e: ApiEvent) => `${fmtAny(rec(rec(e.payload).progress).epoch)} / ${fmtAny(rec(rec(e.payload).progress).epoch_target_total)}`, num: true },
              { key: 'lr', header: 'LR', cell: (e: ApiEvent) => { const v = rec(rec(e.payload).progress).learning_rate; return typeof v === 'number' ? v.toExponential(2) : DASH; }, num: true },
              { key: 'step', header: 'Step', cell: (e: ApiEvent) => fmtAny(rec(rec(e.payload).progress).global_step), num: true },
            ]}
            rows={progress}
            rowKey={(e) => String(e.seq)}
            maxHeight
            empty="No progress events in the loaded window."
          />
        </Card>
        <Card title="실패 · failures">
          <DataTable caption="Failed, invalid, blocked or rejected candidates" columns={candCols([{ key: 'st', header: 'Status', cell: (c) => `${c.status}${c.candidate_status ? ` / ${c.candidate_status}` : ''}` }, { key: 'rc', header: 'Reason', cell: (c) => c.reason_code ?? DASH }, { key: 'at', header: 'Attempt', cell: (c) => fmtAny(c.attempt), num: true }])} rows={failures} rowKey={(c) => c.id} empty="No failures recorded." />
        </Card>
        <Card title="예산 승인 · budget admission">
          <DataTable caption="Eligibility versus admission" columns={candCols([{ key: 'cs', header: 'Candidate status', cell: (c) => c.candidate_status ?? c.status }, { key: 'gate', header: 'Gate / admission', cell: (c) => <span className="mono wrap-any">{JSON.stringify(c.gate ?? {}).slice(0, 120)}</span> }])} rows={budget} rowKey={(c) => c.id} empty="No promotion or admission records." />
        </Card>
      </div>
      <Card title="복구 · recoveries">
        <p className="help">Server-requested snapshots and client-side resyncs (gap, reconnect). A snapshot replaces local state; it is never merged as new evaluations.</p>
        <ul data-testid="recoveries" style={{ margin: 0, paddingLeft: 18 }}>
          {recoveries.map((e) => (
            <li key={`r${e.seq}`}>seq {e.seq}: snapshot.required (server)</li>
          ))}
          {rs.syncNotes.map((n, i) => (
            <li key={i}>
              {fmtTime(n.at)} (client): {n.detail}
            </li>
          ))}
          {recoveries.length === 0 && rs.syncNotes.length === 0 && <li className="muted">No recoveries.</li>}
        </ul>
      </Card>
    </div>
  );
}

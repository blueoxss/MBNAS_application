/**
 * FINAL_RETRAIN presentation shared by the Server and Monitor screens: kind badge, parent run link and the
 * frozen finalist table (candidate, source evaluation, arm, seed, rung, AP, latency, selection rule).
 * Final rows are evaluated on the final_eval split in their own comparison group; they never mix with search cohorts.
 */
import type { Finalist, Run } from '../api/types';
import { DASH, fmtAny, fmtMs, fmtPct } from '../lib/format';
import { finalistsOf, NO_FINALIST_TEXT, runKind } from '../lib/finalretrain';
import { DataTable, type Column } from './DataTable';
import { Badge, Banner, Card } from './ui';

/** Run kind badge plus, for a FINAL_RETRAIN run, the parent run id with a "load parent" action. */
export function RunKindBadges({ run, onLoad }: { run: Run | null; onLoad: (id: string) => void }): JSX.Element | null {
  const kind = runKind(run);
  if (!run || !kind) return null;
  return (
    <>
      <Badge tone={kind === 'FINAL_RETRAIN' ? 'warn' : 'neutral'} title="run kind" className="run-kind">
        kind: {kind}
      </Badge>
      {kind === 'FINAL_RETRAIN' && run.parent_run_id && (
        <span className="row" data-testid="parent-run-link">
          <span className="muted">parent run</span>
          <span className="mono wrap-any">{run.parent_run_id}</span>
          <button className="btn btn-sm" type="button" onClick={() => onLoad(run.parent_run_id as string)} data-testid="load-parent-btn">
            Load parent run
          </button>
        </span>
      )}
    </>
  );
}

export function FinalistsTable({ finalists }: { finalists: Finalist[] }): JSX.Element {
  const cols: Column<Finalist>[] = [
    { key: 'cand', header: 'Candidate', cell: (f) => <span className="mono wrap-any">{f.candidate_id}</span>, sort: (f) => f.candidate_id },
    { key: 'src', header: 'Source evaluation', cell: (f) => <span className="mono wrap-any">{f.source_evaluation_id}</span>, sort: (f) => f.source_evaluation_id },
    { key: 'arm', header: 'Arm', cell: (f) => f.arm, sort: (f) => f.arm },
    { key: 'seed', header: 'Seed', cell: (f) => fmtAny(f.search_seed), sort: (f) => f.search_seed ?? null, num: true },
    { key: 'rung', header: 'Rung', cell: (f) => f.rung, sort: (f) => f.rung },
    { key: 'ap', header: 'AP [%]', cell: (f) => fmtPct(f.ap), sort: (f) => f.ap ?? null, num: true },
    { key: 'lat', header: 'Latency', cell: (f) => fmtMs(f.latency_ms), sort: (f) => f.latency_ms ?? null, num: true },
    { key: 'rule', header: 'Selection rule', cell: (f) => <span className="wrap-any">{f.selection_rule || DASH}</span> },
  ];
  return <DataTable caption={`Finalists (${finalists.length})`} columns={cols} rows={finalists} rowKey={(f) => `${f.candidate_id}:${f.source_evaluation_id}`} empty={NO_FINALIST_TEXT} testId="finalists-table" />;
}

/** Card for a loaded FINAL_RETRAIN run; renders nothing for other runs. */
export function FinalistsCard({ run }: { run: Run | null }): JSX.Element | null {
  if (!run || runKind(run) !== 'FINAL_RETRAIN') return null;
  const list = finalistsOf(run);
  return (
    <Card title="Finalists · 최종 재학습 대상" id="finalists">
      <p className="help">
        Frozen list: highest L2 AP under the measured latency constraint, tie-break lower latency then stable candidate id, top-k per arm and seed. Evaluated on the final_eval split; separate from the search results.
      </p>
      {list.length === 0 && (
        <Banner tone="warn">
          <span data-testid="no-finalist">{NO_FINALIST_TEXT}{run.reason_code ? ` · reason ${run.reason_code}` : ''}</span>
        </Banner>
      )}
      <FinalistsTable finalists={list} />
    </Card>
  );
}

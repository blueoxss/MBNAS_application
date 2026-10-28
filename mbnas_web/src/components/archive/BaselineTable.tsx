/**
 * Baseline card (design 4.1-3): ALWAYS rendered for a selected run. The judge baseline is highlighted, reference
 * baselines and the gen0 anchor follow, and every row names its tier ("final 12 ep" vs the search tier of the
 * anchor) because the numbers of different tiers are not comparable.
 */
import type { ReactNode } from 'react';
import { DataTable, type Column } from '../DataTable';
import { Badge, Banner, Card } from '../ui';
import type { ArchiveBaseline, ArchiveBaselineList, ArchiveCandidateRow, ArchiveRun } from '../../archive/types';
import { DASH, fmtDur, fmtInt, fmtLat, fmtLatMs, fmtMap, fmtMb, isNum, tierInfo } from '../../archive/format';
import type { Loadable } from '../../state/context';
import { SourceNote } from './ArchiveBits';

interface Row extends ArchiveBaseline {
  isJudge: boolean;
  tierText: string;
}

export function judgeNameOf(list: ArchiveBaselineList | null | undefined, run: ArchiveRun | null | undefined): string | null {
  const j = list?.judge_baseline;
  if (typeof j === 'string' && j) return j;
  if (j && typeof j === 'object' && j.name) return j.name;
  const flagged = list?.baselines?.find((b) => b.is_judge === true);
  if (flagged) return flagged.name;
  return run?.judge_baseline?.name ?? null;
}

export function buildBaselineRows(list: ArchiveBaselineList | null | undefined, judge: string | null, anchorRow?: ArchiveCandidateRow | null): Row[] {
  const anchorTier = (anchor?: { tier?: string | null; epochs?: number | null } | null): string => {
    const t = anchor?.tier ?? anchorRow?.tier;
    return t ? tierInfo(t).label : '탐색 티어 (T1, gen0 앵커)';
  };
  const rows: Row[] = (list?.baselines ?? []).map((b) => ({
    ...b,
    // the contract's is_judge wins; the name match is only the fallback for an older server
    isJudge: b.kind !== 'anchor' && (b.is_judge ?? b.name === judge),
    tierText:
      b.kind === 'anchor'
        ? anchorTier(list?.anchor)
        : isNum(b.epochs)
          ? `final ${b.epochs} ep`
          : b.tier
            ? tierInfo(b.tier).label
            : 'final (epochs 미기재)',
  }));
  // judge first, then the other baselines in server order, anchors last
  const rank = (r: Row): number => (r.isJudge ? 0 : r.kind === 'anchor' ? 2 : 1);
  const sorted = rows.map((r, i) => ({ r, i })).sort((a, b) => rank(a.r) - rank(b.r) || a.i - b.i).map((x) => x.r);
  const a = list?.anchor;
  if (a && a.metrics && !sorted.some((r) => r.kind === 'anchor')) {
    sorted.push({
      name: `anchor ${a.candidate_id}`,
      kind: 'anchor',
      ...a.metrics,
      isJudge: false,
      tierText: anchorTier(a),
    } as Row);
  }
  return sorted;
}

interface Props {
  state: Loadable<ArchiveBaselineList>;
  run: ArchiveRun | null;
  candidates: readonly ArchiveCandidateRow[];
  runSelected: boolean;
  onRetry?: () => void;
}

export function BaselineTable({ state, run, candidates, runSelected, onRetry }: Props): JSX.Element {
  const list = state.data;
  const judge = judgeNameOf(list, run);
  const anchorRow = list?.anchor ? candidates.find((c) => c.candidate_id === list.anchor?.candidate_id && c.tier) ?? null : null;
  const rows = buildBaselineRows(list, judge, anchorRow);
  const judgeRow = rows.find((r) => r.isJudge);
  const jb = typeof list?.judge_baseline === 'object' && list?.judge_baseline ? list.judge_baseline : run?.judge_baseline;
  const notes = list?.notes ?? [];

  const cols: Column<Row>[] = [
    {
      key: 'name',
      header: '모델',
      cell: (r) => (
        <span className={r.isJudge ? 'judge-row' : undefined} data-testid={r.isJudge ? 'judge-baseline-row' : r.kind === 'anchor' ? 'anchor-row' : 'baseline-row'}>
          <strong>{r.name}</strong>{' '}
          {r.isJudge ? <Badge tone="info">★ 판정 기준선</Badge> : r.kind === 'anchor' ? <Badge tone="neutral">앵커 (gen0)</Badge> : <Badge tone="neutral">참고</Badge>}
          {r.status && r.status !== 'ok' && <Badge tone="warn">{r.status}</Badge>}
        </span>
      ),
      sort: (r) => r.name,
    },
    { key: 'tier', header: '티어', cell: (r) => <Badge tone={r.kind === 'anchor' ? 'warn' : 'info'}>{r.tierText}</Badge> },
    { key: 'map', header: 'mAP50-95', cell: (r) => fmtMap(r.map50_95), sort: (r) => r.map50_95 ?? null, num: true },
    { key: 'map50', header: 'mAP50', cell: (r) => fmtMap(r.map50), sort: (r) => r.map50 ?? null, num: true },
    { key: 'sr', header: 'small recall', cell: (r) => fmtMap(r.small_target_recall), sort: (r) => r.small_target_recall ?? null, num: true },
    {
      key: 'lat',
      header: 'latency v2 [ms] (탐색/배타)',
      cell: (r) => (
        <span title={`median ${fmtLat(r.latency_median_ms)} ms / 배타 ${fmtLat(r.latency_exclusive_ms)} ms`}>
          {fmtLat(r.latency_median_ms)} / {fmtLat(r.latency_exclusive_ms)}
        </span>
      ),
      sort: (r) => r.latency_median_ms ?? null,
      num: true,
    },
    { key: 'params', header: 'params_deploy', cell: (r) => fmtInt(r.params_deploy), sort: (r) => r.params_deploy ?? null, num: true },
    { key: 'mem', header: 'peak_mem [MB]', cell: (r) => fmtMb(r.peak_mem_mb).replace(' MB', ''), sort: (r) => r.peak_mem_mb ?? null, num: true },
    { key: 'time', header: 'train_time', cell: (r) => fmtDur(r.train_time_s), sort: (r) => r.train_time_s ?? null, num: true },
    { key: 'head', header: 'head_mode', cell: (r) => r.head_mode ?? DASH },
    {
      key: 'confirm',
      header: 'confirm val / test tiles / test native',
      cell: (r) => (r.confirm ? `${fmtMap(r.confirm.val_map50_95)} / ${fmtMap(r.confirm.test_tiles_map50_95)} / ${fmtMap(r.confirm.test_native_map50_95)}` : DASH),
    },
  ];

  let body: ReactNode;
  if (!runSelected) body = <p className="muted">Run을 선택하면 판정 기준선과 참고 기준선이 여기에 항상 표시됩니다.</p>;
  else if (state.status === 'loading' && !list) body = <p className="muted" role="status">기준선 불러오는 중…</p>;
  else if (state.status === 'error' && !list)
    body = (
      <Banner tone="err">
        <span data-testid="baseline-error" className="wrap-any">기준선을 불러오지 못했습니다: {state.error}</span>{' '}
        {onRetry && <button className="btn btn-sm" type="button" onClick={onRetry}>다시 시도</button>}
      </Banner>
    );
  else if (list && rows.length === 0)
    body = (
      <Banner tone="warn">
        <span data-testid="baseline-missing">이 run에는 기준선 결과(protocol/baselines_v2.json)가 없습니다. 판정 기준선: {judge ?? DASH}.</span>
      </Banner>
    );
  else
    body = (
      <>
        <div className="kpis" data-testid="baseline-summary" style={{ marginBottom: 10 }}>
          <div className="kpi">
            <div className="v">{judge ?? DASH}</div>
            <div className="l">판정 기준선{judgeRow ? ` · ${judgeRow.tierText}` : ''}</div>
          </div>
          <div className="kpi">
            <div className="v">{fmtMap(judgeRow?.map50_95 ?? jb?.map50_95)}</div>
            <div className="l">mAP50-95 (기준선)</div>
          </div>
          <div className="kpi">
            <div className="v">{fmtLatMs(judgeRow?.latency_median_ms ?? jb?.latency_median_ms)}</div>
            <div className="l">latency median (v2)</div>
          </div>
          <div className="kpi">
            <div className="v">{fmtInt(judgeRow?.params_deploy ?? jb?.params_deploy)}</div>
            <div className="l">params_deploy</div>
          </div>
        </div>
        <DataTable caption="기준선 · 앵커 (티어 열 필수 확인)" columns={cols} rows={rows} rowKey={(r) => `${r.kind}:${r.name}`} selectedKey={judgeRow ? `${judgeRow.kind}:${judgeRow.name}` : null} testId="baseline-table" empty="기준선 없음" />
        {notes.length > 0 && <p className="help wrap-any" data-testid="baseline-notes">{notes.join(' ')}</p>}
        <p className="help" style={{ marginTop: 6 }}>
          기준선은 protocol/baselines_v2.json(final 티어, 12/18 ep 재학습)이고 앵커는 탐색 gen0 후보(짧은 탐색 티어)입니다. 학습 길이가 달라 서로 직접 비교하지 마세요. latency는 탐색/배타 측정 순서로 표시합니다.
        </p>
      </>
    );

  return (
    <Card title="Baseline" id="archive-baseline">
      {body}
      <SourceNote source={list?.source} warnings={list?.warnings} />
    </Card>
  );
}

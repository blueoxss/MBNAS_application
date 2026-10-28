/** Run selection bar (design 4.1-1): family / dataset / status filters and the run table. */
import { useMemo, useState } from 'react';
import { DataTable, type Column } from '../DataTable';
import { Banner, Card, SelectField } from '../ui';
import type { ArchiveRunList, ArchiveRunSummary } from '../../archive/types';
import { DASH, fmtDur, fmtInt, fmtMap, fmtStamp } from '../../archive/format';
import type { Loadable } from '../../state/context';
import { SourceNote, StatusBadge, VerdictBadge } from './ArchiveBits';

interface Props {
  runs: Loadable<ArchiveRunList>;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onRefresh: () => void;
}

export function RunPicker({ runs, selectedId, onSelect, onRefresh }: Props): JSX.Element {
  const [family, setFamily] = useState('');
  const [dataset, setDataset] = useState('');
  const [status, setStatus] = useState('');
  const list = runs.data?.runs ?? [];
  const families = useMemo(() => [...new Set(list.map((r) => r.family))].sort(), [list]);
  const datasets = useMemo(() => [...new Set(list.map((r) => r.dataset_id).filter((d): d is string => !!d))].sort(), [list]);
  const shown = list.filter((r) => (!family || r.family === family) && (!dataset || r.dataset_id === dataset) && (!status || r.status === status));
  const missingRoots = (runs.data?.roots ?? []).filter((r) => !r.exists);

  const cols: Column<ArchiveRunSummary>[] = [
    {
      key: 'id',
      header: 'Run',
      cell: (r) => (
        <button className="btn btn-sm run-pick" type="button" aria-pressed={r.id === selectedId} onClick={() => onSelect(r.id)} aria-label={`${r.id} 선택`} data-testid={`run-pick-${r.id}`}>
          <span className="mono wrap-any">{r.id}</span>
        </button>
      ),
      sort: (r) => r.id,
    },
    { key: 'status', header: '상태', cell: (r) => <StatusBadge status={r.status} />, sort: (r) => r.status },
    { key: 'family', header: 'family', cell: (r) => r.family, sort: (r) => r.family },
    { key: 'scale', header: 'scale', cell: (r) => r.scale ?? DASH, sort: (r) => r.scale },
    { key: 'gen', header: '세대 done/planned', cell: (r) => `${r.generations?.done ?? DASH} / ${r.generations?.planned ?? DASH}`, sort: (r) => r.generations?.done ?? null, num: true },
    { key: 'cand', header: '후보 ok/전체', cell: (r) => `${fmtInt(r.candidates?.ok)} / ${fmtInt(r.candidates?.total)}`, sort: (r) => r.candidates?.total ?? null, num: true },
    { key: 'map', header: 'best mAP50-95', cell: (r) => fmtMap(r.best?.map50_95), sort: (r) => r.best?.map50_95 ?? null, num: true },
    {
      key: 'verdicts',
      header: '판정',
      cell: (r) => (
        <span className="tag-list">
          <VerdictBadge label="notebook" value={r.verdicts?.notebook} />
          <VerdictBadge label="transition" value={r.verdicts?.transition} />
          <VerdictBadge label="confirm" value={r.verdicts?.confirm} />
          <VerdictBadge label="smoke" value={r.verdicts?.smoke_check} />
        </span>
      ),
    },
    { key: 'start', header: '시작 (UTC)', cell: (r) => fmtStamp(r.started_at), sort: (r) => r.started_at },
    { key: 'dur', header: '소요', cell: (r) => fmtDur(r.duration_s), sort: (r) => r.duration_s ?? null, num: true },
  ];

  return (
    <Card
      title="Run 선택"
      id="archive-runs"
      actions={
        <button className="btn btn-sm" type="button" onClick={onRefresh} disabled={runs.status === 'loading'} data-testid="runs-refresh">
          {runs.status === 'loading' ? '불러오는 중…' : '목록 새로고침'}
        </button>
      }
    >
      {runs.status === 'error' && (
        <Banner tone="err">
          <span data-testid="runs-error" className="wrap-any">
            run 목록을 불러오지 못했습니다: {runs.error}
          </span>
        </Banner>
      )}
      {missingRoots.length > 0 && (
        <Banner tone="warn">
          <span data-testid="roots-missing">접근할 수 없는 아카이브 루트: {missingRoots.map((r) => r.path).join(', ')}</span>
        </Banner>
      )}
      <div className="filters" style={{ marginBottom: 10 }}>
        <SelectField label="family" value={family} onChange={setFamily} allowEmpty="전체" options={families} testId="filter-family" />
        <SelectField label="dataset" value={dataset} onChange={setDataset} allowEmpty="전체" options={datasets} testId="filter-dataset" />
        <SelectField label="상태" value={status} onChange={setStatus} allowEmpty="전체" options={['running', 'done', 'failed', 'unknown']} testId="filter-status" />
      </div>
      {runs.status === 'loading' && !runs.data && <p className="muted" role="status">run 목록 불러오는 중…</p>}
      {runs.data && (
        <DataTable caption={`아카이브 run ${shown.length} / ${list.length}`} columns={cols} rows={shown} rowKey={(r) => r.id} selectedKey={selectedId} empty="조건에 맞는 run이 없습니다." testId="run-table" maxHeight initialSort={{ key: 'start', dir: 'desc' }} />
      )}
      <SourceNote warnings={runs.data?.warnings} />
    </Card>
  );
}

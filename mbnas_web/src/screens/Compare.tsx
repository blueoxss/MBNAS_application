/**
 * Run 비교 (design 4.2): 2-6 runs of the same dataset - best-so-far mAP50-95 versus cumulative train time,
 * hypervolume versus generation, baselines side by side and the finalists. Runs whose dataset or metric protocol
 * differ are flagged (comparable=false) and still drawn, with the warning.
 */
import { useMemo, useState } from 'react';
import { DataTable, type Column } from '../components/DataTable';
import { LineChart, type LineSeries } from '../components/archive/LineChart';
import { ConnectGate, SourceNote, ViewToggle } from '../components/archive/ArchiveBits';
import { Badge, Banner, Card, SelectField } from '../components/ui';
import { useArchiveQuery } from '../archive/api';
import type { ArchiveCompareRun } from '../archive/types';
import { DASH, fmtDur, fmtInt, fmtLat, fmtMap, fmtVal, isNum, recOf } from '../archive/format';
import { runColor } from '../archive/palette';
import { LoadBox, StatusBadge } from '../components/archive/ArchiveBits';
import { useApp } from '../state/context';

const SHAPES = ['circle', 'diamond', 'square', 'triangle', 'star', 'circle'] as const;
const DASHES = [undefined, '7 3', '2 4', '10 3 2 3', '4 2', '1 3'] as const;

export function curveSeries(runs: readonly ArchiveCompareRun[], which: 'best' | 'hv'): LineSeries[] {
  return runs.map((r, i) => {
    const pts =
      which === 'best'
        ? (r.curves?.best_so_far_map50_95 ?? []).map((p) => ({ key: `${r.id}:${p.generation}`, x: p.cum_train_time_s as number, y: p.value, label: `${r.id} 세대 ${p.generation}: best-so-far mAP50-95 ${fmtMap(p.value)} @ ${fmtDur(p.cum_train_time_s)}${p.candidate_id ? ` (${p.candidate_id})` : ''}`, ok: isNum(p.cum_train_time_s) }))
        : (r.curves?.hypervolume ?? []).map((p) => ({ key: `${r.id}:${p.generation}`, x: p.generation, y: p.value, label: `${r.id} 세대 ${p.generation}: HV ${isNum(p.value) ? p.value.toFixed(4) : DASH}`, ok: true }));
    return {
      key: r.id,
      label: r.id,
      color: runColor(i),
      shape: SHAPES[i % SHAPES.length],
      dash: DASHES[i % DASHES.length],
      step: which === 'best',
      // a point without an x (missing train time) is dropped, never moved to 0
      points: pts.filter((p) => p.ok).map(({ ok: _ok, ...p }) => p),
    };
  });
}

/** Run conditions as the server recorded them (ArchiveCompareRun.conditions). A null member is "not recorded", never inferred. */
export const CONDITION_KEYS = ['dataset', 'split', 'stage', 'epochs_total', 'protocol_hash', 'device', 'precision', 'checkpoint'] as const;
export type ConditionKey = (typeof CONDITION_KEYS)[number];
const NO_RECORD = '기록 없음';

export function conditionValue(run: ArchiveCompareRun, key: ConditionKey): string | number | null {
  const v = recOf(run.conditions)[key];
  return typeof v === 'string' && v !== '' ? v : typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** Pairs of runs whose non-null values of a condition differ. A condition that is null on either side is not compared (and is listed in `unknown`). */
export function conditionDifferences(runs: readonly ArchiveCompareRun[]): { pairs: { a: string; b: string; keys: ConditionKey[] }[]; unknown: ConditionKey[] } {
  const pairs: { a: string; b: string; keys: ConditionKey[] }[] = [];
  for (let i = 0; i < runs.length; i += 1) for (let j = i + 1; j < runs.length; j += 1) {
    const keys = CONDITION_KEYS.filter((key) => { const x = conditionValue(runs[i], key), y = conditionValue(runs[j], key); return x !== null && y !== null && x !== y; });
    if (keys.length > 0) pairs.push({ a: runs[i].id, b: runs[j].id, keys });
  }
  return { pairs, unknown: CONDITION_KEYS.filter((key) => runs.some((run) => conditionValue(run, key) === null)) };
}

export function Compare(): JSX.Element {
  const app = useApp();
  const a = app.archive;
  const connected = app.health.status === 'ok';
  const list = a.runs.data?.runs ?? [];
  const datasets = useMemo(() => [...new Set(list.map((r) => r.dataset_id).filter((d): d is string => !!d))].sort(), [list]);
  const [dataset, setDataset] = useState('');
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const ids = a.compareIds;
  const first = list.find((r) => ids.includes(r.id));
  const ds = dataset || first?.dataset_id || datasets[0] || '';
  const candidates = list.filter((r) => !ds || r.dataset_id === ds);
  const toggle = (id: string, on: boolean): void => a.setCompareIds(on ? [...ids, id] : ids.filter((x) => x !== id));

  const key = ids.length >= 2 ? ids.join(',') : null;
  const q = useArchiveQuery(app.client && connected ? app.client : null, key, (c, s) => c.archiveCompare(ids, s));
  const runs = q.state.data?.runs ?? [];
  const incomparable = runs.filter((r) => r.comparable === false);
  const topIncomparable = q.state.data?.comparable === false;

  const best = useMemo(() => curveSeries(runs, 'best'), [runs]);
  const hv = useMemo(() => curveSeries(runs, 'hv'), [runs]);
  const missingTime = runs.filter((r) => (r.curves?.best_so_far_map50_95 ?? []).some((p) => !isNum(p.cum_train_time_s))).map((r) => r.id);

  const baseCols: Column<ArchiveCompareRun>[] = [
    { key: 'id', header: 'Run', cell: (r) => <span className="mono wrap-any">{r.id}</span>, sort: (r) => r.id },
    { key: 'cmp', header: '비교 가능', cell: (r) => (r.comparable === false ? <Badge tone="warn">⚠ 불가</Badge> : r.comparable ? <Badge tone="ok">가능</Badge> : DASH) },
    { key: 'arm', header: 'arm', cell: (r) => (r.arm ? `${r.arm.space ?? DASH} / ${r.arm.method ?? DASH} / seed ${r.arm.seed ?? DASH}` : DASH) },
    { key: 'name', header: '판정 기준선', cell: (r) => r.judge_baseline?.name ?? DASH },
    { key: 'map', header: 'mAP50-95 (final 티어)', cell: (r) => fmtMap(r.judge_baseline?.map50_95), num: true, sort: (r) => r.judge_baseline?.map50_95 ?? null },
    { key: 'lat', header: 'latency [ms]', cell: (r) => fmtLat(r.judge_baseline?.latency_median_ms), num: true },
    { key: 'params', header: 'params_deploy', cell: (r) => fmtInt(r.judge_baseline?.params_deploy), num: true },
  ];
  const conditionPairs = useMemo(() => conditionDifferences(runs), [runs]);
  const conditionCols: Column<ArchiveCompareRun>[] = [
    { key: 'id', header: 'Run', cell: (r) => <span className="mono wrap-any">{r.id}</span>, sort: (r) => r.id },
    ...CONDITION_KEYS.map((key): Column<ArchiveCompareRun> => ({ key, header: key, cell: (r) => { const v = conditionValue(r, key); return v === null ? <span className="muted">{NO_RECORD}</span> : <span className="mono wrap-any">{String(v)}</span>; } })),
  ];
  const finals = runs.flatMap((r) => (r.finalists ?? []).map((f, i) => ({ run: r.id, i, f: recOf(f) })));
  const finalKeys = [...new Set(finals.flatMap((x) => Object.keys(x.f)))].filter((k) => finals.some((x) => ['string', 'number', 'boolean'].includes(typeof x.f[k]) || x.f[k] == null)).slice(0, 8);
  const finalCols: Column<(typeof finals)[number]>[] = [
    { key: '_run', header: 'Run', cell: (x) => <span className="mono wrap-any">{x.run}</span>, sort: (x) => x.run },
    ...finalKeys.map((k) => ({ key: k, header: k, cell: (x: (typeof finals)[number]) => (typeof x.f[k] === 'number' && !Number.isInteger(x.f[k]) ? fmtMap(x.f[k] as number) : fmtVal(x.f[k])), sort: (x: (typeof finals)[number]) => (typeof x.f[k] === 'number' ? (x.f[k] as number) : String(x.f[k] ?? '')), num: finals.some((x) => typeof x.f[k] === 'number') })),
  ];
  const pointRows = [...best, ...hv].flatMap((s, i) => s.points.map((p) => ({ k: `${i < best.length ? 'best' : 'hv'}|${s.key}|${p.key}`, kind: i < best.length ? 'best-so-far mAP50-95' : 'HV', run: s.key, x: p.x, y: p.y })));

  return (
    <div>
      <div className="page-head">
        <h1>Run 비교</h1>
      </div>
      <p className="muted" style={{ marginTop: -8 }}>같은 dataset의 NAS archive run 2~6개를 골라 탐색 곡선과 기준선을 나란히 봅니다 (읽기 전용 · 콘솔 run 후보 간 비교는 탐색 모니터링의 후보 상세 · 모델 비교).</p>
      <ConnectGate />
      {connected && (
        <>
          <Card title="비교할 run" id="compare-pick">
            {a.runs.status === 'error' && <Banner tone="err"><span className="wrap-any" data-testid="runs-error">run 목록을 불러오지 못했습니다: {a.runs.error}</span></Banner>}
            {a.runs.status === 'loading' && !a.runs.data && <p className="muted" role="status">run 목록 불러오는 중…</p>}
            <div className="filters" style={{ marginBottom: 8 }}>
              <SelectField label="dataset" value={ds} onChange={setDataset} options={datasets} allowEmpty={datasets.length ? undefined : '(없음)'} testId="compare-dataset" />
            </div>
            <fieldset>
              <legend>run 선택 ({ids.length}/6, 최소 2)</legend>
              <div className="stack" data-testid="compare-list">
                {candidates.map((r) => (
                  <label key={r.id} className="check" htmlFor={`cmp-${r.id}`}>
                    <input id={`cmp-${r.id}`} type="checkbox" checked={ids.includes(r.id)} disabled={!ids.includes(r.id) && ids.length >= 6} onChange={(e) => toggle(r.id, e.target.checked)} data-testid={`compare-check-${r.id}`} />
                    <span>
                      <span className="mono wrap-any">{r.id}</span> <StatusBadge status={r.status} /> <span className="help">best mAP50-95 {fmtMap(r.best?.map50_95)} · 세대 {r.generations?.done ?? DASH}</span>
                    </span>
                  </label>
                ))}
                {candidates.length === 0 && <p className="muted">선택할 run이 없습니다.</p>}
              </div>
            </fieldset>
            {ids.length === 1 && <p className="help" data-testid="compare-hint">run을 하나 더 선택하세요.</p>}
          </Card>

          {key && (
            <>
              <LoadBox state={q.state} what="비교 데이터" onRetry={q.reload}>
                {() => null}
              </LoadBox>
              {(incomparable.length > 0 || topIncomparable) && (
                <Banner tone="warn">
                  <span data-testid="compare-warning" className="wrap-any">
                    비교 불가 표시: {incomparable.length > 0 ? incomparable.map((r) => `${r.id} (${r.incomparable_reason ?? '사유 미기재'})`).join(' / ') : (q.state.data?.incomparable_reason ?? '사유 미기재')}. dataset 또는 metric protocol이 달라 곡선을 그려도 같은 척도로 읽을 수 없습니다.
                  </span>
                </Banner>
              )}
              {runs.length > 0 && (
                <>
                  <Card title="탐색 곡선" id="compare-curves" actions={<ViewToggle view={view} onChange={setView} idBase="compare" />}>
                    {view === 'chart' ? (
                      <div className="chart-grid">
                        <div>
                          <h3 style={{ fontSize: '1rem' }}>best-so-far mAP50-95 vs 누적 train_time</h3>
                          <LineChart chartId="cmp-best" title="best-so-far mAP50-95 대 누적 학습 시간" xLabel="누적 train_time [s]" yLabel="best-so-far mAP50-95 (탐색 티어)" series={best} height={280} yDigits={3} />
                        </div>
                        <div>
                          <h3 style={{ fontSize: '1rem' }}>Hypervolume vs 세대</h3>
                          <LineChart chartId="cmp-hv" title="세대별 hypervolume 비교" xLabel="세대" yLabel="HV (런타임)" series={hv} height={280} xInteger yDigits={2} />
                        </div>
                      </div>
                    ) : (
                      <DataTable caption="곡선 점" columns={[{ key: 'kind', header: '곡선', cell: (p) => p.kind }, { key: 'run', header: 'Run', cell: (p) => <span className="mono wrap-any">{p.run}</span> }, { key: 'x', header: 'x (누적 시간 s / 세대)', cell: (p) => fmtVal(p.x), num: true }, { key: 'y', header: 'y', cell: (p) => (isNum(p.y) ? p.y.toFixed(4) : DASH), num: true }]} rows={pointRows} rowKey={(p) => p.k} maxHeight testId="compare-points" />
                    )}
                    <ul className="legend" aria-label="run 범례" data-testid="compare-legend">
                      {runs.map((r, i) => (
                        <li key={r.id}>
                          <svg width="34" height="10" aria-hidden="true"><line x1="0" y1="5" x2="34" y2="5" stroke={runColor(i)} strokeWidth="2" strokeDasharray={DASHES[i % DASHES.length]} /></svg>
                          <span className="mono wrap-any">{r.id}</span>
                        </li>
                      ))}
                    </ul>
                    {missingTime.length > 0 && <p className="help" data-testid="missing-time">누적 학습 시간이 없는 점은 그리지 않았습니다 (0으로 채우지 않음): {missingTime.join(', ')}</p>}
                    <p className="help">best-so-far는 세대별 best 값의 누적 최댓값(계단선)이며 서버가 아티팩트에서 산출한 값입니다. 곡선은 탐색 티어 값이라 기준선(final 티어)과 직접 비교하지 않습니다.</p>
                  </Card>
                  <Card title="기준선 병치" id="compare-baselines">
                    <DataTable caption="run별 판정 기준선" columns={baseCols} rows={runs} rowKey={(r) => r.id} testId="compare-baseline-table" />
                  </Card>
                  <Card title="실행 조건" id="compare-conditions">
                    {conditionPairs.pairs.length > 0 && (
                      <Banner tone="warn">
                        <span data-testid="compare-conditions-differ" className="wrap-any"><strong>조건 상이</strong>: {conditionPairs.pairs.map((p) => `${p.a} ↔ ${p.b} (${p.keys.join(', ')})`).join(' / ')}. 조건이 다른 run의 수치는 같은 조건의 비교가 아닙니다.</span>
                      </Banner>
                    )}
                    <DataTable caption="run별 실행 조건 (서버가 아티팩트에서 읽은 값만, 없으면 기록 없음)" columns={conditionCols} rows={runs} rowKey={(r) => r.id} testId="compare-conditions-table" />
                    <p className="help" data-testid="compare-conditions-note">device·precision은 latency 측정 protocol에 기록된 값이며 학습 장치가 아닙니다. stage는 서버가 구성한 자유 문자열(검색 단계 + final)이고, protocol_hash는 run 매니페스트 또는 행 기록에서 읽은 값입니다.</p>
                    {conditionPairs.unknown.length > 0 && <p className="help" data-testid="compare-conditions-unknown">기록 없음이 있는 조건은 비교하지 않았습니다(같다고도 다르다고도 판단하지 않음): {conditionPairs.unknown.join(', ')}.</p>}
                  </Card>
                  <Card title="Finalists" id="compare-finalists">
                    {finals.length === 0 ? <p className="muted" data-testid="no-finalists">finalist 정보가 없습니다.</p> : <DataTable caption="run별 finalist" columns={finalCols} rows={finals} rowKey={(x) => `${x.run}:${x.i}`} testId="compare-finalists-table" maxHeight />}
                  </Card>
                  {(q.state.data?.notes ?? []).length > 0 && <p className="help wrap-any" data-testid="compare-notes">{(q.state.data?.notes ?? []).join(' ')}</p>}
                  <SourceNote source={q.state.data?.source} warnings={q.state.data?.warnings} />
                </>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

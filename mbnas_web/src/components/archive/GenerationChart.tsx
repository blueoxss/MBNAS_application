/**
 * Generation progress card (design 4.1-4): three line charts over the generation index (hypervolume, best mAP50-95,
 * best small recall) whose points select a generation, and the generation table. The judge baseline mAP50-95 is a
 * horizontal reference line with a tier note. HV and stagnation are the runtime's own values (no recomputation).
 */
import { DataTable, type Column } from '../DataTable';
import { Badge, Card } from '../ui';
import type { ArchiveGeneration } from '../../archive/types';
import { DASH, fmtDelta, fmtFixed, fmtInt, fmtLat, fmtMap, isNum } from '../../archive/format';
import { LineChart, type LineSeries } from './LineChart';
import { ViewToggle } from './ArchiveBits';
import { useState } from 'react';

interface Props {
  generations: readonly ArchiveGeneration[];
  selected: number | null;
  onSelect: (g: number) => void;
  baselineMap: number | null;
  baselineName: string | null;
  baselineTierNote: string;
  note?: string | null;
}

function series(key: string, label: string, color: string, gens: readonly ArchiveGeneration[], pick: (g: ArchiveGeneration) => number | null | undefined, fmt: (v: number | null | undefined) => string): LineSeries {
  return {
    key,
    label,
    color,
    shape: 'circle',
    points: gens.map((g) => ({ key: String(g.generation), x: g.generation, y: isNum(pick(g)) ? (pick(g) as number) : null, label: `세대 ${g.generation}: ${label} ${fmt(pick(g))}. Enter로 세대 상세 열기.` })),
  };
}

export function modeSummary(m: Record<string, number> | undefined): string {
  const e = Object.entries(m ?? {}).filter(([, n]) => n > 0);
  return e.length ? e.map(([k, n]) => `${k} ${n}`).join(' · ') : DASH;
}

export function GenerationChart({ generations, selected, onSelect, baselineMap, baselineName, baselineTierNote, note }: Props): JSX.Element {
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const gens = [...generations].sort((a, b) => a.generation - b.generation);
  const sel = selected === null ? null : String(selected);
  const pick = (_s: string, p: string): void => onSelect(Number(p));
  const refs = baselineMap !== null ? [{ key: 'baseline-map', y: baselineMap, label: `기준선 ${baselineName ?? ''} ${baselineMap.toFixed(4)} (${baselineTierNote})`, color: '#14231c' }] : [];

  const cols: Column<ArchiveGeneration>[] = [
    { key: 'gen', header: '세대', cell: (g) => <button className="btn btn-sm" type="button" onClick={() => onSelect(g.generation)} aria-label={`세대 ${g.generation} 상세 열기`} data-testid={`gen-open-${g.generation}`}>{g.generation}</button>, sort: (g) => g.generation, num: true },
    { key: 'phase', header: 'phase', cell: (g) => g.phase ?? DASH },
    { key: 'valid', header: 'valid / pop', cell: (g) => `${fmtInt(g.valid)} / ${fmtInt(g.population)}`, sort: (g) => g.valid ?? null, num: true },
    { key: 'uniq', header: 'unique fp', cell: (g) => fmtInt(g.unique_fingerprints), sort: (g) => g.unique_fingerprints ?? null, num: true },
    { key: 'newfp', header: '새 fp', cell: (g) => fmtInt(g.new_fingerprints), sort: (g) => g.new_fingerprints ?? null, num: true },
    { key: 'hv', header: 'HV (Δ 전세대)', cell: (g) => <span>{fmtFixed(g.hypervolume, 4)} <span className="muted">({fmtDelta(g.delta_vs_prev?.hypervolume)})</span>{g.hv_improved ? <Badge tone="ok">HV↑</Badge> : null}</span>, sort: (g) => g.hypervolume ?? null, num: true },
    { key: 'map', header: 'best mAP50-95 (Δ 전세대 / Δ vs baseline*)', cell: (g) => <span>{fmtMap(g.best_map50_95)} <span className="muted">({fmtDelta(g.delta_vs_prev?.best_map50_95)} / {fmtDelta(g.delta_vs_baseline?.best_map50_95)})</span></span>, sort: (g) => g.best_map50_95 ?? null, num: true },
    { key: 'sr', header: 'best small recall', cell: (g) => <span>{fmtMap(g.best_small_recall)} <span className="muted">({fmtDelta(g.delta_vs_prev?.best_small_recall)})</span></span>, sort: (g) => g.best_small_recall ?? null, num: true },
    { key: 'lat', header: 'best latency [ms] (Δ 전세대)', cell: (g) => <span>{fmtLat(g.best_latency)} <span className="muted">({fmtDelta(g.delta_vs_prev?.best_latency, 2)})</span></span>, sort: (g) => g.best_latency ?? null, num: true },
    { key: 'stag', header: 'stagnation', cell: (g) => fmtInt(g.stagnation), sort: (g) => g.stagnation ?? null, num: true },
    { key: 'modes', header: 'lineage 모드', cell: (g) => <span className="wrap-any">{modeSummary(g.lineage_modes)}</span> },
  ];

  return (
    <Card title="세대 진행" id="archive-generations">
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
        <ViewToggle view={view} onChange={setView} idBase="gens" />
        <span className="help">점을 클릭(Enter)하면 그 세대의 상세가 열립니다.</span>
      </div>
      {gens.length === 0 ? (
        <p className="muted" data-testid="gens-empty">완료된 세대가 없습니다.</p>
      ) : view === 'chart' ? (
        <div className="chart-grid3" data-testid="gen-charts">
          <div>
            <h3 style={{ fontSize: '0.95rem' }}>Hypervolume</h3>
            <LineChart chartId="gen-hv" title="세대별 hypervolume (런타임 산출)" xLabel="세대" yLabel="HV" series={[series('hv', 'HV', '#3b528b', gens, (g) => g.hypervolume, (v) => fmtFixed(v, 4))]} selectedKey={sel} onSelect={pick} xInteger height={220} yDigits={2} />
          </div>
          <div>
            <h3 style={{ fontSize: '0.95rem' }}>best mAP50-95</h3>
            <LineChart chartId="gen-map" title="세대별 best mAP50-95와 기준선" xLabel="세대" yLabel="mAP50-95" series={[series('map', 'best mAP50-95', '#0072b2', gens, (g) => g.best_map50_95, fmtMap)]} refs={refs} selectedKey={sel} onSelect={pick} xInteger height={220} yDigits={3} />
          </div>
          <div>
            <h3 style={{ fontSize: '0.95rem' }}>best small recall</h3>
            <LineChart chartId="gen-recall" title="세대별 best small-target recall" xLabel="세대" yLabel="small recall" series={[series('recall', 'best small recall', '#d55e00', gens, (g) => g.best_small_recall, fmtMap)]} selectedKey={sel} onSelect={pick} xInteger height={220} yDigits={3} />
          </div>
        </div>
      ) : null}
      <div style={{ marginTop: 10 }}>
        <DataTable caption="세대 요약" columns={cols} rows={gens} rowKey={(g) => String(g.generation)} selectedKey={sel} testId="generation-table" empty="완료된 세대가 없습니다." maxHeight />
      </div>
      <p className="help" style={{ marginTop: 6 }}>
        * Δ vs baseline = 세대 best(탐색 티어) − 판정 기준선 mAP50-95({baselineTierNote}); 티어가 달라 참고용입니다. HV·stagnation·best 값은 런타임이 기록한 값 그대로이며 NSGA-II 다목적이라 스칼라 fitness는 없습니다.
      </p>
      {gens.some((g) => g.delta_vs_baseline?.note) && <p className="help wrap-any" data-testid="baseline-delta-note">{gens.find((g) => g.delta_vs_baseline?.note)?.delta_vs_baseline?.note}</p>}
      {note && <p className="help wrap-any">{note}</p>}
    </Card>
  );
}

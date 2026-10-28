/**
 * Scatter charts of the archive (design 4.1-5): (a) mAP50-95 | mAP50 | small recall vs latency (ms or FPS) and
 * (b) mAP50-95 vs params_deploy. Colour = generation (sequential ramp), diamond = final-tier row, square = baseline
 * (named), star = gen0 anchor. The 2D non-dominated front (mAP up, latency down) is joined by a staircase line for
 * the generation chosen with the slider. Every point is a button (Enter / Space / click) that opens the candidate;
 * "표로 보기" shows exactly the plotted rows in a table.
 */
import { memo, useId, useMemo, useState } from 'react';
import { niceTicks } from '../Scatter';
import { DataTable, type Column } from '../DataTable';
import { Badge, Card, SelectField } from '../ui';
import type { ArchiveBaselineList, ArchiveCandidateRow, ArchiveGeneration, Num } from '../../archive/types';
import { fmtFps, fmtInt, fmtLat, fmtMap, isNum, tierInfo } from '../../archive/format';
import { front2d, orderFront2d, type ObjPoint } from '../../archive/pareto';
import { FRONT_COLOR, MARK_OUTLINE, genColor, rampColor } from '../../archive/palette';
import { Mark, ViewToggle, useBoxWidth, type MarkShape } from './ArchiveBits';

export type YMetric = 'map50_95' | 'map50' | 'small_target_recall';
const Y_LABEL: Record<YMetric, string> = { map50_95: 'mAP50-95', map50: 'mAP50', small_target_recall: 'small-target recall' };
/** the plotted rows above this count only get a tab stop when on the front or in the newest generation */
const TAB_ALL_MAX = 200;

export interface SPoint {
  id: string; // row key: candidate_id + tier
  cid: string;
  generation: number;
  tier: string;
  final: boolean;
  x: number;
  y: number;
  lat: number | null;
  params: number | null;
  map: number | null;
}

interface Extra {
  key: string;
  name: string;
  kind: 'baseline' | 'anchor';
  judge: boolean;
  x: number;
  y: number;
}

const yOf = (r: ArchiveCandidateRow, m: YMetric): number | null => {
  const v = r.metrics?.[m];
  return isNum(v) ? v : null;
};

/** Plotted rows: status ok with finite x and y. A row with a missing value is not plotted (listed in `skipped`). */
export function buildPoints(rows: readonly ArchiveCandidateRow[], xKey: 'latency' | 'params', y: YMetric): { points: SPoint[]; skipped: number } {
  const points: SPoint[] = [];
  let skipped = 0;
  for (const r of rows) {
    if (r.status !== 'ok') continue;
    const x = xKey === 'latency' ? r.metrics?.latency_median_ms : r.metrics?.params_deploy;
    const yy = yOf(r, y);
    if (!isNum(x) || yy === null) {
      skipped += 1;
      continue;
    }
    const ti = tierInfo(r.tier);
    points.push({
      id: `${r.candidate_id}|${r.tier ?? ''}`,
      cid: r.candidate_id,
      generation: r.generation,
      tier: r.tier ?? '',
      final: r.is_final === true || ti.kind === 'final',
      x,
      y: yy,
      lat: isNum(r.metrics?.latency_median_ms) ? r.metrics.latency_median_ms : null,
      params: isNum(r.metrics?.params_deploy) ? r.metrics.params_deploy : null,
      map: yOf(r, 'map50_95'),
    });
  }
  return { points, skipped };
}

function extras(b: ArchiveBaselineList | null, xKey: 'latency' | 'params', y: YMetric, judgeName: string | null): Extra[] {
  const out: Extra[] = [];
  const add = (name: string, kind: 'baseline' | 'anchor', m: { map50_95?: Num; map50?: Num; small_target_recall?: Num; latency_median_ms?: Num; params_deploy?: Num }): void => {
    const x = xKey === 'latency' ? m.latency_median_ms : m.params_deploy;
    const yy = m[y];
    if (isNum(x) && isNum(yy)) out.push({ key: `${kind}:${name}`, name, kind, judge: name === judgeName, x, y: yy });
  };
  for (const bl of b?.baselines ?? []) add(bl.name, bl.kind === 'anchor' ? 'anchor' : 'baseline', bl);
  if (b?.anchor?.metrics && !(b.baselines ?? []).some((x) => x.kind === 'anchor')) add(`anchor ${b.anchor.candidate_id}`, 'anchor', b.anchor.metrics);
  return out;
}

interface ChartProps {
  chartId: string;
  title: string;
  xKind: 'latency' | 'params';
  xFps: boolean;
  yLabel: string;
  points: SPoint[];
  extra: Extra[];
  maxGen: number;
  frontIds: ReadonlySet<string>;
  frontLine: SPoint[];
  onOpen: (cid: string) => void;
}

const xVal = (v: number, kind: 'latency' | 'params', fps: boolean): number => (kind === 'latency' && fps ? 1000 / v : v);

const Layer = memo(function Layer({ points, sx, sy, maxGen, frontIds, xKind, xFps, onOpen, onHover, tabAll, newest }: {
  points: SPoint[];
  sx: (v: number) => number;
  sy: (v: number) => number;
  maxGen: number;
  frontIds: ReadonlySet<string>;
  xKind: 'latency' | 'params';
  xFps: boolean;
  onOpen: (cid: string) => void;
  onHover: (p: SPoint | null) => void;
  tabAll: boolean;
  newest: number;
}) {
  return (
    <g role="group" aria-label="후보 점">
      {points.map((p) => {
        const onFront = frontIds.has(p.id);
        const shape: MarkShape = p.final ? 'diamond' : 'circle';
        const open = (): void => onOpen(p.cid);
        const label = `${p.cid}, 세대 ${p.generation}, ${p.tier || '티어 미기재'}${p.final ? ' (최종)' : ''}${onFront ? ', 정면' : ''}: x ${xKind === 'latency' ? (xFps ? `${fmtFps(p.x)} FPS` : `${fmtLat(p.x)} ms`) : fmtInt(p.x)}, y ${fmtMap(p.y)}. Enter로 상세 열기.`;
        return (
          <g
            key={p.id}
            className="pt"
            role="button"
            tabIndex={tabAll || onFront || p.generation === newest ? 0 : -1}
            aria-label={label}
            data-testid="scatter-point"
            data-cid={p.cid}
            data-gen={p.generation}
            data-front={onFront ? 'true' : 'false'}
            transform={`translate(${sx(xVal(p.x, xKind, xFps)).toFixed(1)} ${sy(p.y).toFixed(1)})`}
            onClick={open}
            onMouseEnter={() => onHover(p)}
            onMouseLeave={() => onHover(null)}
            onFocus={() => onHover(p)}
            onBlur={() => onHover(null)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                open();
              }
            }}
          >
            <circle className="hit" />
            <circle className="ring" />
            {onFront && <circle r={p.final ? 11 : 9} fill="none" stroke={FRONT_COLOR} strokeWidth={2.5} />}
            <g fill={genColor(p.generation, maxGen)} stroke={MARK_OUTLINE} strokeWidth={1.2}>
              <Mark shape={shape} r={5.5} />
            </g>
          </g>
        );
      })}
    </g>
  );
});

function Chart({ chartId, title, xKind, xFps, yLabel, points, extra, maxGen, frontIds, frontLine, onOpen }: ChartProps): JSX.Element {
  const [boxRef, width] = useBoxWidth();
  const [hover, setHover] = useState<SPoint | null>(null);
  const height = 340;
  const m = { l: 62, r: 16, t: 14, b: 50 };
  const iw = width - m.l - m.r;
  const ih = height - m.t - m.b;
  const dom = useMemo(() => {
    const xs = [...points.map((p) => xVal(p.x, xKind, xFps)), ...extra.map((e) => xVal(e.x, xKind, xFps))];
    const ys = [...points.map((p) => p.y), ...extra.map((e) => e.y)];
    const lo = (a: number[], d: number): number => (a.length ? Math.min(...a) : d);
    const hi = (a: number[], d: number): number => (a.length ? Math.max(...a) : d);
    const xmin = lo(xs, 0);
    const xmax = hi(xs, 1);
    const ymin = lo(ys, 0);
    const ymax = hi(ys, 1);
    const xp = xmax > xmin ? (xmax - xmin) * 0.07 : Math.max(0.5, Math.abs(xmin) * 0.1);
    const yp = ymax > ymin ? (ymax - ymin) * 0.08 : 0.02;
    return { x: [xmin - xp, xmax + xp] as [number, number], y: [Math.max(0, ymin - yp), ymax + yp] as [number, number] };
  }, [points, extra, xKind, xFps]);
  const sx = (v: number): number => m.l + ((v - dom.x[0]) / (dom.x[1] - dom.x[0] || 1)) * iw;
  const sy = (v: number): number => m.t + ih - ((v - dom.y[0]) / (dom.y[1] - dom.y[0] || 1)) * ih;
  const xTicks = niceTicks(dom.x[0], dom.x[1], Math.max(3, Math.floor(iw / 90)));
  const yTicks = niceTicks(dom.y[0], dom.y[1], 5);
  const xTitle = xKind === 'params' ? 'params_deploy [개]' : xFps ? '처리량 [FPS] = 1000 / latency_median_ms' : 'latency_median_ms [ms]';
  const newest = points.reduce((g, p) => Math.max(g, p.generation), -1);
  const stair: string[] = [];
  frontLine.forEach((p, i) => {
    const x = sx(xVal(p.x, xKind, xFps));
    const y = sy(p.y);
    if (i > 0) stair.push(`${x.toFixed(1)},${sy(frontLine[i - 1].y).toFixed(1)}`);
    stair.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  });
  const tipX = hover ? sx(xVal(hover.x, xKind, xFps)) : 0;
  const tipY = hover ? sy(hover.y) : 0;
  const tickText = (t: number): string => (xKind === 'params' ? `${Number((t / 1e6).toPrecision(3))}M` : String(Number(t.toPrecision(4))));

  return (
    <div ref={boxRef} className="chart-box" data-testid={`chart-${chartId}`} style={{ position: 'relative' }}>
      <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="group" aria-labelledby={`${chartId}-t`} aria-describedby={`${chartId}-d`}>
        <title id={`${chartId}-t`}>{title}</title>
        <desc id={`${chartId}-d`}>
          산점도: 후보 {points.length}개, 기준선 {extra.length}개. X축 {xTitle}. Y축 {yLabel}. 각 후보 점은 버튼이며 같은 데이터가 &quot;표로 보기&quot;에 있습니다.
        </desc>
        {yTicks.map((t) => (
          <g key={`y${t}`}>
            <line className="grid" x1={m.l} x2={m.l + iw} y1={sy(t)} y2={sy(t)} />
            <text x={m.l - 6} y={sy(t) + 4} textAnchor="end">
              {Number(t.toPrecision(4))}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <g key={`x${t}`}>
            <line className="grid" x1={sx(t)} x2={sx(t)} y1={m.t} y2={m.t + ih} />
            <text x={sx(t)} y={m.t + ih + 16} textAnchor="middle">
              {tickText(t)}
            </text>
          </g>
        ))}
        <line className="axis" x1={m.l} x2={m.l + iw} y1={m.t + ih} y2={m.t + ih} />
        <line className="axis" x1={m.l} x2={m.l} y1={m.t} y2={m.t + ih} />
        <text className="axis-title" x={m.l + iw / 2} y={height - 8} textAnchor="middle">
          {xTitle}
        </text>
        <text className="axis-title" transform={`translate(14 ${m.t + ih / 2}) rotate(-90)`} textAnchor="middle">
          {yLabel}
        </text>
        {stair.length > 1 && <polyline points={stair.join(' ')} fill="none" stroke={FRONT_COLOR} strokeWidth={2} strokeDasharray="7 3" data-testid="front-line" />}
        {extra.map((e) => {
          const cx = sx(xVal(e.x, xKind, xFps));
          const cy = sy(e.y);
          return (
            <g key={e.key} role="img" aria-label={`${e.kind === 'anchor' ? '앵커' : '기준선'} ${e.name}${e.judge ? ' (판정 기준선)' : ''}: x ${xKind === 'latency' ? `${fmtLat(e.x)} ms` : fmtInt(e.x)}, y ${fmtMap(e.y)}`} data-testid={`scatter-${e.kind}`} data-name={e.name} transform={`translate(${cx.toFixed(1)} ${cy.toFixed(1)})`}>
              <g fill={e.kind === 'anchor' ? '#ffffff' : e.judge ? '#14231c' : '#5b6770'} stroke={MARK_OUTLINE} strokeWidth={e.judge ? 2 : 1.2}>
                <Mark shape={e.kind === 'anchor' ? 'star' : 'square'} r={e.judge ? 7 : 5.5} />
              </g>
              <text x={cx > width - 130 ? -9 : 9} y={-8} textAnchor={cx > width - 130 ? 'end' : 'start'} style={{ fontSize: 11, fontWeight: e.judge ? 700 : 400 }}>
                {e.name}
              </text>
            </g>
          );
        })}
        <Layer points={points} sx={sx} sy={sy} maxGen={maxGen} frontIds={frontIds} xKind={xKind} xFps={xFps} onOpen={onOpen} onHover={setHover} tabAll={points.length <= TAB_ALL_MAX} newest={newest} />
      </svg>
      {hover && (
        <div className="chart-tip" role="presentation" style={{ left: Math.min(Math.max(tipX + 12, 4), Math.max(4, width - 230)), top: Math.max(0, tipY - 12) }}>
          <strong>{hover.cid}</strong> · 세대 {hover.generation} · {hover.tier || '—'}
          <br />
          x {xKind === 'latency' ? (xFps ? `${fmtFps(hover.x)} FPS` : `${fmtLat(hover.x)} ms`) : fmtInt(hover.x)} · y {fmtMap(hover.y)}
        </div>
      )}
    </div>
  );
}

export interface ArchiveScatterProps {
  rows: readonly ArchiveCandidateRow[];
  generations: readonly ArchiveGeneration[];
  baselines: ArchiveBaselineList | null;
  judgeName: string | null;
  onOpen: (cid: string) => void;
}

export function ArchiveScatter({ rows, generations, baselines, judgeName, onOpen }: ArchiveScatterProps): JSX.Element {
  const uid = useId();
  const [yMetric, setYMetric] = useState<YMetric>('map50_95');
  const [fps, setFps] = useState(false);
  const [tier, setTier] = useState('');
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const maxGen = useMemo(() => rows.reduce((g, r) => Math.max(g, r.generation), generations.reduce((g, x) => Math.max(g, x.generation), 0)), [rows, generations]);
  const [slider, setSlider] = useState<number | null>(null);
  const g = Math.min(slider ?? maxGen, maxGen);
  const tiers = useMemo(() => [...new Set(rows.map((r) => r.tier).filter((t): t is string => !!t))].sort(), [rows]);
  const shownRows = useMemo(() => rows.filter((r) => r.generation <= g && (!tier || r.tier === tier)), [rows, g, tier]);

  const a = useMemo(() => buildPoints(shownRows, 'latency', yMetric), [shownRows, yMetric]);
  const b = useMemo(() => buildPoints(shownRows, 'params', 'map50_95'), [shownRows]);
  const extraA = useMemo(() => extras(baselines, 'latency', yMetric, judgeName), [baselines, yMetric, judgeName]);
  const extraB = useMemo(() => extras(baselines, 'params', 'map50_95', judgeName), [baselines, judgeName]);

  // front (mAP50-95 up, latency down) of rows up to generation g: the server's list when the tier filter is off
  const frontInfo = useMemo(() => {
    const objPts: ObjPoint[] = shownRows.filter((r) => r.status === 'ok').map((r) => ({ id: `${r.candidate_id}|${r.tier ?? ''}`, map: yOf(r, 'map50_95'), lat: r.metrics?.latency_median_ms }));
    const serverIds = generations.find((x) => x.generation === g)?.front2d;
    if (!tier && serverIds) {
      const set = new Set(serverIds);
      // server ids are candidate ids; map them to every plotted row of that candidate
      const ids = objPts.filter((p) => set.has(p.id.split('|')[0])).map((p) => p.id);
      return { ids, source: '서버 산출 (front2d)' as const, ordered: orderFront2d(objPts, ids) };
    }
    const ids = front2d(objPts);
    return { ids, source: '클라이언트 계산 (표시 중인 행 기준)' as const, ordered: orderFront2d(objPts, ids) };
  }, [shownRows, generations, g, tier]);
  const frontSet = useMemo(() => new Set(frontInfo.ids), [frontInfo]);
  const byId = useMemo(() => new Map(a.points.map((p) => [p.id, p])), [a.points]);
  const lineA = useMemo(() => (yMetric === 'map50_95' ? frontInfo.ordered.map((o) => byId.get(o.id)).filter((p): p is SPoint => !!p) : []), [frontInfo, byId, yMetric]);

  const cols: Column<SPoint>[] = [
    { key: 'cid', header: '후보', cell: (p) => <button className="btn btn-sm" type="button" onClick={() => onOpen(p.cid)} aria-label={`${p.cid} 상세 열기`}>{p.cid}</button>, sort: (p) => p.cid },
    { key: 'gen', header: '세대', cell: (p) => p.generation, sort: (p) => p.generation, num: true },
    { key: 'tier', header: '티어', cell: (p) => p.tier || '—', sort: (p) => p.tier },
    { key: 'x', header: fps ? 'FPS' : 'latency [ms]', cell: (p) => (fps ? fmtFps(p.lat) : fmtLat(p.lat)), sort: (p) => p.lat, num: true },
    { key: 'y', header: Y_LABEL[yMetric], cell: (p) => fmtMap(p.y), sort: (p) => p.y, num: true },
    { key: 'params', header: 'params_deploy', cell: (p) => fmtInt(p.params), sort: (p) => p.params, num: true },
    { key: 'front', header: '정면(2D)', cell: (p) => (frontSet.has(p.id) ? <Badge tone="err">정면</Badge> : '—'), sort: (p) => (frontSet.has(p.id) ? 0 : 1) },
  ];

  return (
    <Card title="산점도" id="archive-scatter">
      <div className="filters" style={{ marginBottom: 10 }}>
        <SelectField label="Y 지표" value={yMetric} onChange={(v) => setYMetric(v as YMetric)} options={[{ value: 'map50_95', label: 'mAP50-95' }, { value: 'map50', label: 'mAP50' }, { value: 'small_target_recall', label: 'small-target recall' }]} testId="scatter-y" />
        <SelectField label="티어" value={tier} onChange={setTier} allowEmpty="전체 티어" options={tiers} testId="scatter-tier" />
        <SelectField label="x축 (a)" value={fps ? 'fps' : 'ms'} onChange={(v) => setFps(v === 'fps')} options={[{ value: 'ms', label: 'latency [ms]' }, { value: 'fps', label: 'FPS (1000/ms)' }]} testId="scatter-x" />
        <div className="field">
          <label htmlFor={`${uid}-gen`}>
            정면·점 표시 세대 ≤ <span data-testid="scatter-gen-value">{g}</span>
          </label>
          <input id={`${uid}-gen`} type="range" min={0} max={Math.max(0, maxGen)} step={1} value={g} onChange={(e) => setSlider(Number(e.target.value))} data-testid="scatter-gen" aria-valuetext={`세대 ${g}까지`} style={{ minHeight: 44 }} />
        </div>
      </div>
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
        <ViewToggle view={view} onChange={setView} idBase="scatter" />
        <span className="help" data-testid="scatter-count">
          표시 {a.points.length}행 · 정면 {frontInfo.ids.length}개{a.skipped > 0 ? ` · 값 결측으로 제외 ${a.skipped}행` : ''}
        </span>
      </div>
      {view === 'chart' ? (
        <div className="chart-grid">
          <div>
            <h3 style={{ fontSize: '1rem' }}>(a) {Y_LABEL[yMetric]} – {fps ? 'FPS' : 'latency'}</h3>
            <Chart chartId="scatter-a" title={`${Y_LABEL[yMetric]} 대 ${fps ? '처리량' : '지연'} 산점도`} xKind="latency" xFps={fps} yLabel={Y_LABEL[yMetric]} points={a.points} extra={extraA} maxGen={maxGen} frontIds={frontSet} frontLine={lineA} onOpen={onOpen} />
          </div>
          <div>
            <h3 style={{ fontSize: '1rem' }}>(b) mAP50-95 – params_deploy</h3>
            <Chart chartId="scatter-b" title="mAP50-95 대 params_deploy 산점도" xKind="params" xFps={false} yLabel="mAP50-95" points={b.points} extra={extraB} maxGen={maxGen} frontIds={frontSet} frontLine={[]} onOpen={onOpen} />
          </div>
        </div>
      ) : (
        <DataTable caption="산점도 (a)에 그려진 행과 동일" columns={cols} rows={a.points} rowKey={(p) => p.id} maxHeight empty="그릴 수 있는 행이 없습니다." testId="scatter-table" />
      )}
      <Legend maxGen={maxGen} source={frontInfo.source} judgeName={judgeName} />
    </Card>
  );
}

function Legend({ maxGen, source, judgeName }: { maxGen: number; source: string; judgeName: string | null }): JSX.Element {
  const grad = `linear-gradient(90deg, ${[0, 0.25, 0.5, 0.75, 1].map(rampColor).join(', ')})`;
  const item = (shape: MarkShape, fill: string, text: string): JSX.Element => (
    <li>
      <svg width="16" height="16" aria-hidden="true">
        <g transform="translate(8 8)" fill={fill} stroke={MARK_OUTLINE} strokeWidth="1.2">
          <Mark shape={shape} r={5} />
        </g>
      </svg>
      {text}
    </li>
  );
  return (
    <div style={{ marginTop: 10 }} data-testid="scatter-legend">
      <ul className="legend" aria-label="범례">
        <li>
          세대 색:
          <span aria-hidden="true" style={{ display: 'inline-block', width: 96, height: 12, background: grad, border: `1px solid ${MARK_OUTLINE}`, borderRadius: 3 }} />
          0 … {maxGen}
        </li>
        {item('circle', '#5ec962', '탐색 티어 행 (원)')}
        {item('diamond', '#5ec962', 'final 티어 행 (다이아)')}
        {item('square', '#5b6770', `기준선 (사각)${judgeName ? `, 굵은 외곽 = 판정 기준선 ${judgeName}` : ''}`)}
        {item('star', '#ffffff', 'gen0 앵커 (별)')}
        <li>
          <svg width="34" height="10" aria-hidden="true">
            <line x1="0" y1="5" x2="34" y2="5" stroke={FRONT_COLOR} strokeWidth="2" strokeDasharray="7 3" />
          </svg>
          2D 정면 선 / 붉은 고리 = 정면
        </li>
      </ul>
      <p className="help" style={{ marginTop: 6 }}>
        정면 = (mAP50-95↑, latency↓) 2D 비지배 집합, 선택 세대까지의 ok 행 · 출처: {source}. HV는 런타임 산출값이며 여기서 계산하지 않습니다. 기준선(final 티어, latency v2)과 탐색 티어 행은 학습 길이·측정 프로토콜이 달라 직접 비교할 수 없습니다.
      </p>
    </div>
  );
}

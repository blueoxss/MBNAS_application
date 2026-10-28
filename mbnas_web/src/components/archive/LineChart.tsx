/**
 * Pure SVG multi-series line chart (no library). Points are buttons (Enter / Space / click) when `onSelect` is
 * given. A missing y value (null) breaks the line and draws no mark: it is never plotted as 0. The same numbers
 * are always available in a table next to the chart (the caller renders it).
 */
import { useMemo, useState } from 'react';
import { niceTicks } from '../Scatter';
import { MARK_OUTLINE } from '../../archive/palette';
import { isNum } from '../../archive/format';
import { Mark, useBoxWidth, type MarkShape } from './ArchiveBits';

export interface LinePoint {
  key: string;
  x: number;
  y: number | null | undefined;
  /** accessible name of the point (and its tooltip text) */
  label: string;
}

export interface LineSeries {
  key: string;
  label: string;
  color: string;
  shape: MarkShape;
  dash?: string;
  /** draw a staircase (best-so-far) instead of straight segments */
  step?: boolean;
  points: LinePoint[];
}

export interface RefLine {
  key: string;
  y: number;
  label: string;
  color: string;
}

interface Props {
  chartId: string;
  title: string;
  xLabel: string;
  yLabel: string;
  series: LineSeries[];
  refs?: RefLine[];
  selectedKey?: string | null;
  onSelect?: (seriesKey: string, pointKey: string) => void;
  height?: number;
  xInteger?: boolean;
  yDigits?: number;
}

export function LineChart({ chartId, title, xLabel, yLabel, series, refs = [], selectedKey, onSelect, height = 240, xInteger, yDigits = 3 }: Props): JSX.Element {
  const [boxRef, width] = useBoxWidth();
  const [hover, setHover] = useState<{ sk: string; pk: string } | null>(null);
  const m = { l: 62, r: 14, t: 12, b: 46 };
  const iw = width - m.l - m.r;
  const ih = height - m.t - m.b;

  const dom = useMemo(() => {
    const xs: number[] = [];
    const ys: number[] = [];
    for (const s of series) for (const p of s.points) if (isNum(p.x) && isNum(p.y)) { xs.push(p.x); ys.push(p.y); }
    for (const r of refs) if (isNum(r.y)) ys.push(r.y);
    const xmin = xs.length ? Math.min(...xs) : 0;
    const xmax = xs.length ? Math.max(...xs) : 1;
    const ymin = ys.length ? Math.min(...ys) : 0;
    const ymax = ys.length ? Math.max(...ys) : 1;
    const xp = xmax > xmin ? (xmax - xmin) * 0.05 : 0.5;
    const yp = ymax > ymin ? (ymax - ymin) * 0.08 : Math.max(Math.abs(ymax) * 0.05, 0.01);
    return { x: [xmin - xp, xmax + xp] as [number, number], y: [ymin - yp, ymax + yp] as [number, number] };
  }, [series, refs]);

  const sx = (v: number): number => m.l + ((v - dom.x[0]) / (dom.x[1] - dom.x[0] || 1)) * iw;
  const sy = (v: number): number => m.t + ih - ((v - dom.y[0]) / (dom.y[1] - dom.y[0] || 1)) * ih;
  const xTicks = niceTicks(dom.x[0], dom.x[1], Math.max(3, Math.floor(iw / 90))).filter((t) => !xInteger || Number.isInteger(t));
  const yTicks = niceTicks(dom.y[0], dom.y[1], 4);
  const nPts = series.reduce((n, s) => n + s.points.filter((p) => isNum(p.y)).length, 0);

  // line path per series: runs of consecutive finite points
  const paths = series.map((s) => {
    const runs: { x: number; y: number }[][] = [];
    let cur: { x: number; y: number }[] = [];
    for (const p of s.points) {
      if (isNum(p.x) && isNum(p.y)) cur.push({ x: p.x, y: p.y });
      else if (cur.length) { runs.push(cur); cur = []; }
    }
    if (cur.length) runs.push(cur);
    return runs.map((r) => {
      const pts: string[] = [];
      r.forEach((p, i) => {
        if (s.step && i > 0) pts.push(`${sx(p.x).toFixed(1)},${sy(r[i - 1].y).toFixed(1)}`);
        pts.push(`${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`);
      });
      return pts.join(' ');
    });
  });

  const tip = hover ? series.find((s) => s.key === hover.sk)?.points.find((p) => p.key === hover.pk) : undefined;

  return (
    <div ref={boxRef} className="chart-box line-chart" data-testid={`chart-${chartId}`} style={{ position: 'relative' }}>
      <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="group" aria-labelledby={`${chartId}-t`} aria-describedby={`${chartId}-d`}>
        <title id={`${chartId}-t`}>{title}</title>
        <desc id={`${chartId}-d`}>
          선 그래프, 점 {nPts}개. X축 {xLabel}. Y축 {yLabel}. {onSelect ? '각 점은 버튼이며 ' : ''}같은 수치가 아래 표에 있습니다.
        </desc>
        {yTicks.map((t) => (
          <g key={`y${t}`}>
            <line className="grid" x1={m.l} x2={m.l + iw} y1={sy(t)} y2={sy(t)} />
            <text x={m.l - 6} y={sy(t) + 4} textAnchor="end">
              {Number(t.toFixed(yDigits + 2))}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <g key={`x${t}`}>
            <line className="grid" x1={sx(t)} x2={sx(t)} y1={m.t} y2={m.t + ih} />
            <text x={sx(t)} y={m.t + ih + 16} textAnchor="middle">
              {Number(t.toPrecision(6))}
            </text>
          </g>
        ))}
        <line className="axis" x1={m.l} x2={m.l + iw} y1={m.t + ih} y2={m.t + ih} />
        <line className="axis" x1={m.l} x2={m.l} y1={m.t} y2={m.t + ih} />
        <text className="axis-title" x={m.l + iw / 2} y={height - 8} textAnchor="middle">
          {xLabel}
        </text>
        <text className="axis-title" transform={`translate(13 ${m.t + ih / 2}) rotate(-90)`} textAnchor="middle">
          {yLabel}
        </text>
        {refs.map((r) => (
          <g key={r.key} data-testid={`ref-${r.key}`}>
            <line x1={m.l} x2={m.l + iw} y1={sy(r.y)} y2={sy(r.y)} stroke={r.color} strokeWidth={1.5} strokeDasharray="6 4" />
            <text x={m.l + iw - 4} y={sy(r.y) - 4} textAnchor="end" style={{ fill: r.color, fontWeight: 600 }}>
              {r.label}
            </text>
          </g>
        ))}
        {series.map((s, si) =>
          paths[si].map((pts, i) => <polyline key={`${s.key}-${i}`} points={pts} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} data-testid={`line-${s.key}`} />),
        )}
        {series.map((s) => (
          <g key={`m-${s.key}`} role="group" aria-label={`${s.label} 점`}>
            {s.points.map((p) => {
              if (!isNum(p.x) || !isNum(p.y)) return null;
              const cx = sx(p.x);
              const cy = sy(p.y);
              const sel = selectedKey === p.key;
              const open = (): void => onSelect?.(s.key, p.key);
              const common = { transform: `translate(${cx.toFixed(1)} ${cy.toFixed(1)})`, onMouseEnter: () => setHover({ sk: s.key, pk: p.key }), onMouseLeave: () => setHover(null) };
              return onSelect ? (
                <g
                  key={p.key}
                  className="pt"
                  role="button"
                  tabIndex={0}
                  aria-label={p.label}
                  aria-pressed={sel}
                  data-testid="line-point"
                  data-key={p.key}
                  data-series={s.key}
                  {...common}
                  onFocus={() => setHover({ sk: s.key, pk: p.key })}
                  onBlur={() => setHover(null)}
                  onClick={open}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      open();
                    }
                  }}
                >
                  <circle className="hit" />
                  <circle className="ring" />
                  {sel && <circle r={10} fill="none" stroke={MARK_OUTLINE} strokeWidth={2.5} />}
                  <g fill={s.color} stroke={MARK_OUTLINE} strokeWidth={1.2}>
                    <Mark shape={s.shape} r={5} />
                  </g>
                </g>
              ) : (
                <g key={p.key} role="img" aria-label={p.label} data-testid="line-point" data-key={p.key} data-series={s.key} {...common}>
                  <g fill={s.color} stroke={MARK_OUTLINE} strokeWidth={1.2}>
                    <Mark shape={s.shape} r={4} />
                  </g>
                </g>
              );
            })}
          </g>
        ))}
      </svg>
      {tip && isNum(tip.y) && (
        <div className="chart-tip" role="presentation" style={{ left: Math.min(Math.max(sx(tip.x) + 10, 4), Math.max(4, width - 190)), top: Math.max(0, sy(tip.y) - 8) }}>
          {tip.label}
        </div>
      )}
    </div>
  );
}

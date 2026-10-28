/**
 * SVG scatter chart (no charting library). X is latency in ms (or FPS = 1000 / latency_ms when toggled),
 * Y is fitness (0-1) or AP x 100 (%). Every point is keyboard focusable (tabindex=0, role=button) and
 * opens the candidate modal with Enter / Space / click; an equivalent table is rendered by the caller.
 * Lines are drawn only from the segments the caller computed (baseline / generation / representative);
 * this component never connects points on its own and never calls anything a Pareto frontier.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import type { PlotPoint } from '../lib/cohort';
import { fmtMs, fmtNum, latencyToFps } from '../lib/format';
import type { Segment } from '../lib/lines';

export type XMode = 'ms' | 'fps';

const ARM_STYLE: Record<string, { color: string; shape: 'square' | 'circle' | 'triangle' | 'diamond'; label: string }> = {
  Baseline: { color: 'var(--series-baseline)', shape: 'square', label: 'Baseline (square)' },
  GA: { color: 'var(--series-ga)', shape: 'circle', label: 'GA (circle)' },
  IndependentRandom: { color: 'var(--series-random)', shape: 'triangle', label: 'IndependentRandom (triangle)' },
  Final: { color: 'var(--series-final)', shape: 'diamond', label: 'Final (diamond)' },
};

export function armStyle(arm: string): { color: string; shape: 'square' | 'circle' | 'triangle' | 'diamond'; label: string } {
  return ARM_STYLE[arm] ?? { color: 'var(--series-baseline)', shape: 'circle', label: arm };
}

export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (max <= min) return [min];
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out;
}

export function xValue(p: PlotPoint, mode: XMode): number {
  return mode === 'fps' ? latencyToFps(p.latency_ms) : p.latency_ms;
}

function Marker({ shape, r }: { shape: 'square' | 'circle' | 'triangle' | 'diamond'; r: number }): JSX.Element {
  switch (shape) {
    case 'square':
      return <rect x={-r} y={-r} width={2 * r} height={2 * r} />;
    case 'triangle':
      return <polygon points={`0,${-r - 1} ${r + 1},${r} ${-r - 1},${r}`} />;
    case 'diamond':
      return <polygon points={`0,${-r - 2} ${r + 2},0 0,${r + 2} ${-r - 2},0`} />;
    default:
      return <circle r={r} />;
  }
}

export interface ScatterProps {
  chartId: string;
  title: string;
  yLabel: string;
  yKind: 'fitness' | 'ap';
  xMode: XMode;
  points: readonly PlotPoint[];
  segments: readonly Segment[];
  onOpen: (p: PlotPoint) => void;
}

function useWidth(): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(560);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = (): void => setW(Math.max(280, Math.floor(el.clientWidth || 560)));
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export function pointLabel(p: PlotPoint): string {
  const c = p.c;
  const seed = c.search_seed !== undefined ? ` seed ${c.search_seed}` : '';
  const ap = typeof c.ap === 'number' ? `${(c.ap * 100).toFixed(2)} %` : '—';
  const fit = fmtNum(c.fitness, 3);
  return `${c.arm}${seed} ${c.name}, generation ${c.generation}, rung ${c.rung}: latency ${fmtMs(c.latency_ms)}, fitness ${fit}, AP ${ap}. Press Enter to open details.`;
}

export function Scatter({ chartId, title, yLabel, yKind, xMode, points, segments, onOpen }: ScatterProps): JSX.Element {
  const [boxRef, width] = useWidth();
  const height = 360;
  const m = { l: 56, r: 14, t: 14, b: 52 };
  const iw = width - m.l - m.r;
  const ih = height - m.t - m.b;

  const { xs, ys, xDom, yDom } = useMemo(() => {
    const xs = points.map((p) => xValue(p, xMode));
    const ys = points.map((p) => p.y);
    const xmin = xs.length ? Math.min(...xs) : 0;
    const xmax = xs.length ? Math.max(...xs) : 1;
    const pad = xmax > xmin ? (xmax - xmin) * 0.06 : Math.max(1, Math.abs(xmin) * 0.1);
    const xDom: [number, number] = [Math.max(0, xmin - pad), xmax + pad];
    const ymax = ys.length ? Math.max(...ys) : 1;
    const yDom: [number, number] = yKind === 'fitness' ? [0, 1] : [0, Math.max(5, Math.ceil((ymax * 1.1) / 5) * 5)];
    return { xs, ys, xDom, yDom };
  }, [points, xMode, yKind]);

  const sx = (v: number): number => m.l + ((v - xDom[0]) / (xDom[1] - xDom[0] || 1)) * iw;
  const sy = (v: number): number => m.t + ih - ((v - yDom[0]) / (yDom[1] - yDom[0] || 1)) * ih;
  const xTicks = niceTicks(xDom[0], xDom[1], Math.max(3, Math.floor(iw / 90)));
  const yTicks = niceTicks(yDom[0], yDom[1], 5);
  const xTitle = xMode === 'ms' ? 'Latency [ms]' : 'Throughput [FPS] = 1000 / latency_ms (batch-1 derived)';
  const yTitle = yKind === 'fitness' ? `${yLabel} [0–1]` : `${yLabel} [%]`;
  const polyline = (s: Segment): string => s.points.map((p) => `${sx(xValue(p, xMode)).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ');

  return (
    <div ref={boxRef} className="chart-box" data-testid={`chart-${chartId}`}>
      <svg className="chart-svg" viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="group" aria-labelledby={`${chartId}-t`} aria-describedby={`${chartId}-d`}>
        <title id={`${chartId}-t`}>{title}</title>
        <desc id={`${chartId}-d`}>
          Scatter chart of {points.length} evaluated candidates. X axis {xTitle}. Y axis {yTitle}. Each point is a button; the same data is in the table below the charts.
        </desc>
        {yTicks.map((t) => (
          <g key={`y${t}`}>
            <line className="grid" x1={m.l} x2={m.l + iw} y1={sy(t)} y2={sy(t)} />
            <text x={m.l - 6} y={sy(t) + 4} textAnchor="end">
              {yKind === 'fitness' ? t.toFixed(2) : t}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <g key={`x${t}`}>
            <line className="grid" x1={sx(t)} x2={sx(t)} y1={m.t} y2={m.t + ih} />
            <text x={sx(t)} y={m.t + ih + 16} textAnchor="middle">
              {xMode === 'ms' ? t : Number(t.toPrecision(3))}
            </text>
          </g>
        ))}
        <line className="axis" x1={m.l} x2={m.l + iw} y1={m.t + ih} y2={m.t + ih} />
        <line className="axis" x1={m.l} x2={m.l} y1={m.t} y2={m.t + ih} />
        <text className="axis-title" x={m.l + iw / 2} y={height - 8} textAnchor="middle">
          {xTitle}
        </text>
        <text className="axis-title" transform={`translate(14 ${m.t + ih / 2}) rotate(-90)`} textAnchor="middle">
          {yTitle}
        </text>

        {segments.map((s) => {
          const arm = s.points[0]?.c.arm ?? 'Baseline';
          const color = armStyle(arm).color;
          const props =
            s.kind === 'generation'
              ? { strokeDasharray: '2 5', strokeWidth: 2, strokeLinecap: 'round' as const }
              : s.kind === 'representative'
                ? { strokeWidth: 3 }
                : { strokeWidth: 1.5, opacity: 0.75 };
          return <polyline key={s.key} data-line={s.kind} data-testid={`line-${s.kind}`} points={polyline(s)} fill="none" stroke={color} {...props} />;
        })}

        <g role="group" aria-label={`${title} points`}>
          {points.map((p, i) => {
            const st = armStyle(p.c.arm);
            const cx = sx(xs[i]);
            const cy = sy(ys[i]);
            const open = (): void => onOpen(p);
            return (
              <g
                key={p.id}
                className="pt"
                role="button"
                tabIndex={0}
                aria-label={pointLabel(p)}
                data-testid="chart-point"
                data-eval-id={p.id}
                transform={`translate(${cx.toFixed(1)} ${cy.toFixed(1)})`}
                onClick={open}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    open();
                  }
                }}
              >
                <title>{`${p.c.name} · ${fmtMs(p.latency_ms)} · ${yKind === 'fitness' ? fmtNum(p.y, 3) : `${p.y.toFixed(2)} %`}`}</title>
                <circle className="hit" />
                <circle className="ring" />
                <g fill={st.color} stroke="#ffffff" strokeWidth={1.5}>
                  <Marker shape={st.shape} r={6} />
                </g>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

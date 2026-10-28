/**
 * Lines drawn over the scatter charts and the generation bookkeeping behind them.
 *  - baseline line: Baseline points in latency order (one polyline);
 *  - dotted generation guide: points of ONE complete generation (planning closed, pending 0, counts invariant) of one
 *    cohort, latency order; refused for open generations, generations spanning cohorts and generations with < 2 points;
 *  - solid representative line: representatives of CONSECUTIVE complete generations of one arm/seed/rung, broken by any
 *    gap, open generation or undrawn representative. Lines are never called a Pareto frontier.
 *
 * 사용처: src/screens/Monitor.tsx, src/components/Scatter.tsx, src/state/reducer.ts (generationKey).
 */
import type { Generation } from '../api/types';
import type { PlotPoint } from './cohort';

export interface Segment {
  kind: 'baseline' | 'generation' | 'representative';
  key: string;
  label: string;
  points: PlotPoint[];
}

export type SkipReason = 'OPEN_GENERATION' | 'MIXED_COHORT' | 'TOO_FEW_POINTS';

export interface SkippedGeneration {
  key: string;
  reason: SkipReason;
  /** cohorts the skipped generation touches (empty/absent: unknown, shown for every cohort) */
  cohorts?: string[];
}

/** Identity of a generation of one arm/seed/rung; works for Generation records and for candidate rows. */
export function generationKey(x: { arm?: string; search_seed?: number | null; rung: string; generation: number }): string {
  return `${x.arm ?? ''}|${x.search_seed ?? ''}|${x.rung}|${x.generation}`;
}

const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v);

/** evaluated + failed + not_admitted + pending == planned, all five counts present. */
export function countsInvariant(g: Generation): boolean {
  const { planned_count: p, evaluated_count: e, failed_count: f, not_admitted_count: n, pending_count: q } = g;
  return isInt(p) && isInt(e) && isInt(f) && isInt(n) && isInt(q) && e + f + n + q === p;
}

/** complete && planning_closed && pending == 0 && counts invariant (the only state in which lines may be drawn). */
export function isGenerationComplete(g: Generation): boolean {
  return g.complete === true && g.planning_closed === true && g.pending_count === 0 && countsInvariant(g);
}

const byLatency = (a: PlotPoint, b: PlotPoint): number => a.latency_ms - b.latency_ms || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/** Baseline points of the cohort joined in latency order (needs >= 2 points). */
export function baselineLines(points: readonly PlotPoint[]): Segment[] {
  const pts = points.filter((p) => p.c.arm === 'Baseline').sort(byLatency);
  return pts.length >= 2 ? [{ kind: 'baseline', key: 'baseline', label: 'Baseline family (latency order)', points: pts }] : [];
}

const genLabel = (g: Generation): string => `${g.arm} gen ${g.generation} (${g.rung}${g.search_seed !== undefined ? `, seed ${g.search_seed}` : ''})`;

/**
 * Dotted guides for the selected cohort. `all` are ALL drawable points (every cohort) so that a generation spanning
 * several cohorts is detected; `skipped` also lists generations of other cohorts that have too few points.
 */
export function generationGuides(inCohort: readonly PlotPoint[], gens: readonly Generation[], all: readonly PlotPoint[]): { segments: Segment[]; skipped: SkippedGeneration[] } {
  const segments: Segment[] = [];
  const skipped: SkippedGeneration[] = [];
  const selected = inCohort[0]?.cohort ?? null;
  for (const g of gens) {
    const key = generationKey(g);
    const pts = all.filter((p) => p.c.arm === g.arm && generationKey(p.c) === key);
    const cohorts = [...new Set(pts.map((p) => p.cohort))];
    if (!isGenerationComplete(g)) {
      skipped.push({ key, reason: 'OPEN_GENERATION', cohorts });
    } else if (cohorts.length > 1) {
      skipped.push({ key, reason: 'MIXED_COHORT', cohorts });
    } else if (pts.length < 2) {
      skipped.push({ key, reason: 'TOO_FEW_POINTS', cohorts });
    } else if (cohorts[0] === selected) {
      segments.push({ kind: 'generation', key: `gen|${key}`, label: genLabel(g), points: [...pts].sort(byLatency) });
    }
  }
  return { segments, skipped };
}

/** The skipped generations that concern the cohort currently drawn. */
export function skippedForCohort(skipped: readonly SkippedGeneration[], inCohort: readonly PlotPoint[]): SkippedGeneration[] {
  const cohort = inCohort[0]?.cohort;
  if (cohort === undefined) return [];
  return skipped.filter((s) => !s.cohorts || s.cohorts.length === 0 || s.cohorts.includes(cohort));
}

/** One-sentence explanation of why some generations have no dotted line ('' when nothing was skipped). */
export function skippedHelpText(skipped: readonly { key?: string; reason: SkipReason }[]): string {
  if (skipped.length === 0) return '';
  const n = (r: SkipReason): number => skipped.filter((s) => s.reason === r).length;
  const parts: string[] = [];
  if (n('OPEN_GENERATION')) parts.push(`${n('OPEN_GENERATION')} open (not yet complete)`);
  if (n('MIXED_COHORT')) parts.push(`${n('MIXED_COHORT')} spanning several cohorts`);
  if (n('TOO_FEW_POINTS')) parts.push(`${n('TOO_FEW_POINTS')} too few points (needs 2)`);
  return `${skipped.length} generation(s) without a dotted line: ${parts.join(', ')}.`;
}

/** Representative of a set of points: highest fitness, then lower latency, then the smaller candidate id. */
export function selectRepresentative(points: readonly PlotPoint[]): PlotPoint | null {
  let best: PlotPoint | null = null;
  for (const p of points) {
    if (best === null) {
      best = p;
      continue;
    }
    const pf = p.c.fitness ?? Number.NEGATIVE_INFINITY;
    const bf = best.c.fitness ?? Number.NEGATIVE_INFINITY;
    if (pf !== bf ? pf > bf : p.latency_ms !== best.latency_ms ? p.latency_ms < best.latency_ms : p.c.candidate_id < best.c.candidate_id) best = p;
  }
  return best;
}

/** Solid lines through the server's representatives of consecutive complete generations (series = arm + seed + rung). */
export function representativeLines(inCohort: readonly PlotPoint[], gens: readonly Generation[]): Segment[] {
  const series = new Map<string, Map<number, PlotPoint>>();
  const names = new Map<string, string>();
  for (const g of gens) {
    if (!isGenerationComplete(g)) continue;
    const evalId = g.representative_evaluation_id ?? null;
    const candId = g.representative_candidate_id ?? null;
    if (!evalId && !candId) continue;
    const hit = inCohort.find((p) => p.c.arm === g.arm && (evalId ? p.c.id === evalId : p.c.candidate_id === candId && p.c.rung === g.rung));
    if (!hit) continue;
    const sk = `${g.arm}|${g.search_seed ?? ''}|${g.rung}`;
    if (!series.has(sk)) {
      series.set(sk, new Map());
      names.set(sk, `${g.arm} representatives (${g.rung}${g.search_seed !== undefined ? `, seed ${g.search_seed}` : ''})`);
    }
    series.get(sk)?.set(g.generation, hit);
  }
  const out: Segment[] = [];
  for (const [sk, byGen] of series) {
    const order = [...byGen.keys()].sort((a, b) => a - b);
    let run: number[] = [];
    const flush = (): void => {
      if (run.length >= 2) out.push({ kind: 'representative', key: `rep|${sk}|${run[0]}-${run[run.length - 1]}`, label: `${names.get(sk)} gen ${run[0]}–${run[run.length - 1]}`, points: run.map((g) => byGen.get(g) as PlotPoint) });
      run = [];
    };
    for (const g of order) {
      if (run.length > 0 && g !== run[run.length - 1] + 1) flush();
      run.push(g);
    }
    flush();
  }
  return out;
}

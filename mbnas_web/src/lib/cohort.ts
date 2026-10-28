/**
 * Plot points and the cohort guard.
 *  - Only EVALUATED rows with finite, valid metrics become points; failed, provisional, superseded and unmeasured rows
 *    are counted under `excluded` and never drawn.
 *  - A cohort is the set of rows that may share one chart: same rung, device, precision, batch, resolved input shape,
 *    latency scope/source, cumulative epochs, comparison group, evaluation/objective/split/protocol hash and
 *    post-processing runtime. The seed and the arm are series dimensions, NOT cohort dimensions.
 *  - EVALUATED rows missing a mandatory field are UNRESOLVED_COHORT and are not drawn (never guessed).
 *  - Final retraining rows (arm Final / rung FINAL / comparison group final) form their own cohort.
 *
 * 재구현(2026-10-15), 원본 누락(리포 루트 .gitignore `lib/` 규칙으로 커밋되지 않음).
 * 근거 테스트: tests/cohort_lines.test.ts, tests/final_retrain.test.tsx ('final cohort'), tests/s0910_fix1.test.tsx
 * (S8-F5 postprocess_runtime, S9-F2 cohort labels), tests/components.test.tsx, e2e/console.spec.ts (cohort guard);
 * 사용처 src/screens/Monitor.tsx, src/components/Scatter.tsx. 어떤 필드가 '필수'인지는 테스트가 열거한 것
 * (batch, resolved_input_shape, protocol_hash, search_seed, evaluation_hash)에 해시·그룹 필드를 더해 정했다.
 */
import type { Candidate } from '../api/types';

export const UNRESOLVED_COHORT = 'UNRESOLVED_COHORT';

export type PlotMetric = 'fitness' | 'ap';
export type ExcludedReason = 'NOT_EVALUATED' | 'INVALID_METRICS' | 'SUPERSEDED' | 'UNRESOLVED_COHORT';

export interface PlotPoint {
  /** evaluation id */
  id: string;
  c: Candidate;
  cohort: string;
  latency_ms: number;
  /** fitness (0-1) or AP x 100 (%) */
  y: number;
}

export interface CohortGroup {
  key: string;
  label: string;
  count: number;
}

export interface CohortSelection {
  groups: CohortGroup[];
  selected: string | null;
  inCohort: PlotPoint[];
  hiddenOtherCohort: number;
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const nonEmpty = (v: unknown): v is string => typeof v === 'string' && v.trim() !== '';

/** Final retraining row: arm Final, rung FINAL or comparison group final (own cohort, no search seed needed). */
export function isFinalRow(c: Pick<Candidate, 'arm' | 'rung' | 'comparison_group'>): boolean {
  return c.arm === 'Final' || c.rung === 'FINAL' || c.comparison_group === 'final';
}

function resolved(c: Candidate): boolean {
  if (!isNum(c.batch)) return false;
  if (!Array.isArray(c.resolved_input_shape) || c.resolved_input_shape.length === 0) return false;
  if (![c.protocol_hash, c.evaluation_hash, c.objective_hash, c.split_hash, c.comparison_group].every(nonEmpty)) return false;
  if (c.arm !== 'Baseline' && !isFinalRow(c) && !isNum(c.search_seed)) return false;
  return true;
}

/** Cohort identity of one row; UNRESOLVED_COHORT when a mandatory field is missing. */
export function cohortKey(c: Candidate): string {
  if (!resolved(c)) return UNRESOLVED_COHORT;
  return [
    c.rung,
    c.device,
    c.precision,
    c.batch,
    (c.resolved_input_shape ?? []).join('x'),
    c.latency_scope,
    c.latency_source,
    c.epoch_target_total ?? '',
    c.comparison_group,
    c.evaluation_hash,
    c.objective_hash,
    c.split_hash,
    c.protocol_hash,
    c.postprocess_runtime ?? '',
  ].join('|');
}

/** First 8 characters of a hash without its algorithm prefix. */
function short(h: string | undefined | null): string {
  if (!h) return '—';
  return h.replace(/^sha256:/, '').slice(0, 8);
}

/** Readable cohort name; differs whenever the key differs (hashes are shown shortened). */
export function cohortLabel(c: Candidate): string {
  const key = cohortKey(c);
  if (key === UNRESOLVED_COHORT) return UNRESOLVED_COHORT;
  const parts = [
    isFinalRow(c) ? 'FINAL retraining' : null,
    `rung ${c.rung}`,
    c.epoch_target_total !== undefined ? `${c.epoch_target_total} ep` : null,
    `${c.device} ${c.precision}`,
    `batch ${c.batch}`,
    `${(c.resolved_input_shape ?? []).join('x')} px`,
    `${c.latency_scope}`,
    `${c.latency_source}`,
    c.postprocess_runtime ? `post ${c.postprocess_runtime}` : null,
    `eval ${short(c.evaluation_hash)}`,
    `group ${short(c.comparison_group)}`,
    `split ${short(c.split_hash)}`,
    `protocol ${short(c.protocol_hash)}`,
    `objective ${short(c.objective_hash)}`,
  ];
  return parts.filter((p): p is string => p !== null).join(' · ');
}

function metricsValid(c: Candidate, metric: PlotMetric): boolean {
  if (!isNum(c.ap) || c.ap < 0 || c.ap > 1) return false;
  if (!isNum(c.latency_ms) || c.latency_ms <= 0) return false;
  if (!isNum(c.size_mb) || c.size_mb <= 0) return false;
  if (metric === 'fitness' && (!isNum(c.fitness) || c.fitness < 0 || c.fitness > 1)) return false;
  return true;
}

/** Rows -> drawable points plus a count of every excluded row by reason. */
export function buildPlotPoints(rows: readonly Candidate[], metric: PlotMetric): { points: PlotPoint[]; excluded: Record<ExcludedReason, number> } {
  const superseded = new Set<string>();
  for (const r of rows) if (r.supersedes_evaluation_id) superseded.add(r.supersedes_evaluation_id);
  const excluded: Record<ExcludedReason, number> = { NOT_EVALUATED: 0, INVALID_METRICS: 0, SUPERSEDED: 0, UNRESOLVED_COHORT: 0 };
  const points: PlotPoint[] = [];
  for (const c of rows) {
    if (c.status !== 'EVALUATED') {
      excluded.NOT_EVALUATED += 1;
      continue;
    }
    if (superseded.has(c.id)) {
      excluded.SUPERSEDED += 1;
      continue;
    }
    if (!metricsValid(c, metric)) {
      excluded.INVALID_METRICS += 1;
      continue;
    }
    const cohort = cohortKey(c);
    if (cohort === UNRESOLVED_COHORT) {
      excluded.UNRESOLVED_COHORT += 1;
      continue;
    }
    points.push({ id: c.id, c, cohort, latency_ms: c.latency_ms as number, y: metric === 'fitness' ? (c.fitness as number) : (c.ap as number) * 100 });
  }
  return { points, excluded };
}

/** One cohort is drawn at a time: the wanted one when present, else the largest. Other cohorts are only counted. */
export function selectCohort(points: readonly PlotPoint[], wanted: string | null | undefined): CohortSelection {
  const byKey = new Map<string, CohortGroup>();
  for (const p of points) {
    const g = byKey.get(p.cohort);
    if (g) g.count += 1;
    else byKey.set(p.cohort, { key: p.cohort, label: cohortLabel(p.c), count: 1 });
  }
  const groups = [...byKey.values()].sort((a, b) => b.count - a.count); // stable: ties keep first-seen order
  const selected = wanted && byKey.has(wanted) ? wanted : (groups[0]?.key ?? null);
  const inCohort = selected === null ? [] : points.filter((p) => p.cohort === selected);
  return { groups, selected, inCohort, hiddenOtherCohort: points.length - inCohort.length };
}

// ---- facets ---------------------------------------------------------------------------------------------

export type FacetName = 'arm' | 'seed' | 'generation' | 'rung' | 'family' | 'postprocess';
export type FacetFilters = Partial<Record<FacetName, string>>;

export const FACETS: readonly { name: FacetName; label: string }[] = [
  { name: 'arm', label: 'Arm' },
  { name: 'seed', label: 'Search seed' },
  { name: 'generation', label: 'Generation' },
  { name: 'rung', label: 'Rung' },
  { name: 'family', label: 'Family' },
  { name: 'postprocess', label: 'Postprocess runtime' },
];

function facetValue(c: Candidate, f: FacetName): string | null {
  switch (f) {
    case 'arm':
      return c.arm;
    case 'seed':
      return c.search_seed === undefined ? null : String(c.search_seed);
    case 'generation':
      return String(c.generation);
    case 'rung':
      return c.rung;
    case 'family':
      return c.family;
    case 'postprocess':
      return c.postprocess_runtime ?? null;
  }
}

/** Distinct values of a facet among the rows (numeric facets sorted numerically). */
export function facetOptions(rows: readonly Candidate[], f: FacetName): string[] {
  const vals = [...new Set(rows.map((c) => facetValue(c, f)).filter((v): v is string => v !== null))];
  return f === 'seed' || f === 'generation' ? vals.sort((a, b) => Number(a) - Number(b)) : vals.sort();
}

/** Keep the points that match every non-empty facet filter. */
export function applyFacetFilters(points: readonly PlotPoint[], filters: FacetFilters): PlotPoint[] {
  const active = (Object.entries(filters) as [FacetName, string | undefined][]).filter(([, v]) => v !== undefined && v !== '');
  if (active.length === 0) return [...points];
  return points.filter((p) => active.every(([f, v]) => facetValue(p.c, f) === v));
}

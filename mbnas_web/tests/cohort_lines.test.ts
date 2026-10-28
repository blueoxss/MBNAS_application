import { beforeEach, describe, expect, it } from 'vitest';
import { applyFacetFilters, buildPlotPoints, cohortKey, selectCohort, UNRESOLVED_COHORT } from '../src/lib/cohort';
import { baselineLines, countsInvariant, generationGuides, isGenerationComplete, representativeLines, selectRepresentative } from '../src/lib/lines';
import { baselineRow, cand, fixtureCandidates, fixtureGenerations, gen, resetCounter } from './fixtures';

beforeEach(resetCounter);

describe('plot points', () => {
  it('only EVALUATED rows with finite valid metrics become points; failed, provisional and null are not points', () => {
    const { points, excluded } = buildPlotPoints(fixtureCandidates(), 'fitness');
    expect(points.every((p) => p.c.status === 'EVALUATED')).toBe(true);
    expect(points.some((p) => p.c.name === 'ga-g1-failed' || p.c.name === 'ga-g2-training')).toBe(false);
    expect(excluded.NOT_EVALUATED).toBe(2);
    expect(excluded.UNRESOLVED_COHORT).toBe(1); // ga-missing-batch
  });
  it('rejects NaN, infinite, out-of-range and non-positive metrics', () => {
    const rows = [cand({ ap: Number.NaN }), cand({ ap: 1.5 }), cand({ latency_ms: 0 }), cand({ latency_ms: Number.POSITIVE_INFINITY }), cand({ size_mb: -1 }), cand({ fitness: 1.5 }), cand()];
    expect(buildPlotPoints(rows, 'fitness').points).toHaveLength(1);
    expect(buildPlotPoints(rows, 'ap').points).toHaveLength(2); // fitness out of range does not matter for the AP chart
  });
  it('hides superseded evaluation revisions', () => {
    const a = cand({ id: 'e1' });
    const b = cand({ id: 'e2', supersedes_evaluation_id: 'e1', candidate_id: a.candidate_id });
    const { points, excluded } = buildPlotPoints([a, b], 'ap');
    expect(points.map((p) => p.id)).toEqual(['e2']);
    expect(excluded.SUPERSEDED).toBe(1);
  });
});

describe('cohort guard', () => {
  it('different rung, device, precision, batch, shape, scope or latency source are different cohorts', () => {
    const base = cand();
    const variants = [{ rung: 'L1' }, { device: 'other' }, { precision: 'FP32' as const }, { batch: 8 }, { resolved_input_shape: [320, 320] }, { latency_scope: 'end_to_end' }, { latency_source: 'calibrated_proxy' as const }, { epoch_target_total: 30 }, { comparison_group: 'other' }, { evaluation_hash: 'x' }, { objective_hash: 'x' }, { split_hash: 'x' }, { protocol_hash: 'x' }];
    for (const v of variants) expect(cohortKey(cand(v))).not.toBe(cohortKey(base));
  });
  it('the seed is a series dimension, not a cohort dimension', () => {
    expect(cohortKey(cand({ search_seed: 101 }))).toBe(cohortKey(cand({ search_seed: 202 })));
  });
  it('rows of other cohorts are not drawn with the selected cohort and never join its lines', () => {
    const rows = [
      baselineRow('a', 20, 0.1, 10),
      baselineRow('b', 40, 0.2, 20),
      baselineRow('a2', 50, 0.25, 25),
      baselineRow('c', 60, 0.3, 30, { device: 'other-device' }),
      baselineRow('d', 80, 0.4, 40, { device: 'other-device' }),
    ];
    const { points } = buildPlotPoints(rows, 'ap');
    const sel = selectCohort(points, null);
    expect(sel.groups).toHaveLength(2);
    expect(sel.inCohort).toHaveLength(3);
    expect(sel.hiddenOtherCohort).toBe(2);
    const lines = baselineLines(sel.inCohort);
    expect(lines).toHaveLength(1);
    expect(lines[0].points.map((p) => p.c.name)).toEqual(['a', 'b', 'a2']);
    const other = selectCohort(points, sel.groups[1].key);
    expect(other.inCohort.map((p) => p.c.name).sort()).toEqual(['c', 'd']);
  });
  it('EVALUATED rows missing mandatory fields fall into UNRESOLVED_COHORT and are not drawn', () => {
    for (const missing of [{ batch: undefined }, { resolved_input_shape: undefined }, { protocol_hash: undefined }, { search_seed: undefined }, { evaluation_hash: '' }]) {
      const c = cand(missing);
      expect(cohortKey(c)).toBe(UNRESOLVED_COHORT);
      expect(buildPlotPoints([c], 'ap').points).toHaveLength(0);
    }
    // baselines do not need a search seed
    expect(cohortKey(baselineRow('x', 10, 0.1, 5))).not.toBe(UNRESOLVED_COHORT);
  });
  it('facet filters narrow rows', () => {
    const { points } = buildPlotPoints(fixtureCandidates(), 'ap');
    expect(applyFacetFilters(points, { arm: 'Baseline' }).every((p) => p.c.arm === 'Baseline')).toBe(true);
    expect(applyFacetFilters(points, { arm: 'GA', generation: '1' }).every((p) => p.c.generation === 1)).toBe(true);
  });
});

describe('generation completeness', () => {
  it('requires complete && planning_closed && pending==0 && counts invariant', () => {
    expect(isGenerationComplete(gen())).toBe(true);
    expect(isGenerationComplete(gen({ planning_closed: false }))).toBe(false);
    expect(isGenerationComplete(gen({ pending_count: 1, evaluated_count: 3 }))).toBe(false);
    expect(isGenerationComplete(gen({ complete: false }))).toBe(false);
    expect(isGenerationComplete(gen({ evaluated_count: 3 }))).toBe(false); // 3+0+0+0 != 4
    expect(countsInvariant(gen({ evaluated_count: 2, failed_count: 1, not_admitted_count: 1 }))).toBe(true);
    expect(countsInvariant(gen({ failed_count: undefined }))).toBe(false);
  });
});

describe('lines', () => {
  it('connects baseline family points in latency order only', () => {
    const rows = [baselineRow('l', 70, 0.3, 50), baselineRow('s', 20, 0.1, 10), baselineRow('m', 40, 0.2, 30)];
    const pts = buildPlotPoints(rows, 'ap').points;
    const [seg] = baselineLines(pts);
    expect(seg.points.map((p) => p.c.name)).toEqual(['s', 'm', 'l']);
  });

  it('draws the dotted guide only for closed generations with pending 0', () => {
    const { points } = buildPlotPoints(fixtureCandidates(), 'fitness');
    const sel = selectCohort(points, null);
    const g = generationGuides(sel.inCohort, fixtureGenerations(), points);
    const keys = g.segments.map((s) => s.label);
    expect(keys.some((k) => k.includes('GA gen 0'))).toBe(true);
    expect(keys.some((k) => k.includes('GA gen 1'))).toBe(true);
    expect(keys.some((k) => k.includes('gen 2'))).toBe(false); // open generation
    expect(g.skipped.some((s) => s.reason === 'OPEN_GENERATION')).toBe(true);
    const gen0 = g.segments.find((s) => s.label.includes('GA gen 0'));
    const lat = gen0?.points.map((p) => p.latency_ms) ?? [];
    expect(lat).toEqual([...lat].sort((a, b) => a - b));
  });

  it('refuses a dotted guide when one generation spans cohorts', () => {
    const rows = [cand({ id: 'a', generation: 0, latency_ms: 30 }), cand({ id: 'b', generation: 0, latency_ms: 40 }), cand({ id: 'c', generation: 0, latency_ms: 50, device: 'other' })];
    const all = buildPlotPoints(rows, 'ap').points;
    const sel = selectCohort(all, null);
    const g = generationGuides(sel.inCohort, [gen({ planned_count: 3, evaluated_count: 3 })], all);
    expect(g.segments).toHaveLength(0);
    expect(g.skipped[0].reason).toBe('MIXED_COHORT');
  });

  it('representative rule: highest fitness, then lower latency, then stable candidate id', () => {
    const mk = (id: string, fitness: number, lat: number) => cand({ id: `e-${id}`, candidate_id: `c-${id}`, fitness, latency_ms: lat });
    const pts = (rows: ReturnType<typeof cand>[]) => buildPlotPoints(rows, 'fitness').points;
    expect(selectRepresentative(pts([mk('a', 0.5, 50), mk('b', 0.6, 90), mk('c', 0.4, 10)]))?.c.candidate_id).toBe('c-b');
    expect(selectRepresentative(pts([mk('a', 0.6, 70), mk('b', 0.6, 40), mk('c', 0.6, 90)]))?.c.candidate_id).toBe('c-b');
    expect(selectRepresentative(pts([mk('z', 0.6, 40), mk('m', 0.6, 40), mk('q', 0.6, 40)]))?.c.candidate_id).toBe('c-m');
    expect(selectRepresentative([])).toBeNull();
  });

  const repRows = () => [0, 1, 2, 3].map((g) => cand({ id: `e${g}`, candidate_id: `c${g}`, generation: g, latency_ms: 30 + g * 10, ap: 0.1 + g * 0.05 }));
  const repGens = (skip: number[] = [], open: number[] = []) =>
    [0, 1, 2, 3]
      .filter((g) => !skip.includes(g))
      .map((g) => gen({ generation: g, planned_count: 1, evaluated_count: open.includes(g) ? 0 : 1, pending_count: open.includes(g) ? 1 : 0, complete: !open.includes(g), planning_closed: !open.includes(g), representative_candidate_id: `c${g}`, representative_evaluation_id: `e${g}` }));

  it('joins representatives across consecutive complete generations with a solid line', () => {
    const pts = buildPlotPoints(repRows(), 'fitness').points;
    const segs = representativeLines(pts, repGens());
    expect(segs).toHaveLength(1);
    expect(segs[0].points.map((p) => p.c.generation)).toEqual([0, 1, 2, 3]);
  });
  it('breaks the line at a missing generation (G2 absent: G1 and G3 are not joined)', () => {
    const pts = buildPlotPoints(repRows(), 'fitness').points;
    expect(representativeLines(pts, repGens([2]))).toHaveLength(1); // only G0-G1; G3 alone has no line
    expect(representativeLines(pts, repGens([2]))[0].points.map((p) => p.c.generation)).toEqual([0, 1]);
    expect(representativeLines(pts, repGens([1, 2]))).toHaveLength(0);
  });
  it('breaks the line at an open generation and when the representative row is not drawn', () => {
    const pts = buildPlotPoints(repRows(), 'fitness').points;
    const segs = representativeLines(pts, repGens([], [2]));
    expect(segs).toHaveLength(1);
    expect(segs[0].points.map((p) => p.c.generation)).toEqual([0, 1]);
    const without = pts.filter((p) => p.c.generation !== 1);
    const segs2 = representativeLines(without, repGens());
    expect(segs2).toHaveLength(1);
    expect(segs2[0].points.map((p) => p.c.generation)).toEqual([2, 3]); // G0 alone, then G2-G3; never G0 joined to G2
  });
  it('never joins different seeds or rungs', () => {
    const rows = [
      cand({ id: 'a0', candidate_id: 'a0', generation: 0, search_seed: 101 }),
      cand({ id: 'b1', candidate_id: 'b1', generation: 1, search_seed: 202 }),
    ];
    const pts = buildPlotPoints(rows, 'fitness').points;
    const gens = [
      gen({ generation: 0, search_seed: 101, planned_count: 1, evaluated_count: 1, representative_evaluation_id: 'a0' }),
      gen({ generation: 1, search_seed: 202, planned_count: 1, evaluated_count: 1, representative_evaluation_id: 'b1' }),
    ];
    expect(representativeLines(pts, gens)).toHaveLength(0);
  });
});

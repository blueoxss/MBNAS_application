import { describe, expect, it } from 'vitest';
import { dominates2d, dominates4d, front2d, front4d, frontChange, orderFront2d, upToGeneration, type ObjPoint } from '../src/archive/pareto';
import { genColor, rampColor, runColor, RUN_COLORS } from '../src/archive/palette';

const P = (id: string, map: number | null, lat: number | null, recall: number | null = 0.5, params: number | null = 1000): ObjPoint => ({ id, map, lat, recall, params });

/** independent brute-force definition (written separately from the module under test) */
function brute2d(pts: ObjPoint[]): string[] {
  const v = pts.filter((p) => typeof p.map === 'number' && typeof p.lat === 'number');
  const out: string[] = [];
  for (const a of v) {
    let dominated = false;
    for (const b of v) {
      if (a === b) continue;
      const geMap = (b.map as number) >= (a.map as number);
      const leLat = (b.lat as number) <= (a.lat as number);
      const strict = (b.map as number) > (a.map as number) || (b.lat as number) < (a.lat as number);
      if (geMap && leLat && strict) dominated = true;
    }
    if (!dominated) out.push(a.id);
  }
  return out;
}

describe('2D non-dominated front (mAP up, latency down)', () => {
  it('matches a hand-computed example', () => {
    // a: best mAP but slow; b: fast and decent; c: dominated by b (lower mAP, slower); d: dominated by a; e: fastest, low mAP
    const pts = [P('a', 0.5, 10), P('b', 0.45, 5), P('c', 0.4, 6), P('d', 0.49, 11), P('e', 0.3, 2)];
    expect(front2d(pts).sort()).toEqual(['a', 'b', 'e']);
  });
  it('dominance is strict in at least one objective; equal points both stay', () => {
    expect(dominates2d({ map: 0.5, lat: 5 }, { map: 0.5, lat: 5 })).toBe(false);
    expect(dominates2d({ map: 0.5, lat: 5 }, { map: 0.5, lat: 6 })).toBe(true);
    expect(dominates2d({ map: 0.5, lat: 5 }, { map: 0.4, lat: 5 })).toBe(true);
    expect(dominates2d({ map: 0.4, lat: 4 }, { map: 0.5, lat: 5 })).toBe(false);
    expect(front2d([P('x', 0.5, 5), P('y', 0.5, 5)]).sort()).toEqual(['x', 'y']);
  });
  it('a point with a missing objective is neither on the front nor a dominator', () => {
    const pts = [P('a', 0.5, 10), P('nolat', 0.9, null), P('nomap', null, 1)];
    expect(front2d(pts)).toEqual(['a']);
  });
  it('agrees with the brute force definition on 300 pseudo random sets', () => {
    let s = 12345;
    const rnd = (): number => {
      s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
      return s / 4294967296;
    };
    for (let t = 0; t < 300; t++) {
      const n = 1 + Math.floor(rnd() * 40);
      // coarse grid so that ties occur
      const pts = Array.from({ length: n }, (_, i) => P(`p${i}`, Math.round(rnd() * 10) / 10, Math.round(rnd() * 10)));
      expect(front2d(pts).sort()).toEqual(brute2d(pts).sort());
    }
  });
  it('empty input gives an empty front', () => {
    expect(front2d([])).toEqual([]);
    expect(front4d([])).toEqual([]);
  });
});

describe('4D front (mAP up, small recall up, latency down, params down)', () => {
  it('worse in one objective but better in another keeps both; strictly worse everywhere is dropped', () => {
    const a = P('a', 0.5, 5, 0.9, 1000);
    const b = P('b', 0.4, 6, 0.8, 1200); // dominated by a
    const c = P('c', 0.4, 6, 0.95, 1200); // better recall than a: stays
    expect(front4d([a, b, c]).sort()).toEqual(['a', 'c']);
    expect(dominates4d({ map: 1, recall: 1, lat: 1, params: 1 }, { map: 1, recall: 1, lat: 1, params: 1 })).toBe(false);
  });
  it('rows missing any of the four objectives are excluded', () => {
    expect(front4d([P('a', 0.5, 5, null, 1000), P('b', 0.4, 6, 0.8, 1200)])).toEqual(['b']);
  });
  it('the 2D front is a subset of the points that are not 4D-dominated by something with equal 2D coordinates', () => {
    const pts = [P('a', 0.5, 5, 0.9, 1000), P('b', 0.45, 4, 0.7, 900), P('c', 0.3, 8, 0.5, 5000)];
    // every 4D-front point must be undominated in 4D; 2D front points here are a and b
    expect(front2d(pts).sort()).toEqual(['a', 'b']);
    expect(front4d(pts).sort()).toEqual(['a', 'b']);
  });
});

describe('front helpers', () => {
  it('orderFront2d sorts by latency ascending', () => {
    const pts = [P('a', 0.5, 10), P('b', 0.45, 5), P('e', 0.3, 2)];
    expect(orderFront2d(pts, ['a', 'b', 'e']).map((p) => p.id)).toEqual(['e', 'b', 'a']);
  });
  it('upToGeneration is cumulative and inclusive', () => {
    const rows = [{ generation: 0 }, { generation: 1 }, { generation: 2 }];
    expect(upToGeneration(rows, 1)).toHaveLength(2);
    expect(upToGeneration(rows, -1)).toHaveLength(0);
  });
  it('frontChange reports added and removed ids', () => {
    expect(frontChange(['a', 'b'], ['b', 'c'])).toEqual({ added: ['c'], removed: ['a'] });
    expect(frontChange([], ['a'])).toEqual({ added: ['a'], removed: [] });
  });
});

describe('palette', () => {
  it('is deterministic, in #rrggbb form, starts yellow and ends dark purple', () => {
    for (const t of [0, 0.1, 0.5, 0.99, 1]) expect(rampColor(t)).toMatch(/^#[0-9a-f]{6}$/);
    expect(rampColor(0)).toBe('#fde725');
    expect(rampColor(1)).toBe('#440154');
    expect(rampColor(0.5)).toBe(rampColor(0.5));
  });
  it('clamps out-of-range and non-finite input', () => {
    expect(rampColor(-3)).toBe(rampColor(0));
    expect(rampColor(7)).toBe(rampColor(1));
    expect(rampColor(Number.NaN)).toBe(rampColor(0.5));
  });
  it('genColor spreads generations over the ramp and handles a single generation', () => {
    expect(genColor(0, 4)).toBe(rampColor(0));
    expect(genColor(4, 4)).toBe(rampColor(1));
    expect(genColor(2, 4)).toBe(rampColor(0.5));
    expect(genColor(0, 0)).toBe(rampColor(0.5));
    const cols = new Set([0, 1, 2, 3, 4].map((g) => genColor(g, 4)));
    expect(cols.size).toBe(5);
  });
  it('run colours cycle and are distinct for up to six runs', () => {
    expect(new Set(RUN_COLORS).size).toBe(RUN_COLORS.length);
    expect(runColor(0)).toBe(runColor(RUN_COLORS.length));
    expect(runColor(-1)).toBe(RUN_COLORS[RUN_COLORS.length - 1]);
  });
});

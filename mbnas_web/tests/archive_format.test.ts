import { describe, expect, it } from 'vitest';
import { buildArchiveHash, buildCompareHash, parseHashString } from '../src/archive/hash';
import { DASH, epochsLabel, fmtBool, fmtDelta, fmtDeltaInt, fmtDur, fmtFps, fmtInt, fmtLat, fmtMap, fmtStamp, fmtVal, histogramRows, numOrNull, shortHash, tierInfo } from '../src/archive/format';
import { flatten, gateCounts, sortGates } from '../src/components/archive/OverviewCard';
import { buildBaselineRows } from '../src/components/archive/BaselineTable';
import { buildPoints } from '../src/components/archive/ArchiveScatter';
import { fixtureBaselines, RUN_A, SYNTH_A } from './archive_fixtures';

describe('number formatting: missing is a dash, never 0', () => {
  it('mAP 4 decimals, latency 2 decimals, params integer with separators', () => {
    expect(fmtMap(0.49391829)).toBe('0.4939');
    expect(fmtLat(7.6127)).toBe('7.61');
    expect(fmtInt(2375031)).toBe('2,375,031');
    expect(fmtInt(2375031.4)).toBe('2,375,031');
  });
  it('null / undefined / NaN / Infinity / strings give the dash', () => {
    for (const bad of [null, undefined, Number.NaN, Infinity, 'x', {}]) {
      expect(fmtMap(bad)).toBe(DASH);
      expect(fmtLat(bad)).toBe(DASH);
      expect(fmtInt(bad)).toBe(DASH);
      expect(fmtDelta(bad)).toBe(DASH);
      expect(fmtDur(bad)).toBe(DASH);
    }
    expect(fmtMap(0)).toBe('0.0000'); // a real zero stays a zero
  });
  it('signed deltas', () => {
    expect(fmtDelta(0.01234)).toBe('+0.0123');
    expect(fmtDelta(-0.01234)).toBe('-0.0123');
    expect(fmtDelta(0)).toBe('0.0000');
    expect(fmtDelta(-0.00001)).toBe('0.0000');
    expect(fmtDelta(-1.234, 2)).toBe('-1.23');
    expect(fmtDeltaInt(1500)).toBe('+1,500');
    expect(fmtDeltaInt(-2000)).toBe('-2,000');
  });
  it('FPS = 1000 / ms, non-positive latency gives the dash', () => {
    expect(fmtFps(8)).toBe('125.0');
    expect(fmtFps(0)).toBe(DASH);
    expect(fmtFps(null)).toBe(DASH);
  });
  it('durations and stamps', () => {
    expect(fmtDur(42)).toBe('42 s');
    expect(fmtDur(307)).toBe('5 min 07 s');
    expect(fmtDur(3723)).toBe('1 h 02 min 03 s');
    expect(fmtStamp('2026-10-12T22:48:29Z')).toBe('2026-10-12 22:48');
    expect(fmtStamp('not a date')).toBe('not a date');
    expect(fmtStamp(null)).toBe(DASH);
  });
  it('misc helpers', () => {
    expect(shortHash('abcdef0123456789', 6)).toBe('abcdef…');
    expect(shortHash(null)).toBe(DASH);
    expect(fmtBool(null)).toBe(DASH);
    expect(fmtVal({ a: 1 })).toBe('{"a":1}');
    expect(fmtVal(1234567)).toBe('1,234,567');
    expect(fmtVal(0.123456789)).toBe('0.123457');
    expect(numOrNull('0.5')).toBe(0.5);
    expect(numOrNull('')).toBeNull();
    expect(epochsLabel('final', 12)).toBe('final 12 ep');
    expect(epochsLabel('final', null)).toBe('final');
  });
});

describe('fidelity tier labels', () => {
  it('parses search and final tiers with their epochs', () => {
    expect(tierInfo('T@8e')).toMatchObject({ kind: 'search', epochs: 8 });
    expect(tierInfo('T@10e').label).toContain('10 ep');
    expect(tierInfo('final@12e')).toMatchObject({ kind: 'final', epochs: 12 });
    expect(tierInfo('final@12e').label).toContain('최종');
    expect(tierInfo('weird')).toMatchObject({ kind: 'other', label: 'weird' });
    expect(tierInfo(null).label).toBe(DASH);
  });
});

describe('module histogram comparison', () => {
  it('delta = current - previous, sorted by |delta|, missing previous gives null (not cur - 0)', () => {
    const rows = histogramRows({ Conv: 10, C3k2: 4, SPPF: 2 }, { Conv: 8, C3k2: 9, A2C2f: 3 });
    expect(rows.map((r) => [r.module, r.cur, r.prev, r.delta])).toEqual([
      ['C3k2', 4, 9, -5],
      ['A2C2f', 0, 3, -3],
      ['Conv', 10, 8, 2], // |Δ| ties are ordered by name
      ['SPPF', 2, 0, 2],
    ]);
    const noPrev = histogramRows({ Conv: 3, SPPF: 1 }, null);
    expect(noPrev.every((r) => r.delta === null && r.prev === null)).toBe(true);
    expect(noPrev[0].module).toBe('Conv');
  });
  it('ties are ordered by name for a stable display', () => {
    const rows = histogramRows({ B: 2, A: 2 }, { B: 1, A: 1 });
    expect(rows.map((r) => r.module)).toEqual(['A', 'B']);
  });
});

describe('hash parsing and building', () => {
  const screens = ['archive', 'compare', 'server'];
  it('parses screen, run, gen, cid', () => {
    const p = parseHashString('#/archive?run=drone26_day_20261001_010516&gen=3&cid=g003_c0012', screens, 'archive');
    expect(p).toMatchObject({ screen: 'archive', run: 'drone26_day_20261001_010516', gen: 3, cid: 'g003_c0012', runs: [] });
  });
  it('empty or unknown hash falls back, malformed values are dropped', () => {
    expect(parseHashString('', screens, 'archive').screen).toBe('archive');
    expect(parseHashString('#/nope', screens, 'archive').screen).toBe('archive');
    const p = parseHashString('#/archive?run=../etc&gen=-1&cid=a/b', screens, 'archive');
    expect(p).toMatchObject({ run: null, gen: null, cid: null });
    expect(parseHashString('#/archive?gen=abc', screens, 'archive').gen).toBeNull();
    expect(parseHashString('#/archive?run=' + 'a'.repeat(129), screens, 'archive').run).toBeNull();
  });
  it('compare ids: deduplicated, validated and capped at six', () => {
    const p = parseHashString('#/compare?runs=a,b,a,../x,c,d,e,f,g', screens, 'archive');
    expect(p.runs).toEqual(['a', 'b', 'c', 'd', 'e', 'f']);
  });
  it('builds round-trippable hashes and never writes gen/cid without a run', () => {
    expect(buildArchiveHash({})).toBe('#/archive');
    expect(buildArchiveHash({ run: 'r1' })).toBe('#/archive?run=r1');
    expect(buildArchiveHash({ run: 'r1', gen: 0, cid: 'c1' })).toBe('#/archive?run=r1&gen=0&cid=c1');
    expect(buildArchiveHash({ gen: 2, cid: 'c1' })).toBe('#/archive');
    const h = buildArchiveHash({ run: 'r.1-x', gen: 12, cid: 'g012_c0003' });
    expect(parseHashString(h, screens, 'server')).toMatchObject({ screen: 'archive', run: 'r.1-x', gen: 12, cid: 'g012_c0003' });
    expect(buildCompareHash([])).toBe('#/compare');
    expect(parseHashString(buildCompareHash(['a', 'b']), screens, 'archive').runs).toEqual(['a', 'b']);
  });
});

describe('overview helpers', () => {
  it('gates: FAIL first, then WARN, original order kept inside each group', () => {
    const g = [
      { name: 'p1', status: 'PASS' },
      { name: 'w1', status: 'WARN' },
      { name: 'f1', status: 'FAIL' },
      { name: 'i1', status: 'INFO' },
      { name: 'f2', status: 'FAIL' },
      { name: 'p2', status: 'PASS' },
    ].map((x) => ({ ...x, hard: null, measured: null, criterion: null }));
    expect(sortGates(g).map((x) => x.name)).toEqual(['f1', 'f2', 'w1', 'p1', 'i1', 'p2']);
    expect(gateCounts(g)).toEqual({ PASS: 2, WARN: 1, FAIL: 2, INFO: 1 });
  });
  it('flatten keeps scalars and compact arrays, two levels deep', () => {
    expect(flatten({ a: 1, b: { c: 2, d: { e: 3, f: { g: 4 } } }, h: [1, 2] })).toEqual([
      ['a', 1],
      ['b.c', 2],
      ['b.d.e', 3],
      ['b.d.f', { g: 4 }],
      ['h', [1, 2]],
    ]);
  });
});

describe('baseline rows', () => {
  it('judge first, anchor last, every row carries a tier label', () => {
    const list = fixtureBaselines(RUN_A);
    const rows = buildBaselineRows(list, 'baseline_yolo26n', SYNTH_A.rows[0]);
    expect(rows[0].name).toBe('baseline_yolo26n');
    expect(rows[0].isJudge).toBe(true);
    expect(rows[0].tierText).toBe('final 12 ep');
    expect(rows[rows.length - 1].kind).toBe('anchor');
    expect(rows[rows.length - 1].tierText).toContain('T@8e');
    expect(rows.filter((r) => r.isJudge)).toHaveLength(1);
  });
  it('the contract is_judge flag decides; without it the judge name is the fallback; without both nothing is highlighted', () => {
    const list = fixtureBaselines(RUN_A);
    expect(buildBaselineRows(list, null).filter((r) => r.isJudge).map((r) => r.name)).toEqual(['baseline_yolo26n']);
    const older = { ...list, baselines: (list?.baselines ?? []).map((b) => ({ ...b, is_judge: undefined })) } as unknown as NonNullable<typeof list>;
    expect(buildBaselineRows(older, 'baseline_yolov11n').filter((r) => r.isJudge).map((r) => r.name)).toEqual(['baseline_yolov11n']);
    expect(buildBaselineRows(older, null).some((r) => r.isJudge)).toBe(false);
  });
});

describe('scatter points', () => {
  it('only ok rows with finite x and y are plotted; missing values are counted, not zero-filled', () => {
    const { points, skipped } = buildPoints(SYNTH_A.rows, 'latency', 'map50_95');
    expect(points.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
    expect(points.some((p) => p.x === 0)).toBe(false);
    const okRows = SYNTH_A.rows.filter((r) => r.status === 'ok');
    expect(points.length + skipped).toBe(okRows.length);
    expect(skipped).toBeGreaterThan(0); // the synthetic run has an ok row without latency
    expect(points.filter((p) => p.final).length).toBe(SYNTH_A.rows.filter((r) => r.is_final).length);
  });
});

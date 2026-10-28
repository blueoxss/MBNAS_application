/**
 * OWNER-ONLY (imports the test-only fitness oracle; DD-A43 a). Keeps the strength the oracle gave the public fixtures before
 * the split: every fitness the public tests/fixtures.ts stamps on its canonical rows must equal the oracle value of the
 * row's metrics under the run's weights and anchors, bit for bit; the recorded table may hold nothing else; and outside the
 * metric domain both answer null.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { cand, fixtureCandidates, fixtureFinalSnapshot, inMetricDomain, metricKey, RECORDED_FITNESS, resetCounter, stampedFitness } from './fixtures';
import { candWithOracle, oracleFitness } from './fixtures_oracle';

beforeEach(resetCounter);

const parse = (key: string): { ap: number; recall: number; latency_ms: number; size_mb: number } => {
  const [ap, recall, latency_ms, size_mb] = key.split('|').map(Number);
  return { ap, recall, latency_ms, size_mb };
};

/** Every row the public fixtures (and the Playwright mock built from them) serve with a server fitness. */
function canonicalRows(): ReturnType<typeof cand>[] {
  resetCounter();
  const rows = [...fixtureCandidates(), ...fixtureFinalSnapshot().candidates];
  rows.push(cand(), cand({ latency_ms: 44 }), cand({ latency_ms: 38, ap: 0.33 })); // default row, e2e/mock.ts "happy" row, e2e console live row
  return rows;
}

describe('recorded server fitness of the public fixtures (oracle re-derivation)', () => {
  it('every RECORDED_FITNESS entry is the oracle value of its metric tuple, bit for bit', () => {
    const keys = Object.keys(RECORDED_FITNESS);
    expect(keys.length).toBe(19);
    for (const key of keys) {
      const m = parse(key);
      expect(metricKey(m), key).toBe(key); // the key round-trips (no lossy float printing)
      expect(Object.is(oracleFitness(m), RECORDED_FITNESS[key]), `${key}: oracle ${oracleFitness(m)} vs recorded ${RECORDED_FITNESS[key]}`).toBe(true);
    }
  });

  it('every canonical row in the metric domain carries exactly the oracle fitness (never the stand-in)', () => {
    const rows = canonicalRows();
    let scored = 0;
    for (const row of rows) {
      if (!inMetricDomain(row)) {
        expect(row.fitness, row.name).toBeNull();
        continue;
      }
      scored += 1;
      expect(RECORDED_FITNESS[metricKey(row)], `${row.name} is recorded`).toBeDefined();
      expect(Object.is(row.fitness, oracleFitness(row)), `${row.name}: ${row.fitness} vs ${oracleFitness(row)}`).toBe(true);
    }
    expect(scored).toBe(rows.length - 2); // the failed and the training row have no metrics
  });

  it('the recorded table holds only canonical tuples (no orphan values)', () => {
    const used = new Set(canonicalRows().filter(inMetricDomain).map(metricKey));
    expect(Object.keys(RECORDED_FITNESS).filter((k) => !used.has(k))).toEqual([]);
  });

  it('outside the metric domain the public stamp and the oracle both answer null; inside, the oracle is finite', () => {
    const base = { id: 'x', ap: 0.2, recall: 0.3, latency_ms: 50, size_mb: 20 };
    for (const bad of [{ ap: Number.NaN }, { ap: 1.5 }, { ap: -0.1 }, { recall: 2 }, { latency_ms: 0 }, { latency_ms: Number.POSITIVE_INFINITY }, { size_mb: -1 }, { size_mb: 0 }, { ap: null }]) {
      const row = { ...base, ...bad } as typeof base;
      expect(stampedFitness(row), JSON.stringify(bad)).toBeNull();
      expect(oracleFitness(row), JSON.stringify(bad)).toBeNull();
    }
    expect(oracleFitness(base)).toBe(stampedFitness(base));
  });

  it('candWithOracle gives arbitrary rows a metric-consistent fitness, and an explicit fitness wins', () => {
    const row = candWithOracle({ ap: 0.4, recall: 0.5, latency_ms: 120, size_mb: 60 });
    expect(row.fitness).toBe(oracleFitness(row));
    expect(row.fitness).not.toBe(stampedFitness(row)); // an unrecorded tuple: the public stand-in would differ
    expect(candWithOracle({ ap: 0.4, fitness: 0.123 }).fitness).toBe(0.123);
    expect(candWithOracle({ ap: Number.NaN }).fitness).toBeNull();
  });
});

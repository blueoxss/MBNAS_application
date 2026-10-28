/**
 * TEST-ONLY fitness oracle (DD-A21 / WP-6 R1). The console never computes fitness (DD-A8): the server does
 * (mbnas_core protocol/fitness.py, pinned by tests/owner/unit/test_protocol_fitness.py). Synthetic fixtures still need
 * internally consistent rows (a candidate's `fitness` that matches its metrics, weights and the run's anchors), so this
 * file keeps the published formula for FIXTURES ONLY:
 *   F = w_ap*AP + w_recall*R + w_latency*r_T + w_size*r_S,  r = 1 - clip((value - min) / (max - min), 0, 1).
 * It lives outside src/ and is never imported from src/, so it cannot reach the production bundle (dist/ grep:
 * `npm run check:bundle`). Invalid input gives null (never 0), like the server.
 */

export interface OracleWeights {
  ap: number;
  recall: number;
  latency_reward: number;
  size_reward: number;
}

export interface OracleAnchors {
  latency_bounds_ms: readonly [number, number];
  size_bounds_MB: readonly [number, number];
}

export interface OracleMetrics {
  ap: number | null | undefined;
  recall: number | null | undefined;
  latency_ms: number | null | undefined;
  size_mb: number | null | undefined;
}

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Bounded reward in [0, 1] against fixed anchors (1 at or below min, 0 at or above max). */
export function oracleReward(value: number, bounds: readonly [number, number]): number {
  const [lo, hi] = bounds;
  return 1 - Math.min(Math.max((value - lo) / (hi - lo), 0), 1);
}

/** Fixture fitness and rewards, or nulls when a metric / weight / anchor is unusable. */
export function fitnessOracle(m: OracleMetrics, w: OracleWeights, a: OracleAnchors): { fitness: number | null; r_T: number | null; r_S: number | null } {
  const none = { fitness: null, r_T: null, r_S: null };
  const weights = [w?.ap, w?.recall, w?.latency_reward, w?.size_reward];
  if (!weights.every(finite) || weights.some((x) => x < 0) || Math.abs(weights.reduce((s, x) => s + x, 0) - 1) > 1e-6) return none;
  const anchorOk = (b: readonly [number, number] | undefined): b is readonly [number, number] => Array.isArray(b) && b.length === 2 && finite(b[0]) && finite(b[1]) && b[0] > 0 && b[0] < b[1];
  if (!anchorOk(a?.latency_bounds_ms) || !anchorOk(a?.size_bounds_MB)) return none;
  if (![m.ap, m.recall, m.latency_ms, m.size_mb].every(finite)) return none;
  const [ap, recall, lat, size] = [m.ap, m.recall, m.latency_ms, m.size_mb] as number[];
  if (ap < 0 || ap > 1 || recall < 0 || recall > 1 || lat <= 0 || size <= 0) return none;
  const r_T = oracleReward(lat, a.latency_bounds_ms);
  const r_S = oracleReward(size, a.size_bounds_MB);
  return { fitness: w.ap * ap + w.recall * recall + w.latency_reward * r_T + w.size_reward * r_S, r_T, r_S };
}

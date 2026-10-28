/**
 * OWNER-ONLY fixture helpers that use the test-only fitness oracle (tests/fitness_oracle.ts). DD-A43 (a): the oracle and
 * every file that imports it, directly or transitively, are excluded from the public and registration bundles (packager
 * [deny-importers] rule), so everything oracle-based lives here and in the owner-only tests that import this module:
 *   - tests/fixtures_oracle.test.ts  re-derives the RECORDED_FITNESS table and the canonical rows of tests/fixtures.ts;
 *   - e2e/weights.spec.ts            stamps a weighted row the way the server would.
 * tests/fixtures.ts, tests/mockapi.ts and e2e/mock.ts must never import this file (tests/oracle_isolation.test.ts).
 */
import type { Candidate } from '../src/api/types';
import { fitnessOracle, type OracleMetrics } from './fitness_oracle';
import { ANCHORS, cand, WEIGHTS, type CandInit } from './fixtures';

/** Oracle fitness of a metric tuple under the fixture run's weights and anchors (null outside the metric domain). */
export function oracleFitness(m: OracleMetrics): number | null {
  return fitnessOracle(m, WEIGHTS, ANCHORS).fitness;
}

/** A synthetic row whose fitness is computed from its own metrics by the oracle (unless `fitness` is given). */
export function candWithOracle(over: CandInit = {}): Candidate {
  const row = cand({ ...over, fitness: over.fitness === undefined ? null : over.fitness });
  return over.fitness === undefined ? { ...row, fitness: oracleFitness(row) } : row;
}
